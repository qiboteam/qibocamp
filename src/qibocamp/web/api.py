import asyncio
import html
from datetime import datetime
import json
import logging
import os
import tempfile
import threading
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from qibocamp import __version__, auth, config, qibocal_environment
from qibocamp.reports.actions import execute_bulk_action, infer_targets
from qibocamp.reports.upload import UploadError, receive_report, upload_limit
from qibocamp.reports.archive import (
    create_archive,
    delete_archive,
    get_archive_index,
    get_archive_metadata,
    get_archive_storage_dir,
    get_archive_zip_path,
    list_archives,
    resolve_meta_file,
    resolve_protocol_data_dir,
    resolve_report_dir,
    restore_archive,
    update_archive,
    zip_directory,
)
from qibocamp.web.docs import DOCS_NAVIGATION, resolve_docs_content
from qibocamp.reports.generator import get_report_protocols, regenerate_report
from qibocamp.web.graphql import build_router as build_graphql_router
from qibocamp.reports.live import handle_live_websocket
from qibocamp.logger import (
    log_error,
    log_history,
    log_info,
    setup_uvicorn_logging,
)
from qibocamp.version import version_info
from qibocamp.models import (
    VersionResponse,
    FolderListing,
    UploadCapability,
    UploadPolicy,
    UploadResult,
    FolderEntry,
    AdminConfigResponse,
    ArchiveCreateRequest,
    ArchiveMetadata,
    ArchiveReportIndexItem,
    ArchiveRestoreRequest,
    ArchiveUpdateRequest,
    AuthStatusResponse,
    BulkActionRequest,
    BulkActionResponse,
    ChangeDirectoryRequest,
    DirectoryBreadcrumb,
    DirectoryBrowseResponse,
    DirectoryEntry,
    FilterStats,
    HealthResponse,
    InviteCreateRequest,
    InviteModel,
    InviteValidateResponse,
    LoginRequest,
    LoginResponse,
    Note,
    NoteUpdate,
    NoteCreate,
    PaginatedReportsResponse,
    PasswordResetCreateRequest,
    PasswordResetModel,
    PlatformDataResponse,
    ProtocolDetail,
    QibocalInstallRequest,
    QibocalOptions,
    QibocalStatus,
    RegisterRequest,
    ReportDetail,
    ReportPathResponse,
    ReportSummary,
    ServerDirectoryInfo,
    SingleLabelRequest,
    UpdateAuthorRequest,
    UserModel,
    UserRole,
    UserRoleUpdate,
)
from qibocamp.reports.notes import (
    note_at,
    replace_note,
    remove_note,
    NoteMismatch,
    NotesError,
    append_note,
    load_notes,
    protocol_notes_directory,
)
from qibocamp.reports.scanner import (
    report_index_token,
    cached_report_count,
    _parse_meta_json,
    compute_filter_stats,
    filter_reports,
    get_report_detail,
    invalidate_report_cache,
    scan_reports,
)
from qibocamp.web.websocket import handle_report_websocket

# Backward-compatible internal aliases

_INSTALL_TASKS: set[asyncio.Task[QibocalStatus]] = set()
_INSTALL_LOGGER = logging.getLogger(__name__)
INSTALL_STREAM_BUFFER_ENTRIES = 128
INSTALL_STREAM_POLL_INTERVAL = 0.025


class _InstallationOutput:
    """A bounded thread-to-async bridge that never holds up the installer."""

    def __init__(self):
        self._entries: deque[str] = deque(maxlen=INSTALL_STREAM_BUFFER_ENTRIES)
        self._lock = threading.Lock()
        self._started = False
        self._connected = True
        self._dropped = False

    @property
    def started(self) -> bool:
        with self._lock:
            return self._started

    def append(self, text: str) -> None:
        with self._lock:
            self._started = True
            if not self._connected:
                return
            if len(self._entries) == self._entries.maxlen:
                self._dropped = True
            if len(text) > qibocal_environment.INSTALL_OUTPUT_CHUNK_BYTES:
                self._dropped = True
                text = text[-qibocal_environment.INSTALL_OUTPUT_CHUNK_BYTES :]
            self._entries.append(text)

    def drain(self) -> list[str]:
        with self._lock:
            entries = list(self._entries)
            self._entries.clear()
            if self._dropped:
                entries.insert(
                    0,
                    "\x1b[33mEarlier installer output omitted (slow client).\x1b[0m\n",
                )
                self._dropped = False
            return entries

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False
            self._entries.clear()
            self._dropped = False


def _installation_finished(task: asyncio.Task[QibocalStatus]) -> None:
    _INSTALL_TASKS.discard(task)
    if task.cancelled():
        _INSTALL_LOGGER.error("Qibocal installation monitoring was cancelled.")
        return
    error = task.exception()
    if isinstance(error, qibocal_environment.EnvironmentOperationError):
        _INSTALL_LOGGER.warning("%s", error.detail)
    elif error is not None:
        _INSTALL_LOGGER.error("Unexpected Qibocal installation failure", exc_info=error)


async def _installation_events(
    task: asyncio.Task[QibocalStatus], output: _InstallationOutput
):
    try:
        while True:
            for text in output.drain():
                yield json.dumps({"type": "output", "text": text}) + "\n"
            if task.done():
                # Completion may race the first drain; keep all remaining chunks
                # ahead of the single terminal event.
                for text in output.drain():
                    yield json.dumps({"type": "output", "text": text}) + "\n"
                error = task.exception()
                if error is None:
                    event = {
                        "type": "complete",
                        "status": task.result().model_dump(),
                    }
                else:
                    event = {
                        "type": "error",
                        "detail": error.detail
                        if isinstance(
                            error, qibocal_environment.EnvironmentOperationError
                        )
                        else f"Unexpected Qibocal installation failure: {error}",
                    }
                yield json.dumps(event) + "\n"
                return
            await asyncio.sleep(INSTALL_STREAM_POLL_INTERVAL)
    finally:
        output.disconnect()


def _resolve_report_target_dir(report_id: str) -> Path:
    return resolve_report_dir(REPORT_ROOT_DIR, report_id)


security = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict | None:
    """Extract user from bearer token or return synthetic admin if auth is disabled."""
    if not auth.is_auth_enabled():
        return {
            "id": "anonymous",
            "username": "anonymous",
            "role": UserRole.ADMIN.value,
        }
    if not credentials or not credentials.credentials:
        return None
    payload = auth.decode_access_token(credentials.credentials)
    if not payload:
        return None
    user = auth.get_user_by_id(payload.get("sub"))
    if not user:
        return None
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
    }


get_current_user_optional = get_current_user


def require_authenticated(
    user: dict | None = Depends(get_current_user),
) -> dict:
    """Ensure request is authenticated when auth is enabled."""
    if not auth.is_auth_enabled():
        return {
            "id": "anonymous",
            "username": "anonymous",
            "role": UserRole.ADMIN.value,
        }
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_role(allowed_roles: list[str]):
    def dependency(user: dict = Depends(require_authenticated)) -> dict:
        if not auth.is_auth_enabled():
            return user
        if user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied: role '{user['role']}' cannot perform this action",
            )
        return user

    return dependency


require_viewer = require_role(
    [UserRole.VIEWER.value, UserRole.EDITOR.value, UserRole.ADMIN.value]
)
require_editor = require_role([UserRole.EDITOR.value, UserRole.ADMIN.value])
require_admin = require_role([UserRole.ADMIN.value])


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_uvicorn_logging()
    if auth.is_auth_enabled():
        token = auth.create_initial_admin_invite_if_needed()
        if token:
            log_info(f"Initial admin invitation token generated: {token}")
    try:
        yield
    finally:
        # Worker threads keep the environment lock after a client disconnects.
        # Graceful shutdown waits for their bounded installation timeout.
        if _INSTALL_TASKS:
            await asyncio.gather(*tuple(_INSTALL_TASKS), return_exceptions=True)


