"""Environment administration and fresh-process plot generation tests."""

import asyncio
import builtins
import json
import os
import signal
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from importlib import metadata
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from qibocamp import auth, logger

from qibocamp.web import api

from qibocamp.reports import generation_worker

from qibocamp.reports import generator
from qibocamp import qibocal_environment as environment
from qibocamp.web.api import app
from qibocamp.models import ProtocolDetail, QibocalInstallRequest, QibocalStatus

_REAL_SUBPROCESS_RUN = subprocess.run
_REAL_SUBPROCESS_POPEN = subprocess.Popen
_REAL_GITHUB_BRANCHES = environment._github_branches


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch):
    monkeypatch.setattr(auth, "_AUTH_ENABLED", True)
    monkeypatch.setattr(environment, "_INSTALL_LOCK", threading.Lock())
    monkeypatch.setattr(environment, "_ENVIRONMENT_LOCK", threading.Lock())
    monkeypatch.setattr(environment, "_INSTALL_CANCEL", None)
    monkeypatch.setattr(
        subprocess,
        "run",
        Mock(side_effect=AssertionError("Real installs are forbidden")),
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        Mock(side_effect=AssertionError("Real installs are forbidden")),
    )
    monkeypatch.setattr(
        httpx, "get", Mock(side_effect=AssertionError("Unexpected external request"))
    )
    monkeypatch.setattr(
        environment, "_github_branches", lambda: (["main", "0.1"], "main", None)
    )


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def credentials():
    result = {}
    for role in ("admin", "editor", "viewer"):
        user = auth.create_user(role, "password123", role)
        result[role] = {"Authorization": f"Bearer {auth.create_access_token(user)}"}
    return result


def mock_metadata(
    monkeypatch,
    *,
    version="0.2.7",
    source="pypi",
    commit="12345",
    revision=None,
    origin_path=None,
):
    if version is None:
        mock = Mock(side_effect=metadata.PackageNotFoundError("qibocal"))
    else:
        origin = None
        if source == "git":
            origin = json.dumps(
                {
                    "url": "https://github.com/qiboteam/qibocal.git",
                    "vcs_info": {
                        "vcs": "git",
                        "commit_id": commit,
                        "requested_revision": revision,
                    },
                }
            )
        elif source == "local":
            origin = json.dumps({"url": "file:///local/qibocal"})
        elif source == "invalid":
            origin = "{"
        distribution = SimpleNamespace(
            version=version, read_text=Mock(return_value=origin)
        )
        if origin_path is not None:
            origin_path.write_text(origin, encoding="utf-8")
            distribution.files = [
                metadata.PackagePath("qibocal.dist-info/direct_url.json")
            ]
            distribution.locate_file = Mock(return_value=origin_path)
            distribution.read_text = Mock(
                side_effect=lambda name: origin_path.read_text(encoding="utf-8")
            )
        mock = Mock(return_value=distribution)
    monkeypatch.setattr(environment.metadata, "distribution", mock)
    return mock


def mock_pypi(monkeypatch, releases=None):
    response = httpx.Response(
        200,
        request=httpx.Request("GET", environment.PYPI_URL),
        json={"releases": releases or {"0.2.7": [{}]}},
    )
    mock = Mock(return_value=response)
    monkeypatch.setattr(httpx, "get", mock)
    return mock


def mock_installer(monkeypatch, *, returncode=0, stderr="", side_effect=None):
    monkeypatch.setattr(environment.util, "find_spec", Mock(return_value=object()))
    # pip, whatever the machine running the tests has on its PATH.
    monkeypatch.setattr(environment.shutil, "which", Mock(return_value=None))
    processes = []
    if isinstance(side_effect, subprocess.TimeoutExpired):
        monkeypatch.setattr(environment, "INSTALL_TIMEOUT", 0.05)

    def start(command, **kwargs):
        if isinstance(side_effect, subprocess.TimeoutExpired):
            script = "import time; time.sleep(60)"
        else:
            result = subprocess.CompletedProcess(
                command, returncode, "installer log", stderr
            )
            if callable(side_effect):
                result = side_effect(command, **kwargs)
            elif side_effect is not None:
                raise side_effect
            script = (
                f"import sys; sys.stdout.write({result.stdout!r}); "
                f"sys.stderr.write({result.stderr!r}); sys.exit({result.returncode})"
            )
        process = _REAL_SUBPROCESS_POPEN([sys.executable, "-c", script], **kwargs)
        processes.append(process)
        return process

    mock = Mock(side_effect=start)
    mock.processes = processes
    monkeypatch.setattr(subprocess, "Popen", mock)
    return mock


def mock_script_installer(monkeypatch, script, *, stdin=None):
    """Run only a harmless test script, never the requested package command.

    The installation is one run here — the one whose output, timing and
    cancellation are under test — rather than pip's two.
    """
    monkeypatch.setattr(environment.util, "find_spec", Mock(return_value=object()))
    monkeypatch.setattr(environment, "_installer_commands", lambda target, editable=False: [["installer"]])
    processes = []

    def start(command, **kwargs):
        process = _REAL_SUBPROCESS_POPEN(
            [sys.executable, "-c", script], stdin=stdin, **kwargs
        )
        processes.append(process)
        return process

    installer = Mock(side_effect=start)
    installer.processes = processes
    monkeypatch.setattr(subprocess, "Popen", installer)
    return installer


@pytest.mark.parametrize(
    ("version", "source", "expected_source"),
    [
        (None, "pypi", None),
        ("0.2.7", "pypi", "pypi"),
        ("0.2.8.dev1", "git", "git"),
        ("0.2.7", "local", "local"),
        ("0.2.7", "invalid", None),
    ],
)
def test_status_reads_only_package_metadata(
    monkeypatch, client, credentials, version, source, expected_source
):
    mock_metadata(monkeypatch, version=version, source=source)
    original_import = builtins.__import__

    def no_qibocal_import(name, *args, **kwargs):
        assert name != "qibocal" and not name.startswith("qibocal.")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_qibocal_import)
    response = client.get("/api/qibocal", headers=credentials["viewer"])
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "installed": version is not None,
        "version": version,
        "source": expected_source,
        "git_branch": None,
        # Installed from a folder: the folder is named.
        "local_path": "/local/qibocal" if expected_source == "local" else None,
    }


@pytest.mark.parametrize(
    ("revision", "branch"),
    [
        ("spectroscopy_pca", "spectroscopy_pca"),
        ("refs/heads/feature/branch", "feature/branch"),
        ("refs/heads/abcdef0", "abcdef0"),
        ("a" * 40, None),
        ("abcdef0", None),
        (None, None),
        (42, None),
    ],
)
def test_git_status_reads_branch_from_metadata_without_network(
    monkeypatch, client, credentials, revision, branch
):
    mock_metadata(monkeypatch, source="git", revision=revision)
    response = client.get("/api/qibocal", headers=credentials["viewer"])
    assert response.status_code == 200
    assert response.json()["git_branch"] == branch
    httpx.get.assert_not_called()


@pytest.mark.parametrize("role", ["viewer", "editor", None])
def test_environment_administration_requires_real_admin(client, credentials, role):
    headers = credentials.get(role, {})
    for method, path, kwargs in (
        ("get", "/api/admin/qibocal/options", {}),
        ("post", "/api/admin/qibocal/install", {"json": {"option": "git"}}),
        ("get", "/api/admin/logs", {}),
        ("post", "/api/admin/qibocal/install/stream", {"json": {"option": "git"}}),
        ("post", "/api/admin/qibocal/install/stop", {}),
    ):
        response = getattr(client, method)(path, headers=headers, **kwargs)
        assert response.status_code == (401 if role is None else 403)
        assert response.json()["detail"]
    subprocess.run.assert_not_called()
    subprocess.Popen.assert_not_called()
    httpx.get.assert_not_called()


