"""Directory scanner and search indexing for Qibocal reports (Issue #10, Issue #3)."""

import json
import os
import time
import re
from collections import Counter
from pathlib import Path
from typing import Any

from qibocamp.reports.generator import (
    _extract_protocol_timing_map,
    format_execution_time,
    get_execution_order,
    has_cached_report,
    cached_protocols,
    report_overview,
    load_cached_protocols,
    sort_protocols_by_execution_order,
)
from qibocamp.models import (
    AuthorFrequency,
    DateHistogramBin,
    FilterStats,
    PlatformFrequency,
    ProtocolFrequency,
    ProtocolSummary,
    ReportDetail,
    ReportSummary,
    TagFrequency,
)
from qibocamp.reports.notes import load_notes

IGNORED_DIRS = {
    "node_modules",
    ".venv",
    "venv",
    ".git",
    "dist",
    ".devenv",
    "__pycache__",
    ".pytest_cache",
    ".gemini",
    "build",
    ".cache",
    ".archive",
    "archive",
    "archives",
}


def is_discovery_directory(path: Path, root_dir: Path) -> bool:
    """Allow the selected root, excluding hidden and ignored descendants."""
    try:
        parts = path.relative_to(root_dir).parts
    except ValueError:
        return False
    return path.is_dir() and not any(
        part.startswith(".") or part in IGNORED_DIRS for part in parts
    )


def is_report_directory(path: Path, root_dir: Path | None = None) -> bool:
    """Recognize report artifacts within a discovery boundary (default: path)."""
    if not is_discovery_directory(path, root_dir if root_dir is not None else path):
        return False

    # Standard Qibocal output contains meta.json
    if (path / "meta.json").is_file():
        return True

    # Pre-cached report directory containing json artifacts
    report_sub = path / "report"
    if report_sub.is_dir() and (
        (report_sub / "meta.json").is_file() or any(report_sub.glob("*.json"))
    ):
        return True

    # Raw acquisition run directory containing action.yml in subdirectories
    data_sub = path / "data"
    return data_sub.is_dir() and (
        any(data_sub.glob("*/action.yml"))
        or any(data_sub.glob("*/results.json"))
        or any(data_sub.glob("*/data.json"))
    )


def _target_value(text: str) -> Any:
    text = text.strip().strip("'\"")
    return int(text) if text.isdigit() else text


def parse_targets_text(text: str) -> list[Any]:
    """The targets in a folder name: `3`, `0, 1`, or pairs such as `(2, 3)` or `[2, 3]`.

    A pair stays a pair — a list of its qubits — as `meta.json` would have it.
    """
    targets: list[Any] = []
    for group in re.finditer(r"[\[(]([^\[\]()]*)[\])]", text):
        members = [_target_value(t) for t in group.group(1).split(",") if t.strip()]
        if members:
            targets.append(members)
    rest = re.sub(r"[\[(][^\[\]()]*[\])]", ",", text)
    targets.extend(_target_value(t) for t in rest.split(",") if t.strip())
    return targets


def flat_qubits(targets: list[Any]) -> list[Any]:
    """Every qubit a report touches, pairs opened up, in order, once."""
    seen: list[Any] = []
    for target in targets:
        for qubit in target if isinstance(target, (list, tuple)) else [target]:
            if qubit not in seen:
                seen.append(qubit)
    return seen


def target_label(target: Any) -> str:
    """A target as a name reads: `2` for a qubit, `2-3` for a pair."""
    if isinstance(target, (list, tuple)):
        return "-".join(target_label(item) for item in target)
    return str(target)


