import inspect
import importlib
import os
import click

from splent_cli.commands.database.db_reset import db_reset
from splent_cli.utils.decorators import requires_db
from splent_cli.utils.feature_utils import get_features_from_pyproject
from splent_framework.seeders.BaseSeeder import BaseSeeder
from splent_framework.managers.feature_order import FeatureLoadOrderResolver
from splent_framework.utils.pyproject_reader import PyprojectReader


def _resolve_feature_order(features_raw: list[str]) -> list[str]:
    """Return features in topological order using the product's UVL constraints.

    Falls back to the declared order in pyproject.toml if no UVL is available.
    """
    splent_app = os.getenv("SPLENT_APP", "")
    working_dir = os.getenv("WORKING_DIR", "/workspace")
    product_dir = os.path.join(working_dir, splent_app) if splent_app else ""

    uvl_path = None
    if product_dir:
        try:
            uvl_cfg = PyprojectReader.for_product(product_dir).uvl_config
            uvl_file = uvl_cfg.get("file")
            if uvl_file:
                uvl_path = os.path.join(product_dir, "uvl", uvl_file)
        except Exception:
            pass

    return FeatureLoadOrderResolver().resolve(features_raw, uvl_path)


def get_installed_seeders(specific_module=None):
    features_raw = get_features_from_pyproject()
    if not features_raw:
        click.echo(click.style("⚠️  No features found in pyproject.toml", fg="yellow"))
        return []

    ordered = _resolve_feature_order(features_raw)

    seeders = []
    for feature in ordered:
        # Handle "splent-io/splent_feature_auth@v1.0.0" → org_safe.base_name.seeders
        base_name = feature.split("@")[0]
        if "/" in base_name:
            org_raw, base_name = base_name.split("/", 1)
            org_safe = org_raw.replace("-", "_").replace(".", "_")
        else:
            org_safe = "splent_io"
        module_name = f"{org_safe}.{base_name}.seeders"

        if specific_module and not base_name.endswith(specific_module):
            continue

        try:
            seeder_module = importlib.import_module(module_name)
            importlib.reload(seeder_module)

            for attr in dir(seeder_module):
                obj = getattr(seeder_module, attr)
                if (
                    inspect.isclass(obj)
                    and issubclass(obj, BaseSeeder)
                    and obj is not BaseSeeder
                ):
                    seeders.append(obj())

        except ModuleNotFoundError:
            # feature simply has no seeders.py
            continue
        except Exception as e:
            click.echo(
                click.style(
                    f"❌ Error loading seeders from {module_name}: {e}", fg="red"
                ),
                err=True,
            )

    # Order is already topological — no sort needed
    return seeders


@requires_db
@click.command(
    "db:seed", short_help="Populate the database using feature-level seeders."
)
@click.option("--reset", is_flag=True, help="Reset the database before seeding.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompts.")
@click.argument("module", required=False)
def db_seed(reset, yes, module):
    if reset:
        if yes or click.confirm(
            click.style("⚠️  This will reset the database. Continue?", fg="red"),
            abort=True,
        ):
            click.echo(click.style("🔄 Resetting the database...", fg="yellow"))
            ctx = click.get_current_context()
            ctx.invoke(db_reset, yes=True)
        else:
            click.echo(click.style("❌ Database reset cancelled.", fg="yellow"))
            return

    seeders = get_installed_seeders(specific_module=module)
    if not seeders:
        click.echo(click.style("⚠️  No seeders found.", fg="yellow"))
        return

    click.echo(
        click.style(
            f"🌱 Seeding {'feature ' + module if module else 'all features'}...",
            fg="green",
        )
    )

    success = True
    for seeder in seeders:
        try:
            seeder.run()
            click.echo(
                click.style(f"✔ {seeder.__class__.__name__} completed.", fg="blue")
            )
        except Exception as e:
            click.echo(
                click.style(f"❌ Error in {seeder.__class__.__name__}: {e}", fg="red")
            )
            success = False
            break

    if success:
        click.echo(click.style("✅ Database successfully populated.", fg="green"))


cli_command = db_seed
