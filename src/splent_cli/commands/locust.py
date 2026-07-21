"""splent locust — load testing for the active product.

Locust runs inside the product's web container, which already has
splent_framework and every installed feature importable, so no extra image is
built. Discovery is done by splent_framework's locustfile bootstrap (1.7.1+),
which finds each feature's locustfile on its own; a feature argument narrows it
through SPLENT_LOCUSTFILES.

The web interface listens on port 8089 inside the container. Products
scaffolded by this CLI publish that port; for an older product add
"8089:8089" to the web service's ports and recreate the container.
"""

import os
import shlex

import click

from splent_cli.services import context
from splent_cli.utils.proc import require_docker, run

# Feature locustfile locations, relative to the workspace. Kept in sync with
# splent_framework.bootstraps.locustfile_bootstrap.DEFAULT_PATTERNS.
FEATURE_LOCUSTFILE_PATTERNS = (
    "features/*/{feature}/tests/load/locustfile.py",
    "features/*/{feature}/tests/locustfile.py",
    "features/*/{feature}/src/*/*/tests/load/locustfile.py",
    "app/features/{feature}/tests/locustfile.py",
)

# Resolved inside the container: the framework bootstrap that discovers and
# re-exports every feature's HttpUser classes.
BOOTSTRAP_SNIPPET = (
    "locust -f \"$(python -c 'import splent_framework.bootstraps.locustfile_bootstrap as m; "
    "print(m.__file__)')\" --web-host 0.0.0.0"
)


def _web_container() -> str:
    return f"{context.require_app()}_web"


def _container_running(name: str) -> bool:
    result = run(
        ["docker", "ps", "-q", "-f", f"name=^{name}$"], check=False, capture=True
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _locust_installed(name: str) -> bool:
    result = run(
        ["docker", "exec", name, "python", "-c", "import locust"],
        check=False,
        capture=True,
    )
    return result.returncode == 0


def _feature_patterns(feature: str) -> str:
    return ",".join(p.format(feature=feature) for p in FEATURE_LOCUSTFILE_PATTERNS)


def _feature_has_locustfile(workspace: str, feature: str) -> bool:
    import glob

    for pattern in FEATURE_LOCUSTFILE_PATTERNS:
        if glob.glob(os.path.join(workspace, pattern.format(feature=feature))):
            return True
    return False


@click.command(
    "locust", short_help="Run Locust load tests inside the product web container."
)
@click.argument("feature", required=False)
@context.requires_product
def locust(feature):
    require_docker()
    container = _web_container()

    if not _container_running(container):
        raise click.ClickException(
            f"Container '{container}' is not running. Start the product first: splent product:up"
        )

    if not _locust_installed(container):
        raise click.ClickException(
            f"locust is not installed in '{container}'.\n"
            'Add "locust" to [project.optional-dependencies].dev in the product\'s '
            "pyproject.toml and rebuild (splent product:derive), or install it once "
            f"with: docker exec {container} pip install locust"
        )

    workspace = str(context.workspace())
    env_args = ["-e", "WORKING_DIR=/workspace"]
    if feature:
        if not _feature_has_locustfile(workspace, feature):
            raise click.ClickException(
                f"No locustfile found for feature '{feature}'. Looked under: "
                + ", ".join(FEATURE_LOCUSTFILE_PATTERNS)
            )
        env_args += ["-e", f"SPLENT_LOCUSTFILES={_feature_patterns(feature)}"]

    check = run(
        ["docker", "exec", container, "sh", "-c", "pgrep -f 'locust' >/dev/null"],
        check=False,
        capture=True,
    )
    if check.returncode == 0:
        click.echo("Locust is already running in the product container.")
        return

    cmd = ["docker", "exec", "-d", *env_args, container, "sh", "-c", BOOTSTRAP_SNIPPET]
    click.echo(f"Command: {' '.join(shlex.quote(c) for c in cmd)}")
    run(cmd)
    click.echo(click.style("Locust is running at http://localhost:8089", fg="green"))
    click.echo("Stop it with: splent locust:stop")


@click.command(
    "locust:stop", short_help="Stop the Locust process in the product web container."
)
@context.requires_product
def locust_stop():
    require_docker()
    container = _web_container()

    if not _container_running(container):
        click.echo(f"Container '{container}' is not running; nothing to stop.")
        return

    result = run(
        ["docker", "exec", container, "sh", "-c", "pkill -f 'locust' || true"],
        check=False,
        capture=True,
    )
    if result.returncode == 0:
        click.echo("Locust stopped.")
    else:
        click.echo("No Locust process was running.")
