import os
import shutil
import stat
import click
import zlib
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

def copy_raw_file(template_name, filename):
    src = os.path.join(PathUtils.get_splent_cli_templates_dir(), template_name)
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    shutil.copy(src, filename)

@click.command("product:create", help="Creates a new product with a given name.")
@click.argument("name")
@click.option("--features-file", type=click.Path(exists=True), help="Path to features.txt")
def make_product(name, features_file):
    env = setup_jinja_env()
    offset = zlib.crc32(name.encode("utf-8")) % 1000  # 0–999
    web_port = 5000 + offset
    db_port = 33060 + offset
    redis_port = 6379 + offset
    context = {
        "product_name": name,
        "pascal_name": pascalcase(name),
        "web_port": web_port,
        "db_port": db_port,
        "redis_port": redis_port
    }

    base_path = os.path.join(PathUtils.get_working_dir(), name)

    if os.path.exists(base_path):
        click.echo(click.style(f"The product '{name}' already exists.", fg="red"))
        return

    for subdir in [
        "docker", "entrypoints", "scripts", f"src/{name}",
        f"src/{name}/static", f"src/{name}/static/css",
        f"src/{name}/static/fonts", f"src/{name}/static/js",
        f"src/{name}/templates"
    ]:
        os.makedirs(os.path.join(base_path, subdir), exist_ok=True)

    open(os.path.join(base_path, "src", "__init__.py"), "a").close()

    jinja_templates = {
        "docker/.env.dev.example": "product/product_.env.dev.example.j2",
        "docker/.env.prod.example": "product/product_.env.prod.example.j2",
        "docker/docker-compose.dev.yml": "product/product_docker-compose.dev.yml.j2",
        "docker/docker-compose.prod.yml": "product/product_docker-compose.prod.yml.j2",
        f"docker/Dockerfile.{name}.dev": "product/product_Dockerfile.dev.j2",
        f"docker/Dockerfile.{name}.prod": "product/product_Dockerfile.prod.j2",
        "entrypoints/entrypoint.dev.sh": "product/product_entrypoint.dev.sh.j2",
        "entrypoints/entrypoint.prod.sh": "product/product_entrypoint.prod.sh.j2",
        "scripts/00_core_requirements_dev.sh": "product/product_00_core_requirements_dev.sh.j2",
        "scripts/00_install_features.sh": "product/product_00_install_features.sh.j2",
        "scripts/01_compile_assets.sh": "product/product_01_compile_assets.sh.j2",
        "scripts/02_0_db_wait_connection.sh": "product/product_02_0_db_wait_connection.sh.j2",
        "scripts/02_1_db_create_db_test.sh": "product/product_02_1_db_create_db_test.sh.j2",
        "scripts/03_initialize_migrations.sh": "product/product_03_initialize_migrations.sh.j2",
        "scripts/04_handle_migrations.sh": "product/product_04_handle_migrations.sh.j2",
        "scripts/05_0_start_app_dev.sh": "product/product_05_0_start_app_dev.sh.j2",
        "scripts/05_1_start_app_prod.sh": "product/product_05_1_start_app_prod.sh.j2",
        ".gitignore": "product/product_.gitignore.j2",
        "LICENSE": "product/product_LICENSE.j2",
        "package.json": "product/product_package.json.j2",
        "pyproject.toml": "product/product_pyproject.toml.j2",
        "README.md": "product/product_README.md.j2",
        f"src/{name}/__init__.py": "product/product_init.py.j2",
        f"src/{name}/config.py": "product/product_config.py.j2",
        f"src/{name}/errors.py": "product/product_errors.py.j2",
        f"src/{name}/logging.py": "product/product_logging.py.j2",
    }

    raw_files = {
        f"src/{name}/static/css/app.css": "product/product_app.css",
        f"src/{name}/static/css/dropzone.css": "product/product_dropzone.css",
        f"src/{name}/static/css/own.css": "product/product_own.css",
        f"src/{name}/static/js/app.js": "product/product_app.js",
        f"src/{name}/templates/400.html": "product/product_400.html",
        f"src/{name}/templates/401.html": "product/product_401.html",
        f"src/{name}/templates/404.html": "product/product_404.html",
        f"src/{name}/templates/500.html": "product/product_500.html",
        f"src/{name}/templates/base_template.html": "product/product_base_template.html",
    }

    for rel_path, tpl in jinja_templates.items():
        abs_path = os.path.join(base_path, rel_path)
        render_and_write_file(env, tpl, abs_path, context)

    for rel_path, tpl in raw_files.items():
        abs_path = os.path.join(base_path, rel_path)
        copy_raw_file(tpl, abs_path)

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

    click.echo(click.style(f"✅ Product '{name}' created successfully in {base_path}", fg="green"))