app = FastAPI(
    title="Qibocamp Server",
    description="REST API for serving and managing Qibocal calibration reports",
    version=__version__,
    docs_url="/api/docs/swagger",
    redoc_url="/api/docs/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)


@app.exception_handler(auth.AuthStoreError)
async def auth_store_error_handler(_request: Request, error: auth.AuthStoreError) -> JSONResponse:
    # Nobody can sign in until it is mended by hand: said as it is, never papered over.
    log_error(str(error))
    return JSONResponse(status_code=503, content={"detail": str(error)})


@app.exception_handler(NotesError)
async def notes_error_handler(_request: Request, error: NotesError) -> JSONResponse:
    log_error(str(error))
    return JSONResponse(status_code=500, content={"detail": str(error)})


# Plotly JSON compresses five to ten times; anything above a kilobyte goes gzipped.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# A form of another site needs no permission to be sent — an upload, say — so
# what the browser does not stop, the server does: a change asked by a page
# of an origin not allowed is refused, whatever it is.
@app.middleware("http")
async def refuse_foreign_pages(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin and request.method not in ("GET", "HEAD", "OPTIONS"):
        own = f"{request.url.scheme}://{request.url.netloc}"
        if not config.origin_allowed(origin, own):
            return JSONResponse(status_code=403, content={"detail": f"Requests from {origin} are not allowed."})
    return await call_next(request)


# The dashboard calls the server from another origin: its own, and no other.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.allowed_origins(),
    allow_origin_regex=config.LOCAL_ORIGIN,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Parameters-File"],
)

REPORT_ROOT_DIR = Path(os.environ.get("QIBOCAMP_DIR", Path.cwd())).resolve()
ORIGINAL_ROOT_DIR = Path(
    os.environ.get("QIBOCAMP_ORIGINAL_REPORT_DIR", REPORT_ROOT_DIR)
).resolve()
SERVER_NAME = os.environ.get("QIBOCAMP_SERVER_NAME", "local-instance")


def attachment(filename: str) -> str:
    """A `Content-Disposition` downloading as `filename`, whatever its characters.

    Folder names come from anywhere — an upload, say — while a header is
    Latin-1, and a `"` would end the plain name: an ASCII approximation for
    old clients, and the name itself, encoded, as RFC 6266 has it.
    """
    from urllib.parse import quote

    plain = "".join(c if c.isascii() and c.isprintable() and c not in '"\\' else "_" for c in filename)
    return f"attachment; filename=\"{plain}\"; filename*=utf-8''{quote(filename, safe='')}"


def set_report_root(path: Path, is_original: bool = False) -> None:
    """Set the root directory to scan for reports."""
    global REPORT_ROOT_DIR, ORIGINAL_ROOT_DIR
    resolved = path.resolve()
    if is_original or "ORIGINAL_ROOT_DIR" not in globals() or ORIGINAL_ROOT_DIR is None:
        ORIGINAL_ROOT_DIR = resolved
    REPORT_ROOT_DIR = resolved
    invalidate_report_cache()


def get_report_root() -> Path:
    """Get the current root directory."""
    return REPORT_ROOT_DIR


def get_original_root() -> Path:
    """Get the original directory where the server was spawned."""
    return ORIGINAL_ROOT_DIR


@app.get("/api/qibocal", response_model=QibocalStatus, tags=["Qibocal"])
def qibocal_status(
    response: Response, _user: dict = Depends(require_viewer),
) -> QibocalStatus:
    response.headers["Cache-Control"] = "no-store"
    try:
        return qibocal_environment.get_qibocal_status()
    except qibocal_environment.EnvironmentOperationError as error:
        raise HTTPException(error.status_code, error.detail) from error


@app.get("/api/admin/qibocal/options", response_model=QibocalOptions, tags=["Qibocal"])
def qibocal_options(
    response: Response, _user: dict = Depends(require_admin),
) -> QibocalOptions:
    response.headers["Cache-Control"] = "no-store"
    try:
        return qibocal_environment.get_qibocal_options()
    except qibocal_environment.EnvironmentOperationError as error:
        raise HTTPException(error.status_code, error.detail) from error


@app.get("/api/admin/filesystem", response_model=FolderListing, tags=["Qibocal"])
def admin_filesystem(
    response: Response,
    _user: Annotated[dict, Depends(require_admin)],
    path: str = "",
) -> FolderListing:
    """The folders under a path on the server, for picking the one Qibocal is installed from.

    Any absolute path, the home folder without one: the sources are rarely
    under the reports, and administering the environment is already the
    administrator's — or anyone's, on an open server.
    """
    response.headers["Cache-Control"] = "no-store"
    target = Path(path).expanduser() if path.strip() else Path.home()
    if not target.is_absolute():
        raise HTTPException(status_code=400, detail="The path must be absolute.")
    target = target.resolve()
    if not target.is_dir():
        raise HTTPException(status_code=404, detail=f"No such folder: {target}")

    def is_project(folder: Path) -> bool:
        return any((folder / name).is_file() for name in ("pyproject.toml", "setup.py"))

    entries: list[FolderEntry] = []
    try:
        for item in sorted(target.iterdir(), key=lambda entry: entry.name.lower()):
            if item.name.startswith(".") or not item.is_dir():
                continue
            entries.append(FolderEntry(name=item.name, path=str(item), project=is_project(item)))
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=f"Cannot read {target}: {error}") from error
    return FolderListing(
        path=str(target),
        parent=None if target.parent == target else str(target.parent),
        entries=entries,
        project=is_project(target),
    )


@app.get("/api/admin/logs", tags=["Diagnostics"])
def admin_logs(
    response: Response,
    _user: Annotated[dict, Depends(require_admin)],
    after: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return log_history.snapshot(after)


@app.post("/api/admin/qibocal/install", response_model=QibocalStatus, tags=["Qibocal"])
def qibocal_install(
    body: QibocalInstallRequest, response: Response,
    _user: dict = Depends(require_admin),
) -> QibocalStatus:
    response.headers["Cache-Control"] = "no-store"
    try:
        return qibocal_environment.install_cli(body.option)
    except qibocal_environment.EnvironmentOperationError as error:
        raise HTTPException(error.status_code, error.detail) from error


@app.post("/api/admin/qibocal/install/stream", tags=["Qibocal"])
async def qibocal_install_stream(
    body: QibocalInstallRequest,
    _user: Annotated[dict, Depends(require_admin)],
) -> StreamingResponse:
    output = _InstallationOutput()
    task = asyncio.create_task(
        asyncio.to_thread(
            qibocal_environment.install_cli, body.option, output.append
        )
    )
    _INSTALL_TASKS.add(task)
    task.add_done_callback(_installation_finished)
    while not output.started and not task.done():
        await asyncio.sleep(INSTALL_STREAM_POLL_INTERVAL)
    if not output.started:
        error = task.exception()
        if isinstance(error, qibocal_environment.EnvironmentOperationError):
            raise HTTPException(error.status_code, error.detail) from error
        task.result()
    return StreamingResponse(
        _installation_events(task, output),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.post("/api/admin/qibocal/install/stop", tags=["Qibocal"])
def qibocal_install_stop(
    response: Response,
    _user: dict = Depends(require_admin),
) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    try:
        qibocal_environment.stop_qibocal_installation()
    except qibocal_environment.EnvironmentOperationError as error:
        raise HTTPException(error.status_code, error.detail) from error
    return {"detail": "Qibocal installation stop requested."}


def unchanged_since(request: Request, response: Response) -> Response | None:
    """Answer a list or statistics request from the client's own copy when nothing changed.

    The index has a token — the newest mtime it was built from — which goes
    out as a weak `ETag`; a request bringing it back in `If-None-Match` is
    answered `304 Not Modified`, and the client keeps what it has.
    `Cache-Control: private, no-cache` has the browser ask every time, and
    keep the copy to itself. `scan_reports` first, so the token is current.
    """
    etag = f'W/"{report_index_token()}"'
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, no-cache"
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "private, no-cache"})
    return None


# --- Health Endpoint ---
@app.get("/api/version", response_model=VersionResponse, tags=["Health"])
def get_version() -> VersionResponse:
    """The server's version, the API it speaks, and its features: public, as the API is."""
    return VersionResponse(**version_info())


@app.get("/api/health", response_model=HealthResponse, tags=["Health"])
def health_check(
    response: Response,
    user: dict | None = Depends(get_current_user_optional),
) -> HealthResponse:
    """Server health status and basic info."""
    # Prevent browsers from serving a stale cached body on retries/reloads.
    response.headers["Cache-Control"] = "no-store"
    if auth.is_auth_enabled() and not user:
        return HealthResponse(
            status="ok",
            server_name=SERVER_NAME,
            reports_count=0,
            root_dir="",
            original_root_dir="",
        )
    # Counted from the index as it stands: a health ping must not walk the tree.
    return HealthResponse(
        status="ok",
        server_name=SERVER_NAME,
        reports_count=cached_report_count(REPORT_ROOT_DIR),
        root_dir=str(REPORT_ROOT_DIR),
        original_root_dir=str(ORIGINAL_ROOT_DIR),
    )