def test_anonymous_status_requires_viewer(client):
    assert client.get("/api/qibocal").status_code == 401


@pytest.mark.parametrize("with_admin_token", [False, True])
def test_open_server_can_administer_environment(
    monkeypatch, client, credentials, with_admin_token
):
    monkeypatch.setattr(auth, "_AUTH_ENABLED", False)
    headers = credentials["admin"] if with_admin_token else {}
    mock_metadata(monkeypatch, version=None)
    mock_pypi(monkeypatch)
    installed = QibocalStatus(installed=True, version="0.2.7", source="pypi")
    installer = Mock(return_value=installed)
    monkeypatch.setattr(environment, "install_cli", installer)
    stop = Mock()
    monkeypatch.setattr(environment, "stop_qibocal_installation", stop)
    assert client.get("/api/qibocal").json()["installed"] is False
    options = client.get("/api/admin/qibocal/options", headers=headers)
    assert options.status_code == 200
    assert options.json()["options"]
    logs = client.get("/api/admin/logs", headers=headers)
    assert logs.status_code == 200
    assert set(logs.json()) == {"entries", "cursor"}
    response = client.post(
        "/api/admin/qibocal/install", json={"option": "git"}, headers=headers
    )
    assert response.status_code == 200
    assert response.json() == installed.model_dump()
    installer.assert_called_once_with("git")
    stream = client.post(
        "/api/admin/qibocal/install/stream", json={"option": "git"}, headers=headers
    )
    assert stream.status_code == 200
    assert json.loads(stream.text) == {
        "type": "complete", "status": installed.model_dump()
    }
    assert installer.call_count == 2
    assert installer.call_args.args[0] == "git"
    assert (
        client.post("/api/admin/qibocal/install/stop", headers=headers).status_code
        == 200
    )
    stop.assert_called_once_with()
    subprocess.run.assert_not_called()
    subprocess.Popen.assert_not_called()


@pytest.mark.parametrize("path", ["/api/admin/qibocal/options", "/api/admin/logs"])
def test_current_user_role_not_stale_token_role(client, path):
    user = auth.create_user("former-admin", "password123", "viewer")
    forged_role_token = auth.create_access_token({**user, "role": "admin"})
    response = client.get(
        path,
        headers={"Authorization": f"Bearer {forged_role_token}"},
    )
    assert response.status_code == 403


def test_options_sort_filter_and_limit_versions(monkeypatch, client, credentials):
    mock_metadata(monkeypatch, version=None)
    monkeypatch.setattr(environment.platform, "python_version", lambda: "3.12.1")
    releases = {
        "0.2.9": [{}],
        "0.2.10": [{}],
        "0.3.0": [{}],
        "0.3.1": [{}],
        "0.9.9": [{}],
        "0.10.0": [{}],
        "0.10.1": [
            {"yanked": True},
            {"requires_python": ">=4"},
            {"requires_python": ">=3.10,<3.13"},
        ],
        "0.11.0": [{"requires_python": ">=3.13"}],
        "0.12.0": [{"yanked": True}],
        "1.0.0rc1": [{}],
        "1.0.0.dev1": [{}],
        "1.0.0+local": [{}],
        "garbage": [{}],
        "1.1.0": [],
        "1.2.0": [{"requires_python": "not a specifier"}],
        "1.3.0": "invalid files",
        "v0.10.0": [{}],
    }
    request = mock_pypi(monkeypatch, releases)
    response = client.get("/api/admin/qibocal/options", headers=credentials["admin"])
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["installed"] is False
    assert data["version"] is None and data["source"] is None
    assert data["pypi_error"] is None
    # The last two offers are the Git repository and a folder on the server.
    assert [option["version"] for option in data["options"][:-2]] == [
        "0.10.1",
        "0.10.0",
        "0.9.9",
        "0.3.1",
        "0.3.0",
    ]
    assert data["options"][0] == {
        "id": "pypi:0.10.1",
        "label": "Qibocal 0.10.1 (PyPI)",
        "source": "pypi",
        "version": "0.10.1",
    }
    assert data["options"][-2] == {
        "id": "git",
        "label": "Git repository (latest)",
        "source": "git",
        "version": None,
    }
    assert data["options"][-1]["id"] == "local" and data["options"][-1]["source"] == "local"
    request.assert_called_once_with(environment.PYPI_URL, timeout=10.0)


@pytest.mark.parametrize(
    ("releases", "expected"),
    [
        (
            ["0.2.9", "0.2.8", "0.2.7", "0.2.6", "0.2.5", "0.1.3", "0.1.2"],
            ["0.2.9", "0.2.8", "0.2.7", "0.2.6", "0.2.5", "0.1.3"],
        ),
        (
            ["2.3.5", "2.3.4", "2.3.3", "2.3.2", "2.3.1", "1.9.2", "1.9.1"],
            ["2.3.5", "2.3.4", "2.3.3", "2.3.2", "2.3.1", "1.9.2"],
        ),
        (["0.2.1", "0.1.3"], ["0.2.1", "0.1.3"]),
        (["0.2.1"], ["0.2.1"]),
        ([], []),
    ],
)
def test_options_include_previous_release_line_without_duplicates(
    monkeypatch, releases, expected
):
    mock_pypi(monkeypatch, {version: [{}] for version in releases} or {"invalid": []})
    options, error = environment._pypi_options()
    assert error is None
    assert [option.version for option in options] == expected


def test_previous_release_line_filters_incompatible_and_yanked_versions(monkeypatch):
    releases = {f"0.2.{patch}": [{}] for patch in range(5, 10)}
    releases.update(
        {
            "0.1.9": [{"yanked": True}],
            "0.1.8": [{"requires_python": ">=99"}],
            "0.1.7": [{}],
            "0.1.10rc1": [{}],
        }
    )
    mock_pypi(monkeypatch, releases)
    options, error = environment._pypi_options()
    assert error is None
    assert len(options) == 6
    assert options[-1].id == "pypi:0.1.7"


def test_previous_release_line_can_be_installed(monkeypatch, client, credentials):
    mock_metadata(monkeypatch, version="0.1.7")
    mock_pypi(
        monkeypatch,
        {
            version: [{}]
            for version in ["0.2.9", "0.2.8", "0.2.7", "0.2.6", "0.2.5", "0.1.7"]
        },
    )
    installer = mock_installer(monkeypatch)
    response = client.post(
        "/api/admin/qibocal/install",
        headers=credentials["admin"],
        json={"option": "pypi:0.1.7"},
    )
    assert response.status_code == 200
    assert installer.call_args.args[0][-1] == "qibocal==0.1.7"


