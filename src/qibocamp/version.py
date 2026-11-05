"""What a server is: its version, the API it speaks, and what it can do.

The dashboard and the launcher ask `GET /api/version` before relying on a
server, rather than probing for endpoints: `api_version` counts the API's
breaking changes, and `features` names what was added since without one.
"""

import json
import re
from importlib.metadata import PackageNotFoundError, distribution

from qibocamp import __version__

# Raised on every breaking change of the REST or GraphQL API, never otherwise.
API_VERSION = 1

# What a client may rely on, beyond the API version, by name.
FEATURES = ("graphql", "uploads", "archives", "notes", "live", "parameters", "progressive")

# A GitHub archive of a commit, as the launcher installs it.
ARCHIVE_COMMIT = re.compile(r"/archive/([0-9a-f]{40})\.(?:tar\.gz|zip)$")


def installed_commit() -> str | None:
    """The commit this installation was made from, when pip recorded one.

    A Git URL leaves it in `vcs_info`; the archive of a commit, in its URL.
    A release, or a local checkout, has none.
    """
    try:
        text = distribution("qibocamp").read_text("direct_url.json")
    except PackageNotFoundError:
        return None
    try:
        origin = json.loads(text or "{}")
    except ValueError:
        return None
    commit = (origin.get("vcs_info") or {}).get("commit_id")
    if isinstance(commit, str) and commit:
        return commit
    found = ARCHIVE_COMMIT.search(origin.get("url") or "")
    return found.group(1) if found else None


REPOSITORY = "https://github.com/qiboteam/qibocamp"


def default_install() -> str:
    """What a server on another machine installs, unless told: this very qibocamp.

    From GitHub, until the package is on PyPI: the commit this installation
    was made from, when known, and `main` otherwise.
    """
    return f"git+{REPOSITORY}@{installed_commit() or 'main'}"


def version_info() -> dict:
    return {
        "version": __version__,
        "api_version": API_VERSION,
        "commit": installed_commit(),
        "features": list(FEATURES),
    }
