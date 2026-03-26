import os
import time
import click
import requests
from splent_cli.commands.feature.feature_clone import feature_clone
from splent_cli.utils.cache_utils import make_feature_writable
from splent_cli.services import context


@click.command(
    "feature:fork",
    short_help="Fork a feature on GitHub and clone it locally",
)
@click.argument("feature_name", required=True)
@click.option(
    "--version", "-v", default="v1.0.0", help="Feature version (default: v1.0.0)"
)
def feature_fork(feature_name, version):
    token = os.getenv("GITHUB_TOKEN")
    github_user = os.getenv("GITHUB_USER")

    if not token or not github_user:
        click.secho("✖ Error: GITHUB_TOKEN and GITHUB_USER must be set", fg="red")
        raise SystemExit(1)

    upstream_owner = "splent-io"
    api_url = f"https://api.github.com/repos/{upstream_owner}/{feature_name}/forks"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }

    click.secho(
        f"🔁 Forking {upstream_owner}/{feature_name} into {github_user}...", fg="cyan"
    )
    resp = requests.post(api_url, headers=headers, json={"default_branch_only": False})
    if resp.status_code not in (201, 202):
        click.secho(f"❌ Failed: {resp.status_code} {resp.text}", fg="red")
        raise SystemExit(2)

    fork_html_url = resp.json()["html_url"]
    click.secho(f"🌐 Fork created: {fork_html_url}", fg="green")

    # Esperar a que GitHub lo procese
    for i in range(5):
        r = requests.get(
            f"https://api.github.com/repos/{github_user}/{feature_name}",
            headers=headers,
        )
        if r.status_code == 200:
            click.secho("✅ Fork is ready.", fg="green")
            break
        click.secho("⌛ Waiting for GitHub to process fork...", fg="yellow")
        time.sleep(3)
    else:
        click.secho("⚠ Fork may still be processing.", fg="yellow")

    # Llamar a clone automáticamente
    click.secho("\n🚀 Cloning fork into local namespace...\n", fg="cyan")
    ctx = click.get_current_context()
    ctx.invoke(feature_clone, full_name=f"{github_user}/{feature_name}@{version}")

    # Forked features are meant to be edited — unlock the cached copy
    ns_safe = github_user.replace("-", "_").replace(".", "_")
    workspace = str(context.workspace())
    forked_path = os.path.join(
        workspace, ".splent_cache", "features", ns_safe, f"{feature_name}@{version}"
    )
    if os.path.exists(forked_path):
        make_feature_writable(forked_path)
        click.secho("🔓 Fork unlocked for editing.", fg="green")
