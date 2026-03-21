import subprocess
import click


def _run(cmd: list) -> tuple:
    """Returns (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        return 1, "", "command not found"
    except subprocess.TimeoutExpired:
        return 1, "", "timed out"


def _ok(msg): return click.style("[✔] ", fg="green") + msg
def _fail(msg): return click.style("[✖] ", fg="red") + msg
def _warn(msg): return click.style("[⚠] ", fg="yellow") + msg


@click.command("check:docker", short_help="Verify Docker and Docker Compose are available and running.")
def check_docker():
    """
    Checks that Docker and Docker Compose are installed and the Docker daemon is responding.
    """
    click.echo(click.style("\n🐳 Docker Check\n", fg="cyan", bold=True))
    ok_count = fail_count = 0

    # 1. docker binary
    rc, out, _ = _run(["docker", "--version"])
    if rc == 0:
        click.echo(_ok(f"Docker installed  —  {out}"))
        ok_count += 1
    else:
        click.echo(_fail("Docker not found — install Docker Desktop or Docker Engine"))
        fail_count += 1

    # 2. docker compose
    rc, out, _ = _run(["docker", "compose", "version"])
    if rc == 0:
        click.echo(_ok(f"Docker Compose available  —  {out}"))
        ok_count += 1
    else:
        # try legacy docker-compose
        rc2, out2, _ = _run(["docker-compose", "--version"])
        if rc2 == 0:
            click.echo(_warn(f"Legacy docker-compose found  —  {out2}  (upgrade to Compose V2)"))
            ok_count += 1
        else:
            click.echo(_fail("Docker Compose not found"))
            fail_count += 1

    # 3. daemon responding
    rc, _, err = _run(["docker", "info"])
    if rc == 0:
        click.echo(_ok("Docker daemon is running"))
        ok_count += 1
    else:
        msg = err.splitlines()[0] if err else "unknown error"
        click.echo(_fail(f"Docker daemon not responding — {msg}"))
        fail_count += 1

    # 4. can list containers (basic permissions check)
    rc, out, err = _run(["docker", "ps", "--format", "{{.Names}}"])
    if rc == 0:
        n = len([l for l in out.splitlines() if l.strip()])
        click.echo(_ok(f"Docker socket accessible  —  {n} container(s) running"))
        ok_count += 1
    else:
        msg = err.splitlines()[0] if err else "permission denied"
        click.echo(_fail(f"Cannot access Docker socket — {msg}"))
        fail_count += 1

    click.echo()
    if fail_count == 0:
        click.secho("✅ Docker environment OK.", fg="green")
    else:
        click.secho(f"❌ {fail_count} check(s) failed.", fg="red")
        raise SystemExit(1)


cli_command = check_docker
