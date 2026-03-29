import os

import click
from flask import current_app
from flask_migrate import downgrade as alembic_downgrade

from splent_cli.utils.decorators import requires_db
from splent_cli.utils.lifecycle import advance_state, resolve_feature_key_from_entry
from splent_framework.managers.migration_manager import MigrationManager
from splent_framework.managers.feature_order import FeatureLoadOrderResolver
from splent_framework.utils.feature_utils import get_features_from_pyproject
from splent_framework.utils.path_utils import PathUtils
from splent_framework.utils.pyproject_reader import PyprojectReader


def _find_dependents(feature: str, product_dir: str) -> list[str]:
    """Find features that depend on `feature` via UVL constraints.

    Uses FeatureLoadOrderResolver's UVL parsing (same logic as feature:order).
    Returns list of dependent feature package names.
    """
    try:
        reader = PyprojectReader.for_product(product_dir)
        workspace = PathUtils.get_working_dir()

        # Resolve UVL path (catalog first, legacy fallback)
        uvl_path = None
        spl_name = reader.splent_config.get("spl")
        if spl_name:
            candidate = os.path.join(workspace, "splent_catalog", spl_name, f"{spl_name}.uvl")
            if os.path.isfile(candidate):
                uvl_path = candidate
        if not uvl_path:
            uvl_file = reader.uvl_config.get("file")
            if uvl_file:
                uvl_path = os.path.join(product_dir, "uvl", uvl_file)

        if not uvl_path or not os.path.isfile(uvl_path):
            return []

        with open(uvl_path) as f:
            uvl_text = f.read()

        # Use the framework's UVL parser (same as feature:order)
        package_map = FeatureLoadOrderResolver._parse_package_map(uvl_text)
        constraints = FeatureLoadOrderResolver._parse_constraints(uvl_text)
        pkg_to_short = {v: k for k, v in package_map.items()}

        target_short = pkg_to_short.get(feature)
        if not target_short:
            return []

        # A => target means A depends on target
        return [
            package_map[src]
            for src, dst in constraints
            if dst == target_short and src in package_map
        ]
    except Exception:
        return []


@requires_db
@click.command(
    "db:rollback",
    short_help="Roll back migrations for a feature.",
)
@click.argument("feature")
@click.option(
    "--steps", default=1, show_default=True, help="Number of migrations to roll back."
)
@click.option(
    "--cascade", is_flag=True, help="Also rollback dependent features."
)
def db_rollback(feature, steps, cascade):
    app = current_app
    product_path = PathUtils.get_app_base_dir()
    product_name = os.getenv("SPLENT_APP", "")

    # Verify feature exists in the product
    declared = [e for e in (get_features_from_pyproject() or [])]
    feature_found = any(feature in e for e in declared)
    if not feature_found:
        click.secho(f"❌ Feature '{feature}' is not declared in this product.", fg="red")
        raise SystemExit(1)

    mdir = MigrationManager.get_feature_migration_dir(feature)
    if not mdir or not os.path.isdir(mdir):
        click.secho(f"  ℹ️  Feature '{feature}' has no migrations — nothing to roll back.", fg="yellow")
        return

    # Check for dependent features
    dependents = _find_dependents(feature, product_path)
    # Filter to only those with applied migrations
    migrated_dependents = []
    for dep in dependents:
        dep_rev = MigrationManager.get_current_feature_revision(
            dep, app.extensions["migrate"].db.engine
        )
        if dep_rev is not None:
            migrated_dependents.append(dep)

    if migrated_dependents:
        click.secho(
            f"  ⚠️  These features depend on {feature} and have applied migrations:",
            fg="yellow",
        )
        for dep in migrated_dependents:
            click.echo(f"     • {dep}")
        click.echo()

        if not cascade:
            if not click.confirm(
                "  Roll back dependents first, then this feature?", default=False
            ):
                click.echo("  ❎ Cancelled.")
                raise SystemExit(1)

        # Rollback dependents first (reverse order)
        for dep in reversed(migrated_dependents):
            dep_mdir = MigrationManager.get_feature_migration_dir(dep)
            if dep_mdir:
                click.echo(
                    click.style(f"  ⬇️  Rolling back {dep} (dependent)...", fg="cyan")
                )
                try:
                    alembic_downgrade(directory=dep_mdir, revision=f"-{steps}")
                    dep_rev = MigrationManager.get_current_feature_revision(
                        dep, app.extensions["migrate"].db.engine
                    )
                    MigrationManager.update_feature_status(app, dep, dep_rev)
                    click.secho(f"  ✅ {dep} → {dep_rev or 'base'}", fg="green")

                    for entry in get_features_from_pyproject() or []:
                        key, ns, name, version = resolve_feature_key_from_entry(entry)
                        if name == dep:
                            target = "installed" if dep_rev is None else "migrated"
                            advance_state(
                                product_path, product_name, key,
                                to=target, namespace=ns, name=name, version=version,
                            )
                            break
                except Exception as e:
                    click.secho(f"  ❌ {dep}: {e}", fg="red")
                    raise SystemExit(1)

    # Rollback the target feature
    click.echo(
        click.style(f"  ⬇️  Rolling back {steps} step(s) for {feature}...", fg="cyan")
    )
    try:
        alembic_downgrade(directory=mdir, revision=f"-{steps}")
        revision = MigrationManager.get_current_feature_revision(
            feature, app.extensions["migrate"].db.engine
        )
        MigrationManager.update_feature_status(app, feature, revision)
        click.secho(f"  ✅ {feature} → {revision or 'base'}", fg="green")

        for entry in get_features_from_pyproject() or []:
            key, ns, name, version = resolve_feature_key_from_entry(entry)
            if name == feature:
                target = "installed" if revision is None else "migrated"
                advance_state(
                    product_path, product_name, key,
                    to=target, namespace=ns, name=name, version=version,
                )
                break
    except Exception as e:
        click.secho(f"  ❌ {feature}: {e}", fg="red")


cli_command = db_rollback
