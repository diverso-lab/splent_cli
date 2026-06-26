"""Tests for the get_app() boot guard in product:config and product:routes.

Hardened behavior under test:

* Both ``product:config`` and ``product:routes`` boot the Flask app via
  ``splent_cli.utils.dynamic_imports.get_app``. If that call raises (e.g. a
  feature fails to import), the command must surface a concise
  ``click.ClickException`` ("could not boot app: ...") on STDERR with a
  non-zero exit code -- never a long framework traceback bubbling out.

Plus a couple of core happy-path tests using a fake booted app so no real
docker / git / network / database is required.
"""

import pytest
from unittest.mock import MagicMock
from click.testing import CliRunner

from splent_cli.commands.product.product_config import product_config
from splent_cli.commands.product.product_routes import product_routes


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


@pytest.fixture(autouse=True)
def _product_env(tmp_path, monkeypatch):
    """Give both commands a valid product context (passes requires_product)."""
    monkeypatch.setenv("WORKING_DIR", str(tmp_path))
    monkeypatch.setenv("SPLENT_APP", "test_app")
    monkeypatch.delenv("SPLENT_ENV", raising=False)


def _make_fake_app():
    """A minimal stand-in for a booted Flask app.

    Provides just the attributes the commands touch:
    ``extensions``, ``config`` (with .keys()), and ``url_map.iter_rules()``.
    """
    app = MagicMock()
    app.extensions = {}
    app.config = {}
    app.url_map.iter_rules.return_value = []
    return app


# ---------------------------------------------------------------------------
# Hardened: get_app() failure -> clean ClickException, no traceback
# ---------------------------------------------------------------------------


class TestProductConfigBootGuard:
    def test_get_app_error_surfaced_as_clickexception(self, runner, monkeypatch):
        def boom():
            raise ImportError("No module named 'splent_feature_broken'")

        monkeypatch.setattr(
            "splent_cli.utils.dynamic_imports.get_app", boom, raising=False
        )

        result = runner.invoke(product_config, [], catch_exceptions=False)

        assert result.exit_code != 0
        # Concise, actionable message on stderr.
        assert "could not boot app" in result.stderr
        # No raw framework traceback / exception class leakage.
        assert "Traceback" not in result.stderr
        assert "ImportError" not in result.stderr

    def test_get_app_error_includes_cause_detail(self, runner, monkeypatch):
        def boom():
            raise RuntimeError("redis feature blew up")

        monkeypatch.setattr(
            "splent_cli.utils.dynamic_imports.get_app", boom, raising=False
        )

        result = runner.invoke(product_config, [], catch_exceptions=False)

        assert result.exit_code != 0
        assert "could not boot app" in result.stderr
        # The underlying cause is still echoed so the user can debug.
        assert "redis feature blew up" in result.stderr
        assert "Traceback" not in result.stderr


class TestProductRoutesBootGuard:
    def test_get_app_error_surfaced_as_clickexception(self, runner, monkeypatch):
        def boom():
            raise ImportError("No module named 'splent_feature_broken'")

        monkeypatch.setattr(
            "splent_cli.utils.dynamic_imports.get_app", boom, raising=False
        )

        result = runner.invoke(product_routes, [], catch_exceptions=False)

        assert result.exit_code != 0
        assert "could not boot app" in result.stderr
        assert "Traceback" not in result.stderr
        assert "ImportError" not in result.stderr

    def test_get_app_error_includes_cause_detail(self, runner, monkeypatch):
        def boom():
            raise RuntimeError("auth feature blew up")

        monkeypatch.setattr(
            "splent_cli.utils.dynamic_imports.get_app", boom, raising=False
        )

        result = runner.invoke(product_routes, [], catch_exceptions=False)

        assert result.exit_code != 0
        assert "could not boot app" in result.stderr
        assert "auth feature blew up" in result.stderr
        assert "Traceback" not in result.stderr


# ---------------------------------------------------------------------------
# Guard runs before context: requires_product still enforced
# ---------------------------------------------------------------------------


class TestRequiresProduct:
    def test_config_aborts_when_no_product_selected(self, runner, monkeypatch):
        monkeypatch.delenv("SPLENT_APP", raising=False)
        result = runner.invoke(product_config, [])
        assert result.exit_code != 0
        assert "No product selected" in result.output
        assert "Traceback" not in result.output

    def test_routes_aborts_when_no_product_selected(self, runner, monkeypatch):
        monkeypatch.delenv("SPLENT_APP", raising=False)
        result = runner.invoke(product_routes, [])
        assert result.exit_code != 0
        assert "No product selected" in result.output
        assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# Happy paths with a fake booted app (no real boot)
# ---------------------------------------------------------------------------


class TestProductConfigHappyPath:
    def test_empty_config_no_keys_matched(self, runner, monkeypatch):
        app = _make_fake_app()  # no config keys
        monkeypatch.setattr(
            "splent_cli.utils.dynamic_imports.get_app",
            lambda: app,
            raising=False,
        )

        result = runner.invoke(product_config, [], catch_exceptions=False)

        assert result.exit_code == 0
        assert "No config keys matched" in result.output
        assert "Traceback" not in result.output

    def test_renders_config_rows_with_source(self, runner, monkeypatch):
        app = _make_fake_app()
        app.config = {"DATABASE_URL": "sqlite://", "lowercase_ignored": "x"}
        app.extensions = {
            "splent_config_trace": {
                "DATABASE_URL": {
                    "source": "splent_io.splent_feature_db",
                    "action": "set",
                }
            }
        }
        monkeypatch.setattr(
            "splent_cli.utils.dynamic_imports.get_app",
            lambda: app,
            raising=False,
        )

        result = runner.invoke(product_config, [], catch_exceptions=False)

        assert result.exit_code == 0
        assert "DATABASE_URL" in result.output
        # Source attribution prettified to "feature (splent_feature_db)".
        assert "feature (splent_feature_db)" in result.output
        # Lowercase keys are skipped.
        assert "lowercase_ignored" not in result.output
        assert "Traceback" not in result.output


class TestProductRoutesHappyPath:
    def test_no_routes_is_clean(self, runner, monkeypatch):
        app = _make_fake_app()  # iter_rules -> []
        monkeypatch.setattr(
            "splent_cli.utils.dynamic_imports.get_app",
            lambda: app,
            raising=False,
        )

        result = runner.invoke(product_routes, [], catch_exceptions=False)

        assert result.exit_code == 0
        assert "No routes matched" in result.output
        assert "Traceback" not in result.output

    def test_renders_routes_with_feature_attribution(self, runner, monkeypatch):
        app = _make_fake_app()
        rule = MagicMock()
        rule.rule = "/auth/login"
        rule.methods = {"GET", "POST", "HEAD", "OPTIONS"}
        rule.endpoint = "splent_feature_auth.login"
        app.url_map.iter_rules.return_value = [rule]
        app.extensions = {
            "splent_blueprint_trace": {
                "splent_feature_auth": "splent_io.splent_feature_auth"
            }
        }
        monkeypatch.setattr(
            "splent_cli.utils.dynamic_imports.get_app",
            lambda: app,
            raising=False,
        )

        result = runner.invoke(product_routes, [], catch_exceptions=False)

        assert result.exit_code == 0
        assert "/auth/login" in result.output
        assert "splent_feature_auth" in result.output
        assert "GET" in result.output
        assert "Traceback" not in result.output