# --- Authentication Endpoints ---
@app.get("/api/auth/status", response_model=AuthStatusResponse, tags=["Authentication"])
def get_auth_status(response: Response) -> AuthStatusResponse:
    """Check whether server authentication is active."""
    # Prevent browsers from serving a stale cached body on retries/reloads.
    response.headers["Cache-Control"] = "no-store"
    enabled = auth.is_auth_enabled()
    has_users = bool(auth.load_auth_data().get("users"))
    return AuthStatusResponse(
        auth_enabled=enabled,
        server_name=SERVER_NAME,
        has_users=has_users,
    )


@app.post("/api/auth/login", response_model=LoginResponse, tags=["Authentication"])
def login_user(req: LoginRequest) -> LoginResponse:
    """Authenticate with username and password."""
    if not auth.is_auth_enabled():
        synthetic_user = {
            "id": "admin",
            "username": req.username or "admin",
            "role": UserRole.ADMIN.value,
            "created_at": "",
        }
        token = auth.create_access_token(synthetic_user)
        return LoginResponse(
            access_token=token,
            token_type="bearer",
            user=UserModel(**synthetic_user),
        )
    user = auth.authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = auth.create_access_token(user)
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        user=UserModel(**user),
    )


@app.get("/api/auth/me", response_model=UserModel, tags=["Authentication"])
def get_current_user_profile(user: dict = Depends(require_authenticated)) -> UserModel:
    """Get current authenticated user profile."""
    return UserModel(
        id=user["id"],
        username=user["username"],
        role=user["role"],
        created_at=user.get("created_at", ""),
    )


@app.get(
    "/api/auth/invite/{token}",
    response_model=InviteValidateResponse,
    tags=["Authentication"],
)
def check_invitation(token: str) -> InviteValidateResponse:
    """Validate an invitation token."""
    valid, reason, inv = auth.validate_invite(token)
    if not valid or not inv:
        return InviteValidateResponse(
            valid=False,
            token=token,
            detail=reason,
            server_name=SERVER_NAME,
        )
    return InviteValidateResponse(
        valid=True,
        token=token,
        role=inv.get("role"),
        target_username=inv.get("target_username"),
        expires_at=inv.get("expires_at"),
        server_name=SERVER_NAME,
    )


@app.get(
    "/api/auth/password-reset/{token}",
    response_model=InviteValidateResponse,
    tags=["Authentication"],
)
def check_password_reset(token: str) -> InviteValidateResponse:
    """Validate a password reset token."""
    valid, reset = auth.validate_password_reset(token)
    if not valid or not reset:
        return InviteValidateResponse(
            valid=False,
            token=token,
            detail="Password reset token not found or has expired",
            server_name=SERVER_NAME,
        )
    user = auth.get_user_by_username(reset.get("username") or "")
    return InviteValidateResponse(
        valid=True,
        token=token,
        role=(user or {}).get("role", UserRole.VIEWER.value),
        target_username=reset.get("username"),
        expires_at=reset.get("expires_at"),
        server_name=SERVER_NAME,
    )


@app.post("/api/auth/register", response_model=LoginResponse, tags=["Authentication"])
def register_user(req: RegisterRequest) -> LoginResponse:
    """Self-register or reclaim access using a valid invitation or password reset token."""
    # Checked and used in one step: a single-use token registers one account.
    with auth.store_lock():
        return _register_user(req)


def _register_user(req: RegisterRequest) -> LoginResponse:
    # Try to validate as a password reset token first
    valid_reset, reset = auth.validate_password_reset(req.invite_token)
    if valid_reset and reset:
        try:
            target_username = reset.get("username")
            if req.username.strip().lower() != target_username.strip().lower():
                raise ValueError(
                    f"This password reset token is specifically for '{target_username}'"
                )
            user = auth.reset_user_password(target_username, req.password)
            # Mark the password reset as used
            auth.delete_password_reset(req.invite_token)
            token = auth.create_access_token(user)
            return LoginResponse(
                access_token=token,
                token_type="bearer",
                user=UserModel(**user),
            )
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err))

    # Fall back to invite token validation
    valid, reason, inv = auth.validate_invite(req.invite_token)
    if not valid or not inv:
        raise HTTPException(status_code=400, detail=reason)
    try:
        target_username = inv.get("target_username")
        if target_username:
            if req.username.strip().lower() != target_username.strip().lower():
                raise ValueError(
                    f"This invitation token is specifically reserved for '{target_username}'"
                )
            user = auth.reset_user_password(target_username, req.password)
        else:
            user = auth.create_user(
                username=req.username,
                password=req.password,
                role=inv["role"],
            )
        auth.use_invite(req.invite_token)
        token = auth.create_access_token(user)
        return LoginResponse(
            access_token=token,
            token_type="bearer",
            user=UserModel(**user),
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))


# --- Admin Management Endpoints ---
@app.get(
    "/api/admin/users",
    response_model=list[UserModel],
    tags=["Admin"],
    dependencies=[Depends(require_admin)],
)
def admin_list_users() -> list[UserModel]:
    """List all registered users."""
    users = auth.list_users()
    return [UserModel(**u) for u in users]


@app.put(
    "/api/admin/users/{user_id}/role",
    response_model=UserModel,
    tags=["Admin"],
    dependencies=[Depends(require_admin)],
)
def admin_update_user_role(user_id: str, req: UserRoleUpdate) -> UserModel:
    """Update role for a user."""
    try:
        updated = auth.update_user_role(user_id, req.role)
        return UserModel(**updated)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))


@app.delete(
    "/api/admin/users/{user_id}",
    tags=["Admin"],
    dependencies=[Depends(require_admin)],
)
def admin_delete_user(user_id: str) -> dict[str, bool]:
    """Delete a user account."""
    try:
        success = auth.delete_user(user_id)
        if not success:
            raise HTTPException(status_code=404, detail="User not found")
        return {"deleted": True}
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))


@app.get(
    "/api/admin/invites",
    response_model=list[InviteModel],
    tags=["Admin"],
    dependencies=[Depends(require_admin)],
)
def admin_list_invites() -> list[InviteModel]:
    """List active invitation tokens."""
    invites = auth.list_invites()
    return [InviteModel(**i) for i in invites]


@app.post(
    "/api/admin/invites",
    response_model=InviteModel,
    tags=["Admin"],
)
def admin_create_invite(
    req: InviteCreateRequest,
    user: dict = Depends(require_admin),
) -> InviteModel:
    """Create a new invitation token."""
    try:
        inv = auth.create_invite(
            role=req.role,
            expires_in_hours=req.expires_in_hours,
            max_uses=req.max_uses,
            created_by=user.get("username", "admin"),
            target_username=req.target_username,
        )
        return InviteModel(**inv)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))


@app.delete(
    "/api/admin/invites/{token}",
    tags=["Admin"],
    dependencies=[Depends(require_admin)],
)
def admin_delete_invite(token: str) -> dict[str, bool]:
    """Revoke an invitation token."""
    success = auth.delete_invite(token)
    if not success:
        raise HTTPException(status_code=404, detail="Invitation token not found")
    return {"deleted": True}


@app.get(
    "/api/admin/password-resets",
    response_model=list[PasswordResetModel],
    tags=["Admin"],
    dependencies=[Depends(require_admin)],
)
def admin_list_password_resets() -> list[PasswordResetModel]:
    """List active password reset tokens."""
    resets = auth.list_password_resets()
    return [PasswordResetModel(**r) for r in resets]


@app.post(
    "/api/admin/password-resets",
    response_model=PasswordResetModel,
    tags=["Admin"],
)
def admin_create_password_reset(
    req: PasswordResetCreateRequest,
    user: dict = Depends(require_admin),
) -> PasswordResetModel:
    """Create a new password reset token for a user."""
    try:
        reset = auth.create_password_reset(
            user_id=req.user_id,
            created_by=user.get("username", "admin"),
        )
        return PasswordResetModel(**reset)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))


@app.delete(
    "/api/admin/password-resets/{token}",
    tags=["Admin"],
    dependencies=[Depends(require_admin)],
)
def admin_delete_password_reset(token: str) -> dict[str, bool]:
    """Revoke a password reset token."""
    success = auth.delete_password_reset(token)
    if not success:
        raise HTTPException(status_code=404, detail="Password reset token not found")
    return {"deleted": True}


@app.get(
    "/api/admin/config",
    response_model=AdminConfigResponse,
    tags=["Admin"],
    dependencies=[Depends(require_admin)],
)
def admin_get_config() -> AdminConfigResponse:
    """Get server configuration overview and stats."""
    reports = scan_reports(REPORT_ROOT_DIR)
    users = auth.list_users()
    invites = auth.list_invites()
    return AdminConfigResponse(
        auth_enabled=auth.is_auth_enabled(),
        server_name=SERVER_NAME,
        root_dir=str(REPORT_ROOT_DIR),
        original_root_dir=str(ORIGINAL_ROOT_DIR),
        reports_count=len(reports),
        users_count=len(users),
        invites_count=len(invites),
    )


