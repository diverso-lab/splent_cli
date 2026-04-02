"""
splent feature:refinement

Interactive wizard to configure refinement for a feature.
Reads the base feature's contract, presents extensible points,
and generates the [tool.splent.refinement] section in pyproject.toml.
"""

import os
import re
import tomllib

import click

from splent_cli.services import context
from splent_cli.utils.feature_utils import normalize_namespace, read_features_from_data


DEFAULT_NAMESPACE = os.getenv("SPLENT_DEFAULT_NAMESPACE", "splent_io")


# ── Helpers ──────────────────────────────────────────────────────────────


def _read_extensible_contract(feature_path: str) -> dict:
    """Read the extensible section from a feature's contract."""
    pyproject = os.path.join(feature_path, "pyproject.toml")
    if not os.path.isfile(pyproject):
        return {}
    with open(pyproject, "rb") as f:
        data = tomllib.load(f)
    ext = (
        data.get("tool", {}).get("splent", {}).get("contract", {}).get("extensible", {})
    )
    return {
        "services": ext.get("services", []),
        "templates": ext.get("templates", []),
        "models": ext.get("models", []),
        "hooks": ext.get("hooks", []),
        "routes": ext.get("routes", False),
    }


def _read_provides(feature_path: str) -> dict:
    """Read what a feature provides from its contract."""
    pyproject = os.path.join(feature_path, "pyproject.toml")
    if not os.path.isfile(pyproject):
        return {}
    with open(pyproject, "rb") as f:
        data = tomllib.load(f)
    provides = (
        data.get("tool", {}).get("splent", {}).get("contract", {}).get("provides", {})
    )
    return {
        "services": provides.get("services", []),
        "templates": provides.get("templates", []),
        "models": provides.get("models", []),
        "hooks": provides.get("hooks", []),
        "blueprints": provides.get("blueprints", []),
    }


def _resolve_feature_path(
    workspace: str, feature_name: str, product: str
) -> str | None:
    """Find a feature's path: workspace root first, then product symlinks, then cache."""
    # Workspace root (editable)
    root = os.path.join(workspace, feature_name)
    if os.path.isdir(root):
        return root

    # Product symlinks
    features_dir = os.path.join(workspace, product, "features")
    if os.path.isdir(features_dir):
        for org_dir in os.listdir(features_dir):
            org_path = os.path.join(features_dir, org_dir)
            if not os.path.isdir(org_path):
                continue
            for entry in os.listdir(org_path):
                bare = entry.split("@")[0]
                if bare == feature_name:
                    link = os.path.join(org_path, entry)
                    real = os.path.realpath(link)
                    if os.path.isdir(real):
                        return real
    return None


def _get_product_features(workspace: str, product: str) -> list[dict]:
    """Get all features in the product with their extensible contracts."""
    pyproject = os.path.join(workspace, product, "pyproject.toml")
    if not os.path.isfile(pyproject):
        return []

    with open(pyproject, "rb") as f:
        data = tomllib.load(f)

    features = read_features_from_data(data)
    result = []
    for entry in features:
        # Parse name and detect pinned vs editable
        raw_name = entry.split("/")[-1] if "/" in entry else entry
        pinned = "@" in raw_name
        name = raw_name.split("@")[0]
        short = name.replace("splent_feature_", "")

        path = _resolve_feature_path(workspace, name, product)
        if not path:
            continue

        ext = _read_extensible_contract(path)
        has_extensible = (
            ext.get("services")
            or ext.get("templates")
            or ext.get("models")
            or ext.get("hooks")
            or ext.get("routes")
        )

        result.append(
            {
                "entry": entry,
                "name": name,
                "short": short,
                "path": path,
                "pinned": pinned,
                "extensible": ext,
                "has_extensible": bool(has_extensible),
            }
        )

    return result


