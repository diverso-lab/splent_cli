"""
Regression tests for the hardened guards in uvl_utils.py.

Focus: the safety behaviors added during hardening —
  * _require_flamapy() raises a friendly ClickException (with install hint)
    instead of a raw ImportError/ModuleNotFoundError when the optional
    `uvl` extra is not installed.
  * list_all_features_from_uvl() turns an opaque flamapy parse failure on a
    malformed/empty UVL into a ClickException that NAMES the offending file,
    rather than letting a raw flamapy traceback escape.

These tests never touch real flamapy / disk parsing — the flamapy import and
the transformation are mocked at the boundary.
"""
import builtins

import click
import pytest

from splent_cli.commands.uvl import uvl_utils
from splent_cli.commands.uvl.uvl_utils import (
    _require_flamapy,
    get_root_feature,
    iter_children,
    list_all_features_from_uvl,
)


# ---------------------------------------------------------------------------
# _require_flamapy — friendly message when the optional extra is missing
# ---------------------------------------------------------------------------

def _block_flamapy_import(monkeypatch):
    """Make `import flamapy` raise ModuleNotFoundError, others normal."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "flamapy" or name.startswith("flamapy."):
            raise ModuleNotFoundError("No module named 'flamapy'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


class TestRequireFlamapyMissing:
    def test_raises_clickexception_not_importerror(self, monkeypatch):
        _block_flamapy_import(monkeypatch)
        with pytest.raises(click.ClickException) as exc:
            _require_flamapy()
        # ModuleNotFoundError must be translated, not leaked.
        assert not isinstance(exc.value, ImportError)

    def test_message_mentions_flamapy_and_install_hint(self, monkeypatch):
        _block_flamapy_import(monkeypatch)
        with pytest.raises(click.ClickException) as exc:
            _require_flamapy()
        msg = str(exc.value)
        assert "flamapy" in msg
        # Friendly, actionable install instruction (the [uvl] extra).
        assert "pip install" in msg
        assert "uvl" in msg

    def test_present_flamapy_does_not_raise(self):
        # flamapy is installed in the test image: guard must be a no-op.
        _require_flamapy()


# ---------------------------------------------------------------------------
# list_all_features_from_uvl — malformed UVL names the file, no opaque traceback
# ---------------------------------------------------------------------------

class _FakeDM:
    """Stand-in for DiscoverMetamodels whose transformation blows up."""

    def __init__(self, exc):
        self._exc = exc

    def use_transformation_t2m(self, path, fmt):
        raise self._exc


class TestMalformedUvlIsNamed:
    def test_parse_failure_raises_clickexception_naming_file(
        self, monkeypatch
    ):
        boom = ValueError("Unexpected token at line 1")
        monkeypatch.setattr(
            uvl_utils, "_discover_metamodels", lambda: _FakeDM(boom)
        )
        with pytest.raises(click.ClickException) as exc:
            list_all_features_from_uvl("/path/to/broken_model.uvl")
        msg = str(exc.value)
        # The offending file must be identified in the message.
        assert "broken_model.uvl" in msg

    def test_parse_failure_is_not_raw_exception(self, monkeypatch):
        boom = ValueError("Unexpected token at line 1")
        monkeypatch.setattr(
            uvl_utils, "_discover_metamodels", lambda: _FakeDM(boom)
        )
        with pytest.raises(click.ClickException) as exc:
            list_all_features_from_uvl("/path/to/broken_model.uvl")
        # Not the raw flamapy/parser exception type.
        assert not isinstance(exc.value, ValueError)

    def test_empty_uvl_parse_failure_named(self, monkeypatch):
        # An empty UVL typically makes the transformation choke.
        boom = Exception("empty model")
        monkeypatch.setattr(
            uvl_utils, "_discover_metamodels", lambda: _FakeDM(boom)
        )
        with pytest.raises(click.ClickException) as exc:
            list_all_features_from_uvl("/tmp/empty.uvl")
        assert "empty.uvl" in str(exc.value)

    def test_clickexception_from_transform_is_passed_through(
        self, monkeypatch
    ):
        # If the transformation already raised a ClickException (e.g. a nested
        # guard), it must propagate unchanged — not be re-wrapped/double-named.
        original = click.ClickException("nested guard message")
        monkeypatch.setattr(
            uvl_utils, "_discover_metamodels", lambda: _FakeDM(original)
        )
        with pytest.raises(click.ClickException) as exc:
            list_all_features_from_uvl("/path/to/model.uvl")
        assert exc.value is original
        assert str(exc.value) == "nested guard message"


# ---------------------------------------------------------------------------
# Happy path: parse a (mocked) feature model into a sorted name list + root
# ---------------------------------------------------------------------------

class _FakeFeature:
    def __init__(self, name, children=None):
        self.name = name
        self.children = children or []


class _FakeFM:
    def __init__(self, root):
        self.root = root


class _HappyDM:
    def __init__(self, fm):
        self._fm = fm

    def use_transformation_t2m(self, path, fmt):
        assert fmt == "fm"
        return self._fm


class TestListAllFeaturesHappyPath:
    def _model(self):
        leaf_a = _FakeFeature("auth")
        leaf_b = _FakeFeature("profile")
        root = _FakeFeature("Root", children=[leaf_b, leaf_a])
        return _FakeFM(root)

    def test_returns_sorted_names_and_root(self, monkeypatch):
        monkeypatch.setattr(
            uvl_utils,
            "_discover_metamodels",
            lambda: _HappyDM(self._model()),
        )
        names, root_name = list_all_features_from_uvl("/x/model.uvl")
        assert root_name == "Root"
        assert names == sorted(names)
        assert set(names) == {"Root", "auth", "profile"}

    def test_missing_root_name_raises_clean(self, monkeypatch):
        bad_root = _FakeFeature("")  # empty/missing name
        monkeypatch.setattr(
            uvl_utils,
            "_discover_metamodels",
            lambda: _HappyDM(_FakeFM(bad_root)),
        )
        with pytest.raises(click.ClickException, match="root feature name"):
            list_all_features_from_uvl("/x/model.uvl")


# ---------------------------------------------------------------------------
# Tree traversal helpers used by list_all_features_from_uvl
# ---------------------------------------------------------------------------

class TestIterChildren:
    def test_children_attribute(self):
        node = _FakeFeature("p", children=[_FakeFeature("c")])
        kids = iter_children(node)
        assert [k.name for k in kids] == ["c"]

    def test_none_children_returns_empty(self):
        class N:
            children = None
        assert iter_children(N()) == []

    def test_get_children_method_fallback(self):
        class N:
            def get_children(self):
                return [_FakeFeature("z")]
        kids = iter_children(N())
        assert [k.name for k in kids] == ["z"]

    def test_no_children_at_all(self):
        class N:
            pass
        assert iter_children(N()) == []


class TestGetRootFeature:
    def test_root_attribute(self):
        root = _FakeFeature("R")
        assert get_root_feature(_FakeFM(root)) is root

    def test_root_callable(self):
        root = _FakeFeature("R")

        class FM:
            def root(self):
                return root
        assert get_root_feature(FM()) is root

    def test_get_root_method(self):
        root = _FakeFeature("R")

        class FM:
            def get_root(self):
                return root
        assert get_root_feature(FM()) is root

    def test_no_root_raises_clean(self):
        class FM:
            pass
        with pytest.raises(click.ClickException, match="root feature"):
            get_root_feature(FM())
