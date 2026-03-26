"""
Tests for the feature lifecycle state machine.
"""
import pytest
from pathlib import Path

from splent_cli.utils.lifecycle import (
    state_rank,
    require_state,
    advance_state,
    resolve_feature_key_from_entry,
)
from splent_cli.utils.manifest import (
    set_feature_state,
    get_feature_state,
    feature_key,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def product(tmp_path):
    """A minimal product directory with manifest support."""
    product_path = tmp_path / "test_app"
    product_path.mkdir()
    return product_path


def _set(product_path, state, name="splent_feature_auth", version="v1.2.2"):
    key = feature_key("splent_io", name, version)
    set_feature_state(
        str(product_path), "test_app", key, state,
        namespace="splent_io", name=name, version=version, mode="pinned",
    )
    return key


# ---------------------------------------------------------------------------
# Tests: state_rank
# ---------------------------------------------------------------------------

class TestStateRank:

    def test_declared_is_lowest(self):
        assert state_rank("declared") == 0

    def test_installed_gt_declared(self):
        assert state_rank("installed") > state_rank("declared")

    def test_migrated_gt_installed(self):
        assert state_rank("migrated") > state_rank("installed")

    def test_active_gt_migrated(self):
        assert state_rank("active") > state_rank("migrated")

    def test_disabled_equals_active(self):
        assert state_rank("disabled") == state_rank("active")

    def test_none_is_negative(self):
        assert state_rank(None) == -1

    def test_unknown_is_negative(self):
        assert state_rank("bogus") == -1


# ---------------------------------------------------------------------------
# Tests: require_state
# ---------------------------------------------------------------------------

class TestRequireState:

    def test_passes_when_no_state_tracked(self, product):
        """Untracked features should not block commands."""
        key = feature_key("splent_io", "splent_feature_auth", "v1.2.2")
        result = require_state(str(product), key, min_state="installed", command="db:migrate")
        assert result is None

    def test_passes_when_state_is_sufficient(self, product):
        key = _set(product, "installed")
        result = require_state(str(product), key, min_state="installed", command="db:migrate")
        assert result == "installed"

    def test_passes_when_state_exceeds_minimum(self, product):
        key = _set(product, "migrated")
        result = require_state(str(product), key, min_state="installed", command="db:migrate")
        assert result == "migrated"

    def test_blocks_when_state_is_insufficient(self, product):
        key = _set(product, "declared")
        with pytest.raises(SystemExit):
            require_state(str(product), key, min_state="installed", command="db:migrate")

    def test_blocks_when_state_is_in_blocked_set(self, product):
        key = _set(product, "migrated")
        with pytest.raises(SystemExit):
            require_state(str(product), key, command="feature:remove")

    def test_force_bypasses_blocked_state(self, product):
        key = _set(product, "migrated")
        result = require_state(str(product), key, command="feature:remove", force=True)
        assert result == "migrated"

    def test_force_bypasses_insufficient_state(self, product):
        key = _set(product, "declared")
        result = require_state(
            str(product), key, min_state="installed", command="db:migrate", force=True
        )
        assert result == "declared"


# ---------------------------------------------------------------------------
# Tests: advance_state
# ---------------------------------------------------------------------------

class TestAdvanceState:

    def test_advances_from_declared_to_installed(self, product):
        key = _set(product, "declared")
        advance_state(
            str(product), "test_app", key,
            to="installed", namespace="splent_io",
            name="splent_feature_auth", version="v1.2.2",
        )
        assert get_feature_state(str(product), key) == "installed"

    def test_advances_from_installed_to_migrated(self, product):
        key = _set(product, "installed")
        advance_state(
            str(product), "test_app", key,
            to="migrated", namespace="splent_io",
            name="splent_feature_auth", version="v1.2.2",
        )
        assert get_feature_state(str(product), key) == "migrated"

    def test_does_not_regress_state(self, product):
        key = _set(product, "migrated")
        advance_state(
            str(product), "test_app", key,
            to="installed", namespace="splent_io",
            name="splent_feature_auth", version="v1.2.2",
        )
        # Should still be "migrated" — advance_state doesn't regress
        assert get_feature_state(str(product), key) == "migrated"

    def test_allows_regression_to_declared(self, product):
        """Rollback to declared is allowed (explicit regression)."""
        key = _set(product, "installed")
        advance_state(
            str(product), "test_app", key,
            to="declared", namespace="splent_io",
            name="splent_feature_auth", version="v1.2.2",
        )
        assert get_feature_state(str(product), key) == "declared"

    def test_allows_regression_to_disabled(self, product):
        key = _set(product, "active")
        advance_state(
            str(product), "test_app", key,
            to="disabled", namespace="splent_io",
            name="splent_feature_auth", version="v1.2.2",
        )
        assert get_feature_state(str(product), key) == "disabled"

    def test_creates_entry_if_not_tracked(self, product):
        key = feature_key("splent_io", "splent_feature_new", "v1.0.0")
        advance_state(
            str(product), "test_app", key,
            to="installed", namespace="splent_io",
            name="splent_feature_new", version="v1.0.0",
        )
        assert get_feature_state(str(product), key) == "installed"


# ---------------------------------------------------------------------------
# Tests: resolve_feature_key_from_entry
# ---------------------------------------------------------------------------

class TestResolveFeatureKeyFromEntry:

    def test_full_entry(self):
        key, ns, name, ver = resolve_feature_key_from_entry(
            "splent-io/splent_feature_auth@v1.2.2"
        )
        assert key == "splent_io/splent_feature_auth@v1.2.2"
        assert ns == "splent_io"
        assert name == "splent_feature_auth"
        assert ver == "v1.2.2"

    def test_no_org(self):
        key, ns, name, ver = resolve_feature_key_from_entry(
            "splent_feature_auth@v1.2.2"
        )
        assert ns == "splent_io"
        assert name == "splent_feature_auth"

    def test_no_version(self):
        key, ns, name, ver = resolve_feature_key_from_entry(
            "splent-io/splent_feature_auth"
        )
        assert ver is None
        assert key == "splent_io/splent_feature_auth"

    def test_dot_org(self):
        key, ns, name, ver = resolve_feature_key_from_entry(
            "my.org/my_feature@v2.0.0"
        )
        assert ns == "my_org"
