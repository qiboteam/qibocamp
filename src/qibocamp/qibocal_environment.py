"""Inspect and administer Qibocal in the running server's Python environment."""

import codecs
import json
from pathlib import Path
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as ReaderTimeoutError
from contextlib import contextmanager
from importlib import metadata, util
from urllib.parse import quote, unquote, urlparse

import httpx
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from qibocamp.logger import log_output
from qibocamp.models import QibocalOption, QibocalOptions, QibocalStatus

PYPI_URL = "https://pypi.org/pypi/qibocal/json"
GIT_URL = "git+https://github.com/qiboteam/qibocal.git"
GITHUB_REPO_URL = "https://api.github.com/repos/qiboteam/qibocal"
INSTALL_TIMEOUT = 600
GENERATION_TIMEOUT = 300
_INSTALL_LOCK = threading.Lock()
class _SharedLock:
    """Shared by plot generations, which run side by side, and exclusive for installing.

    An installation waiting keeps new generations out, so that a busy server
    still gets its new Qibocal once the generations under way end.
    """

    def __init__(self):
        self._condition = threading.Condition()
        self._shared = 0
        self._exclusive = False
        self._waiting = 0

    def acquire_shared(self, timeout: float) -> bool:
        with self._condition:
            return self._condition.wait_for(
                lambda: not self._exclusive and not self._waiting, timeout
            ) and self._take_shared()

    def _take_shared(self) -> bool:
        self._shared += 1
        return True

    def release_shared(self) -> None:
        with self._condition:
            self._shared -= 1
            self._condition.notify_all()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        """Exclusive, as `threading.Lock.acquire`."""
        with self._condition:
            self._waiting += 1
            try:
                acquired = self._condition.wait_for(
                    lambda: not self._exclusive and not self._shared,
                    0 if not blocking else None if timeout is None or timeout < 0 else timeout,
                )
                if acquired:
                    self._exclusive = True
                return acquired
            finally:
                self._waiting -= 1
                self._condition.notify_all()

    def release(self) -> None:
        with self._condition:
            self._exclusive = False
            self._condition.notify_all()

    def locked(self) -> bool:
        with self._condition:
            return self._exclusive or self._shared > 0


_ENVIRONMENT_LOCK = _SharedLock()
_INSTALL_STATE_LOCK = threading.Lock()
_INSTALL_CANCEL: threading.Event | None = None
INSTALL_OUTPUT_CHUNK_BYTES = 4096
INSTALL_FAILURE_TAIL = 2000


class EnvironmentOperationError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@contextmanager
def generation_environment():
    """Keep package files stable for the lifetime of a generation subprocess."""
    lock = _ENVIRONMENT_LOCK
    shared = hasattr(lock, "acquire_shared")
    timeout = INSTALL_TIMEOUT + GENERATION_TIMEOUT + 30
    if not (lock.acquire_shared(timeout) if shared else lock.acquire(timeout=timeout)):
        raise EnvironmentOperationError(
            409, "The Qibocal environment is busy; try plot generation again."
        )
    try:
        yield
    finally:
        lock.release_shared() if shared else lock.release()


def _read_status(expected_git_commit: str | None = None) -> QibocalStatus:
    try:
        distribution = metadata.distribution("qibocal")
        version = distribution.version or None
        direct_url = distribution.read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return QibocalStatus(installed=False)
    except (OSError, ValueError) as error:
        raise EnvironmentOperationError(
            500, f"Could not read Qibocal package metadata: {error}"
        ) from error

    source = "pypi"
    origin = None
    local_path = None
    if direct_url:
        try:
            origin = json.loads(direct_url)
            if (
                isinstance(origin, dict)
                and isinstance(origin.get("vcs_info"), dict)
                and origin["vcs_info"].get("vcs") == "git"
            ):
                source = "git"
            elif isinstance(origin, dict) and str(origin.get("url", "")).startswith("file://"):
                # Installed from a folder on this machine.
                source = "local"
                local_path = unquote(urlparse(origin["url"]).path)
            else:
                source = None
        except (ValueError, TypeError):
            source = None
    if expected_git_commit is not None:
        vcs_info = origin.get("vcs_info") if isinstance(origin, dict) else None
        if (
            source != "git"
            or not isinstance(vcs_info, dict)
            or vcs_info.get("commit_id") != expected_git_commit
        ):
            raise EnvironmentOperationError(
                500,
                "The installer finished, but Qibocal metadata does not match "
                "the requested Git branch commit.",
            )
    branch = None
    if source == "git":
        revision = origin["vcs_info"].get("requested_revision")
        if (
            isinstance(revision, str)
            and revision
            and not re.fullmatch(r"[0-9a-fA-F]{7,40}", revision)
        ):
            branch = revision.removeprefix("refs/heads/")
    return QibocalStatus(
        installed=True, version=version, source=source, git_branch=branch, local_path=local_path
    )


