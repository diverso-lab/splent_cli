import os
from pathlib import Path
import click
from jinja2 import Environment, FileSystemLoader, select_autoescape
from splent_cli.utils.path_utils import PathUtils
from splent_cli.utils.feature_utils import normalize_namespace
from splent_cli.services import context

try:
    from importlib.metadata import version as _pkg_version

    _CLI_VERSION = _pkg_version("splent_cli")
except Exception:
    _CLI_VERSION = "dev"


FEATURE_TYPES = ("full", "light", "config", "service")

FEATURE_TYPE_HELP = {
    "full": "Complete domain feature (models, routes, services, hooks, templates, migrations, tests)",
    "light": "Lightweight feature (routes, hooks, templates, tests — no models or migrations)",
    "config": "Infrastructure feature (config.py only — no routes, services, or UI)",
    "service": "Service feature (services, config, commands, signals, tests — no UI)",
}


def pascalcase(s):
    return "".join(word.capitalize() for word in s.split("_"))


def setup_jinja_env():
    env = Environment(
        loader=FileSystemLoader(searchpath=PathUtils.get_splent_cli_templates_dir()),
        autoescape=select_autoescape(["html", "xml", "j2"]),
    )
    env.filters["pascalcase"] = pascalcase
    return env


def render_and_write_file(env, template_name, filename, context):
    """Render a Jinja template and write the result to disk."""
    content = env.get_template(template_name).render(context) + "\n"
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, "w") as f:
        f.write(content)


def _build_file_map(feature_type, short_name):
    """Return (src_files, base_files) dicts for the given feature type."""
    T = "feature/"  # template prefix

    # ── Common to ALL types ──────────────────────────────────────────
    src_common = {
        "tests/conftest.py": f"{T}feature_tests_conftest.py.j2",
        "tests/unit/__init__.py": None,
        "tests/unit/test_services.py": f"{T}feature_tests_unit.py.j2",
    }
    base_common = {
        ".gitignore": f"{T}feature_.gitignore.j2",
        "MANIFEST.in": f"{T}feature_MANIFEST.in.j2",
    }

    if feature_type == "full":
        src = {
            "__init__.py": f"{T}feature_init.py.j2",
            "config.py": f"{T}feature_config.py.j2",
            "routes.py": f"{T}feature_routes.py.j2",
            "models.py": f"{T}feature_models.py.j2",
            "repositories.py": f"{T}feature_repositories.py.j2",
            "services.py": f"{T}feature_services.py.j2",
            "forms.py": f"{T}feature_forms.py.j2",
            "seeders.py": f"{T}feature_seeders.py.j2",
            "hooks.py": f"{T}feature_hooks.py.j2",
            "signals.py": f"{T}feature_signals.py.j2",
            "commands.py": f"{T}feature_commands.py.j2",
            f"templates/{short_name}/index.html": f"{T}feature_templates_index.html.j2",
            "assets/js/scripts.js": f"{T}feature_scripts.js.j2",
            "assets/js/webpack.config.js": f"{T}feature_webpack.config.js.j2",
            "tests/integration/__init__.py": None,
            "tests/integration/test_repositories.py": f"{T}feature_tests_integration.py.j2",
            "tests/functional/__init__.py": None,
            "tests/functional/test_routes.py": f"{T}feature_tests_functional.py.j2",
            "tests/e2e/__init__.py": None,
            "tests/e2e/test_browser.py": f"{T}feature_tests_e2e.py.j2",
            "tests/load/locustfile.py": f"{T}feature_tests_locustfile.py.j2",
            "migrations/env.py": f"{T}feature_migrations_env.py.j2",
            "migrations/alembic.ini": f"{T}feature_migrations_alembic.ini.j2",
            "migrations/script.py.mako": f"{T}feature_migrations_script.py.mako.j2",
            "migrations/versions/.gitkeep": None,
            "translations/.gitkeep": None,
        }
        base = {"pyproject.toml": f"{T}feature_pyproject.toml.j2"}

    elif feature_type == "light":
        src = {
            "__init__.py": f"{T}feature_init_light.py.j2",
            "routes.py": f"{T}feature_routes.py.j2",
            "hooks.py": f"{T}feature_hooks.py.j2",
            f"templates/{short_name}/index.html": f"{T}feature_templates_index.html.j2",
            "tests/functional/__init__.py": None,
            "tests/functional/test_routes.py": f"{T}feature_tests_functional.py.j2",
        }
        base = {"pyproject.toml": f"{T}feature_pyproject.toml.j2"}

    elif feature_type == "config":
        src = {
            "__init__.py": f"{T}feature_init_config.py.j2",
            "config.py": f"{T}feature_config.py.j2",
        }
        base = {"pyproject.toml": f"{T}feature_pyproject_minimal.toml.j2"}

    elif feature_type == "service":
        src = {
            "__init__.py": f"{T}feature_init_service.py.j2",
            "config.py": f"{T}feature_config.py.j2",
            "services.py": f"{T}feature_services.py.j2",
            "signals.py": f"{T}feature_signals.py.j2",
            "commands.py": f"{T}feature_commands.py.j2",
        }
        base = {"pyproject.toml": f"{T}feature_pyproject_minimal.toml.j2"}

    else:
        raise ValueError(f"Unknown feature type: {feature_type}")

    # Merge common files (type-specific wins on conflict)
    merged_src = {**src_common, **src}
    merged_base = {**base_common, **base}
    return merged_src, merged_base


