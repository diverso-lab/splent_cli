"""
Unit tests for reading the published web port out of `docker ps`.

A product publishes more than one port (locust sits on 8089), and docker does
not list them app-first. Taking the first match reported 8089 as the site URL
while the app was actually on the mapping to container port 5000.
"""

import pytest

from splent_cli.commands.product.product_port import (
    APP_CONTAINER_PORT,
    get_runtime_ports,
    parse_port_mappings,
)

REAL_LINE = (
    "0.0.0.0:8089->8089/tcp, [::]:8089->8089/tcp, "
    "0.0.0.0:5818->5000/tcp, [::]:5818->5000/tcp"
)


def test_ipv4_and_ipv6_duplicates_collapse():
    assert parse_port_mappings(REAL_LINE) == [("8089", "8089"), ("5818", "5000")]


def test_no_published_ports_parses_to_nothing():
    assert parse_port_mappings("") == []


def _docker_ps(monkeypatch, stdout):
    import subprocess as sp
    from splent_cli.commands.product import product_port

    class _Result:
        returncode = 0
        stderr = ""

        def __init__(self, out):
            self.stdout = out

    monkeypatch.setattr(product_port.subprocess, "run", lambda *a, **k: _Result(stdout))
    assert sp  # keep the import meaningful for readers


def test_app_port_wins_over_a_port_listed_first(monkeypatch):
    """The regression: locust is listed before the app."""
    _docker_ps(monkeypatch, f"my_app_web {REAL_LINE}\n")
    assert get_runtime_ports("my_app_web") == ("5818", APP_CONTAINER_PORT)


def test_single_mapping_is_used_even_if_it_is_not_the_app_port(monkeypatch):
    _docker_ps(monkeypatch, "my_app_web 0.0.0.0:9000->9000/tcp\n")
    assert get_runtime_ports("my_app_web") == ("9000", "9000")


def test_several_mappings_without_the_app_port_is_an_error(monkeypatch):
    import click

    _docker_ps(monkeypatch, "my_app_web 0.0.0.0:8089->8089/tcp, 0.0.0.0:9000->9001/tcp\n")
    with pytest.raises(click.ClickException) as exc:
        get_runtime_ports("my_app_web")
    assert "8089->8089" in str(exc.value)


def test_container_not_running(monkeypatch):
    import click

    _docker_ps(monkeypatch, "other_container 0.0.0.0:1->2/tcp\n")
    with pytest.raises(click.ClickException):
        get_runtime_ports("my_app_web")
