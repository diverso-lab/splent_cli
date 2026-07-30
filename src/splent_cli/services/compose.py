"""
Docker Compose helpers shared across all product commands.
"""

import os
import subprocess
from pathlib import Path

from splent_cli.utils.feature_utils import normalize_namespace


def project_name(name: str, env: str) -> str:
    """Generate a safe Docker Compose project name from product/feature name and env."""
    return f"{name}_{env}".replace("/", "_").replace("@", "_").replace(".", "_")


def feature_project_name(feature_ref: str, product: str, env: str) -> str:
    """Name the Compose project of a feature's own Docker stack, per product.

    A feature that ships a top-level ``docker/`` directory contributes a stack
    of its own, and that stack belongs to the product that started it, exactly
    like the product's database does. The product name therefore has to be part
    of the project name.

    Without it, two products pinning the same feature version resolve to the
    same project: the second ``product:up`` recreates the container the first
    product was using, and both end up sharing one container and one named
    volume, because Compose prefixes volume names with the project name and
    ``<project>_elasticsearch_data`` is then literally the same volume. Two
    products that never agreed to share state silently do.

    The per-product host port offset the CLI already applies to every
    ``*_HOST_PORT`` variable, ``zlib.crc32(product) % 1000``, only makes sense
    under this rule: a distinct port per product presupposes a distinct
    instance to publish it.

    ``feature_ref`` is a normalised ref (``splent_io/splent_feature_x@v0.1.0``).
    The pinned version stays in the name so a product that moves to a new
    version gets a new stack instead of reusing the old one.
    """
    return project_name(f"{product}/{feature_ref}", env)


def deploy_project_name(product: str) -> str:
    """Name the Compose project a product is deployed under.

    ``product:deploy`` used to pass no ``-p`` at all, so Compose fell back to
    the name of the directory holding the compose file. That directory is
    ``docker`` in every product, so every product deployed to a host landed in
    one project called ``docker``: shared volumes (``docker_elasticsearch_data``
    for all of them) and, worse, the same service names, so deploying the
    second product recreated the first product's containers out from under it.

    Two products of one line on one host is the ordinary case for a product
    line, so this was not an exotic configuration.
    """
    return project_name(product, "deploy")


LEGACY_DEPLOY_PROJECT = "docker"


def legacy_deploy_project_notice(product: str) -> str | None:
    """Report containers still deployed under the old shared ``docker`` project.

    They hold the ports and the volumes the new project wants, so the first
    deploy after this change fails on a port conflict unless they go. Removing
    them is the operator's call: this returns the notice and the command, and
    touches nothing. Volumes survive ``down`` without ``-v``.
    """
    try:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={LEGACY_DEPLOY_PROJECT}",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    containers = [c.strip() for c in result.stdout.splitlines() if c.strip()]
    if not containers:
        return None

    return (
        f"  {len(containers)} container(s) are still deployed under the old "
        f"project '{LEGACY_DEPLOY_PROJECT}', which every product on this host "
        f"shared.\n"
        f"  They hold the ports this deploy needs. Stop them with:\n"
        f"  docker compose -p {LEGACY_DEPLOY_PROJECT} "
        f"-f docker/docker-compose.deploy.yml down"
    )


def legacy_feature_project_name(feature_ref: str, env: str) -> str:
    """The name a feature's stack had before it belonged to a product.

    Nothing starts a stack under this name any more. It exists so a command can
    recognise containers left over from that era and point at them, instead of
    letting them run on unnoticed next to the per-product stack that replaced
    them.
    """
    return project_name(feature_ref, env)


