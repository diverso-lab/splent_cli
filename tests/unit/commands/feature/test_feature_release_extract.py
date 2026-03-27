"""Tests for regex-based extraction helpers in feature_release.py."""
import pytest
from pathlib import Path

from splent_cli.commands.feature.feature_release import (
    _extract_blueprints,
    _extract_hooks,
    _extract_models,
    _extract_routes,
    _extract_services,
    _scan_dependencies,
    parse_feature_ref,
)


# ---------------------------------------------------------------------------
# parse_feature_ref
# ---------------------------------------------------------------------------

class TestParseFeatureRef:
    def test_full_format(self):
        ns, name, ver = parse_feature_ref("splent-io/auth@v1.2.3")
        assert ns == "splent-io"
        assert name == "auth"
        assert ver == "v1.2.3"

    def test_no_version(self):
        ns, name, ver = parse_feature_ref("splent-io/auth")
        assert ns == "splent-io"
        assert name == "auth"
        assert ver is None

    def test_no_namespace_uses_default(self):
        ns, name, ver = parse_feature_ref("auth@v1.0.0", default_ns="myorg")
        assert ns == "myorg"
        assert name == "auth"
        assert ver == "v1.0.0"

    def test_name_only(self):
        ns, name, ver = parse_feature_ref("auth", default_ns="default_org")
        assert name == "auth"
        assert ver is None
        assert ns == "default_org"

    def test_preserves_namespace_dashes(self):
        ns, name, _ = parse_feature_ref("my-org/myfeature")
        assert ns == "my-org"


# ---------------------------------------------------------------------------
# _extract_routes
# ---------------------------------------------------------------------------

class TestExtractRoutes:
    def test_finds_routes(self, tmp_path):
        f = tmp_path / "routes.py"
        f.write_text(
            '@bp.route("/login")\ndef login(): pass\n'
            '@bp.route("/logout")\ndef logout(): pass\n'
        )
        result = _extract_routes(f)
        assert "/login" in result
        assert "/logout" in result

    def test_returns_empty_when_file_missing(self, tmp_path):
        assert _extract_routes(tmp_path / "missing.py") == []

    def test_deduplicates_routes(self, tmp_path):
        f = tmp_path / "routes.py"
        f.write_text('@bp.route("/login")\n@bp.route("/login")\n')
        result = _extract_routes(f)
        assert result.count("/login") == 1

    def test_result_is_sorted(self, tmp_path):
        f = tmp_path / "routes.py"
        f.write_text(
            '@bp.route("/z")\n'
            '@bp.route("/a")\n'
        )
        result = _extract_routes(f)
        assert result == sorted(result)

    def test_finds_single_quoted_routes(self, tmp_path):
        f = tmp_path / "routes.py"
        f.write_text("@bp.route('/profile')\ndef profile(): pass\n")
        result = _extract_routes(f)
        assert "/profile" in result


# ---------------------------------------------------------------------------
# _extract_blueprints
# ---------------------------------------------------------------------------

class TestExtractBlueprints:
    def test_finds_base_blueprint(self, tmp_path):
        f = tmp_path / "__init__.py"
        f.write_text('auth_bp = BaseBlueprint("auth", __name__)\n')
        result = _extract_blueprints(f)
        assert "auth_bp" in result

    def test_finds_blueprint(self, tmp_path):
        f = tmp_path / "__init__.py"
        f.write_text('bp = Blueprint("auth", __name__)\n')
        result = _extract_blueprints(f)
        assert "bp" in result

    def test_returns_empty_when_file_missing(self, tmp_path):
        assert _extract_blueprints(tmp_path / "missing.py") == []

    def test_deduplicates(self, tmp_path):
        f = tmp_path / "__init__.py"
        f.write_text(
            'bp = BaseBlueprint("auth", __name__)\n'
            'bp = BaseBlueprint("auth2", __name__)\n'
        )
        result = _extract_blueprints(f)
        assert result.count("bp") == 1


# ---------------------------------------------------------------------------
# _extract_models
# ---------------------------------------------------------------------------

