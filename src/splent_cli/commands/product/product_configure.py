"""
product:configure — Interactive SPL feature configurator.

Reads the UVL from the SPL catalog, builds an abstract SPL model,
presents an interactive prompt respecting all UVL group semantics
(mandatory, optional, alternative, or, cardinality), propagates
constraints including parent-chain activation and contrapositive
exclusion, validates with Flamapy, and writes the result to pyproject.toml.

Design: depends on abstractions (SPLModel), not on Flamapy's concrete API.
The only Flamapy coupling is in _load_spl_model() and the final validation.

Aligned with the UVL specification (Benavides et al., JSS 2025):
  - Table 1  : constraint semantics (mandatory, alternative, or, cardinality)
  - Fig. 4-5 : metamodel (features, groups, constraints)
  - Fig. 7-9 : cross-tree constraints (implies, excludes, equivalence)
  - Section 5.2 : feature cardinality (recognised, not clone-expanded)
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field

import click

from splent_cli.services import context
from splent_cli.utils.feature_utils import normalize_namespace


# ═══════════════════════════════════════════════════════════════════
# Abstract SPL Model — mirrors the UVL metamodel (Fig. 4, 5)
# ═══════════════════════════════════════════════════════════════════


@dataclass
class SPLFeature:
    """A feature in the SPL model."""

    name: str
    org: str = "splent-io"
    package: str = ""  # empty → abstract (no deployable artefact)
    parent: str | None = None  # parent feature name (None for root)
    groups: list[SPLGroup] = field(default_factory=list)
    card_min: int = 1  # feature cardinality lower bound
    card_max: int = 1  # feature cardinality upper bound

    @property
    def is_abstract(self) -> bool:
        return not self.package


@dataclass
class SPLGroup:
    """A group relationship owned by a parent feature (UVL Fig. 5).

    Group types and their formal cardinality (UVL Table 1):
        mandatory   — [1..1], single child:  s(p(f),C) = s(f,C)
        optional    — [0..1], single child:  s(p(f),C) >= s(f,C)
        alternative — <1..1> over N children: Σs(f,C) = 1
        or          — <1..N> over N children: Σs(f,C) >= 1
        cardinality — <n..m> over N children: n <= Σs(f,C) <= m
    """

    group_type: str  # "mandatory"|"optional"|"alternative"|"or"|"cardinality"
    children: list[str] = field(default_factory=list)
    card_min: int = 0
    card_max: int = 0


@dataclass
class SPLConstraint:
    """A cross-tree constraint (UVL Fig. 7)."""

    kind: str  # "implies" | "excludes"
    source: str
    target: str


@dataclass
class SPLModel:
    """Full SPL model — the only abstraction the configurator depends on."""

    root_name: str
    features: dict[str, SPLFeature] = field(default_factory=dict)
    constraints: list[SPLConstraint] = field(default_factory=list)
    uvl_path: str = ""

    # ── Derived queries ────────────────────────────────────────

    def parent_of(self, name: str) -> str | None:
        f = self.features.get(name)
        return f.parent if f else None

    def ancestor_chain(self, name: str) -> list[str]:
        """Return [parent, grandparent, ...] up to (excluding) root."""
        chain: list[str] = []
        cur = self.parent_of(name)
        while cur and cur != self.root_name:
            chain.append(cur)
            cur = self.parent_of(cur)
        return chain

    def children_of(self, name: str) -> list[str]:
        """All direct children of a feature (across all its groups)."""
        f = self.features.get(name)
        if not f:
            return []
        out: list[str] = []
        for g in f.groups:
            out.extend(g.children)
        return out

    def is_mandatory(self, name: str) -> bool:
        """True if this feature is mandatory relative to its parent."""
        f = self.features.get(name)
        if not f or not f.parent:
            return name == self.root_name
        parent = self.features.get(f.parent)
        if not parent:
            return False
        for g in parent.groups:
            if name in g.children and g.group_type == "mandatory":
                return True
        return False

    def owning_group(self, name: str) -> SPLGroup | None:
        """Return the group that contains this feature as a child."""
        f = self.features.get(name)
        if not f or not f.parent:
            return None
        parent = self.features.get(f.parent)
        if not parent:
            return None
        for g in parent.groups:
            if name in g.children:
                return g
        return None

    def all_mandatory_recursive(self) -> set[str]:
        """All features that MUST be selected (mandatory chain from root)."""
        result = {self.root_name}
        stack = [self.root_name]
        while stack:
            cur = stack.pop()
            f = self.features.get(cur)
            if not f:
                continue
            for g in f.groups:
                if g.group_type == "mandatory":
                    for ch in g.children:
                        if ch not in result:
                            result.add(ch)
                            stack.append(ch)
        return result

    def groups_needing_selection(self) -> list[tuple[str, SPLGroup]]:
        """Return (parent_name, group) pairs that require interactive selection.

        These are alternative/or/cardinality groups whose parent is
        in the mandatory chain (or will be activated).
        """
        out: list[tuple[str, SPLGroup]] = []
        for f in self.features.values():
            for g in f.groups:
                if g.group_type in ("alternative", "or", "cardinality"):
                    out.append((f.name, g))
        return out


# ═══════════════════════════════════════════════════════════════════
# UVL text parsers — robust constraint & cardinality extraction
# ═══════════════════════════════════════════════════════════════════

# Feature-name pattern: bare identifier OR "quoted string"
_FEAT = r'(?:"[^"]+"|[\w]+)'


def _strip_feat(s: str) -> str:
    """Strip whitespace and surrounding quotes from a feature reference."""
    return s.strip().strip('"')


def _parse_constraints_from_uvl(
    uvl_path: str,
    model: SPLModel,
) -> list[SPLConstraint]:
    """Parse cross-tree constraints directly from UVL text (Section 5.1).

    Handles the Boolean-level constraint patterns:
        A => B           → implies(A, B)
        A <=> B          → implies(A, B) + implies(B, A)
        !(A & B)         → excludes(A, B)
        !A | !B          → excludes(A, B)   (De Morgan equivalence)
    """
    constraints: list[SPLConstraint] = []
    with open(uvl_path, "r") as f:
        text = f.read()

    match = re.search(r"^constraints\s*$", text, re.MULTILINE)
    if not match:
        return constraints

    section = text[match.end() :]
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//") or line.startswith("/*"):
            continue
        # Stop at next top-level keyword (not indented)
        if re.match(r"^(features|imports|include)\b", line):
            break

        # ── A => B ────────────────────────────────────────────
        m = re.match(rf"^({_FEAT})\s*=>\s*({_FEAT})$", line)
        if m:
            constraints.append(
                SPLConstraint(
                    "implies", _strip_feat(m.group(1)), _strip_feat(m.group(2))
                )
            )
            continue

        # ── A <=> B  (equivalence → two implications) ────────
        m = re.match(rf"^({_FEAT})\s*<=>\s*({_FEAT})$", line)
        if m:
            a, b = _strip_feat(m.group(1)), _strip_feat(m.group(2))
            constraints.append(SPLConstraint("implies", a, b))
            constraints.append(SPLConstraint("implies", b, a))
            continue

        # ── !(A & B) → excludes ──────────────────────────────
        m = re.match(rf"^!\s*\(\s*({_FEAT})\s*&\s*({_FEAT})\s*\)$", line)
        if m:
            constraints.append(
                SPLConstraint(
                    "excludes", _strip_feat(m.group(1)), _strip_feat(m.group(2))
                )
            )
            continue

        # ── !A | !B → excludes (De Morgan) ───────────────────
        m = re.match(rf"^!\s*({_FEAT})\s*\|\s*!\s*({_FEAT})$", line)
        if m:
            constraints.append(
                SPLConstraint(
                    "excludes", _strip_feat(m.group(1)), _strip_feat(m.group(2))
                )
            )
            continue

    return constraints


def _parse_feature_cardinalities(uvl_path: str) -> dict[str, tuple[int, int]]:
    """Parse feature cardinalities from UVL text (Section 4.3).

    Matches patterns like:  Catalogue cardinality [1..5]
    Returns {feature_name: (min, max)}.
    """
    result: dict[str, tuple[int, int]] = {}
    with open(uvl_path, "r") as f:
        for line in f:
            m = re.search(rf"({_FEAT})\s+cardinality\s+\[(\d+)\.\.(\d+)\]", line)
            if m:
                name = _strip_feat(m.group(1))
                result[name] = (int(m.group(2)), int(m.group(3)))
    return result


# ═══════════════════════════════════════════════════════════════════
# Flamapy adapter — the ONLY place that touches Flamapy's concrete API
# ═══════════════════════════════════════════════════════════════════


def _load_spl_model(catalog_dir: str, spl_name: str) -> SPLModel:
    """Load UVL via Flamapy and build an abstract SPLModel."""
    from flamapy.core.discover import DiscoverMetamodels

    uvl_path = os.path.join(catalog_dir, spl_name, f"{spl_name}.uvl")
    if not os.path.isfile(uvl_path):
        raise click.ClickException(f"UVL not found: {uvl_path}")

    dm = DiscoverMetamodels()
    fm = dm.use_transformation_t2m(uvl_path, "fm")

    model = SPLModel(root_name=fm.root.name, uvl_path=uvl_path)

    # ── Walk entire tree recursively ──
    def walk(node, parent_name: str | None):
        attrs = {}
        for attr in node.get_attributes():
            attrs[attr.name] = str(attr.default_value).strip("'\"")

        feat = SPLFeature(
            name=node.name,
            org=attrs.get("org", "splent-io"),
            package=attrs.get("package", ""),
            parent=parent_name,
        )
        model.features[node.name] = feat

        for rel in node.get_relations():
            n_children = len(rel.children)

            # Determine group type with correct cardinality (Table 1)
            if rel.is_alternative():
                gtype = "alternative"
                cmin, cmax = 1, 1
            elif rel.is_or():
                gtype = "or"
                cmin, cmax = 1, n_children
            elif rel.is_mandatory():
                gtype = "mandatory"
                cmin, cmax = 1, 1
            elif rel.is_optional():
                gtype = "optional"
                cmin, cmax = 0, 1
            else:
                # Group cardinality [n..m]
                gtype = "cardinality"
                cmin = getattr(rel, "card_min", 0)
                cmax = getattr(rel, "card_max", 0)

            group = SPLGroup(
                group_type=gtype,
                children=[ch.name for ch in rel.children],
                card_min=cmin,
                card_max=cmax,
            )
            feat.groups.append(group)

            for child in rel.children:
                walk(child, node.name)

    walk(fm.root, None)

    # ── Parse cross-tree constraints from UVL text (robust) ──
    model.constraints = _parse_constraints_from_uvl(uvl_path, model)

    # ── Parse feature cardinalities ──
    cardinalities = _parse_feature_cardinalities(uvl_path)
    for name, (cmin, cmax) in cardinalities.items():
        if name in model.features:
            model.features[name].card_min = cmin
            model.features[name].card_max = cmax

    return model


# ═══════════════════════════════════════════════════════════════════
# Constraint propagation — works purely on SPLModel
# ═══════════════════════════════════════════════════════════════════


def propagate(
    selected: set[str],
    model: SPLModel,
    mandatory: set[str],
    excluded: set[str] | None = None,
) -> tuple[set[str], set[str]]:
    """Full constraint propagation returning (selected, excluded).

    Forward (UVL Table 1):
      1. A selected ∧ (A ⇒ B) → B selected
      2. child selected → parent selected   s(p(f),C) >= s(f,C)
      3. parent selected → mandatory children selected
    Backward:
      4. A selected ∧ (A excludes B) → B excluded
      5. B excluded ∧ (A ⇒ B) → A excluded  (contrapositive)
    """
    result = set(selected) | mandatory
    excl = set(excluded) if excluded else set()
    changed = True
    while changed:
        changed = False

        # Forward implication
        for c in model.constraints:
            if c.kind == "implies" and c.source in result and c.target not in result:
                if c.target in excl:
                    continue  # conflict — SAT validation will catch
                result.add(c.target)
                changed = True

        # Parent-chain activation: child selected → ancestors selected
        for name in list(result):
            for ancestor in model.ancestor_chain(name):
                if ancestor not in result:
                    result.add(ancestor)
                    changed = True

        # Mandatory children: parent selected → mandatory children selected
        for name in list(result):
            feat = model.features.get(name)
            if not feat:
                continue
            for g in feat.groups:
                if g.group_type == "mandatory":
                    for ch in g.children:
                        if ch not in result:
                            result.add(ch)
                            changed = True

        # Excludes: A selected ∧ (A excludes B) → B excluded
        for c in model.constraints:
            if c.kind == "excludes":
                if c.source in result and c.target not in excl:
                    excl.add(c.target)
                    changed = True
                if c.target in result and c.source not in excl:
                    excl.add(c.source)
                    changed = True

        # Contrapositive: B excluded ∧ (A ⇒ B) → A excluded
        for c in model.constraints:
            if c.kind == "implies" and c.target in excl and c.source not in excl:
                excl.add(c.source)
                changed = True

    return result, excl


def deps_for(
    name: str,
    model: SPLModel,
    mandatory: set[str],
    excluded: set[str] | None = None,
) -> set[str]:
    """Features auto-selected if *name* is toggled on (excluding mandatory)."""
    sel, _ = propagate({name}, model, mandatory, excluded)
    return sel - mandatory - {name}


def check_excludes(
    selected: set[str],
    model: SPLModel,
) -> list[tuple[str, str]]:
    """Return list of violated excludes constraints."""
    violations = []
    for c in model.constraints:
        if c.kind == "excludes" and c.source in selected and c.target in selected:
            violations.append((c.source, c.target))
    return violations


# ═══════════════════════════════════════════════════════════════════
# Interactive configurator — works purely on SPLModel
# ═══════════════════════════════════════════════════════════════════


def _render_deps(deps: set[str], already_selected: set[str]) -> str:
    """Render dependency labels for display."""
    if not deps:
        return ""
    labels = []
    for d in sorted(deps):
        if d in already_selected:
            labels.append(click.style(f"{d} \u2713", fg="green"))
        else:
            labels.append(click.style(f"+ {d}", fg="yellow"))
    return f"  (requires: {', '.join(labels)})"


def _prompt_alternative(
    group: SPLGroup,
    selected: set[str],
    excluded: set[str],
    model: SPLModel,
    mandatory: set[str],
    indent: str = "    ",
) -> str:
    """Prompt user to pick exactly one from an alternative group.

    Blocked choices (in excluded set) are shown but not selectable.
    """
    available_indices: list[int] = []
    for i, choice in enumerate(group.children, 1):
        if choice in excluded:
            click.echo(
                f"{indent}{i}) "
                + click.style(f"{choice} (blocked by constraint)", fg="red")
            )
        else:
            deps = deps_for(choice, model, mandatory, excluded)
            deps_str = ""
            if deps:
                deps_str = click.style(
                    f"  (requires: {', '.join(sorted(deps))})", fg="bright_black"
                )
            click.echo(f"{indent}{i}) {choice}{deps_str}")
            available_indices.append(i)

    if not available_indices:
        raise click.ClickException(
            "All alternatives blocked by constraints \u2014 unsatisfiable."
        )

    while True:
        raw = click.prompt(
            f"{indent}Select [1-{len(group.children)}]",
            type=int,
            default=available_indices[0],
        )
        if 1 <= raw <= len(group.children):
            chosen = group.children[raw - 1]
            if chosen in excluded:
                click.secho(
                    f"{indent}{chosen} is blocked by a constraint. Pick another.",
                    fg="red",
                )
                continue
            return chosen
        click.secho(
            f"{indent}Enter a number between 1 and {len(group.children)}.",
            fg="red",
        )


def _prompt_or(
    group: SPLGroup,
    selected: set[str],
    excluded: set[str],
    model: SPLModel,
    mandatory: set[str],
    indent: str = "    ",
) -> list[str]:
    """Prompt user to pick one or more from an or group.

    Blocked choices are displayed but skipped.
    """
    chosen: list[str] = []
    for choice in group.children:
        if choice in excluded:
            click.echo(
                f"{indent}"
                + click.style(f"  {choice} (blocked by constraint)", fg="red")
            )
            continue
        deps = deps_for(choice, model, mandatory, excluded)
        deps_str = _render_deps(deps, selected)
        if click.confirm(
            f"{indent}Include {click.style(choice, bold=True)}?{deps_str}",
            default=False,
        ):
            chosen.append(choice)

    if not chosen:
        # Must select at least one — pick first non-excluded
        for ch in group.children:
            if ch not in excluded:
                click.secho(
                    f"{indent}At least one required \u2014 selecting {ch}",
                    fg="yellow",
                )
                chosen.append(ch)
                break
        if not chosen:
            raise click.ClickException(
                "All or-group members blocked by constraints \u2014 unsatisfiable."
            )
    return chosen


def _prompt_cardinality(
    group: SPLGroup,
    selected: set[str],
    excluded: set[str],
    model: SPLModel,
    mandatory: set[str],
    indent: str = "    ",
) -> list[str]:
    """Prompt user to pick [n..m] from a cardinality group.

    Blocked choices are skipped; auto-selects when needed to meet minimum.
    """
    click.echo(
        f"{indent}Select between {group.card_min} and {group.card_max} "
        f"of the following:"
    )
    available = [ch for ch in group.children if ch not in excluded]
    if len(available) < group.card_min:
        raise click.ClickException(
            f"Not enough available features ({len(available)}) "
            f"to satisfy minimum cardinality ({group.card_min})."
        )

    chosen: list[str] = []
    for choice in group.children:
        if len(chosen) >= group.card_max:
            break
        if choice in excluded:
            click.echo(
                f"{indent}  "
                + click.style(f"{choice} (blocked by constraint)", fg="red")
            )
            continue
        deps = deps_for(choice, model, mandatory, excluded)
        deps_str = _render_deps(deps, selected)
        needed = group.card_min - len(chosen)
        remaining_available = len(
            [
                c
                for c in group.children[group.children.index(choice) + 1 :]
                if c not in excluded and c not in chosen
            ]
        )
        # Auto-select if remaining won't cover minimum
        if needed > 0 and remaining_available < needed:
            click.echo(
                f"{indent}  \u2713 {choice}"
                + click.style("  (auto-selected to meet minimum)", fg="cyan")
            )
            chosen.append(choice)
        elif click.confirm(
            f"{indent}  Include {click.style(choice, bold=True)}?{deps_str}",
            default=False,
        ):
            chosen.append(choice)

    while len(chosen) < group.card_min:
        for ch in group.children:
            if ch not in chosen and ch not in excluded:
                chosen.append(ch)
                click.secho(
                    f"{indent}  \u2713 {ch} (auto-selected to meet minimum)",
                    fg="yellow",
                )
                if len(chosen) >= group.card_min:
                    break
    return chosen


def _propagate_and_report(
    selected: set[str],
    excluded: set[str],
    model: SPLModel,
    mandatory: set[str],
    prefix: str,
):
    """Run propagation, update sets in-place, and report changes."""
    new_sel, new_excl = propagate(selected, model, mandatory, excluded)

    auto = new_sel - selected - mandatory
    for name in sorted(auto):
        click.echo(
            f"{prefix}\u21b3 {name}" + click.style("  (auto-selected)", fg="cyan")
        )
    selected.update(new_sel - mandatory)

    new_blocked = new_excl - excluded
    for name in sorted(new_blocked):
        click.echo(
            f"{prefix}\u2717 {name}"
            + click.style("  (now blocked by constraint)", fg="red")
        )
    excluded.update(new_excl)


def _configure_subtree(
    name: str,
    selected: set[str],
    excluded: set[str],
    model: SPLModel,
    mandatory: set[str],
    indent: int = 4,
):
    """Recursively walk the feature tree and prompt for selections.

    Processing order per feature:
      Phase 1 — mandatory groups: recurse into children (already selected)
      Phase 2 — selection groups (alternative/or/cardinality): prompt + recurse
      Phase 3 — optional groups: prompt, recurse if selected
    """
    feat = model.features.get(name)
    if not feat:
        return
    prefix = " " * indent

    # Warn if feature has cardinality > 1 (clone semantics not fully supported)
    if feat.card_max > 1:
        click.secho(
            f"{prefix}Note: {name} has feature cardinality "
            f"[{feat.card_min}..{feat.card_max}] \u2014 "
            f"clone semantics (Section 5.2) are not yet implemented; "
            f"treating as single instance.",
            fg="yellow",
        )

    # ── Phase 1: mandatory groups → recurse ──────────────────
    for group in feat.groups:
        if group.group_type == "mandatory":
            for ch in group.children:
                _configure_subtree(ch, selected, excluded, model, mandatory, indent)

    # ── Phase 2: selection groups (alternative/or/cardinality) ─
    for group in feat.groups:
        if group.group_type not in ("alternative", "or", "cardinality"):
            continue
        # Only prompt if this feature is active
        if name not in selected and name not in mandatory:
            continue

        label_map = {
            "alternative": "pick exactly one",
            "or": "pick one or more",
            "cardinality": f"pick {group.card_min}..{group.card_max}",
        }
        click.secho(f"{prefix}{name}", bold=True, nl=False)
        click.echo(click.style(f"  ({label_map[group.group_type]})", fg="bright_black"))

        sub_indent = prefix + "  "

        if group.group_type == "alternative":
            chosen = _prompt_alternative(
                group, selected, excluded, model, mandatory, indent=sub_indent
            )
            selected.add(chosen)
            _propagate_and_report(
                selected, excluded, model, mandatory, sub_indent + "  "
            )
            click.echo(
                f"{sub_indent}\u2192 {click.style(chosen, fg='green', bold=True)}"
            )
            click.echo()
            _configure_subtree(chosen, selected, excluded, model, mandatory, indent + 2)

        elif group.group_type == "or":
            chosen_list = _prompt_or(
                group, selected, excluded, model, mandatory, indent=sub_indent
            )
            selected.update(chosen_list)
            _propagate_and_report(
                selected, excluded, model, mandatory, sub_indent + "  "
            )
            click.echo()
            for ch in chosen_list:
                _configure_subtree(ch, selected, excluded, model, mandatory, indent + 2)

        elif group.group_type == "cardinality":
            chosen_list = _prompt_cardinality(
                group, selected, excluded, model, mandatory, indent=sub_indent
            )
            selected.update(chosen_list)
            _propagate_and_report(
                selected, excluded, model, mandatory, sub_indent + "  "
            )
            click.echo()
            for ch in chosen_list:
                _configure_subtree(ch, selected, excluded, model, mandatory, indent + 2)

    # ── Phase 3: optional groups → prompt, recurse if selected ─
    has_optionals = any(
        g.group_type == "optional"
        for g in feat.groups
        if any(ch not in mandatory and ch not in selected for ch in g.children)
    )
    showed_header = False

    for group in feat.groups:
        if group.group_type != "optional":
            continue
        for ch in group.children:
            # Already handled by mandatory set
            if ch in mandatory:
                _configure_subtree(ch, selected, excluded, model, mandatory, indent)
                continue

            # Already auto-selected by propagation
            if ch in selected:
                click.echo(
                    f"{prefix}\u2713 {ch}"
                    + click.style("  (auto-selected by dependency)", fg="cyan")
                )
                _configure_subtree(ch, selected, excluded, model, mandatory, indent + 2)
                continue

            # Blocked by constraint
            if ch in excluded:
                click.echo(
                    f"{prefix}\u2717 {click.style(ch, fg='red')}"
                    + click.style("  (blocked by constraint)", fg="red")
                )
                continue

            # Show header once
            if not showed_header and has_optionals:
                click.secho(f"{prefix}Optional features:", bold=True)
                click.echo()
                showed_header = True

            # Compute dependencies and conflicts for display
            deps = deps_for(ch, model, mandatory, excluded)
            deps_str = _render_deps(deps, selected)

            conflict_str = ""
            conflicts = []
            for c in model.constraints:
                if c.kind == "excludes":
                    if c.source == ch and c.target in (selected | mandatory):
                        conflicts.append(c.target)
                    elif c.target == ch and c.source in (selected | mandatory):
                        conflicts.append(c.source)
            if conflicts:
                conflict_str = click.style(
                    f"  (conflicts: {', '.join(sorted(conflicts))})", fg="red"
                )

            if click.confirm(
                f"{prefix}Include {click.style(ch, bold=True)}?"
                f"{deps_str}{conflict_str}",
                default=False,
            ):
                selected.add(ch)
                _propagate_and_report(
                    selected, excluded, model, mandatory, prefix + "  "
                )
                # Recurse into the newly selected feature
                _configure_subtree(ch, selected, excluded, model, mandatory, indent + 2)


# ═══════════════════════════════════════════════════════════════════
# CLI command
# ═══════════════════════════════════════════════════════════════════


@click.command(
    "product:configure",
    short_help="Interactive feature configurator for the active product.",
)
def product_configure():
    """
    Interactive SPL feature configurator.

    \b
    Reads the UVL model from the SPL catalog, walks the full feature tree
    recursively, respects mandatory/optional/alternative/or/cardinality
    group semantics, propagates constraints (including parent-chain,
    excludes, and contrapositive), validates with Flamapy,
    and writes to pyproject.toml.
    """
    product = context.require_app()
    workspace = str(context.workspace())
    product_path = os.path.join(workspace, product)
    pyproject_path = os.path.join(product_path, "pyproject.toml")

    if not os.path.isfile(pyproject_path):
        raise click.ClickException(f"Product not found: {product}")

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    spl_name = data.get("tool", {}).get("splent", {}).get("spl")
    if not spl_name:
        raise click.ClickException(
            "No SPL configured. Set [tool.splent].spl in pyproject.toml"
        )

    catalog_dir = os.path.join(workspace, "splent_catalog")
    model = _load_spl_model(catalog_dir, spl_name)

    # All features that are mandatory from root down
    mandatory = model.all_mandatory_recursive()

    # User selections (not including mandatory) and blocked features
    selected: set[str] = set()
    excluded: set[str] = set()

    # Initial propagation to seed the excluded set
    _, initial_excl = propagate(set(), model, mandatory)
    excluded.update(initial_excl)

    # ── Header ──────────────────────────────────────────────
    click.echo()
    click.secho(f"  SPL Configurator \u2014 {spl_name}", bold=True)
    click.echo(click.style(f"  {'\u2500' * 55}", fg="bright_black"))
    click.echo()

    # ── Show mandatory leaf features ───────────────────────
    click.secho("  Mandatory (auto-selected):", fg="green")
    for name in sorted(mandatory):
        if name == model.root_name:
            continue
        feat = model.features[name]
        has_selection_group = any(
            g.group_type in ("alternative", "or", "cardinality") for g in feat.groups
        )
        if not has_selection_group:
            click.echo(f"    \u2713 {name}")
    click.echo()

    # ── Recursive tree walk for interactive configuration ──
    _configure_subtree(model.root_name, selected, excluded, model, mandatory, indent=4)

    # ── Build final selection ──────────────────────────────
    all_selected, _ = propagate(selected, model, mandatory, excluded)

    # ── Summary ────────────────────────────────────────────
    click.echo()
    click.secho("  \u2500\u2500\u2500 Configuration \u2500\u2500\u2500", bold=True)
    click.echo()

    def render_tree(name: str, indent: int = 4):
        feat = model.features[name]
        pfx = " " * indent

        if name == model.root_name:
            pass  # skip root display
        elif name in all_selected:
            tag = ""
            if name in mandatory:
                tag = click.style("  (mandatory)", fg="bright_black")
            click.echo(f"{pfx}\u2713 {name}{tag}")
        else:
            click.echo(click.style(f"{pfx}\u00b7 {name}", fg="bright_black"))

        for g in feat.groups:
            for ch in g.children:
                render_tree(ch, indent + (0 if name == model.root_name else 2))

    render_tree(model.root_name)
    click.echo()

    # ── Check excludes ─────────────────────────────────────
    violations = check_excludes(all_selected, model)
    if violations:
        for src, tgt in violations:
            click.secho(f"  Conflict: {src} excludes {tgt}", fg="red")

    # ── Validate with Flamapy ──────────────────────────────
    click.echo("  Validating configuration... ", nl=False)
    from flamapy.interfaces.python.flamapy_feature_model import FLAMAFeatureModel
    from splent_cli.commands.uvl.uvl_utils import (
        list_all_features_from_uvl,
        write_csvconf_full,
    )

    universe, _ = list_all_features_from_uvl(model.uvl_path)
    conf_path = write_csvconf_full(universe, all_selected)

    try:
        fma = FLAMAFeatureModel(model.uvl_path)
        ok = fma.satisfiable_configuration(conf_path, full_configuration=False)
    finally:
        try:
            os.remove(conf_path)
        except OSError:
            pass

    if not ok:
        click.secho("UNSATISFIABLE", fg="red", bold=True)
        click.echo("  The selected configuration violates UVL constraints.")
        raise SystemExit(1)

    click.secho("satisfiable", fg="green", bold=True)
    click.echo()

    # ── Apply ──────────────────────────────────────────────
    if not click.confirm("  Apply this configuration? (attach features to product)"):
        click.echo("  Cancelled.")
        return

    click.echo()

    import subprocess
    import tomli_w
    from splent_cli.utils.feature_utils import write_features_to_data

    # Clear existing
    with open(pyproject_path, "rb") as f:
        pydata = tomllib.load(f)
    write_features_to_data(pydata, [])
    with open(pyproject_path, "wb") as f:
        tomli_w.dump(pydata, f)

    # Clean stale symlinks
    features_dir = os.path.join(product_path, "features")
    if os.path.isdir(features_dir):
        for org_dir in os.listdir(features_dir):
            org_path = os.path.join(features_dir, org_dir)
            if not os.path.isdir(org_path):
                continue
            for entry in os.listdir(org_path):
                entry_path = os.path.join(org_path, entry)
                if os.path.islink(entry_path):
                    os.unlink(entry_path)

    # Resolve versions and build feature entries
    cache_dir = os.path.join(workspace, ".splent_cache", "features")
    feature_entries = []

    for name in sorted(all_selected):
        feat = model.features.get(name)
        if not feat or feat.is_abstract:
            continue  # skip root and abstract group parents

        org = feat.org
        package = feat.package
        org_safe = normalize_namespace(org)
        org_cache = os.path.join(cache_dir, org_safe)
        version = None

        if os.path.isdir(org_cache):
            candidates = sorted(
                [e for e in os.listdir(org_cache) if e.startswith(f"{package}@")],
                reverse=True,
            )
            if candidates:
                version = candidates[0].split("@", 1)[1]

        if not version:
            try:
                result = subprocess.run(
                    ["pip", "index", "versions", package],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0 and "(" in result.stdout:
                    pypi_ver = result.stdout.split("(")[1].split(")")[0].strip()
                    version = f"v{pypi_ver}"
            except Exception:
                pass

        entry = f"{org}/{package}@{version}" if version else f"{org}/{package}"
        feature_entries.append(entry)
        click.echo(f"    {entry}")

    # Write to pyproject
    with open(pyproject_path, "rb") as pf:
        current_data = tomllib.load(pf)
    write_features_to_data(current_data, feature_entries)
    with open(pyproject_path, "wb") as pf:
        tomli_w.dump(current_data, pf)

    click.echo()
    click.secho("  Configuration applied.", fg="green", bold=True)
    click.echo()
    click.echo("  Next: download and link features with:")
    click.echo("     splent product:sync")
    click.echo()


cli_command = product_configure
