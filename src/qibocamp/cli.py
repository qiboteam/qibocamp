"""Command Line Interface for qibocamp (Issue #9, #10, #11)."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
import uvicorn

from qibocamp import config
from qibocamp.web.api import set_report_root
from qibocamp.logger import get_uvicorn_log_config



def name_server(name: str) -> None:
    """The name the server answers with, the one it is registered under."""
    from qibocamp.web import api

    os.environ["QIBOCAMP_SERVER_NAME"] = name
    api.SERVER_NAME = name

@click.group(name="qibocamp")
@click.version_option(package_name="qibocamp", prog_name="qibocamp")
def main():
    """Qibocamp: the command line and server for Qibocal calibration reports."""


def requirements_text(_context, _parameter, value: str | None) -> str | None:
    """The requirements as given, or the content of the file named after `@`."""
    if value is None or not value.startswith("@"):
        return value
    path = Path(value[1:]).expanduser()
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise click.BadParameter(f"Could not read '{path}': {error}") from error


def notebook_options(*, hidden: bool = False):
    """Keep connect and add option parsing identical."""

    def decorate(command):
        for option in reversed(
            [
                click.option(
                    "--ssh",
                    hidden=hidden,
                    help="SSH options followed by a host or user@host destination.",
                ),
                click.option(
                    "-q",
                    "--queue",
                    hidden=hidden,
                    help="SLURM partition on the access node.",
                ),
                click.option(
                    "--slurm",
                    hidden=hidden,
                    help="Additional srun options, as a quoted argument string.",
                ),
                click.option(
                    "-w",
                    "--workdir",
                    hidden=hidden,
                    help="Working directory on the access node.",
                ),
                click.option(
                    "--venv",
                    hidden=hidden,
                    help="Kernel environment name or path on the computing node.",
                ),
                click.option(
                    "--marimo/--jupyter",
                    default=None,
                    hidden=hidden,
                    help="Use Marimo instead of JupyterLab.",
                ),
                click.option(
                    "--no-interactive/--interactive",
                    "-n",
                    default=None,
                    hidden=hidden,
                    help="Print the connection URL without opening a browser.",
                ),
                click.option(
                    "--timeout",
                    hidden=hidden,
                    type=click.FloatRange(min=0, min_open=True),
                    help="Startup timeout in seconds (default: 300).",
                ),
                click.option(
                    "--subnet",
                    hidden=hidden,
                    metavar="CIDR",
                    help="Restrict SLURM computing-node IPs to this subnet "
                    "(e.g. 192.168.0.0/24).",
                ),
                click.option(
                    "--rebuild-on-compute/--rebuild-on-access",
                    default=None,
                    hidden=hidden,
                    help="Where `notebook rebuild` regenerates the environment of a "
                    "SLURM connection: on a computing node through srun, or on the "
                    "access node (default).",
                ),
                click.option(
                    "--remote",
                    hidden=hidden,
                    metavar="NAME",
                    help="A remote execution connection of remotes.json, for qibocal-widgets: "
                    "exported to the notebook as QIBOCAL_WIDGETS_CONNECTION, and written "
                    "to remotes.json where the notebook runs.",
                ),
                click.option(
                    "--reports",
                    hidden=hidden,
                    metavar="DIR",
                    help="Where the session's reports are kept, exported to the notebook "
                    "and its kernels as QIBOCAMP_REPORTS_DIR; relative to the working "
                    "directory.",
                ),
                click.option(
                    "--requirements",
                    hidden=hidden,
                    metavar="TEXT|@FILE",
                    callback=requirements_text,
                    help="What the kernel environment must hold, in requirements.txt "
                    "format, or @FILE to read it from a file: installed when the "
                    "environment is created, and on top of it whenever it changes.",
                ),
            ]
        ):
            command = option(command)
        return command

    return decorate


def remote_server_options(command):
    """The flags that move `report server` onto another machine.

    They are named after the notebook ones on purpose: a host reachable by
    `qibocamp notebook connect --ssh … --venv …` serves reports with the same
    two words.
    """
    for option in reversed(
        [
            click.option(
                "--ssh",
                help="SSH options followed by a host or user@host destination. "
                "Given, DIRECTORY is read on that host and the server is "
                "tunnelled back here.",
            ),
            click.option(
                "-w",
                "--workdir",
                help="Working directory on the remote host (default: a fresh "
                "temporary directory).",
            ),
            click.option(
                "--venv",
                help="Environment name or path on the remote host "
                "(default: qibocamp). It is created when missing.",
            ),
            click.option(
                "--install",
                help="Packages installed in the remote environment when it "
                "has no qibocamp (default: this qibocamp, from GitHub: "
                "the commit it was installed from, or main).",
            ),
            click.option(
                "--timeout",
                type=click.FloatRange(min=0, min_open=True),
                help="Remote startup timeout in seconds (default: 300).",
            ),
        ]
    ):
        command = option(command)
    return command


def registration_options(command):
    """How a server shows up in `servers.json`, if at all.

    The launcher starts most servers for a single look; those have no business
    in the configuration, hence `--no-register`.
    """
    for option in reversed(
        [
            click.option(
                "--name",
                help="Name the server is registered under (default: generated).",
            ),
            click.option(
                "--register/--no-register",
                default=True,
                help="Add the server to servers.json, where the dashboard "
                "finds it (default: --register).",
            ),
        ]
    ):
        command = option(command)
    return command


def reports_directory(directory: str | None) -> Path:
    """The directory to serve: the one named, or the shared one, created on demand."""
    if directory is None:
        return config.get_reports_dir()
    path = Path(directory)
    if not path.is_dir():
        raise click.UsageError(f"Directory '{directory}' does not exist.")
    return path.resolve()


@main.group(name="notebook")
def notebook():
    """Connect to notebooks and manage registered connections."""


@notebook.command(name="connect")
@click.argument("connection", required=False)
@notebook_options()
def notebook_connect(connection: str | None, **options):
    """Start a local, SSH, or SLURM notebook, optionally using a named CONNECTION."""
    from qibocamp.remote.notebook import launch, load_options

    launch(load_options(connection, options))


@notebook.command(name="rebuild")
@click.argument("connection", required=False)
@notebook_options()
def notebook_rebuild(connection: str | None, **options):
    """Destroy the kernel environment of a session and make it again from scratch.

    Where the sessions of CONNECTION — or of the options given — run, the
    kernel environment is removed entirely and created anew, with qibocal
    and the --requirements alone; whatever else was installed in it is gone.
    No notebook is started. Only a Python virtual environment is removed.
    """
    from qibocamp.remote.notebook import load_options, rebuild

    rebuild(load_options(connection, options))


@notebook.command(name="queues")
@click.option("--ssh", help="SSH options followed by a host or user@host destination: the machine; empty, this one.")
@click.option("-q", "--queue", "partitions", multiple=True, help="A partition whose jobs are wanted (repeatable).")
@click.option("--json", "as_json", is_flag=True, help="Print the answer as JSON.")
@click.option("--timeout", type=float, default=30.0, show_default=True, help="Seconds to wait for the controller.")
def notebook_queues(ssh, partitions, as_json, timeout):
    """What a machine's SLURM queues are doing, asked once.

    The jobs of each --queue, and a line for every partition of the machine:
    its nodes by state, its jobs by state, and how many are yours. One
    squeue for the whole machine, filtered there, so the answer is only as
    large as what it shows.
    """
    import json as json_module

    from qibocamp.remote.slurm import query_machine

    try:
        answer = query_machine(ssh=ssh, partitions=list(partitions), timeout=timeout)
    except (RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json_module.dumps(answer, indent=2))
        return
    if answer["error"]:
        raise click.ClickException(answer["error"])
    from rich.console import Console
    from rich.table import Table

    table = Table(title=f"Partitions on {ssh or 'this machine'}", show_header=True, header_style="bold")
    for column in ("Partition", "Nodes", "Jobs", "Yours"):
        table.add_column(column)
    describe = lambda counts: ", ".join(f"{count} {state.lower()}" for state, count in counts.items()) or "—"
    for line in answer["partitions"]:
        name = line["partition"] + ("*" if line["default"] else "") + ("" if line["known"] else " (other)")
        table.add_row(name, describe(line["nodes"]), describe(line["jobs"]), str(line["mine"]))
    Console().print(table)


@notebook.command(name="queue")
@click.argument("connection", required=False)
@click.option("--ssh", help="SSH options followed by a host or user@host destination.")
@click.option("-q", "--queue", help="SLURM partition to look at.")
@click.option("-u", "--user", help="Only the jobs of this user.")
@click.option(
    "-t", "--state", "states", multiple=True,
    help="Only jobs in this state (repeatable): PENDING, RUNNING, …",
)
@click.option("--json", "as_json", is_flag=True, help="Print the answer as JSON.")
@click.option("--timeout", type=float, default=30.0, show_default=True, help="Seconds to wait for the controller.")
def notebook_queue(connection, ssh, queue, user, states, as_json, timeout):
    """What a SLURM queue is doing, asked once.

    The host and the partition come from a saved CONNECTION, or from --ssh
    and --queue; squeue, sinfo and whoami are run in one round trip. Ask
    sparingly: every call costs the controller something.
    """
    import json as json_module

    from qibocamp.remote.notebook import load_connections, registered_connection
    from qibocamp.remote.slurm import query_queue

    values = {}
    if connection is not None:
        values = registered_connection(load_connections(), connection)
    ssh = ssh or values.get("ssh")
    queue = queue or values.get("queue")
    try:
        answer = query_queue(
            ssh=ssh, partition=queue, user=user, states=list(states) or None, timeout=timeout
        )
    except (RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json_module.dumps(answer, indent=2))
        return
    if answer["error"]:
        raise click.ClickException(answer["error"])
    from rich.console import Console
    from rich.table import Table

    console = Console()
    summary = answer["summary"]
    nodes = ", ".join(f"{count} {state.lower()}" for state, count in summary["nodes"].items())
    where = f"partition {queue}" if queue else "every partition"
    console.print(f"[bold]{where}[/bold]" + (f" on {ssh}" if ssh else "") + (f" · nodes: {nodes}" if nodes else ""))
    table = Table(show_header=True, header_style="bold")
    for column in ("Job", "Partition", "Name", "User", "State", "Time", "Limit", "Nodes"):
        table.add_column(column)
    for job in answer["jobs"]:
        table.add_row(
            job["id"], job["partition"], job["name"], job["user"], job["state"],
            job["time"], job["time_limit"], job["nodes"] or (f"({job['reason']})" if job["reason"] else ""),
        )
    console.print(table)


@notebook.command(name="list")
@click.option("--raw", is_flag=True, help="Print the connection file verbatim.")
def notebook_list(raw: bool):
    """List registered notebook connections."""
    from qibocamp.remote.notebook import list_connections

    list_connections(raw=raw)


@notebook.command(name="add")
@click.argument("connection", required=False)
@notebook_options(hidden=True)
def notebook_add(connection: str | None, **options):
    """Register CONNECTION with the options of `qibocamp notebook connect`.

    See `qibocamp notebook connect --help` for all connection options.
    With no options, prompt for a name and non-default options.
    """
    from qibocamp.remote.notebook import add_connection, prompt_options

    interactive = all(value is None for value in options.values())
    if connection is None:
        connection = click.prompt("Connection name")
    if interactive:
        options = prompt_options(
            [
                param
                for param in notebook_connect.params
                if isinstance(param, click.Option)
            ]
        )
    add_connection(connection, options)


@notebook.command(name="update")
@click.argument("connection", required=False)
@notebook_options(hidden=True)
def notebook_update(connection: str | None, **options):
    """Update an existing CONNECTION using `qibocamp notebook connect` options.

    See `qibocamp notebook connect --help` for all connection options.
    Unspecified settings are preserved. With no options, prompt for changes
    using the connection's current values.
    """
    from qibocamp.remote.notebook import load_options, prompt_options, update_connection

    if connection is None:
        connection = click.prompt("Connection name")
    if all(value is None for value in options.values()):
        options = prompt_options(
            [
                param
                for param in notebook_connect.params
                if isinstance(param, click.Option)
            ],
            current=load_options(connection, {}),
        )
    update_connection(connection, options)


@notebook.command(name="remove")
@click.argument("connection")
def notebook_remove(connection: str):
    """Remove a registered CONNECTION immediately, without confirmation."""
    from qibocamp.remote.notebook import remove_connection

    remove_connection(connection)


@main.group(name="agents")
def agents():
    """Agent configurations of qibocal-agent."""


@agents.command(name="push")
@click.argument("ssh")
@click.option(
    "--agents",
    "entries",
    default="{}",
    help="The configurations to write, as a JSON object of agents.NAME entries.",
)
@click.option("--remove", multiple=True, help="A configuration to remove there; repeatable.")
def agents_push(ssh: str, entries: str, remove: tuple[str, ...]):
    """Write agent configurations into agents.json on the SSH destination.

    The entries given are merged into its `agents`, the names removed are
    dropped, and nothing else in the file changes: configurations kept here
    for an access node reach it this way, without environments or keys.
    """
    from qibocamp.remote.agents import push

    try:
        parsed = json.loads(entries)
    except ValueError as error:
        raise click.BadParameter(f"--agents is not JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise click.BadParameter("--agents must be a JSON object.")
    click.echo(json.dumps(push(ssh, parsed, list(remove))))


@agents.command(name="rebuild")
@click.argument("name")
@click.option("--venv", default=None, help="The configuration's environment; default: NAME.")
@click.option("--ssh", default=None, help="The configuration's SSH destination, where its server starts.")
@click.option("--via", default=None, help="The SSH destination of the machine the configuration is kept for.")
def agents_rebuild(name: str, venv: str | None, ssh: str | None, via: str | None):
    """Delete the managed environment of the agent configuration NAME.

    Where its calibration server starts, resolved as qibocal-agent resolves
    it, under `~/.cache/qibocal/agents/envs` unless absolute; qibocal-agent
    creates it again, from the configuration alone, the next time it starts.
    """
    from qibocamp.remote.agents import remove_environment

    click.echo(json.dumps(remove_environment(name, venv, ssh, via)))


@agents.command(name="install")
@click.option("--ref", default="main", show_default=True, help="Branch, tag or commit of qibocal-mcp-servers to install.")
def agents_install(ref: str):
    """Install qibocal-agent where the application keeps it, and print its path.

    In `~/.cache/qibocal/agents/tools`, created when missing, with Python 3.12
    or 3.13 from uv when this one is another; `REF` replaces whatever was
    installed. The printed path is the command an assistant registers.
    """
    from qibocamp.remote.agents import install_tools

    click.echo(install_tools(ref))


@main.group(name="config")
def config_group():
    """Locate and clean local server configurations."""


@config_group.command(name="path")
def config_path():
    """Print the configuration directory as a plain-text absolute path."""
    click.echo(config.get_config_dir(create=False).resolve())


@config_group.command(name="clean")
@click.option(
    "-f", "--force", is_flag=True, help="Remove configurations without prompting."
)
def config_clean(force: bool):
    """Erase registered servers and authentication data, asking for confirmation."""
    from qibocamp import auth

    config_dir = config.get_config_dir(create=False)
    paths = dict.fromkeys(
        path.parent.resolve() / path.name
        for path in (
            config.get_config_file(create=False),
            config_dir / "auth.json",
            auth.get_auth_file(create=False),
        )
    )
    existing = [path for path in paths if path.exists() or path.is_symlink()]
    if not existing:
        click.echo("No configuration files to remove.", err=True)
        return

    for path in existing:
        if path.is_dir() and not path.is_symlink():
            raise click.ClickException(
                f"Refusing to remove configuration directory '{path}'."
            )

    if not force:
        click.echo(
            "This erases registered servers, users, invitations, and signing keys:",
            err=True,
        )
        for path in existing:
            click.echo(f"  {path}", err=True)
        click.confirm(
            "Remove these configuration files?", default=False, abort=True, err=True
        )

    for path in existing:
        try:
            path.unlink()
        except OSError as exc:
            raise click.ClickException(f"Could not remove '{path}': {exc}") from exc
        click.echo(f"Removed {path}", err=True)


@main.group(name="report")
def report():
    """Manage and serve Qibocal calibration reports."""


class ServerGroup(click.Group):
    """`qibocamp report server [DIRECTORY]` starts a server; its subcommands act on one.

    A group with an optional argument would read `upgrade` as the directory:
    a first word that names a subcommand is the subcommand instead, and the
    group's own options are left at their defaults.
    """

    # Options may follow DIRECTORY, as they always could: a group would
    # otherwise stop at the directory and read `--ssh` as a command.
    allow_interspersed_args = True

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if args and args[0] in self.commands:
            super().parse_args(ctx, [])
            ctx._protected_args, ctx.args = [args[0]], list(args[1:])
            return ctx.args
        return super().parse_args(ctx, args)


@report.group(name="server", cls=ServerGroup, invoke_without_command=True)
@click.pass_context
@click.argument("directory", required=False, type=click.Path(file_okay=False))
@click.option("--host", default="localhost", help="Host address to bind to.")
@click.option("--port", default=8000, type=int, help="Port to listen on.")
@click.option(
    "--auth/--no-auth",
    default=False,
    help="Enable user authentication and role management.",
)
@registration_options
@remote_server_options
def server(
    ctx: click.Context,
    directory: str | None,
    host: str,
    port: int,
    auth: bool,
    name: str | None,
    register: bool,
    ssh: str | None,
    **remote,
):
    """Start the FastAPI backend server, here or on another machine.

    DIRECTORY holds the calibration runs to serve. Left out, the shared
    reports directory is served — $XDG_DATA_HOME/qibocamp/reports, created
    when missing — on whichever machine the server runs.

    `qibocamp report server upgrade` brings a remote server's qibocamp
    up to date instead of starting one.
    """
    if ctx.invoked_subcommand:
        return
    if ssh:
        from qibocamp.remote.server import launch, load_options

        launch(
            load_options(
                {
                    "ssh": ssh,
                    "directory": directory,
                    "auth": auth,
                    "name": name,
                    "registered": register,
                    **remote,
                }
            )
        )
        return
    for flag, value in remote.items():
        if value is not None:
            raise click.UsageError(f"--{flag} only applies together with --ssh.")
    # The directory is only ours to check when the server runs here.
    dir_path = reports_directory(directory)
    os.environ["QIBOCAMP_ORIGINAL_REPORT_DIR"] = str(dir_path)
    os.environ["QIBOCAMP_DIR"] = str(dir_path)
    set_report_root(dir_path, is_original=True)
    name_server(name or f"local-{dir_path.name}")

    if auth:
        os.environ["QIBOCAMP_AUTH_ENABLED"] = "1"
        from qibocamp import auth as auth_mod

        auth_mod.set_auth_enabled(True)
        initial_token = auth_mod.create_initial_admin_invite_if_needed()
    else:
        initial_token = None

    url = f"http://{host}:{port}"
    if register:
        config.add_server(
            url=url,
            name=os.environ["QIBOCAMP_SERVER_NAME"],
            description=f"Serving reports from {dir_path}",
            avatar="quantum-ring",
        )

    console = Console()
    auth_lines = ""
    if auth:
        auth_lines = (
            f"\n[bold cyan]🔒 Auth & Roles       :[/bold cyan] [green]Enabled[/green]"
        )
        if initial_token:
            auth_lines += (
                f"\n[bold yellow]🔑 Admin Invite Link  :[/bold yellow] "
                f"{config.invite_link(url, initial_token)}"
            )

    console.print(
        Panel.fit(
            f"[bold cyan]📁 Reports Directory :[/bold cyan] {dir_path}\n"
            f"[bold cyan]⚙️  FastAPI Server   :[/bold cyan] {url}\n"
            f"[bold cyan]📖 REST API Docs     :[/bold cyan] {url}/api/docs/swagger\n"
            f"[bold cyan]🖥️  Dashboard        :[/bold cyan] {config.DASHBOARD_URL}"
            f"{auth_lines}\n\n"
            "[dim]The server has no page of its own: add its address in the Qibocamp Dashboard,\n"
            "or in the Qibocamp desktop application. Press Ctrl+C to stop the server.[/dim]",
            title="[bold magenta]⚛️  Qibocamp Server[/bold magenta]",
            border_style="magenta",
        )
    )

    uvicorn.run(
        "qibocamp.web.api:app",
        host=host,
        port=port,
        log_config=get_uvicorn_log_config(),
    )


@report.command(name="targets")
@click.argument("directory", required=False, type=click.Path(file_okay=False))
def report_targets(directory: str | None):
    """Gather each report's targets from its protocols, into its meta.

    Every protocol's action.yml under DIRECTORY — the shared reports
    directory, left out — names the qubits, or pairs, it was run on; their
    union goes into the report's meta.json as `targets-inferred`, next to
    the session's own `targets`, and the dashboard shows it first.
    """
    from qibocamp.reports.actions import infer_targets

    root = reports_directory(directory)
    counts = infer_targets(root)
    click.echo(
        f"{counts['reports']} report(s) looked at, {counts['updated']} updated, "
        f"{counts['without_targets']} without protocol targets."
    )


@server.command(name="upgrade")
@click.option(
    "--ssh",
    required=True,
    help="SSH options followed by a host or user@host destination: the machine whose environment is updated.",
)
@click.option("-w", "--workdir", help="Working directory on the remote host.")
@click.option(
    "--venv",
    help="Environment name or path on the remote host (default: qibocamp). It is created when missing.",
)
@click.option(
    "--install",
    help="Packages put back by force in the remote environment (default: this qibocamp, from GitHub: the commit it was installed from, or main).",
)
@click.option(
    "--timeout",
    type=click.FloatRange(min=0, min_open=True),
    help="Seconds to wait for the installation (default: 300).",
)
def server_upgrade(ssh: str, **remote):
    """Bring a remote server's qibocamp up to date, without starting it.

    The --install packages are put back by force in the --venv environment on
    the --ssh host — the packages themselves first, then their dependencies —
    which otherwise keeps what it holds for good, since it is only filled when
    it has no qibocamp. Nothing is served; start the server as usual afterwards.
    """
    from qibocamp.remote.server import load_options, upgrade

    upgrade(load_options({"ssh": ssh, "upgrade": True, "registered": False, **remote}))


@report.command(name="upload")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.argument("server")
@click.option("--token", envvar="QIBOCAMP_TOKEN", help="Sign in with this token (also QIBOCAMP_TOKEN).")
@click.option("-u", "--username", help="Sign in as this user; the password is asked, or taken from QIBOCAMP_PASSWORD.")
@click.option("--json", "output_json", is_flag=True, help="Answer with the server's reply, as JSON.")
def report_upload(path: Path, server: str, token: str | None, username: str | None, output_json: bool):
    """Upload the report at PATH to SERVER, as the dashboard does.

    PATH is a report folder, sent under its own name, or the zip of one, as a
    download gives it. A server with authentication asks to sign in: with
    --token, or as --username. The report lands at the top of the server's
    reports; one of the same name there is never overwritten.
    """
    from qibocamp.reports.send import SendError, send_report

    password = None
    if username and not token:
        password = os.environ.get("QIBOCAMP_PASSWORD") or click.prompt(
            f"Password for {username}", hide_input=True
        )
    try:
        sent = send_report(path, server, token=token, username=username, password=password)
    except SendError as error:
        raise click.ClickException(str(error)) from error
    if output_json:
        click.echo(json.dumps({"id": sent.id, "name": sent.name}))
    else:
        click.echo(f"Uploaded to {server} as {sent.name}.")


@report.command(name="dev")
@click.argument("directory", required=False, type=click.Path(file_okay=False))
@click.option("--host", default="localhost", help="Host address to bind to.")
@click.option("--port", default=8000, type=int, help="Backend port to listen on.")
@click.option(
    "--frontend-port", default=5173, type=int, help="Frontend Vite dev server port."
)
@click.option(
    "--reload/--no-reload",
    default=True,
    help="Enable auto-reload for backend server.",
)
@click.option(
    "--auth/--no-auth",
    default=False,
    help="Enable user authentication and role management.",
)
@registration_options
def dev(
    directory: str | None,
    host: str,
    port: int,
    frontend_port: int,
    reload: bool = True,
    auth: bool = False,
    name: str | None = None,
    register: bool = True,
):
    """Serve Qibocal reports in developer mode with live Vite HMR.

    DIRECTORY defaults to the shared reports directory, as for `report server`.
    """
    dir_path = reports_directory(directory)
    os.environ["QIBOCAMP_ORIGINAL_REPORT_DIR"] = str(dir_path)
    os.environ["QIBOCAMP_DIR"] = str(dir_path)
    set_report_root(dir_path, is_original=True)
    name_server(name or f"local-{dir_path.name}-dev")

    if auth:
        os.environ["QIBOCAMP_AUTH_ENABLED"] = "1"
        from qibocamp import auth as auth_mod

        auth_mod.set_auth_enabled(True)
        initial_token = auth_mod.create_initial_admin_invite_if_needed()
    else:
        initial_token = None

    # Locate frontend directory
    frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
    if not frontend_dir.is_dir() or not (frontend_dir / "package.json").is_file():
        frontend_dir = Path.cwd() / "frontend"

    if not frontend_dir.is_dir() or not (frontend_dir / "package.json").is_file():
        raise click.ClickException(
            f"Frontend directory not found at '{frontend_dir}'. "
            "Developer mode requires the frontend source tree."
        )

    # Determine package manager command
    if shutil.which("pnpm"):
        vite_cmd = ["pnpm", "run", "dev", "--port", str(frontend_port), "--host", host]
    elif shutil.which("npm"):
        vite_cmd = [
            "npm",
            "run",
            "dev",
            "--",
            "--port",
            str(frontend_port),
            "--host",
            host,
        ]
    elif shutil.which("npx"):
        vite_cmd = ["npx", "vite", "--port", str(frontend_port), "--host", host]
    else:
        raise click.ClickException(
            "Could not find 'pnpm' or 'npm' in PATH. "
            "Developer mode requires Node.js and pnpm or npm."
        )

    backend_url = f"http://{host}:{port}"
    frontend_url = f"http://{host}:{frontend_port}"

    if register:
        config.add_server(
            url=backend_url,
            name=os.environ["QIBOCAMP_SERVER_NAME"],
            description=f"Serving reports (dev mode) from {dir_path}",
            avatar="quantum-ring",
        )

    console = Console()
    dev_title = "[bold magenta]⚛️  Qibocamp Server (Developer Mode)[/bold magenta]"
    auth_lines = ""
    if auth:
        auth_lines = (
            f"\n[bold cyan]🔒 Auth & Roles       :[/bold cyan] [green]Enabled[/green]"
        )
        if initial_token:
            auth_lines += (
                f"\n[bold yellow]🔑 Admin Invite Link  :[/bold yellow] "
                f"{config.invite_link(backend_url, initial_token, dashboard=frontend_url)}"
            )

    panel_content = (
        f"[bold cyan]📁 Reports Directory :[/bold cyan] {dir_path}\n"
        f"[bold cyan]⚡ Vite Dev Frontend :[/bold cyan] {frontend_url}\n"
        f"[bold cyan]⚙️  Backend API       :[/bold cyan] {backend_url}\n"
        f"[bold cyan]📖 REST API Docs     :[/bold cyan] "
        f"{backend_url}/api/docs/swagger"
        f"{auth_lines}\n\n"
        "[dim]Starting Vite development server with Hot Module Replacement...[/dim]\n"
        "[dim]Press Ctrl+C to stop both backend and frontend.[/dim]"
    )
    console.print(Panel.fit(panel_content, title=dev_title, border_style="magenta"))

    env = os.environ.copy()
    env["BACKEND_URL"] = backend_url

    vite_proc = subprocess.Popen(vite_cmd, cwd=frontend_dir, env=env)

    try:
        uvicorn.run(
            "qibocamp.web.api:app",
            host=host,
            port=port,
            reload=reload,
            log_config=get_uvicorn_log_config(),
        )
    finally:
        if vite_proc.poll() is None:
            console.print("\n[dim]Stopping Vite development server...[/dim]")
            vite_proc.terminate()
            try:
                vite_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                vite_proc.kill()


# --- Administrator and Invite Link Management ---
@report.group(name="admin")
def admin_group():
    """Manage server administrators and invitation links."""


@admin_group.command(name="list")
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    default=False,
    help="List all registered users, including non-administrators.",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    default=False,
    help="Output administrators in JSON format.",
)
@click.option("--ssh", help="SSH destination of the server's machine: list its administrators there.")
@click.option("--venv", help="The server's environment on that machine (default: qibocamp).")
def admin_list(show_all: bool, output_json: bool, ssh: str | None, venv: str | None):
    """List registered server administrators."""
    from qibocamp import auth as auth_mod

    if ssh:
        # The users are the server's, kept on its machine: asked there.
        from qibocamp.remote.server import run_remote_cli

        click.echo(run_remote_cli(ssh, venv, ["report", "admin", "list", *(["--all"] if show_all else []), *(["--json"] if output_json else [])]), nl=False)
        return

    users = auth_mod.list_users() if show_all else auth_mod.list_admins()
    if output_json:
        click.echo(json.dumps(users, indent=2))
        return

    console = Console()
    if not users:
        label = "users" if show_all else "administrators"
        console.print(
            f"[yellow]No registered {label} found in authentication database.[/yellow]"
        )
        console.print(
            "[dim]Hint: Start the server with --auth to generate an initial admin invite link.[/dim]"
        )
        return

    title = "Registered Users" if show_all else "Registered Administrators"
    table = Table(title=f"⚛️  {title}", border_style="magenta")
    table.add_column("#", justify="right", style="dim", width=4)
    table.add_column("Username", style="bold cyan")
    table.add_column("Role", style="bold")
    table.add_column("User ID", style="dim")
    table.add_column("Created At", style="green")

    for idx, u in enumerate(users, start=1):
        role = u.get("role", "")
        role_styled = (
            f"[bold magenta]{role}[/bold magenta]"
            if role == "admin"
            else f"[bold blue]{role}[/bold blue]"
            if role == "editor"
            else f"[dim]{role}[/dim]"
        )
        created = u.get("created_at", "")
        if created and "T" in created:
            created = created.replace("T", " ")[:19]
        table.add_row(
            str(idx), u.get("username", ""), role_styled, u.get("id", ""), created
        )

    console.print(table)


@admin_group.command(name="invite")
@click.argument("username", required=False, default=None)
@click.option(
    "-u",
    "--username",
    "opt_username",
    default=None,
    help="Administrator username for which to regenerate the invite link.",
)
@click.option(
    "--server-url",
    default=None,
    help="Explicit base URL of the report server (e.g. http://localhost:8000).",
)
@click.option(
    "--host",
    default=None,
    help="Server host to use when constructing the invite link.",
)
@click.option(
    "--port",
    default=None,
    type=int,
    help="Server port to use when constructing the invite link.",
)
@click.option(
    "-e",
    "--expires-in-hours",
    default=168,
    type=int,
    help="Invite link validity in hours (default: 168 = 7 days, 0 for never expires).",
)
@click.option(
    "--interactive/--no-interactive",
    default=None,
    help="Force interactive or non-interactive administrator selection.",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    default=False,
    help="Output generated invitation details in JSON format.",
)
@click.option("--ssh", help="SSH destination of the server's machine: the invitation is made there, in its users.")
@click.option("--venv", help="The server's environment on that machine (default: qibocamp).")
def admin_invite(
    username: str | None,
    opt_username: str | None,
    server_url: str | None,
    host: str | None,
    port: int | None,
    expires_in_hours: int,
    interactive: bool | None,
    output_json: bool,
    ssh: str | None,
    venv: str | None,
):
    """Regenerate an invitation link for a server administrator.

    An invitation is valid only in the users of the server's own machine:
    with --ssh, it is made there.
    """
    if ssh:
        from qibocamp.remote.server import run_remote_cli

        arguments = ["report", "admin", "invite"]
        if username or opt_username:
            arguments.append(username or opt_username)
        if server_url:
            arguments += ["--server-url", server_url]
        if host:
            arguments += ["--host", host]
        if port:
            arguments += ["--port", str(port)]
        arguments += ["--expires-in-hours", str(expires_in_hours), "--no-interactive"]
        if output_json:
            arguments.append("--json")
        click.echo(run_remote_cli(ssh, venv, arguments), nl=False)
        return
    from qibocamp import auth as auth_mod
    from qibocamp import config

    console = Console()
    target_username = (username or opt_username or "").strip()
    admins = auth_mod.list_admins()
    all_users = auth_mod.list_users()

    is_tty = sys.stdin.isatty() if hasattr(sys.stdin, "isatty") else False
    should_interact = (interactive is True) or (
        interactive is None and is_tty and not target_username
    )

    if not target_username:
        if not all_users and not admins:
            # Fresh instance without any users registered yet
            if not output_json:
                console.print(
                    "[yellow]No registered administrators found in the database.[/yellow]\n"
                    "[dim]Generating an initial administrator invitation token...[/dim]"
                )
            chosen_username = None
        elif should_interact:
            if not admins:
                existing_names = ", ".join(u["username"] for u in all_users) or "None"
                raise click.ClickException(
                    f"No administrators exist in the database. Existing users are: {existing_names}"
                )
            if len(admins) == 1:
                chosen_username = admins[0]["username"]
                if not output_json:
                    console.print(
                        f"[cyan]Found 1 administrator:[/cyan] [bold]{chosen_username}[/bold]"
                    )
            else:
                if not output_json:
                    console.print("[bold cyan]Available Administrators:[/bold cyan]")
                    for idx, a in enumerate(admins, start=1):
                        console.print(
                            f"  [bold magenta][{idx}][/bold magenta] {a['username']} [dim](ID: {a['id']})[/dim]"
                        )
                choice = click.prompt(
                    f"Select administrator [1-{len(admins)}] or enter username",
                    default="1",
                ).strip()
                if choice.isdigit() and 1 <= int(choice) <= len(admins):
                    chosen_username = admins[int(choice) - 1]["username"]
                else:
                    match = next(
                        (
                            a["username"]
                            for a in admins
                            if a["username"].lower() == choice.lower()
                        ),
                        None,
                    )
                    if match:
                        chosen_username = match
                    else:
                        raise click.ClickException(
                            f"Invalid administrator selection '{choice}'."
                        )
        else:
            # Non-interactive and no username passed
            if len(admins) == 1:
                chosen_username = admins[0]["username"]
            elif not admins and not all_users:
                chosen_username = None
            else:
                available = ", ".join(a["username"] for a in admins)
                raise click.ClickException(
                    f"Missing administrator username. Please specify a username or run interactively.\n"
                    f"Available administrators: {available}"
                )
    else:
        # target_username was explicitly given
        user_match = next(
            (
                u
                for u in all_users
                if u["username"].lower() == target_username.lower()
            ),
            None,
        )
        if not user_match:
            available = ", ".join(a["username"] for a in admins) or "None"
            raise click.ClickException(
                f"Administrator user '{target_username}' not found. Available administrators: {available}"
            )
        if user_match.get("role") != "admin":
            raise click.ClickException(
                f"User '{user_match['username']}' has role '{user_match['role']}', not 'admin'."
            )
        chosen_username = user_match["username"]

    # Generate or regenerate the invite token
    try:
        invite_data = auth_mod.regenerate_admin_invite(
            username=chosen_username,
            expires_in_hours=expires_in_hours if expires_in_hours > 0 else None,
        )
    except ValueError as err:
        raise click.ClickException(str(err))

    # Resolve server base URL
    if server_url:
        base_url = server_url.rstrip("/")
    else:
        if host is not None or port is not None:
            h = host or "localhost"
            p = port or 8000
            base_url = f"http://{h}:{p}"
        else:
            servers = config.load_servers()
            if servers and servers[0].get("url"):
                base_url = servers[0]["url"].rstrip("/")
            else:
                base_url = "http://localhost:8000"

    invite_url = config.invite_link(base_url, invite_data["token"])
    expires_str = invite_data.get("expires_at") or "Never"

    if output_json:
        result = {
            "username": chosen_username,
            "role": invite_data["role"],
            "token": invite_data["token"],
            "invite_url": invite_url,
            "expires_at": invite_data.get("expires_at"),
            "server_url": base_url,
        }
        click.echo(json.dumps(result, indent=2))
        return

    admin_display = (
        f"[bold green]{chosen_username}[/bold green]"
        if chosen_username
        else "[bold yellow]Initial Admin (Unregistered)[/bold yellow]"
    )
    panel_content = (
        f"[bold cyan]👤 Administrator  :[/bold cyan] {admin_display}\n"
        f"[bold cyan]🔒 Assigned Role  :[/bold cyan] [bold magenta]admin[/bold magenta]\n"
        f"[bold cyan]⏰ Validity       :[/bold cyan] {expires_str}\n"
        f"[bold cyan]🌐 Server Endpoint:[/bold cyan] {base_url}\n\n"
        f"[bold yellow]🔑 Invite Link    :[/bold yellow]\n"
        f"[underline bold]{invite_url}[/underline bold]\n\n"
        "[dim]To restore access, open the invite link in a browser: it opens the\n"
        "dashboard on this server.[/dim]"
    )
    console.print(
        Panel.fit(
            panel_content,
            title="[bold magenta]⚛️  Qibocamp Admin Invite Link[/bold magenta]",
            border_style="magenta",
        )
    )


@report.command(name="invite")
@click.argument("username", required=False, default=None)
@click.option(
    "-u",
    "--username",
    "opt_username",
    default=None,
    help="Administrator username for which to regenerate the invite link.",
)
@click.option(
    "--server-url",
    default=None,
    help="Explicit base URL of the report server (e.g. http://localhost:8000).",
)
@click.option(
    "--host",
    default=None,
    help="Server host to use when constructing the invite link.",
)
@click.option(
    "--port",
    default=None,
    type=int,
    help="Server port to use when constructing the invite link.",
)
@click.option(
    "-e",
    "--expires-in-hours",
    default=168,
    type=int,
    help="Invite link validity in hours (default: 168 = 7 days, 0 for never expires).",
)
@click.option(
    "--interactive/--no-interactive",
    default=None,
    help="Force interactive or non-interactive administrator selection.",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    default=False,
    help="Output generated invitation details in JSON format.",
)
@click.pass_context
def invite_shortcut(
    ctx,
    username: str | None,
    opt_username: str | None,
    server_url: str | None,
    host: str | None,
    port: int | None,
    expires_in_hours: int,
    interactive: bool | None,
    output_json: bool,
):
    """Regenerate an invitation link for a server administrator."""
    ctx.forward(admin_invite)


