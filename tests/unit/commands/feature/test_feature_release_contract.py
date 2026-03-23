"""
Tests for the contract auto-inference logic in feature_release.

These functions are pure (file I/O + regex), no CLI invocation needed.
"""

import tomllib
from pathlib import Path
import pytest

from splent_cli.commands.feature.feature_release import (
    _extract_routes,
    _extract_blueprints,
    _extract_models,
    _scan_dependencies,
    infer_contract,
    write_contract,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _src(tmp_path, org="splent_io", name="my_feature"):
    """Create the expected src/<org>/<name>/ layout and return the src dir."""
    d = tmp_path / "src" / org / name
    d.mkdir(parents=True)
    return d


# ---------------------------------------------------------------------------
# _extract_routes
# ---------------------------------------------------------------------------

class TestExtractRoutes:
    def test_single_route(self, tmp_path):
        f = tmp_path / "routes.py"
        f.write_text("@my_bp.route('/home', methods=['GET'])\ndef index(): pass\n")
        assert _extract_routes(f) == ["/home"]

    def test_multiple_routes(self, tmp_path):
        f = tmp_path / "routes.py"
        f.write_text(
            "@my_bp.route('/signup')\ndef signup(): pass\n"
            "@my_bp.route('/login')\ndef login(): pass\n"
        )
        assert _extract_routes(f) == ["/login", "/signup"]

    def test_deduplicates_same_route(self, tmp_path):
        f = tmp_path / "routes.py"
        f.write_text("@bp.route('/same')\n@bp.route('/same')\n")
        assert _extract_routes(f) == ["/same"]

    def test_route_with_variable(self, tmp_path):
        f = tmp_path / "routes.py"
        f.write_text("@bp.route('/confirm/<token>')\ndef confirm(token): pass\n")
        assert _extract_routes(f) == ["/confirm/<token>"]

    def test_missing_file_returns_empty(self, tmp_path):
        assert _extract_routes(tmp_path / "nonexistent.py") == []

    def test_file_with_no_routes(self, tmp_path):
        f = tmp_path / "routes.py"
        f.write_text("# nothing here\n")
        assert _extract_routes(f) == []


# ---------------------------------------------------------------------------
# _extract_blueprints
# ---------------------------------------------------------------------------

class TestExtractBlueprints:
    def test_base_blueprint(self, tmp_path):
        f = tmp_path / "__init__.py"
        f.write_text("auth_bp = BaseBlueprint('auth', __name__)\n")
        assert _extract_blueprints(f) == ["auth_bp"]

    def test_plain_blueprint(self, tmp_path):
        f = tmp_path / "__init__.py"
        f.write_text("my_bp = Blueprint('my', __name__)\n")
        assert _extract_blueprints(f) == ["my_bp"]

    def test_multiple_blueprints(self, tmp_path):
        f = tmp_path / "__init__.py"
        f.write_text(
            "a_bp = BaseBlueprint('a', __name__)\n"
            "b_bp = BaseBlueprint('b', __name__)\n"
        )
        assert _extract_blueprints(f) == ["a_bp", "b_bp"]

    def test_missing_file_returns_empty(self, tmp_path):
        assert _extract_blueprints(tmp_path / "nonexistent.py") == []


# ---------------------------------------------------------------------------
# _extract_models
# ---------------------------------------------------------------------------

class TestExtractModels:
    def test_single_model(self, tmp_path):
        f = tmp_path / "models.py"
        f.write_text("class User(db.Model):\n    id = db.Column(db.Integer)\n")
        assert _extract_models(f) == ["User"]

    def test_multiple_models(self, tmp_path):
        f = tmp_path / "models.py"
        f.write_text(
            "class User(db.Model): pass\n"
            "class Profile(db.Model): pass\n"
        )
        assert _extract_models(f) == ["Profile", "User"]

    def test_non_model_classes_excluded(self, tmp_path):
        f = tmp_path / "models.py"
        f.write_text(
            "class Helper:\n    pass\n"
            "class Token(db.Model):\n    pass\n"
        )
        assert _extract_models(f) == ["Token"]

    def test_missing_file_returns_empty(self, tmp_path):
        assert _extract_models(tmp_path / "nonexistent.py") == []


# ---------------------------------------------------------------------------
# _scan_dependencies
# ---------------------------------------------------------------------------

class TestScanDependencies:
    def test_detects_inter_feature_import(self, tmp_path):
        f = tmp_path / "routes.py"
        f.write_text("from splent_io.splent_feature_mail import MailService\n")
        req_features, env_vars = _scan_dependencies(tmp_path, "splent_feature_auth")
        assert "mail" in req_features

    def test_excludes_own_feature(self, tmp_path):
        f = tmp_path / "__init__.py"
        f.write_text("from splent_io.splent_feature_auth import auth_bp\n")
        req_features, _ = _scan_dependencies(tmp_path, "splent_feature_auth")
        assert "auth" not in req_features

    def test_detects_os_getenv(self, tmp_path):
        f = tmp_path / "config.py"
        f.write_text('redis_url = os.getenv("REDIS_URL", "redis://localhost")\n')
        _, env_vars = _scan_dependencies(tmp_path, "splent_feature_redis")
        assert "REDIS_URL" in env_vars

    def test_detects_os_environ_get(self, tmp_path):
        f = tmp_path / "services.py"
        f.write_text('host = os.environ.get("MAIL_SERVER", "localhost")\n')
        _, env_vars = _scan_dependencies(tmp_path, "splent_feature_mail")
        assert "MAIL_SERVER" in env_vars

    def test_detects_os_environ_subscript(self, tmp_path):
        f = tmp_path / "app.py"
        f.write_text('key = os.environ["SECRET_KEY"]\n')
        _, env_vars = _scan_dependencies(tmp_path, "splent_feature_x")
        assert "SECRET_KEY" in env_vars

    def test_no_dependencies_returns_empty(self, tmp_path):
        f = tmp_path / "routes.py"
        f.write_text("# no imports, no getenv\n")
        req_features, env_vars = _scan_dependencies(tmp_path, "splent_feature_x")
        assert req_features == []
        assert env_vars == []

    def test_scans_subdirectories(self, tmp_path):
        sub = tmp_path / "subpkg"
        sub.mkdir()
        (sub / "helper.py").write_text('os.getenv("DEEP_VAR")\n')
        _, env_vars = _scan_dependencies(tmp_path, "splent_feature_x")
        assert "DEEP_VAR" in env_vars


# ---------------------------------------------------------------------------
# infer_contract (integration)
# ---------------------------------------------------------------------------

class TestInferContract:
    def _setup_feature(self, tmp_path, org="splent_io", name="splent_feature_demo"):
        """Create a minimal feature layout and return the feature root."""
        feature_root = tmp_path / name
        src = feature_root / "src" / org / name
        src.mkdir(parents=True)

        (src / "__init__.py").write_text(
            f"demo_bp = BaseBlueprint('demo', __name__)\n"
            "def init_feature(app): pass\n"
            "def inject_context_vars(app): return {}\n"
        )
        (src / "routes.py").write_text(
            "@demo_bp.route('/demo')\ndef index(): pass\n"
        )
        (src / "models.py").write_text(
            "class DemoItem(db.Model):\n    id = db.Column(db.Integer)\n"
        )
        return str(feature_root)

    def test_basic_inference(self, tmp_path):
        feature_root = self._setup_feature(tmp_path)
        contract = infer_contract(feature_root, "splent_io", "splent_feature_demo")
        assert "/demo" in contract["routes"]
        assert "demo_bp" in contract["blueprints"]
        assert "DemoItem" in contract["models"]
        assert contract["commands"] == []

    def test_infers_feature_dependency(self, tmp_path):
        feature_root = self._setup_feature(tmp_path)
        src = Path(feature_root) / "src" / "splent_io" / "splent_feature_demo"
        (src / "services.py").write_text(
            "from splent_io.splent_feature_mail import MailService\n"
        )
        contract = infer_contract(feature_root, "splent_io", "splent_feature_demo")
        assert "mail" in contract["requires_features"]

    def test_infers_env_vars(self, tmp_path):
        feature_root = self._setup_feature(tmp_path)
        src = Path(feature_root) / "src" / "splent_io" / "splent_feature_demo"
        (src / "config.py").write_text('os.getenv("API_KEY")\n')
        contract = infer_contract(feature_root, "splent_io", "splent_feature_demo")
        assert "API_KEY" in contract["env_vars"]

    def test_namespace_with_dash_normalised(self, tmp_path):
        """Namespace 'splent-io' should map to src dir 'splent_io'."""
        name = "splent_feature_demo"
        src = tmp_path / name / "src" / "splent_io" / name
        src.mkdir(parents=True)
        (src / "__init__.py").write_text("demo_bp = BaseBlueprint('demo', __name__)\n")
        (src / "routes.py").write_text("@demo_bp.route('/demo')\ndef i(): pass\n")
        (src / "models.py").write_text("")
        contract = infer_contract(str(tmp_path / name), "splent-io", name)
        assert "/demo" in contract["routes"]


# ---------------------------------------------------------------------------
# write_contract
# ---------------------------------------------------------------------------

class TestWriteContract:
    def _base_pyproject(self, tmp_path, with_contract=False):
        text = (
            "[project]\nname = \"splent_feature_x\"\nversion = \"1.0.0\"\n"
            "\n[tool.setuptools]\npackage-dir = { \"\" = \"src\" }\n"
        )
        if with_contract:
            text += (
                "\n[tool.splent.contract]\n"
                "description = \"existing description\"\n"
                "\n[tool.splent.contract.provides]\n"
                "routes = [\"/old\"]\n"
            )
        path = tmp_path / "pyproject.toml"
        path.write_text(text)
        return str(path)

    def _sample_contract(self):
        return {
            "routes": ["/demo"],
            "blueprints": ["demo_bp"],
            "models": ["DemoModel"],
            "commands": [],
            "requires_features": ["auth"],
            "env_vars": ["API_KEY"],
        }

    def test_writes_contract_section(self, tmp_path):
        path = self._base_pyproject(tmp_path)
        write_contract(path, self._sample_contract(), "splent_feature_demo")
        data = tomllib.loads(Path(path).read_text())
        assert data["tool"]["splent"]["contract"]["provides"]["routes"] == ["/demo"]

    def test_preserves_existing_description(self, tmp_path):
        path = self._base_pyproject(tmp_path, with_contract=True)
        write_contract(path, self._sample_contract(), "splent_feature_x")
        data = tomllib.loads(Path(path).read_text())
        assert data["tool"]["splent"]["contract"]["description"] == "existing description"

    def test_replaces_old_contract(self, tmp_path):
        path = self._base_pyproject(tmp_path, with_contract=True)
        write_contract(path, self._sample_contract(), "splent_feature_x")
        text = Path(path).read_text()
        assert "/old" not in text
        assert "/demo" in text

    def test_contract_is_valid_toml(self, tmp_path):
        path = self._base_pyproject(tmp_path)
        write_contract(path, self._sample_contract(), "splent_feature_demo")
        # Should not raise
        data = tomllib.loads(Path(path).read_text())
        assert "tool" in data

    def test_requires_fields_written(self, tmp_path):
        path = self._base_pyproject(tmp_path)
        write_contract(path, self._sample_contract(), "splent_feature_demo")
        data = tomllib.loads(Path(path).read_text())
        requires = data["tool"]["splent"]["contract"]["requires"]
        assert requires["features"] == ["auth"]
        assert requires["env_vars"] == ["API_KEY"]

    def test_empty_lists_written_as_empty(self, tmp_path):
        path = self._base_pyproject(tmp_path)
        contract = {
            "routes": [], "blueprints": [], "models": [], "commands": [],
            "requires_features": [], "env_vars": [],
        }
        write_contract(path, contract, "splent_feature_demo")
        data = tomllib.loads(Path(path).read_text())
        assert data["tool"]["splent"]["contract"]["provides"]["routes"] == []
