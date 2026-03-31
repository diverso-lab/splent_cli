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


@click.command(
    "feature:create",
    short_help="Create a new feature in the local cache.",
    help="Creates a new feature locally in the cache using the pattern <namespace>/<feature_name>.",
)
@click.argument("full_name")
def make_feature(full_name):
    """
    Creates a new feature locally in the SPLENT cache.
    The name must follow the pattern <namespace>/<feature_name>.

    Example:
      splent feature:create drorganvidez/notepad
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

    template_ctx = {
        "feature_name": feature_name,
        "short_name": short_name,
        "org_safe": org_safe,
        "feature_import": f"{org_safe}.{feature_name}",
        "cli_version": _CLI_VERSION,
    }

    # --- File mappings ---
    src_files_and_templates = {
        "__init__.py": "feature/feature_init.py.j2",
        "config.py": "feature/feature_config.py.j2",
        "routes.py": "feature/feature_routes.py.j2",
        "models.py": "feature/feature_models.py.j2",
        "repositories.py": "feature/feature_repositories.py.j2",
        "services.py": "feature/feature_services.py.j2",
        "forms.py": "feature/feature_forms.py.j2",
        "seeders.py": "feature/feature_seeders.py.j2",
        "hooks.py": "feature/feature_hooks.py.j2",
        "signals.py": "feature/feature_signals.py.j2",
        os.path.join(
            "templates", short_name, "index.html"
        ): "feature/feature_templates_index.html.j2",
        os.path.join("assets", "js", "scripts.js"): "feature/feature_scripts.js.j2",
        os.path.join(
            "assets", "js", "webpack.config.js"
        ): "feature/feature_webpack.config.js.j2",
        # Test structure
        os.path.join("tests", "conftest.py"): "feature/feature_tests_conftest.py.j2",
        os.path.join("tests", "unit", "__init__.py"): None,
        os.path.join(
            "tests", "unit", "test_services.py"
        ): "feature/feature_tests_unit.py.j2",
        os.path.join("tests", "integration", "__init__.py"): None,
        os.path.join(
            "tests", "integration", "test_repositories.py"
        ): "feature/feature_tests_integration.py.j2",
        os.path.join("tests", "functional", "__init__.py"): None,
        os.path.join(
            "tests", "functional", "test_routes.py"
        ): "feature/feature_tests_functional.py.j2",
        os.path.join("tests", "e2e", "__init__.py"): None,
        os.path.join(
            "tests", "e2e", "test_browser.py"
        ): "feature/feature_tests_e2e.py.j2",
        os.path.join(
            "tests", "load", "locustfile.py"
        ): "feature/feature_tests_locustfile.py.j2",
        # Migrations scaffold
        os.path.join("migrations", "env.py"): "feature/feature_migrations_env.py.j2",
        os.path.join(
            "migrations", "alembic.ini"
        ): "feature/feature_migrations_alembic.ini.j2",
        os.path.join(
            "migrations", "script.py.mako"
        ): "feature/feature_migrations_script.py.mako.j2",
        os.path.join("migrations", "versions", ".gitkeep"): None,
        # Translations scaffold
        os.path.join("translations", ".gitkeep"): None,
    }

    base_files_and_templates = {
        ".gitignore": "feature/feature_.gitignore.j2",
        "pyproject.toml": "feature/feature_pyproject.toml.j2",
        "MANIFEST.in": "feature/feature_MANIFEST.in.j2",
    }

    # --- Create src structure ---
    for filename, template in src_files_and_templates.items():
        full_path = os.path.join(src_path, filename)
        if template:
            render_and_write_file(env, template, full_path, template_ctx)
        else:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            Path(full_path).touch()

    # --- Create base files ---
    for filename, template in base_files_and_templates.items():
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

    click.echo(
        click.style(f"✅ Feature '{full_name}' created successfully!", fg="green")
    )
    click.echo(click.style(f"📦 Created at: {feature_dir}", fg="blue"))
    click.echo(click.style(f"🏷️  Namespace: {org_safe}", fg="bright_black"))

    if chown_failed:
        click.secho(
            "⚠️  Could not set ownership 1000:1000 on some files.\n"
            "   If running outside the Docker container, this is expected and harmless.",
            fg="yellow",
        )


cli_command = make_feature
