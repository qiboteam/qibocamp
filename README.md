<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/qibocamp_logo_light.svg" />
  <img src="docs/assets/qibocamp_logo_dark.svg" alt="Qibocamp" width="300" style="margin: 1.2rem 0;" />
</picture>

**Qibocamp, the desktop application for [Qibocal](https://github.com/qiboteam/qibocal) calibrations: notebooks, report servers, remote execution and agents, wherever they run, and their reports, in one place.**

[![Python](https://img.shields.io/badge/python-3.10+-purple.svg)](https://www.python.org/)
[![Tauri](https://img.shields.io/badge/app-Tauri_2-24c8db.svg)](https://tauri.app/)
[![Vue 3](https://img.shields.io/badge/dashboard-Vue_3-42b883.svg)](https://vuejs.org/)
[![FastAPI](https://img.shields.io/badge/server-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

</div>

**Qibocamp** gathers the whole calibration workflow around
[Qibocal](https://github.com/qiboteam/qibocal), the calibration library: it
opens notebook sessions, starts and updates report servers, dispatches
acquisitions and calibration agents — on this machine, over SSH, or through
SLURM — and reads the reports they produce in the dashboard it embeds.
Qibocamp builds on top of Qibocal, but is a separate project: Qibocal runs
the calibrations, Qibocamp organizes them and their reports. It comes in three
components: the **Qibocamp** desktop application itself, the **Qibocamp
Dashboard**, also deployed as a web page, and the **Qibocamp Server**, deployed
wherever the reports are.

## ⚡ The application

- **Notebooks**: JupyterLab or Marimo sessions, here, on an access node or on a computing node through SLURM, each with a kernel environment of its own, its requirements kept and regenerated on demand.
- **Remote execution**: the connections qibocal-widgets' `Remote` plugin dispatches a notebook's acquisitions with, managed alongside the sessions.
- **Servers**: report servers started here or on another machine, tunnelled back, updated in place; their users and invitations administered from the application.
- **Agents**: the configurations of [qibocal-agent](https://github.com/qiboteam/qibocal-mcp-servers), for an assistant over MCP or for autonomous runs, here or pushed to an access node.
- **Running**: every process it started, its output, and the SLURM queues it waits in.
- **Reports**: the dashboard, embedded, connected to every server at once.
- **Configuration**: the files it shares with the command line, edited in place; a tray icon and keyboard shortcuts throughout.

Installers for macOS, Linux and Windows are attached to every
[release](https://github.com/qiboteam/qibocamp/releases). They are not
signed yet: see [Installation](docs/user-guide/launcher/installation.md) for
opening them the first time. The application installs the `qibocamp` command
line it drives by itself, the first time it is needed; a Python 3.10 or newer
is all it asks of the machine.

With Nix, on Linux and Apple silicon, the application comes with its command
line:

```bash
nix run github:qiboteam/qibocamp
```

See [With Nix](docs/user-guide/launcher/installation.md#with-nix).

## 📊 The Qibocamp Dashboard

A static single-page application, from [`frontend/`](frontend/), deployed at
[qibo.science/qibocamp](https://qibo.science/qibocamp) and embedded
in the application. It connects to any number of report servers from the
browser:

- **Interactive reports**: protocol plots and fit tables, platform parameters before and after, the parameters each protocol ran with, notes, and downloads.
- **Live reports**: a running calibration followed as it goes, only new or changed protocols regenerated.
- **Search, filters and statistics**: runs by title, platform, author, protocol, target, tag or date, and the activity behind them.
- **Batch actions, archives and uploads**: tags, authors and deletion across runs; archives; reports sent from one server to another.
- **Administration**: users, roles and invitations of a server with authentication.

Any static host serves it: `cd frontend && pnpm run build`, and a server lets
it call with `QIBOCAMP_ALLOWED_ORIGINS`.

## 🖥️ The Qibocamp Server

A headless server reads a folder of Qibocal runs and answers the dashboard's
REST and GraphQL API. Install it from GitHub — it is not on PyPI yet — and point
it at the runs:

```bash
pip install git+https://github.com/qiboteam/qibocamp
qibocamp report server ./runs --port 8000 --auth
```

With `--auth` it prints the invitation of its first administrator. Without a
folder, the shared `~/.local/share/qibocamp/reports` is served. When the runs
live on another machine, `--ssh` starts the server there, in an environment of
its own with this very Qibocamp installed, and tunnels it back:

```bash
qibocamp report server /shared/calibration --ssh myuser@login
```

The same command line opens notebooks — `qibocamp notebook connect --ssh
myuser@login -q mychip` — and manages everything the application does: see
**`man qibocamp`** ([source](data/share/man/man1/qibocamp.1)) and the
[CLI reference](docs/reference/cli.md). Every server documents its API at
`/api/docs/swagger`, and says what it is at `/api/version`.

## 🛠️ Development

```bash
unzip -q sample_data/test-data.zip -d sample_data   # the sample reports travel zipped
qibocamp report dev ./sample_data   # server with auto-reload, dashboard with HMR on :5173
cd launcher && pnpm install && pnpm run icons && pnpm run app
```

Tests: `pytest` for the server, `node --test` in `frontend/` and `launcher/`.
Releases are tagged with the version in `pyproject.toml` (`v0.1.0a1`); the
workflows build the installers and deploy the dashboard. See the
[development workflow](docs/developer/workflow.md).

## 📖 Documentation

The [user guide](docs/user-guide/index.md), the
[administrator's guide](docs/admin-guide/index.md) and the references, also
under **Documentation** in the dashboard, or at
[qibo.science/qibocamp/#/docs](https://qibo.science/qibocamp/#/docs).

## 📄 License

Licensed under the [Apache License, Version 2.0](LICENSE).