def _multi_select(items: list[str], label: str) -> list[str]:
    """Interactive multi-select: show numbered items, user types numbers separated by spaces."""
    if not items:
        return []

    click.echo()
    click.echo(click.style(f"  Available {label}:", bold=True))
    for i, item in enumerate(items, 1):
        click.echo(f"    {i}. {item}")
    click.echo("    0. (none)")
    click.echo()

    raw = click.prompt(
        f"  Select {label} (space-separated numbers, or 0 for none)",
        default="0",
    )

    if raw.strip() == "0":
        return []

    selected = []
    for part in raw.split():
        try:
            idx = int(part)
            if 1 <= idx <= len(items):
                selected.append(items[idx - 1])
        except ValueError:
            pass

    return selected


def _generate_refinement_toml(
    base_name: str,
    override_services: list[tuple[str, str]],
    override_templates: list[tuple[str, str]],
    override_hooks: list[tuple[str, str]],
    extend_models: list[tuple[str, str]],
    add_routes: list[tuple[str, str]],
) -> str:
    """Generate the [tool.splent.refinement] TOML block."""
    lines = []
    lines.append("")
    lines.append("# -- Refinement (auto-generated by feature:refinement) ---------")
    lines.append("[tool.splent.refinement]")
    lines.append(f'refines = "{base_name}"')

    # Extends
    has_extends = extend_models or add_routes
    if has_extends:
        lines.append("")
        lines.append("[tool.splent.refinement.extends]")
        if extend_models:
            entries = ", ".join(
                f'{{ target = "{t}", mixin = "{m}" }}' for t, m in extend_models
            )
            lines.append(f"models = [{entries}]")
        if add_routes:
            entries = ", ".join(
                f'{{ blueprint = "{b}", module = "{m}" }}' for b, m in add_routes
            )
            lines.append(f"routes = [{entries}]")

    # Overrides
    has_overrides = override_services or override_templates or override_hooks
    if has_overrides:
        lines.append("")
        lines.append("[tool.splent.refinement.overrides]")
        if override_services:
            entries = ", ".join(
                f'{{ target = "{t}", replacement = "{r}" }}'
                for t, r in override_services
            )
            lines.append(f"services = [{entries}]")
        if override_templates:
            entries = ", ".join(
                f'{{ target = "{t}", replacement = "{r}" }}'
                for t, r in override_templates
            )
            lines.append(f"templates = [{entries}]")
        if override_hooks:
            entries = ", ".join(
                f'{{ target = "{t}", replacement = "{r}" }}' for t, r in override_hooks
            )
            lines.append(f"hooks = [{entries}]")

    lines.append("")
    return "\n".join(lines)


def _scaffold_mixin(
    feature_path: str, ns: str, feature_name: str, model_name: str, mixin_name: str
):
    """Create a skeleton mixin class in the feature's models.py.

    Replaces the scaffold's default db.Model with only the mixin —
    refinement features should not declare their own tables.
    """
    ns_safe = normalize_namespace(ns)
    models_path = os.path.join(feature_path, "src", ns_safe, feature_name, "models.py")

    mixin_code = f'''from splent_framework.db import db


class {mixin_name}:
    """Mixin applied to {model_name} at startup.

    Add columns and methods here. They will be injected into the base
    {model_name} model without modifying the original feature's code.
    """
    # Example:
    # tags = db.Column(db.String(500), nullable=True, default="")
    #
    # def get_tags_list(self):
    #     return [t.strip() for t in (self.tags or "").split(",") if t.strip()]
    pass
'''

    # Check if mixin already exists (e.g. wizard run twice)
    if os.path.isfile(models_path):
        with open(models_path, "r") as f:
            content = f.read()
        if mixin_name in content:
            return

    # Overwrite — refinement features only need mixins, not db.Model classes
    os.makedirs(os.path.dirname(models_path), exist_ok=True)
    with open(models_path, "w") as f:
        f.write(mixin_code)


