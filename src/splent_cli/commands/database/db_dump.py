import click
from dotenv import load_dotenv
import gzip
import os
import re
import shutil
import tempfile
from datetime import datetime

from splent_cli.services import context
from splent_cli.utils.proc import run, require_tool

# A dump below this size cannot be a real database export (even an empty
# schema produces a larger header), so treat anything smaller as truncated
# output from a connection that dropped mid-dump.
MIN_DUMP_BYTES = 1024

# Auto-generated dumps are named dump_<database>_YYYYMMDD_HHMMSS.sql[.gz].
# The database name is part of the prefix on purpose: dumps land in the
# directory the CLI was invoked from, which is usually the workspace root
# shared by every product, so a prefix of just "dump" would make one product's
# retention delete every other product's dumps. Retention only ever deletes
# files matching this exact shape, so unrelated files are never touched.
_TIMESTAMP_RE = re.compile(r"_\d{8}_\d{6}$")


def _prune_old_dumps(directory, kept_basename, retention):
    """Keep the ``retention`` most recent timestamped dumps that share
    ``kept_basename``'s prefix in ``directory`` and delete the rest."""
    stem = kept_basename
    for ext in (".sql.gz", ".sql"):
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
            break
    prefix = _TIMESTAMP_RE.sub("", stem)
    pattern = re.compile(re.escape(prefix) + r"_\d{8}_\d{6}\.sql(\.gz)?")
    candidates = [
        os.path.join(directory, name)
        for name in os.listdir(directory)
        if pattern.fullmatch(name)
    ]
    candidates.sort(key=os.path.getmtime, reverse=True)
    for stale in candidates[retention:]:
        os.remove(stale)
        click.echo(click.style(f"Pruned old dump {os.path.basename(stale)}", dim=True))


@click.command(
    "db:dump",
    short_help="Create a SQL dump of the MariaDB database.",
)
@click.argument("filename", required=False)
@click.option(
    "--gzip",
    "gzip_output",
    is_flag=True,
    help="Compress the dump with gzip (the file gets a .sql.gz extension).",
)
@click.option(
    "--retention",
    type=click.IntRange(min=0),
    default=0,
    help=(
        "Keep only the N most recent timestamped dumps with the same prefix "
        "in the target directory. 0 keeps everything."
    ),
)
@context.requires_product
def db_dump(filename, gzip_output, retention):
    load_dotenv()

    mariadb_hostname = os.getenv("MARIADB_HOSTNAME")
    mariadb_user = os.getenv("MARIADB_USER")
    mariadb_password = os.getenv("MARIADB_PASSWORD")
    mariadb_database = os.getenv("MARIADB_DATABASE")

    missing = [
        k
        for k, v in {
            "MARIADB_HOSTNAME": mariadb_hostname,
            "MARIADB_USER": mariadb_user,
            "MARIADB_PASSWORD": mariadb_password,
            "MARIADB_DATABASE": mariadb_database,
        }.items()
        if not v
    ]
    if missing:
        click.secho(f"❌ Missing env vars: {', '.join(missing)}", fg="red")
        raise SystemExit(1)

    # Generate default filename if not provided
    if not filename:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"dump_{mariadb_database}_{timestamp}.sql"
        if gzip_output:
            filename += ".gz"
    elif filename.endswith(".sql.gz"):
        # An explicit .sql.gz name implies compression.
        gzip_output = True
    elif filename.endswith(".sql"):
        if gzip_output:
            filename += ".gz"
    else:
        # Ensure filename has the right extension
        filename += ".sql.gz" if gzip_output else ".sql"

    require_tool(
        "mysqldump",
        "Install the MariaDB/MySQL client tools (provides 'mysqldump').",
    )

    env = {**os.environ, "MYSQL_PWD": mariadb_password or ""}

    # Dump to a temp file first and only replace the target on success, so a
    # pre-existing file with the same name is never truncated or deleted if the
    # dump fails midway.
    target_dir = os.path.dirname(os.path.abspath(filename)) or "."
    fd, tmp_path = tempfile.mkstemp(
        prefix=".db_dump_", suffix=".sql.tmp", dir=target_dir
    )
    # Every temp file that may need cleaning up if the dump fails.
    temp_paths = [tmp_path]
    try:
        with os.fdopen(fd, "wb") as out:
            run(
                [
                    "mysqldump",
                    # Reliability flags. --single-transaction takes a
                    # consistent snapshot without locking tables, --quick
                    # streams rows instead of buffering whole tables in
                    # memory, and the explicit charset avoids silent
                    # mojibake on restore.
                    "--single-transaction",
                    "--quick",
                    "--default-character-set=utf8mb4",
                    f"-h{mariadb_hostname}",
                    f"-u{mariadb_user}",
                    mariadb_database,
                ],
                stdout=out,
                text=False,
                env=env,
                tool_hint="Install the MariaDB/MySQL client tools (provides 'mysqldump').",
            )

        # A truncated dump restores as an empty or partial database, which is
        # worse than no dump at all. Refuse to promote anything implausibly
        # small.
        size = os.path.getsize(tmp_path)
        if size < MIN_DUMP_BYTES:
            raise click.ClickException(
                f"Dump is only {size} bytes (minimum {MIN_DUMP_BYTES}). "
                "The partial file was discarded."
            )

        if gzip_output:
            # mkstemp again rather than gzip.open on a plain path: a dump holds
            # every password hash in the user table, and gzip.open would create
            # the file 0644 through the umask. mkstemp gives 0600, which
            # os.replace preserves. Writing through GzipFile(fileobj=...) also
            # keeps the temp file's name out of the gzip header.
            gz_fd, gz_tmp_path = tempfile.mkstemp(
                prefix=".db_dump_", suffix=".sql.gz.tmp", dir=target_dir
            )
            temp_paths.append(gz_tmp_path)
            with os.fdopen(gz_fd, "wb") as gz_raw:
                with gzip.GzipFile(fileobj=gz_raw, mode="wb") as gz:
                    with open(tmp_path, "rb") as raw:
                        shutil.copyfileobj(raw, gz)
            os.remove(tmp_path)
            temp_paths.remove(tmp_path)
            os.replace(gz_tmp_path, filename)
        else:
            os.replace(tmp_path, filename)
        click.echo(
            click.style(f"Database dump created successfully: {filename}", fg="green")
        )
    except BaseException:
        for partial in temp_paths:
            if os.path.exists(partial):
                os.remove(partial)
        raise

    if retention:
        _prune_old_dumps(target_dir, os.path.basename(filename), retention)


cli_command = db_dump
