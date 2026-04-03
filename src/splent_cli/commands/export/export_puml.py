"""
export:puml — Generate PlantUML diagrams of the product's feature model.

Two diagram modes:
  - Feature model (default): component diagram with UVL tree + contracts
  - Class diagram (--classes): UML class diagram from SQLAlchemy models

Outputs:
  - .puml file (always)
  - .pdf  (--pdf), .png (--png), .svg (--svg)
"""

import os
import re
import subprocess
import tomllib

import click

from splent_cli.services import context
from splent_cli.utils.feature_utils import normalize_namespace
from splent_framework.utils.pyproject_reader import PyprojectReader


# ---------------------------------------------------------------------------
# UVL parser
# ---------------------------------------------------------------------------


def _parse_uvl(uvl_path: str) -> dict:
    """Return {features: [{name, package, org, cardinality}], constraints: [str]}."""
    with open(uvl_path, "r", encoding="utf-8") as f:
        text = f.read()

    features = []
    constraints = []
    in_features = False
    in_constraints = False
    current_cardinality = "optional"

    for line in text.splitlines():
        stripped = line.strip()

        if stripped == "features":
            in_features, in_constraints = True, False
            continue
        if stripped == "constraints":
            in_features, in_constraints = False, True
            continue

        if in_features:
            if stripped in ("mandatory", "optional"):
                current_cardinality = stripped
                continue
            m = re.match(r"(\w+)\s*\{([^}]*)\}", stripped)
            if m:
                attrs = m.group(2)
                org_m = re.search(r"org\s+'([^']+)'", attrs)
                pkg_m = re.search(r"package\s+'([^']+)'", attrs)
                features.append(
                    {
                        "name": m.group(1),
                        "package": pkg_m.group(1) if pkg_m else "",
                        "org": org_m.group(1) if org_m else "",
                        "cardinality": current_cardinality,
                    }
                )

        if in_constraints and "=>" in stripped:
            constraints.append(stripped)

    return {"features": features, "constraints": constraints}


# ---------------------------------------------------------------------------
# Contract reader
# ---------------------------------------------------------------------------


def _read_contract(feature_path: str) -> dict | None:
    pyproject = os.path.join(feature_path, "pyproject.toml")
    if not os.path.isfile(pyproject):
        return None
    with open(pyproject, "rb") as f:
        data = tomllib.load(f)
    return data.get("tool", {}).get("splent", {}).get("contract")


def _resolve_feature_path(
    workspace: str, product: str, org: str, package: str
) -> str | None:
    org_safe = normalize_namespace(org)
    features_dir = os.path.join(workspace, product, "features", org_safe)
    if not os.path.isdir(features_dir):
        return None
    for entry in os.listdir(features_dir):
        if entry.startswith(package):
            return os.path.abspath(os.path.join(features_dir, entry))
    return None


# ---------------------------------------------------------------------------
# Model parser — extracts classes, attributes, FKs, relationships from .py
# ---------------------------------------------------------------------------

_COL_TYPE_MAP = {
    "Integer": "int",
    "String": "str",
    "Text": "str",
    "Boolean": "bool",
    "DateTime": "datetime",
    "Float": "float",
    "Date": "date",
    "Time": "time",
    "LargeBinary": "bytes",
}


