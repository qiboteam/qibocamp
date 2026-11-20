# Desktop Launcher: Agents

**Agents** manages the configurations of `qibocal-agent`, from the [Qibocal MCP servers](https://github.com/qiboteam/qibocal-mcp-servers) — the MCP server an assistant such as Copilot, Claude or Cursor registers, `qibocal-agent mcp`, and the autonomous calibration agent, `qibocal-agent run` — and dispatches autonomous runs. The page lists the configurations, with **Refresh**, and **New configuration** for the form, then the **Saved prompts**; **Setup**, below **New configuration**, opens what is not one configuration's own on a page of its own, **Agents - Setup**.

## How it fits together

Three machines may take part in a calibration: where the agent runs — an assistant, or an autonomous run — where the calibration server runs, and where the instruments are reached. A configuration says how they are arranged.

The **SSH destination** is where the calibration server starts, over SSH, from wherever the configuration is used: usually a cluster's access node. With a **SLURM partition**, the server dispatches each operation as one `srun` job on that partition, whose nodes reach the instruments, and holds no allocation in between:

```text
 this machine                      access node                     compute node
┌──────────────────────┐   ssh    ┌───────────────────────┐ srun  ┌──────────────────┐
│ assistant            │ ───────▶ │ calibration server    │ ────▶ │ one operation    │
│  └ qibocal-agent mcp │          │ (qibocal-mcp)         │       │ ── instruments   │
│ or autonomous run    │ ◀─────── │ reports, platform     │ ◀──── │                  │
└──────────────────────┘          └───────────────────────┘       └──────────────────┘
       kept for                        SSH destination              SLURM partition
```

Without a partition, the server runs the operations itself, so it must reach the instruments; without an SSH destination, the server starts where the agent runs. A lab workstation wired to the instruments needs neither:

```text
 this machine
┌─────────────────────────────────────────────────────────────────────┐
│ assistant ── qibocal-agent mcp ── calibration server ── instruments │
└─────────────────────────────────────────────────────────────────────┘
```

A configuration is **kept for** where its user is. **This machine**: an assistant here, or autonomous runs dispatched from here, read it from this machine's `agents.json`. **Another machine**, one of the application's [Machines](machines.md): an assistant running there — the access node itself, say — reads it from the `agents.json` there, which the launcher writes for it:

```text
 this machine                         another machine: the access node
┌──────────────────────┐   push    ┌──────────────────────────────┐ srun  ┌──────────────┐
│ Qibocamp application │ ────────▶ │ agents.json                  │ ────▶ │ compute node │
│  └ agent-hosts.json  │   (ssh)   │ assistant                    │       │ instruments  │
└──────────────────────┘           │  └ qibocal-agent mcp, server │       └──────────────┘
                                   └──────────────────────────────┘
```

In both cases an assistant registers `qibocal-agent mcp` **once**. It starts nothing by itself: its calibration tools appear when the assistant selects a configuration, which it does when asked to calibrate — listing the configurations, and asking which one when that is not said. Autonomous runs name their configuration, and start at once.

## Setup

**Agents - Setup**, from the **Setup** button of the page:

- **What it is**: the explanation above, in brief, and a link here.
- **qibocal-agent**: the one the page runs and registers, by absolute path, with its version. **Install** — **Update** once installed — puts the branch, tag or commit given (`main` by default, the repository's branches and tags offered) in `~/.cache/qibocal/agents/tools`, an environment of its own with Python 3.12 or 3.13, which uv provides when needed: `qibocamp agents install --ref REF`, its output in **Running**. **Choose environment…** takes another one instead, a checkout's `.venv` say, and **Detect** forgets it: the installed one is used, else the one on the `PATH`.
- **MCP registration**: the registration of `qibocal-agent mcp` for an assistant on this machine, with **Copy** — for Copilot, `~/.copilot/mcp-config.json`; other assistants take the same command and arguments. One serves every configuration, so there is nothing to register again when one is added. An assistant on another machine registers the same, with the path of the qibocal-agent installed there. The launcher writes no other tool's file.
- **Machines**: the machines a configuration is kept for, or reaches, are the application's, on [Machines](machines.md).

## Where a configuration is kept

- For **this machine**, it is an entry of `agents.json` — `~/.config/qibocal/agents.json`, or `$XDG_CONFIG_HOME/qibocal/agents.json`, or `$QIBOCAL_AGENTS_CONFIG_DIR/agents.json` — the file `qibocal-agent` itself reads, every configuration under `agents`. Each has a name: `default` is the one used when none is given, an ordinary entry otherwise, and `agent` and `agents` are reserved. The file is the tool's, and it refuses a key it does not know, so the launcher writes nothing else in it.
- For **another machine**, it is kept here, under that machine's name in `agent-hosts.json`, and written to the `agents.json` there — at the machine's destination — when saved, with `qibocamp agents push`: the entries it owns are merged into the remote `agents`, and every other one is left as it is. Only the configuration travels, never environments or keys. When the machine cannot be reached, the configuration is kept here anyway, and **Push** sends it again.

Two filters, on one line, select the list together — chips while they are few, dropdowns once the machines make them long: **Kept for** — this machine, or each other machine — and **Reaches** — **Directly**, a configuration without SSH destination, or the machine its SSH destination is. Each card says both: *this machine* or *for* a machine, and *direct* or *via* a machine.

## The settings

They are those of the [configuration guide](https://github.com/qiboteam/qibocal-mcp-servers/blob/main/docs/guides/configuration.rst): the **Platform**, the **Model** of autonomous runs, the **SSH destination** and the **SLURM partition** first; under **More options**, the model endpoint, the output and working directories, the platforms folder, the environment or a server interpreter, the dependencies, platform repositories and srun options — one per line — and the timeout, and the **Objective** of a run given none. Every configuration, `default` included, has its own environment, working folder and report folder, named after it. What the form does not show, a `report` section for instance, is kept as it is.

## Each configuration

- **Run**, for a configuration of this machine, dispatches an autonomous agent: `qibocal-agent run NAME "PROMPT"`, its output in **Running**. The prompt is typed in the dialog, picked among the **Saved prompts**, or left empty for the configuration's own objective. A run needs a model; its credentials, an `OPENAI_API_KEY` say, come from your shell profile, which the run is started through, since an application opened from the Dock inherits none: the launcher stores no key. The agent is **not a hardware safety layer**: give approved limits, a budget and when to stop.
- **Push**, for a configuration kept for another machine, writes it there again.
- **Reports** starts a report server on the execution host — the configuration's SSH destination, or the machine it is kept for — serving its output, `~/.local/share/qibocal/agents/runs/NAME` unless set otherwise, and opens it in the dashboard; it is started for a look, and nothing is registered.
- **Regenerate env**, after a confirmation, deletes the configuration's managed environment entirely — its **Environment**, or its name, under `~/.cache/qibocal/agents/envs` unless absolute — where its calibration server starts: its SSH destination, reached from the machine it is kept for when it is kept for one, or else that machine or this one. qibocal-agent creates it again, from the configuration alone, the next time it starts: `qibocamp agents rebuild NAME`. It is unavailable while a run of the configuration is in **Running**, and for a configuration with a server interpreter, which has no managed environment; stop any assistant using it first.
- **Edit**, its name and where it is kept included, **Duplicate**, and the bin.

## A run in Running

A run is a card of **Running**, as a notebook session is:

- **Its queue**: a configuration with a SLURM partition waits in it, one job per operation; the card shows the partition's queue while the agent runs — on the SSH destination, or here — and **Queues** lists it, with the partitions of this machine's other configurations.
- **Reports** starts the report server of its configuration — the same one **Reports** starts from the configuration, serving all its runs, not this one alone — or opens it in the dashboard when it runs. The two cards lead to each other: *reports running* on the agent's, *agent NAME* on the server's.
- **Stop both** stops the agent and that report server together; **Stop** stops the agent alone, and the server goes on serving its reports.

## Saved prompts

Prompts for autonomous runs, kept in `launcher.json`, apart from the configurations, and offered by **Run**. **+** saves one without running it; **Save prompt**, on an agent's run in **Running**, saves the prompt that run was given — or its configuration's objective, given none — under a name typed there.