def test_github_branches_are_paginated_default_first_and_exposed_in_options(
    monkeypatch, client, credentials
):
    mock_metadata(monkeypatch)
    names = [f"feature/{index:03d}" for index in range(100)]

    def get(url, **kwargs):
        if url == environment.PYPI_URL:
            data = {"releases": {"0.2.7": [{}]}}
        elif url == environment.GITHUB_REPO_URL:
            data = {"default_branch": "main"}
        else:
            assert url == f"{environment.GITHUB_REPO_URL}/branches"
            assert kwargs["params"]["per_page"] == 100
            page = kwargs["params"]["page"]
            data = (
                [{"name": name} for name in names]
                if page == 1
                else [{"name": "main"}, {"name": "0.1"}]
            )
        return httpx.Response(200, json=data, request=httpx.Request("GET", url))

    request = Mock(side_effect=get)
    monkeypatch.setattr(httpx, "get", request)
    monkeypatch.setattr(environment, "_github_branches", _REAL_GITHUB_BRANCHES)
    response = client.get("/api/admin/qibocal/options", headers=credentials["admin"])
    assert response.status_code == 200
    data = response.json()
    assert data["git_default_branch"] == "main"
    assert data["github_error"] is None
    assert data["git_branches"] == ["main", "0.1", *names]
    assert request.call_count == 4


@pytest.mark.parametrize(
    "failure", ["network", "http", "json", "schema", "missing-default"]
)
def test_github_errors_are_explicit_and_preserve_the_default_git_option(
    monkeypatch, failure
):
    if failure == "network":
        get = Mock(side_effect=httpx.ConnectError("offline"))
    elif failure == "http":
        get = Mock(
            return_value=httpx.Response(
                403, request=httpx.Request("GET", environment.GITHUB_REPO_URL)
            )
        )
    elif failure == "json":
        get = Mock(
            return_value=httpx.Response(
                200,
                text="invalid",
                request=httpx.Request("GET", environment.GITHUB_REPO_URL),
            )
        )
    else:
        responses = [
            httpx.Response(
                200,
                json={"default_branch": "main"},
                request=httpx.Request("GET", environment.GITHUB_REPO_URL),
            ),
            httpx.Response(
                200,
                json={} if failure == "schema" else [{"name": "another"}],
                request=httpx.Request("GET", f"{environment.GITHUB_REPO_URL}/branches"),
            ),
        ]
        get = Mock(side_effect=responses)
    monkeypatch.setattr(httpx, "get", get)
    branches, default, error = _REAL_GITHUB_BRANCHES()
    assert branches == [] and default is None
    assert error.startswith("Could not fetch Qibocal branches from GitHub:")


@pytest.mark.parametrize(
    "path", ["/api/admin/qibocal/install", "/api/admin/qibocal/install/stream"]
)
def test_git_branch_is_validated_and_installed_at_its_verified_commit(
    monkeypatch, client, credentials, path, tmp_path
):
    sha = "a" * 40
    origin_path = tmp_path / "direct_url.json"
    mock_metadata(monkeypatch, source="git", commit=sha, origin_path=origin_path)
    request = Mock(
        return_value=httpx.Response(
            200,
            json={"name": "feature/branch", "commit": {"sha": sha}},
            request=httpx.Request(
                "GET", f"{environment.GITHUB_REPO_URL}/branches/feature%2Fbranch"
            ),
        )
    )
    monkeypatch.setattr(httpx, "get", request)
    installer = mock_installer(monkeypatch)
    response = client.post(
        path, headers=credentials["admin"], json={"option": "git:feature/branch"}
    )
    assert response.status_code == 200
    request.assert_called_once_with(
        f"{environment.GITHUB_REPO_URL}/branches/feature%2Fbranch", timeout=10.0
    )
    if path.endswith("/stream"):
        complete = json.loads(response.text.splitlines()[-1])
        assert complete["type"] == "complete"
        assert complete["status"]["git_branch"] == "feature/branch"
    else:
        assert response.json()["source"] == "git"
        assert response.json()["git_branch"] == "feature/branch"
    assert json.loads(origin_path.read_text())["vcs_info"] == {
        "vcs": "git",
        "commit_id": sha,
        "requested_revision": "refs/heads/feature/branch",
    }
    assert (
        client.get("/api/qibocal", headers=credentials["viewer"]).json()["git_branch"]
        == "feature/branch"
    )
    assert (
        client.get("/api/admin/qibocal/options", headers=credentials["admin"]).json()[
            "git_branch"
        ]
        == "feature/branch"
    )
    assert installer.call_args.args[0][-1] == f"{environment.GIT_URL}@{sha}"


@pytest.mark.parametrize("missing_file", [False, True])
def test_branch_metadata_write_failures_are_explicit(monkeypatch, missing_file):
    distribution = mock_metadata(monkeypatch, source="git").return_value
    distribution.files = (
        []
        if missing_file
        else [metadata.PackagePath("qibocal.dist-info/direct_url.json")]
    )
    distribution.locate_file = Mock(
        return_value=Mock(write_text=Mock(side_effect=OSError("read-only environment")))
    )
    with pytest.raises(
        environment.EnvironmentOperationError,
        match="Could not preserve the installed Qibocal Git branch",
    ) as caught:
        environment._record_git_branch("spectroscopy_pca")
    assert caught.value.status_code == 500


@pytest.mark.parametrize(
    ("status", "data", "expected"),
    [
        (404, {}, 400),
        (403, {}, 502),
        (200, {"name": "another", "commit": {"sha": "a" * 40}}, 502),
        (200, {"name": "main", "commit": {"sha": "--upload-pack=evil"}}, 502),
        (200, {"name": "main", "commit": "invalid"}, 502),
        (200, {}, 502),
    ],
)
def test_git_branch_rejections_never_start_the_installer(
    monkeypatch, client, credentials, status, data, expected
):
    monkeypatch.setattr(
        httpx,
        "get",
        Mock(
            return_value=httpx.Response(
                status,
                json=data,
                request=httpx.Request(
                    "GET", f"{environment.GITHUB_REPO_URL}/branches/main"
                ),
            )
        ),
    )
    response = client.post(
        "/api/admin/qibocal/install/stream",
        headers=credentials["admin"],
        json={"option": "git:main"},
    )
    assert response.status_code == expected
    subprocess.Popen.assert_not_called()


def test_git_branch_commit_must_match_installed_metadata(
    monkeypatch, client, credentials
):
    mock_metadata(monkeypatch, source="git", commit="b" * 40)
    monkeypatch.setattr(
        httpx,
        "get",
        Mock(
            return_value=httpx.Response(
                200,
                json={"name": "main", "commit": {"sha": "a" * 40}},
                request=httpx.Request(
                    "GET", f"{environment.GITHUB_REPO_URL}/branches/main"
                ),
            )
        ),
    )
    mock_installer(monkeypatch)
    response = client.post(
        "/api/admin/qibocal/install",
        headers=credentials["admin"],
        json={"option": "git:main"},
    )
    assert response.status_code == 500
    assert "Git branch commit" in response.json()["detail"]


@pytest.mark.parametrize("failure", ["network", "http", "json", "schema"])
def test_pypi_errors_explicitly_preserve_git(monkeypatch, client, credentials, failure):
    mock_metadata(monkeypatch)
    if failure == "network":
        monkeypatch.setattr(
            httpx, "get", Mock(side_effect=httpx.ConnectError("offline"))
        )
    elif failure == "http":
        monkeypatch.setattr(
            httpx,
            "get",
            Mock(
                return_value=httpx.Response(
                    503, request=httpx.Request("GET", environment.PYPI_URL)
                )
            ),
        )
    elif failure == "json":
        monkeypatch.setattr(
            httpx,
            "get",
            Mock(
                return_value=httpx.Response(
                    200,
                    text="not json",
                    request=httpx.Request("GET", environment.PYPI_URL),
                )
            ),
        )
    else:
        monkeypatch.setattr(
            httpx,
            "get",
            Mock(
                return_value=httpx.Response(
                    200,
                    json={"versions": []},
                    request=httpx.Request("GET", environment.PYPI_URL),
                )
            ),
        )
    data = client.get("/api/admin/qibocal/options", headers=credentials["admin"]).json()
    assert data["installed"] is True
    assert data["pypi_error"].startswith("Could not fetch Qibocal versions from PyPI:")
    assert [option["id"] for option in data["options"]] == ["git", "local"]