# --- Uploads: reports sent here from another server, through the dashboard ---


@app.get("/api/uploads", response_model=UploadCapability, tags=["Reports"])
def upload_capability(
    response: Response, user: Annotated[dict | None, Depends(get_current_user)]
) -> UploadCapability:
    """Whether the caller may upload reports here: a server without
    authentication accepts anyone; one with it, whom its policy names."""
    response.headers["Cache-Control"] = "no-store"
    return UploadCapability(accepted=auth.may_upload(user), auth_enabled=auth.is_auth_enabled())


@app.post("/api/uploads", response_model=UploadResult, tags=["Reports"])
async def upload_report(
    request: Request, user: Annotated[dict | None, Depends(get_current_user)]
) -> UploadResult:
    """Receive a report: the zip of one report folder, as a download gives it.

    It lands at the top of the reports directory under its folder's name; a
    report of that name already here is a 409, and nothing is overwritten.
    """
    if not auth.may_upload(user):
        if user is None:
            raise HTTPException(
                status_code=401,
                detail="Sign in to upload reports to this server.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        raise HTTPException(status_code=403, detail="You may not upload reports to this server.")
    limit = upload_limit()
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as received:
        path = Path(received.name)
        size = 0
        try:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status_code=413, detail="The report is larger than this server accepts.")
                received.write(chunk)
        except BaseException:
            received.close()
            path.unlink(missing_ok=True)
            raise
    try:
        destination = await asyncio.to_thread(receive_report, path, REPORT_ROOT_DIR)
    except UploadError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    finally:
        path.unlink(missing_ok=True)
    invalidate_report_cache()
    log_info(f"Received report {destination.name} from {user.get('username') if user else 'anonymous'}")
    return UploadResult(id=destination.relative_to(REPORT_ROOT_DIR).as_posix(), name=destination.name)


@app.get("/api/admin/uploads", response_model=UploadPolicy, tags=["Reports"])
def get_upload_policy(_user: Annotated[dict, Depends(require_admin)]) -> UploadPolicy:
    """Who may upload here, beyond the administrators."""
    return UploadPolicy(**auth.get_upload_policy())


@app.put("/api/admin/uploads", response_model=UploadPolicy, tags=["Reports"])
def put_upload_policy(
    policy: UploadPolicy, _user: Annotated[dict, Depends(require_admin)]
) -> UploadPolicy:
    """Change who may upload here: administrators always may."""
    try:
        return UploadPolicy(**auth.set_upload_policy(policy.policy, policy.users))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


class InferTargetsResponse(BaseModel):
    reports: int
    updated: int
    without_targets: int


@app.post(
    "/api/admin/reports/infer-targets",
    response_model=InferTargetsResponse,
    tags=["Reports"],
)
async def admin_infer_targets(
    response: Response, _user: Annotated[dict, Depends(require_admin)]
) -> InferTargetsResponse:
    """Gather each report's targets from its protocols, into `targets-inferred`.

    Reads every protocol's `action.yml` under the reports, so it is run on
    request rather than at every scan; the scanner prefers what it wrote.
    """
    response.headers["Cache-Control"] = "no-store"
    counts = await asyncio.to_thread(infer_targets, REPORT_ROOT_DIR)
    log_info(
        f"Inferred targets for {counts['updated']} of {counts['reports']} report(s)"
    )
    return InferTargetsResponse(**counts)


# --- Server Directory Management Endpoints ---
def _get_relative_current() -> str:
    """Return relative path of REPORT_ROOT_DIR compared to ORIGINAL_ROOT_DIR."""
    try:
        cur = REPORT_ROOT_DIR.resolve()
        orig = ORIGINAL_ROOT_DIR.resolve()
        if cur == orig:
            return ""
        return cur.relative_to(orig).as_posix()
    except ValueError:
        return str(REPORT_ROOT_DIR)


def _count_reports_fast(path: Path, root_dir: Path | None = None) -> int:
    """Fast count of reports in a folder without affecting main report cache."""
    from qibocamp.reports.scanner import (
        IGNORED_DIRS,
        is_discovery_directory,
        is_report_directory,
    )

    root_dir = root_dir if root_dir is not None else path
    if not is_discovery_directory(path, root_dir):
        return 0
    if is_report_directory(path, root_dir):
        return 1
    count = 0
    for dirpath, dirnames, _ in os.walk(path):
        dirnames[:] = [
            d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")
        ]
        dp = Path(dirpath)
        if dp != path and is_report_directory(dp, root_dir):
            count += 1
            dirnames.clear()
    return count


@app.get(
    "/api/server/directory",
    response_model=ServerDirectoryInfo,
    tags=["Server Directory"],
    dependencies=[Depends(require_viewer)],
)
def get_server_directory_info() -> ServerDirectoryInfo:
    """Get original root and currently active server directory information."""
    reports = scan_reports(REPORT_ROOT_DIR)
    return ServerDirectoryInfo(
        original_root=str(ORIGINAL_ROOT_DIR),
        current_root=str(REPORT_ROOT_DIR),
        relative_current=_get_relative_current(),
        reports_count=len(reports),
    )


@app.get(
    "/api/server/directory/browse",
    response_model=DirectoryBrowseResponse,
    tags=["Server Directory"],
    dependencies=[Depends(require_viewer)],
)
def browse_server_directory(
    path: str = "",
    scope: str = "original",
) -> DirectoryBrowseResponse:
    """Browse subdirectories of the originally spawned server directory or current report root."""
    from qibocamp.reports.scanner import is_discovery_directory, is_report_directory

    base_root = (
        REPORT_ROOT_DIR.resolve()
        if scope in ("root", "current")
        else ORIGINAL_ROOT_DIR.resolve()
    )
    clean_subpath = path.strip().lstrip("/")
    target = (base_root / clean_subpath).resolve()

    # Strict security check: target must be inside or equal to base_root
    if not (target == base_root or base_root in target.parents):
        raise HTTPException(
            status_code=403,
            detail=(
                "Access forbidden: cannot browse outside server folder"
            ),
        )

    if not is_discovery_directory(target, base_root):
        raise HTTPException(
            status_code=404,
            detail=f"Directory '{clean_subpath}' not found",
        )

    # Compute breadcrumbs
    root_display_name = base_root.name or "root"
    breadcrumbs = [DirectoryBreadcrumb(name=root_display_name, path="")]
    if clean_subpath:
        rel_parts = Path(clean_subpath).parts
        accum: list[str] = []
        for part in rel_parts:
            accum.append(part)
            breadcrumbs.append(DirectoryBreadcrumb(name=part, path="/".join(accum)))

    # Compute parent path
    if target == base_root:
        parent_path = None
    else:
        parent_rel = target.parent.relative_to(base_root).as_posix()
        parent_path = "" if parent_rel == "." else parent_rel

    # List subdirectories (treating report folders as leaves and omitting them)
    subdirs: list[DirectoryEntry] = []
    try:
        for item in sorted(target.iterdir(), key=lambda x: x.name.lower()):
            if (
                is_discovery_directory(item, base_root)
                and not is_report_directory(item, base_root)
            ):
                has_sub = any(
                    is_discovery_directory(c, base_root)
                    and not is_report_directory(c, base_root)
                    for c in item.iterdir()
                )
                is_cur = item.resolve() == REPORT_ROOT_DIR.resolve()
                item_rel = item.relative_to(base_root).as_posix()
                count = _count_reports_fast(item, base_root)
                subdirs.append(
                    DirectoryEntry(
                        name=item.name,
                        path=item_rel,
                        has_subdirs=has_sub,
                        is_current=is_cur,
                        reports_count=count,
                    )
                )
    except PermissionError:
        pass

    current_rel = "" if target == base_root else target.relative_to(base_root).as_posix()
    is_active_root = target.resolve() == REPORT_ROOT_DIR.resolve()
    reports_in_current = _count_reports_fast(target, base_root)

    return DirectoryBrowseResponse(
        original_root=str(ORIGINAL_ROOT_DIR.resolve()),
        current_root=str(REPORT_ROOT_DIR.resolve()),
        current_browse_path=current_rel,
        parent_path=parent_path,
        breadcrumbs=breadcrumbs,
        directories=subdirs,
        is_active_root=is_active_root,
        reports_count=reports_in_current,
    )


