"""
product:console — Interactive Python REPL with the Flask app loaded.

Drops into an IPython or standard Python shell with the app context,
database session, and all feature models auto-imported.
"""

import code
import os

import click

from splent_cli.services import context


def _collect_models(app):
    """Find all SQLAlchemy model classes registered via db.Model."""
    from splent_framework.db import db

    models = {}
    for cls in db.Model.__subclasses__():
        models[cls.__name__] = cls
    return models


def _collect_services(app):
    """Find services registered in app.extensions['splent_services']."""
    services = {}
    registry = app.extensions.get("splent_services", {})
    for key, svc in registry.items():
        # Use class name as the console variable
        name = type(svc).__name__
        services[name] = svc
    return services


@click.command(
    "product:console",
    short_help="Interactive Python shell with the Flask app loaded.",
)
@context.requires_product
def product_console():
    """Open an interactive Python shell with the Flask app context.

    \b
    Auto-imports:
      app     — the Flask application
      db      — SQLAlchemy instance
      Models  — all db.Model subclasses (User, Note, UserProfile, ...)

    \b
    Examples:
        >>> app.config['SESSION_TYPE']
        'redis'
        >>> User.query.count()
        42
        >>> db.session.query(Note).filter_by(user_id=1).all()
    """
    product = context.require_app()

    os.environ.setdefault("SPLENT_ENV", "dev")
    from splent_cli.utils.dynamic_imports import get_app
    from splent_framework.db import db

    app = get_app()

    with app.app_context():
        # Build namespace
        namespace = {
            "app": app,
            "db": db,
        }

        # Auto-import models
        models = _collect_models(app)
        namespace.update(models)

        # Auto-import services
        services = _collect_services(app)
        namespace.update(services)

        # Banner
        model_names = ", ".join(sorted(models.keys())) if models else "(none)"
        service_names = ", ".join(sorted(services.keys())) if services else "(none)"

        banner = (
            f"\n\033[1m🐍 SPLENT console — {product}\033[0m\n"
            f"   app, db loaded.\n"
            f"   Models:   {model_names}\n"
            f"   Services: {service_names}\n"
        )

        # Try IPython first, fall back to standard REPL
        try:
            from IPython import embed

            print(banner)
            embed(user_ns=namespace, colors="neutral")
        except ImportError:
            code.interact(banner=banner, local=namespace)


cli_command = product_console