def _record_git_branch(branch: str) -> None:
    """Retain the selected branch when the installer target is a pinned commit."""
    try:
        distribution = metadata.distribution("qibocal")
        origin_file = next(
            (
                file
                for file in distribution.files or []
                if file.name == "direct_url.json"
                and file.parent.name.endswith(".dist-info")
            ),
            None,
        )
        if origin_file is None:
            raise ValueError("the package origin metadata file is missing")
        origin = json.loads(distribution.read_text("direct_url.json"))
        if not isinstance(origin, dict) or not isinstance(origin.get("vcs_info"), dict):
            raise TypeError("the package origin metadata is invalid")
        origin["vcs_info"]["requested_revision"] = f"refs/heads/{branch}"
        distribution.locate_file(origin_file).write_text(
            json.dumps(origin, indent=2) + "\n", encoding="utf-8"
        )
    except (metadata.PackageNotFoundError, OSError, ValueError, TypeError) as error:
        raise EnvironmentOperationError(
            500, f"Could not preserve the installed Qibocal Git branch: {error}"
        ) from error


def get_qibocal_status() -> QibocalStatus:
    """Read distribution metadata without importing Qibocal or its dependencies."""
    with generation_environment():
        return _read_status()


def _compatible_file(file: object, python_version: Version) -> bool:
    if not isinstance(file, dict) or file.get("yanked"):
        return False
    requires_python = file.get("requires_python")
    if not requires_python:
        return True
    try:
        return SpecifierSet(requires_python).contains(python_version, prereleases=True)
    except (InvalidSpecifier, TypeError):
        return False


def _pypi_options() -> tuple[list[QibocalOption], str | None]:
    try:
        response = httpx.get(PYPI_URL, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("releases"), dict):
            raise TypeError("PyPI returned an invalid release listing")
        python_version = Version(platform.python_version())
        versions: set[Version] = set()
        for version_text, files in data["releases"].items():
            try:
                version = Version(version_text)
            except (InvalidVersion, TypeError):
                continue
            if version.is_prerelease or version.is_devrelease or version.local:
                continue
            if isinstance(files, list) and any(
                _compatible_file(file, python_version) for file in files
            ):
                versions.add(version)
        ordered = sorted(versions, reverse=True)
        selected = ordered[:5]
        if ordered:
            latest = ordered[0]
            previous = next(
                (
                    version
                    for version in ordered
                    if (
                        version.major == latest.major - 1
                        if latest.major
                        else version.major == 0 and version.minor == latest.minor - 1
                    )
                ),
                None,
            )
            if previous is not None and previous not in selected:
                selected.append(previous)
        return [
            QibocalOption(
                id=f"pypi:{version}",
                label=f"Qibocal {version} (PyPI)",
                source="pypi",
                version=str(version),
            )
            for version in selected
        ], None
    except (httpx.HTTPError, ValueError, TypeError) as error:
        return [], f"Could not fetch Qibocal versions from PyPI: {error}"


