import os
import subprocess
import click
import docker
import signal
import psutil

from splent_cli.services import context
from splent_cli.utils.proc import run, require_docker, require_tool


@click.command("locust", short_help="Launch the Locust load testing container.")
@click.argument("module", required=False)
@context.requires_product
def locust(module):
    # Absolute paths
    working_dir = os.getenv("WORKING_DIR", "")
    core_dir = os.path.join(working_dir, "core")
    docker_dir = os.path.join(working_dir, "docker/")
    modules_dir = os.path.join(working_dir, "app/modules")

    def validate_module(module):
        """Check if the module exists."""
        if module:
            module_path = os.path.join(modules_dir, module)
            if not os.path.exists(module_path):
                raise click.UsageError(f"module '{module}' does not exist.")
            locustfile_path = os.path.join(module_path, "tests", "locustfile.py")
            if not os.path.exists(locustfile_path):
                raise click.UsageError(
                    f"Locustfile for module '{module}' does not exist at path "
                    f"'{locustfile_path}'."
                )

    def run_docker_locust(volume_name, module):
        """Build and run the Locust container with the specified volume."""

        try:
            # Check if the container already exists
            client.containers.get("locust_container")
            click.echo("Locust container is already running.")
            return
        except docker.errors.NotFound:
            pass  # Container does not exist, proceed to create it

        click.echo(
            f"Starting Locust in Docker environment on port 8089 with volume: {volume_name}..."
        )

        # Build Locust's image
        build_command = [
            "docker",
            "build",
            "-f",
            os.path.join(docker_dir, "images/Dockerfile.locust"),
            "-t",
            "locust-image",
            ".",
        ]
        click.echo(f"Build command: {' '.join(build_command)}")
        run(build_command)

        # Define the locustfile path
        locustfile_path = os.path.join(core_dir, "bootstraps/locustfile_bootstrap.py")
        if module:
            locustfile_path = f"{modules_dir}/{module}/tests/locustfile.py"

        # Only attach to docker_flasky_network if it actually exists; otherwise
        # the container would fail to start with an unhelpful docker error.
        network_name = "docker_flasky_network"
        network_args = []
        network_check = run(
            ["docker", "network", "inspect", network_name],
            check=False,
            capture=True,
        )
        if network_check.returncode == 0:
            network_args = ["--network", network_name]
        else:
            click.secho(
                f"⚠️  Network '{network_name}' not found; starting Locust without it.",
                fg="yellow",
            )

        # Run the Locust container
        up_command = [
            "docker",
            "run",
            "-d",
            "-p",
            "8089:8089",
            "-v",
            f"{volume_name}:/workspace",
            "--name",
            "locust_container",
            *network_args,
            "locust-image",
            "-f",
            locustfile_path,
        ]

        click.echo(f"Docker Run command: {' '.join(up_command)}")
        run(up_command)
        click.echo(
            click.style("Locust is running at http://localhost:8089", fg="green")
        )

    def _is_our_locust(proc):
        """True only for a locust process started from this working dir."""
        try:
            info = proc.as_dict(attrs=["name", "cmdline", "cwd"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
        name = info.get("name") or ""
        cmdline = info.get("cmdline") or []
        is_locust = name == "locust" or any(
            os.path.basename(part) == "locust" for part in cmdline[:1]
        )
        if not is_locust:
            return False
        # Scope to processes launched from this working dir so we don't match
        # an unrelated locust running elsewhere on the machine.
        cwd = info.get("cwd")
        if cwd and working_dir:
            return os.path.normpath(cwd).startswith(os.path.normpath(working_dir))
        return cwd is None or not working_dir

    def is_locust_running():
        """Check if Locust is already running for this working dir."""
        for proc in psutil.process_iter(["pid", "name"]):
            if _is_our_locust(proc):
                return True
        return False

    def run_in_console(module):
        if is_locust_running():
            click.echo("Locust is already running.")
            return

        locustfile_path = os.path.join(core_dir, "bootstraps/locustfile_bootstrap.py")
        if module:
            locustfile_path = os.path.join(
                modules_dir, module, "tests", "locustfile.py"
            )
        require_tool(
            "locust",
            "Install it with: pip install locust",
        )
        locust_command = ["locust", "-f", locustfile_path]
        click.echo(f"Locust command: {' '.join(locust_command)}")
        subprocess.Popen(
            locust_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        click.echo(
            click.style("Locust is running at http://localhost:8089", fg="green")
        )

    def run_local_locust(module):
        """Run Locust in the local environment."""
        click.echo("Starting Locust in local environment on port 8089...")
        run_in_console(module)

    def run_vagrant_locust(module):
        """Run Locust in the Vagrant environment."""
        click.echo("Starting Locust in Vagrant environment on port 8089...")
        run_in_console(module)

    # Validate module if provided
    if module:
        validate_module(module)

    if working_dir == "/workspace/":
        # Ensure docker is installed and the daemon is reachable before we try
        # to talk to it, so a broken setup yields an actionable message.
        require_docker()
        try:
            client = docker.from_env()
        except docker.errors.DockerException as e:
            raise click.ClickException(f"Could not connect to the Docker daemon: {e}")

        try:
            web_container = client.containers.get("web_app_container")
            volume_name = next(
                (
                    mount.get("Name") or mount.get("Source")
                    for mount in web_container.attrs.get("Mounts", [])
                    if mount.get("Destination") == "/app"
                ),
                None,
            )

            if not volume_name:
                raise ValueError("No volume or bind mount found mounted on /app")

            run_docker_locust(volume_name, module)

        except docker.errors.NotFound:
            click.echo(click.style("Web container not found.", fg="red"))
        except click.ClickException:
            raise
        except Exception as e:
            click.echo(click.style(f"An error occurred: {str(e)}", fg="red"))

    elif working_dir == "":
        run_local_locust(module)

    elif working_dir == "/vagrant/":
        run_vagrant_locust(module)

    else:
        click.echo(click.style(f"Unrecognized WORKING_DIR: {working_dir}", fg="red"))


@click.command("locust:stop", short_help="Stop the Locust load testing container.")
def stop():
    working_dir = os.getenv("WORKING_DIR", "")

    def _is_our_locust(proc):
        """True only for a locust process started from this working dir."""
        try:
            info = proc.as_dict(attrs=["name", "cmdline", "cwd"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
        name = info.get("name") or ""
        cmdline = info.get("cmdline") or []
        is_locust = name == "locust" or any(
            os.path.basename(part) == "locust" for part in cmdline[:1]
        )
        if not is_locust:
            return False
        cwd = info.get("cwd")
        if cwd and working_dir:
            return os.path.normpath(cwd).startswith(os.path.normpath(working_dir))
        return cwd is None or not working_dir

    def stop_local_locust():
        """Stop Locust process in the local environment."""
        click.echo("Stopping Locust in local environment...")
        for proc in psutil.process_iter(["pid", "name"]):
            if _is_our_locust(proc):
                click.echo(f"Stopping Locust process with PID {proc.pid}...")
                try:
                    os.kill(proc.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError) as e:
                    click.secho(f"⚠️  Could not stop PID {proc.pid}: {e}", fg="yellow")

    def stop_docker_locust():
        click.echo("Stopping Locust container if it is running...")
        require_docker()
        stop_command = ["docker", "stop", "locust_container"]
        rm_command = ["docker", "rm", "locust_container"]

        result = run(stop_command, check=False, capture=True)
        if result.returncode != 0:
            click.secho("⚠️  Could not stop Locust container.", fg="yellow")

        result = run(rm_command, check=False, capture=True)
        if result.returncode != 0:
            click.secho("⚠️  Could not remove Locust container.", fg="yellow")

    if working_dir == "/workspace/":
        stop_docker_locust()

    elif working_dir == "" or working_dir == "/vagrant/":
        stop_local_locust()

    else:
        click.echo(click.style(f"Unrecognized WORKING_DIR: {working_dir}", fg="red"))
