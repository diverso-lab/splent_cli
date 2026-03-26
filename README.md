# SPLENT CLI

Command-line tool for managing SPLENT products, features, databases, and environments.

## Quick start

```bash
make setup           # Prepare .env, start Docker, enter CLI container
splent --help        # See all available commands
```

## Key commands

| Command | Description |
|---------|-------------|
| `product:create` | Create a new product |
| `product:derive --dev` | Full SPL derivation pipeline |
| `feature:add` / `feature:attach` | Add features to a product |
| `feature:status` | Show feature lifecycle states |
| `feature:release` | Release a feature (tag + PyPI + GitHub) |
| `db:migrate` / `db:upgrade` | Manage per-feature migrations |
| `export:puml` | Generate PlantUML diagrams |
| `doctor` | System health check |

## Requirements

- Docker + Docker Compose
- Python 3.13+

## Documentation

Full documentation at **[docs.splent.io](https://docs.splent.io)**

## License

Creative Commons CC BY 4.0 - SPLENT - Diverso Lab
