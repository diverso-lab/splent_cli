"""Tests for release.bump — version format validation."""
import pytest
import click
from splent_cli.services.release import bump


class TestBump:
    def test_patch_bump(self):
        assert bump("v1.2.3", "patch") == "v1.2.4"

    def test_minor_bump(self):
        assert bump("v1.2.3", "minor") == "v1.3.0"

    def test_major_bump(self):
        assert bump("v1.2.3", "major") == "v2.0.0"

    def test_no_v_prefix(self):
        assert bump("1.2.3", "patch") == "v1.2.4"

    def test_rejects_missing_parts(self):
        with pytest.raises(click.ClickException, match="parse version"):
            bump("v1.2", "patch")

    def test_rejects_non_numeric_parts(self):
        with pytest.raises(click.ClickException, match="parse version"):
            bump("v1.2.alpha", "patch")

    def test_rejects_empty_string(self):
        with pytest.raises(click.ClickException):
            bump("", "patch")

    def test_rejects_too_many_parts(self):
        with pytest.raises(click.ClickException):
            bump("v1.2.3.4", "patch")
