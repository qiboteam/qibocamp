"""Reports open at once, and their plots are generated a batch at a time (#98)."""

import asyncio
import json
from urllib.parse import quote

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from qibocamp import auth
from qibocamp.models import ProtocolDetail
from qibocamp.reports import generator, progressive
from qibocamp.web import api
from qibocamp.web.websocket import handle_report_websocket

TASKS = [f"rabi-{i}" for i in range(5)]


@pytest.fixture
def report(tmp_path, monkeypatch):
    directory = tmp_path / "run"
    directory.mkdir()
    (directory / "meta.json").write_text('{"author": "test", "stats": {}}')
    (directory / "history.json").write_text(json.dumps(TASKS))
    for task in TASKS:
        (directory / "data" / task).mkdir(parents=True)
        (directory / "data" / task / "data.json").write_text('{"value": 1}')
    api.set_report_root(tmp_path, is_original=True)
    auth.set_auth_enabled(False)
    monkeypatch.setattr(progressive, "BATCH_SIZE", 2)
    progressive._GENERATIONS.clear()
    yield directory
    auth.set_auth_enabled(None)


def generated(calls, fail=()):
    """A stand-in for the worker: outputs named after the batch, failures where asked."""

    def generate(report_dir, ids=None):
        calls.append(list(ids))
        return [
            ProtocolDetail(id=task, name=task, html=f"plot of {task}", figures=[{"id": task}])
            if task not in fail
            else ProtocolDetail(id=task, name=task, status="error", error="fit failed")
            for task in ids
        ], None

    return generate


def test_opening_a_report_generates_nothing(report, monkeypatch):
    monkeypatch.setattr(generator, "_generate_qibocal_protocols", lambda *_: pytest.fail("generated on open"))
    overview = generator.report_overview(report)
    assert [p.id for p in overview] == TASKS
    assert {p.status for p in overview} == {generator.PENDING}
    query = '{ report(id: "run") { protocols_detail { id status } } }'
    with TestClient(api.app) as client:
        body = client.post("/api/graphql", json={"query": query}).json()
        assert [p["status"] for p in body["data"]["report"]["protocols_detail"]] == ["pending"] * 5
        outputs = client.post("/api/graphql", json={"query": '{ protocol(report_id: "run", protocol_id: "rabi-3") { status html } }'}).json()
        assert outputs["data"]["protocol"] == {"status": "pending", "html": ""}


def test_batches_go_on_screen_first_and_are_cached_as_they_end(report, monkeypatch):
    calls = []
    monkeypatch.setattr(generator, "_generate_qibocal_protocols", generated(calls))

    async def scenario():
        generation = progressive.generation_for(report)
        generation.show(["rabi-3", "rabi-4"])
        inbox = asyncio.Queue()
        await generation.subscribe(inbox)
        messages = []
        while not messages or messages[-1]["type"] != "done":
            messages.append(await asyncio.wait_for(inbox.get(), 5))
        generation.unsubscribe(inbox)
        return messages

    messages = asyncio.run(scenario())
    assert calls == [["rabi-3", "rabi-4"], ["rabi-0", "rabi-1"], ["rabi-2"]], "on screen first, then in order"
    sent = [m["protocol"]["id"] for m in messages if m["type"] == "protocol"]
    assert sent == ["rabi-3", "rabi-4", "rabi-0", "rabi-1", "rabi-2"], "each protocol as soon as its batch ends"
    assert messages[0] == {"type": "queued", "ids": TASKS, "regenerating": [], "generating": []}
    cached = {p.id: p for p in generator.load_cached_protocols(report)}
    assert set(cached) == set(TASKS) and cached["rabi-3"].html == "plot of rabi-3"
    assert generator.missing_protocols(report) == []
    assert set(json.loads((report / "report" / ".live-inputs.json").read_text())) == set(TASKS)
    assert progressive._GENERATIONS == {}, "nothing left behind once done and closed"


