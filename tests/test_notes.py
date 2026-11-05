"""Qibocal-compatible histories, legacy reports, and authorized commenting."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from qibocamp import auth

from qibocamp.web import api

from qibocamp.reports import generator

from qibocamp.reports import live
from qibocamp.reports.live_inputs import metadata_inputs, protocol_inputs
from qibocamp.models import Note, ProtocolDetail, ReportDetail
from qibocamp.reports.notes import (
    NotesError,
    append_note,
    load_notes,
    protocol_notes_directory,
)


@pytest.fixture
def report(tmp_path, monkeypatch):
    directory = tmp_path / "nested" / "run"
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text('{"author": "test", "stats": {}}')
    (directory / "history.json").write_text('["rabi-0", "rabi-1"]')
    for task in ("rabi-0", "rabi-1"):
        data = directory / "data" / task
        data.mkdir(parents=True)
        (data / "data.json").write_text('{"value": 1}')
    generator.write_protocol_cache(
        directory,
        [
            ProtocolDetail(id=task, name=task, html="cached")
            for task in ("rabi-0", "rabi-1")
        ],
    )
    api.set_report_root(tmp_path, is_original=True)
    monkeypatch.setattr(live, "LIVE_POLL_INTERVAL", 0.01)
    auth.set_auth_enabled(False)
    yield directory
    auth.set_auth_enabled(None)


def prefix():
    return f"/api/reports/{quote('nested/run', safe='')}"


def test_legacy_models_and_reports(report):
    assert ProtocolDetail(id="rabi", name="Rabi").notes == []
    assert ReportDetail(id="run", path="run", date="").notes == []
    with TestClient(api.app) as client:
        assert client.get(prefix()).json()["notes"] == []
        protocols = client.get(f"{prefix()}/protocols").json()
        assert len(protocols) == 2
        assert all(p["notes"] == [] and p["html"] == "cached" for p in protocols)
        for suffix in ("/notes", "/protocols/rabi-0/notes"):
            response = client.get(prefix() + suffix)
            assert response.status_code == 200
            assert response.json() == []
            assert response.headers["cache-control"] == "no-store"
    assert not list(report.rglob("notes.json"))


def test_qibocal_histories_are_fresh_and_iteration_specific(report):
    notes = [
        {
            "content": "Initial finding",
            "timestamp": "2026-10-01T12:00:00+02:00",
            "author": "agent",
        },
        {"content": "Follow-up\nMore detail", "timestamp": "2026-10-01T11:00:00Z"},
    ]
    (report / "notes.json").write_text(json.dumps(notes))
    (report / "data" / "rabi-1" / "notes.json").write_text(json.dumps(notes))
    with TestClient(api.app) as client:
        detail = client.get(prefix()).json()
        assert len(detail["notes"]) == 2
        assert detail["notes"][0]["timestamp"] == "2026-10-01T10:00:00Z"
        assert detail["notes"][1]["author"] is None
        protocols = client.get(f"{prefix()}/protocols").json()
        assert protocols[0]["notes"] == []
        assert protocols[1]["notes"] == detail["notes"]
        append_note(report / "data" / "rabi-1", "After caching", None)
        assert len(client.get(f"{prefix()}/protocols").json()[1]["notes"]) == 3
    # An ambiguous legacy routine name must not select an arbitrary iteration.
    assert protocol_notes_directory(report, "rabi") is None
    assert protocol_notes_directory(report, "rabi_1").name == "rabi-1"


@pytest.mark.parametrize("scope", ["", "/protocols/rabi-1"])
def test_append_preserves_artifacts_and_history(report, scope):
    directory = report if not scope else report / "data" / "rabi-1"
    original = append_note(directory, "Agent finding", "agent")
    artifacts = {
        path: path.read_bytes()
        for path in report.rglob("*")
        if path.is_file()
        and not path.name.startswith(".notes")
        and path.name != "notes.json"
    }
    with TestClient(api.app) as client:
        response = client.post(f"{prefix()}{scope}/notes", json={"content": " Review "})
        assert response.status_code == 200
        saved = response.json()
        assert saved[0] == original[0].model_dump(mode="json")
        assert saved[1]["content"] == "Review"
        assert saved[1]["author"] is None
        assert (
            datetime.fromisoformat(saved[1]["timestamp"].replace("Z", "+00:00")).tzinfo
            == timezone.utc
        )
        assert response.headers["cache-control"] == "no-store"
        assert client.get(f"{prefix()}{scope}/notes").json() == saved
        for content in ("", " \n "):
            assert (
                client.post(
                    f"{prefix()}{scope}/notes", json={"content": content}
                ).status_code
                == 422
            )
        assert (
            client.post(
                f"{prefix()}{scope}/notes", json={"content": "Spoof", "author": "admin"}
            ).status_code
            == 422
        )
    assert load_notes(directory)[0] == original[0]
    assert all(path.read_bytes() == raw for path, raw in artifacts.items())
    if scope:
        assert load_notes(report / "data" / "rabi-0") == []


@pytest.mark.parametrize("role", ["viewer", "editor", "admin"])
def test_comment_permissions_and_server_assigned_author(report, role):
    auth.set_auth_enabled(True)
    user = auth.create_user(f"notes_{role}", "password", role)
    headers = {"Authorization": f"Bearer {auth.create_access_token(user)}"}
    with TestClient(api.app) as client:
        for suffix in ("/notes", "/protocols/rabi-0/notes"):
            url = prefix() + suffix
            assert client.get(url).status_code == 401
            assert client.post(url, json={"content": "Finding"}).status_code == 401
            assert client.get(url, headers=headers).status_code == 200
            response = client.post(url, json={"content": "Finding"}, headers=headers)
            assert response.status_code == (403 if role == "viewer" else 200)
            if role != "viewer":
                assert response.json()[0]["author"] == user["username"]


def test_concurrent_server_appends_retain_every_comment(report):
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(lambda i: append_note(report, f"Finding {i}", None), range(20))
        )
    assert {note.content for note in load_notes(report)} == {
        f"Finding {i}" for i in range(20)
    }
    assert not list(report.glob(".notes-*.json"))


def test_windows_lock_adapter_releases_on_success_and_invalid_history(
    report, monkeypatch
):
    calls = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(
        sys.modules,
        "msvcrt",
        SimpleNamespace(
            LK_LOCK=1,
            LK_UNLCK=0,
            locking=lambda fd, mode, length: calls.append((mode, length)),
        ),
    )
    append_note(report, "Finding", None)
    assert calls == [(1, 1), (0, 1)]
    (report / "notes.json").write_text("invalid")
    with pytest.raises(NotesError):
        append_note(report, "Another", None)
    assert calls == [(1, 1), (0, 1), (1, 1), (0, 1)]


@pytest.mark.parametrize(
    "raw", ["not json", "{}", '[{"content": ""}]', '[{"content": "Missing timestamp"}]']
)
@pytest.mark.parametrize("scope", ["", "/protocols/rabi-0"])
def test_invalid_histories_surface_and_are_not_overwritten(report, raw, scope):
    directory = report if not scope else report / "data" / "rabi-0"
    path = directory / "notes.json"
    path.write_text(raw)
    with TestClient(api.app) as client:
        assert client.get(prefix()).status_code == 500
        assert client.get(f"{prefix()}{scope}/notes").status_code == 500
        response = client.post(f"{prefix()}{scope}/notes", json={"content": "Finding"})
        assert response.status_code == 500
        assert "Invalid notes" in response.json()["detail"]
    assert path.read_text() == raw


def test_notes_when_serving_a_single_report_root(report):
    api.set_report_root(report)
    with TestClient(api.app) as client:
        response = client.post(
            "/api/reports/run/notes", json={"content": "Root report"}
        )
        assert response.status_code == 200
        assert client.get("/api/reports/run").json()["notes"] == response.json()


def test_notes_cannot_target_outside_reports_or_symlinks(report, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "notes.json").write_text("[]")
    (report / "data" / "external").symlink_to(outside, target_is_directory=True)
    (report / "notes.json").symlink_to(outside / "notes.json")
    with TestClient(api.app) as client:
        for path in [
            "/api/reports/missing/notes",
            f"{prefix()}/protocols/missing/notes",
            f"{prefix()}/protocols/rabi/notes",
            f"{prefix()}/protocols/external/notes",
            f"/api/reports/{quote(str(outside), safe='')}/notes",
            "/api/reports/nested/../outside/notes",
        ]:
            assert client.post(path, json={"content": "No"}).status_code == 404
        assert (
            client.post(f"{prefix()}/notes", json={"content": "No"}).status_code == 500
        )
    assert (outside / "notes.json").read_text() == "[]"
    with pytest.raises(NotesError, match="symbolic"):
        load_notes(report)


@pytest.mark.parametrize("with_notes", [False, True])
def test_websocket_serializes_histories_and_legacy_outputs(report, with_notes):
    if with_notes:
        append_note(report, "Session", "agent")
        append_note(report / "data" / "rabi-0", "Protocol", None)
    with TestClient(api.app) as client:
        with client.websocket_connect("/ws/reports/nested/run") as socket:
            metadata = socket.receive_json()
            assert metadata["type"] == "metadata"
            assert len(metadata["report"]["notes"]) == int(with_notes)
            queued = socket.receive_json()
            assert queued["type"] == "queued" and queued["ids"] == [], "all cached: nothing to generate"
            assert socket.receive_json()["type"] == "done"
            socket.send_json({"type": "regenerate", "ids": ["rabi-0"]})
            assert socket.receive_json()["regenerating"] == ["rabi-0"]
            while (message := socket.receive_json())["type"] != "protocol":
                pass
            assert message["protocol"]["id"] == "rabi-0"
            assert len(message["protocol"]["notes"]) == int(with_notes), "a generated protocol carries its notes"


def test_generation_and_regeneration_keep_notes_outside_plot_cache(report, monkeypatch):
    note = append_note(report / "data" / "rabi-1", "Keep this finding", "agent")[0]
    monkeypatch.setattr(
        generator,
        "_generate_qibocal_protocols",
        lambda _: (
            [ProtocolDetail(id="rabi-1", name="Rabi", html="regenerated")],
            None,
        ),
    )
    protocols = generator.regenerate_report(report)
    assert protocols[0].notes == [note]
    cached = json.loads((report / "report" / "protocols.json").read_text())
    assert "notes" not in cached[0]
    assert load_notes(report / "data" / "rabi-1") == [note]
    monkeypatch.setattr(
        generator, "_generate_qibocal_protocols", lambda _: (None, generator.GenerationError("No Qibocal"))
    )
    protocols = generator.generate_report_on_the_fly(report)
    assert next(p for p in protocols if p.id == "rabi-1").notes == [note]


def test_live_comments_push_without_regenerating_plots(report, monkeypatch):
    before = protocol_inputs(report)
    meta_before = metadata_inputs(report)
    monkeypatch.setattr(
        live,
        "_generate_qibocal_protocols",
        lambda *_: pytest.fail("Comments must not regenerate plots"),
    )
    with TestClient(api.app) as client:
        with client.websocket_connect("/ws/live/reports/nested/run") as socket:
            assert socket.receive_json()["type"] == "live"
            assert socket.receive_json()["type"] == "snapshot"
            # Wait until the initial metadata has established a baseline.
            assert socket.receive_json()["type"] == "metadata"
            append_note(report, "Live session", None)
            append_note(report / "data" / "rabi-1", "Live protocol", "agent")
            assert protocol_inputs(report) == before
            assert metadata_inputs(report) != meta_before
            metadata = socket.receive_json()
            assert metadata["type"] == "metadata"
            assert metadata["report"]["notes"][0]["content"] == "Live session"
            update = socket.receive_json()
            assert update["type"] == "update"
            protocol = next(p for p in update["protocols"] if p["id"] == "rabi-1")
            assert protocol["notes"][0]["content"] == "Live protocol"
            assert protocol["html"] == "cached"


def test_live_comment_update_preserves_latest_generation_failure(report, monkeypatch):
    calls = []

    def fail_generation(_path, ids):
        calls.append(ids)
        return [
            ProtocolDetail(id=task, name=task, status="error", error="Fit failed")
            for task in ids
        ], None

    monkeypatch.setattr(live, "_generate_qibocal_protocols", fail_generation)
    with TestClient(api.app) as client:
        with client.websocket_connect("/ws/live/reports/nested/run") as socket:
            assert socket.receive_json()["type"] == "live"
            assert socket.receive_json()["type"] == "snapshot"
            assert socket.receive_json()["type"] == "metadata"
            (report / "data" / "rabi-0" / "data.json").write_text('{"value": 2}')
            assert socket.receive_json()["type"] == "metadata"
            failed = socket.receive_json()["protocols"][0]
            assert failed["status"] == "error"
            assert failed["error"] == "Fit failed"
            append_note(report / "data" / "rabi-0", "Investigating failure", None)
            assert socket.receive_json()["type"] == "metadata"
            update = socket.receive_json()
            protocol = next(p for p in update["protocols"] if p["id"] == "rabi-0")
            assert protocol["status"] == "error"
            assert protocol["error"] == "Fit failed"
            assert protocol["html"] == "cached"
            assert protocol["notes"][0]["content"] == "Investigating failure"
    assert calls == [["rabi-0"]]


def test_note_validation_matches_qibocal():
    note = Note(content=" finding ", timestamp=datetime.now(timezone.utc))
    assert note.content == "finding"
    with pytest.raises(ValidationError):
        Note(content="Finding", timestamp="2026-01-01T00:00:00")
    with pytest.raises(ValidationError):
        note.content = "Changed"
