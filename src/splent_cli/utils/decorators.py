# splent_cli/utils/decorators.py


def requires_app(command):
    command.requires_app = True
    return command


def requires_db(command):
    """Mark a command as needing both Flask app context and an active DB connection.

    The CLI runner will perform a connectivity check before executing the command
    and show a clean error if the database is unreachable.
    """
    command.requires_app = True
    command.requires_db = True
    return command
