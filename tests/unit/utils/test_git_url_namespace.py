"""
A pinned feature entry carries the Python namespace (splent_io) while the
repository lives under the hosting org (splent-io). Cloning has to reach the
repo either way, or pinning a product makes it unresolvable on any machine
whose cache is empty.
"""

from splent_cli.utils.git_url import candidate_urls, namespace_spellings


def test_underscore_namespace_also_tries_the_hyphen_spelling():
    assert namespace_spellings("splent_io") == ["splent_io", "splent-io"]


def test_hyphen_namespace_is_left_alone():
    assert namespace_spellings("splent-io") == ["splent-io"]


def test_namespace_without_separators_yields_one_spelling():
    assert namespace_spellings("acme") == ["acme"]


def test_candidates_cover_both_spellings_over_both_transports():
    urls = [
        display for _, display, _ in candidate_urls("splent_io", "splent_feature_theme")
    ]
    assert "git@github.com:splent_io/splent_feature_theme.git" in urls
    assert "git@github.com:splent-io/splent_feature_theme.git" in urls
    assert "https://github.com/splent-io/splent_feature_theme.git" in urls


def test_the_spelling_as_written_is_tried_first():
    urls = [
        display for _, display, _ in candidate_urls("splent_io", "splent_feature_theme")
    ]
    assert urls[0] == "git@github.com:splent_io/splent_feature_theme.git"
    assert urls.index("git@github.com:splent_io/splent_feature_theme.git") < urls.index(
        "git@github.com:splent-io/splent_feature_theme.git"
    )


def test_a_hyphen_namespace_produces_no_extra_candidates():
    assert len(candidate_urls("splent-io", "splent_feature_theme")) == 2