def _parse_models(models_path: str) -> list[dict]:
    """Parse a models.py file and extract class info.

    Returns list of:
      {name, feature, attributes: [{name, type, pk, nullable, unique}],
       methods: [str], fks: [{column, target_table, target_col}],
       relationships: [{name, target, uselist}]}
    """
    if not os.path.isfile(models_path):
        return []

    with open(models_path, "r", encoding="utf-8") as f:
        text = f.read()

    # Join continuation lines (lines starting with whitespace after an assignment)
    # so multi-line db.Column(...) definitions become single lines
    joined_lines = []
    for line in text.splitlines():
        if (
            joined_lines
            and line
            and line[0] in (" ", "\t")
            and not line.strip().startswith(("class ", "def ", "@", "#"))
        ):
            if "=" not in line.lstrip() or joined_lines[-1].rstrip().endswith(
                (",", "(")
            ):
                joined_lines[-1] += " " + line.strip()
                continue
        joined_lines.append(line)
    text = "\n".join(joined_lines)

    classes = []
    # Split by class definition (with or without parentheses for mixins)
    class_blocks = re.split(r"^(class \w+(?:\(.*?\))?:)", text, flags=re.MULTILINE)

    i = 1
    while i < len(class_blocks):
        header = class_blocks[i]
        body = class_blocks[i + 1] if i + 1 < len(class_blocks) else ""
        i += 2

        class_m = re.match(r"class (\w+)", header)
        if not class_m:
            continue
        class_name = class_m.group(1)

        attributes = []
        methods = []
        fks = []
        relationships = []

        for line in body.splitlines():
            stripped = line.strip()

            # db.Column(...)
            col_m = re.match(r"(\w+)\s*=\s*db\.Column\((.+)\)", stripped)
            if col_m:
                attr_name = col_m.group(1)
                col_args = col_m.group(2)

                # Type
                type_m = re.search(r"db\.(\w+)", col_args)
                raw_type = type_m.group(1) if type_m else "?"
                attr_type = _COL_TYPE_MAP.get(raw_type, raw_type)

                pk = "primary_key=True" in col_args
                nullable = "nullable=False" not in col_args and not pk
                unique = "unique=True" in col_args

                attributes.append(
                    {
                        "name": attr_name,
                        "type": attr_type,
                        "pk": pk,
                        "nullable": nullable,
                        "unique": unique,
                    }
                )

                # Foreign key
                fk_m = re.search(r'db\.ForeignKey\(["\'](\w+)\.(\w+)["\']\)', col_args)
                if fk_m:
                    fks.append(
                        {
                            "column": attr_name,
                            "target_table": fk_m.group(1),
                            "target_col": fk_m.group(2),
                        }
                    )
                continue

            # db.relationship(...)
            rel_m = re.match(r"(\w+)\s*=\s*db\.relationship\((.+)\)", stripped)
            if rel_m:
                rel_name = rel_m.group(1)
                rel_args = rel_m.group(2)
                # Target can be unquoted (User) or quoted ("User" / 'User')
                target_m = re.match(r"""[\"']?(\w+)[\"']?""", rel_args)
                target = target_m.group(1) if target_m else None
                if not target:
                    continue
                uselist = "uselist=False" not in rel_args
                relationships.append(
                    {
                        "name": rel_name,
                        "target": target,
                        "uselist": uselist,
                    }
                )
                continue

            # Methods (def ...)
            method_m = re.match(r"def (\w+)\(self.*?\)", stripped)
            if method_m:
                mname = method_m.group(1)
                if not mname.startswith("_"):
                    methods.append(mname)

        classes.append(
            {
                "name": class_name,
                "attributes": attributes,
                "methods": methods,
                "fks": fks,
                "relationships": relationships,
            }
        )

    return classes


# ---------------------------------------------------------------------------
# PlantUML generators
# ---------------------------------------------------------------------------


def _generate_feature_puml(product_name: str, uvl_data: dict, contracts: dict) -> str:
    """Feature model component diagram."""
    lines = [
        "@startuml",
        f"title {product_name} — Feature Model",
        "",
        "skinparam packageStyle rectangle",
        "skinparam componentStyle rectangle",
        "skinparam defaultFontSize 11",
        "skinparam shadowing false",
        "skinparam linetype ortho",
        "",
        f'package "{product_name}" as product {{',
    ]

    for feat in uvl_data["features"]:
        name = feat["name"]
        package = feat["package"]
        card = feat["cardinality"]
        stereotype = "<<mandatory>>" if card == "mandatory" else "<<optional>>"
        contract = contracts.get(package)

        details = []
        if contract:
            provides = contract.get("provides", {})
            requires = contract.get("requires", {})
            for label, key in [
                ("Models", "models"),
                ("Routes", "routes"),
                ("Blueprints", "blueprints"),
                ("Services", "services"),
                ("Hooks", "hooks"),
                ("Docker", "docker"),
            ]:
                vals = provides.get(key, [])
                if vals:
                    details.append(f"**{label}**: {', '.join(vals)}")
            env_vars = requires.get("env_vars", [])
            if env_vars:
                details.append(f"**Env vars**: {', '.join(env_vars)}")
            req_features = requires.get("features", [])
            if req_features:
                details.append(f"**Requires**: {', '.join(req_features)}")

        detail_block = "\\n".join(details) if details else ""
        description = contract.get("description", "") if contract else ""
        lines.append(
            f'  component "{name}\\n{stereotype}\\n---\\n{description}\\n{detail_block}" as {name}'
        )

    lines.append("}")
    lines.append("")

    for constraint in uvl_data["constraints"]:
        parts = constraint.split("=>")
        if len(parts) == 2:
            src = parts[0].split("#")[0].strip()
            dst = parts[1].split("#")[0].strip()
            lines.append(f"{src} ..> {dst} : requires")

    lines.extend(["", "@enduml"])
    return "\n".join(lines)