@app.post(
    "/api/server/directory",
    response_model=ServerDirectoryInfo,
    tags=["Server Directory"],
    dependencies=[Depends(require_editor)],
)
def change_server_directory(req: ChangeDirectoryRequest) -> ServerDirectoryInfo:
    """Change report root directory (must be subfolder of spawned directory)."""
    from qibocamp.reports.scanner import is_report_directory

    orig = ORIGINAL_ROOT_DIR.resolve()
    clean_subpath = req.path.strip().lstrip("/")
    target = (orig / clean_subpath).resolve()

    # Strict security check: target must be inside or equal to ORIGINAL_ROOT_DIR
    if not (target == orig or orig in target.parents):
        raise HTTPException(
            status_code=403,
            detail=(
                "Target directory must be a subfolder of the originally spawned "
                "server folder"
            ),
        )

    if not target.is_dir():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Target directory '{clean_subpath}' does not exist or is not "
                "a directory"
            ),
        )

    if is_report_directory(target, target):
        raise HTTPException(
            status_code=400,
            detail=(
                "Cannot select a calibration report folder as the server root directory"
            ),
        )

    set_report_root(target)
    reports = scan_reports(REPORT_ROOT_DIR)

    return ServerDirectoryInfo(
        original_root=str(orig),
        current_root=str(REPORT_ROOT_DIR),
        relative_current=_get_relative_current(),
        reports_count=len(reports),
    )


# --- Reports & Search Endpoints ---
@app.get(
    "/api/reports",
    response_model=PaginatedReportsResponse | list[ReportSummary],
    tags=["Reports"],
    dependencies=[Depends(require_viewer)],
)
def get_reports(
    request: Request,
    response: Response,
    q: str | None = None,
    folder: str | None = None,
    author: Annotated[list[str] | None, Query()] = None,
    platform: Annotated[list[str] | None, Query()] = None,
    protocol: Annotated[list[str] | None, Query()] = None,
    tag: Annotated[list[str] | None, Query()] = None,
    qubit: Annotated[list[str] | None, Query()] = None,
    start_date: str | None = None,
    end_date: str | None = None,
    sort_by: str = "date_desc",
    page: int | None = Query(None, ge=1, description="Page number (1-indexed)"),
    page_size: int | None = Query(None, ge=1, le=500, description="Items per page"),
) -> PaginatedReportsResponse | list[ReportSummary]:
    """List and filter Qibocal reports, optionally paginated."""
    all_reports = scan_reports(REPORT_ROOT_DIR)
    if (unchanged := unchanged_since(request, response)) is not None:
        return unchanged
    filtered = filter_reports(
        all_reports,
        query=q,
        folder=folder,
        authors=author,
        platforms=platform,
        tags=tag,
        protocols=protocol,
        qubits=qubit,
        start_date=start_date,
        end_date=end_date,
        sort_by=sort_by,
    )

    total = len(filtered)
    response.headers["X-Total-Count"] = str(total)

    if page is not None or page_size is not None:
        page_num = page or 1
        effective_page_size = page_size or 20
        total_pages = (
            max(1, (total + effective_page_size - 1) // effective_page_size)
            if total > 0
            else 1
        )
        start_idx = (page_num - 1) * effective_page_size
        end_idx = start_idx + effective_page_size
        items = filtered[start_idx:end_idx]

        response.headers["X-Page"] = str(page_num)
        response.headers["X-Page-Size"] = str(effective_page_size)
        response.headers["X-Total-Pages"] = str(total_pages)

        return PaginatedReportsResponse(
            items=items,
            total=total,
            page=page_num,
            page_size=effective_page_size,
            total_pages=total_pages,
        )

    return filtered


@app.get(
    "/api/reports/stats",
    response_model=FilterStats,
    tags=["Reports"],
    dependencies=[Depends(require_viewer)],
)
def get_filter_statistics(
    request: Request,
    response: Response,
    q: str | None = None,
    folder: str | None = None,
    author: Annotated[list[str] | None, Query()] = None,
    platform: Annotated[list[str] | None, Query()] = None,
    protocol: Annotated[list[str] | None, Query()] = None,
    tag: Annotated[list[str] | None, Query()] = None,
    qubit: Annotated[list[str] | None, Query()] = None,
    start_date: str | None = None,
    end_date: str | None = None,
    sort_by: str = "date_desc",
) -> FilterStats:
    """Return filter statistics: protocol frequencies, authors, date histogram."""
    all_reports = scan_reports(REPORT_ROOT_DIR)
    if (unchanged := unchanged_since(request, response)) is not None:
        return unchanged
    filtered = filter_reports(
        all_reports,
        query=q,
        folder=folder,
        authors=author,
        platforms=platform,
        tags=tag,
        protocols=protocol,
        qubits=qubit,
        start_date=start_date,
        end_date=end_date,
        sort_by=sort_by,
    )
    return compute_filter_stats(filtered)


# --- Changing and removing comments: before the report-wide catch-alls below,
# which would otherwise read 'run/notes/0' as a report id. ---
def _may_change_note(user: dict, directory: Path, index: int, timestamp: datetime) -> None:
    """Whoever wrote a comment may change it; an administrator may change any."""
    if not auth.is_auth_enabled() or user["role"] == UserRole.ADMIN.value:
        return
    try:
        note = note_at(load_notes(directory), index, timestamp)
    except NoteMismatch as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if note.author != user["username"]:
        raise HTTPException(status_code=403, detail="Only its author can change this comment")


def _change_note(directory: Path, index: int, body: NoteUpdate) -> list[Note]:
    try:
        return replace_note(directory, index, body.timestamp, body.content)
    except NoteMismatch as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def _delete_note(directory: Path, index: int, timestamp: datetime) -> list[Note]:
    try:
        return remove_note(directory, index, timestamp)
    except NoteMismatch as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.put(
    "/api/reports/{report_id:path}/protocols/{protocol_id}/notes/{index}",
    response_model=list[Note],
    tags=["Reports"],
)
def change_protocol_note(
    report_id: str,
    protocol_id: str,
    index: int,
    body: NoteUpdate,
    response: Response,
    user: Annotated[dict, Depends(require_editor)],
) -> list[Note]:
    response.headers["Cache-Control"] = "no-store"
    directory = _notes_directory(report_id, protocol_id)
    _may_change_note(user, directory, index, body.timestamp)
    return _change_note(directory, index, body)


@app.delete(
    "/api/reports/{report_id:path}/protocols/{protocol_id}/notes/{index}",
    response_model=list[Note],
    tags=["Reports"],
)
def delete_protocol_note(
    report_id: str,
    protocol_id: str,
    index: int,
    timestamp: datetime,
    response: Response,
    user: Annotated[dict, Depends(require_editor)],
) -> list[Note]:
    response.headers["Cache-Control"] = "no-store"
    directory = _notes_directory(report_id, protocol_id)
    _may_change_note(user, directory, index, timestamp)
    return _delete_note(directory, index, timestamp)


@app.put(
    "/api/reports/{report_id:path}/notes/{index}",
    response_model=list[Note],
    tags=["Reports"],
)
def change_session_note(
    report_id: str,
    index: int,
    body: NoteUpdate,
    response: Response,
    user: Annotated[dict, Depends(require_editor)],
) -> list[Note]:
    response.headers["Cache-Control"] = "no-store"
    directory = _notes_directory(report_id)
    _may_change_note(user, directory, index, body.timestamp)
    return _change_note(directory, index, body)


@app.delete(
    "/api/reports/{report_id:path}/notes/{index}",
    response_model=list[Note],
    tags=["Reports"],
)
def delete_session_note(
    report_id: str,
    index: int,
    timestamp: datetime,
    response: Response,
    user: Annotated[dict, Depends(require_editor)],
) -> list[Note]:
    response.headers["Cache-Control"] = "no-store"
    directory = _notes_directory(report_id)
    _may_change_note(user, directory, index, timestamp)
    return _delete_note(directory, index, timestamp)



# --- Bulk & Single Report Actions ---
@app.post(
    "/api/reports/bulk-action",
    response_model=BulkActionResponse,
    tags=["Reports"],
    dependencies=[Depends(require_editor)],
)
def bulk_report_action(req: BulkActionRequest) -> BulkActionResponse:
    """Execute bulk actions (delete, label, unlabel, author) across reports."""
    return execute_bulk_action(REPORT_ROOT_DIR, req)


# Report ids are relative paths, which may hold a `/`: every route of one
# report takes it as a path, ahead of the catch-all DELETE below.
@app.delete(
    "/api/reports/{report_id:path}/label/{label_name:path}",
    response_model=BulkActionResponse,
    tags=["Reports"],
    dependencies=[Depends(require_editor)],
)
def remove_single_report_label(report_id: str, label_name: str) -> BulkActionResponse:
    """Remove a label or tag from a single report."""
    return execute_bulk_action(
        REPORT_ROOT_DIR,
        BulkActionRequest(action="unlabel", report_ids=[report_id], label=label_name),
    )


@app.put(
    "/api/reports/{report_id:path}/author",
    response_model=BulkActionResponse,
    tags=["Reports"],
    dependencies=[Depends(require_editor)],
)
def update_single_report_author(
    report_id: str, body: UpdateAuthorRequest
) -> BulkActionResponse:
    """Update the author of a single report."""
    return execute_bulk_action(
        REPORT_ROOT_DIR,
        BulkActionRequest(action="author", report_ids=[report_id], author=body.author),
    )


@app.post(
    "/api/reports/{report_id:path}/label",
    response_model=BulkActionResponse,
    tags=["Reports"],
    dependencies=[Depends(require_editor)],
)
def label_single_report(report_id: str, body: SingleLabelRequest) -> BulkActionResponse:
    """Add a label or tag to a single report."""
    return execute_bulk_action(
        REPORT_ROOT_DIR,
        BulkActionRequest(action="label", report_ids=[report_id], label=body.label),
    )


@app.delete(
    "/api/reports/{report_id:path}",
    response_model=BulkActionResponse,
    tags=["Reports"],
    dependencies=[Depends(require_editor)],
)
def delete_single_report(report_id: str) -> BulkActionResponse:
    """Delete a single report directory."""
    return execute_bulk_action(
        REPORT_ROOT_DIR, BulkActionRequest(action="delete", report_ids=[report_id])
    )


# --- Real-Time WebSocket ---
def socket_from_allowed_page(websocket: WebSocket) -> bool:
    """Whether a socket is opened by a page allowed to call: CORS does not cover sockets."""
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    scheme = "https" if websocket.url.scheme == "wss" else "http"
    return config.origin_allowed(origin, f"{scheme}://{websocket.url.netloc}")


@app.websocket("/ws/live/reports/{report_id:path}")
async def live_report_websocket_endpoint(websocket: WebSocket, report_id: str):
    """Subscribe to incremental plots and metadata; requires editor access."""
    if not socket_from_allowed_page(websocket):
        await websocket.close(code=1008)
        return
    token = websocket.query_params.get("token")

    def authorized():
        if not auth.is_auth_enabled():
            return True
        payload = auth.decode_access_token(token) if token else None
        user = auth.get_user_by_id(payload.get("sub")) if payload else None
        return bool(
            user and user["role"] in (UserRole.EDITOR.value, UserRole.ADMIN.value)
        )

    await handle_live_websocket(websocket, report_id, REPORT_ROOT_DIR, authorized)


@app.websocket("/ws/reports/{report_id:path}")
async def report_websocket_endpoint(websocket: WebSocket, report_id: str):
    """WebSocket endpoint for real-time report streaming and plot generation."""
    if not socket_from_allowed_page(websocket):
        await websocket.close(code=1008)
        return
    can_edit = True
    if auth.is_auth_enabled():
        token = websocket.query_params.get("token")
        payload = auth.decode_access_token(token) if token else None
        # A deleted user's token still decodes: the user must still be there.
        user = auth.get_user_by_id(payload.get("sub")) if payload else None
        if not user:
            await websocket.close(code=1008)
            return
        can_edit = user["role"] in (UserRole.EDITOR.value, UserRole.ADMIN.value)
    await handle_report_websocket(websocket, report_id, REPORT_ROOT_DIR, can_edit)


# --- Protocol Outputs & Regeneration ---
def _notes_directory(report_id: str, protocol_id: str | None = None) -> Path:
    root = REPORT_ROOT_DIR.resolve()
    report = next((r for r in scan_reports(root) if r.id == report_id), None)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    target = Path(report.path).resolve()
    if not target.is_dir() or not target.is_relative_to(root):
        raise HTTPException(status_code=404, detail="Report not found")
    if protocol_id is not None:
        directory = protocol_notes_directory(target, protocol_id)
        if directory is None:
            raise HTTPException(
                status_code=404, detail="Protocol not found or ambiguous"
            )
        return directory
    return target


@app.get(
    "/api/reports/{report_id:path}/protocols/{protocol_id}/notes",
    response_model=list[Note],
    tags=["Reports"],
    dependencies=[Depends(require_viewer)],
)
def get_protocol_notes(
    report_id: str, protocol_id: str, response: Response
) -> list[Note]:
    response.headers["Cache-Control"] = "no-store"
    return load_notes(_notes_directory(report_id, protocol_id))


@app.post(
    "/api/reports/{report_id:path}/protocols/{protocol_id}/notes",
    response_model=list[Note],
    tags=["Reports"],
)
def add_protocol_note(
    report_id: str,
    protocol_id: str,
    body: NoteCreate,
    response: Response,
    user: Annotated[dict, Depends(require_editor)],
) -> list[Note]:
    response.headers["Cache-Control"] = "no-store"
    directory = _notes_directory(report_id, protocol_id)
    author = user["username"] if auth.is_auth_enabled() else None
    notes = append_note(directory, body.content, author)
    log_info(f"Added protocol note for '{report_id}/{protocol_id}'")
    return notes


# The file of a protocol's parameters, as Qibocal writes it, with its media
# type: `params.json` (qibocal#1732), or the `action.yml` of the runs so far.
PARAMETER_FILES = {
    "params.json": "application/json",
    "action.yml": "application/yaml; charset=utf-8",
    "action.yaml": "application/yaml; charset=utf-8",
}


def _parameters_file(report_id: str, protocol_id: str) -> Path:
    """The parameter file Qibocal wrote next to a protocol's data."""
    target_dir = resolve_report_dir(REPORT_ROOT_DIR, report_id)
    data_dir = resolve_protocol_data_dir(target_dir, protocol_id)
    if data_dir is None or not data_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"Data directory for protocol '{protocol_id}' not found in report '{report_id}'",
        )
    for name in PARAMETER_FILES:
        path = data_dir / name
        if path.is_file() and not path.is_symlink():
            return path
    raise HTTPException(
        status_code=404, detail=f"No parameter file for protocol '{protocol_id}'"
    )


