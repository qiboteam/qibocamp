"""A report's plots generated a batch at a time, while someone looks at it.

A report opens with every protocol it ran: those already generated with their
outputs, the others as placeholders (`generator.report_overview`). This fills
the placeholders: one generation per report, shared by every page that has it
open, generates the missing protocols a batch at a time — those on screen
first — publishes each batch into the cache as it ends, and sends each
protocol to every page at once. Regenerating is the same queue: the protocols
asked for keep their outputs, marked, until their new ones arrive.

The generation stops after the batch under way once nobody looks; what it did
is cached, and the next page that opens the report goes on from there.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from qibocamp.logger import log_info, log_warning
from qibocamp.models import ProtocolDetail
from qibocamp.reports.generator import (
    generate_protocols,
    get_execution_order,
    missing_protocols,
    placeholder,
)

BATCH_SIZE = 6
"""Protocols per worker: Qibocal is imported once per batch, and the cache grows as each ends."""

_GENERATIONS: dict[str, Generation] = {}


def generation_for(report_dir: Path) -> Generation:
    """The one generation of a report, shared by every page that has it open."""
    key = str(report_dir.resolve())
    generation = _GENERATIONS.get(key)
    if generation is None:
        generation = _GENERATIONS[key] = Generation(report_dir, key)
    return generation


def _payload(protocol: ProtocolDetail) -> dict:
    return protocol.model_dump(mode="json")


class Generation:
    def __init__(self, report_dir: Path, key: str):
        self.report_dir = report_dir
        self.key = key
        self.queue: list[str] = []
        """What is left to generate, in execution order."""
        self.batch: list[str] = []
        """What the worker is generating now."""
        self.regenerating: set[str] = set()
        """Queued protocols that have outputs already, shown until replaced."""
        self.visible: list[str] = []
        """The protocols on screen, last said by a page: generated first."""
        self.listeners: set[asyncio.Queue] = set()
        self.task: asyncio.Task | None = None

    # --- Pages ------------------------------------------------------------

    async def subscribe(self, inbox: asyncio.Queue) -> None:
        """A page opened the report: tell it what is under way, and generate what is missing."""
        self.listeners.add(inbox)
        missing = await asyncio.get_running_loop().run_in_executor(
            None, missing_protocols, self.report_dir
        )
        self._add(missing)
        inbox.put_nowait(self._state())
        if not self.queue and not self.batch:
            inbox.put_nowait({"type": "done"})
        self._start()

    def unsubscribe(self, inbox: asyncio.Queue) -> None:
        self.listeners.discard(inbox)
        self._forget_if_idle()

    def show(self, ids: list[str]) -> None:
        """The protocols a page has on screen now: they go first."""
        self.visible = [str(task_id) for task_id in ids][:50]

    async def regenerate(self, ids: list[str] | None) -> None:
        """Generate protocols again, all of them when `ids` is None; they keep their outputs meanwhile."""
        order = await asyncio.get_running_loop().run_in_executor(
            None, get_execution_order, self.report_dir
        )
        wanted = order if ids is None else [task_id for task_id in order if task_id in set(ids)]
        # Asked for anew: a protocol in the batch under way goes again after it.
        self.queue = [task_id for task_id in self.queue if task_id not in wanted]
        self.queue.extend(wanted)
        self.regenerating.update(wanted)
        self._broadcast(self._state())
        self._start()

    # --- The work ---------------------------------------------------------

    def _add(self, ids: list[str]) -> None:
        for task_id in ids:
            if task_id not in self.queue and task_id not in self.batch:
                self.queue.append(task_id)

    def _state(self) -> dict:
        return {
            "type": "queued",
            "ids": [*self.batch, *self.queue],
            "regenerating": sorted(self.regenerating),
            "generating": list(self.batch),
        }

    def _next_batch(self) -> list[str]:
        first = [task_id for task_id in self.visible if task_id in self.queue]
        rest = [task_id for task_id in self.queue if task_id not in first]
        batch = [*first, *rest][:BATCH_SIZE]
        self.queue = [task_id for task_id in self.queue if task_id not in batch]
        return batch

    def _start(self) -> None:
        if self.queue and (self.task is None or self.task.done()):
            self.task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while self.queue and self.listeners:
                self.batch = self._next_batch()
                self._broadcast(self._state())
                log_info(f"Generating {', '.join(self.batch)} of '{self.report_dir.name}'...")
                try:
                    protocols = await loop.run_in_executor(
                        None, generate_protocols, self.report_dir, list(self.batch)
                    )
                except Exception as error:  # noqa: BLE001 - a batch failing is the page's to see
                    log_warning(f"Plot generation of '{self.report_dir.name}' failed: {error}")
                    protocols = [
                        placeholder(self.report_dir, task_id).model_copy(
                            update={"status": "error", "error": f"Plot generation failed: {error}"}
                        )
                        for task_id in self.batch
                    ]
                for protocol in protocols:
                    self.regenerating.discard(protocol.id)
                    self._broadcast({"type": "protocol", "protocol": _payload(protocol)})
                self.batch = []
            if not self.queue:
                self.regenerating.clear()
                self._broadcast({"type": "done"})
        finally:
            self.batch = []
            # This task is about to end: idle, unless a page is still there.
            if not self.listeners and _GENERATIONS.get(self.key) is self:
                del _GENERATIONS[self.key]

    def _broadcast(self, message: dict) -> None:
        for inbox in list(self.listeners):
            inbox.put_nowait(message)

    def _forget_if_idle(self) -> None:
        # Nobody looks and nothing runs: what was asked for is dropped with it.
        idle = not self.listeners and (self.task is None or self.task.done())
        if idle and _GENERATIONS.get(self.key) is self:
            del _GENERATIONS[self.key]
