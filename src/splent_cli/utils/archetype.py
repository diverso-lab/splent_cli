"""Feature archetype detection (full / light / service / config).

The archetype is DERIVED from source code, never declared by hand: a
feature with real models is ``full``, with routes but no models ``light``,
with implemented services only ``service``, otherwise ``config``.

Shared by ``feature:clean`` and the contract generator so the marketplace
index can rely on a single definition of "what kind of feature is this".
"""

import os
import re

ARCHETYPES = ("full", "light", "config", "service")

ARCHETYPE_LABELS = {
    "full": "Complete domain feature (models, routes, services, templates, migrations)",
    "light": "Lightweight UI feature (routes, hooks, templates)",
    "config": "Infrastructure feature (config.py only)",
    "service": "Backend service (services, config, commands, signals)",
}


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def detect_archetype(src_dir: str) -> str:
    """Detect the real archetype from source file contents."""
    models = _read(os.path.join(src_dir, "models.py"))
    routes = _read(os.path.join(src_dir, "routes.py"))
    services = _read(os.path.join(src_dir, "services.py"))

    has_models = models and re.search(r"db\.Column", models)
    has_routes = routes and re.search(r"@\w+\.route\s*\(", routes)
    has_services = services and re.search(
        r"def\s+(?!__)\w+\s*\(.*\):\s*\n\s+(?!pass\b|\.\.\.|\s*#)",
        services,
        re.MULTILINE,
    )

    if has_models:
        return "full"
    if has_routes:
        return "light"
    if has_services:
        return "service"
    return "config"
