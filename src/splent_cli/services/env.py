import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv


CLI_ENV_FILE_VAR = "SPLENT_CLI_ENV_FILE"
SYNCED_ENV_VARS = (
    "SPLENT_API_URL",
    "SPLENT_API_TOKEN",
    "SPLENT_MARKETPLACE_AUTH",
)


def cli_env_path() -> Path:
    explicit_path = os.getenv(CLI_ENV_FILE_VAR)
    if explicit_path:
        return Path(explicit_path).expanduser()

    workspace = Path(os.getenv("WORKING_DIR", "/workspace"))
    workspace_cli_env = workspace / "splent_cli" / ".env"
    if workspace_cli_env.exists():
        return workspace_cli_env

    if os.getenv("WORKING_DIR"):
        return workspace / ".env"

    source_root = Path(__file__).resolve().parents[3]
    source_env = source_root / ".env"
    if source_env.exists():
        return source_env

    return workspace / ".env"


def load_cli_env() -> None:
    env_path = cli_env_path()
    if env_path.exists():
        values = dotenv_values(env_path)
        load_dotenv(env_path, override=True)
        for key in SYNCED_ENV_VARS:
            if key not in values:
                os.environ.pop(key, None)