@pytest.mark.parametrize("source", ["pypi", "git"])
def test_admin_install_uses_fixed_target_and_server_python(
    monkeypatch, client, credentials, source
):
    mock_metadata(monkeypatch, source=source)
    mock_pypi(monkeypatch)
    run = mock_installer(monkeypatch)
    response = client.post(
        "/api/admin/qibocal/install",
        headers=credentials["admin"],
        json={"option": "git" if source == "git" else "pypi:0.2.7"},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "installed": True,
        "version": "0.2.7",
        "source": source,
        "git_branch": None,
        "local_path": None,
    }
    # Qibocal alone by force, then its dependencies only where it needs others.
    target = environment.GIT_URL if source == "git" else "qibocal==0.2.7"
    assert run.call_count == 2
    replace, dependencies = (call.args[0] for call in run.call_args_list)
    assert replace[:4] == [sys.executable, "-m", "pip", "install"]
    assert "--force-reinstall" in replace and "--no-deps" in replace
    assert "--force-reinstall" not in dependencies and "--upgrade" not in dependencies
    assert dependencies[dependencies.index("--upgrade-strategy") + 1] == "only-if-needed"
    assert "--prefer-binary" in replace and "--prefer-binary" in dependencies
    assert replace[-1] == dependencies[-1] == target
    args, kwargs = run.call_args
    assert kwargs["stdout"] == subprocess.PIPE
    assert kwargs["stderr"] == subprocess.STDOUT
    assert kwargs["env"]["PYTHONUNBUFFERED"] == "1"
    assert kwargs["env"]["FORCE_COLOR"] == "1"
    assert kwargs["env"]["UV_COLOR"] == "always"
    assert kwargs.get("shell", False) is False


def test_uv_fallback_targets_server_python(monkeypatch, client, credentials):
    mock_metadata(monkeypatch, source="git")
    run = mock_installer(monkeypatch)
    monkeypatch.setattr(environment.util, "find_spec", Mock(return_value=None))
    monkeypatch.setattr(environment.shutil, "which", Mock(return_value="/tools/uv"))
    response = client.post(
        "/api/admin/qibocal/install",
        headers=credentials["admin"],
        json={"option": "git"},
    )
    assert response.status_code == 200
    command = run.call_args.args[0]
    assert run.call_count == 1
    assert command[:5] == ["/tools/uv", "pip", "install", "--python", sys.executable]
    assert "--upgrade" not in command, "the dependencies are not upgraded wholesale"
    assert command[command.index("--upgrade-package") + 1] == "qibocal"
    assert "--reinstall-package" in command and "--refresh-package" in command
    assert command[-1] == environment.GIT_URL


@pytest.mark.parametrize(
    "path", ["/api/admin/qibocal/install", "/api/admin/qibocal/install/stream"]
)
@pytest.mark.parametrize(
    "option",
    [
        "https://example.com/qibocal.whl",
        environment.GIT_URL,
        "git --extra-index-url evil",
        "pypi:0.2.7 --extra-index-url evil",
        "pypi:0.2.6",
        "pypi:1.0.0rc1",
        "pypi:1.0.0",
        "pypi:1.1.0",
    ],
)
def test_install_rejects_unvalidated_targets(
    monkeypatch, client, credentials, option, path
):
    mock_pypi(
        monkeypatch,
        {
            "0.2.7": [{}],
            "1.0.0rc1": [{}],
            "1.0.0": [{"yanked": True}],
            "1.1.0": [{"requires_python": ">=4"}],
        },
    )
    response = client.post(
        path,
        headers=credentials["admin"],
        json={"option": option},
    )
    assert response.status_code == 400
    assert response.json()["detail"]
    subprocess.run.assert_not_called()
    subprocess.Popen.assert_not_called()


@pytest.mark.parametrize(
    "path", ["/api/admin/qibocal/install", "/api/admin/qibocal/install/stream"]
)
def test_pypi_install_requires_successful_server_validation(
    monkeypatch, client, credentials, path
):
    monkeypatch.setattr(httpx, "get", Mock(side_effect=httpx.ConnectError("offline")))
    response = client.post(
        path,
        headers=credentials["admin"],
        json={"option": "pypi:0.2.7"},
    )
    assert response.status_code == 502
    assert "offline" in response.json()["detail"]
    subprocess.run.assert_not_called()
    subprocess.Popen.assert_not_called()


def test_git_install_does_not_depend_on_pypi(monkeypatch, client, credentials):
    mock_metadata(monkeypatch, source="git")
    mock_installer(monkeypatch)
    response = client.post(
        "/api/admin/qibocal/install",
        headers=credentials["admin"],
        json={"option": "git"},
    )
    assert response.status_code == 200
    httpx.get.assert_not_called()


def test_install_preserves_existing_report_caches(monkeypatch, tmp_path):
    cached_paths = []
    for report in ("current-report", "unrelated-report"):
        cache = tmp_path / report / "report" / "protocols.json"
        cache.parent.mkdir(parents=True)
        cache.write_text('[{"id": "rabi", "name": "Rabi", "figures": []}]')
        cached_paths.append((cache, cache.read_text()))
    mock_metadata(monkeypatch, source="git")
    mock_installer(monkeypatch)
    assert environment.install_cli("git").installed
    assert all(path.read_text() == content for path, content in cached_paths)


def test_metadata_read_failures_have_specific_details(monkeypatch, client, credentials):
    monkeypatch.setattr(
        environment.metadata, "distribution", Mock(side_effect=OSError("access denied"))
    )
    response = client.get("/api/qibocal", headers=credentials["viewer"])
    assert response.status_code == 500
    assert (
        response.json()["detail"]
        == "Could not read Qibocal package metadata: access denied"
    )