def network_name(product: str) -> str:
    """The Docker network this product's containers talk to each other on.

    One network per product, and this is the second half of the isolation the
    per-product project name starts. The project name gives each product its
    own containers; without a network of its own they all still sit on one
    shared bridge, and Compose gives every container a DNS alias equal to its
    service name. Two products running the same feature then publish the same
    alias, Docker's resolver answers with both addresses in arbitrary order,
    and roughly half of one product's queries land on the other product's
    server. That is not a theory: with two elasticsearch containers up, six
    lookups of ``splent_feature_elasticsearch`` from one product's web
    container returned the other product's address twice.

    It also ends an access no one ever intended. Every product's database sat
    on the shared network under a name any other product's code could resolve,
    so a stray hostname in a config file reached a sibling product's data.

    The name goes into the products' merged .env as SPLENT_NETWORK, and every
    compose file resolves its network through ``${SPLENT_NETWORK:-...}``, so a
    product that predates this still lands on the old shared network rather
    than failing to start.
    """
    return f"{product}_network"


SHARED_NETWORK = "splent_network"


def ensure_network(name: str) -> bool:
    """Create the network if it is not there. True when it exists afterwards.

    Every compose file declares its network ``external``, which means Compose
    expects somebody else to have created it and refuses to start otherwise.
    That somebody used to be the reader of an error message telling them to
    run ``docker network create``. Since the name is now derived per product
    rather than fixed, asking a person to type it is asking them to get it
    right; creating it is one call and cannot be got wrong.
    """

    def _docker(*args) -> int | None:
        """Exit status, or None when docker could not be asked at all.

        Guarded like every other docker call in this module: a convenience
        that creates a network must never be the reason a command dies, and
        without this a machine with no docker on PATH raises out of the middle
        of ``product:up`` instead of failing at the compose call with a message
        about docker.
        """
        try:
            return subprocess.run(
                ["docker", "network", *args],
                capture_output=True,
                text=True,
                timeout=30,
            ).returncode
        except (OSError, subprocess.SubprocessError):
            return None

    if _docker("inspect", name) != 0 and _docker("create", name) != 0:
        return False
    attach_self_to_network(name)
    return True


def attach_self_to_network(name: str) -> bool:
    """Put the CLI's own container on exactly this product network.

    The CLI runs inside a container and reaches the product's services by
    name: the database for db:migrate, a feature's own server for the
    commands that feature contributes. That worked while every product shared
    one network. Now that each product has its own, the CLI has to join the
    one belonging to the product it is acting on, or those commands resolve
    nothing and report a running service as down.

    It also has to *leave* the others, which is why this is not simply
    "connect". A container on two product networks is back to one name
    answering from two servers, and the container it would happen to be is
    the one running ``feature:search reindex``: an index rebuilt into a
    sibling product's node, no error anywhere. One at a time is also all the
    CLI ever needs, since it acts on one active product.

    Silent and idempotent. False when there is nothing to attach (running on
    the host rather than in a container) or docker cannot be asked; either way
    the caller carries on and the real command reports the real problem.
    """
    if not os.path.exists("/.dockerenv"):
        return False

    # A container's hostname is its own short id unless someone overrode it,
    # which is what lets it name itself to docker without being told.
    container = os.uname().nodename
    fmt = "{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}"

    def _run(args) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None

    inspected = _run(["docker", "inspect", container, "--format", fmt])
    if inspected is None or inspected.returncode != 0:
        return False
    current = set(inspected.stdout.split())

    # Leave every other product network. SHARED_NETWORK is left alone: it is
    # where products written before this rule still live, and where the CLI
    # container itself is attached by its own compose file.
    for other in current:
        if other != name and other != SHARED_NETWORK and other.endswith("_network"):
            _run(["docker", "network", "disconnect", other, container])

    if name in current:
        return True

    connected = _run(["docker", "network", "connect", name, container])
    return connected is not None and connected.returncode == 0


def product_env_file(product_path: str, env: str = "dev") -> str | None:
    """The merged env file Compose should read for this product, or None.

    Which file that is depends on the environment: ``product:env --merge``
    writes development values to ``docker/.env`` and production values to
    ``docker/.env.deploy``. Handing the development file to a production
    stack, which is what a single hardcoded path did, published nginx on the
    development port and gave the search node the development heap, silently
    and in production only.
    """
    candidate = os.path.join(
        product_path, "docker", ".env.deploy" if env == "prod" else ".env"
    )
    return candidate if os.path.isfile(candidate) else None


