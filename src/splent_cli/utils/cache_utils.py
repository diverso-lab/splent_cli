"""
Utilities for managing the .splent_cache directory.

Provides helpers to protect versioned (pinned) features from accidental
modification by setting filesystem permissions to read-only.
"""

import os
import stat


def make_feature_readonly(path: str) -> None:
    """Remove write permissions from all files in a cached feature.

    Skips the .git directory so that git internals remain functional.
    Directories keep their execute bit so they remain traversable.
    """
    for root, dirs, files in os.walk(path):
        # Skip .git internals
        if ".git" in root.split(os.sep):
            continue
        # Remove .git from dirs to avoid descending into it
        if ".git" in dirs:
            dirs.remove(".git")

        for name in files:
            fp = os.path.join(root, name)
            try:
                os.chmod(fp, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            except OSError:
                pass


def make_feature_writable(path: str) -> None:
    """Restore write permissions on all files in a cached feature.

    Used before deleting a cached feature (e.g. --force reclone)
    or when forking a pinned feature into editable mode.
    """
    for root, dirs, files in os.walk(path):
        if ".git" in root.split(os.sep):
            continue
        if ".git" in dirs:
            dirs.remove(".git")

        for name in files:
            fp = os.path.join(root, name)
            try:
                os.chmod(
                    fp,
                    stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH,
                )
            except OSError:
                pass