def _generate_class_puml(
    product_name: str, all_models: dict[str, list[dict]], uvl_data: dict
) -> str:
    """UML class diagram from parsed SQLAlchemy models."""
    lines = [
        "@startuml",
        f"title {product_name} — Class Diagram",
        "",
        "skinparam classAttributeIconSize 0",
        "skinparam defaultFontSize 11",
        "skinparam shadowing false",
        "skinparam linetype ortho",
        "",
        "hide empty methods",
        "",
    ]

    # Collect all class names → feature for package grouping
    class_to_feature: dict[str, str] = {}
    # Table name → class name mapping for FK resolution
    table_to_class: dict[str, str] = {}

    for feat_name, models in all_models.items():
        for model in models:
            class_to_feature[model["name"]] = feat_name
            # SQLAlchemy default table name: class name lowercased
            table_name = re.sub(r"(?<!^)(?=[A-Z])", "_", model["name"]).lower()
            table_to_class[table_name] = model["name"]

    # Group classes by feature package
    for feat_name, models in all_models.items():
        if not models:
            continue

        # Find UVL short name for the feature
        short_name = feat_name
        for f in uvl_data.get("features", []):
            if f["package"] == feat_name:
                short_name = f["name"]
                break

        lines.append(f'package "{short_name}" <<Feature>> {{')

        for model in models:
            lines.append(f"  class {model['name']} {{")

            # Attributes
            for attr in model["attributes"]:
                visibility = "+" if not attr["name"].startswith("_") else "-"
                stereo = ""
                if attr["pk"]:
                    stereo = " <<PK>>"
                elif attr["unique"]:
                    stereo = " <<unique>>"

                nullable = "" if not attr["nullable"] else "?"
                lines.append(
                    f"    {visibility} {attr['name']} : {attr['type']}{nullable}{stereo}"
                )

            # Separator if there are methods
            if model["methods"]:
                lines.append("    ..")
                for method in model["methods"]:
                    lines.append(f"    + {method}()")

            lines.append("  }")

        lines.append("}")
        lines.append("")

    # Relationships — deduplicate by class pair
    drawn_pairs: set[frozenset] = set()
    # Collect all FK and relationship info first
    all_rels: list[
        tuple[str, str, str, str]
    ] = []  # (source, target, label, cardinality)

    for feat_name, models in all_models.items():
        for model in models:
            for fk in model["fks"]:
                target_class = table_to_class.get(fk["target_table"])
                if not target_class:
                    continue
                fk_attr = next(
                    (a for a in model["attributes"] if a["name"] == fk["column"]),
                    None,
                )
                card = '"1" -- "1"' if (fk_attr and fk_attr["unique"]) else '"1" -- "*"'
                all_rels.append((target_class, model["name"], fk["column"], card))

            for rel in model.get("relationships", []):
                card = '"1" -- "1"' if not rel["uselist"] else '"1" -- "*"'
                all_rels.append((model["name"], rel["target"], rel["name"], card))

    for source, target, label, card in all_rels:
        pair = frozenset((source, target))
        if pair in drawn_pairs:
            continue
        drawn_pairs.add(pair)
        lines.append(f"{source} {card} {target} : {label}")

    lines.extend(["", "@enduml"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dependency graph generator
# ---------------------------------------------------------------------------


def _generate_deps_puml(product_name: str, uvl_data: dict) -> str:
    """Clean dependency graph — just nodes and arrows."""
    lines = [
        "@startuml",
        f"title {product_name} — Feature Dependencies",
        "",
        "skinparam defaultFontSize 12",
        "skinparam shadowing false",
        "skinparam roundCorner 10",
        "skinparam componentStyle rectangle",
        "left to right direction",
        "",
    ]

    for feat in uvl_data["features"]:
        name = feat["name"]
        card = feat["cardinality"]
        color = "#2E86C1" if card == "mandatory" else "#AED6F1"
        lines.append(f"[{name}] as {name} {color}")

    lines.append("")

    for constraint in uvl_data["constraints"]:
        parts = constraint.split("=>")
        if len(parts) == 2:
            src = parts[0].split("#")[0].strip()
            dst = parts[1].split("#")[0].strip()
            lines.append(f"{src} --> {dst} : requires")

    lines.append("")

    # Legend
    lines.append("legend right")
    lines.append("  <#2E86C1> Mandatory")
    lines.append("  <#AED6F1> Optional")
    lines.append("endlegend")

    lines.extend(["", "@enduml"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deployment diagram generator
# ---------------------------------------------------------------------------


def _parse_compose(compose_path: str) -> dict:
    """Parse a docker-compose YAML and return service info."""
    import yaml

    with open(compose_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("services", {})


def _generate_deployment_puml(
    product_name: str,
    workspace: str,
    product_dir: str,
    uvl_data: dict,
    feature_paths: dict[str, str],
) -> str:
    """Deployment diagram from docker-compose files."""
    lines = [
        "@startuml",
        f"title {product_name} — Deployment Diagram",
        "",
        "skinparam defaultFontSize 11",
        "skinparam shadowing false",
        "skinparam nodeStyle rectangle",
        "",
    ]

    all_services: list[dict] = []

    # Product docker-compose
    product_compose = os.path.join(product_dir, "docker", "docker-compose.dev.yml")
    if not os.path.isfile(product_compose):
        product_compose = os.path.join(product_dir, "docker", "docker-compose.yml")

    if os.path.isfile(product_compose):
        services = _parse_compose(product_compose)
        for svc_name, svc_cfg in services.items():
            all_services.append(
                {
                    "name": svc_name,
                    "source": product_name,
                    "image": svc_cfg.get("image", "Dockerfile"),
                    "ports": svc_cfg.get("ports", []),
                    "depends_on": svc_cfg.get("depends_on", []),
                    "volumes": svc_cfg.get("volumes", []),
                    "container_name": svc_cfg.get("container_name", svc_name),
                }
            )

    # Feature docker-compose files
    for feat in uvl_data["features"]:
        fpath = feature_paths.get(feat["package"])
        if not fpath:
            continue
        docker_dir = os.path.join(fpath, "docker")
        if not os.path.isdir(docker_dir):
            continue
        # Try dev first, then generic
        for fname in ("docker-compose.dev.yml", "docker-compose.yml"):
            compose_f = os.path.join(docker_dir, fname)
            if os.path.isfile(compose_f):
                services = _parse_compose(compose_f)
                for svc_name, svc_cfg in services.items():
                    all_services.append(
                        {
                            "name": svc_name,
                            "source": feat["name"],
                            "image": svc_cfg.get("image", "Dockerfile"),
                            "ports": svc_cfg.get("ports", []),
                            "depends_on": svc_cfg.get("depends_on", []),
                            "volumes": svc_cfg.get("volumes", []),
                            "container_name": svc_cfg.get("container_name", svc_name),
                        }
                    )
                break

    # CLI container
    cli_compose = os.path.join(workspace, "splent_cli", "docker", "docker-compose.yml")
    if os.path.isfile(cli_compose):
        services = _parse_compose(cli_compose)
        for svc_name, svc_cfg in services.items():
            all_services.append(
                {
                    "name": svc_name,
                    "source": "splent_cli",
                    "image": svc_cfg.get("image", "Dockerfile"),
                    "ports": svc_cfg.get("ports", []),
                    "depends_on": svc_cfg.get("depends_on", []),
                    "volumes": svc_cfg.get("volumes", []),
                    "container_name": svc_cfg.get("container_name", svc_name),
                }
            )

    # Draw
    lines.append('cloud "splent_network" {')

    for svc in all_services:
        cname = svc["container_name"]
        safe_id = re.sub(r"[^a-zA-Z0-9_]", "_", cname)
        image = svc["image"]

        # Ports
        port_lines = []
        for p in svc["ports"]:
            port_lines.append(str(p))

        port_str = ""
        if port_lines:
            port_str = "\\n" + "\\n".join(port_lines)

        # Icon based on type
        if "mariadb" in image or "mysql" in image or "postgres" in image:
            lines.append(
                f'  database "{cname}\\n<size:9>{image}</size>{port_str}" as {safe_id}'
            )
        elif "redis" in image:
            lines.append(
                f'  storage "{cname}\\n<size:9>{image}</size>{port_str}" as {safe_id}'
            )
        elif "mailhog" in image or "mail" in image:
            lines.append(
                f'  collections "{cname}\\n<size:9>{image}</size>{port_str}" as {safe_id}'
            )
        else:
            lines.append(
                f'  node "{cname}\\n<size:9>{image}</size>{port_str}" as {safe_id}'
            )

    lines.append("}")
    lines.append("")

    # Dependencies
    drawn = set()
    for svc in all_services:
        safe_src = re.sub(r"[^a-zA-Z0-9_]", "_", svc["container_name"])
        for dep in svc["depends_on"]:
            # Find the dependency container name
            dep_svc = next((s for s in all_services if s["name"] == dep), None)
            if dep_svc:
                safe_dst = re.sub(r"[^a-zA-Z0-9_]", "_", dep_svc["container_name"])
                key = f"{safe_src}->{safe_dst}"
                if key not in drawn:
                    drawn.add(key)
                    lines.append(f"{safe_src} --> {safe_dst} : depends")

    # Named volumes only (skip bind mounts like /var/run, ./, ../../, $HOME, etc.)
    volumes_seen = set()
    for svc in all_services:
        safe_id = re.sub(r"[^a-zA-Z0-9_]", "_", svc["container_name"])
        for vol in svc["volumes"]:
            vol_str = str(vol)
            if ":" not in vol_str:
                continue
            vol_name = vol_str.split(":")[0]
            # Skip bind mounts (paths starting with /, $, ~, or relative ./)
            if re.match(r"^[/.$~]", vol_name):
                continue
            vol_safe = re.sub(r"[^a-zA-Z0-9_]", "_", vol_name)
            if vol_name not in volumes_seen:
                volumes_seen.add(vol_name)
                lines.append(f'file "{vol_name}" as {vol_safe}')
            lines.append(f"{safe_id} --> {vol_safe} : volume")

    lines.extend(["", "@enduml"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Render helper
# ---------------------------------------------------------------------------


def _render_exports(
    puml_path: str, base: str, export_pdf: bool, export_png: bool, export_svg: bool
) -> None:
    """Generate SVG/PDF/PNG from a .puml file."""
    plantuml_bin = "plantuml"

    try:
        subprocess.run([plantuml_bin, "-version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        click.secho(
            "❌ PlantUML not found. Rebuild the CLI container:\n   make setup-rebuild",
            fg="red",
        )
        raise SystemExit(1)

    svg_path = f"{base}.svg"
    click.echo("🔧 Rendering diagram...")
    try:
        subprocess.run(
            [plantuml_bin, "-tsvg", puml_path],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        click.secho("❌ PlantUML failed to render diagram.", fg="red")
        stderr = e.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        if stderr:
            click.secho(stderr.strip(), fg="bright_black")
        stdout = e.stdout
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if stdout:
            click.secho(stdout.strip(), fg="bright_black")
        return

    try:
        if export_svg:
            click.secho(f"✅ SVG exported: {svg_path}", fg="green")

        if export_pdf:
            click.echo("📄 Converting to PDF...")
            try:
                subprocess.run(
                    ["rsvg-convert", "-f", "pdf", "-o", f"{base}.pdf", svg_path],
                    check=True,
                )
                click.secho(f"✅ PDF exported: {base}.pdf", fg="green")
            except FileNotFoundError:
                click.secho(
                    "❌ rsvg-convert not found. Rebuild: make setup-rebuild",
                    fg="red",
                )

        if export_png:
            click.echo("🖼️  Converting to PNG...")
            try:
                subprocess.run(
                    [
                        "rsvg-convert",
                        "-f",
                        "png",
                        "--dpi-x",
                        "150",
                        "--dpi-y",
                        "150",
                        "-o",
                        f"{base}.png",
                        svg_path,
                    ],
                    check=True,
                )
                click.secho(f"✅ PNG exported: {base}.png", fg="green")
            except FileNotFoundError:
                click.secho(
                    "❌ rsvg-convert not found. Rebuild: make setup-rebuild",
                    fg="red",
                )
    finally:
        if not export_svg and os.path.exists(svg_path):
            os.remove(svg_path)


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


@click.command(
    "export:puml",
    short_help="Export the product feature model as a PlantUML diagram.",
)
@click.option(
    "--classes",
    "mode_classes",
    is_flag=True,
    help="UML class diagram (models, attributes, FKs, cardinalities).",
)
@click.option(
    "--deps",
    "mode_deps",
    is_flag=True,
    help="Feature dependency graph (clean nodes + arrows).",
)
@click.option(
    "--deployment",
    "mode_deployment",
    is_flag=True,
    help="Docker deployment diagram (containers, ports, networks).",
)
@click.option("--pdf", "export_pdf", is_flag=True, help="Also export as PDF.")
@click.option("--png", "export_png", is_flag=True, help="Also export as PNG.")
@click.option("--svg", "export_svg", is_flag=True, help="Also export as SVG.")
@click.option(
    "-o", "--output", default=None, help="Output filename (without extension)."
)
def export_puml(
    mode_classes, mode_deps, mode_deployment, export_pdf, export_png, export_svg, output
):
    """
    Generate PlantUML diagrams from the active product.

    \b
    Diagram modes (default: feature model):
      (none)       Feature model — components + UVL constraints + contracts
      --classes    UML class diagram — models, attributes, FKs, cardinalities
      --deps       Dependency graph — clean feature nodes + requires arrows
      --deployment Docker deployment — containers, ports, volumes, networks

    \b
    Examples:
        splent export:puml
        splent export:puml --classes --pdf
        splent export:puml --deps --png
        splent export:puml --deployment --svg
        splent export:puml -o docs/model --pdf
    """
    workspace = str(context.workspace())
    product = context.require_app()
    product_dir = os.path.join(workspace, product)

    # Read UVL — try SPL catalog first, fall back to legacy [tool.splent.uvl]
    import tomllib

    uvl_path = None
    pyproject_path = os.path.join(product_dir, "pyproject.toml")
    if os.path.isfile(pyproject_path):
        with open(pyproject_path, "rb") as f:
            pydata = tomllib.load(f)
        spl_name = pydata.get("tool", {}).get("splent", {}).get("spl")
        if spl_name:
            candidate = os.path.join(workspace, "splent_catalog", spl_name, f"{spl_name}.uvl")
            if os.path.isfile(candidate):
                uvl_path = candidate

    # Legacy fallback: [tool.splent.uvl].file
    if not uvl_path:
        try:
            uvl_cfg = PyprojectReader.for_product(product_dir).uvl_config
            uvl_file = uvl_cfg.get("file")
            if uvl_file:
                candidate = os.path.join(product_dir, "uvl", uvl_file)
                if os.path.isfile(candidate):
                    uvl_path = candidate
        except (FileNotFoundError, RuntimeError):
            pass

    if not uvl_path:
        click.secho(
            "❌ No UVL file found.\n"
            "   Set [tool.splent].spl in pyproject.toml (SPL catalog)\n"
            "   or [tool.splent.uvl].file (legacy).",
            fg="red",
        )
        raise SystemExit(1)

    click.echo(f"📖 Reading UVL model: {uvl_path}")
    uvl_data = _parse_uvl(uvl_path)

    # Check for stale contracts
    from splent_cli.utils.contract_freshness import check_and_refresh_contracts
    from splent_cli.utils.feature_utils import load_product_features

    try:
        features_raw = load_product_features(product_dir, os.getenv("SPLENT_ENV"))
        check_and_refresh_contracts(workspace, features_raw)
    except FileNotFoundError:
        pass

    # Resolve feature paths
    feature_paths: dict[str, str] = {}
    for feat in uvl_data["features"]:
        fpath = _resolve_feature_path(workspace, product, feat["org"], feat["package"])
        if fpath:
            feature_paths[feat["package"]] = fpath

    # Determine mode and suffix
    if mode_classes:
        suffix = "_classes"
    elif mode_deps:
        suffix = "_deps"
    elif mode_deployment:
        suffix = "_deployment"
    else:
        suffix = ""

    if output:
        base = output
    else:
        exports_dir = os.path.join(product_dir, "exports", "puml")
        os.makedirs(exports_dir, exist_ok=True)
        base = os.path.join(exports_dir, f"{product}{suffix}")

    # Generate diagram
    if mode_classes:
        all_models: dict[str, list[dict]] = {}
        for package, fpath in feature_paths.items():
            src_root = os.path.join(fpath, "src")
            if not os.path.isdir(src_root):
                continue
            for org_dir in os.listdir(src_root):
                models_path = os.path.join(src_root, org_dir, package, "models.py")
                if os.path.isfile(models_path):
                    parsed = _parse_models(models_path)
                    if parsed:
                        all_models[package] = parsed
                    break
        # Apply refinement mixins: merge mixin attributes into target models
        for package, fpath in feature_paths.items():
            pyproject_path = os.path.join(fpath, "pyproject.toml")
            if not os.path.isfile(pyproject_path):
                continue
            import tomllib as _tomllib
            with open(pyproject_path, "rb") as f:
                feat_data = _tomllib.load(f)
            extends = (
                feat_data.get("tool", {})
                .get("splent", {})
                .get("refinement", {})
                .get("extends", {})
                .get("models", [])
            )
            if not extends:
                continue
            # Parse the mixin's models.py
            mixin_models = []
            src_root = os.path.join(fpath, "src")
            if os.path.isdir(src_root):
                for org_dir in os.listdir(src_root):
                    mpath = os.path.join(src_root, org_dir, package, "models.py")
                    if os.path.isfile(mpath):
                        mixin_models = _parse_models(mpath)
                        break
            # Merge each mixin into its target model
            for ext in extends:
                target_name = ext.get("target")
                mixin_name = ext.get("mixin")
                if not target_name or not mixin_name:
                    continue
                # Find the mixin class in parsed mixin_models
                mixin_cls = next((m for m in mixin_models if m["name"] == mixin_name), None)
                if not mixin_cls:
                    continue
                # Find the target model across all parsed features
                for feat_models in all_models.values():
                    for model in feat_models:
                        if model["name"] == target_name:
                            model["attributes"].extend(mixin_cls["attributes"])
                            model["methods"].extend(mixin_cls["methods"])
                            break

        click.echo(
            f"📋 Parsed models from {len(all_models)}/{len(feature_paths)} features."
        )
        puml_src = _generate_class_puml(product, all_models, uvl_data)

    elif mode_deps:
        puml_src = _generate_deps_puml(product, uvl_data)

    elif mode_deployment:
        puml_src = _generate_deployment_puml(
            product, workspace, product_dir, uvl_data, feature_paths
        )

    else:
        contracts = {}
        for feat in uvl_data["features"]:
            fpath = feature_paths.get(feat["package"])
            if fpath:
                contract = _read_contract(fpath)
                if contract:
                    contracts[feat["package"]] = contract
        click.echo(
            f"📋 Loaded contracts for {len(contracts)}/{len(uvl_data['features'])} features."
        )
        puml_src = _generate_feature_puml(product, uvl_data, contracts)

    # Write .puml
    puml_path = f"{base}.puml"
    os.makedirs(os.path.dirname(puml_path) or ".", exist_ok=True)
    with open(puml_path, "w", encoding="utf-8") as f:
        f.write(puml_src)
    click.secho(f"✅ PlantUML source written: {puml_path}", fg="green")

    # Render exports
    if export_pdf or export_png or export_svg:
        _render_exports(puml_path, base, export_pdf, export_png, export_svg)


cli_command = export_puml
