import os
from pathlib import Path

import click
import tomllib

from flamapy.interfaces.python.flamapy_feature_model import FLAMAFeatureModel


def _read_splent_app(workspace: str = "/workspace") -> str:
    env_path = os.path.join(workspace, ".env")
    if not os.path.exists(env_path):
        raise click.ClickException("Missing /workspace/.env (run: splent product:select <app>)")

    app_name = None
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("SPLENT_APP="):
                app_name = line.strip().split("=", 1)[1]

    if not app_name:
        raise click.ClickException("SPLENT_APP not set in /workspace/.env (run: splent product:select <app>)")

    product_path = os.path.join(workspace, app_name)
    if not os.path.isdir(product_path):
        raise click.ClickException(f"Active product not found: {product_path}")

    return app_name


def _load_pyproject(pyproject_path: str) -> dict:
    p = Path(pyproject_path)
    if not p.exists():
        raise click.ClickException(f"Missing {pyproject_path}")
    with open(p, "rb") as f:
        return tomllib.load(f)


def _get_uvl_cfg(data: dict) -> dict:
    try:
        return data["tool"]["splent"]["uvl"]
    except KeyError:
        raise click.ClickException("Missing [tool.splent.uvl] in pyproject.toml")


@click.command(
    "uvl:configs",
    short_help="Print the number of valid configurations represented by the UVL model",
)
@click.option("--workspace", default="/workspace", show_default=True)
@click.option("--pyproject", default=None, help="Override pyproject.toml path")
@click.option(
    "--with-sat",
    is_flag=True,
    help="Force PySAT backend (useful in some environments; slower sometimes)",
)
def uvl_configs(workspace, pyproject, with_sat):
    app_name = _read_splent_app(workspace=workspace)
    product_path = os.path.join(workspace, app_name)

    pyproject_path = pyproject or os.path.join(product_path, "pyproject.toml")
    data = _load_pyproject(pyproject_path)

    uvl_cfg = _get_uvl_cfg(data)
    file = uvl_cfg.get("file")
    if not file:
        raise click.ClickException("Missing [tool.splent.uvl].file in pyproject.toml")

    local_uvl = os.path.join(product_path, "uvl", file)
    if not os.path.exists(local_uvl):
        raise click.ClickException(f"UVL not downloaded: {local_uvl} (run: splent uvl:fetch)")

    fm = FLAMAFeatureModel(local_uvl)

    try:
        n = fm.configurations_number(with_sat=bool(with_sat))
    except TypeError:
        # Backward compatibility with versions where the param might not exist
        n = fm.configurations_number()

    click.echo()
    click.echo("UVL configs")
    click.echo(f"Product : {app_name}")
    click.echo(f"UVL     : {local_uvl}")
    click.echo(f"Count   : {n}")
    click.echo()