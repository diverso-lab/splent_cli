"""
feature:translate — Extract and compile translations for a feature.

Wraps pybabel extract/init/compile with SPLENT conventions:
- Extracts from the feature's src/ directory (Python + Jinja2)
- Stores translations in the feature's translations/ directory
- Supports per-feature or all-features mode
"""

import os
import subprocess

import click

from splent_cli.services import context
from splent_cli.utils.feature_utils import (
    load_product_features,
    parse_feature_entry,
)


def _resolve_feature_src(workspace, ns_safe, name):
    """Return the feature's source directory and translations directory."""
    # Editable at workspace root
    root = os.path.join(workspace, name)
    if os.path.isdir(root):
        src = os.path.join(root, "src", ns_safe, name)
        return root, src, os.path.join(src, "translations")

    # Cache
    cache = os.path.join(workspace, ".splent_cache", "features", ns_safe, name)
    if os.path.isdir(cache):
        src = os.path.join(cache, "src", ns_safe, name)
        return cache, src, os.path.join(src, "translations")

    return None, None, None


def _run_pybabel(args, cwd):
    """Run pybabel command and return (success, output)."""
    import sys

    cmd = [
        sys.executable,
        "-c",
        "from babel.messages.frontend import main; main()",
    ] + args
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        return False, result.stderr.strip()
    return True, result.stdout.strip()


def _extract_feature(feature_root, src_dir, translations_dir, name):
    """Extract translatable strings from a feature."""
    os.makedirs(translations_dir, exist_ok=True)
    pot_file = os.path.join(translations_dir, "messages.pot")

    # Create babel.cfg if missing
    babel_cfg = os.path.join(feature_root, "babel.cfg")
    if not os.path.isfile(babel_cfg):
        with open(babel_cfg, "w") as f:
            f.write("[python: **.py]\n[jinja2: **/templates/**.html]\n")

    ok, output = _run_pybabel(
        ["extract", "-F", babel_cfg, "-o", pot_file, "src/"],
        cwd=feature_root,
    )
    if ok:
        click.echo(f"  {name}: extracted to {os.path.basename(pot_file)}")
    else:
        click.secho(f"  {name}: extract failed — {output}", fg="red")
    return ok


def _init_locale(feature_root, translations_dir, locale, name):
    """Initialize a new locale for a feature."""
    pot_file = os.path.join(translations_dir, "messages.pot")
    if not os.path.isfile(pot_file):
        click.secho(f"  {name}: no .pot file — run --extract first", fg="yellow")
        return False

    locale_dir = os.path.join(translations_dir, locale)
    if os.path.isdir(locale_dir):
        # Update existing
        ok, output = _run_pybabel(
            ["update", "-i", pot_file, "-d", translations_dir, "-l", locale],
            cwd=feature_root,
        )
        action = "updated"
    else:
        ok, output = _run_pybabel(
            ["init", "-i", pot_file, "-d", translations_dir, "-l", locale],
            cwd=feature_root,
        )
        action = "initialized"

    if ok:
        click.echo(f"  {name}: {locale} {action}")
    else:
        click.secho(f"  {name}: {locale} failed — {output}", fg="red")
    return ok


def _compile_feature(feature_root, translations_dir, name):
    """Compile .po files to .mo for a feature."""
    if not os.path.isdir(translations_dir):
        return True

    # Check if there are any .po files
    has_po = False
    for root, dirs, files in os.walk(translations_dir):
        if any(f.endswith(".po") for f in files):
            has_po = True
            break
    if not has_po:
        return True

    ok, output = _run_pybabel(
        ["compile", "-d", translations_dir],
        cwd=feature_root,
    )
    if ok:
        click.echo(f"  {name}: compiled")
    else:
        click.secho(f"  {name}: compile failed — {output}", fg="red")
    return ok


@click.command(
    "feature:translate",
    short_help="Extract, init, or compile translations for features.",
)
@click.argument("feature_ref", required=False)
@click.option(
    "--extract", "do_extract", is_flag=True, help="Extract translatable strings to .pot"
)
@click.option(
    "--init",
    "init_locale",
    default=None,
    help="Initialize a new locale (e.g. --init es)",
)
@click.option("--compile", "do_compile", is_flag=True, help="Compile .po files to .mo")
@context.requires_product
def feature_translate(feature_ref, do_extract, init_locale, do_compile):
    """Extract, initialize, or compile translations for one or all features.

    \b
    Examples:
      splent feature:translate --extract                    # all features
      splent feature:translate auth --extract               # single feature
      splent feature:translate auth --init es               # add Spanish
      splent feature:translate --compile                    # compile all
    """
    if not do_extract and not init_locale and not do_compile:
        click.secho("Specify --extract, --init <locale>, or --compile.", fg="yellow")
        return

    workspace = str(context.workspace())
    product = context.require_app()
    product_dir = os.path.join(workspace, product)

    features = load_product_features(product_dir, os.getenv("SPLENT_ENV"))
    if not features:
        click.secho("No features declared.", fg="yellow")
        return

    click.echo()

    for entry in features:
        ns_safe, name, version = parse_feature_entry(entry)

        if feature_ref:
            short = feature_ref
            if short.startswith("splent_feature_"):
                short = short[len("splent_feature_") :]
            if short not in name and f"splent_feature_{short}" != name:
                continue

        feature_root, src_dir, translations_dir = _resolve_feature_src(
            workspace, ns_safe, name
        )
        if not feature_root:
            continue

        if do_extract:
            _extract_feature(feature_root, src_dir, translations_dir, name)

        if init_locale:
            _init_locale(feature_root, translations_dir, init_locale, name)

        if do_compile:
            _compile_feature(feature_root, translations_dir, name)

    click.echo()


cli_command = feature_translate
