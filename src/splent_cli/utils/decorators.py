# splent_cli/utils/decorators.py

def requires_app(command):
    command.requires_app = True
    return command
