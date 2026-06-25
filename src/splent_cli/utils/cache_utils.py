"""
Utilities for managing the .splent_cache directory.

Provides helpers to protect versioned (pinned) features from accidental
modification by setting filesystem permissions to read-only.
"""

import os
import shutil
import stat
import sys


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
    """Restore write permissions across a cached feature so it can be edited
    or deleted.

    Used before deleting a cached feature (e.g. --force reclone)
    or when forking a pinned feature into editable mode.

    Unlike :func:`make_feature_readonly`, this does NOT skip the ``.git``
    directory: git writes read-only pack files under ``.git/objects`` and a
    subsequent ``rmtree`` would otherwise die with ``PermissionError`` on
    them. Directories are made writable+traversable so their entries can be
    unlinked during deletion.
    """
    if not os.path.exists(path):
        return

    # Make the top-level dir writable too (rmtree must unlink entries in it).
    try:
        os.chmod(path, stat.S_IRWXU)
    except OSError:
        pass

    for root, dirs, files in os.walk(path):
        for name in dirs:
            dp = os.path.join(root, name)
            try:
                os.chmod(dp, stat.S_IRWXU)
            except OSError:
                pass

        for name in files:
            fp = os.path.join(root, name)
            try:
                os.chmod(
                    fp,
                    stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH,
                )
            except OSError:
                pass


def _force_writable_onerror(func, path, exc_info):
    """``shutil.rmtree`` error handler: clear the read-only bit and retry.

    Compatible with both the legacy ``onerror`` signature (Python < 3.12,
    ``exc_info`` is a ``(type, value, tb)`` tuple) and the ``onexc``
    signature (Python >= 3.12, ``exc_info`` is the exception instance).
    Without this, the first read-only file/dir aborts the whole deletion and
    leaves the cache folder in a partial state.
    """
    try:
        os.chmod(path, stat.S_IRWXU)
    except OSError:
        raise
    func(path)


def rmtree_force(path) -> None:
    """Delete a directory tree, surviving read-only files/dirs.

    Reusable replacement for ``shutil.rmtree`` on cache trees that may
    contain read-only entries (pinned features, git pack files). Makes the
    tree writable up front and installs a chmod-and-retry error handler so
    cleanup never dies with ``PermissionError`` leaving partial state.

    Missing paths are a no-op. Use this from any command that needs to remove
    a cached feature so cleanup logic is not re-implemented per call site.
    """
    if not os.path.exists(path) and not os.path.islink(path):
        return

    make_feature_writable(str(path))

    # Python 3.12 deprecated ``onerror`` in favour of ``onexc`` (different
    # final argument). Pick the right keyword so a single handler works on
    # both without triggering a DeprecationWarning.
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_force_writable_onerror)
    else:
        shutil.rmtree(path, onerror=_force_writable_onerror)
