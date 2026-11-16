"""Documentation catalog and markdown content resolver."""

import sysconfig
from pathlib import Path

from fastapi import HTTPException

DOCS_NAVIGATION = [
    {
        "section": "Overview",
        "path": "index",
        "items": [
            {
                "id": "index",
                "path": "index",
                "title": "Overview & Ecosystem",
                "description": (
                    "Introduction to Qibo ecosystem, projects, and Qibocamp"
                    " architecture"
                ),
            }
        ],
    },
    {
        "section": "User Guide",
        "path": "user-guide",
        "items": [
            {
                "id": "user-guide/quickstart",
                "path": "user-guide/quickstart",
                "title": "Getting Started & Quickstart",
                "description": (
                    "Installation, CLI commands, and report directory structure"
                ),
            },
            {
                "id": "user-guide/dashboard",
                "path": "user-guide/dashboard",
                "title": "Dashboard & Server Management",
                "description": (
                    "Managing servers, search, smart facets, dual views, and bulk"
                    " actions"
                ),
            },
            {
                "id": "user-guide/reports",
                "path": "user-guide/reports",
                "title": "Reports, Protocols & Exports",
                "description": (
                    "Interactive charts, protocol timings, downloads, and PDF printing"
                ),
            },
        ],
    },
    {
        "section": "Desktop Launcher",
        "path": "user-guide/launcher",
        "items": [
            {
                "id": "user-guide/launcher/installation",
                "path": "user-guide/launcher/installation",
                "title": "Installation",
                "description": "Installers for each platform, opening an unsigned build, and the Qibocamp Server the launcher drives",
            },
            {
                "id": "user-guide/launcher/sections",
                "path": "user-guide/launcher/sections",
                "title": "Sections & Windows",
                "description": "The sections, several windows, running in the background, and zoom",
            },
            {
                "id": "user-guide/launcher/keyboard",
                "path": "user-guide/launcher/keyboard",
                "title": "Keyboard Shortcuts",
                "description": "GitHub-style keys, the menu shortcuts, and the keys of each page",
            },
            {
                "id": "user-guide/launcher/qibocamp",
                "path": "user-guide/launcher/qibocamp",
                "title": "Installing Qibocamp",
                "description": "The launcher's own environment, its version, and other environments",
            },
            {
                "id": "user-guide/launcher/servers",
                "path": "user-guide/launcher/servers",
                "title": "Report Servers",
                "description": "Starting and registering servers, here or over SSH, and handling them while they run",
            },
            {
                "id": "user-guide/launcher/notebooks",
                "path": "user-guide/launcher/notebooks",
                "title": "Notebooks & Queues",
                "description": "JupyterLab and Marimo sessions, locally, over SSH or through SLURM, and the queues",
            },
            {
                "id": "user-guide/launcher/remote",
                "path": "user-guide/launcher/remote",
                "title": "Remote Execution",
                "description": "Connections for qibocal-widgets' remote executor, and their reports",
            },
            {
                "id": "user-guide/launcher/agents",
                "path": "user-guide/launcher/agents",
                "title": "Agents",
                "description": "Configurations of the Qibocal MCP servers, here and on access nodes, and autonomous runs",
            },
            {
                "id": "user-guide/launcher/machines",
                "path": "user-guide/launcher/machines",
                "title": "Machines",
                "description": "The machines reached over SSH, named once for every page, and what each one serves",
            },
            {
                "id": "user-guide/launcher/configuration",
                "path": "user-guide/launcher/configuration",
                "title": "Configuration",
                "description": "The files shared with the command line, their formats, and the web client",
            },
        ],
    },
    {
        "section": "Admin Guide",
        "path": "admin-guide",
        "items": [
            {
                "id": "admin-guide/index",
                "path": "admin-guide",
                "title": "Server Administration & Security",
                "description": (
                    "Enabling authentication (--auth), initial bootstrap, and"
                    " server administration"
                ),
            },
            {
                "id": "admin-guide/roles",
                "path": "admin-guide/roles",
                "title": "Roles & Permissions Matrix",
                "description": (
                    "Viewer, Editor, and Admin capabilities, restrictions, and"
                    " enforcement"
                ),
            },
            {
                "id": "admin-guide/invitations",
                "path": "admin-guide/invitations",
                "title": "Invitations & Team Onboarding",
                "description": (
                    "Generating tokens, expiration policies, paste-box flows, and"
                    " revocation"
                ),
            },
        ],
    },
    {
        "section": "Developer & Architecture",
        "path": "developer",
        "items": [
            {
                "id": "developer/architecture",
                "path": "developer/architecture",
                "title": "System Architecture",
                "description": (
                    "FastAPI backend, Vue 3 SPA frontend, and execution modes"
                ),
            },
            {
                "id": "developer/workflow",
                "path": "developer/workflow",
                "title": "Development Workflow",
                "description": (
                    "devenv environment, live Vite HMR, testing, and packaging"
                ),
            },
            {
                "id": "developer/design-system",
                "path": "developer/design-system",
                "title": "Design System & Aesthetics",
                "description": (
                    "BackMarket design palette, typography, and print media CSS"
                ),
            },
            {
                "id": "developer/nix",
                "path": "developer/nix",
                "title": "The Nix Package",
                "description": "The flake, its hashes, its CI, and the binary cache",
            },
        ],
    },
    {
        "section": "Reference",
        "path": "reference",
        "items": [
            {
                "id": "reference/cli",
                "path": "reference/cli",
                "title": "CLI Reference",
                "description": (
                    "Complete command line options and environment variables"
                ),
            },
            {
                "id": "reference/api",
                "path": "reference/api",
                "title": "REST API & WebSockets",
                "description": (
                    "REST endpoints specification, schemas, and WebSocket events"
                ),
            },
        ],
    },
]