@pytest.mark.parametrize(
    "path", ["/api/admin/qibocal/install", "/api/admin/qibocal/install/stream"]
)
def test_missing_installer_is_explicit(monkeypatch, client, credentials, path):
    monkeypatch.setattr(environment.util, "find_spec", Mock(return_value=None))
    monkeypatch.setattr(environment.shutil, "which", Mock(return_value=None))
    response = client.post(
        path,
        headers=credentials["admin"],
        json={"option": "git"},
    )
    assert response.status_code == 503
    assert "No package installer" in response.json()["detail"]
    subprocess.run.assert_not_called()
    subprocess.Popen.assert_not_called()


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    ("failure", "status", "message"),
    [
        ("exit", 502, "dependency conflict"),
        ("timeout", 504, "timed out"),
        ("start", 503, "not permitted"),
        ("metadata", 500, "metadata is missing"),
        ("version", 500, "does not match"),
        ("invalid-version", 500, "does not match"),
        ("source", 500, "does not match"),
    ],
)
def test_install_failures_and_lock_cleanup(
    monkeypatch, client, credentials, failure, status, message, streaming
):
    mock_pypi(monkeypatch)
    mock_metadata(
        monkeypatch,
        version=None
        if failure == "metadata"
        else "0.2.6"
        if failure == "version"
        else "not-a-version"
        if failure == "invalid-version"
        else "0.2.7",
        source="git" if failure == "source" else "pypi",
    )
    side_effect = (
        subprocess.TimeoutExpired(["pip"], 600)
        if failure == "timeout"
        else OSError("not permitted")
        if failure == "start"
        else None
    )
    installer = mock_installer(
        monkeypatch,
        returncode=1 if failure == "exit" else 0,
        stderr="dependency conflict",
        side_effect=side_effect,
    )
    response = client.post(
        "/api/admin/qibocal/install" + ("/stream" if streaming else ""),
        headers=credentials["admin"],
        json={"option": "pypi:0.2.7"},
    )
    if streaming:
        assert response.status_code == 200
        events = [json.loads(line) for line in response.text.splitlines()]
        assert events[0]["type"] == "output"
        assert events[-1]["type"] == "error"
        assert message in events[-1]["detail"]
        assert sum(event["type"] != "output" for event in events) == 1
    else:
        assert response.status_code == status
        assert message in response.json()["detail"]
    for process in installer.processes:
        assert process.poll() is not None
        assert process.stdout.closed
    assert environment._INSTALL_LOCK.acquire(blocking=False)
    environment._INSTALL_LOCK.release()
    assert environment._ENVIRONMENT_LOCK.acquire(blocking=False)
    environment._ENVIRONMENT_LOCK.release()


