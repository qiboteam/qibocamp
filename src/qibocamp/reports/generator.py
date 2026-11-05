"""Report generation and caching logic for qibocamp (Issue #10)."""

import json
import shutil
import subprocess
import sys
import threading
import uuid
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import Any

from qibocamp.reports.live_inputs import protocol_inputs, write_input_cache
from qibocamp.logger import log_info, log_success, log_warning
from qibocamp.models import ProtocolDetail
from qibocamp.reports.notes import attach_protocol_notes
from qibocamp.qibocal_environment import (
    GENERATION_TIMEOUT,
    EnvironmentOperationError,
    generation_environment,
    get_qibocal_status,
)

_REPORT_LOCKS = weakref.WeakValueDictionary()
_REPORT_LOCKS_GUARD = threading.Lock()


def report_generation_lock(report_dir: Path):
    """Serialize cache reads and generation for the same report."""
    key = str(report_dir.resolve())
    with _REPORT_LOCKS_GUARD:
        lock = _REPORT_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _REPORT_LOCKS[key] = lock
        return lock


def write_protocol_cache(report_dir: Path, protocols: list[ProtocolDetail]) -> None:
    """Atomically publish a complete cache, including incremental updates."""
    report_path = report_dir / "report"
    report_path.mkdir(parents=True, exist_ok=True)
    temporary = report_path / f".protocols-{uuid.uuid4().hex}.json"
    try:
        temporary.write_text(
            json.dumps(
                [p.model_dump(exclude={"notes"}) for p in protocols],
                default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o),
            ),
            encoding="utf-8",
        )
        temporary.replace(report_path / "protocols.json")
    finally:
        temporary.unlink(missing_ok=True)


class GenerationError(str):
    """Why Qibocal produced no outputs: the message, and a code the dashboard acts on, when one applies."""

    def __new__(cls, message: str, error_code: str | None = None):
        instance = super().__new__(cls, message)
        instance.error_code = error_code
        return instance


def has_cached_report(report_dir: Path) -> bool:
    """Check whether a pre-cached report folder exists."""
    report_path = report_dir / "report"
    if not report_path.is_dir():
        return False
    # Check if there are json files inside
    return any(not path.name.startswith(".") for path in report_path.glob("*.json"))


