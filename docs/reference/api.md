# Reference: REST API & WebSockets

The Qibocamp Server exposes a fully typed OpenAPI 3.1 REST API along with real-time WebSocket capabilities.

Interactive Swagger documentation is available at **`/api/docs/swagger`**, and the raw OpenAPI specification is available at **`/api/openapi.json`**.

---

## 🩺 Health & Server Diagnostics

### `GET /api/admin/logs?after=0`
Available on open servers; requires a signed-in administrator when authentication
is enabled (the same authorization as Qibocal environment administration). Returns bounded
in-memory server log history with ANSI color escapes preserved:

```json
{"entries": [{"id": 1, "text": "\u001b[32mServer ready\u001b[0m\n"}], "cursor": 1}
```

Pass the last `cursor` as `after` to read newer entries. Logs include Rich
application messages and Python/uvicorn logging, and disappear on server
restart. Responses use `Cache-Control: no-store`.

### `GET /api/version`
What the server is, for a client to check before relying on it; public, with
or without authentication. `api_version` is raised on every breaking change
of the REST or GraphQL API, and only then; `features` names what a client may
rely on beyond it; `commit` is the one the installation was made from, when
pip recorded it — a Git URL or the archive of a commit — and `null` otherwise.
The dashboard shows the version on each server's card, and warns when the API
is not the one it speaks.

```json
{
  "version": "0.1.0a1",
  "api_version": 1,
  "commit": "0123456789abcdef0123456789abcdef01234567",
  "features": ["graphql", "uploads", "archives", "notes", "live", "parameters", "progressive"]
}
```

