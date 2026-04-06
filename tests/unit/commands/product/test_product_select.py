"""Tests for product:select — SystemExit on missing product, .env update."""
from click.testing import CliRunner
from splent_cli.commands.product.product_select import select_app


class TestProductSelect:
    def test_exits_when_product_not_found(self, workspace):
        runner = CliRunner(mix_stderr=True)
        result = runner.invoke(select_app, ["nonexistent_app"])
        assert result.exit_code == 1
        assert (
            "not found" in result.output.lower()
            or "nonexistent_app" in result.output
        )

    def test_creates_env_entry_when_product_exists(self, workspace):
        (workspace / "myapp").mkdir()
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(select_app, ["myapp"])
        assert result.exit_code == 0
        env_content = (workspace / ".env").read_text()
        assert "SPLENT_APP=myapp" in env_content

    def test_updates_existing_env_entry(self, workspace):
        (workspace / "myapp").mkdir()
        (workspace / "otherapp").mkdir()
        (workspace / ".env").write_text("SPLENT_APP=otherapp\n")
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(select_app, ["myapp"])
        assert result.exit_code == 0
        env_content = (workspace / ".env").read_text()
        assert "SPLENT_APP=myapp" in env_content
        assert "SPLENT_APP=otherapp" not in env_content
