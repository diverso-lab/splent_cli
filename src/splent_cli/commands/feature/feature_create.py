import os
import click
from jinja2 import Environment, FileSystemLoader, select_autoescape
from splent_cli.utils.path_utils import PathUtils


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
    """Renderiza una plantilla Jinja y la escribe en disco."""
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
    org_safe = namespace.replace("-", "_")

    # --- Target directory (local cache) ---
    workspace = PathUtils.get_working_dir()
    cache_dir = os.path.join(
        workspace, ".splent_cache", "features", org_safe, feature_name
    )
    src_path = os.path.join(cache_dir, "src", org_safe, feature_name)

    # --- Validation ---
    if os.path.exists(cache_dir):
        click.echo(
            click.style(f"⚠️  The feature '{full_name}' already exists.", fg="yellow")
        )
        return

    # --- Jinja setup ---
    env = setup_jinja_env()
    context = {
        "feature_name": feature_name,
        "org_safe": org_safe,
        "feature_import": f"{org_safe}.{feature_name}",
    }

    # --- File mappings ---
    src_files_and_templates = {
        "__init__.py": "feature/feature_init.py.j2",
        "routes.py": "feature/feature_routes.py.j2",
        "models.py": "feature/feature_models.py.j2",
        "repositories.py": "feature/feature_repositories.py.j2",
        "services.py": "feature/feature_services.py.j2",
        "forms.py": "feature/feature_forms.py.j2",
        "seeders.py": "feature/feature_seeders.py.j2",
        os.path.join(
            "templates", feature_name, "index.html"
        ): "feature/feature_templates_index.html.j2",
        os.path.join("assets", "js", "scripts.js"): "feature/feature_scripts.js.j2",
        os.path.join(
            "assets", "js", "webpack.config.js"
        ): "feature/feature_webpack.config.js.j2",
        os.path.join("tests", "__init__.py"): None,
        os.path.join("tests", "test_unit.py"): "feature/feature_tests_test_unit.py.j2",
        os.path.join(
            "tests", "locustfile.py"
        ): "feature/feature_tests_locustfile.py.j2",
        os.path.join(
            "tests", "test_selenium.py"
        ): "feature/feature_tests_test_selenium.py.j2",
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
            render_and_write_file(env, template, full_path, context)
        else:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            open(full_path, "a").close()

    # --- Create base files ---
    for filename, template in base_files_and_templates.items():
        full_path = os.path.join(cache_dir, filename)
        if template:
            render_and_write_file(env, template, full_path, context)
        else:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            open(full_path, "a").close()

    # --- src/__init__.py (namespace root) ---
    src_root = os.path.join(cache_dir, "src")
    os.makedirs(src_root, exist_ok=True)
    open(os.path.join(src_root, "__init__.py"), "a").close()

    # --- Permissions (UID:GID 1000:1000) ---
    uid, gid = 1000, 1000
    for root, dirs, files in os.walk(cache_dir):
        os.chown(root, uid, gid)
        for d in dirs:
            os.chown(os.path.join(root, d), uid, gid)
        for f in files:
            os.chown(os.path.join(root, f), uid, gid)

    click.echo(
        click.style(f"✅ Feature '{full_name}' created successfully!", fg="green")
    )
    click.echo(click.style(f"📦 Cached at: {cache_dir}", fg="blue"))
    click.echo(click.style(f"🏷️  Namespace: {org_safe}", fg="bright_black"))


cli_command = make_feature
