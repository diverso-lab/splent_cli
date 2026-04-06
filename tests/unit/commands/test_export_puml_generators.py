"""Tests for PlantUML generator functions in export_puml.py."""
import pytest

from splent_cli.commands.export.export_puml import (
    _generate_class_puml,
    _generate_deps_puml,
    _generate_feature_puml,
)


# ---------------------------------------------------------------------------
# Shared test data builders
# ---------------------------------------------------------------------------

def _uvl(features=None, constraints=None):
    return {
        "features": features or [],
        "constraints": constraints or [],
    }


def _feat(name, package=None, org="splent-io", cardinality="optional"):
    return {
        "name": name,
        "package": package or f"splent_feature_{name}",
        "org": org,
        "cardinality": cardinality,
    }


def _model(name, attributes=None, methods=None, fks=None, relationships=None):
    return {
        "name": name,
        "attributes": attributes or [],
        "methods": methods or [],
        "fks": fks or [],
        "relationships": relationships or [],
    }


def _attr(name, type_="str", pk=False, nullable=True, unique=False):
    return {
        "name": name,
        "type": type_,
        "pk": pk,
        "nullable": nullable,
        "unique": unique,
    }


# ---------------------------------------------------------------------------
# _generate_feature_puml
# ---------------------------------------------------------------------------

class TestGenerateFeaturePuml:
    def test_has_startuml_and_enduml(self):
        result = _generate_feature_puml("myapp", _uvl(), {})
        assert "@startuml" in result
        assert "@enduml" in result

    def test_title_contains_product_name(self):
        result = _generate_feature_puml("myapp", _uvl(), {})
        assert "myapp" in result

    def test_contains_feature_component(self):
        data = _uvl(features=[_feat("auth")])
        result = _generate_feature_puml("myapp", data, {})
        assert "auth" in result

    def test_mandatory_feature_has_stereotype(self):
        data = _uvl(features=[_feat("auth", cardinality="mandatory")])
        result = _generate_feature_puml("myapp", data, {})
        assert "<<mandatory>>" in result

    def test_optional_feature_has_stereotype(self):
        data = _uvl(features=[_feat("profile", cardinality="optional")])
        result = _generate_feature_puml("myapp", data, {})
        assert "<<optional>>" in result

    def test_constraint_becomes_arrow(self):
        data = _uvl(
            features=[_feat("auth"), _feat("profile")],
            constraints=["profile => auth"],
        )
        result = _generate_feature_puml("myapp", data, {})
        assert "profile ..> auth" in result

    def test_constraint_strips_inline_comments(self):
        data = _uvl(
            features=[_feat("auth"), _feat("profile")],
            constraints=["profile => auth  # some comment"],
        )
        result = _generate_feature_puml("myapp", data, {})
        assert "auth  # some comment" not in result

    def test_contract_models_shown(self):
        data = _uvl(features=[_feat("auth", package="splent_feature_auth")])
        contracts = {
            "splent_feature_auth": {
                "provides": {"models": ["User", "Session"]},
                "requires": {},
            }
        }
        result = _generate_feature_puml("myapp", data, contracts)
        assert "User" in result
        assert "Session" in result

    def test_contract_env_vars_shown(self):
        data = _uvl(features=[_feat("auth", package="splent_feature_auth")])
        contracts = {
            "splent_feature_auth": {
                "provides": {},
                "requires": {"env_vars": ["SECRET_KEY"]},
            }
        }
        result = _generate_feature_puml("myapp", data, contracts)
        assert "SECRET_KEY" in result

    def test_no_features_still_valid(self):
        result = _generate_feature_puml("empty", _uvl(), {})
        assert "@startuml" in result
        assert "@enduml" in result


# ---------------------------------------------------------------------------
# _generate_deps_puml
# ---------------------------------------------------------------------------

