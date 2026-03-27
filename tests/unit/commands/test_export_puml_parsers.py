"""Tests for pure parser functions in export_puml.py."""
import pytest

from splent_cli.commands.export_puml import (
    _parse_models,
    _parse_uvl,
    _read_contract,
)


# ---------------------------------------------------------------------------
# _parse_uvl
# ---------------------------------------------------------------------------

class TestParseUvl:
    def _uvl(self, tmp_path, content):
        p = tmp_path / "model.uvl"
        p.write_text(content)
        return str(p)

    def test_parses_feature_names(self, tmp_path):
        path = self._uvl(tmp_path, (
            "features\n"
            "    auth { org 'splent-io' package 'splent_feature_auth' }\n"
            "    profile { org 'splent-io' package 'splent_feature_profile' }\n"
            "constraints\n"
        ))
        data = _parse_uvl(path)
        names = [f["name"] for f in data["features"]]
        assert "auth" in names
        assert "profile" in names

    def test_parses_package_and_org(self, tmp_path):
        path = self._uvl(tmp_path, (
            "features\n"
            "    auth { org 'splent-io' package 'splent_feature_auth' }\n"
            "constraints\n"
        ))
        data = _parse_uvl(path)
        feat = data["features"][0]
        assert feat["package"] == "splent_feature_auth"
        assert feat["org"] == "splent-io"

    def test_parses_mandatory_cardinality(self, tmp_path):
        path = self._uvl(tmp_path, (
            "features\n"
            "    mandatory\n"
            "    auth { org 'x' package 'splent_feature_auth' }\n"
            "    optional\n"
            "    profile { org 'x' package 'splent_feature_profile' }\n"
            "constraints\n"
        ))
        data = _parse_uvl(path)
        by_name = {f["name"]: f for f in data["features"]}
        assert by_name["auth"]["cardinality"] == "mandatory"
        assert by_name["profile"]["cardinality"] == "optional"

    def test_default_cardinality_is_optional(self, tmp_path):
        path = self._uvl(tmp_path, (
            "features\n"
            "    auth { org 'x' package 'splent_feature_auth' }\n"
            "constraints\n"
        ))
        data = _parse_uvl(path)
        assert data["features"][0]["cardinality"] == "optional"

    def test_parses_constraints(self, tmp_path):
        path = self._uvl(tmp_path, (
            "features\n"
            "    auth { org 'x' package 'splent_feature_auth' }\n"
            "constraints\n"
            "    profile => auth\n"
        ))
        data = _parse_uvl(path)
        assert any("profile => auth" in c for c in data["constraints"])

    def test_empty_file_returns_empty(self, tmp_path):
        path = self._uvl(tmp_path, "")
        data = _parse_uvl(path)
        assert data["features"] == []
        assert data["constraints"] == []

    def test_no_features_section(self, tmp_path):
        path = self._uvl(tmp_path, "constraints\n    a => b\n")
        data = _parse_uvl(path)
        assert data["features"] == []


# ---------------------------------------------------------------------------
# _parse_models
# ---------------------------------------------------------------------------

class TestParseModels:
    def test_returns_empty_for_missing_file(self, tmp_path):
        assert _parse_models(str(tmp_path / "models.py")) == []

    def test_parses_integer_primary_key(self, tmp_path):
        p = tmp_path / "models.py"
        p.write_text(
            "class User(db.Model):\n"
            "    id = db.Column(db.Integer, primary_key=True)\n"
        )
        result = _parse_models(str(p))
        assert len(result) == 1
        attrs = {a["name"]: a for a in result[0]["attributes"]}
        assert attrs["id"]["pk"] is True
        assert attrs["id"]["type"] == "int"
        assert attrs["id"]["nullable"] is False

    def test_parses_string_column(self, tmp_path):
        p = tmp_path / "models.py"
        p.write_text(
            "class User(db.Model):\n"
            "    name = db.Column(db.String(100))\n"
        )
        result = _parse_models(str(p))
        attrs = {a["name"]: a for a in result[0]["attributes"]}
        assert attrs["name"]["type"] == "str"
        assert attrs["name"]["pk"] is False

    def test_parses_unique_column(self, tmp_path):
        p = tmp_path / "models.py"
        p.write_text(
            "class User(db.Model):\n"
            "    email = db.Column(db.String(200), unique=True)\n"
        )
        result = _parse_models(str(p))
        attrs = {a["name"]: a for a in result[0]["attributes"]}
        assert attrs["email"]["unique"] is True

    def test_parses_foreign_key(self, tmp_path):
        p = tmp_path / "models.py"
        p.write_text(
            "class Post(db.Model):\n"
            "    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))\n"
        )
        result = _parse_models(str(p))
        fks = result[0]["fks"]
        assert len(fks) == 1
        assert fks[0]["column"] == "user_id"
        assert fks[0]["target_table"] == "user"
        assert fks[0]["target_col"] == "id"

    def test_parses_relationship(self, tmp_path):
        p = tmp_path / "models.py"
        p.write_text(
            "class User(db.Model):\n"
            "    posts = db.relationship(Post, backref='user')\n"
        )
        result = _parse_models(str(p))
        rels = result[0]["relationships"]
        assert len(rels) == 1
        assert rels[0]["target"] == "Post"
        assert rels[0]["uselist"] is True

    def test_relationship_uselist_false(self, tmp_path):
        p = tmp_path / "models.py"
        p.write_text(
            "class User(db.Model):\n"
            "    profile = db.relationship(Profile, uselist=False)\n"
        )
        result = _parse_models(str(p))
        assert result[0]["relationships"][0]["uselist"] is False

    def test_includes_public_methods_only(self, tmp_path):
        p = tmp_path / "models.py"
        p.write_text(
            "class User(db.Model):\n"
            "    def greet(self):\n"
            "        pass\n"
            "    def _private(self):\n"
            "        pass\n"
        )
        result = _parse_models(str(p))
        methods = result[0]["methods"]
        assert "greet" in methods
        assert "_private" not in methods

    def test_parses_multiple_classes(self, tmp_path):
        p = tmp_path / "models.py"
        p.write_text(
            "class User(db.Model):\n"
            "    id = db.Column(db.Integer, primary_key=True)\n"
            "class Post(db.Model):\n"
            "    id = db.Column(db.Integer, primary_key=True)\n"
        )
        result = _parse_models(str(p))
        names = [c["name"] for c in result]
        assert "User" in names
        assert "Post" in names

    def test_empty_file_returns_empty(self, tmp_path):
        p = tmp_path / "models.py"
        p.write_text("")
        assert _parse_models(str(p)) == []


# ---------------------------------------------------------------------------
# _read_contract
# ---------------------------------------------------------------------------

class TestReadContract:
    def test_returns_none_when_no_pyproject(self, tmp_path):
        assert _read_contract(str(tmp_path)) is None

    def test_returns_contract_dict(self, tmp_path):
        (tmp_path / "pyproject.toml").write_bytes(
            b"[tool.splent.contract]\n"
            b'routes = ["/auth/login"]\n'
        )
        result = _read_contract(str(tmp_path))
        assert result is not None
        assert "/auth/login" in result["routes"]

    def test_returns_none_when_no_contract_key(self, tmp_path):
        (tmp_path / "pyproject.toml").write_bytes(
            b'[tool.splent]\nname = "test"\n'
        )
        assert _read_contract(str(tmp_path)) is None

    def test_returns_none_when_no_tool_splent_section(self, tmp_path):
        (tmp_path / "pyproject.toml").write_bytes(
            b'[project]\nname = "test"\n'
        )
        assert _read_contract(str(tmp_path)) is None
