"""One request where there were several: the GraphQL endpoint.

The REST API answers one question per request, and the dashboard asks
several at once — the page of reports, the statistics of everything, the
statistics of what is filtered; a report, then its protocols; whether a
server authenticates, then who the caller is. This schema answers them
together, at ``/api/graphql``, with the very shapes the REST endpoints
answer with: field names are kept in ``snake_case`` on purpose, so a client
reads a GraphQL result exactly as it reads the REST one.

Nothing is written through here yet: queries only. Mutations stay with the
REST endpoints until there is a reason to move them.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import strawberry
from graphql import GraphQLError
from strawberry.fastapi import BaseContext, GraphQLRouter
from strawberry.scalars import JSON
from strawberry.schema.config import StrawberryConfig
from strawberry.types import Info

from qibocamp import auth
from qibocamp.auth import UserRole
from qibocamp.reports.generator import report_overview
from qibocamp.models import (
    FilterStats,
    ProtocolDetail,
    ReportDetail,
    ReportSummary,
)
from qibocamp.reports.scanner import (
    cached_report_count,
    compute_filter_stats,
    filter_reports,
    get_report_detail,
    read_platform_snapshot,
    scan_reports,
)

VIEWERS = (UserRole.VIEWER.value, UserRole.EDITOR.value, UserRole.ADMIN.value)


# --- Context and authorisation -----------------------------------------------


class Context(BaseContext):
    """What every resolver may ask: who calls, and where the reports are."""

    def __init__(self, user: dict | None, report_root: Callable[[], Path]):
        super().__init__()
        self.user = user
        self.report_root = report_root


def require(info: Info, roles: tuple[str, ...] = VIEWERS) -> dict:
    """The caller, or the error the REST API would answer with.

    ``UNAUTHENTICATED`` stands for the 401 and ``FORBIDDEN`` for the 403, as
    extension codes, since a GraphQL response is a 200 whatever happened to
    one of its fields; the client reads the code where it read the status.
    """
    if not auth.is_auth_enabled():
        return {"id": "anonymous", "username": "anonymous", "role": UserRole.ADMIN.value}
    user = info.context.user
    if user is None:
        raise GraphQLError(
            "Authentication required", extensions={"code": "UNAUTHENTICATED"}
        )
    if user["role"] not in roles:
        raise GraphQLError(
            f"Permission denied: role '{user['role']}' cannot perform this action",
            extensions={"code": "FORBIDDEN"},
        )
    return user


# --- Types: the REST shapes, field for field ----------------------------------


@strawberry.input(description="What `/api/reports` takes as query parameters.")
class ReportFilter:
    query: str | None = None
    folder: str | None = None
    authors: list[str] | None = None
    platforms: list[str] | None = None
    protocols: list[str] | None = None
    tags: list[str] | None = None
    qubits: list[str] | None = None
    start_date: str | None = None
    end_date: str | None = None
    sort_by: str = "date_desc"


@strawberry.type(description="A report as the list shows it: `ReportSummary`.")
class Report:
    id: str
    path: str
    title: str
    date: str
    time: str | None
    author: str | None
    platform: str | None
    targets: JSON
    protocols: list[str]
    tags: list[str]
    total_execution_time: str | None
    has_cached_report: bool
    has_old_platform: bool
    has_new_platform: bool

    @staticmethod
    def of(summary: ReportSummary) -> Report:
        return Report(
            id=summary.id,
            path=summary.path,
            title=summary.title,
            date=summary.date,
            time=summary.time,
            author=summary.author,
            platform=summary.platform,
            targets=summary.targets,
            protocols=summary.protocols,
            tags=summary.tags,
            total_execution_time=summary.total_execution_time,
            has_cached_report=summary.has_cached_report,
            has_old_platform=summary.has_old_platform,
            has_new_platform=summary.has_new_platform,
        )


@strawberry.type(description="One page of reports: `PaginatedReportsResponse`.")
class ReportPage:
    items: list[Report]
    total: int
    page: int
    page_size: int
    total_pages: int


@strawberry.type
class Frequency:
    name: str
    count: int


@strawberry.type
class HistogramBin:
    date: str
    count: int


@strawberry.type(description="What `/api/reports/stats` answers: `FilterStats`.")
class Stats:
    authors: list[str]
    tags: list[str]
    protocols: list[Frequency]
    date_histogram: list[HistogramBin]
    platforms: list[Frequency]
    author_frequencies: list[Frequency]
    tag_frequencies: list[Frequency]
    total_reports: int

    @staticmethod
    def of(stats: FilterStats) -> Stats:
        frequencies = lambda items: [Frequency(name=i.name, count=i.count) for i in items]
        return Stats(
            authors=stats.authors,
            tags=stats.tags,
            protocols=frequencies(stats.protocols),
            date_histogram=[
                HistogramBin(date=b.date, count=b.count) for b in stats.date_histogram
            ],
            platforms=frequencies(stats.platforms),
            author_frequencies=frequencies(stats.author_frequencies),
            tag_frequencies=frequencies(stats.tag_frequencies),
            total_reports=stats.total_reports,
        )


@strawberry.type
class Note:
    content: str
    timestamp: str
    author: str | None

    @staticmethod
    def of(note: Any) -> Note:
        data = note.model_dump(mode="json")
        return Note(
            content=data["content"], timestamp=data["timestamp"], author=data.get("author")
        )


@strawberry.type
class ProtocolSummary:
    id: str
    name: str
    category: str | None
    execution_time: str | None
    status: str
    num_figures: int


@strawberry.type(
    description="A protocol's outputs: `ProtocolDetail`. Figures are Plotly JSON."
)
class Protocol:
    id: str
    name: str
    category: str | None
    execution_time: str | None
    status: str
    html: str | None
    figures: JSON
    num_figures: int
    error: str | None
    error_code: str | None
    notes: list[Note]

    @staticmethod
    def of(detail: ProtocolDetail) -> Protocol:
        return Protocol(
            id=detail.id,
            name=detail.name,
            category=detail.category,
            execution_time=detail.execution_time,
            status=detail.status,
            html=detail.html,
            figures=detail.figures,
            num_figures=len(detail.figures),
            error=detail.error,
            error_code=detail.error_code,
            notes=[Note.of(n) for n in detail.notes],
        )


@strawberry.type(description="A report in full: `ReportDetail`, with its protocols on request.")
class ReportFull(Report):
    history: JSON
    protocols_summary: list[ProtocolSummary]
    notes: list[Note]
    _directory: strawberry.Private[Path]

    @strawberry.field(
        description="Every file of `platform/`, parsed, by stem; read only when selected."
    )
    def platform_snapshot(self) -> JSON:
        return read_platform_snapshot(self._directory)

    @strawberry.field(
        description=(
            "Every protocol of the report, in execution order, without generating anything: "
            "those not generated yet have status `pending` and no outputs, and the report "
            "socket generates them. Select `html` and `figures` here to have every cached "
            "output at once, or leave them out and ask `protocol` for one protocol's "
            "outputs at a time, as the cards come into view."
        )
    )
    def protocols_detail(self) -> list[Protocol]:
        return [Protocol.of(p) for p in report_overview(self._directory)]

    @staticmethod
    def of(detail: ReportDetail, directory: Path) -> ReportFull:
        base = Report.of(detail)
        return ReportFull(
            **{name: getattr(base, name) for name in base.__dataclass_fields__},
            history=detail.history if detail.history is not None else {},
            protocols_summary=[
                ProtocolSummary(
                    id=p.id,
                    name=p.name,
                    category=p.category,
                    execution_time=p.execution_time,
                    status=p.status,
                    num_figures=p.num_figures,
                )
                for p in detail.protocols_summary
            ],
            notes=[Note.of(n) for n in detail.notes],
            _directory=directory,
        )


@strawberry.type
class User:
    id: str
    username: str
    role: str


@strawberry.type(
    description=(
        "Whether this server authenticates, and who the caller is: what "
        "`/api/auth/status` and `/api/auth/me` answer, in one."
    )
)
class Session:
    auth_enabled: bool
    user: User | None


@strawberry.type(description="What `/api/health` answers.")
class Health:
    status: str
    server_name: str
    reports_count: int
    root_dir: str


# --- The queries ---------------------------------------------------------------


def _filtered(info: Info, where: ReportFilter | None) -> list[ReportSummary]:
    reports = scan_reports(info.context.report_root())
    if where is None:
        return filter_reports(reports)
    return filter_reports(
        reports,
        query=where.query,
        folder=where.folder,
        authors=where.authors,
        platforms=where.platforms,
        tags=where.tags,
        protocols=where.protocols,
        qubits=where.qubits,
        start_date=where.start_date,
        end_date=where.end_date,
        sort_by=where.sort_by,
    )


@strawberry.type
class Query:
    @strawberry.field(description="Reports matching the filter, one page at a time.")
    def reports(
        self,
        info: Info,
        where: ReportFilter | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> ReportPage | None:
        # Nullable on purpose: a refused field is null with its error, and
        # the other fields of the same request still answer.
        require(info)
        page = max(1, page)
        page_size = max(1, min(500, page_size))
        matching = _filtered(info, where)
        total = len(matching)
        total_pages = max(1, (total + page_size - 1) // page_size) if total else 1
        start = (page - 1) * page_size
        return ReportPage(
            items=[Report.of(r) for r in matching[start : start + page_size]],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    @strawberry.field(description="Statistics of the reports matching the filter; of all, without one.")
    def stats(self, info: Info, where: ReportFilter | None = None) -> Stats | None:
        require(info)
        return Stats.of(compute_filter_stats(_filtered(info, where)))

    @strawberry.field(description="One report, with everything `/api/reports/{id}` answers.")
    def report(self, info: Info, id: str) -> ReportFull | None:
        require(info)
        root = info.context.report_root().resolve()
        detail = get_report_detail(root, id)
        if detail is None:
            return None
        return ReportFull.of(detail, Path(detail.path))

    @strawberry.field(
        description=(
            "One protocol of a report, for its outputs: the figures and the HTML of the "
            "card that just came into view; `pending`, without outputs, when not generated "
            "yet. Null when the report, or the protocol, is not there."
        )
    )
    def protocol(self, info: Info, report_id: str, protocol_id: str) -> Protocol | None:
        require(info)
        root = info.context.report_root().resolve()
        detail = get_report_detail(root, report_id)
        if detail is None:
            return None
        wanted = protocol_id.lower().replace("-", "_")
        for item in report_overview(Path(detail.path)):
            if item.id == protocol_id or item.id.lower().replace("-", "_") == wanted:
                return Protocol.of(item)
        return None

    @strawberry.field(description="Whether this server authenticates, and who is asking.")
    def session(self, info: Info) -> Session:
        if not auth.is_auth_enabled():
            return Session(
                auth_enabled=False,
                user=User(id="admin", username="local-admin", role=UserRole.ADMIN.value),
            )
        user = info.context.user
        return Session(
            auth_enabled=True,
            user=User(id=user["id"], username=user["username"], role=user["role"])
            if user
            else None,
        )

    @strawberry.field
    def health(self, info: Info) -> Health:
        # As `/api/health` answers it: the reports and where they are, to a reader only.
        name = os.environ.get("QIBOCAMP_SERVER_NAME", "local-instance")
        if auth.is_auth_enabled() and info.context.user is None:
            return Health(status="ok", server_name=name, reports_count=0, root_dir="")
        root = info.context.report_root()
        return Health(
            status="ok",
            server_name=name,
            reports_count=cached_report_count(root),
            root_dir=str(root),
        )


schema = strawberry.Schema(
    query=Query,
    # The REST shapes, field for field: no camelCase between the two.
    config=StrawberryConfig(auto_camel_case=False),
)


def build_router(
    current_user: Callable[..., dict | None],
    report_root: Callable[[], Path],
) -> GraphQLRouter:
    """The router to include at `/api/graphql`.

    `current_user` is the REST API's own dependency, so both halves read the
    bearer token the same way; `report_root` is read at every request, since
    the directory can be changed while the server runs.
    """
    from fastapi import Depends

    def context_getter(user: dict | None = Depends(current_user)) -> Context:
        return Context(user=user, report_root=report_root)

    # An empty path: with the prefix, the endpoint is `/api/graphql` itself,
    # not `/api/graphql/`, which the SPA catch-all would answer for.
    return GraphQLRouter(schema, path="", context_getter=context_getter, graphql_ide="graphiql")