`progressive`: a report opens without generating anything, and the report socket generates its plots a batch at a time ([`WS /ws/reports/{report_id}`](#ws-wsreportsreport_id)).

### `GET /api/health`
Returns system status, active server identifier, report count, and scanned root directory.

#### Response Example
```json
{
  "status": "ok",
  "server_name": "local-sample_data",
  "reports_count": 4,
  "root_dir": "/path/to/sample_data"
}
```

---

### `GET /api/uploads`
Whether the caller may upload reports here: `{"accepted", "auth_enabled"}`. A server without authentication accepts anyone; one with it, its administrators and whom its upload policy names. What a sender asks before uploading.

### `POST /api/uploads`
Receive a report: the body is the zip of one report folder, as `/api/reports/{id}/download/full` gives it (`Content-Type: application/zip`). It is checked — one top-level folder holding a `meta.json`, no absolute path, no `..`, no link, at most `QIBOCAMP_UPLOAD_LIMIT` bytes unpacked (4 GiB) — and unpacked at the top of the reports directory under its folder's name. Answers `{"id", "name"}`; `401` to sign in, `403` for an account the policy leaves out, `409` when a report of that name is already there, which is never overwritten, `413` when too large.

### Uploading a report by hand

What `qibocamp report upload` does, with `curl` — from any machine, with no Qibocamp installed. Zip the report folder under its own name, from the folder holding it:

```bash
cd ./runs
zip -r report.zip "21:47:29_[3]_pi-pulse"
```

A server with authentication wants a token: sign in for one — a server without it needs none, and the `Authorization` headers below can be left out.

```bash
SERVER=https://reports.example.org
TOKEN=$(curl -s "$SERVER/api/auth/login" -H 'Content-Type: application/json' \
  -d '{"username": "alice", "password": "…"}' | python3 -c 'import json, sys; print(json.load(sys.stdin)["access_token"])')
```

Ask whether the upload is taken, then send it:

```bash
curl -s "$SERVER/api/uploads" -H "Authorization: Bearer $TOKEN"
# {"accepted":true,"auth_enabled":true}

curl -s -X POST "$SERVER/api/uploads" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/zip' --data-binary @report.zip
# {"id":"21:47:29_[3]_pi-pulse","name":"21:47:29_[3]_pi-pulse"}
```

The body is the zip itself, not a form: `--data-binary`, not `-F`. A refusal says why in `detail`, with the statuses above: `401` to sign in, `403` for an account the policy leaves out, `409` for a report of that name already there, `413` when too large.

### `GET /api/admin/uploads`, `PUT /api/admin/uploads`
Who may upload, beyond the administrators, who always may: `{"policy": "admins" | "editors" | "viewers", "users": [user ids]}` — editors too, every signed-in user, and chosen individuals. Kept in `auth.json`. Administrators only.

### `POST /api/admin/reports/infer-targets`
Read every protocol's `action.yml` under the reports for the qubits, or pairs, it was run on, and write their union into each report's `meta.json` as `targets-inferred`, next to the session's own `targets`, which is kept; the list shows the inferred targets first. Answers `{"reports", "updated", "without_targets"}`. Reads every report, so it runs on request — here, from **Server Administration**, or with `qibocamp report targets` — rather than at every scan. Administrators only.

---

## 🚚 Transport

- **Compression.** Responses above a kilobyte are gzipped when the client accepts it; protocol figures, which are Plotly JSON, shrink five to ten times.
- **Conditional requests.** `GET /api/reports` and `GET /api/reports/stats` carry a weak `ETag` — the newest modification time the report index was built from — and `Cache-Control: private, no-cache`. A request bringing the tag back in `If-None-Match` is answered `304 Not Modified`.
- **Index validation.** The report index is validated by walking the report tree; a walk stands for `QIBOCAMP_SCAN_INTERVAL` seconds (default `2`; `0` walks at every request) before the next, so a burst of requests costs one walk.
- **What is not sent.** `search_index`, the server's full-text index of a report, stays on the server; `platform_snapshot` is no longer inlined in a report's detail, nor in the WebSocket `metadata` message. `GET /api/health` counts reports from the index as it stands, without validating it.

## 🧬 GraphQL

### `POST /api/graphql`
One request where the REST API needs several. The schema answers, together,
what the dashboard asks at once — the page of reports, the statistics of
everything and of what is filtered; a report and its protocols; whether a
server authenticates and who the caller is — and keeps the REST shapes field
for field: names are `snake_case`, so a GraphQL result is read exactly as the
REST response it stands for. Queries only; mutations stay with the REST
endpoints. A GET of the same address opens GraphiQL, the schema's own explorer.

| Query | Answers as | Role |
| --- | --- | --- |
| `reports(where: ReportFilter, page: Int = 1, page_size: Int = 20)` | `GET /api/reports` with the same filters, paginated | viewer |
| `stats(where: ReportFilter)` | `GET /api/reports/stats` | viewer |
| `report(id: String!)` | `GET /api/reports/{id}`; its `protocols_detail` field lists every protocol, in execution order, without generating anything — those not generated yet have status `pending` and no outputs; `null` for a report that does not exist | viewer |
| `protocol(report_id: String!, protocol_id: String!)` | one protocol, for its `html` and `figures` alone; status `pending`, without outputs, when not generated yet; `null` when either is not there | viewer |
| `session` | `GET /api/auth/status` and `GET /api/auth/me`, in one: `auth_enabled`, and the `user` the token names, or `null` | anyone |
| `health` | `GET /api/health` | anyone |

`ReportFilter` names the query parameters of `/api/reports`, in the plural
where they repeat: `query`, `folder`, `authors`, `platforms`, `protocols`,
`tags`, `qubits`, `start_date`, `end_date`, `sort_by`.

```graphql
query Dashboard($where: ReportFilter, $page: Int!, $page_size: Int!) {
  reports(where: $where, page: $page, page_size: $page_size) {
    items { id title date author platform targets protocols tags has_cached_report }
    total page page_size total_pages
  }
  stats { protocols { name count } date_histogram { date count } total_reports }
  filtered: stats(where: $where) { total_reports }
}
```

A report is opened in two steps by the dashboard: `report(id)` with
`protocols_detail { id name status num_figures notes { … } }` — the cards,
without the megabytes they show — then `protocol(report_id, protocol_id) { html
figures }` for each card as it nears the viewport, and for the rest in idle
time. Pre-cached outputs are parsed once on the server and kept in memory while
their files are unchanged, so the per-protocol requests cost a lookup. The
protocols still `pending` are generated over the report socket,
[`WS /ws/reports/{report_id}`](#ws-wsreportsreport_id), and never by a GraphQL
request.

Authorization is the same bearer token, in the same header. A field refused
for want of a session comes back `null`, with an error whose
`extensions.code` is `UNAUTHENTICATED` — the 401 of the REST API — or
`FORBIDDEN` for the 403; the response itself is a 200, as GraphQL responses
are.

---

## 📑 Reports & Filtering

### `GET /api/reports`
Retrieve a paginated, filtered list of calibration reports.

#### Query Parameters
- `q`: Free-text search matching title, platform, author, or tags.
- `folder`: Only the reports under this folder of the root.
- `author`, `platform`, `protocol`, `tag`: Filter by author handle, platform, executed routine name, or tag; each may repeat.
- `qubit`: Filter by target qubit, a pair counting for both of its qubits; may repeat.
- `start_date`, `end_date`: Filter by date range (`YYYY-MM-DD`).
- `sort_by`: Sorting order (`date_desc`, `date_asc`, `title`).
- `page`, `page_size`: Paginate, 20 to a page unless said otherwise; without either, the whole list.

### `GET /api/reports/stats`
Returns aggregated statistical distributions across all discovered reports:
- `authors`: Unique authors list.
- `tags`: Unique tags list.
- `protocols`: Array of `{ name, count }` sorted by frequency descending.
- `platforms`: Platform distribution.
- `date_histogram`: Array of `{ date, count }` chronologically ordered.

### `GET /api/reports/{report_id}`
Retrieve detailed metadata, history, and protocol summaries for a single report. The `platform_snapshot` field is empty here: the platform files are served by `GET /api/reports/{report_id}/platform/{type}`, and by the GraphQL `report(id) { platform_snapshot }` field when selected.

Report details include session `notes`; protocol outputs include per-execution
`notes`. Both default to `[]` for reports without a notes file. Notes are read
from their source files independently of cached plots.

### `GET /api/reports/{report_id}/protocols/{protocol_id}/parameters`
The parameters the protocol was run with, as Qibocal wrote them next to its
data: the file as it is — `params.json` (`application/json`), or the
`action.yml` of earlier runs (`application/yaml`) — its name in the
`X-Parameters-File` header. With `?download=true` it comes as an attachment
named after the report and the protocol. `404` when the protocol, or its
parameter file, is not there.

### `GET /api/reports/{report_id}/notes`
### `GET /api/reports/{report_id}/protocols/{protocol_id}/notes`
Return session or protocol comment histories. Requires viewer access on
authenticated servers. Responses use `Cache-Control: no-store`.

### `POST /api/reports/{report_id}/notes`
### `POST /api/reports/{report_id}/protocols/{protocol_id}/notes`
Append a comment and return the complete updated history. Requires editor or
administrator access on authenticated servers; open servers also allow writes.
The body is `{"content": "Reviewed the fit."}`. Blank content and extra fields
are rejected. The server supplies the UTC timestamp and authenticated username,
or `null` author on open servers:

```json
[{"content": "Reviewed the fit.", "timestamp": "2026-10-10T06:00:00Z", "author": "editor"}]
```

Use the exact protocol execution ID, including its iteration (e.g. `rabi-1`).
Ambiguous legacy names are rejected rather than assigned to an arbitrary
iteration. Unknown report or protocol IDs return 404. Invalid stored histories
return an explicit error and are never silently replaced. Server writes are
serialized across workers and saved atomically.

### `PUT /api/reports/{report_id}/notes/{index}`
### `PUT /api/reports/{report_id}/protocols/{protocol_id}/notes/{index}`
### `DELETE /api/reports/{report_id}/notes/{index}?timestamp=…`
### `DELETE /api/reports/{report_id}/protocols/{protocol_id}/notes/{index}?timestamp=…`
Change, or remove, one comment, and return the complete updated history. A
comment is addressed by its position in the history and its timestamp — in the
body for `PUT` (`{"content": "…", "timestamp": "2026-10-10T06:00:00Z"}`), as a
query parameter for `DELETE` — so a client holding a stale history cannot touch
the wrong one: a mismatch is a `409`, and the client reloads. A changed comment
keeps its author and its timestamp. On authenticated servers only the author
of a comment, or an administrator, may change it (`403` otherwise); open
servers allow anyone. The comments' content is Markdown, rendered by the
dashboard with raw HTML neutralised. External writers (including Qibocal's list-replace
API) must coordinate writes with the report server to avoid losing comments.

### `GET /api/reports/{report_id}/path`
Return the report folder path for use with `qq upload`. Available on open servers
and to signed-in viewers, editors, and administrators. Open servers and
administrators receive an absolute filesystem path; viewers and editors receive
a path relative to the server's currently selected report root, without exposing
the absolute root. Responses use `Cache-Control: no-store`.

```json
{"path": "2026-10-09/calibration-run", "is_absolute": false}
```

---

## 🔬 Protocol Outputs & Regeneration

### `GET /api/reports/{report_id}/protocols`
Fetch all protocol outputs including formatted HTML tables and serialized Plotly figure structures.

Failed outputs have `status: "error"` and an informative `error` message. The
optional `error_code: "qibocal_not_installed"` identifies a genuinely missing
Qibocal installation. Missing dependencies, incompatible Qibocal APIs, invalid
report data, and individual routine failures retain their own error messages
without that code. Partial figures and tables remain available when a routine
fails for some targets.

### `POST /api/reports/{report_id}/regenerate`
Purge the cached `report/` folder for this run and recompute figures on the fly using Qibocal evaluation routines, in one request. The dashboard regenerates over the report socket instead, protocol by protocol.

`GET /api/reports/{report_id}/protocols` likewise answers every protocol with its outputs, generating those missing first, in one go.

Generation uses a fresh process in the server's Python environment, with a
five-minute timeout, so subsequent requests use a newly installed Qibocal version
without restarting the server.

---

## 🧩 Qibocal Server Environment

### `GET /api/qibocal`
Inspect Qibocal's installed package metadata without importing its plotting
dependencies. Requires viewer access when authentication is enabled; it is also
available on open servers. Responses use `Cache-Control: no-store`.

```json
{"installed": true, "version": "0.2.7", "source": "pypi"}
```

If absent, `installed` is `false` and both `version` and `source` are `null`.
`source` is `"pypi"` or `"git"` for recognized installations, or `null` for an
unrecognized installation origin.

### `GET /api/admin/filesystem?path=`
The folders under an absolute path on the server — the home folder without one — for picking the one Qibocal is installed from: `path`, `parent`, `project` (whether it holds a `pyproject.toml` or `setup.py`), and `entries` with the same for each subfolder, hidden ones left out. Administrators only; anyone on an open server.

### `GET /api/admin/qibocal/options`
Available on open servers. When authentication is enabled, requires a logged-in
user whose current role is **admin**.

Returns the same installed status plus up to five recent stable, non-yanked
PyPI releases compatible with the server's Python version, plus the latest
compatible release from the previous major line if not already included.
For 0.x releases, the previous minor line is used instead (for example,
0.1.x alongside 0.2.x). The fixed official Git repository option follows.
Versions are ordered by Python package version semantics, not alphabetically.
Responses use `Cache-Control: no-store`.

```json
{
  "installed": false,
  "version": null,
  "source": null,
  "options": [
    {
      "id": "pypi:0.2.7",
      "label": "Qibocal 0.2.7 (PyPI)",
      "source": "pypi",
      "version": "0.2.7"
    },
    {
      "id": "git",
      "label": "Git repository (latest)",
      "source": "git",
      "version": null
    }
  ],
  "pypi_error": null,
  "git_branches": ["main", "0.1", "feature/branch"],
  "git_default_branch": "main",
  "github_error": null
}
```

Releases come from `https://pypi.org/pypi/qibocal/json`. If PyPI is unavailable
or returns invalid data, `pypi_error` explicitly describes the failure and the
Git option remains available.

Branches are fetched from the official `qiboteam/qibocal` GitHub repository,
including all pages of the branch listing. The default branch comes first,
followed by the other branches alphabetically. If GitHub is unavailable or
rate-limits the server, `github_error` describes the failure, branch choices
are empty, and the default `"git"` option remains available.

### `POST /api/admin/qibocal/install`
Uses the same open-server or administrator authorization as the options endpoint.

- **Body**: `{"option": "pypi:0.2.7"}`, `{"option": "git"}`, or
  `{"option": "git:feature/branch"}`.
- PyPI selections are revalidated against the server's recent compatible
  options, then installed as an exact `qibocal==<version>` pin.
- `"git"` always installs `git+https://github.com/qiboteam/qibocal.git`; arbitrary
  URLs, package names, versions outside the offered choices, and installer
  arguments are rejected.
- `"git:<branch>"` resolves the named branch through the official repository's
  GitHub API and pins installation to its verified commit SHA. Arbitrary Git
  refs and installer arguments are never interpolated into the command.
  Installed package metadata must confirm that commit before success.
- Installation upgrades/reinstalls Qibocal using the server's `sys.executable`
  through pip, or `uv pip --python <sys.executable>` when pip is unavailable.
  The installer has a ten-minute timeout.
- Installs and plot generation are synchronized. Concurrent installation
  requests return **409**.
- Success returns `{"installed": true, "version": "...", "source": "pypi"}`,
  or `"source": "git"`, only after package metadata confirms the installation.
  Responses use `Cache-Control: no-store`.

Errors return a descriptive `detail`: **400** for an invalid selection,
**401/403** for insufficient authentication/permissions, **409** for concurrent
installation or a busy generation environment, **502** for PyPI/GitHub validation or
installer failure, **503** for an unavailable installer, **504** for installation
timeout, and **500** if installed metadata cannot confirm the requested result.

Installing a version does not clear any report caches. Regenerate the current
report with its existing `/regenerate` endpoint to refresh cached outputs.

This changes the **server's Python environment**, not the browser or just the
current report. Qibocal and its resolved dependencies are shared by every report
and user on that server; other processes using the same environment can also be
affected. Run the report server in a dedicated virtual environment rather than
a shared or system-wide Python environment. Administrator authorization limits
who can request installation but does not isolate dependency changes.

### `POST /api/admin/qibocal/install/stream`
Accepts the same body and strict administrator authorization as `/install`,
but responds with `application/x-ndjson` for live installer progress. Each
newline-delimited JSON event is one of:

```json
{"type": "output", "text": "Collecting qibocal...\n"}
{"type": "complete", "status": {"installed": true, "version": "0.2.7", "source": "pypi"}}
{"type": "error", "detail": "Qibocal installation failed..."}
```

Output preserves ANSI colors. Authentication and request validation failures
use normal HTTP error responses; installation failures after streaming begins
use an `error` event. Do not treat HTTP 200 or an interrupted stream as successful
installation: wait for `complete`. Disconnecting does not interrupt the
server-side installation. The diagnostics footer uses this endpoint and
regenerates the open report or preview only after confirmed success.

---

## 💾 On-the-Fly Downloads & Meta

### `GET /api/reports/{report_id}/download/full`
Streams the entire calibration run directory as a compressed `.zip` archive.

### `GET /api/reports/{report_id}/download/new-platform`
Streams the calibrated `new_platform/` configuration directory as a `.zip` archive.

### `GET /api/reports/{report_id}/download/old-platform`
Streams the initial `platform/` configuration directory as a `.zip` archive.

### `GET /api/reports/{report_id}/download/data/{protocol_id}`
Streams the data directory for a specific protocol routine as a `.zip` archive.

### `GET /api/reports/{report_id}/meta.json`
Returns `meta.json` directly as an inline `application/json` payload for browser inspection.

---

## ⚡ Bulk & Report Actions

### `POST /api/reports/bulk-action`
Executes batch operations across multiple reports.

#### Payload Schema
```json
{
  "action": "label", // "label" | "unlabel" | "author" | "delete"
  "report_ids": ["10:50:56_[0]_resonator_punchout", "21:47:29_[3]_pi-pulse"],
  "label": "verified",
  "author": "new_author"
}
```

### `POST /api/reports/{report_id}/label`
Add a tag to a single report: the body is `{"label": "verified"}`, written to the report's `tag` in `meta.json`.

### `DELETE /api/reports/{report_id}/label/{label_name}`
Remove a tag from a single report.

### `PUT /api/reports/{report_id}/author`
Update the author handle for a single report.

### `DELETE /api/reports/{report_id}`
Delete a single report directory from disk.

---

## 🔌 WebSockets

### `WS /ws/reports/{report_id}`
A report's metadata, then its plots as they are generated: the protocols not
generated yet, a batch of six at a time, those the page has on screen first.
Every page with the report open shares one generation, which stops after the
batch under way once none is left; each batch is cached as it ends, so the next
page goes on from there. Pass the access token in the `token` query parameter
when authentication is enabled.

The server sends:

```json
{"type": "metadata", "report": {"id": "run", "history": ["rabi-0", "t1-0"]}}
{"type": "queued", "ids": ["t1-0"], "regenerating": [], "generating": ["t1-0"]}
{"type": "protocol", "protocol": {"id": "t1-0", "name": "T1", "status": "success", "html": "...", "figures": []}}
{"type": "done"}
{"type": "error", "message": "Regenerating plots needs editor access."}
```

`queued` says what is left — `generating`, the batch under way, included — and
which of those are regenerated, whose previous outputs stand until replaced;
it comes when a page connects and with every batch. A `protocol` comes as soon
as its batch ends, notes attached. A protocol that fails is cached as failed
when it had no outputs; one that had keeps them, and its `protocol` message
carries them with status `error` and why. When Qibocal produces nothing — not
installed, say — every protocol of the batch is answered with that error,
`error_code` included, and nothing is cached, so that it is tried again.

The page sends:

```json
{"type": "visible", "ids": ["t1-0", "ramsey-0"]}
{"type": "regenerate", "ids": ["t1-0"]}
{"type": "regenerate", "ids": null}
"ping"
```

`visible`, the placeholders on screen, generated first; `regenerate`, some
protocols again or, with `null`, all of them, which requires editor or
administrator access; `ping`, answered `pong`.

### `WS /ws/live/reports/{report_id}`
Subscribe to incremental report updates. Requires editor or administrator access
when authentication is enabled; pass the access token in the `token` query
parameter, as with the report-loading socket. Authorization is rechecked while
subscribed. Disconnecting stops monitoring.

The server checks `data/{task_id}/` inputs every second and debounces changes
until two consecutive snapshots match. It excludes generated `report/` artifacts
and worker result files. Changes to metadata or execution order refresh metadata
without re-plotting unchanged tasks.

```json
{"type": "live", "active": true}
{"type": "snapshot", "protocols": [{"id": "rabi-0", "name": "Rabi", "status": "success", "figures": []}]}
{"type": "metadata", "report": {"id": "run", "history": ["rabi-0"]}}
{"type": "update", "protocols": [{"id": "rabi-0", "name": "Rabi", "status": "success", "figures": []}], "removed": []}
{"type": "error", "message": "Live update failed: ..."}
```

An initial `snapshot` contains all currently cached protocols, including updates
missed while disconnected; use it to reconcile the complete displayed list.
In subsequent `update` messages, `protocols` contains only changed or new outputs.
Merge them by `id` and delete IDs in `removed`. Metadata carries a complete report
detail. Generation errors do not necessarily close the connection; permission
revocation and deleted reports do.

Incremental outputs are atomically merged into `report/protocols.json`. Input
fingerprints in `report/.live-inputs.json` allow subsequent subscriptions to
detect changes and share cached results. A cache without fingerprints — one
written by Qibocal itself, say — takes its existing cached tasks as the initial
baseline. Full regeneration still
discards all cached outputs.

---

## 📖 Documentation Content

### `GET /api/docs-content/{doc_name}`
Serve raw markdown documentation content for the in-app documentation reader.

### `GET /api/docs-nav`
Returns the structured documentation navigation tree (sections, pages, and titles).
