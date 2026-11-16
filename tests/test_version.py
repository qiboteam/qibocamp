"""What a server says it is."""

import json

from fastapi.testclient import TestClient

from qibocamp import __version__, auth, version
from qibocamp.web.api import app


def test_the_version_is_public_and_the_package_s(monkeypatch):
    monkeypatch.setenv("QIBOCAMP_AUTH_ENABLED", "1")
    auth.set_auth_enabled(True)
    try:
        answer = TestClient(app).get("/api/version")
    finally:
        auth.set_auth_enabled(None)
    assert answer.status_code == 200
    body = answer.json()
    assert body["version"] == __version__ != "0+unknown"
    assert body["api_version"] == version.API_VERSION
    assert "graphql" in body["features"]


class Recorded:
    def __init__(self, origin):
        self.origin = origin

    def read_text(self, name):
        assert name == "direct_url.json"
        return json.dumps(self.origin) if self.origin is not None else None


def test_the_commit_is_the_one_pip_recorded(monkeypatch):
    # No packager's commit: the Nix package's tests run with one written in.
    monkeypatch.setattr(version._build, "COMMIT", None)
    sha = "0123456789abcdef0123456789abcdef01234567"
    cases = [
        ({"url": "https://github.com/qiboteam/qibocamp", "vcs_info": {"vcs": "git", "commit_id": sha}}, sha),
        ({"url": f"https://github.com/qiboteam/qibocamp/archive/{sha}.tar.gz", "archive_info": {}}, sha),
        ({"url": "https://github.com/qiboteam/qibocamp/archive/refs/heads/main.tar.gz"}, None),
        ({"url": "file:///src", "dir_info": {"editable": True}}, None),
        (None, None),
    ]
    for origin, commit in cases:
        monkeypatch.setattr(version, "distribution", lambda _name, origin=origin: Recorded(origin))
        assert version.installed_commit() == commit


def test_without_pip_s_records_the_packager_s_commit(monkeypatch):
    # The Nix flake writes the commit in `_build`: no direct_url.json there.
    sha = "fedcba9876543210fedcba9876543210fedcba98"
    monkeypatch.setattr(version, "distribution", lambda _name: Recorded(None))
    monkeypatch.setattr(version._build, "COMMIT", sha)
    assert version.installed_commit() == sha
    assert version.default_install() == f"git+https://github.com/qiboteam/qibocamp@{sha}"

    def missing(_name):
        raise version.PackageNotFoundError("qibocamp")

    monkeypatch.setattr(version, "distribution", missing)
    assert version.installed_commit() == sha, "nor any distribution at all"

    pip_s = "0123456789abcdef0123456789abcdef01234567"
    monkeypatch.setattr(version, "distribution", lambda _name: Recorded({"url": "x", "vcs_info": {"commit_id": pip_s}}))
    assert version.installed_commit() == pip_s, "pip's record first"

    monkeypatch.setattr(version._build, "COMMIT", None)
    monkeypatch.setattr(version, "distribution", lambda _name: Recorded(None))
    assert version.installed_commit() is None
    assert version.default_install().endswith("@main")
