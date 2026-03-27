"""Tests for manifest.get_dependents — exception handling specificity."""
from unittest.mock import patch
from pathlib import Path
from splent_cli.utils.manifest import get_dependents, set_feature_state

KEY = "splent_io/splent_feature_auth@v1.0.0"
NS = "splent_io"
NAME = "splent_feature_auth"
VER = "v1.0.0"


def _make_product(tmp_path):
    product_path = str(tmp_path / "myapp")
    Path(product_path).mkdir()
    return product_path


def _add_auth_feature(product_path):
    set_feature_state(
        product_path, "myapp", KEY,
        "declared", namespace=NS, name=NAME, version=VER,
    )


def _make_features_dir(product_path):
    d = Path(product_path) / "features" / NS / NAME
    d.mkdir(parents=True)
    return d


class TestGetDependentsErrorHandling:
    def test_skips_unreadable_pyproject(self, tmp_path):
        """OSError on a feature pyproject.toml is silently skipped."""
        product_path = _make_product(tmp_path)
        _add_auth_feature(product_path)
        d = _make_features_dir(product_path)
        (d / "pyproject.toml").write_text("[project]\nname = 'auth'\n")

        target = "splent_cli.utils.manifest.tomllib.load"
        with patch(target, side_effect=OSError("perm denied")):
            result = get_dependents(product_path, "some_feature")

        assert result == []

    def test_skips_malformed_toml(self, tmp_path):
        """TOMLDecodeError on a feature pyproject is silently skipped."""
        product_path = _make_product(tmp_path)
        _add_auth_feature(product_path)
        d = _make_features_dir(product_path)
        (d / "pyproject.toml").write_bytes(b"not valid toml ][[[")

        result = get_dependents(product_path, "some_feature")
        assert result == []

    def test_returns_dependents_when_valid(self, tmp_path):
        """Normal case: returns features that depend on the given feature."""
        product_path = _make_product(tmp_path)
        profile_key = "splent_io/splent_feature_profile@v1.0.0"
        set_feature_state(
            product_path, "myapp", profile_key,
            "declared", namespace=NS,
            name="splent_feature_profile", version=VER,
        )
        d = (
            Path(product_path)
            / "features" / NS / "splent_feature_profile"
        )
        d.mkdir(parents=True)
        (d / "pyproject.toml").write_text(
            "[tool.splent.contract.requires]\n"
            'features = ["splent_feature_auth"]\n'
        )

        result = get_dependents(product_path, "splent_feature_auth")
        assert "splent_feature_profile" in result