def env_file_args(product_path: str, env: str = "dev") -> list[str]:
    """``--env-file`` flags aiming Compose at the product's merged env file.

    Compose reads the ``.env`` sitting next to the compose file it was given.
    For a feature stack that file is the feature's own template, not the
    product's, so the per-product values never arrive: the host port that
    ``product:env --merge`` offset for this product, and every variable whose
    ``__PRODUCT__`` placeholder it resolved. An unset port variable is not an
    error for Compose, it publishes a random host port instead, which is how
    the symptom shows up.

    Returns an empty list when the file is not there yet, so the caller still
    gets a usable command rather than a Compose error about a missing env
    file. Callers that start containers should say so first: see
    ``missing_env_file_notice``.
    """
    env_file = product_env_file(product_path, env)
    return ["--env-file", env_file] if env_file else []


def missing_env_file_notice(product_path: str, env: str = "dev") -> str | None:
    """Warn that a stack is about to start without the product's values.

    Silence here is expensive. Compose treats an unset variable in a port
    mapping as "publish on whatever is free", so the stack comes up, reports
    success, and listens on a port nobody can predict, which is the exact
    failure the per-product offset exists to prevent. Say it before starting,
    with the command that fixes it.
    """
    if product_env_file(product_path, env) is not None:
        return None
    name = ".env.deploy" if env == "prod" else ".env"
    flag = " --prod" if env == "prod" else ""
    return (
        f"  docker/{name} does not exist, so feature stacks start without this "
        f"product's values: host ports fall back to whatever is free.\n"
        f"  Run: splent product:env --merge{flag}"
    )


def feature_compose_cmd(
    project: str, compose_file: str, product_path: str, env: str = "dev"
) -> list[str]:
    """The ``docker compose`` prefix that addresses one feature stack.

    Every command that launches, stops, restarts or inspects a feature stack
    builds its invocation from here, so the project name and the product's env
    file can never disagree between two commands. A mismatch would leave one
    command unable to find what another one started.
    """
    return [
        "docker",
        "compose",
        "-p",
        project,
        *env_file_args(product_path, env),
        "-f",
        compose_file,
    ]


def legacy_feature_stack_notice(
    feature_ref: str, env: str, compose_file: str
) -> str | None:
    """Report a feature stack still running under its pre-per-product name.

    Those containers are orphans now: no product manages them, yet they hold
    the ports and the data the product used to reach. Removing them is the
    user's call, not ours, so this only returns the notice and the command that
    does it. Their named volumes survive ``down`` without ``-v``, so nothing is
    lost by following it.

    Returns None when there is nothing to report or when docker cannot be
    asked, since a courtesy notice must never be the reason a command fails.
    """
    legacy = legacy_feature_project_name(feature_ref, env)
    try:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={legacy}",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    running = [c.strip() for c in result.stdout.splitlines() if c.strip()]
    if not running:
        return None

    short = feature_ref.split("/")[-1] if "/" in feature_ref else feature_ref
    return (
        f"  {short}: {len(running)} container(s) are still running under the old "
        f"shared project '{legacy}', which no product manages any more.\n"
        f"  Run: docker compose -p {legacy} -f {compose_file} down"
    )


def resolve_file(product_path: str, env: str) -> str | None:
    """Return the path to the active docker-compose file, or None if not found.

    Prefers docker-compose.{env}.yml, falls back to docker-compose.yml.
    """
    docker_dir = os.path.join(product_path, "docker")
    preferred = os.path.join(docker_dir, f"docker-compose.{env}.yml")
    fallback = os.path.join(docker_dir, "docker-compose.yml")
    if os.path.exists(preferred):
        return preferred
    if os.path.exists(fallback):
        return fallback
    return None