@click.command(
    "feature:create",
    short_help="Create a new feature in the workspace.",
)
@click.argument("full_name")
@click.option(
    "--type",
    "feature_type",
    type=click.Choice(FEATURE_TYPES, case_sensitive=False),
    default="full",
    help="Scaffold type: full (default), light, config, or service.",
)
def make_feature(full_name, feature_type):
    """
    Creates a new feature in the workspace.
    The name must follow the pattern <namespace>/<feature_name>.

    \b
    Types:
      full     Complete domain feature (models, routes, services, hooks, templates, migrations)
      light    Lightweight feature (routes, hooks, templates — no models or migrations)
      config   Infrastructure feature (config.py only — no routes, services, or UI)
      service  Service feature (services, config, commands, signals — no UI)

    \b
    Examples:
      splent feature:create splent-io/splent_feature_billing
      splent feature:create splent-io/splent_feature_redis --type config
      splent feature:create splent-io/splent_feature_sidebar --type light
      splent feature:create splent-io/splent_feature_email_queue --type service
    """

    # --- Validate input pattern ---
    if "/" not in full_name:
        click.echo("❌ Invalid format. Use: <namespace>/<feature_name>")
        raise SystemExit(1)

    namespace, feature_name = full_name.split("/", 1)
    org_safe = normalize_namespace(namespace)

    # --- Target directory (workspace root for editable features) ---
    workspace = str(context.workspace())
    feature_dir = os.path.join(workspace, feature_name)
    src_path = os.path.join(feature_dir, "src", org_safe, feature_name)

    # --- Validation ---
    if os.path.exists(feature_dir):
        click.echo(
            click.style(
                f"⚠️  The feature '{full_name}' already exists at {feature_dir}.",
                fg="yellow",
            )
        )
        return

    # --- Jinja setup ---
    templates_dir = PathUtils.get_splent_cli_templates_dir()
    if not os.path.isdir(templates_dir):
        click.secho(
            f"❌ Templates directory not found: {templates_dir}\n"
            "   Ensure the CLI is installed correctly.",
            fg="red",
        )
        raise SystemExit(1)
    env = setup_jinja_env()
    # Derive short name: splent_feature_notes → notes
    short_name = feature_name
    if short_name.startswith("splent_feature_"):
        short_name = short_name[len("splent_feature_") :]

    # PascalCase for class names: notes_tags → NotesTags
    pascal_name = "".join(w.capitalize() for w in short_name.split("_"))

    template_ctx = {
        "feature_name": feature_name,
        "short_name": short_name,
        "pascal_name": pascal_name,
        "org_safe": org_safe,
        "feature_import": f"{org_safe}.{feature_name}",
        "cli_version": _CLI_VERSION,
    }

    # --- Build file maps for the selected type ---
    src_files, base_files = _build_file_map(feature_type, short_name)

    # --- Create src structure ---
    for filename, template in src_files.items():
        full_path = os.path.join(src_path, filename)
        if template:
            render_and_write_file(env, template, full_path, template_ctx)
        else:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            Path(full_path).touch()

    # --- Create base files ---
    for filename, template in base_files.items():
        full_path = os.path.join(feature_dir, filename)
        if template:
            render_and_write_file(env, template, full_path, template_ctx)
        else:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            Path(full_path).touch()

    # --- src/__init__.py (namespace root) ---
    src_root = os.path.join(feature_dir, "src")
    os.makedirs(src_root, exist_ok=True)
    open(os.path.join(src_root, "__init__.py"), "a").close()

    # --- Permissions (UID:GID 1000:1000) ---
    uid, gid = 1000, 1000
    chown_failed = False
    for root, dirs, files in os.walk(feature_dir):
        try:
            os.chown(root, uid, gid)
        except PermissionError:
            chown_failed = True
        for d in dirs:
            try:
                os.chown(os.path.join(root, d), uid, gid)
            except PermissionError:
                chown_failed = True
        for f in files:
            try:
                os.chown(os.path.join(root, f), uid, gid)
            except PermissionError:
                chown_failed = True

    # --- Summary ---
    click.echo()
    click.echo(
        click.style(f"  ✅ Feature '{full_name}' created ", fg="green")
        + click.style(f"({feature_type})", fg="cyan")
    )
    click.echo(click.style(f"     {feature_dir}", fg="bright_black"))
    click.echo()

    if chown_failed:
        click.secho(
            "  ⚠️  Could not set ownership 1000:1000 on some files.\n"
            "     If running outside the Docker container, this is expected.",
            fg="yellow",
        )


cli_command = make_feature
