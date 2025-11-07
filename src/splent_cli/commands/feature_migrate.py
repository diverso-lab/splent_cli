import os
import shutil
import click

@click.command("feature:migrate-structure", help="Nest feature code under its organization (e.g. src/splent_io/feature/)")
@click.option("--workspace", default="/workspace", help="Workspace base path (default: /workspace)")
def migrate_feature_structure(workspace):
    base_cache = os.path.join(workspace, ".splent_cache", "features")
    if not os.path.exists(base_cache):
        click.echo(f"❌ No cache folder found at {base_cache}")
        return

    migrated = []
    skipped = []

    for org in os.listdir(base_cache):
        org_path = os.path.join(base_cache, org)
        if not os.path.isdir(org_path):
            continue

        for feature_dir in os.listdir(org_path):
            feature_path = os.path.join(org_path, feature_dir)
            if not os.path.isdir(feature_path):
                continue

            src_path = os.path.join(feature_path, "src")
            if not os.path.exists(src_path):
                continue

            org_subdir = os.path.join(src_path, org)
            if os.path.exists(org_subdir):
                skipped.append(f"{org}/{feature_dir}")
                continue

            # Detectar carpeta raíz de código dentro de src/
            inner_dirs = [
                d for d in os.listdir(src_path)
                if os.path.isdir(os.path.join(src_path, d)) and not d.startswith("__")
            ]

            if len(inner_dirs) != 1:
                click.echo(f"⚠️  Ambiguous src structure in {org}/{feature_dir}, skipping")
                continue

            feature_name = inner_dirs[0]
            old_feature_path = os.path.join(src_path, feature_name)
            new_org_path = os.path.join(src_path, org)
            new_feature_path = os.path.join(new_org_path, feature_name)

            click.echo(f"♻️  Moving {org}/{feature_dir}: {feature_name}/ → {org}/{feature_name}/")

            try:
                os.makedirs(new_org_path, exist_ok=True)
                shutil.move(old_feature_path, new_feature_path)
                migrated.append(f"{org}/{feature_dir}")
            except Exception as e:
                click.echo(f"❌ Error migrating {org}/{feature_dir}: {e}")

    if migrated:
        click.echo(f"\n✅ Structure migration complete: {len(migrated)} feature(s) reorganized")
        for f in migrated:
            click.echo(f"   • {f}")
    else:
        click.echo("\nℹ️  No features required structure migration.")

    if skipped:
        click.echo(f"\n⚠️  Skipped (already migrated): {', '.join(skipped)}")
