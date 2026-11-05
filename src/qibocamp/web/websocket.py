"""The report socket: a report's metadata, then its plots as they are generated.

The page has the report's protocols already, cached ones with their outputs
and the others as placeholders (GraphQL `protocols_detail`). On this socket:

- the server sends `metadata`, then `queued` — what is left to generate,
  what is being regenerated, what the worker has now — then one `protocol`
  message per protocol generated, and `done` once nothing is left;
- the page sends `{"type": "visible", "ids": [...]}`, the placeholders on
  screen, generated first; `{"type": "regenerate", "ids": [...] | null}`, to
  generate some protocols again, or all of them (editors only); and `ping`.

Every page that has the report open shares one generation
(`reports/progressive.py`).
"""

import asyncio
import json
from pathlib import Path

from fastapi import HTTPException, WebSocket, WebSocketDisconnect

from qibocamp.logger import log_error, log_info, log_warning
from qibocamp.reports.archive import resolve_report_dir
from qibocamp.reports.notes import NotesError
from qibocamp.reports.progressive import generation_for
from qibocamp.reports.scanner import get_report_detail


async def _forward(websocket: WebSocket, inbox: asyncio.Queue) -> None:
    """Everything for the page, in order, from one task: the socket has one writer."""
    while True:
        message = await inbox.get()
        if isinstance(message, str):
            await websocket.send_text(message)
        else:
            await websocket.send_json(message)


async def handle_report_websocket(
    websocket: WebSocket, report_id: str, root_dir: Path, can_edit: bool = True
) -> None:
    """Send a report's metadata, then its protocols as they are generated."""
    await websocket.accept()
    log_info(f"WebSocket client connected for report: '{report_id}'")

    try:
        target_dir = resolve_report_dir(root_dir, report_id)
    except HTTPException:
        target_dir = None

    if target_dir is None:
        log_error(f"Report '{report_id}' not found for WebSocket client")
        await websocket.send_json(
            {"type": "error", "message": f"Report '{report_id}' not found"}
        )
        await websocket.close()
        return

    inbox: asyncio.Queue = asyncio.Queue()
    generation = None
    sender = None
    try:
        detail = get_report_detail(root_dir, report_id)
        if detail:
            await websocket.send_json(
                {"type": "metadata", "report": detail.model_dump(mode="json")}
            )
        sender = asyncio.create_task(_forward(websocket, inbox))
        generation = generation_for(target_dir)
        await generation.subscribe(inbox)

        while True:
            text = await websocket.receive_text()
            if text == "ping":
                inbox.put_nowait("pong")
                continue
            try:
                message = json.loads(text)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            kind = message.get("type")
            ids = message.get("ids")
            if kind == "visible" and isinstance(ids, list):
                generation.show(ids)
            elif kind == "regenerate":
                if not can_edit:
                    inbox.put_nowait({"type": "error", "message": "Regenerating plots needs editor access."})
                elif ids is None or isinstance(ids, list):
                    await generation.regenerate(None if ids is None else [str(i) for i in ids])
    except WebSocketDisconnect:
        log_info(f"WebSocket client disconnected for report: '{report_id}'")
    except NotesError as err:
        log_warning(f"Could not load notes for '{report_id}': {err}")
        await websocket.send_json({"type": "error", "message": str(err)})
        await websocket.close()
    except (RuntimeError, OSError) as err:
        log_warning(f"WebSocket session ended for '{report_id}': {err}")
    finally:
        if generation is not None:
            generation.unsubscribe(inbox)
        if sender is not None:
            sender.cancel()