def feature_docker_dir(workspace: str, feature: str) -> str:
    """Return the docker/ directory for a feature.

    Accepts refs like 'splent_io/splent_feature_redis@v1.5.4' or bare 'splent_feature_redis'.
    Checks workspace root first (editable features), then falls back
    to the .splent_cache (pinned features).
    """
    # Extract bare name (strip org prefix and version)
    name = feature.split("/")[-1] if "/" in feature else feature
    bare_name = name.split("@")[0]

    # Editable feature at workspace root
    root_docker = os.path.join(workspace, bare_name, "docker")
    if os.path.isdir(root_docker):
        return root_docker

    # Pinned feature in cache (needs full ref with org and version)
    return os.path.join(workspace, ".splent_cache", "features", feature, "docker")


def normalize_feature_ref(feat: str) -> str:
    """Normalise a raw feature ref to org_safe/name format.

    'features/splent_io/splent_feature_auth' -> 'splent_io/splent_feature_auth'
    'splent_feature_auth'                    -> 'splent_io/splent_feature_auth'
    'splent_io/splent_feature_auth'          -> 'splent_io/splent_feature_auth'
    'splent-io/splent_feature_auth'          -> 'splent_io/splent_feature_auth'
    """
    if "features/" in feat:
        feat = feat.split("features/")[-1]
    if "/" not in feat:
        feat = f"splent_io/{feat}"
    else:
        org, rest = feat.split("/", 1)
        feat = f"{normalize_namespace(org)}/{rest}"
    return feat


def product_path(product: str, workspace: str) -> str:
    """Return the absolute path to a product directory."""
    return os.path.join(workspace, product)


def parse_feature_identifier(identifier: str) -> tuple[str, str, str, str]:
    """Parse a feature identifier into its components.

    Accepts two forms:
      - "namespace/feature_name"   → explicit namespace
      - "feature_name"             → defaults to "splent-io"

    Returns (namespace, namespace_github, namespace_fs, feature_name) where:
      namespace_github  dash-separated  (GitHub org name)
      namespace_fs      underscore-separated (filesystem safe)
    """
    if "/" in identifier:
        namespace, feature_name = identifier.split("/", 1)
    else:
        namespace = "splent-io"
        feature_name = identifier

    namespace_github = namespace.replace("_", "-")
    namespace_fs = normalize_namespace(namespace)

    return namespace, namespace_github, namespace_fs, feature_name


def find_main_container(
    project_name: str, compose_file: str, docker_dir: str
) -> str | None:
    """Find the main container for a product — the one with /workspace mounted."""
    result = subprocess.run(
        ["docker", "compose", "-p", project_name, "-f", compose_file, "ps", "-q"],
        cwd=docker_dir,
        capture_output=True,
        text=True,
    )
    container_ids = [c.strip() for c in result.stdout.splitlines() if c.strip()]

    for cid in container_ids:
        mounts = (
            subprocess.run(
                [
                    "docker",
                    "inspect",
                    "-f",
                    "{{ range .Mounts }}{{ .Destination }} {{ end }}",
                    cid,
                ],
                capture_output=True,
                text=True,
            )
            .stdout.strip()
            .split()
        )
        if "/workspace" in mounts:
            return cid

    return container_ids[0] if container_ids else None


def remove_broken_symlinks(workspace: Path) -> int:
    """Remove broken feature symlinks from all products under workspace.

    Returns the number of symlinks removed.
    """
    removed = 0
    for product_dir in workspace.iterdir():
        features_dir = product_dir / "features"
        if not features_dir.is_dir():
            continue
        for org_dir in features_dir.iterdir():
            if not org_dir.is_dir():
                continue
            for link in org_dir.iterdir():
                if link.is_symlink() and not link.exists():
                    link.unlink()
                    removed += 1
    return removed
