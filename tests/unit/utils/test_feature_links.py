"""One feature, one link.

Two links for the same feature put two copies of the same package where the
product looks for it, and which one wins comes down to directory order.
Removing what the previous declaration created does not prevent that: a link
whose version is no longer declared anywhere, left by an interrupted command
or by hand, is invisible to that bookkeeping and outlives every later attach.
That is how a product pinned to v0.1.1 was found holding links to v0.1.0 and
v0.2.0 and nothing to v0.1.1.
"""

import os

import pytest

from splent_cli.utils.feature_utils import prune_feature_links


NAME = "splent_feature_marketplace"


@pytest.fixture
def links_dir(tmp_path):
    d = tmp_path / "features" / "splent_io"
    d.mkdir(parents=True)
    return d


def _link(links_dir, leaf, target=None):
    target = target or str(links_dir.parent / f"target_{leaf}")
    os.makedirs(target, exist_ok=True)
    os.symlink(target, links_dir / leaf)


def _leaves(links_dir):
    return sorted(os.listdir(links_dir))


def test_the_kept_version_survives_and_the_others_go(tmp_path, links_dir):
    for leaf in (f"{NAME}@v0.1.0", f"{NAME}@v0.1.1", f"{NAME}@v0.2.0"):
        _link(links_dir, leaf)

    removed = prune_feature_links(
        str(tmp_path), "splent_io", NAME, keep=f"{NAME}@v0.2.0"
    )

    assert _leaves(links_dir) == [f"{NAME}@v0.2.0"]
    assert removed == [f"{NAME}@v0.1.0", f"{NAME}@v0.1.1"]


def test_an_editable_link_is_dropped_when_pinning(tmp_path, links_dir):
    """Going from editable to pinned: the bare link is the old declaration."""
    _link(links_dir, NAME)
    _link(links_dir, f"{NAME}@v0.2.0")

    prune_feature_links(str(tmp_path), "splent_io", NAME, keep=f"{NAME}@v0.2.0")

    assert _leaves(links_dir) == [f"{NAME}@v0.2.0"]


def test_a_pinned_link_is_dropped_when_going_editable(tmp_path, links_dir):
    _link(links_dir, NAME)
    _link(links_dir, f"{NAME}@v0.2.0")

    prune_feature_links(str(tmp_path), "splent_io", NAME, keep=NAME)

    assert _leaves(links_dir) == [NAME]


def test_other_features_are_not_touched(tmp_path, links_dir):
    """Including one whose name starts with this one's."""
    _link(links_dir, f"{NAME}@v0.2.0")
    _link(links_dir, "splent_feature_auth@v1.7.0")
    _link(links_dir, f"{NAME}_web@v0.1.0")

    prune_feature_links(str(tmp_path), "splent_io", NAME, keep=f"{NAME}@v0.2.0")

    assert _leaves(links_dir) == [
        "splent_feature_auth@v1.7.0",
        f"{NAME}@v0.2.0",
        f"{NAME}_web@v0.1.0",
    ]


def test_a_real_directory_is_left_alone(tmp_path, links_dir):
    """Someone's checkout is not ours to delete, however it got there."""
    (links_dir / f"{NAME}@v0.1.0").mkdir()
    _link(links_dir, f"{NAME}@v0.2.0")

    removed = prune_feature_links(
        str(tmp_path), "splent_io", NAME, keep=f"{NAME}@v0.2.0"
    )

    assert removed == []
    assert (links_dir / f"{NAME}@v0.1.0").is_dir()


def test_the_namespace_spelling_does_not_matter(tmp_path, links_dir):
    """splent-io and splent_io are the same namespace."""
    _link(links_dir, f"{NAME}@v0.1.0")
    _link(links_dir, f"{NAME}@v0.2.0")

    prune_feature_links(str(tmp_path), "splent-io", NAME, keep=f"{NAME}@v0.2.0")

    assert _leaves(links_dir) == [f"{NAME}@v0.2.0"]


def test_a_product_without_that_namespace_yet_is_not_an_error(tmp_path):
    assert prune_feature_links(str(tmp_path), "splent_io", NAME, keep=NAME) == []
