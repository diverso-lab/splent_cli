import os
import stat
import click
from pathlib import Path
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
    content = env.get_template(template_name).render(context) + "\n"
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, "w") as f:
        f.write(content)


@click.command("feature:create", help="Creates a new feature with a given name.")
@click.argument("name")
def make_feature(name):
    feature_name = f"splent_feature_{name}"
    base_path = os.path.join(PathUtils.get_working_dir(), feature_name)
    src_path = os.path.join(base_path, "src", feature_name)

    if os.path.exists(base_path):
        click.echo(click.style(f"The feature '{feature_name}' already exists.", fg="red"))
        return

    env = setup_jinja_env()
    context = {"feature_name": name}

    # Archivos que van dentro de src/splent_feature_<name>
    src_files_and_templates = {
        "__init__.py": "feature/feature_init.py.j2",
        "routes.py": "feature/feature_routes.py.j2",
        "models.py": "feature/feature_models.py.j2",
        "repositories.py": "feature/feature_repositories.py.j2",
        "services.py": "feature/feature_services.py.j2",
        "forms.py": "feature/feature_forms.py.j2",
        "seeders.py": "feature/feature_seeders.py.j2",
        os.path.join("templates", name, "index.html"): "feature/feature_templates_index.html.j2",
        os.path.join("assets", "js", "scripts.js"): "feature/feature_scripts.js.j2",
        os.path.join("assets", "js", "webpack.config.js"): "feature/feature_webpack.config.js.j2",
        os.path.join("tests", "__init__.py"): None,
        os.path.join("tests", "test_unit.py"): "feature/feature_tests_test_unit.py.j2",
        os.path.join("tests", "locustfile.py"): "feature/feature_tests_locustfile.py.j2",
        os.path.join("tests", "test_selenium.py"): "feature/feature_tests_test_selenium.py.j2",
    }
    
    # Archivos que van en la raíz de splent_feature_<name>
    base_files_and_templates = {
        ".gitignore": "feature/feature_.gitignore.j2",
        "pyproject.toml": "feature/feature_pyproject.toml.j2",
        "MANIFEST.in": "feature/feature_MANIFEST.in.j2"
    }

    # Crear todos los archivos de código en src_path
    for filename, template in src_files_and_templates.items():
        full_path = os.path.join(src_path, filename)
        if template:
            render_and_write_file(env, template, full_path, context)
        else:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            open(full_path, "a").close()
            
    # Crear todos los archivos de código en base_path
    for filename, template in base_files_and_templates.items():
        full_path = os.path.join(base_path, filename)
        if template:
            render_and_write_file(env, template, full_path, context)
        else:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            open(full_path, "a").close()

    # Crear src/__init__.py vacío
    src_root = os.path.join(base_path, "src")
    os.makedirs(src_root, exist_ok=True)
    open(os.path.join(src_root, "__init__.py"), "a").close()

    # Cambiar permisos y propietario
    uid = 1000
    gid = 1000

    os.chown(base_path, uid, gid)
    os.chmod(base_path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IROTH | stat.S_IXOTH)

    for root, dirs, files in os.walk(base_path):
        for d in dirs:
            dir_path = os.path.join(root, d)
            os.chown(dir_path, uid, gid)
            os.chmod(dir_path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IROTH | stat.S_IXOTH)
        for f in files:
            file_path = os.path.join(root, f)
            os.chown(file_path, uid, gid)
            os.chmod(
                file_path,
                stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH,
            )

    click.echo(click.style(f"Feature '{feature_name}' created successfully in {base_path}", fg="green"))
