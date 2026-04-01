import os
import tomllib
import subprocess
import shutil
import click
from splent_cli.services import context, compose
from splent_cli.utils.feature_utils import read_features_from_data


def _is_port_var(key: str, value: str) -> bool:
    """Check if an env var looks like a port declaration.

    Matches variables ending in _PORT or containing _PORT_ with a numeric value.
    Excludes internal ports (like MAIL_PORT=1025 which is a container-internal port).
    """
    if not value.strip().isdigit():
        return False
    key_upper = key.upper()
    # HOST_PORT suffix = definitely a host port mapping
    if key_upper.endswith("_HOST_PORT"):
        return True
    # _PORT_ONE, _PORT_TWO = mailhog pattern
    if "_PORT_ONE" in key_upper or "_PORT_TWO" in key_upper:
        return True
    return False


@click.command(
    "product:env", short_help="Generate or merge .env files for the active product."
)
@click.option(
    "--generate",
    is_flag=True,
    help="Generate the .env file for the product (and optionally all features).",
)
@click.option(
    "--merge", is_flag=True, help="Merge all feature .env files into the product .env."
)
@click.option(
    "--dev",
    "env_name",
    flag_value="dev",
    help="Use development env (.env.dev.example).",
)
@click.option(
    "--prod",
    "env_name",
    flag_value="prod",
    help="Use production env (.env.prod.example).",
)
@click.option(
    "--all",
    "process_all",
    is_flag=True,
    help="Also process all features declared in pyproject.toml (only for --generate).",
)
def product_env(generate, merge, env_name, process_all):
    """
    Manage environment files for the active product.

    Examples:
        splent product:env --generate --dev
        splent product:env --generate --prod
        splent product:env --generate --all --dev
        splent product:env --merge --dev
    """
    workspace = str(context.workspace())
    product = context.require_app()

    if not env_name:
        click.secho("  You must specify --dev or --prod.", fg="red")
        raise SystemExit(1)

    product_path = os.path.join(workspace, product)
    docker_dir = os.path.join(product_path, "docker")
    py_path = os.path.join(product_path, "pyproject.toml")

    if not os.path.exists(py_path):
        click.secho(f"  pyproject.toml not found at {py_path}", fg="red")
        raise SystemExit(1)

    if not os.path.exists(docker_dir):
        click.secho(f"  docker directory not found at {docker_dir}", fg="red")
        raise SystemExit(1)

    if not (generate or merge):
        click.secho("  You must specify either --generate or --merge.", fg="red")
        raise SystemExit(1)

    # ── Generate mode ─────────────────────────────────────────────────────
    if generate:
        env_file = os.path.join(docker_dir, ".env")
        example_specific = os.path.join(docker_dir, f".env.{env_name}.example")
        example_generic = os.path.join(docker_dir, ".env.example")

        if os.path.exists(env_file):
            click.echo(
                click.style("  product ", dim=True)
                + click.style(".env exists, skipping", dim=True)
            )
        else:
            selected = (
                example_specific
                if os.path.exists(example_specific)
                else example_generic
            )
            if selected:
                shutil.copyfile(selected, env_file)
                click.echo(
                    click.style("  product ", dim=True)
                    + f".env created from {os.path.basename(selected)}"
                )
            else:
                click.secho(
                    f"  product  no .env template found in {docker_dir}", fg="yellow"
                )

        if not process_all:
            return

        # Process all declared features
        with open(py_path, "rb") as f:
            data = tomllib.load(f)

        features = read_features_from_data(data, env_name)
        if not features:
            click.echo(click.style("  No features declared.", dim=True))
            return

        created, skipped, no_template, failed = 0, 0, 0, 0
        for f_entry in features:
            base_name = f_entry.split("@")[0]
            cmd = ["splent", "feature:env", base_name, "--generate", f"--{env_name}"]
            result = subprocess.run(cmd, capture_output=True, text=True)
            output = result.stdout.strip()
            lines = [line.strip() for line in output.splitlines() if line.strip()]
            cleaned = "\n".join(lines)

            if result.returncode != 0:
                if "docker directory not found" in cleaned:
                    no_template += 1
                else:
                    failed += 1
                    click.secho(f"  {base_name}: {cleaned}", fg="red")
                continue
            if "No .env template" in cleaned:
                no_template += 1
            elif "Created" in cleaned:
                created += 1
                short = base_name.split("/")[-1] if "/" in base_name else base_name
                click.echo(f"  {click.style('  +', fg='green')}  {short} .env created")
            else:
                skipped += 1

        if failed:
            click.secho(f"  {created} created, {failed} failed", fg="red")
        return

    # ── Merge mode ────────────────────────────────────────────────────────
    if merge:
        # Locate product base env file
        candidates = [
            os.path.join(docker_dir, f".env.{env_name}.example"),
            os.path.join(docker_dir, ".env.example"),
            os.path.join(docker_dir, ".env"),
        ]
        base_env = next((c for c in candidates if os.path.exists(c)), None)

        if not base_env:
            click.secho(
                f"  No .env template found for {product} ({env_name})", fg="red"
            )
            raise SystemExit(1)

        target_env = os.path.join(docker_dir, ".env")
        if base_env != target_env:
            shutil.copyfile(base_env, target_env)

        with open(py_path, "rb") as f:
            data = tomllib.load(f)
        features = read_features_from_data(data, env_name)
        if not features:
            return

        merged = {}
        with open(target_env, "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    merged[k] = v

        feature_env_paths = []
        for feature in features:
            clean_ref = compose.normalize_feature_ref(feature)
            bare_name = clean_ref.split("/")[-1] if "/" in clean_ref else clean_ref
            bare_name = bare_name.split("@")[0]  # strip version
            docker_dir_f = compose.feature_docker_dir(workspace, bare_name)

            candidates_f = [
                os.path.join(docker_dir_f, ".env"),
                os.path.join(docker_dir_f, f".env.{env_name}.example"),
                os.path.join(docker_dir_f, ".env.example"),
            ]
            base_f = next((c for c in candidates_f if os.path.exists(c)), None)
            if not base_f:
                continue

            target_f = os.path.join(docker_dir_f, ".env")
            if base_f != target_f:
                shutil.copyfile(base_f, target_f)

            feature_env_paths.append(target_f)

        # Compute product port offset for feature port adjustment
        import zlib

        port_offset = zlib.crc32(product.encode("utf-8")) % 1000
        port_adjusted = []

        for f_env in feature_env_paths:
            if not os.path.exists(f_env):
                continue
            with open(f_env, "r", encoding="utf-8") as f:
                for line in f:
                    if "=" in line and not line.strip().startswith("#"):
                        k, v = line.strip().split("=", 1)
                        if k not in merged and _is_port_var(k, v):
                            try:
                                original = int(v)
                                adjusted = original + port_offset
                                merged.setdefault(k, str(adjusted))
                                port_adjusted.append((k, original, adjusted))
                            except ValueError:
                                merged.setdefault(k, v)
                        else:
                            merged.setdefault(k, v)

        with open(target_env, "w", encoding="utf-8") as f:
            for k, v in merged.items():
                f.write(f"{k}={v}\n")

        if port_adjusted:
            click.echo(
                click.style("  ports    ", dim=True)
                + f"adjusted {len(port_adjusted)} feature port(s) (+{port_offset})"
            )

        click.echo(
            click.style("  merged   ", dim=True)
            + f"{len(feature_env_paths)} feature .env file(s) into product .env"
        )
