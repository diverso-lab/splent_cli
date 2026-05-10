import os

from splent_cli.services.env import CLI_ENV_FILE_VAR, cli_env_path, load_cli_env


def test_cli_env_path_prefers_explicit_path(tmp_path, monkeypatch):
    env_path = tmp_path / "custom.env"
    monkeypatch.setenv(CLI_ENV_FILE_VAR, str(env_path))
    monkeypatch.setenv("WORKING_DIR", str(tmp_path / "workspace"))

    assert cli_env_path() == env_path


def test_cli_env_path_prefers_workspace_cli_env(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    cli_env = workspace / "splent_cli" / ".env"
    cli_env.parent.mkdir(parents=True)
    cli_env.write_text("SPLENT_API_TOKEN=repo-token\n")
    (workspace / ".env").write_text("SPLENT_API_TOKEN=workspace-token\n")
    monkeypatch.setenv("WORKING_DIR", str(workspace))
    monkeypatch.delenv(CLI_ENV_FILE_VAR, raising=False)

    assert cli_env_path() == cli_env


def test_cli_env_path_falls_back_to_workspace_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKING_DIR", str(tmp_path))
    monkeypatch.delenv(CLI_ENV_FILE_VAR, raising=False)

    assert cli_env_path() == tmp_path / ".env"


def test_load_cli_env_overrides_existing_process_env(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("SPLENT_API_TOKEN=file-token\n")
    monkeypatch.setenv(CLI_ENV_FILE_VAR, str(env_path))
    monkeypatch.setenv("SPLENT_API_TOKEN", "process-token")

    load_cli_env()

    assert os.getenv("SPLENT_API_TOKEN") == "file-token"


def test_load_cli_env_unsets_marketplace_values_missing_from_file(
    tmp_path, monkeypatch
):
    env_path = tmp_path / ".env"
    env_path.write_text("WORKING_DIR=/workspace\n")
    monkeypatch.setenv(CLI_ENV_FILE_VAR, str(env_path))
    monkeypatch.setenv("SPLENT_API_URL", "http://env-api.local")
    monkeypatch.setenv("SPLENT_API_TOKEN", "process-token")
    monkeypatch.setenv("SPLENT_MARKETPLACE_AUTH", "true")

    load_cli_env()

    assert os.getenv("SPLENT_API_URL") is None
    assert os.getenv("SPLENT_API_TOKEN") is None
    assert os.getenv("SPLENT_MARKETPLACE_AUTH") is None
