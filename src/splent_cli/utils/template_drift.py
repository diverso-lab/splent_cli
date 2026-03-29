"""
Shared utilities for template drift detection and sync (product:drift,
product:sync-template, feature:drift, feature:sync-template).
"""

import difflib
import os
import zlib
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

try:
    from importlib.metadata import version as _pkg_version

    CLI_VERSION = _pkg_version("splent_cli")
except Exception:
    CLI_VERSION = "dev"


# ── Jinja helpers ─────────────────────────────────────────────────────────────


def _pascalcase(s: str) -> str:
    return "".join(word.capitalize() for word in s.split("_"))


def setup_jinja_env() -> Environment:
    from splent_cli.utils.path_utils import PathUtils

    env = Environment(
        loader=FileSystemLoader(searchpath=PathUtils.get_splent_cli_templates_dir()),
        autoescape=select_autoescape(["html", "xml", "j2"]),
    )
    env.filters["pascalcase"] = _pascalcase
    return env


def render_template(template_name: str, ctx: dict) -> str:
    """Render a Jinja template and return the result as a string."""
    env = setup_jinja_env()
    return env.get_template(template_name).render(ctx) + "\n"


# ── Template context builders ─────────────────────────────────────────────────


def product_ctx(product_name: str) -> dict:
    offset = zlib.crc32(product_name.encode("utf-8")) % 1000

    # Read spl from product's pyproject if available
    spl_name = ""
    try:
        import tomllib

        pyproject = os.path.join(
            os.getenv("WORKING_DIR", "/workspace"), product_name, "pyproject.toml"
        )
        if os.path.isfile(pyproject):
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
            spl_name = data.get("tool", {}).get("splent", {}).get("spl", "")
    except Exception:
        pass

    return {
        "product_name": product_name,
        "pascal_name": _pascalcase(product_name),
        "web_port": 5000 + offset,
        "db_port": 33060 + offset,
        "redis_port": 6379 + offset,
        "mailhog_port_one": 8025 + offset,
        "mailhog_port_two": 1025 + offset,
        "cli_version": CLI_VERSION,
        "network_name": "splent_network",
        "spl_name": spl_name,
    }


def feature_ctx(org_safe: str, feature_name: str) -> dict:
    short_name = feature_name
    if short_name.startswith("splent_feature_"):
        short_name = short_name[len("splent_feature_"):]
    return {
        "feature_name": feature_name,
        "short_name": short_name,
        "org_safe": org_safe,
        "feature_import": f"{org_safe}.{feature_name}",
        "cli_version": CLI_VERSION,
    }


# ── Diff helper ───────────────────────────────────────────────────────────────


def file_diff(path: Path, expected: str) -> list[str] | None:
    """Return unified diff lines if file differs from expected, else None."""
    if not path.exists():
        return None
    current = path.read_text(encoding="utf-8", errors="replace")
    if current == expected:
        return None
    return list(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile=f"{path.name} (current)",
            tofile=f"{path.name} (template)",
        )
    )


def count_changed_lines(diff: list[str]) -> int:
    return len(
        [
            line
            for line in diff
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
        ]
    )


# ── Stored version reader ─────────────────────────────────────────────────────


def get_stored_cli_version(pyproject_path: Path) -> str | None:
    """Read the cli_version stored in [tool.splent] from a pyproject.toml."""
    try:
        import tomllib

        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        return data.get("tool", {}).get("splent", {}).get("cli_version")
    except Exception:
        return None


# ── Product file groups ───────────────────────────────────────────────────────
# Maps relative path (from product root) → Jinja template name.
# {name} in the key is replaced with the actual product name at runtime.

PRODUCT_GROUPS: dict[str, dict[str, str]] = {
    "scripts": {
        "scripts/00_core_requirements_dev.sh": "product/product_00_core_requirements_dev.sh.j2",
        "scripts/00_install_features.sh": "product/product_00_install_features.sh.j2",
        "scripts/01_compile_assets.sh": "product/product_01_compile_assets.sh.j2",
        "scripts/02_0_db_wait_connection.sh": "product/product_02_0_db_wait_connection.sh.j2",
        "scripts/02_1_db_create_db_test.sh": "product/product_02_1_db_create_db_test.sh.j2",
        "scripts/02_2_db_create_splent_migrations.sh": "product/product_02_2_db_create_splent_migrations.sh.j2",
        "scripts/03_initialize_migrations.sh": "product/product_03_initialize_migrations.sh.j2",
        "scripts/04_handle_migrations.sh": "product/product_04_handle_migrations.sh.j2",
        "scripts/05_0_start_app_dev.sh": "product/product_05_0_start_app_dev.sh.j2",
        "scripts/05_1_start_app_prod.sh": "product/product_05_1_start_app_prod.sh.j2",
    },
    "entrypoints": {
        "entrypoints/entrypoint.dev.sh": "product/product_entrypoint.dev.sh.j2",
        "entrypoints/entrypoint.prod.sh": "product/product_entrypoint.prod.sh.j2",
    },
    "docker": {
        "docker/Dockerfile.{name}.dev": "product/product_Dockerfile.dev.j2",
        "docker/Dockerfile.{name}.prod": "product/product_Dockerfile.prod.j2",
        "docker/.env.dev.example": "product/product_.env.dev.example.j2",
        "docker/.env.prod.example": "product/product_.env.prod.example.j2",
    },
}

GROUP_LABELS = {
    "scripts": "scripts/",
    "entrypoints": "entrypoints/",
    "docker": "docker/",
}


def resolve_product_rel(rel_tpl: str, product_name: str) -> str:
    return rel_tpl.replace("{name}", product_name)


# ── Feature file groups ───────────────────────────────────────────────────────
# Maps relative path (from feature cache root) → Jinja template name.
# {org} and {name} are replaced at runtime.

FEATURE_FILES: dict[str, str] = {
    ".gitignore": "feature/feature_.gitignore.j2",
    "MANIFEST.in": "feature/feature_MANIFEST.in.j2",
    "src/{org}/{name}/assets/js/webpack.config.js": "feature/feature_webpack.config.js.j2",
}


def resolve_feature_rel(rel_tpl: str, org_safe: str, feature_name: str) -> str:
    return rel_tpl.replace("{org}", org_safe).replace("{name}", feature_name)