def _github_branches() -> tuple[list[str], str | None, str | None]:
    try:
        response = httpx.get(GITHUB_REPO_URL, timeout=10.0)
        response.raise_for_status()
        repository = response.json()
        default = (
            repository.get("default_branch") if isinstance(repository, dict) else None
        )
        if not isinstance(default, str) or not default:
            raise TypeError("GitHub returned an invalid default branch")
        branches: set[str] = set()
        for page in range(1, 101):
            response = httpx.get(
                f"{GITHUB_REPO_URL}/branches",
                params={"per_page": 100, "page": page},
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list) or any(
                not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or not item["name"]
                for item in data
            ):
                raise TypeError("GitHub returned an invalid branch listing")
            branches.update(item["name"] for item in data)
            if len(data) < 100:
                break
        else:
            raise ValueError("GitHub's branch listing exceeded 100 pages")
        if default not in branches:
            raise ValueError(
                "GitHub's default branch is missing from the branch listing"
            )
        return [default, *sorted(branches - {default}, key=str.casefold)], default, None
    except (httpx.HTTPError, ValueError, TypeError) as error:
        return [], None, f"Could not fetch Qibocal branches from GitHub: {error}"


LOCAL_OPTION = QibocalOption(id="local", label="A folder on the server", source="local")


def get_qibocal_options() -> QibocalOptions:
    status = get_qibocal_status()
    options, error = _pypi_options()
    branches, default, github_error = _github_branches()
    options.append(
        QibocalOption(
            id="git", label="Git repository (latest)", source="git", version=None
        )
    )
    return QibocalOptions(
        **status.model_dump(),
        options=[*options, LOCAL_OPTION],
        pypi_error=error,
        git_branches=branches,
        git_default_branch=default,
        github_error=github_error,
    )


def _git_branch_target(branch: str) -> str:
    if not branch or len(branch) > 196:
        raise EnvironmentOperationError(400, "Invalid Qibocal Git branch.")
    try:
        response = httpx.get(
            f"{GITHUB_REPO_URL}/branches/{quote(branch, safe='')}", timeout=10.0
        )
        if response.status_code == 404:
            raise EnvironmentOperationError(
                400, "Choose a branch available in the official Qibocal repository."
            )
        response.raise_for_status()
        data = response.json()
        commit = data.get("commit") if isinstance(data, dict) else None
        sha = commit.get("sha") if isinstance(commit, dict) else None
        if (
            not isinstance(data, dict)
            or data.get("name") != branch
            or not isinstance(sha, str)
            or re.fullmatch(r"[0-9a-f]{40}", sha) is None
        ):
            raise ValueError("GitHub returned invalid branch metadata")
        # Pin GitHub's verified commit, never interpolate an arbitrary Git ref.
        return f"{GIT_URL}@{sha}"
    except (httpx.HTTPError, ValueError, TypeError) as error:
        raise EnvironmentOperationError(
            502, f"Could not validate the Qibocal Git branch with GitHub: {error}"
        ) from error


def local_project(path_text: str) -> Path:
    """A folder on this machine holding a Python project, or the error for the client.

    Any absolute path: the folder with Qibocal's sources is rarely under the
    reports, and only administrators — or anyone, on an open server, who may
    already change the environment — get here.
    """
    if not path_text or len(path_text) > 4096:
        raise EnvironmentOperationError(400, "Name the folder to install Qibocal from.")
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        raise EnvironmentOperationError(400, "The folder must be an absolute path on the server.")
    if not path.is_dir():
        raise EnvironmentOperationError(400, f"No such folder on the server: {path}")
    if not any((path / name).is_file() for name in ("pyproject.toml", "setup.py")):
        raise EnvironmentOperationError(
            400, f"{path} is not a Python project: it has no pyproject.toml or setup.py."
        )
    return path.resolve()


def _installation_target(option: str) -> tuple[str, str, str | None]:
    if option == "git":
        return GIT_URL, "git", None
    if option.startswith("local:"):
        return str(local_project(option.removeprefix("local:"))), "local", None
    if option.startswith("git:"):
        return _git_branch_target(option.removeprefix("git:")), "git", None
    if not option.startswith("pypi:"):
        raise EnvironmentOperationError(400, "Invalid Qibocal installation option.")
    try:
        Version(option.removeprefix("pypi:"))
    except InvalidVersion as error:
        raise EnvironmentOperationError(
            400, "Invalid Qibocal installation option."
        ) from error
    options, pypi_error = _pypi_options()
    if pypi_error:
        raise EnvironmentOperationError(502, pypi_error)
    selected = next((item for item in options if item.id == option), None)
    if selected is None:
        raise EnvironmentOperationError(
            400,
            "Choose one of the recent compatible Qibocal versions "
            "offered by the server.",
        )
    return f"qibocal=={selected.version}", "pypi", selected.version


