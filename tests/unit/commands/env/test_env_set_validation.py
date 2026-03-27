"""Tests for env:set — value validation."""
import pytest
from splent_cli.commands.env.env_set import _validate_env_value
import click


class TestEnvValueValidation:
    def test_accepts_normal_token(self):
        val = _validate_env_value("ghp_abc123XYZ", "token")
        assert val == "ghp_abc123XYZ"

    def test_accepts_token_with_equals(self):
        # PyPI tokens contain '=' padding — must be accepted
        val = _validate_env_value("pypi-abc==", "token")
        assert val == "pypi-abc=="

    def test_strips_surrounding_whitespace(self):
        val = _validate_env_value("  mytoken  ", "token")
        assert val == "mytoken"

    def test_rejects_token_with_newline(self):
        with pytest.raises(click.ClickException, match="newline"):
            _validate_env_value("token\ninjected=BAD", "token")

    def test_rejects_token_with_carriage_return(self):
        with pytest.raises(click.ClickException):
            _validate_env_value("token\rinjected", "token")

    def test_rejects_multiline_value(self):
        with pytest.raises(click.ClickException):
            _validate_env_value("line1\nline2\nline3", "token")