def test_concurrent_install_conflicts_and_generation_waits(
    monkeypatch, client, credentials, tmp_path
):
    mock_metadata(monkeypatch, source="git")
    monkeypatch.setattr(environment.util, "find_spec", Mock(return_value=object()))
    installation_started = threading.Event()
    release_installation = threading.Event()
    generation_started = threading.Event()

    def install(command, **kwargs):
        installation_started.set()
        assert release_installation.wait(5)
        return subprocess.CompletedProcess(command, 0, "", "")

    def run(command, **kwargs):
        generation_started.set()
        Path(command[-1]).write_text(
            json.dumps(
                {
                    "protocols": [{"id": "rabi", "name": "Rabi", "figures": []}],
                    "error": None,
                    "error_code": None,
                }
            )
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    mock_installer(monkeypatch, side_effect=install)
    monkeypatch.setattr(subprocess, "run", run)
    with ThreadPoolExecutor(max_workers=2) as executor:
        install = executor.submit(
            client.post,
            "/api/admin/qibocal/install",
            headers=credentials["admin"],
            json={"option": "git"},
        )
        try:
            assert installation_started.wait(5)
            conflict = client.post(
                "/api/admin/qibocal/install",
                headers=credentials["admin"],
                json={"option": "git"},
            )
            assert conflict.status_code == 409
            assert "already in progress" in conflict.json()["detail"]
            generation = executor.submit(
                generator._generate_qibocal_protocols, tmp_path
            )
            assert not generation_started.wait(0.1)
        finally:
            release_installation.set()
        assert install.result(timeout=5).status_code == 200
        protocols, error = generation.result(timeout=5)
        assert protocols[0].id == "rabi" and error is None
        assert generation_started.is_set()


def test_install_waits_for_active_generation(monkeypatch):
    mock_metadata(monkeypatch, source="git")
    installer_called = threading.Event()
    waiting_for_environment = threading.Event()

    def run(*args, **kwargs):
        installer_called.set()
        return subprocess.CompletedProcess([], 0, "", "")

    mock_installer(monkeypatch, side_effect=run)
    original_commands = environment._installer_commands

    def commands(target):
        result = original_commands(target)
        waiting_for_environment.set()
        return result

    monkeypatch.setattr(environment, "_installer_commands", commands)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with environment.generation_environment():
            install = executor.submit(environment.install_cli, "git")
            assert waiting_for_environment.wait(5)
            assert not installer_called.wait(0.1)
        assert install.result(timeout=5).installed is True


@pytest.mark.parametrize("source", ["pypi", "git"])
def test_streaming_install_protocol_success(monkeypatch, client, credentials, source):
    mock_metadata(monkeypatch, source=source)
    mock_pypi(monkeypatch)
    mock_installer(monkeypatch, stderr="\x1b[31mcolored stderr\x1b[0m\n")
    response = client.post(
        "/api/admin/qibocal/install/stream",
        headers=credentials["admin"],
        json={"option": "git" if source == "git" else "pypi:0.2.7"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-ndjson"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[-1] == {
        "type": "complete",
        "status": {
            "installed": True,
            "version": "0.2.7",
            "source": source,
            "git_branch": None,
            "local_path": None,
        },
    }
    assert all(set(event) == {"type", "text"} for event in events[:-1])
    assert all(event["type"] == "output" for event in events[:-1])
    text = "".join(event["text"] for event in events[:-1])
    assert "installer log" in text
    assert "\x1b[31mcolored stderr\x1b[0m" in text
    assert not api._INSTALL_TASKS


@pytest.mark.parametrize(
    "body", [{}, {"option": ""}, {"option": 7}, {"option": "x" * 201}]
)
def test_streaming_body_validation_precedes_installation(client, credentials, body):
    response = client.post(
        "/api/admin/qibocal/install/stream", headers=credentials["admin"], json=body
    )
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/json"
    subprocess.Popen.assert_not_called()


@pytest.mark.parametrize(
    "path",
    [
        "/api/admin/logs",
        "/api/admin/qibocal/install/stream",
        "/api/admin/qibocal/install/stop",
    ],
)
def test_diagnostics_reject_invalid_bearer(client, path):
    headers = {"Authorization": "Bearer invalid"}
    if path.endswith("/logs"):
        response = client.get(path, headers=headers)
    else:
        response = client.post(path, headers=headers, json={"option": "git"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    subprocess.Popen.assert_not_called()


def test_streaming_uses_current_role_not_token_role(client):
    user = auth.create_user("demoted-stream-admin", "password123", "viewer")
    token = auth.create_access_token({**user, "role": "admin"})
    response = client.post(
        "/api/admin/qibocal/install/stream",
        headers={"Authorization": f"Bearer {token}"},
        json={"option": "git"},
    )
    assert response.status_code == 403
    subprocess.Popen.assert_not_called()


@pytest.mark.parametrize(
    "path", ["/api/admin/qibocal/install", "/api/admin/qibocal/install/stream"]
)
def test_install_busy_rejection_is_normal_http(monkeypatch, client, credentials, path):
    environment._INSTALL_LOCK.acquire()
    try:
        response = client.post(
            path, headers=credentials["admin"], json={"option": "git"}
        )
        assert response.status_code == 409
        assert response.headers["content-type"] == "application/json"
        assert "already in progress" in response.json()["detail"]
    finally:
        environment._INSTALL_LOCK.release()
    subprocess.Popen.assert_not_called()
    httpx.get.assert_not_called()


@pytest.mark.parametrize(
    "path", ["/api/admin/qibocal/install", "/api/admin/qibocal/install/stream"]
)
def test_install_generation_wait_timeout_releases_install_lock(
    monkeypatch, client, credentials, path
):
    mock_installer(monkeypatch)
    lock = threading.Lock()
    lock.acquire()
    monkeypatch.setattr(environment, "_ENVIRONMENT_LOCK", lock)
    monkeypatch.setattr(environment, "GENERATION_TIMEOUT", -29.98)
    response = client.post(path, headers=credentials["admin"], json={"option": "git"})
    assert response.status_code == 409
    assert "plot generation is still running" in response.json()["detail"]
    assert lock.locked()
    lock.release()
    assert not environment._INSTALL_LOCK.locked()
    subprocess.Popen.assert_not_called()


def test_stop_without_active_installation(client, credentials):
    response = client.post(
        "/api/admin/qibocal/install/stop", headers=credentials["admin"]
    )
    assert response.status_code == 409
    assert "No Qibocal installation" in response.json()["detail"]


@pytest.mark.parametrize("closed_output", [False, True])
@pytest.mark.parametrize("ignore_interrupt", [False, True])
def test_stop_kills_installer_and_releases_locks(
    monkeypatch, client, credentials, closed_output, ignore_interrupt
):
    ready = threading.Event()
    script = "import os, signal, time; "
    if ignore_interrupt:
        script += "signal.signal(signal.SIGINT, signal.SIG_IGN); "
    script += "print('ready', flush=True); "
    if closed_output:
        script += "os.close(1); os.close(2); "
    script += "time.sleep(60)"
    installer = mock_script_installer(monkeypatch, script)

    def progress(text):
        if "ready" in text:
            ready.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(environment.install_cli, "git", progress)
        assert ready.wait(5)
        response = client.post(
            "/api/admin/qibocal/install/stop", headers=credentials["admin"]
        )
        assert response.status_code == 200
        with pytest.raises(
            environment.EnvironmentOperationError, match="KeyboardInterrupt"
        ):
            pending.result(timeout=5)

    process = installer.processes[0]
    assert process.poll() is not None
    if os.name == "posix":
        assert process.returncode == -(
            signal.SIGKILL if ignore_interrupt else signal.SIGINT
        )
    assert process.stdout.closed
    assert not environment._INSTALL_LOCK.locked()
    assert not environment._ENVIRONMENT_LOCK.locked()
    assert environment._INSTALL_CANCEL is None
    assert not any(
        thread.name.startswith("qibocal-output") for thread in threading.enumerate()
    )
    mock_metadata(monkeypatch, source="git")
    mock_installer(monkeypatch)
    assert environment.install_cli("git").installed


def test_stop_while_waiting_for_generation_never_starts_installer(monkeypatch):
    waiting = threading.Event()
    monkeypatch.setattr(
        environment,
        "_installer_commands",
        lambda target: waiting.set() or [["unused"]],
    )
    with environment.generation_environment():
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(environment.install_cli, "git")
            assert waiting.wait(5)
            environment.stop_qibocal_installation()
            with pytest.raises(
                environment.EnvironmentOperationError, match="KeyboardInterrupt"
            ):
                pending.result(timeout=5)
        assert environment._ENVIRONMENT_LOCK.locked()
    assert not environment._INSTALL_LOCK.locked()
    subprocess.Popen.assert_not_called()


def test_installer_output_is_incremental_colored_and_utf8_safe(monkeypatch):
    mock_metadata(monkeypatch, source="git")
    script = (
        "import sys; "
        "sys.stdout.buffer.write(b'\\x1b[32mfirst \\xc3'); sys.stdout.flush(); "
        "sys.stdin.buffer.read(1); "
        "sys.stdout.buffer.write(b'\\xa9\\x1b[0m\\n'); sys.stdout.flush(); "
        "sys.stderr.write('merged stderr\\n'); sys.stderr.flush()"
    )
    installer = mock_script_installer(monkeypatch, script, stdin=subprocess.PIPE)
    chunks = []
    incremental = False

    def progress(text):
        nonlocal incremental
        chunks.append(text)
        if "first " in text:
            process = installer.processes[0]
            assert process.poll() is None
            incremental = True
            process.stdin.write(b"x")
            process.stdin.close()

    assert environment.install_cli("git", progress).installed
    assert incremental
    combined = "".join(chunks)
    assert "\x1b[32mfirst \u00e9\x1b[0m\n" in combined
    assert "merged stderr" in combined
    assert "\ufffd" not in combined
    assert installer.processes[0].stdout.closed
    assert installer.call_args.kwargs["start_new_session"] == (os.name == "posix")


def test_install_failure_keeps_only_bounded_output_tail(monkeypatch):
    script = "import sys; sys.stdout.write('x' * 100000 + 'failure tail'); sys.exit(1)"
    mock_script_installer(monkeypatch, script)
    chunk_sizes = []
    with pytest.raises(environment.EnvironmentOperationError) as caught:
        environment.install_cli("git", lambda text: chunk_sizes.append(len(text)))
    error = caught.value
    assert error.status_code == 502
    prefix = "Qibocal installation failed (exit 1): "
    assert error.detail.startswith(prefix)
    assert len(error.detail.removeprefix(prefix)) == environment.INSTALL_FAILURE_TAIL
    assert error.detail.endswith("failure tail")
    assert max(chunk_sizes) <= environment.INSTALL_OUTPUT_CHUNK_BYTES
    assert not environment._INSTALL_LOCK.locked()
    assert not environment._ENVIRONMENT_LOCK.locked()


@pytest.mark.parametrize("closed_output", [False, True])
def test_installer_timeout_kills_reaps_and_closes_pipe(monkeypatch, closed_output):
    script = "import os, time; "
    if closed_output:
        script += "os.close(1); os.close(2); "
    script += "time.sleep(60)"
    installer = mock_script_installer(monkeypatch, script)
    monkeypatch.setattr(environment, "INSTALL_TIMEOUT", 0.05)
    with pytest.raises(
        environment.EnvironmentOperationError, match="timed out"
    ) as caught:
        environment.install_cli("git")
    assert caught.value.status_code == 504
    process = installer.processes[0]
    assert process.poll() is not None
    assert process.stdout.closed
    assert not environment._INSTALL_LOCK.locked()
    assert not environment._ENVIRONMENT_LOCK.locked()
    assert not any(
        thread.name.startswith("qibocal-output") for thread in threading.enumerate()
    )


def test_unexpected_progress_error_propagates_with_process_and_lock_cleanup(
    monkeypatch,
):
    installer = mock_script_installer(
        monkeypatch,
        "import time; print('ready', flush=True); time.sleep(60)",
    )

    def progress(text):
        if "ready" in text:
            raise RuntimeError("broken progress consumer")

    with pytest.raises(RuntimeError, match="broken progress consumer"):
        environment.install_cli("git", progress)
    process = installer.processes[0]
    assert process.poll() is not None
    assert process.stdout.closed
    assert not environment._INSTALL_LOCK.locked()
    assert not environment._ENVIRONMENT_LOCK.locked()


def test_pipe_reader_error_propagates_with_process_and_lock_cleanup(monkeypatch):
    installer = mock_script_installer(monkeypatch, "import time; time.sleep(60)")
    start = installer.side_effect

    def broken_output(command, **kwargs):
        process = start(command, **kwargs)
        monkeypatch.setattr(
            process.stdout, "read", Mock(side_effect=OSError("broken output pipe"))
        )
        return process

    installer.side_effect = broken_output
    with pytest.raises(OSError, match="broken output pipe"):
        environment.install_cli("git")
    process = installer.processes[0]
    assert process.poll() is not None
    assert process.stdout.closed
    assert not environment._INSTALL_LOCK.locked()
    assert not environment._ENVIRONMENT_LOCK.locked()


def test_unexpected_stream_error_is_logged_and_reported(
    monkeypatch, client, credentials, caplog
):
    mock_installer(monkeypatch)
    monkeypatch.setattr(
        environment.metadata,
        "distribution",
        Mock(side_effect=RuntimeError("bad metadata")),
    )
    response = client.post(
        "/api/admin/qibocal/install/stream",
        headers=credentials["admin"],
        json={"option": "git"},
    )
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[-1] == {
        "type": "error",
        "detail": "Unexpected Qibocal installation failure: bad metadata",
    }
    assert "Unexpected Qibocal installation failure" in caplog.text
    assert "RuntimeError: bad metadata" in caplog.text
    assert not environment._INSTALL_LOCK.locked()
    assert not environment._ENVIRONMENT_LOCK.locked()


def test_unexpected_preflight_failure_is_not_hidden(monkeypatch, credentials, caplog):
    monkeypatch.setattr(
        environment,
        "_installer_commands",
        Mock(side_effect=RuntimeError("bad preflight")),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/admin/qibocal/install/stream",
            headers=credentials["admin"],
            json={"option": "git"},
        )
    assert response.status_code == 500
    assert "application/x-ndjson" not in response.headers["content-type"]
    assert "RuntimeError: bad preflight" in caplog.text
    assert not environment._INSTALL_LOCK.locked()
    assert not environment._ENVIRONMENT_LOCK.locked()
    subprocess.Popen.assert_not_called()


def test_stream_output_buffer_is_bounded_and_nonblocking():
    output = api._InstallationOutput()
    for index in range(1000):
        output.append(str(index))
    entries = output.drain()
    assert len(entries) == api.INSTALL_STREAM_BUFFER_ENTRIES + 1
    assert "Earlier installer output omitted" in entries[0]
    assert entries[1:] == [
        str(index) for index in range(1000 - api.INSTALL_STREAM_BUFFER_ENTRIES, 1000)
    ]
    output.append("x" * (environment.INSTALL_OUTPUT_CHUNK_BYTES * 2))
    assert len(output.drain()[-1]) == environment.INSTALL_OUTPUT_CHUNK_BYTES
    output.disconnect()
    for _ in range(1000):
        output.append("disconnected")
    assert output.drain() == []


@pytest.mark.asyncio
async def test_stream_delivers_output_while_worker_and_event_loop_remain_live(
    monkeypatch,
):
    mock_metadata(monkeypatch, source="git")
    installer = mock_script_installer(
        monkeypatch,
        "import sys; print('early output', flush=True); "
        "sys.stdin.buffer.read(1); print('late output', flush=True)",
        stdin=subprocess.PIPE,
    )
    response = await asyncio.wait_for(
        api.qibocal_install_stream(QibocalInstallRequest(option="git"), _user={}), 3
    )
    iterator = response.body_iterator
    texts = []
    try:
        while not any("early output" in text for text in texts):
            event = json.loads(await asyncio.wait_for(anext(iterator), 3))
            assert event["type"] == "output"
            texts.append(event["text"])
        assert environment._ENVIRONMENT_LOCK.locked()
        assert len(api._INSTALL_TASKS) == 1
        assert not next(iter(api._INSTALL_TASKS)).done()
        await asyncio.wait_for(asyncio.sleep(0.01), 1)
    finally:
        process = installer.processes[0]
        process.stdin.write(b"x")
        process.stdin.close()
    events = [json.loads(line) async for line in iterator]
    assert "late output" in "".join(event.get("text", "") for event in events)
    assert events[-1]["type"] == "complete"
    assert not api._INSTALL_TASKS


@pytest.mark.parametrize("returncode", [0, 1])
@pytest.mark.asyncio
async def test_disconnect_keeps_install_running_and_shutdown_waits(
    monkeypatch, returncode, caplog, client, credentials
):
    history = logger.LogHistory()
    monkeypatch.setattr(logger, "log_history", history)
    monkeypatch.setattr(api, "log_history", history)
    mock_metadata(monkeypatch, source="git")
    monkeypatch.setattr(auth, "create_initial_admin_invite_if_needed", lambda: None)
    installer = mock_script_installer(
        monkeypatch,
        "import sys; print('waiting', flush=True); "
        "sys.stdin.buffer.read(1); print('\\x1b[32mfinished\\x1b[0m', flush=True); "
        f"sys.exit({returncode})",
        stdin=subprocess.PIPE,
    )
    manager = api.lifespan(app)
    await manager.__aenter__()
    response = await asyncio.wait_for(
        api.qibocal_install_stream(QibocalInstallRequest(option="git"), _user={}), 3
    )
    disconnected = asyncio.Event()

    async def send(message):
        if message["type"] == "http.response.body" and b"waiting" in message["body"]:
            disconnected.set()

    async def receive():
        await disconnected.wait()
        return {"type": "http.disconnect"}

    await asyncio.wait_for(
        response({"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send), 3
    )
    task = next(iter(api._INSTALL_TASKS))
    disconnected_cursor = history.snapshot()["cursor"]
    assert not task.cancelled() and not task.done()
    assert environment._INSTALL_LOCK.locked()
    assert environment._ENVIRONMENT_LOCK.locked()
    shutdown = asyncio.create_task(manager.__aexit__(None, None, None))
    try:
        await asyncio.sleep(0.05)
        assert not shutdown.done()
    finally:
        process = installer.processes[0]
        process.stdin.write(b"x")
        process.stdin.close()
    await asyncio.wait_for(shutdown, 3)
    assert task.done() and not task.cancelled()
    if returncode:
        assert isinstance(task.exception(), environment.EnvironmentOperationError)
        assert "installation failed" in caplog.text
    else:
        assert task.result().installed
    assert process.stdout.closed
    assert not environment._INSTALL_LOCK.locked()
    assert not environment._ENVIRONMENT_LOCK.locked()
    assert not api._INSTALL_TASKS
    logs = client.get(
        f"/api/admin/logs?after={disconnected_cursor}", headers=credentials["admin"]
    )
    assert logs.status_code == 200
    assert any(
        "\x1b[32mfinished\x1b[0m" in entry["text"] for entry in logs.json()["entries"]
    )
    assert logs.json()["cursor"] > disconnected_cursor


def test_generation_uses_result_file_not_qibocal_logs(monkeypatch, tmp_path):
    def run(command, **kwargs):
        assert command[:3] == [sys.executable, "-m", "qibocamp.reports.generation_worker"]
        assert command[-2] == str(tmp_path.resolve())
        assert kwargs["timeout"] == environment.GENERATION_TIMEOUT
        assert kwargs.get("shell", False) is False
        Path(command[-1]).write_text(
            json.dumps(
                {
                    "protocols": [
                        {"id": "rabi", "name": "Rabi", "figures": [{"data": []}]}
                    ],
                    "error": None,
                    "error_code": None,
                }
            )
        )
        return subprocess.CompletedProcess(command, 0, "Qibocal logs, not JSON", "")

    monkeypatch.setattr(subprocess, "run", run)
    protocols, error = generator._generate_qibocal_protocols(tmp_path)
    assert error is None and protocols[0].id == "rabi"
    assert not list(tmp_path.glob(".qibocamp-generation-*"))


@pytest.mark.parametrize("failure", ["timeout", "exit", "no-result", "bad-result"])
def test_worker_failures_are_informative_and_cleaned(monkeypatch, tmp_path, failure):
    def run(command, **kwargs):
        if failure == "timeout":
            Path(command[-1]).write_text("partial")
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if failure == "bad-result":
            Path(command[-1]).write_text("log instead of JSON")
        return subprocess.CompletedProcess(
            command, 1 if failure == "exit" else 0, "", "dependency failure"
        )

    monkeypatch.setattr(subprocess, "run", run)
    protocols = generator.generate_report_on_the_fly(tmp_path)
    assert protocols[0].status == "error"
    assert protocols[0].error
    assert protocols[0].error_code is None
    assert "not installed" not in protocols[0].error
    assert not list(tmp_path.glob(".qibocamp-generation-*"))


@pytest.mark.parametrize(
    ("missing_name", "installed", "expected_code"),
    [
        ("qibocal", False, "qibocal_not_installed"),
        ("qibocal", True, None),
        ("qibolab", True, None),
        ("API", True, None),
    ],
)
def test_native_import_errors_only_label_real_absence(
    monkeypatch, tmp_path, missing_name, installed, expected_code
):
    original_import = builtins.__import__

    def import_qibocal(name, *args, **kwargs):
        if name.startswith("qibocal."):
            if missing_name == "API":
                raise ImportError("cannot import name Output")
            raise ModuleNotFoundError(
                f"No module named '{missing_name}'", name=missing_name
            )
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_qibocal)
    monkeypatch.setattr(
        generator,
        "get_qibocal_status",
        lambda: QibocalStatus(
            installed=installed, version="0.2.7" if installed else None
        ),
    )
    protocols, error = generator._generate_qibocal_protocols_native(tmp_path)
    assert protocols is None
    assert getattr(error, "error_code", None) == expected_code
    if expected_code:
        assert "Qibocal is not installed" in error
    else:
        assert "Error importing Qibocal or its dependencies" in error
        assert "not installed" not in error


def test_missing_code_survives_worker_and_protocol_serialization(monkeypatch, tmp_path):
    def run(command, **kwargs):
        Path(command[-1]).write_text(
            json.dumps(
                {
                    "protocols": None,
                    "error": "Qibocal is not installed in the environment.",
                    "error_code": "qibocal_not_installed",
                }
            )
        )
        return subprocess.CompletedProcess(command, 0, "many logs", "")

    monkeypatch.setattr(subprocess, "run", run)
    detail = generator.generate_report_on_the_fly(tmp_path)[0]
    assert detail.status == "error"
    assert detail.model_dump()["error_code"] == "qibocal_not_installed"


def test_an_error_without_a_code_reaches_the_dashboard_as_its_message(monkeypatch, tmp_path):
    monkeypatch.setattr(
        generator,
        "_generate_qibocal_protocols",
        lambda path: (None, generator.GenerationError("Qibocal is not installed in the environment.")),
    )
    protocol = generator.generate_report_on_the_fly(tmp_path)[0]
    assert "Qibocal is not installed" in protocol.error
    assert protocol.error_code is None


def test_all_native_errors_preserved_without_false_absence(monkeypatch, tmp_path):
    detail = ProtocolDetail(
        id="rabi",
        name="Rabi",
        status="error",
        error="Fit data is incompatible",
        html="<p>Partial results</p>",
    )
    monkeypatch.setattr(
        generator, "_generate_qibocal_protocols", lambda path: ([detail], None)
    )
    assert generator.generate_report_on_the_fly(tmp_path) == [detail]
    assert not generator.has_cached_report(tmp_path)


def test_empty_native_results_do_not_claim_qibocal_absence(monkeypatch, tmp_path):
    monkeypatch.setattr(
        generator, "_generate_qibocal_protocols", lambda path: ([], None)
    )
    protocol = generator.generate_report_on_the_fly(tmp_path)[0]
    assert "not installed" not in protocol.error
    assert protocol.error_code is None


@pytest.mark.parametrize("targets", [[], [0], [0, 1]])
def test_native_target_errors_preserve_details_and_partial_output(
    monkeypatch, tmp_path, targets
):
    task = SimpleNamespace(operation_name="rabi", targets=targets)
    completed = SimpleNamespace(task=task)
    output_module = ModuleType("qibocal.auto.output")
    output_module.Output = SimpleNamespace(
        load=lambda path: SimpleNamespace(history={"rabi": completed})
    )
    report_module = ModuleType("qibocal.cli.report")

    def figures(completed, target):
        if target == 1:
            return [{"data": [], "layout": {}}], "<p>Target 1 succeeded</p>"
        raise RuntimeError("fitting did not converge")

    report_module.generate_figures_and_report = figures
    monkeypatch.setitem(sys.modules, "qibocal.auto.output", output_module)
    monkeypatch.setitem(sys.modules, "qibocal.cli.report", report_module)
    protocols, error = generator._generate_qibocal_protocols_native(tmp_path)
    assert error is None
    assert protocols[0].status == "error"
    assert "fitting did not converge" in protocols[0].error
    assert protocols[0].error_code is None
    assert bool(protocols[0].figures) == (targets == [0, 1])


def test_worker_serializes_array_like_values_and_logs(monkeypatch, tmp_path, capsys):
    class ArrayLike:
        def tolist(self):
            return [1, 2]

    def native(path):
        print("Qibocal emitted a log")
        return [
            ProtocolDetail(
                id="rabi", name="Rabi", figures=[{"data": [{"x": ArrayLike()}]}]
            )
        ], None

    result = tmp_path / "worker-result.json"
    monkeypatch.setattr(generation_worker, "_generate_qibocal_protocols_native", native)
    monkeypatch.setattr(sys, "argv", ["worker", str(tmp_path), str(result)])
    generation_worker.main()
    assert "Qibocal emitted a log" in capsys.readouterr().out
    payload = json.loads(result.read_text())
    assert payload["protocols"][0]["figures"][0]["data"][0]["x"] == [1, 2]


def test_fresh_worker_uses_changed_version_not_stale_parent_module(
    monkeypatch, tmp_path
):
    package = tmp_path / "packages" / "qibocal"
    for subpackage in ("", "auto", "cli"):
        directory = package / subpackage
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "__init__.py").write_text("")
    (package / "auto" / "output.py").write_text(
        "from types import SimpleNamespace\n"
        "class Output:\n"
        "    @staticmethod\n"
        "    def load(path):\n"
        "        task = SimpleNamespace(operation_name='rabi', targets=[])\n"
        "        return SimpleNamespace(history={'rabi': SimpleNamespace(task=task)})\n"
    )
    (package / "cli" / "report.py").write_text(
        "from qibocal import __version__\n"
        "def generate_figures_and_report(completed, target):\n"
        "    print('Qibocal worker log')\n"
        "    return [], '<p>' + __version__ + '</p>'\n"
    )
    stale = ModuleType("qibocal")
    stale.__version__ = "stale"
    monkeypatch.setitem(sys.modules, "qibocal", stale)
    monkeypatch.setattr(subprocess, "run", _REAL_SUBPROCESS_RUN)
    monkeypatch.setattr(subprocess, "Popen", _REAL_SUBPROCESS_POPEN)
    # The stand-in Qibocal first, and qibocamp still importable: where it is
    # only found through the path — the Nix package's tests — replacing the
    # path would leave the worker without it.
    import qibocamp

    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(
            [str(package.parent), str(Path(qibocamp.__file__).resolve().parent.parent)]
            + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else [])
        ),
    )
    for version in ("1.0", "2.0.1"):
        (package / "__init__.py").write_text(f"__version__ = '{version}'\n")
        protocols, error = generator._generate_qibocal_protocols(tmp_path)
        assert error is None
        assert protocols and protocols[0].status == "success"
        assert protocols[0].html == f"<p>{version}</p>"
    assert sys.modules["qibocal"] is stale
    assert not list(tmp_path.glob(".qibocamp-generation-*"))