def _installer_commands(target: str, editable: bool = False) -> list[list[str]]:
    """The installs that put Qibocal in place, run in turn.

    Only Qibocal itself is put back by force — a branch or a commit carries
    the same version number as the installation it replaces, so pip would
    otherwise call it satisfied — and its dependencies are left as they are
    unless the new Qibocal asks for others. Forcing them all would reinstall
    each at its newest version, and a release published minutes ago may have
    no wheel yet: pip would then compile it, SQLAlchemy say, with a compiler
    the server may not have. For the same reason a version with a wheel is
    preferred to a newer one that would have to be built.
    """
    # A local folder is installed editable: the sources are the installation,
    # and a change there is live at the next plot, without installing again.
    requirement = ["-e", target] if editable else [target]
    # uv first: it resolves and installs far faster than pip.
    uv = shutil.which("uv")
    if uv:
        return [
            [
                uv,
                "pip",
                "install",
                "--python",
                sys.executable,
                "--upgrade-package",
                "qibocal",
                "--reinstall-package",
                "qibocal",
                "--refresh-package",
                "qibocal",
                "--index-url",
                "https://pypi.org/simple",
                *requirement,
            ]
        ]
    if util.find_spec("pip") is not None:
        pip = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--prefer-binary",
            "--index-url",
            "https://pypi.org/simple",
        ]
        return [
            # Qibocal alone, whatever is installed.
            [*pip, "--force-reinstall", "--no-deps", *requirement],
            # Then what it needs, and nothing it does not.
            [*pip, "--upgrade-strategy", "only-if-needed", *requirement],
        ]
    raise EnvironmentOperationError(
        503,
        "No package installer is available: install pip in the server's Python "
        "environment or make uv available on the server PATH.",
    )


def _kill_installer(process: subprocess.Popen, *, interrupt: bool = False) -> None:
    if interrupt:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
        elif process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
    # Force cleanup of any remaining children, including inherited output pipes.
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        process.kill()


def stop_qibocal_installation() -> None:
    """Request termination of the current installation, including its children."""
    with _INSTALL_STATE_LOCK:
        if _INSTALL_CANCEL is None:
            raise EnvironmentOperationError(409, "No Qibocal installation is running.")
        _INSTALL_CANCEL.set()


def _check_install_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise EnvironmentOperationError(
            409,
            "Qibocal installation interrupted by administrator (KeyboardInterrupt).",
        )