class TestExtractModels:
    def test_finds_sqlalchemy_model_classes(self, tmp_path):
        f = tmp_path / "models.py"
        f.write_text(
            "class User(db.Model):\n    pass\n"
            "class Post(db.Model):\n    pass\n"
            "class Helper:\n    pass\n"
        )
        result = _extract_models(f)
        assert "User" in result
        assert "Post" in result
        assert "Helper" not in result

    def test_returns_empty_when_file_missing(self, tmp_path):
        assert _extract_models(tmp_path / "missing.py") == []

    def test_deduplicates_class_names(self, tmp_path):
        f = tmp_path / "models.py"
        f.write_text(
            "class User(db.Model):\n    pass\n"
            "class User(db.Model):\n    pass\n"
        )
        result = _extract_models(f)
        assert result.count("User") == 1


# ---------------------------------------------------------------------------
# _extract_hooks
# ---------------------------------------------------------------------------

class TestExtractHooks:
    def test_finds_template_hooks(self, tmp_path):
        f = tmp_path / "hooks.py"
        f.write_text(
            'register_template_hook("sidebar", render_sidebar)\n'
            'register_template_hook("navbar", render_navbar)\n'
        )
        result = _extract_hooks(f)
        assert "sidebar" in result
        assert "navbar" in result

    def test_returns_empty_when_file_missing(self, tmp_path):
        assert _extract_hooks(tmp_path / "missing.py") == []

    def test_deduplicates_hooks(self, tmp_path):
        f = tmp_path / "hooks.py"
        f.write_text(
            'register_template_hook("sidebar", fn1)\n'
            'register_template_hook("sidebar", fn2)\n'
        )
        result = _extract_hooks(f)
        assert result.count("sidebar") == 1


# ---------------------------------------------------------------------------
# _extract_services
# ---------------------------------------------------------------------------

class TestExtractServices:
    def test_finds_base_service_classes(self, tmp_path):
        f = tmp_path / "services.py"
        f.write_text(
            "class AuthService(BaseService):\n    pass\n"
            "class NotAService:\n    pass\n"
        )
        result = _extract_services(f)
        assert "AuthService" in result
        assert "NotAService" not in result

    def test_finds_service_suffix_classes(self, tmp_path):
        f = tmp_path / "services.py"
        f.write_text("class UserService(Service):\n    pass\n")
        result = _extract_services(f)
        assert "UserService" in result

    def test_returns_empty_when_file_missing(self, tmp_path):
        assert _extract_services(tmp_path / "missing.py") == []


# ---------------------------------------------------------------------------
# _scan_dependencies
# ---------------------------------------------------------------------------

class TestScanDependencies:
    def test_finds_feature_imports(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "views.py").write_text(
            "from splent_io.splent_feature_profile import something\n"
        )
        features, _ = _scan_dependencies(src, "splent_feature_auth")
        assert "profile" in features

    def test_excludes_own_feature_name(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "views.py").write_text(
            "from splent_io.splent_feature_auth import something\n"
        )
        features, _ = _scan_dependencies(src, "splent_feature_auth")
        assert "auth" not in features

    def test_finds_env_vars_getenv(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "config.py").write_text(
            'secret = os.getenv("MY_SECRET_KEY")\n'
        )
        _, env_vars = _scan_dependencies(src, "splent_feature_auth")
        assert "MY_SECRET_KEY" in env_vars

    def test_finds_env_vars_environ_get(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "config.py").write_text(
            'val = os.environ.get("DB_URL")\n'
        )
        _, env_vars = _scan_dependencies(src, "splent_feature_auth")
        assert "DB_URL" in env_vars

    def test_finds_env_vars_environ_bracket(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "config.py").write_text(
            'val = os.environ["API_KEY"]\n'
        )
        _, env_vars = _scan_dependencies(src, "splent_feature_auth")
        assert "API_KEY" in env_vars

    def test_scans_subdirectories(self, tmp_path):
        src = tmp_path / "src"
        sub = src / "subpkg"
        sub.mkdir(parents=True)
        (sub / "deep.py").write_text(
            "from splent_io.splent_feature_notes import something\n"
        )
        features, _ = _scan_dependencies(src, "splent_feature_auth")
        assert "notes" in features

    def test_empty_src_dir(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        features, env_vars = _scan_dependencies(src, "splent_feature_auth")
        assert features == []
        assert env_vars == []

    def test_results_are_sorted(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "views.py").write_text(
            "from splent_io.splent_feature_z import a\n"
            "from splent_io.splent_feature_a import b\n"
            'os.getenv("Z_VAR")\nos.getenv("A_VAR")\n'
        )
        features, env_vars = _scan_dependencies(src, "splent_feature_other")
        assert features == sorted(features)
        assert env_vars == sorted(env_vars)