def test_an_interrupted_generation_resumes_where_it_stopped(report, monkeypatch):
    calls = []
    monkeypatch.setattr(generator, "_generate_qibocal_protocols", generated(calls))

    async def first_batch_then_leave():
        generation = progressive.generation_for(report)
        inbox = asyncio.Queue()
        await generation.subscribe(inbox)
        # Gone while the first batch runs: it ends, and nothing follows.
        while not (await asyncio.wait_for(inbox.get(), 5)).get("generating"):
            pass
        generation.unsubscribe(inbox)
        await generation.task

    asyncio.run(first_batch_then_leave())
    assert calls == [["rabi-0", "rabi-1"]], "nobody looks: no further batch"
    assert generator.missing_protocols(report) == ["rabi-2", "rabi-3", "rabi-4"]


def test_two_pages_share_one_generation(report, monkeypatch):
    calls = []
    monkeypatch.setattr(generator, "_generate_qibocal_protocols", generated(calls))

    async def scenario():
        one, two = asyncio.Queue(), asyncio.Queue()
        await progressive.generation_for(report).subscribe(one)
        await progressive.generation_for(report).subscribe(two)
        for inbox in (one, two):
            seen = []
            while not seen or seen[-1]["type"] != "done":
                seen.append(await asyncio.wait_for(inbox.get(), 5))
            assert sorted(m["protocol"]["id"] for m in seen if m["type"] == "protocol") == TASKS

    asyncio.run(scenario())
    assert sorted(task for batch in calls for task in batch) == TASKS, "each protocol generated once"


def test_without_qibocal_nothing_is_cached_and_each_card_says_why(report, monkeypatch):
    monkeypatch.setattr(
        generator, "_generate_qibocal_protocols",
        lambda *_: (None, generator.GenerationError("Qibocal is not installed", "qibocal_not_installed")),
    )
    answered = generator.generate_protocols(report, ["rabi-0", "rabi-1"])
    assert [(p.status, p.error_code) for p in answered] == [("error", "qibocal_not_installed")] * 2
    assert generator.missing_protocols(report) == TASKS, "tried again once Qibocal is there"
    shown = generator.get_report_protocols(report)
    assert [p.id for p in shown] == TASKS and {p.status for p in shown} == {"error"}


def test_regenerating_keeps_the_outputs_a_failure_would_lose(report, monkeypatch):
    calls = []
    monkeypatch.setattr(generator, "_generate_qibocal_protocols", generated(calls))
    generator.generate_protocols(report, TASKS)
    monkeypatch.setattr(generator, "_generate_qibocal_protocols", generated(calls, fail={"rabi-1"}))

    async def regenerate():
        generation = progressive.generation_for(report)
        inbox = asyncio.Queue()
        await generation.subscribe(inbox)
        assert (await inbox.get())["ids"] == []
        assert (await inbox.get())["type"] == "done"
        await generation.regenerate(["rabi-1", "rabi-2"])
        messages = []
        while not messages or messages[-1]["type"] != "done":
            messages.append(await asyncio.wait_for(inbox.get(), 5))
        return messages

    messages = asyncio.run(regenerate())
    assert messages[0]["regenerating"] == ["rabi-1", "rabi-2"], "marked while redone"
    failed = next(m["protocol"] for m in messages if m["type"] == "protocol" and m["protocol"]["id"] == "rabi-1")
    assert failed["status"] == "error" and failed["html"] == "plot of rabi-1", "the old outputs, marked"
    cached = {p.id: p for p in generator.load_cached_protocols(report)}
    assert cached["rabi-1"].status == "success", "the cache keeps what worked"


def test_a_viewer_cannot_regenerate(report):
    app = FastAPI()

    @app.websocket("/ws/{report_id}")
    async def socket(websocket: WebSocket, report_id: str):
        await handle_report_websocket(websocket, report_id, report.parent, can_edit=False)

    generator.write_protocol_cache(report, [ProtocolDetail(id=t, name=t) for t in TASKS])
    with TestClient(app) as client, client.websocket_connect(f"/ws/{quote('run')}") as ws:
        assert ws.receive_json()["type"] == "metadata"
        assert ws.receive_json()["type"] == "queued"
        assert ws.receive_json()["type"] == "done"
        ws.send_json({"type": "regenerate", "ids": None})
        assert ws.receive_json() == {"type": "error", "message": "Regenerating plots needs editor access."}