def format_execution_time(seconds: float | None) -> str:
    """Format execution duration in seconds to a human-readable string."""
    if seconds is None:
        return "N/A"
    try:
        s = float(seconds)
    except (ValueError, TypeError):
        return str(seconds)
    if s < 0:
        return "N/A"
    if s < 60:
        return f"{s:.2f}s"
    minutes = int(s // 60)
    rem_seconds = int(s % 60)
    return f"{minutes}m {rem_seconds}s"


def _extract_protocol_timing_map(report_dir: Path) -> dict[str, str]:
    """Extract protocol execution times from meta.json stats."""
    timing_map: dict[str, str] = {}
    meta_path = report_dir / "meta.json"
    if not meta_path.is_file():
        meta_path = report_dir / "report" / "meta.json"
    if not meta_path.is_file():
        return timing_map

    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        stats = meta.get("stats", {})
        if isinstance(stats, dict):
            for proto_id, p_stat in stats.items():
                if isinstance(p_stat, dict):
                    acq = p_stat.get("acquisition", 0.0) or 0.0
                    fit = p_stat.get("fit", 0.0) or 0.0
                    total = acq + fit
                    if total > 0:
                        timing_map[proto_id] = format_execution_time(total)
                elif isinstance(p_stat, (int, float)) and p_stat > 0:
                    timing_map[proto_id] = format_execution_time(p_stat)
    except (json.JSONDecodeError, OSError, TypeError) as err:
        log_warning(f"Error reading timing from meta.json: {err}")

    return timing_map


def get_execution_order(report_dir: Path) -> list[str]:
    """Retrieve protocol execution order for a report (Issue #3).

    Authoritative source: history.json
    Effective backup: meta.json (stats keys are sorted by execution order).
    """
    # 1. Authoritative source: history.json
    for path in [report_dir / "history.json", report_dir / "report" / "history.json"]:
        if path.is_file():
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    order = []
                    for item in data:
                        if isinstance(item, str) and item.strip():
                            order.append(item.strip())
                        elif isinstance(item, dict):
                            t_id = (
                                item.get("id")
                                or item.get("task")
                                or item.get("name")
                            )
                            if t_id:
                                order.append(str(t_id).strip())
                    if order:
                        return order
                elif isinstance(data, dict) and data:
                    return [str(k).strip() for k in data.keys()]
            except (json.JSONDecodeError, OSError, TypeError) as err:
                log_warning(f"Error reading history.json for execution order: {err}")

    # 2. Effective backup: meta.json stats keys
    for path in [report_dir / "meta.json", report_dir / "report" / "meta.json"]:
        if path.is_file():
            try:
                with open(path, encoding="utf-8") as f:
                    meta = json.load(f)
                stats = meta.get("stats")
                if isinstance(stats, dict) and stats:
                    return [str(k).strip() for k in stats.keys()]
                protos = meta.get("protocols") or meta.get("actions")
                if isinstance(protos, list) and protos:
                    return [str(p).strip() for p in protos if p]
            except (json.JSONDecodeError, OSError, TypeError) as err:
                log_warning(f"Error reading meta.json for execution order: {err}")

    return []


def _normalize_key(k: str) -> str:
    """Normalize a routine ID or protocol name for matching."""
    return k.replace("-", "_").lower().strip()


def _base_key(k: str) -> str:
    """Extract base protocol name without numeric task index."""
    norm = _normalize_key(k)
    parts = norm.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return norm


def sort_protocols_by_execution_order(
    protocols: list[Any],
    report_dir: Path | None = None,
    order: list[str] | None = None,
) -> list[Any]:
    """Sort protocol objects or names by their execution order (Issue #3).

    Uses history.json as authoritative source, with meta.json stats as backup.
    """
    if not protocols or len(protocols) <= 1:
        return protocols

    if order is None and report_dir is not None:
        order = get_execution_order(report_dir)

    if not order:
        return protocols

    def get_id(p: Any) -> str:
        if hasattr(p, "id"):
            return str(p.id)
        if isinstance(p, dict):
            return str(p.get("id") or p.get("name") or "")
        return str(p)

    used_slots: set[int] = set()
    assigned_slots: dict[int, int] = {}

    # Pass 1: exact matches
    for idx, p in enumerate(protocols):
        pid = get_id(p)
        for slot_idx, ord_id in enumerate(order):
            if slot_idx not in used_slots and pid == ord_id:
                used_slots.add(slot_idx)
                assigned_slots[idx] = slot_idx
                break

    # Pass 2: normalized matches (handling dashes vs underscores)
    for idx, p in enumerate(protocols):
        if idx in assigned_slots:
            continue
        pid_norm = _normalize_key(get_id(p))
        for slot_idx, ord_id in enumerate(order):
            if slot_idx not in used_slots and pid_norm == _normalize_key(ord_id):
                used_slots.add(slot_idx)
                assigned_slots[idx] = slot_idx
                break

    # Pass 3: base key matches (ignoring trailing execution index)
    for idx, p in enumerate(protocols):
        if idx in assigned_slots:
            continue
        pid_base = _base_key(get_id(p))
        for slot_idx, ord_id in enumerate(order):
            if slot_idx not in used_slots and pid_base == _base_key(ord_id):
                used_slots.add(slot_idx)
                assigned_slots[idx] = slot_idx
                break

    # Pass 4: prefix / substring matches
    for idx, p in enumerate(protocols):
        if idx in assigned_slots:
            continue
        pid_base = _base_key(get_id(p))
        for slot_idx, ord_id in enumerate(order):
            ord_base = _base_key(ord_id)
            if slot_idx not in used_slots and (
                pid_base.startswith(ord_base) or ord_base.startswith(pid_base)
            ):
                used_slots.add(slot_idx)
                assigned_slots[idx] = slot_idx
                break

    def sort_key(item: tuple[int, Any]) -> tuple[int, int]:
        orig_idx, _ = item
        slot = assigned_slots.get(orig_idx, len(order))
        return (slot, orig_idx)

    sorted_pairs = sorted(enumerate(protocols), key=sort_key)
    return [p for _, p in sorted_pairs]


def load_cached_protocols(report_dir: Path) -> list[ProtocolDetail]:
    """Load pre-cached report protocol details without importing qibocal."""
    report_path = report_dir / "report"
    protocols: list[ProtocolDetail] = []

    if not report_path.is_dir():
        return protocols

    log_info(f"Loading pre-cached report artifacts for '{report_dir.name}'...")
    timing_map = _extract_protocol_timing_map(report_dir)

    # Check for protocols.json first
    single_file = report_path / "protocols.json"
    if single_file.exists():
        try:
            with open(single_file, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    protocols = [ProtocolDetail(**p) for p in data]
                    for proto in protocols:
                        if not proto.execution_time or proto.execution_time == "N/A":
                            proto.execution_time = (
                                timing_map.get(proto.id)
                                or timing_map.get(proto.id.replace("-", "_"))
                                or timing_map.get(proto.id.replace("_", "-"))
                                or "N/A"
                            )
                    protocols = sort_protocols_by_execution_order(protocols, report_dir)
                    log_success(
                        f"Loaded {len(protocols)} pre-cached protocol(s) "
                        f"for '{report_dir.name}'"
                    )
                    return attach_protocol_notes(report_dir, protocols)
        except (json.JSONDecodeError, OSError, TypeError) as err:
            log_warning(f"Error parsing protocols.json: {err}")

    # Otherwise read all *.json files except meta.json and history.json
    for json_file in report_path.glob("*.json"):
        if json_file.name.startswith(".") or json_file.name in (
            "meta.json", "history.json", "notes.json"
        ):
            continue
        try:
            with open(json_file, encoding="utf-8") as f:
                p_data = json.load(f)
                proto_obj = ProtocolDetail(**p_data)
                if not proto_obj.execution_time or proto_obj.execution_time == "N/A":
                    proto_obj.execution_time = (
                        timing_map.get(proto_obj.id)
                        or timing_map.get(proto_obj.id.replace("-", "_"))
                        or timing_map.get(proto_obj.id.replace("_", "-"))
                        or "N/A"
                    )
                protocols.append(proto_obj)
        except (json.JSONDecodeError, OSError, TypeError) as err:
            log_warning(f"Error reading {json_file.name}: {err}")

    protocols = sort_protocols_by_execution_order(protocols, report_dir)

    log_success(
        f"Loaded {len(protocols)} pre-cached protocol(s) for '{report_dir.name}'"
    )
    return attach_protocol_notes(report_dir, protocols)


def _generate_qibocal_protocols(
    report_dir: Path,
    protocol_ids: list[str] | None = None,
) -> tuple[list[ProtocolDetail] | None, GenerationError | None]:
    """Run Qibocal in a fresh interpreter, so version changes take effect immediately."""
    result_path = report_dir.resolve() / f".qibocamp-generation-{uuid.uuid4().hex}.json"
    try:
        with generation_environment():
            result = subprocess.run(
                [
                    sys.executable, "-m", "qibocamp.reports.generation_worker",
                    str(report_dir.resolve()), str(result_path),
                    *([json.dumps(protocol_ids)] if protocol_ids is not None else []),
                ],
                capture_output=True, text=True, timeout=GENERATION_TIMEOUT, check=False,
            )
            if result.returncode:
                output = (result.stderr or result.stdout or "No worker output.").strip()
                return None, GenerationError(f"Qibocal generation worker failed (exit {result.returncode}): {output[-2000:]}")
            with result_path.open(encoding="utf-8") as stream:
                payload = json.load(stream)
            protocols = payload["protocols"]
            error = payload.get("error")
            return (
                [ProtocolDetail(**protocol) for protocol in protocols]
                if protocols is not None else None,
                GenerationError(error, payload.get("error_code")) if error else None,
            )
    except subprocess.TimeoutExpired:
        return None, GenerationError(f"Qibocal plot generation timed out after {GENERATION_TIMEOUT} seconds.")
    except EnvironmentOperationError as error:
        return None, GenerationError(error.detail)
    except (OSError, ValueError, TypeError, KeyError) as error:
        return None, GenerationError(f"Qibocal generation worker could not return plot results: {error}")
    finally:
        try:
            result_path.unlink(missing_ok=True)
        except OSError as error:
            log_warning(f"Could not remove Qibocal worker result '{result_path}': {error}")


def normalize_targets(raw):
    """Targets as qibocal keys its data and results by them.

    `action.yml` is read with plain YAML, so a qubit pair comes back as a
    list — `[2, 3]` — while qibocal keys data and results by the tuple
    `(2, 3)`, and a list cannot be a key at all. Nested lists become nested
    tuples, as qibocal's own `_nested_list_to_tuples` does; everything else
    is left alone.
    """
    if isinstance(raw, list):
        return tuple(normalize_targets(item) for item in raw)
    return raw


def results_target(results, target):
    """The target as the results know it: a pair they only hold reversed, reversed.

    STOPGAP (#100), to remove once Qibocal is fixed: `snz_optimize` keys its
    results by the pair ordered by drive frequency, while its targets keep
    the pair as written, and Qibocal's report then plots it without the fit.
    A pair and its reverse may have distinct gates, so this is wrong in
    general; it holds while each pair is calibrated in one direction only.
    """
    if results is None or not (isinstance(target, tuple) and len(target) == 2):
        return target
    try:
        if target in results:
            return target
        reverse = target[::-1]
        return reverse if reverse in results else target
    except TypeError:
        return target


def target_label(target) -> str:
    """A target as a name reads: `2` for a qubit, `2-3` for a pair."""
    if isinstance(target, (list, tuple)):
        return "-".join(target_label(item) for item in target)
    return str(target)


def _generate_qibocal_protocols_native(
    report_dir: Path,
    protocol_ids: list[str] | None = None,
) -> tuple[list[ProtocolDetail] | None, GenerationError | None]:
    """Attempt to generate report protocols using native Qibocal if available.

    Returns:
        (protocols, None) if generation succeeded.
        (None, error_message) if Qibocal is not installed or generation failed.
    """
    try:
        from qibocal.auto.output import Output  # type: ignore
        if protocol_ids is not None:
            from qibocal.auto.task import Completed  # type: ignore
        from qibocal.cli.report import generate_figures_and_report  # type: ignore
    except ImportError as error:
        if (
            isinstance(error, ModuleNotFoundError)
            and error.name == "qibocal"
            and not get_qibocal_status().installed
        ):
            return None, GenerationError(
                "Qibocal is not installed in the environment. "
                "Install Qibocal to enable on-the-fly plot generation, "
                "or use pre-cached report outputs.",
                "qibocal_not_installed",
            )
        return None, GenerationError(f"Error importing Qibocal or its dependencies: {error}")
    except (AttributeError, RuntimeError, OSError) as err:
        return None, GenerationError(f"Error importing Qibocal: {err}")

    try:
        if protocol_ids is None:
            tasks = Output.load(report_dir).history.items()
        else:
            # Load only selected tasks; an unfinished sibling must not block updates.
            tasks = ((task_id, None) for task_id in protocol_ids)
        timing_map = _extract_protocol_timing_map(report_dir)
        generated: list[ProtocolDetail] = []
        for task_id, completed in tasks:
            task_str = str(task_id)
            if protocol_ids is not None:
                try:
                    completed = Completed.load(report_dir / "data" / task_str)
                except (
                    AttributeError,
                    RuntimeError,
                    ValueError,
                    TypeError,
                    KeyError,
                    OSError,
                    ImportError,
                ) as error:
                    message = f"Could not load routine '{task_str}': {error}"
                    log_warning(message)
                    generated.append(
                        ProtocolDetail(
                            id=task_str,
                            name=task_str.replace("_", " ").title(),
                            category="calibration",
                            execution_time=timing_map.get(task_str, "N/A"),
                            status="error",
                            error=message,
                        )
                    )
                    continue
            clean_name = (
                getattr(completed.task, "operation_name", task_str)
                .replace("_", " ")
                .title()
            )
            # A pair target is a list off the YAML; qibocal wants the tuple.
            targets = [normalize_targets(t) for t in getattr(completed.task, "targets", []) or []]
            all_figs: list[dict] = []
            html_parts: list[str] = []
            target_errors: list[str] = []

            if targets:
                for target in targets:
                    try:
                        figs, fit_rep = generate_figures_and_report(
                            completed, results_target(completed.results, target)
                        )
                        for fig in figs:
                            if hasattr(fig, "to_json"):
                                fig_dict = json.loads(fig.to_json())
                            elif hasattr(fig, "to_dict"):
                                fig_dict = fig.to_dict()
                            elif isinstance(fig, dict):
                                fig_dict = fig
                            else:
                                continue
                            if "id" not in fig_dict:
                                fig_dict["id"] = f"{task_str}-q{target_label(target)}"
                            all_figs.append(fig_dict)
                        if fit_rep:
                            html_parts.append(str(fit_rep))
                    except (
                        AttributeError,
                        RuntimeError,
                        ValueError,
                        TypeError,
                        KeyError,
                        OSError,
                        ImportError,
                    ) as r_err:
                        target_errors.append(f"Target {target_label(target)}: {r_err}")
                        log_warning(
                            f"Error generating target {target_label(target)} for "
                            f"'{task_str}': {r_err}"
                        )
            else:
                try:
                    figs, fit_rep = generate_figures_and_report(completed, None)
                    for fig in figs:
                        if hasattr(fig, "to_json"):
                            fig_dict = json.loads(fig.to_json())
                        elif hasattr(fig, "to_dict"):
                            fig_dict = fig.to_dict()
                        elif isinstance(fig, dict):
                            fig_dict = fig
                        else:
                            continue
                        all_figs.append(fig_dict)
                    if fit_rep:
                        html_parts.append(str(fit_rep))
                except (
                    AttributeError,
                    RuntimeError,
                    ValueError,
                    TypeError,
                    KeyError,
                    OSError,
                    ImportError,
                ) as r_err:
                    target_errors.append(str(r_err))
                    log_warning(
                        f"Error generating target-less routine '{task_str}': {r_err}"
                    )

            has_output = bool(all_figs or html_parts)
            exec_time = (
                timing_map.get(task_str)
                or timing_map.get(task_str.replace("-", "_"))
                or timing_map.get(task_str.replace("_", "-"))
                or "N/A"
            )
            generated.append(
                ProtocolDetail(
                    id=task_str,
                    name=clean_name,
                    category="calibration",
                    execution_time=exec_time,
                    status="success" if has_output and not target_errors else "error",
                    error=None
                    if has_output and not target_errors
                    else (
                        f"Qibocal could not produce all outputs for routine '{task_str}'."
                        + (" " + "; ".join(target_errors) if target_errors else "")
                    ),
                    html="\n".join(html_parts),
                    figures=all_figs,
                )
            )
        return generated, None
    except (
        AttributeError,
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        OSError,
        ImportError,
    ) as err:
        log_warning(f"Qibocal report generation failed for '{report_dir.name}': {err}")
        return None, GenerationError(f"Qibocal failed to generate report: {err}")


def generate_report_on_the_fly(
    report_dir: Path, progress_callback: Callable[[int, int, str], None] | None = None
) -> list[ProtocolDetail]:
    """On-the-fly generation of protocol outputs.

    If Qibocal is available and generation succeeds, populates the report/ cache
    directory. If Qibocal is not available or generation fails, returns protocol
    details containing error messages for the dashboard.
    """
    log_info(f"Generating protocol plots on-the-fly for report '{report_dir.name}'...")

    try:
        inputs = protocol_inputs(report_dir)
    except (OSError, ValueError, TypeError) as error:
        log_warning(f"Could not fingerprint report inputs: {error}")
        inputs = None
    native_protocols, error_reason = _generate_qibocal_protocols(report_dir)

    if native_protocols:
        protocols = sort_protocols_by_execution_order(native_protocols, report_dir)
        if not any(p.status == "success" for p in native_protocols):
            log_warning(
                f"Qibocal failed to generate outputs for '{report_dir.name}': "
                + "; ".join(p.error or p.id for p in protocols)
            )
            return attach_protocol_notes(report_dir, protocols)
        log_success(
            f"Generated {len(native_protocols)} protocol(s) "
            "using native Qibocal engine."
        )
        # Cache into report/ directory
        report_path = report_dir / "report"
        report_path.mkdir(parents=True, exist_ok=True)

        meta_summary = {
            "protocols": [p.id for p in protocols],
            "generated_by": "qibocamp-server",
            "count": len(protocols),
        }
        with open(report_path / "meta.json", "w", encoding="utf-8") as f:
            json.dump(meta_summary, f, indent=2)

        for p in protocols:
            with open(report_path / f"{p.id}.json", "w", encoding="utf-8") as f:
                json.dump(
                    p.model_dump(exclude={"notes"}),
                    f,
                    indent=2,
                    default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o),
                )

        write_protocol_cache(report_dir, protocols)
        if inputs is not None:
            write_input_cache(
                report_dir,
                {
                    p.id: inputs[p.id] for p in protocols
                    if p.status == "success" and p.id in inputs
                },
            )
        log_success(
            f"Report '{report_dir.name}' plots generated and cached "
            f"successfully ({len(protocols)} routines)."
        )
        return attach_protocol_notes(report_dir, protocols)

    # Qibocal cannot generate the plots
    discovered_protocols: list[str] = []
    exec_order = get_execution_order(report_dir)
    if exec_order:
        discovered_protocols = list(exec_order)
    else:
        data_dir = report_dir / "data"
        if data_dir.is_dir():
            for p in sorted(data_dir.iterdir()):
                if p.is_dir() and not p.name.startswith("."):
                    discovered_protocols.append(p.name)

        if not discovered_protocols:
            meta_file = report_dir / "meta.json"
            if meta_file.exists():
                try:
                    with open(meta_file, encoding="utf-8") as f:
                        m = json.load(f)
                        discovered_protocols = m.get("protocols") or m.get("actions") or []
                except (json.JSONDecodeError, OSError):
                    pass

    if not discovered_protocols:
        discovered_protocols = [report_dir.name]

    err_msg = error_reason or (
        "Qibocal did not produce protocol outputs for this report."
    )

    protocols = []
    timing_map = _extract_protocol_timing_map(report_dir)
    total = len(discovered_protocols)
    for idx, proto in enumerate(discovered_protocols):
        clean_name = proto.replace("_", " ").title()
        if progress_callback:
            try:
                progress_callback(idx + 1, total, clean_name)
            except (TypeError, RuntimeError, OSError):
                pass
        exec_time = (
            timing_map.get(proto)
            or timing_map.get(proto.replace("-", "_"))
            or timing_map.get(proto.replace("_", "-"))
            or "N/A"
        )
        protocols.append(
            ProtocolDetail(
                id=proto,
                name=clean_name,
                category="calibration",
                execution_time=exec_time,
                status="error",
                error=err_msg,
                error_code=error_reason.error_code if error_reason else None,
                html="",
                figures=[],
            )
        )

    protocols = sort_protocols_by_execution_order(protocols, report_dir)
    log_warning(f"Plots could not be generated for '{report_dir.name}': {err_msg}")
    return attach_protocol_notes(report_dir, protocols)


# The pre-cached outputs of the reports looked at most recently, parsed once:
# a report's figures are asked for by the detail (to count them), by the
# protocols endpoint, and now protocol by protocol over GraphQL, and parsing
# megabytes of Plotly JSON for each of those is what made a report slow to
# open. An entry is keyed by the files it was read from and their mtimes, so
# a regenerated report is read again; the notes are attached afresh at every
# use, since they change without the outputs changing.
_OUTPUT_CACHE: dict[str, tuple[tuple, list[ProtocolDetail]]] = {}
_OUTPUT_CACHE_LIMIT = 16


def _outputs_key(report_dir: Path) -> tuple | None:
    report_path = report_dir / "report"
    try:
        files = sorted(
            (entry.name, entry.stat().st_mtime_ns)
            for entry in report_path.iterdir()
            if entry.suffix == ".json" and not entry.name.startswith(".")
        )
        return (report_path.stat().st_mtime_ns, tuple(files))
    except OSError:
        return None


def cached_protocols(report_dir: Path) -> list[ProtocolDetail]:
    """`load_cached_protocols`, remembered while the cached files are unchanged."""
    key = _outputs_key(report_dir)
    slot = str(report_dir.resolve())
    entry = _OUTPUT_CACHE.get(slot)
    if key is not None and entry is not None and entry[0] == key:
        return attach_protocol_notes(report_dir, entry[1])
    protocols = load_cached_protocols(report_dir)
    if key is not None:
        if slot not in _OUTPUT_CACHE and len(_OUTPUT_CACHE) >= _OUTPUT_CACHE_LIMIT:
            _OUTPUT_CACHE.pop(next(iter(_OUTPUT_CACHE)))
        _OUTPUT_CACHE[slot] = (key, protocols)
    return protocols


def forget_cached_protocols(report_dir: Path) -> None:
    _OUTPUT_CACHE.pop(str(report_dir.resolve()), None)


# --- The protocols as far as they are generated -----------------------------
#
# A report opens with every protocol it ran, at once: those already generated
# with their outputs, the others as placeholders, `pending`, which the report
# socket fills batch by batch (`reports/progressive.py`). Nothing here imports
# Qibocal or generates anything.

PENDING = "pending"
"""The status of a protocol whose outputs are not generated yet."""


def _timing(timing_map: dict[str, str], task_id: str) -> str:
    return (
        timing_map.get(task_id)
        or timing_map.get(task_id.replace("-", "_"))
        or timing_map.get(task_id.replace("_", "-"))
        or timing_map.get(task_id.rsplit("-", 1)[0])
        or "N/A"
    )


def placeholder(report_dir: Path, task_id: str, timing_map: dict[str, str] | None = None) -> ProtocolDetail:
    """A protocol not generated yet: named, timed, without outputs."""
    timing_map = _extract_protocol_timing_map(report_dir) if timing_map is None else timing_map
    return ProtocolDetail(
        id=task_id,
        name=task_id.replace("_", " ").title(),
        category="calibration",
        execution_time=_timing(timing_map, task_id),
        status=PENDING,
    )


def missing_protocols(report_dir: Path) -> list[str]:
    """The tasks of a report whose outputs are not cached, in execution order."""
    cached = {p.id for p in load_cached_protocols(report_dir)} if has_cached_report(report_dir) else set()
    return [task_id for task_id in get_execution_order(report_dir) if task_id not in cached]


def report_overview(report_dir: Path) -> list[ProtocolDetail]:
    """Every protocol of a report, in execution order: cached, or a placeholder."""
    cached = cached_protocols(report_dir) if has_cached_report(report_dir) else []
    known = {p.id for p in cached}
    timing_map = _extract_protocol_timing_map(report_dir)
    missing = [
        placeholder(report_dir, task_id, timing_map)
        for task_id in get_execution_order(report_dir)
        if task_id not in known
    ]
    return sort_protocols_by_execution_order(
        [*cached, *attach_protocol_notes(report_dir, missing)], report_dir
    )


def merge_generated(report_dir: Path, generated: list[ProtocolDetail]) -> list[ProtocolDetail]:
    """Publish a batch of generated protocols into the cache, as each batch ends.

    A success replaces what was there, and its input fingerprint is recorded.
    A failure is cached only where nothing was: a protocol that had outputs
    keeps them, and the failure is returned, marked, for the page to show.
    Returns the protocols as the page should show them, notes attached.
    """
    with report_generation_lock(report_dir):
        try:
            inputs = protocol_inputs(report_dir)
        except (OSError, ValueError, TypeError) as error:
            log_warning(f"Could not fingerprint report inputs: {error}")
            inputs = {}
        cached = {p.id: p for p in load_cached_protocols(report_dir)} if has_cached_report(report_dir) else {}
        known_path = report_dir / "report" / ".live-inputs.json"
        try:
            known = json.loads(known_path.read_text(encoding="utf-8")) if known_path.is_file() else {}
        except (OSError, ValueError):
            known = {}
        shown = []
        for protocol in generated:
            previous = cached.get(protocol.id)
            if protocol.status == "success" or previous is None:
                cached[protocol.id] = protocol
                if protocol.status == "success" and protocol.id in inputs:
                    known[protocol.id] = inputs[protocol.id]
                shown.append(protocol)
            else:
                shown.append(previous.model_copy(update={
                    "status": "error",
                    "error": protocol.error or "Plot generation failed",
                    "error_code": protocol.error_code,
                }))
        write_protocol_cache(
            report_dir, sort_protocols_by_execution_order(list(cached.values()), report_dir)
        )
        write_input_cache(report_dir, known)
        forget_cached_protocols(report_dir)
        return attach_protocol_notes(report_dir, shown)


def generate_protocols(report_dir: Path, task_ids: list[str]) -> list[ProtocolDetail]:
    """Generate some tasks of a report, in one worker, as the page shows them.

    When Qibocal produces nothing at all — not installed, failed to start —
    every task is answered with that error, uncached, so that it is tried
    again once Qibocal is there.
    """
    protocols, error = _generate_qibocal_protocols(report_dir, task_ids)
    if protocols is None:
        timing_map = _extract_protocol_timing_map(report_dir)
        return attach_protocol_notes(report_dir, [
            placeholder(report_dir, task_id, timing_map).model_copy(update={
                "status": "error",
                "error": str(error or "Qibocal did not produce protocol outputs for this report."),
                "error_code": getattr(error, "error_code", None),
            })
            for task_id in task_ids
        ])
    found = {p.id for p in protocols}
    timing_map = _extract_protocol_timing_map(report_dir)
    for protocol in protocols:
        if not protocol.execution_time or protocol.execution_time == "N/A":
            protocol.execution_time = _timing(timing_map, protocol.id)
    lost = [
        placeholder(report_dir, task_id, timing_map).model_copy(update={
            "status": "error", "error": f"Qibocal returned no outputs for routine '{task_id}'.",
        })
        for task_id in task_ids if task_id not in found
    ]
    return merge_generated(report_dir, [*protocols, *lost])


def get_report_protocols(
    report_dir: Path, progress_callback: Callable[[int, int, str], None] | None = None
) -> list[ProtocolDetail]:
    """Every protocol with its outputs, generating what is missing first, in one go.

    For callers that want everything at once (`/protocols`); the dashboard
    opens the overview instead and lets the report socket generate.
    """
    with report_generation_lock(report_dir):
        if not has_cached_report(report_dir):
            protocols = generate_report_on_the_fly(report_dir, progress_callback=progress_callback)
            return sort_protocols_by_execution_order(protocols, report_dir)
        missing = missing_protocols(report_dir)
        if not missing:
            return sort_protocols_by_execution_order(cached_protocols(report_dir), report_dir)
        if progress_callback:
            progress_callback(1, 1, ", ".join(missing))
        # Failures Qibocal-wide are not cached: they stand in for the placeholders.
        answered = {p.id: p for p in generate_protocols(report_dir, missing)}
        return [
            answered.get(p.id, p) if p.status == PENDING else p
            for p in report_overview(report_dir)
        ]


def regenerate_report(
    report_dir: Path, progress_callback: Callable[[int, int, str], None] | None = None
) -> list[ProtocolDetail]:
    """
    Explicit request for plots regeneration (Issue #10):
    Deletes the existing report/ folder and regenerates all protocol plots.
    """
    with report_generation_lock(report_dir):
        log_info(f"Regenerating plots: removing existing cache for '{report_dir.name}'...")
        forget_cached_protocols(report_dir)
        report_path = report_dir / "report"
        if report_path.is_dir():
            shutil.rmtree(report_path)
        return generate_report_on_the_fly(report_dir, progress_callback=progress_callback)