def documented_pages() -> set[str]:
    """The pages of the documentation, as the navigation names them, and its index."""
    pages = {"index"}
    entries = list(DOCS_NAVIGATION)
    while entries:
        entry = entries.pop()
        if entry.get("path"):
            pages.add(entry["path"].strip("/").removesuffix(".md"))
        entries.extend(entry.get("items", []))
    return pages


def resolve_docs_content(doc_name: str) -> str:
    """Serve plain markdown documentation content: a section, or one of its pages.

    Only a page of the documentation: a name climbing out of it, or hidden,
    names nothing.
    """
    clean_name = doc_name.strip("/").removesuffix(".md")
    # A section, by its name or by its index page.
    clean_name = clean_name.removesuffix("/index") or "index"
    # A page the navigation lists, or nothing: the roots below are folders
    # shared with other packages — site-packages itself among them.
    if clean_name not in documented_pages():
        raise HTTPException(status_code=404, detail=f"Documentation '{doc_name}' not found")

    roots = [
        Path(__file__).resolve().parents[2] / "docs",
        Path.cwd() / "docs",
        Path(sysconfig.get_path("purelib")) / "docs",
        Path(sysconfig.get_path("purelib")),
        Path(sysconfig.get_path("data")) / "docs",
        Path(sysconfig.get_path("data")),
    ]

    targets = [
        f"{clean_name}/index.md",
        f"{clean_name}.md",
    ]

    for root in roots:
        for target in targets:
            p = (root / target).resolve()
            if p.is_file() and p.is_relative_to(root.resolve()):
                return p.read_text(encoding="utf-8")

    # Fallback: if doc_name matches a section path, generate minimal Table of Contents
    for sec in DOCS_NAVIGATION:
        if sec.get("path") == clean_name and sec.get("items"):
            lines = [
                f"# {sec['section']}",
                "",
            ]
            for item in sec["items"]:
                lines.append(f"- [{item['title']}]({item['path']})")
            lines.append("")
            return "\n".join(lines)

    raise HTTPException(status_code=404, detail=f"Documentation '{doc_name}' not found")
