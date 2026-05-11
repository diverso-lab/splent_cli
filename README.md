# SPLENT CLI

Command-line tool for managing SPLENT products, features, databases, and environments.

## Quick start

```bash
make setup        # Prepare .env, start Docker, enter CLI container
splent --help     # See all available commands
```

## Local marketplace flow

When using the CLI from inside the SPLENT Docker container, `127.0.0.1`
points to the container itself. If your marketplace/API is running on your
host machine, use `host.docker.internal`.

The marketplace/API must expose:

```text
GET  /api/auth/check
POST /api/packages
GET  /api/packages
```

For local development with token authentication, configure the API with:

```env
SPLENT_API_TOKEN=mi-token-secreto-local
```

Then, from the app workspace/container, log in:

```bash
splent marketplace:login --url http://host.docker.internal:5000 --token mi-token-secreto-local
```

`marketplace:login` requires a token. You can also put the token in `.env`
first and then run login without flags:

```env
SPLENT_API_URL=http://host.docker.internal:5000
SPLENT_API_TOKEN=mi-token-secreto-local
```

```bash
splent marketplace:login
```

This validates the token and saves the marketplace configuration in the
workspace `.env`:

```env
SPLENT_API_URL=http://host.docker.internal:5000
SPLENT_API_TOKEN=mi-token-secreto-local
SPLENT_MARKETPLACE_AUTH=true
```

`SPLENT_MARKETPLACE_AUTH=true` means the token has been validated with
`GET /api/auth/check`. `feature:search` and `feature:publish` require this
validated login state and a configured `SPLENT_API_TOKEN`.

To log out:

```bash
splent marketplace:logout
```

Logout keeps `SPLENT_API_URL` and `SPLENT_API_TOKEN`, but marks the marketplace
session as inactive:

```env
SPLENT_API_URL=http://host.docker.internal:5000
SPLENT_API_TOKEN=mi-token-secreto-local
SPLENT_MARKETPLACE_AUTH=false
```

Run `splent marketplace:login` again to re-validate the saved token.

Create a feature and publish it:

```bash
splent feature:create splent-io/splent_feature_demo_marketplace --type light
splent feature:publish splent-io/splent_feature_demo_marketplace
```

Verify that it appears in the marketplace:

```bash
splent feature:search demo
```

You can also publish with an explicit version:

```bash
splent feature:publish splent-io/splent_feature_demo_marketplace@0.1.0
```

## Requirements

- Docker + Docker Compose
- Python 3.13+

## Documentation

Full docs, tutorials, and command reference at **[docs.splent.io](https://docs.splent.io)**

## License

Creative Commons CC BY 4.0 - SPLENT - Diverso Lab