@app.get(
    "/api/reports/{report_id:path}/protocols/{protocol_id}/parameters",
    tags=["Reports"],
    dependencies=[Depends(require_viewer)],
    response_class=PlainTextResponse,
)
def get_protocol_parameters(
    report_id: str, protocol_id: str, download: bool = False
) -> Response:
    """The parameters the protocol was run with, as Qibocal wrote them.

    The file as it is — `params.json`, or `action.yml` — with its media type,
    and its name in `X-Parameters-File`; with `download=true`, as an
    attachment named after the report and the protocol.
    """
    path = _parameters_file(report_id, protocol_id)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Could not read {path.name}: {error}") from error
    headers = {"Cache-Control": "private, no-cache", "X-Parameters-File": path.name}
    if download:
        report_name = Path(path).parents[2].name.replace(":", "-")
        headers["Content-Disposition"] = attachment(f"{report_name}_{path.parent.name}_{path.name}")
    return PlainTextResponse(text, media_type=PARAMETER_FILES[path.name], headers=headers)


@app.get(
    "/api/reports/{report_id:path}/notes",
    response_model=list[Note],
    tags=["Reports"],
    dependencies=[Depends(require_viewer)],
)
def get_session_notes(report_id: str, response: Response) -> list[Note]:
    response.headers["Cache-Control"] = "no-store"
    return load_notes(_notes_directory(report_id))


@app.post(
    "/api/reports/{report_id:path}/notes",
    response_model=list[Note],
    tags=["Reports"],
)
def add_session_note(
    report_id: str,
    body: NoteCreate,
    response: Response,
    user: Annotated[dict, Depends(require_editor)],
) -> list[Note]:
    response.headers["Cache-Control"] = "no-store"
    directory = _notes_directory(report_id)
    author = user["username"] if auth.is_auth_enabled() else None
    notes = append_note(directory, body.content, author)
    log_info(f"Added session note for '{report_id}'")
    return notes


@app.get(
    "/api/reports/{report_id:path}/protocols",
    response_model=list[ProtocolDetail],
    tags=["Reports"],
    dependencies=[Depends(require_viewer)],
)
def get_protocols_for_report(report_id: str) -> list[ProtocolDetail]:
    """Get all protocol outputs (HTML and Plotly figures) for a report."""
    log_info(f"HTTP GET /protocols for '{report_id}'")
    target_dir = resolve_report_dir(REPORT_ROOT_DIR, report_id)
    return get_report_protocols(target_dir)