def _scaffold_service(
    feature_path: str,
    ns: str,
    feature_name: str,
    target_service: str,
    replacement_name: str,
):
    """Create a skeleton replacement service in the feature's services.py.

    Replaces the scaffold's default service/repository with only the
    replacement class — refinement features override, not own.
    """
    ns_safe = normalize_namespace(ns)
    services_path = os.path.join(
        feature_path, "src", ns_safe, feature_name, "services.py"
    )

    service_code = f'''class {replacement_name}:
    """Replaces {target_service} at runtime.

    Inherits from the base {target_service} via refine_service().
    You inherit all existing methods and can override or add new ones.
    """
    # Example:
    # def get_by_tag(self, user_id, tag):
    #     ...
    pass
'''

    # Check if replacement already exists
    if os.path.isfile(services_path):
        with open(services_path, "r") as f:
            content = f.read()
        if replacement_name in content:
            return

    # Overwrite — refinement features only need replacement classes
    os.makedirs(os.path.dirname(services_path), exist_ok=True)
    with open(services_path, "w") as f:
        f.write(service_code)

    # Also clean up repositories.py — refinement features don't need one
    repos_path = os.path.join(
        feature_path, "src", ns_safe, feature_name, "repositories.py"
    )
    if os.path.isfile(repos_path):
        with open(repos_path, "w") as f:
            f.write(
                "# Refinement features do not need their own repository.\n"
                "# The base feature's repository is used via the service locator.\n"
            )


def _scaffold_init(
    feature_path: str,
    ns: str,
    feature_name: str,
    base_short: str,
    extend_models: list[tuple[str, str]],
    override_services: list[tuple[str, str]],
):
    """Generate __init__.py with blueprint, init_feature, and refinement wiring."""
    ns_safe = normalize_namespace(ns)
    init_path = os.path.join(feature_path, "src", ns_safe, feature_name, "__init__.py")

    short = feature_name.replace("splent_feature_", "")
    bp_name = f"{short}_bp"

    # Build imports
    imports = [
        "from splent_framework.blueprints.base_blueprint import create_blueprint",
    ]

    refinement_imports = []
    if extend_models:
        refinement_imports.append("refine_model")
    if override_services:
        refinement_imports.append("refine_service")
    if refinement_imports:
        imports.append(
            f"from splent_framework.refinement import {', '.join(refinement_imports)}"
        )

    imports.append("")

    if extend_models:
        mixin_names = ", ".join(m for _, m in extend_models)
        imports.append(f"from .models import {mixin_names}")
    if override_services:
        svc_names = ", ".join(r for _, r in override_services)
        imports.append(f"from .services import {svc_names}")

    # Build init_feature body
    init_lines = []
    for model, mixin in extend_models:
        init_lines.append(f'    refine_model("{model}", {mixin})')

    for target, replacement in override_services:
        init_lines.append(f'    refine_service(app, "{target}", {replacement})')

    if not init_lines:
        init_lines.append("    pass")

    init_body = "\n".join(init_lines).rstrip()

    code = (
        "\n".join(imports)
        + "\n\n"
        + f"{bp_name} = create_blueprint(__name__)\n"
        + "\n\n"
        + "def init_feature(app):\n"
        + f"{init_body}\n"
        + "\n\n"
        + "def inject_context_vars(app):\n"
        + "    return {}\n"
    )

    os.makedirs(os.path.dirname(init_path), exist_ok=True)
    with open(init_path, "w") as f:
        f.write(code)


