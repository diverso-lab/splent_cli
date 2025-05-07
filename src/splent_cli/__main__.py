from splent_cli.cli import check_working_dir, cli, load_commands
from splent_cli.utils.feature_installer import ensure_editable_features_installed


def main():
    check_working_dir()
    ensure_editable_features_installed()
    load_commands(cli)
    cli()


if __name__ == "__main__":
    main()

__all__ = ["main"]