@app.post(
    "/api/reports/{report_id:path}/regenerate",
    response_model=list[ProtocolDetail],
    tags=["Reports"],
    dependencies=[Depends(require_editor)],
)
def regenerate_report_plots(report_id: str) -> list[ProtocolDetail]:
    """Regenerate protocol plots by deleting cached report and re-evaluating."""
    log_info(f"HTTP POST /regenerate for '{report_id}'")
    target_dir = resolve_report_dir(REPORT_ROOT_DIR, report_id)
    return regenerate_report(target_dir)


@app.get(
    "/api/reports/{report_id:path}/path",
    response_model=ReportPathResponse,
    tags=["Reports"],
)
def get_report_upload_path(
    report_id: str,
    response: Response,
    user: Annotated[dict, Depends(require_viewer)],
) -> ReportPathResponse:
    """Return the report folder path with role-appropriate filesystem visibility."""
    response.headers["Cache-Control"] = "no-store"
    root = REPORT_ROOT_DIR.resolve()
    report = next((r for r in scan_reports(root) if r.id == report_id), None)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    target = Path(report.path).resolve()
    if not target.is_dir() or not target.is_relative_to(root):
        raise HTTPException(status_code=404, detail="Report not found")
    absolute = not auth.is_auth_enabled() or user["role"] == UserRole.ADMIN.value
    return ReportPathResponse(
        path=str(target) if absolute else target.relative_to(root).as_posix(),
        is_absolute=absolute,
    )


# --- On-the-Fly Downloads ---
@app.get(
    "/api/reports/{report_id:path}/download/full",
    tags=["Reports"],
    dependencies=[Depends(require_viewer)],
)
def download_full_report_zip(report_id: str) -> StreamingResponse:
    """Download full protocol report folder compressed on the fly as a zip archive."""
    log_info(f"HTTP GET download full folder for '{report_id}'")
    target_dir = resolve_report_dir(REPORT_ROOT_DIR, report_id)
    safe_name = target_dir.name.replace(":", "-")
    buffer = zip_directory(target_dir, prefix=target_dir.name)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": attachment(f"{safe_name}.zip")},
    )


@app.get(
    "/api/reports/{report_id:path}/download/new-platform",
    tags=["Reports"],
    dependencies=[Depends(require_viewer)],
)
def download_new_platform_zip(report_id: str) -> StreamingResponse:
    """Download calibrated new_platform folder on the fly as a zip archive."""
    log_info(f"HTTP GET download new platform for '{report_id}'")
    target_dir = resolve_report_dir(REPORT_ROOT_DIR, report_id)
    new_plat = target_dir / "new_platform"
    if not new_plat.is_dir():
        log_error(f"HTTP 404: new_platform directory not found in '{report_id}'")
        raise HTTPException(
            status_code=404, detail=f"new_platform not found for report {report_id}"
        )
    safe_name = target_dir.name.replace(":", "-")
    buffer = zip_directory(new_plat, prefix="new_platform")
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": attachment(f"{safe_name}_new_platform.zip")
        },
    )


@app.get(
    "/api/reports/{report_id:path}/download/old-platform",
    tags=["Reports"],
    dependencies=[Depends(require_viewer)],
)
def download_old_platform_zip(report_id: str) -> StreamingResponse:
    """Download initial platform folder on the fly as a zip archive."""
    log_info(f"HTTP GET download old platform for '{report_id}'")
    target_dir = resolve_report_dir(REPORT_ROOT_DIR, report_id)
    plat = target_dir / "platform"
    if not plat.is_dir():
        log_error(f"HTTP 404: platform directory not found in '{report_id}'")
        raise HTTPException(
            status_code=404, detail=f"platform not found for report {report_id}"
        )
    safe_name = target_dir.name.replace(":", "-")
    buffer = zip_directory(plat, prefix="platform")
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": attachment(f"{safe_name}_old_platform.zip")
        },
    )


@app.get(
    "/api/reports/{report_id:path}/download/data/{protocol_id}",
    tags=["Reports"],
    dependencies=[Depends(require_viewer)],
)
def download_protocol_data_zip(report_id: str, protocol_id: str) -> StreamingResponse:
    """Download data directory for a specific protocol on the fly as a zip archive."""
    log_info(f"HTTP GET download protocol data for '{report_id}' / '{protocol_id}'")
    target_dir = resolve_report_dir(REPORT_ROOT_DIR, report_id)
    data_dir = resolve_protocol_data_dir(target_dir, protocol_id)
    if not data_dir or not data_dir.is_dir():
        log_error(
            f"HTTP 404: Protocol data not found for '{protocol_id}' in '{report_id}'"
        )
        raise HTTPException(
            status_code=404,
            detail=(
                f"Data directory for protocol '{protocol_id}' not found in report"
                f" '{report_id}'"
            ),
        )

    safe_rep = target_dir.name.replace(":", "-")
    safe_proto = data_dir.name.replace(":", "-")
    buffer = zip_directory(data_dir, prefix=data_dir.name)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": attachment(f"{safe_rep}_{safe_proto}.zip")
        },
    )


@app.get(
    "/api/reports/{report_id:path}/meta.json",
    tags=["Reports"],
    dependencies=[Depends(require_viewer)],
)
def get_report_meta_json(report_id: str) -> Response:
    """Access meta.json as plain inline JSON for browser rendering."""
    log_info(f"HTTP GET meta.json for '{report_id}'")
    target_dir = resolve_report_dir(REPORT_ROOT_DIR, report_id)
    meta_file = resolve_meta_file(target_dir)
    if not meta_file:
        log_error(f"HTTP 404: meta.json not found in '{report_id}'")
        raise HTTPException(
            status_code=404, detail=f"meta.json not found for report {report_id}"
        )
    return Response(
        content=meta_file.read_bytes(),
        media_type="application/json",
        headers={"Content-Disposition": "inline"},
    )


def _read_platform_json_or_yaml(file_prefix: Path) -> Any | None:
    for ext in (".json", ".yaml", ".yml"):
        cand = file_prefix.with_suffix(ext)
        if cand.is_file():
            try:
                with open(cand, encoding="utf-8") as f:
                    if ext in (".yaml", ".yml"):
                        import yaml

                        return yaml.safe_load(f)
                    return json.load(f)
            except Exception:
                pass
    return None


def platform_directory(target_dir: Path, platform_type: str) -> Path:
    """The platform of a report: `old`, as the run found it, or `new`, as it left it."""
    if platform_type not in ("old", "new"):
        raise HTTPException(status_code=404, detail=f"No platform '{platform_type}': old or new")
    return target_dir / ("platform" if platform_type == "old" else "new_platform")


@app.get(
    "/api/reports/{report_id:path}/platform/{platform_type}",
    response_model=PlatformDataResponse,
    tags=["Reports"],
    dependencies=[Depends(require_viewer)],
)
def get_report_platform_data(report_id: str, platform_type: str) -> PlatformDataResponse:
    """Get parameters.json and calibration.json trees for old or new platform."""
    log_info(f"HTTP GET platform data ({platform_type}) for '{report_id}'")
    target_dir = resolve_report_dir(REPORT_ROOT_DIR, report_id)
    plat_dir = platform_directory(target_dir, platform_type)

    has_old = (target_dir / "platform").is_dir()
    has_new = (target_dir / "new_platform").is_dir()

    meta = _parse_meta_json(target_dir / "meta.json")
    platform_name = meta.get("platform") or "Generic QPU"

    params_data = _read_platform_json_or_yaml(plat_dir / "parameters")
    calib_data = _read_platform_json_or_yaml(plat_dir / "calibration")

    return PlatformDataResponse(
        report_id=report_id,
        platform_name=platform_name,
        platform_type=platform_type,
        has_old_platform=has_old,
        has_new_platform=has_new,
        parameters=params_data,
        calibration=calib_data,
    )


@app.get(
    "/api/reports/{report_id:path}/platform/{platform_type}/{file_name}",
    tags=["Reports"],
    dependencies=[Depends(require_viewer)],
)
def get_report_platform_raw_file(
    report_id: str, platform_type: str, file_name: str
) -> Response:
    """Access raw parameters.json or calibration.json inline."""
    target_dir = resolve_report_dir(REPORT_ROOT_DIR, report_id)
    plat_dir = platform_directory(target_dir, platform_type)
    stem, _, suffix = file_name.partition(".")
    target_file = plat_dir / file_name
    if stem not in ("parameters", "calibration") or suffix not in ("json", "yml", "yaml") or not target_file.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"{file_name} not found in {platform_type} platform for report {report_id}",
        )
    return Response(
        content=target_file.read_bytes(),
        media_type="application/json" if target_file.suffix == ".json" else "text/plain",
        headers={"Content-Disposition": "inline"},
    )


