import os
import shutil
import stat
import tomli_w
import click
from splent_cli.services import context
from splent_cli.utils.io_utils import atomic_write, load_toml
from splent_cli.utils.feature_utils import (
    normalize_namespace,
    read_features_from_data,
    write_features_to_data,
)


def _ensure_writable(path):
    """Best-effort: make a file user-writable so an in-place rewrite of a
    read-only cache file does not fail with PermissionError."""
    try:
        mode = os.stat(path).st_mode
        os.chmod(path, mode | stat.S_IWUSR)
    except OSError:
        pass


@click.command(
    "feature:rename",
    short_help="Rename a local feature (non-versioned, non-remote only).",
)
@click.argument("old_name")
@click.argument("new_name")
@click.option(
    "--namespace", "-n", help="Namespace (defaults to GITHUB_USER or 'splent-io')."
)
@context.requires_product
def feature_rename(old_name, new_name, namespace):
    """
    Safe rename for local, non-versioned, non-remote features.
    - Validates namespace and cache existence
    - Renames folder and src structure
    - Updates pyproject + symlink ONLY if feature is active
    - Prints final summary
    """

    # -----------------------------
    # Namespace resolution
    # -----------------------------
    github_user = os.getenv("GITHUB_USER")
    org = namespace or github_user or "splent-io"
    org_safe = normalize_namespace(org)

    workspace = str(context.workspace())
    cache_root = os.path.join(workspace, ".splent_cache", "features", org_safe)
    old_dir = os.path.join(cache_root, old_name)
    new_dir = os.path.join(cache_root, new_name)

    # -----------------------------
    # Validations
    # -----------------------------
    if not os.path.exists(old_dir):
        click.echo(
            click.style(
                f"❌ Feature '{old_name}' not found in namespace '{org_safe}'.",
                fg="red",
            )
        )
        raise SystemExit(1)

    if os.path.exists(new_dir):
        click.echo(
            click.style(
                f"⚠️  A feature named '{new_name}' already exists in '{org_safe}'.",
                fg="yellow",
            )
        )
        raise SystemExit(1)

    if "@" in old_name or "@" in new_name:
        click.echo(
            click.style("❌ Versioned features cannot be renamed manually.", fg="red")
        )
        raise SystemExit(1)

    if os.path.exists(os.path.join(old_dir, ".git")):
        click.echo(
            click.style(
                "❌ Feature is linked to a Git repository; cannot rename.", fg="red"
            )
        )
        raise SystemExit(1)

    # -----------------------------
    # Context
    # -----------------------------
    splent_app = os.getenv(
        "SPLENT_APP"
    )  # optional — used to update pyproject.toml if set
    pyproject_path = None
    features_list = []
    feature_is_active = False

    if splent_app:
        pyproject_path = os.path.join(workspace, splent_app, "pyproject.toml")
        if os.path.exists(pyproject_path):
            try:
                data = load_toml(pyproject_path, what="pyproject.toml")
                features_list = read_features_from_data(data)
                if old_name in features_list:
                    feature_is_active = True
            except click.ClickException as e:
                click.echo(
                    click.style(
                        f"⚠️  Could not read pyproject.toml: {e.format_message()}",
                        fg="yellow",
                    )
                )

    # -----------------------------
    # Rename in cache
    # -----------------------------
    click.echo(
        f"🚚 Renaming feature '{old_name}' → '{new_name}' in namespace '{org_safe}'..."
    )

    # The folder move + in-place rewrites below mutate the cache as a unit. If
    # any step fails mid-way (e.g. a read-only cache file), undo the directory
    # move so we never leave a half-renamed feature behind. If even the rollback
    # fails, tell the user exactly what to fix by hand.
    moved = False
    try:
        shutil.move(old_dir, new_dir)
        moved = True

        old_src = os.path.join(new_dir, "src", org_safe, old_name)
        new_src = os.path.join(new_dir, "src", org_safe, new_name)
        if os.path.exists(old_src):
            os.rename(old_src, new_src)

        # -----------------------------
        # Update imports & templates
        # -----------------------------
        modified_files = 0
        for root, _, files in os.walk(new_dir):
            for file in files:
                if not file.endswith((".py", ".html", ".toml", ".js")):
                    continue
                path = os.path.join(root, file)
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                new_content = content.replace(
                    f"{org_safe}.{old_name}", f"{org_safe}.{new_name}"
                )
                new_content = new_content.replace(
                    f"templates/{old_name}/", f"templates/{new_name}/"
                )
                if new_content != content:
                    # Cache files may be read-only; make writable before rewrite.
                    _ensure_writable(path)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    modified_files += 1
    except Exception as e:
        if moved and os.path.exists(new_dir) and not os.path.exists(old_dir):
            try:
                shutil.move(new_dir, old_dir)
                click.echo(
                    click.style(
                        f"❌ Rename failed ({e}); rolled back to '{old_name}'.",
                        fg="red",
                    )
                )
            except Exception as rb:
                click.echo(
                    click.style(
                        f"❌ Rename failed ({e}) and rollback failed ({rb}).\n"
                        f"   Manual cleanup needed: move '{new_dir}' back to "
                        f"'{old_dir}'.",
                        fg="red",
                    )
                )
        else:
            click.echo(click.style(f"❌ Rename failed: {e}", fg="red"))
        raise SystemExit(1)

    # -----------------------------
    # Update symlink + pyproject if active
    # -----------------------------
    symlink_updated = False
    pyproject_updated = False

    if feature_is_active and splent_app and pyproject_path:
        product_features_dir = os.path.join(workspace, splent_app, "features", org_safe)
        old_link = os.path.join(product_features_dir, old_name)
        new_link = os.path.join(product_features_dir, new_name)

        # Update symlink
        if os.path.islink(old_link):
            os.unlink(old_link)
            rel_target = os.path.relpath(new_dir, product_features_dir)
            os.symlink(rel_target, new_link)
            symlink_updated = True

        # Update pyproject
        updated_features = [new_name if f == old_name else f for f in features_list]
        try:
            data = load_toml(pyproject_path, what="pyproject.toml")
            write_features_to_data(data, updated_features)
            atomic_write(pyproject_path, tomli_w.dumps(data))
            pyproject_updated = True
        except Exception as e:
            msg = e.format_message() if isinstance(e, click.ClickException) else e
            click.echo(
                click.style(f"⚠️  Could not update pyproject.toml: {msg}", fg="yellow")
            )

    # -----------------------------
    # Summary
    # -----------------------------
    click.echo()
    click.echo(click.style("✅ Rename complete!", fg="green"))
    click.echo(click.style(f"📦 New path: {new_dir}", fg="blue"))
    click.echo(click.style(f"🏷️  Namespace: {org_safe}", fg="bright_black"))
    if splent_app:
        click.echo(click.style(f"🧩 Product: {splent_app}", fg="bright_black"))

    click.echo()
    click.echo(click.style("📊 Summary:", fg="bright_white"))
    click.echo(f"   ✏️  Modified files: {modified_files}")
    click.echo(f"   🔗 Symlink updated: {'✅' if symlink_updated else '—'}")
    click.echo(f"   🗂️  pyproject.toml updated: {'✅' if pyproject_updated else '—'}")
    click.echo(f"   ⚙️  Feature was active: {'✅' if feature_is_active else '❌'}")


cli_command = feature_rename