def _parse_meta_json(meta_path: Path) -> dict[str, Any]:
    """Safely parse meta.json."""
    if not meta_path.is_file():
        return {}
    try:
        with open(meta_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _discover_protocols(report_dir: Path, meta_data: dict[str, Any]) -> list[str]:
    """Discover protocols in a report folder in execution order (Issue #3)."""
    # 1. Authoritative source: history.json; 2. Backup: meta.json stats
    exec_order = get_execution_order(report_dir)
    if exec_order:
        return list(dict.fromkeys(k.rsplit("-", 1)[0] for k in exec_order))

    # From meta.json
    if "protocols" in meta_data and isinstance(meta_data["protocols"], list):
        return meta_data["protocols"]
    if "actions" in meta_data and isinstance(meta_data["actions"], list):
        return meta_data["actions"]
    if "stats" in meta_data and isinstance(meta_data["stats"], dict):
        protos = list(dict.fromkeys(k.rsplit("-", 1)[0] for k in meta_data["stats"]))
        if protos:
            return protos

    # From data/ directory
    data_dir = report_dir / "data"
    if data_dir.is_dir():
        protos = [
            p.name
            for p in sorted(data_dir.iterdir())
            if p.is_dir() and not p.name.startswith(".")
        ]
        if protos:
            return protos

    # From report/ directory
    report_path = report_dir / "report"
    if report_path.is_dir():
        protos = [
            p.stem
            for p in sorted(report_path.glob("*.json"))
            if p.stem not in ("meta", "history")
        ]
        if protos:
            return protos

    return ["characterization"]


def _format_date(raw_date: str | None) -> str:
    """Normalize date string to YYYY-MM-DD, leaving missing dates empty."""
    if not raw_date:
        return ""
    clean = raw_date.strip().split("T")[0]
    return clean


def parse_report_directory(
    report_dir: Path,
    root_dir: Path,
    author_identities: dict[str, list[str]] | None = None,
) -> ReportSummary:
    """Parse a report directory into a ReportSummary."""
    meta = _parse_meta_json(report_dir / "meta.json")

    report_id = report_dir.relative_to(root_dir).as_posix()
    title = (
        meta.get("title") or report_dir.name.replace("_", " ").replace("-", " ").title()
    )
    date_str = _format_date(meta.get("date"))
    time_str = (
        meta.get("time") or meta.get("start-time") or meta.get("start_time") or ""
    )
    raw_author = meta.get("author") or meta.get("user") or "Unknown"
    if author_identities:
        from qibocamp.config import resolve_author_identity

        author = resolve_author_identity(raw_author, author_identities)
    else:
        author = raw_author
    platform = meta.get("platform") or "Generic QPU"
    # What the protocols were run on, when it was gathered, over what the
    # session claims: see `actions.infer_targets`.
    targets = meta.get("targets-inferred") or meta.get("targets") or meta.get("qubits") or []
    if not isinstance(targets, list):
        targets = [targets] if targets is not None else []
    if not targets:
        match = re.search(r"_\[(.*)\]_", report_dir.name)
        if match:
            targets = parse_targets_text(match.group(1))
    raw_tags = (
        meta.get("tag")
        or meta.get("tags")
        or meta.get("labels")
        or meta.get("label")
        or []
    )
    if isinstance(raw_tags, str):
        tags = [raw_tags]
    elif isinstance(raw_tags, list):
        tags = [str(t) for t in raw_tags if t is not None]
    else:
        tags = []
    exec_time = meta.get("total_execution_time") or meta.get("duration")
    if not exec_time and isinstance(meta.get("stats"), dict):
        total_s = 0.0
        for p_stat in meta["stats"].values():
            if isinstance(p_stat, dict):
                total_s += (p_stat.get("acquisition", 0.0) or 0.0) + (
                    p_stat.get("fit", 0.0) or 0.0
                )
            elif isinstance(p_stat, (int, float)):
                total_s += float(p_stat)
        if total_s > 0:
            exec_time = format_execution_time(total_s)
    if not exec_time:
        exec_time = "N/A"
    cached = has_cached_report(report_dir)
    protocols = _discover_protocols(report_dir, meta)
    has_old = (report_dir / "platform").is_dir()
    has_new = (report_dir / "new_platform").is_dir()

    # Pre-compile search index from platform, author, tags, protocols, targets, date, id
    tag_tokens = " ".join(tags)
    proto_tokens = " ".join(protocols)
    target_tokens = " ".join(
        f"q{q} {q}" for q in flat_qubits(targets)
    ) + " " + " ".join(target_label(t) for t in targets if isinstance(t, (list, tuple)))
    search_tokens = (
        f"{platform} {author} {raw_author} {tag_tokens} "
        f"{proto_tokens} {target_tokens} {date_str} {time_str} {report_id} {title}"
    )
    search_index = search_tokens.lower()

    return ReportSummary(
        id=report_id,
        path=str(report_dir),
        title=title,
        date=date_str,
        time=time_str,
        author=author,
        platform=platform,
        targets=targets,
        protocols=protocols,
        tags=tags,
        total_execution_time=exec_time,
        has_cached_report=cached,
        has_old_platform=has_old,
        has_new_platform=has_new,
        search_index=search_index,
    )


# In-memory index cache to avoid re-scanning unchanged directories
_REPORT_CACHE: dict[str, Any] = {
    "root_path": None,
    "author_identities_key": None,
    "last_mtime": 0.0,
    "reports": None,
    # When the tree was last walked to check the cache: see `scan_interval`.
    "checked_at": 0.0,
}


def scan_interval() -> float:
    """How long a validated index is trusted before the tree is walked again.

    Validating the cache means walking the whole report tree and stat-ing
    every directory, which every list, statistics, health and notes request
    did, in full, every time — the dominant cost on a large share. Within
    this many seconds the last walk stands; `QIBOCAMP_SCAN_INTERVAL`
    sets it, and 0 walks every time, as before.
    """
    try:
        return max(0.0, float(os.environ.get("QIBOCAMP_SCAN_INTERVAL", "2")))
    except ValueError:
        return 2.0


def invalidate_report_cache() -> None:
    """Invalidate the in-memory indexed reports cache."""
    _REPORT_CACHE["last_mtime"] = 0.0
    _REPORT_CACHE["reports"] = None
    _REPORT_CACHE["checked_at"] = 0.0


def report_index_token() -> str:
    """What the index is, as a string: the newest mtime it was built from.

    The same token for the same content, so a client that still holds a
    response with it is told nothing changed; `scan_reports` first.
    """
    return f"{int(_REPORT_CACHE['last_mtime'] * 1000)}"


def cached_report_count(root_dir: Path) -> int:
    """How many reports there are, from the index as it stands when there is one."""
    reports = _REPORT_CACHE["reports"]
    if reports is not None and _REPORT_CACHE["root_path"] == str(root_dir.resolve()):
        return len(reports)
    return len(scan_reports(root_dir))


def get_directory_mtime(root_dir: Path) -> float:
    """Compute the maximum mtime across root_dir and its report directories."""
    if not root_dir.exists() or not root_dir.is_dir():
        return 0.0

    try:
        latest = root_dir.stat().st_mtime
    except OSError:
        return 0.0

    try:
        for dirpath, dirnames, _ in os.walk(root_dir):
            dirnames[:] = [
                d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")
            ]
            dp = Path(dirpath)
            try:
                s = dp.stat()
                latest = max(latest, s.st_mtime)
            except OSError:
                pass

            if is_report_directory(dp, root_dir):
                for meta_name in ("meta.json", "history.json"):
                    meta_p = dp / meta_name
                    if meta_p.is_file():
                        try:
                            m_mtime = meta_p.stat().st_mtime
                            latest = max(latest, m_mtime)
                        except OSError:
                            pass
                report_meta = dp / "report" / "meta.json"
                if report_meta.is_file():
                    try:
                        rm_mtime = report_meta.stat().st_mtime
                        latest = max(latest, rm_mtime)
                    except OSError:
                        pass
                dirnames.clear()
    except OSError:
        pass

    return latest


def _discover_author_identities() -> dict[str, list[str]] | None:
    try:
        from qibocamp.config import load_servers

        servers = load_servers()
        curr_name = os.environ.get("QIBOCAMP_SERVER_NAME", "")
        for s in servers:
            if curr_name and s.get("name") == curr_name and s.get("author_identities"):
                return s["author_identities"]
        for s in servers:
            if s.get("is_default") and s.get("author_identities"):
                return s["author_identities"]
        for s in servers:
            if s.get("author_identities"):
                return s["author_identities"]
    except (OSError, KeyError, TypeError, ValueError):
        pass
    return None


def scan_reports(
    root_dir: Path,
    author_identities: dict[str, list[str]] | None = None,
) -> list[ReportSummary]:
    """Scan root_dir for reports, caching indexing and invalidating on change."""
    resolved_root = root_dir.resolve()
    if not resolved_root.exists() or not resolved_root.is_dir():
        return []

    if author_identities is None:
        author_identities = _discover_author_identities()

    auth_key = (
        json.dumps(author_identities, sort_keys=True) if author_identities else ""
    )
    same_index = (
        _REPORT_CACHE["root_path"] == str(resolved_root)
        and _REPORT_CACHE["author_identities_key"] == auth_key
        and _REPORT_CACHE["reports"] is not None
    )
    # Walked a moment ago: the last walk stands.
    now = time.monotonic()
    if same_index and now - _REPORT_CACHE["checked_at"] < scan_interval():
        return _REPORT_CACHE["reports"]

    current_mtime = get_directory_mtime(resolved_root)
    _REPORT_CACHE["checked_at"] = now

    # Check if cache is still valid
    if same_index and current_mtime <= _REPORT_CACHE["last_mtime"]:
        return _REPORT_CACHE["reports"]

    reports: list[ReportSummary] = []

    # Check root_dir itself
    if is_report_directory(resolved_root, resolved_root):
        reports = [
            parse_report_directory(
                resolved_root, resolved_root.parent, author_identities=author_identities
            )
        ]
    else:
        # Fast directory walk skipping ignored subtrees
        for dirpath, dirnames, _ in os.walk(resolved_root):
            dirnames[:] = [
                d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")
            ]
            p = Path(dirpath)

            if p != resolved_root and is_report_directory(p, resolved_root):
                reports.append(
                    parse_report_directory(
                        p, resolved_root, author_identities=author_identities
                    )
                )
                # Don't recurse into subdirectories of a discovered report
                dirnames.clear()

    # Update cache
    _REPORT_CACHE["root_path"] = str(resolved_root)
    _REPORT_CACHE["author_identities_key"] = auth_key
    _REPORT_CACHE["last_mtime"] = current_mtime
    _REPORT_CACHE["reports"] = reports

    return reports


def filter_reports(
    reports: list[ReportSummary],
    query: str | None = None,
    authors: list[str] | None = None,
    platforms: list[str] | None = None,
    tags: list[str] | None = None,
    protocols: list[str] | None = None,
    qubits: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    sort_by: str = "date_desc",
    author_identities: dict[str, list[str]] | None = None,
    folder: str | None = None,
) -> list[ReportSummary]:
    """Filter and sort reports based on search criteria."""
    filtered = reports

    if folder:
        clean_folder = folder.strip().strip("/")
        if clean_folder:
            filtered = [
                r
                for r in filtered
                if r.id == clean_folder or r.id.startswith(f"{clean_folder}/")
            ]

    if author_identities is None:
        author_identities = _discover_author_identities()

    if query:
        q_lower = query.lower()
        filtered = [
            r
            for r in filtered
            if (r.search_index and q_lower in r.search_index)
            or q_lower in (r.title or "").lower()
            or q_lower in r.id.lower()
            or q_lower in (r.author or "").lower()
            or q_lower in (r.platform or "").lower()
            or any(q_lower in tag.lower() for tag in r.tags)
            or any(q_lower in p.lower() for p in r.protocols)
        ]

    if authors:
        from qibocamp.config import resolve_author_identity

        expanded_authors: set[str] = set()
        for a in authors:
            clean_a = a.strip().lower()
            expanded_authors.add(clean_a)
            if author_identities:
                for canonical, aliases in author_identities.items():
                    all_variants = [canonical] + [al for al in aliases if al]
                    if any(clean_a == v.strip().lower() for v in all_variants):
                        for v in all_variants:
                            expanded_authors.add(v.strip().lower())
        filtered = [
            r
            for r in filtered
            if (r.author and r.author.strip().lower() in expanded_authors)
            or (
                author_identities
                and resolve_author_identity(r.author, author_identities).strip().lower()
                in expanded_authors
            )
        ]

    if platforms:
        filtered = [r for r in filtered if r.platform in platforms]

    if tags:
        filtered = [r for r in filtered if any(tag in r.tags for tag in tags)]

    if protocols:
        filtered = [r for r in filtered if any(p in r.protocols for p in protocols)]

    if qubits:
        def _norm_q(val: Any) -> set[str]:
            s = str(val).strip().lower()
            res = {s}
            if s.startswith("q") and len(s) > 1 and s[1:].isdigit():
                res.add(s[1:])
            elif s.isdigit():
                res.add(f"q{s}")
            return res

        # A qubit, `2` or `q2`; or a pair, `2-3`, which asks for that pair.
        wanted_q: set[str] = set()
        wanted_pairs: set[str] = set()
        for q in qubits:
            text = str(q).strip().lower()
            if "-" in text and all(part.strip() for part in text.split("-")):
                wanted_pairs.add("-".join(p.strip().lstrip("q") for p in text.split("-")))
            else:
                wanted_q.update(_norm_q(q))

        def _report_has_qubit(report_targets: list[Any]) -> bool:
            for t in report_targets:
                if isinstance(t, (list, tuple)):
                    if target_label(t).lower() in wanted_pairs:
                        return True
                    for sub in t:
                        if _norm_q(sub) & wanted_q:
                            return True
                else:
                    if _norm_q(t) & wanted_q:
                        return True
            return False

        filtered = [r for r in filtered if _report_has_qubit(r.targets)]

    if start_date:
        filtered = [r for r in filtered if r.date >= start_date]

    if end_date:
        filtered = [r for r in filtered if r.date and r.date <= end_date]

    # Sorting
    if sort_by == "date_asc":
        filtered.sort(key=lambda r: (r.date, r.time or ""))
    elif sort_by == "platform":
        filtered.sort(key=lambda r: (r.platform or "").lower())
    elif sort_by in ("id", "title"):
        filtered.sort(key=lambda r: r.id.lower())
    else:  # date_desc default
        filtered.sort(key=lambda r: (r.date, r.time or ""), reverse=True)

    return filtered


def compute_filter_stats(
    reports: list[ReportSummary],
    author_identities: dict[str, list[str]] | None = None,
) -> FilterStats:
    """Compute aggregate filter statistics (Issue #3)."""
    if author_identities is None:
        author_identities = _discover_author_identities()

    from qibocamp.config import resolve_author_identity

    authors = sorted(
        {
            resolve_author_identity(r.author, author_identities)
            for r in reports
            if r.author
        }
    )
    tags = sorted({tag for r in reports for tag in r.tags})

    proto_counter = Counter()
    for r in reports:
        for p in r.protocols:
            proto_counter[p] += 1

    # Sorted from most frequent to least frequent (automated suggestion, Issue #3)
    proto_freqs = [
        ProtocolFrequency(name=name, count=count)
        for name, count in proto_counter.most_common()
    ]

    platform_counter = Counter(r.platform for r in reports if r.platform)
    platform_freqs = [
        PlatformFrequency(name=name, count=count)
        for name, count in platform_counter.most_common()
    ]

    author_counter = Counter(
        resolve_author_identity(r.author, author_identities)
        for r in reports
        if r.author
    )
    author_freqs = [
        AuthorFrequency(name=name, count=count)
        for name, count in author_counter.most_common()
    ]

    tag_counter = Counter(tag for r in reports for tag in r.tags)
    tag_freqs = [
        TagFrequency(name=name, count=count)
        for name, count in tag_counter.most_common()
    ]

    date_counter = Counter(r.date for r in reports if r.date)
    date_histogram = [
        DateHistogramBin(date=d, count=date_counter[d]) for d in sorted(date_counter)
    ]

    return FilterStats(
        authors=authors,
        tags=tags,
        protocols=proto_freqs,
        date_histogram=date_histogram,
        platforms=platform_freqs,
        author_frequencies=author_freqs,
        tag_frequencies=tag_freqs,
        total_reports=len(reports),
    )


def read_platform_snapshot(target_dir: Path) -> dict[str, Any]:
    """Every file of `platform/`, parsed, by stem: the `platform_snapshot`."""
    platform_data: dict[str, Any] = {}
    plat_dir = target_dir / "platform"
    if not plat_dir.is_dir():
        return platform_data
    for f in plat_dir.glob("*.json"):
        try:
            with open(f, encoding="utf-8") as pf:
                platform_data[f.stem] = json.load(pf)
        except (json.JSONDecodeError, OSError):
            pass
    for f in plat_dir.glob("*.yaml"):
        try:
            import yaml

            with open(f, encoding="utf-8") as yf:
                platform_data[f.stem] = yaml.safe_load(yf)
        except (yaml.YAMLError, OSError):
            pass
    return platform_data


def get_report_detail(
    root_dir: Path,
    report_id: str,
    author_identities: dict[str, list[str]] | None = None,
    include_platform: bool = False,
) -> ReportDetail | None:
    """Retrieve full details for a specific report.

    The platform snapshot is left out unless asked for: it is served by its
    own endpoint, and nothing else reads it from here.
    """
    root_dir = root_dir.resolve()
    target_dir = root_dir / report_id
    if not target_dir.is_dir():
        # Search by name match
        for r in scan_reports(root_dir, author_identities=author_identities):
            if r.id == report_id:
                target_dir = Path(r.path)
                break

    if not is_report_directory(target_dir, root_dir):
        return None

    if author_identities is None:
        try:
            from qibocamp.config import load_servers

            for s in load_servers():
                if s.get("is_default") and s.get("author_identities"):
                    author_identities = s["author_identities"]
                    break
        except (OSError, KeyError, TypeError, ValueError):
            author_identities = None

    summary = parse_report_directory(
        target_dir,
        root_dir.parent if target_dir == root_dir else root_dir,
        author_identities=author_identities,
    )

    # Read history.json
    history_data = {}
    hist_path = target_dir / "history.json"
    if hist_path.is_file():
        try:
            with open(hist_path, encoding="utf-8") as f:
                history_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # The platform snapshot only on request: the dashboard reads it from its
    # own endpoint, and it is every file of platform/, parsed, otherwise sent
    # with every report opened.
    platform_data = read_platform_snapshot(target_dir) if include_platform else {}

    # Protocol summaries: read from cache if available, otherwise summarize from
    # metadata in execution order
    if has_cached_report(target_dir):
        # Counted from the outputs, which are kept in memory once read; the
        # protocols not generated yet are there too, pending.
        protocols = report_overview(target_dir)
        proto_summaries = [
            ProtocolSummary(
                id=p.id,
                name=p.name,
                category=p.category,
                execution_time=p.execution_time,
                status=p.status,
                num_figures=len(p.figures),
            )
            for p in protocols
        ]
    else:
        timing_map = _extract_protocol_timing_map(target_dir)
        exec_order = get_execution_order(target_dir)
        proto_list = exec_order if exec_order else summary.protocols
        proto_summaries = [
            ProtocolSummary(
                id=p_name,
                name=p_name.replace("_", " ").title(),
                category="calibration",
                execution_time=timing_map.get(p_name)
                or timing_map.get(p_name.rsplit("-", 1)[0])
                or "N/A",
                status="pending",
                num_figures=0,
            )
            for p_name in proto_list
        ]
        proto_summaries = sort_protocols_by_execution_order(
            proto_summaries, target_dir
        )

    return ReportDetail(
        **summary.model_dump(),
        history=history_data,
        platform_snapshot=platform_data,
        protocols_summary=proto_summaries,
        notes=load_notes(target_dir),
    )