@app.get(
    "/api/reports/{report_id:path}",
    response_model=ReportDetail,
    tags=["Reports"],
    dependencies=[Depends(require_viewer)],
)
def get_single_report(report_id: str) -> ReportDetail:
    """Get metadata, platform snapshot, history, and protocols summary for a report."""
    detail = get_report_detail(REPORT_ROOT_DIR, report_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")
    return detail


# --- Documentation Endpoints ---
@app.get(
    "/api/docs-nav",
    tags=["Documentation"],
)
def get_documentation_navigation() -> list[dict]:
    """Return the structured navigation tree for documentation."""
    return DOCS_NAVIGATION


@app.get(
    "/api/docs-content/{doc_name:path}",
    response_class=PlainTextResponse,
    tags=["Documentation"],
)
def get_documentation(doc_name: str) -> str:
    """Serve plain markdown documentation content."""
    return resolve_docs_content(doc_name)


# --- Archive Management Endpoints (Issue #2) ---
@app.get(
    "/api/archives",
    response_model=list[ArchiveMetadata],
    tags=["Archives"],
    dependencies=[Depends(require_viewer)],
)
def get_archives() -> list[ArchiveMetadata]:
    """List all available archives with metadata, ordered newest first."""
    storage = get_archive_storage_dir(REPORT_ROOT_DIR)
    archives = list_archives(storage)
    return [ArchiveMetadata(**a) for a in archives]


@app.post(
    "/api/archives",
    response_model=ArchiveMetadata,
    tags=["Archives"],
    dependencies=[Depends(require_editor)],
)
def post_create_archive(req: ArchiveCreateRequest) -> ArchiveMetadata:
    """Create a new archive from a selection of reports."""
    try:
        storage = get_archive_storage_dir(REPORT_ROOT_DIR)
        meta = create_archive(
            report_ids=req.report_ids,
            root_dir=REPORT_ROOT_DIR,
            name=req.name,
            description=req.description,
            filters=req.filters,
            remove_from_active=req.remove_from_active,
            storage_dir=storage,
        )
        if req.remove_from_active:
            invalidate_report_cache()
        return ArchiveMetadata(**meta)
    except ValueError as err:
        log_error(f"HTTP 400 creating archive: {err}")
        raise HTTPException(status_code=400, detail=str(err))
    except FileNotFoundError as err:
        log_error(f"HTTP 404 creating archive: {err}")
        raise HTTPException(status_code=404, detail=str(err))


@app.get(
    "/api/archives/{archive_id}",
    response_model=ArchiveMetadata,
    tags=["Archives"],
    dependencies=[Depends(require_viewer)],
)
def get_single_archive_metadata(archive_id: str) -> ArchiveMetadata:
    """Get metadata for a specific archive."""
    storage = get_archive_storage_dir(REPORT_ROOT_DIR)
    meta = get_archive_metadata(archive_id, storage)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Archive '{archive_id}' not found")
    return ArchiveMetadata(**meta)


@app.get(
    "/api/archives/{archive_id}/index",
    response_model=list[ArchiveReportIndexItem],
    tags=["Archives"],
    dependencies=[Depends(require_viewer)],
)
def get_single_archive_index(archive_id: str) -> list[ArchiveReportIndexItem]:
    """Get compact report index for peaking archive content without unzipping."""
    storage = get_archive_storage_dir(REPORT_ROOT_DIR)
    index_data = get_archive_index(archive_id, storage)
    if index_data is None:
        raise HTTPException(
            status_code=404, detail=f"Archive index for '{archive_id}' not found"
        )
    return [ArchiveReportIndexItem(**item) for item in index_data]


@app.get(
    "/api/archives/{archive_id}/download",
    tags=["Archives"],
    dependencies=[Depends(require_viewer)],
)
def download_archive_zip(archive_id: str) -> FileResponse:
    """Download the full zip file of an archive."""
    storage = get_archive_storage_dir(REPORT_ROOT_DIR)
    zip_path = get_archive_zip_path(archive_id, storage)
    if not zip_path or not zip_path.is_file():
        raise HTTPException(
            status_code=404, detail=f"Archive zip for '{archive_id}' not found"
        )
    meta = get_archive_metadata(archive_id, storage)
    raw_name = meta.get("name", archive_id) if meta else archive_id
    safe_name = "".join(c for c in raw_name if c.isalnum() or c in " ._-").strip() or archive_id
    if not safe_name.endswith(".zip"):
        safe_name += ".zip"
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=safe_name,
    )


@app.post(
    "/api/archives/{archive_id}/restore",
    tags=["Archives"],
    dependencies=[Depends(require_editor)],
)
def post_restore_archive(
    archive_id: str, req: ArchiveRestoreRequest | None = None
) -> dict[str, Any]:
    """Restore reports from an archive back to the active reports directory."""
    try:
        storage = get_archive_storage_dir(REPORT_ROOT_DIR)
        report_ids = req.report_ids if req else None
        delete_after = req.delete_after_restore if req else False
        res = restore_archive(
            archive_id=archive_id,
            root_dir=REPORT_ROOT_DIR,
            report_ids=report_ids,
            delete_after_restore=delete_after,
            storage_dir=storage,
        )
        invalidate_report_cache()
        return res
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err))


@app.delete(
    "/api/archives/{archive_id}",
    tags=["Archives"],
    dependencies=[Depends(require_editor)],
)
def delete_single_archive(archive_id: str) -> dict[str, Any]:
    """Delete an archive directory and its files."""
    storage = get_archive_storage_dir(REPORT_ROOT_DIR)
    success = delete_archive(archive_id, storage)
    if not success:
        raise HTTPException(status_code=404, detail=f"Archive '{archive_id}' not found")
    return {"success": True, "archive_id": archive_id}


@app.patch(
    "/api/archives/{archive_id}",
    response_model=ArchiveMetadata,
    tags=["Archives"],
    dependencies=[Depends(require_editor)],
)
def patch_archive_metadata(
    archive_id: str, req: ArchiveUpdateRequest
) -> ArchiveMetadata:
    """Update name or description of an archive."""
    storage = get_archive_storage_dir(REPORT_ROOT_DIR)
    updated = update_archive(
        archive_id=archive_id,
        name=req.name,
        description=req.description,
        storage_dir=storage,
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"Archive '{archive_id}' not found")
    return ArchiveMetadata(**updated)


# --- GraphQL: several of the questions above, answered in one request ---
# Included before the catch-all below, which would otherwise answer for it.
app.include_router(
    build_graphql_router(get_current_user, get_report_root),
    prefix="/api/graphql",
    tags=["GraphQL"],
)


# --- No page of its own: the server is headless ---
# The dashboard is deployed from the frontend package, and embedded in the
# desktop launcher; either reads any number of servers. Whoever opens the
# address in a browser is told where to go.
ROOT_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Qibocamp Server</title>
<style>
body { font: 15px/1.5 system-ui, sans-serif; max-width: 36em; margin: 4em auto; padding: 0 1em; color: #1f2937; }
a { color: #833dff; }
code { background: #f3f4f6; padding: 0.1em 0.3em; border-radius: 0.25em; }
</style>
</head>
<body>
<h1>Qibocamp Server</h1>
<p>This server has no page of its own: <a id="connect" href="{dashboard}">open it in the Qibocamp Dashboard</a>, which adds it to your servers when it is not there yet. In the Qibocamp desktop application, add its address, <code id="address">this page's</code>, under Servers.</p>
<p>The API is under <code>/api</code>; its documentation is at <a href="/api/docs/swagger">/api/docs/swagger</a>.</p>
<script>
// The address as the browser reached it — behind a proxy, under a prefix —
// which the server itself may not know.
const here = location.origin + location.pathname.replace(/\/+$/, "");
document.getElementById("connect").href = {dashboard_json} + "/#/connect?server=" + encodeURIComponent(here);
document.getElementById("address").textContent = here;
</script>
</body>
</html>
"""


@app.get("/", include_in_schema=False)
async def root() -> HTMLResponse:
    dashboard = config.DASHBOARD_URL
    page = ROOT_PAGE.replace("{dashboard_json}", json.dumps(dashboard).replace("</", "<\\/"))
    return HTMLResponse(page.replace("{dashboard}", html.escape(dashboard, quote=True)))