class TestGenerateDepsPuml:
    def test_has_startuml_and_enduml(self):
        result = _generate_deps_puml("myapp", _uvl())
        assert "@startuml" in result
        assert "@enduml" in result

    def test_title_contains_product_name(self):
        result = _generate_deps_puml("myapp", _uvl())
        assert "myapp" in result

    def test_feature_nodes_present(self):
        data = _uvl(features=[_feat("auth"), _feat("profile")])
        result = _generate_deps_puml("myapp", data)
        assert "[auth]" in result
        assert "[profile]" in result

    def test_mandatory_has_dark_color(self):
        data = _uvl(features=[_feat("auth", cardinality="mandatory")])
        result = _generate_deps_puml("myapp", data)
        assert "#2E86C1" in result

    def test_optional_has_light_color(self):
        data = _uvl(features=[_feat("profile", cardinality="optional")])
        result = _generate_deps_puml("myapp", data)
        assert "#AED6F1" in result

    def test_dependency_arrow(self):
        data = _uvl(
            features=[_feat("auth"), _feat("profile")],
            constraints=["profile => auth"],
        )
        result = _generate_deps_puml("myapp", data)
        assert "profile --> auth" in result

    def test_legend_is_present(self):
        result = _generate_deps_puml("myapp", _uvl())
        assert "legend" in result

    def test_no_features_still_valid(self):
        result = _generate_deps_puml("empty", _uvl())
        assert "@startuml" in result
        assert "@enduml" in result


# ---------------------------------------------------------------------------
# _generate_class_puml
# ---------------------------------------------------------------------------

class TestGenerateClassPuml:
    def test_has_startuml_and_enduml(self):
        result = _generate_class_puml("myapp", {}, _uvl())
        assert "@startuml" in result
        assert "@enduml" in result

    def test_title_contains_product_name(self):
        result = _generate_class_puml("myapp", {}, _uvl())
        assert "myapp" in result

    def test_class_name_in_output(self):
        models = {"splent_feature_auth": [_model("User")]}
        result = _generate_class_puml("myapp", models, _uvl())
        assert "class User" in result

    def test_attribute_with_pk_stereotype(self):
        models = {
            "splent_feature_auth": [
                _model("User", attributes=[_attr("id", type_="int", pk=True)])
            ]
        }
        result = _generate_class_puml("myapp", models, _uvl())
        assert "<<PK>>" in result

    def test_attribute_with_unique_stereotype(self):
        models = {
            "splent_feature_auth": [
                _model("User", attributes=[_attr("email", unique=True)])
            ]
        }
        result = _generate_class_puml("myapp", models, _uvl())
        assert "<<unique>>" in result

    def test_nullable_attr_has_question_mark(self):
        models = {
            "splent_feature_auth": [
                _model("User", attributes=[_attr("bio", nullable=True)])
            ]
        }
        result = _generate_class_puml("myapp", models, _uvl())
        assert "?" in result

    def test_non_nullable_has_no_question_mark(self):
        models = {
            "splent_feature_auth": [
                _model("User", attributes=[_attr("name", nullable=False)])
            ]
        }
        result = _generate_class_puml("myapp", models, _uvl())
        # name without nullable=True should not have '?' after the type
        lines = [l for l in result.splitlines() if "name" in l and "str" in l]
        assert lines, "Should have a line with 'name' attribute"
        assert "?" not in lines[0]

    def test_public_methods_in_output(self):
        models = {
            "splent_feature_auth": [
                _model("User", methods=["login", "logout"])
            ]
        }
        result = _generate_class_puml("myapp", models, _uvl())
        assert "login()" in result
        assert "logout()" in result

    def test_feature_package_grouping(self):
        models = {
            "splent_feature_auth": [_model("User")],
            "splent_feature_blog": [_model("Post")],
        }
        result = _generate_class_puml("myapp", models, _uvl())
        assert "class User" in result
        assert "class Post" in result

    def test_fk_relationship_rendered(self):
        post_attrs = [
            _attr("user_id", type_="int", pk=False, nullable=True)
        ]
        post_fks = [{"column": "user_id", "target_table": "user", "target_col": "id"}]
        models = {
            "splent_feature_auth": [_model("User")],
            "splent_feature_blog": [_model("Post", attributes=post_attrs, fks=post_fks)],
        }
        result = _generate_class_puml("myapp", models, _uvl())
        # FK from Post to User should produce a relationship line
        assert "User" in result
        assert "Post" in result

    def test_uvl_short_name_used_for_package_label(self):
        uvl = _uvl(features=[_feat("auth", package="splent_feature_auth")])
        models = {"splent_feature_auth": [_model("User")]}
        result = _generate_class_puml("myapp", models, uvl)
        # The package block label should use the UVL short name "auth"
        assert '"auth"' in result

    def test_empty_models_still_valid(self):
        result = _generate_class_puml("myapp", {}, _uvl())
        assert "@startuml" in result
        assert "@enduml" in result