def _run_installer(
    command: list[str], progress: Callable[[str], None], cancel: threading.Event
) -> tuple[int, str]:
    env = os.environ.copy()
    env.update(
        PYTHONUNBUFFERED="1",
        FORCE_COLOR="1",
        CLICOLOR_FORCE="1",
        UV_COLOR="always",
        PIP_PROGRESS_BAR="off",
    )
    env.pop("NO_COLOR", None)
    env.pop("ANSI_COLORS_DISABLED", None)
    try:
        _check_install_cancel(cancel)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            env=env,
            start_new_session=os.name == "posix",
        )
    except OSError as error:
        raise EnvironmentOperationError(
            503, f"Could not start the Qibocal package installer: {error}"
        ) from error

    completed = False
    tail = ""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    deadline = time.monotonic() + INSTALL_TIMEOUT
    reader: ThreadPoolExecutor | None = None

    def emit(text: str) -> None:
        nonlocal tail
        if text:
            tail = (tail + text)[-INSTALL_FAILURE_TAIL:]
            progress(text)

    try:
        if process.stdout is None:
            raise RuntimeError("The Qibocal installer output pipe is missing.")
        reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qibocal-output")
        while True:
            _check_install_cancel(cancel)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, INSTALL_TIMEOUT)
            # At most one bounded read is outstanding; a pipe read cannot prevent
            # the installer worker from enforcing its deadline.
            pending = reader.submit(process.stdout.read, INSTALL_OUTPUT_CHUNK_BYTES)
            while True:
                _check_install_cancel(cancel)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, INSTALL_TIMEOUT)
                try:
                    chunk = pending.result(timeout=min(remaining, 0.1))
                    break
                except ReaderTimeoutError:
                    continue
            if not chunk:
                break
            emit(decoder.decode(chunk))
        emit(decoder.decode(b"", final=True))
        while True:
            _check_install_cancel(cancel)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, INSTALL_TIMEOUT)
            try:
                returncode = process.wait(timeout=min(remaining, 0.1))
                break
            except subprocess.TimeoutExpired:
                continue
        completed = True
        return returncode, tail
    except subprocess.TimeoutExpired as error:
        raise EnvironmentOperationError(
            504, f"Qibocal installation timed out after {INSTALL_TIMEOUT} seconds."
        ) from error
    finally:
        try:
            if not completed:
                _kill_installer(process, interrupt=cancel.is_set())
            process.wait()
        finally:
            try:
                if process.stdout is not None:
                    process.stdout.close()
            finally:
                if reader is not None:
                    reader.shutdown(wait=True, cancel_futures=True)


def install_cli(
    option: str, progress: Callable[[str], None] | None = None
) -> QibocalStatus:
    """Install a validated official target, optionally reporting live ANSI output."""
    global _INSTALL_CANCEL
    cancel = threading.Event()
    with _INSTALL_STATE_LOCK:
        if not _INSTALL_LOCK.acquire(blocking=False):
            raise EnvironmentOperationError(
                409, "A Qibocal installation is already in progress."
            )
        _INSTALL_CANCEL = cancel
    environment_acquired = False
    try:
        target, source, version = _installation_target(option)
        _check_install_cancel(cancel)
        commands = (
            _installer_commands(target, editable=True)
            if source == "local"
            else _installer_commands(target)
        )
        deadline = time.monotonic() + GENERATION_TIMEOUT + 30
        while not environment_acquired:
            _check_install_cancel(cancel)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            environment_acquired = _ENVIRONMENT_LOCK.acquire(
                timeout=min(remaining, 0.1)
            )
        if not environment_acquired:
            raise EnvironmentOperationError(
                409, "Qibocal plot generation is still running; try installation again."
            )

        def emit(text: str) -> None:
            log_output(text)
            if progress is not None:
                progress(text)

        # The first event follows validation and lock acquisition, so streaming
        # callers can still return ordinary HTTP errors for rejected requests.
        emit(f"\x1b[36mInstalling {target}...\x1b[0m\n")
        returncode, tail = 0, ""
        for command in commands:
            returncode, tail = _run_installer(command, emit, cancel)
            _check_install_cancel(cancel)
            if returncode:
                break
        if returncode:
            output = tail.strip() or "No installer output."
            raise EnvironmentOperationError(
                502,
                f"Qibocal installation failed (exit {returncode}): {output}",
            )
        status = _read_status(
            expected_git_commit=target.removeprefix(f"{GIT_URL}@")
            if option.startswith("git:")
            else None
        )
        if not status.installed or not status.version:
            raise EnvironmentOperationError(
                500, "The installer finished, but Qibocal package metadata is missing."
            )
        try:
            version_matches = version is None or Version(status.version) == Version(
                version
            )
        except InvalidVersion:
            version_matches = False
        if status.source != source or not version_matches:
            raise EnvironmentOperationError(
                500,
                "The installer finished, but Qibocal metadata does not match "
                "the requested installation.",
            )
        if option.startswith("git:"):
            branch = option.removeprefix("git:")
            _record_git_branch(branch)
            status.git_branch = branch
        return status
    finally:
        if environment_acquired:
            _ENVIRONMENT_LOCK.release()
        with _INSTALL_STATE_LOCK:
            _INSTALL_CANCEL = None
            _INSTALL_LOCK.release()