def _update_env_py(
    feature_path: str,
    ns: str,
    feature_name: str,
    extend_models: list[tuple[str, str]],
):
    """Update the feature's migrations/env.py to include refined tables.

    Alembic's autogenerate only detects changes in tables listed in
    FEATURE_TABLES. Refinement features add columns to tables owned by
    the base feature, so we need to include those tables explicitly.
    """
    ns_safe = normalize_namespace(ns)
    env_path = os.path.join(
        feature_path, "src", ns_safe, feature_name, "migrations", "env.py"
    )
    if not os.path.isfile(env_path):
        return

    # Convert model names to table names (SQLAlchemy convention: CamelCase → snake_case)
    import re as _re

    table_names = set()
    for model_name, _ in extend_models:
        # CamelCase → snake_case: "Notes" → "notes", "UserProfile" → "user_profile"
        snake = _re.sub(r"(?<!^)(?=[A-Z])", "_", model_name).lower()
        table_names.add(snake)

    tables_str = ", ".join(f'"{t}"' for t in sorted(table_names))

    with open(env_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Replace FEATURE_TABLES = set() with the explicit set
    content = _re.sub(
        r"FEATURE_TABLES\s*=\s*set\(\)",
        f"FEATURE_TABLES = {{{tables_str}}}",
        content,
    )

    with open(env_path, "w", encoding="utf-8") as f:
        f.write(content)


def _scaffold_hooks(
    feature_path: str, ns: str, feature_name: str, fill_hooks: list[str]
):
    """Create a hooks.py with register_template_hook calls for selected slots."""
    ns_safe = normalize_namespace(ns)
    hooks_path = os.path.join(feature_path, "src", ns_safe, feature_name, "hooks.py")

    # Build function stubs for each hook
    func_lines = []
    register_lines = []
    for hook in fill_hooks:
        # notes.index.before_list → notes_index_before_list
        func_name = hook.replace(".", "_")
        func_lines.append(f"def {func_name}():")
        func_lines.append(f'    """Fill the {hook} slot."""')
        func_lines.append(f'    return render_template("hooks/{func_name}.html")')
        func_lines.append("")
        func_lines.append("")
        register_lines.append(f'register_template_hook("{hook}", {func_name})')

    code = (
        "from splent_framework.hooks.template_hooks import register_template_hook\n"
        "from flask import render_template\n"
        "\n\n" + "\n".join(func_lines) + "\n".join(register_lines) + "\n"
    )

    # Don't overwrite if hooks.py already has content
    if os.path.isfile(hooks_path):
        with open(hooks_path, "r") as f:
            existing = f.read()
        if "register_template_hook" in existing:
            return

    os.makedirs(os.path.dirname(hooks_path), exist_ok=True)
    with open(hooks_path, "w") as f:
        f.write(code)


def _clean_scaffold_for_refinement(feature_path: str, ns: str, feature_name: str):
    """Clean up scaffold files that cause false positives in refinement features.

    The feature:create scaffold generates example code in signals.py and
    config.py that feature:contract detects as real signals/env vars.
    Refinement features typically don't need these.
    """
    ns_safe = normalize_namespace(ns)
    pkg_dir = os.path.join(feature_path, "src", ns_safe, feature_name)

    for filename, content in [
        (
            "signals.py",
            "# Refinement features typically do not define their own signals.\n",
        ),
        (
            "config.py",
            "# Refinement features typically do not need their own config.\n",
        ),
    ]:
        filepath = os.path.join(pkg_dir, filename)
        if os.path.isfile(filepath):
            with open(filepath, "w") as f:
                f.write(content)


# ── Command ──────────────────────────────────────────────────────────────


@click.command(
    "feature:refinement",
    short_help="Interactive wizard to configure refinement for a feature.",
)
@click.argument("refiner_name")
@context.requires_product
def feature_refinement(refiner_name):
    """Interactive wizard to set up refinement configuration.

    Reads the base feature's contract, presents its extensible points,
    and generates the [tool.splent.refinement] section in pyproject.toml
    with skeleton code for mixins and replacement services.

    \b
    Examples:
      splent feature:refinement splent_feature_notes_tags
    """
    workspace = str(context.workspace())
    product = context.require_app()

    # ── Resolve the refiner feature ──────────────────────────────────
    refiner_path = _resolve_feature_path(workspace, refiner_name, product)
    if not refiner_path:
        click.secho(f"  {refiner_name} not found in workspace.", fg="red")
        raise SystemExit(1)

    refiner_pyproject = os.path.join(refiner_path, "pyproject.toml")
    if not os.path.isfile(refiner_pyproject):
        click.secho(f"  pyproject.toml not found in {refiner_name}.", fg="red")
        raise SystemExit(1)

    # Check if refinement already configured
    with open(refiner_pyproject, "rb") as f:
        refiner_data = tomllib.load(f)
    existing_refinement = (
        refiner_data.get("tool", {}).get("splent", {}).get("refinement")
    )
    if existing_refinement:
        click.echo()
        click.echo(
            click.style("  Refinement already configured", fg="yellow")
            + f" (refines: {existing_refinement.get('refines', '?')})"
        )
        if not click.confirm("  Overwrite?", default=False):
            return

    # ── Auto-update contracts before reading extensible points ──────
    click.echo()
    click.echo(click.style("  Updating editable feature contracts...", dim=True))
    from splent_cli.commands.feature.feature_contract import update_contract

    features_raw = _get_product_features(workspace, product)
    for feat in features_raw:
        if feat.get("pinned"):
            continue  # pinned features already have their contract from release
        try:
            ns = DEFAULT_NAMESPACE
            src_dir = os.path.join(feat["path"], "src")
            if os.path.isdir(src_dir):
                for d in sorted(os.listdir(src_dir)):
                    full = os.path.join(src_dir, d)
                    if (
                        os.path.isdir(full)
                        and not d.startswith("__")
                        and not d.endswith(".egg-info")
                        and "." not in d
                    ):
                        ns = d
                        break
            update_contract(feat["path"], ns, feat["name"])
        except Exception:
            pass  # skip features that can't be scanned

    # ── Get product features with extensible contracts ───────────────
    features = _get_product_features(workspace, product)
    extensible_features = [f for f in features if f["has_extensible"]]

    if not extensible_features:
        click.echo()
        click.secho(
            "  No features with extensible points found in this product.", fg="yellow"
        )
        click.echo(
            click.style("  Make sure the base feature has ", dim=True)
            + "[tool.splent.contract.extensible]"
            + click.style(" in its pyproject.toml.", dim=True)
        )
        raise SystemExit(1)

    # ── Step 1: Select base feature ──────────────────────────────────
    click.echo()
    click.secho("  feature:refinement", fg="cyan", bold=True)
    click.echo(click.style(f"  {'─' * 56}", fg="bright_black"))
    click.echo()
    click.secho("  Step 1: Which feature do you want to refine?", bold=True)
    click.echo()

    for i, feat in enumerate(extensible_features, 1):
        ext = feat["extensible"]
        parts = []
        if ext["services"]:
            parts.append(f"{len(ext['services'])} service(s)")
        if ext["models"]:
            parts.append(f"{len(ext['models'])} model(s)")
        if ext["templates"]:
            parts.append(f"{len(ext['templates'])} template(s)")
        if ext["hooks"]:
            parts.append(f"{len(ext['hooks'])} hook(s)")
        if ext["routes"]:
            parts.append("routes")
        summary = ", ".join(parts) if parts else "no extension points"

        click.echo(f"    {i}. {click.style(feat['short'], bold=True)}")
        click.echo(click.style(f"       {summary}", dim=True))

    click.echo()
    choice = click.prompt("  Select", type=click.IntRange(1, len(extensible_features)))
    base = extensible_features[choice - 1]

    base_name = base["name"]
    base_short = base["short"]
    ext = base["extensible"]
    provides = _read_provides(base["path"])

    click.echo()
    click.echo(
        click.style("  Refining: ", dim=True) + click.style(base_short, bold=True)
    )

    # ── Step 2: Select what to override/extend ───────────────────────
    click.echo()
    click.secho("  Step 2: What do you want to do?", bold=True)
    click.echo()

    override_services: list[tuple[str, str]] = []
    override_templates: list[tuple[str, str]] = []
    override_hooks: list[tuple[str, str]] = []
    extend_models: list[tuple[str, str]] = []
    add_routes: list[tuple[str, str]] = []

    # Determine refiner's short name for generating class names
    refiner_short = refiner_name.replace("splent_feature_", "")
    # Strip the base feature name from the refiner if it starts with it
    # e.g. "notes_tags" refining "notes" → suffix is "tags"
    suffix = refiner_short
    if suffix.startswith(base_short + "_"):
        suffix = suffix[len(base_short) + 1 :]
    elif suffix.startswith(base_short):
        suffix = suffix[len(base_short) :]
    # e.g. "tags" → "Tags" for class name suffixes
    refiner_pascal = (
        "".join(w.capitalize() for w in suffix.split("_")) if suffix else "Ext"
    )

    # ── Models ────────────────────────────────────────────────────────
    if ext["models"]:
        click.echo(
            click.style("  Models", bold=True)
            + click.style(" (extend with mixins)", dim=True)
        )
        selected_models = _multi_select(ext["models"], "models to extend")
        for model in selected_models:
            mixin = f"{model}{refiner_pascal}Mixin"
            click.echo(
                click.style("    mixin: ", dim=True)
                + f"{model} <- {click.style(mixin, fg='cyan')}"
            )
            extend_models.append((model, mixin))

    # ── Services ──────────────────────────────────────────────────────
    if ext["services"]:
        click.echo(
            click.style("  Services", bold=True)
            + click.style(" (override with replacement)", dim=True)
        )
        selected_services = _multi_select(ext["services"], "services to override")
        for svc in selected_services:
            replacement = f"{svc}With{refiner_pascal}"
            click.echo(
                click.style("    override: ", dim=True)
                + f"{svc} -> {click.style(replacement, fg='cyan')}"
            )
            override_services.append((svc, replacement))

    # ── Templates ─────────────────────────────────────────────────────
    if ext["templates"]:
        click.echo(
            click.style("  Templates", bold=True)
            + click.style(" (override with replacement)", dim=True)
        )
        selected_templates = _multi_select(ext["templates"], "templates to override")
        for tpl in selected_templates:
            override_templates.append((tpl, ""))
            click.echo(click.style("    override: ", dim=True) + tpl)

    # ── Hooks ─────────────────────────────────────────────────────────
    # Separate hooks into two categories:
    #   - Hooks the base feature registers (from provides.hooks) → can be overridden
    #   - Hook slots the base feature offers in templates → can be filled (additive)
    fill_hooks: list[str] = []
    if ext["hooks"]:
        provided_hooks = set(provides.get("hooks", []))
        overridable_hooks = [h for h in ext["hooks"] if h in provided_hooks]
        fillable_hooks = [h for h in ext["hooks"] if h not in provided_hooks]

        if fillable_hooks:
            click.echo(
                click.style("  Hook slots", bold=True)
                + click.style(" (fill with register_template_hook)", dim=True)
            )
            selected_fill = _multi_select(fillable_hooks, "hook slots to fill")
            for hook in selected_fill:
                fill_hooks.append(hook)
                click.echo(click.style("    fill: ", dim=True) + hook)

        if overridable_hooks:
            click.echo(
                click.style("  Hooks", bold=True)
                + click.style(" (override with replace_template_hook)", dim=True)
            )
            selected_hooks = _multi_select(overridable_hooks, "hooks to override")
            for hook in selected_hooks:
                override_hooks.append((hook, ""))
                click.echo(click.style("    override: ", dim=True) + hook)

    # ── Routes ────────────────────────────────────────────────────────
    if ext["routes"] and provides.get("blueprints"):
        click.echo(
            click.style("  Routes", bold=True)
            + click.style(" (add to existing blueprint)", dim=True)
        )
        if click.confirm(
            "  Add routes to the base feature's blueprint?", default=False
        ):
            bp = (
                provides["blueprints"][0]
                if len(provides["blueprints"]) == 1
                else _multi_select(provides["blueprints"], "blueprint")[0]
                if provides["blueprints"]
                else None
            )
            if bp:
                module = f"routes_{refiner_short}"
                add_routes.append((bp, module))
                click.echo(
                    click.style("    routes: ", dim=True)
                    + f"{bp} <- {click.style(module, fg='cyan')}"
                )

    # ── Check if anything was selected ───────────────────────────────
    total = (
        len(override_services)
        + len(override_templates)
        + len(override_hooks)
        + len(extend_models)
        + len(add_routes)
        + len(fill_hooks)
    )
    if total == 0:
        click.echo()
        click.secho("  Nothing selected. Aborting.", fg="yellow")
        return

    # ── Step 3: Preview and confirm ──────────────────────────────────
    click.echo()
    click.secho("  Step 3: Review", bold=True)
    click.echo(click.style(f"  {'─' * 56}", fg="bright_black"))

    refinement_toml = _generate_refinement_toml(
        base_name,
        override_services,
        override_templates,
        override_hooks,
        extend_models,
        add_routes,
    )

    for line in refinement_toml.strip().splitlines():
        click.echo(f"  {line}")

    click.echo()
    if not click.confirm("  Write to pyproject.toml and scaffold code?", default=True):
        return

    # ── Step 4: Write pyproject.toml ─────────────────────────────────
    # Remove existing refinement block if present
    with open(refiner_pyproject, "r", encoding="utf-8") as f:
        content = f.read()

    # Remove old refinement sections
    content = re.sub(
        r"\n*# -- Refinement.*?(?=\n\[(?!tool\.splent\.refinement)|\Z)",
        "",
        content,
        flags=re.DOTALL,
    )
    content = re.sub(
        r"\n*\[tool\.splent\.refinement\].*?(?=\n\[(?!tool\.splent\.refinement)|\Z)",
        "",
        content,
        flags=re.DOTALL,
    )

    content = content.rstrip() + "\n" + refinement_toml

    with open(refiner_pyproject, "w", encoding="utf-8") as f:
        f.write(content)

    click.echo(click.style("  pyproject.toml updated.", dim=True))

    # ── Step 5: Scaffold code ────────────────────────────────────────
    # Determine namespace from refiner's pyproject
    ns_raw = (
        refiner_data.get("tool", {})
        .get("splent", {})
        .get("namespace", DEFAULT_NAMESPACE)
    )
    ns = normalize_namespace(ns_raw)

    # Also try to detect from src/ directory structure
    src_dir = os.path.join(refiner_path, "src")
    if os.path.isdir(src_dir):
        for d in sorted(os.listdir(src_dir)):
            full = os.path.join(src_dir, d)
            if (
                os.path.isdir(full)
                and not d.startswith("__")
                and not d.endswith(".egg-info")
                and "." not in d
            ):
                ns = d
                break

    for model, mixin in extend_models:
        _scaffold_mixin(refiner_path, ns, refiner_name, model, mixin)
        click.echo(click.style("  scaffolded: ", dim=True) + f"models.py <- {mixin}")

    for target, replacement in override_services:
        _scaffold_service(refiner_path, ns, refiner_name, target, replacement)
        click.echo(
            click.style("  scaffolded: ", dim=True) + f"services.py <- {replacement}"
        )

    _scaffold_init(
        refiner_path,
        ns,
        refiner_name,
        base_short,
        extend_models,
        override_services,
    )
    click.echo(click.style("  scaffolded: ", dim=True) + "__init__.py")

    if fill_hooks:
        _scaffold_hooks(refiner_path, ns, refiner_name, fill_hooks)
        click.echo(
            click.style("  scaffolded: ", dim=True)
            + f"hooks.py <- {len(fill_hooks)} hook slot(s)"
        )

    # Clean up scaffold files that are not needed for refinement features
    _clean_scaffold_for_refinement(refiner_path, ns, refiner_name)

    # Update env.py to include refined tables in migration scope
    if extend_models:
        _update_env_py(refiner_path, ns, refiner_name, extend_models)
        click.echo(click.style("  updated:    ", dim=True) + "migrations/env.py")

    # ── Update the refiner's own contract ───────────────────────────
    try:
        update_contract(refiner_path, ns, refiner_name)
        click.echo(click.style("  contract updated.", dim=True))
    except Exception:
        pass

    click.echo()
    click.secho("  done.", fg="green")
    click.echo()


cli_command = feature_refinement
