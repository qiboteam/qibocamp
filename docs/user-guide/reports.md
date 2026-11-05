# User Guide: Reports, Protocols & Exports

Selecting any calibration run from the dashboard opens the dedicated **Report View** (`/#/reports/{id}`), providing a deep dive into experimental measurements, fitted parameters, and hardware snapshots.

---

## 🧭 Navigation & Sidebar

The left-hand sidebar serves as a quick navigator across all executed routines in the report:

- **Protocol Status Badges**: Quickly see which protocols succeeded (green checkmark) or experienced warnings/failures.
- **Execution Timings**: Review the duration of each individual routine alongside total experiment runtime.
- **Quick Jumping**: Click any protocol in the list to scroll smoothly to its corresponding card.
- **Report Overview**: The first index entry returns to the report header.
- **Collapsible Sidebar**: Click the toggle chevron at the bottom of the sidebar to collapse it to compact icon mode, maximizing screen real estate for charts.

---

## Slideshow

One slide at a time is the default: the report overview followed by one slide
per protocol. The toggle in the report action bar names the mode it switches
to — **Continuous** while the slideshow is on, which scrolls the whole report,
and **Slideshow** while it is off. The overview includes clickable protocol thumbnails;
plots, status, and the thumbnail list update in Live mode. The thumbnails come a
page at a time, 12, 24 or 48 to a page, and only those of the page are drawn;
**Newest first**, the default, or **Oldest first** orders them, and both choices
are remembered. Coming back to the overview from a protocol shows the page its
thumbnail is on.

Use the previous/next buttons or **Left/Right** arrow keys to navigate. **Up**
returns to the overview; **Down** jumps to the last protocol. In either view,
`j`/`l` and `k`/`h` step to the next and previous protocol — the card under
the cursor is outlined in the continuous view — `v` switches the view, `b`
goes back to the search, `d` opens the documentation of the selected protocol,
`x` downloads its data, `c` toggles its comments (the session's, from the
header), `n` and `o` open the new and old platform pages, and `w` toggles the
live mode. Plots are drawn as they come near the viewport, and the rest in the
idle moments after, so a large report shows its first screen at once. On a
server with the GraphQL endpoint, a pre-cached report is also *fetched* that
way: the page opens with the protocols named, and each card's figures arrive
as it nears the viewport, the rest in idle time. Sidebar entries
work in both expanded and collapsed mode. Arrow shortcuts do not intercept text
editing, chart interactions, or modal controls.

Each slide is a page of the browser's history: its address names the protocol,
`?protocol=<id>`, and the overview's names none. The browser's **Back** returns
to the slide shown before — from a protocol opened on the overview, to the
overview — and **Forward** goes again; a slide's address can be bookmarked or
shared. Live following and the continuous view leave the history alone.

In Live mode, viewing the last protocol slide follows newly arriving protocols.
Selecting an earlier slide or the overview stops following; returning to the
last protocol resumes it. The sidebar still follows independently when its index
is at the bottom. Switching back to the scroll view keeps your selected protocol
in view. Printing includes the complete report in either view mode.

---

## 📊 Protocol Cards & Visualizations

Each protocol routine is presented in an individual card:

### 1. Interactive Plotly Charts
- Interactive 2D and 3D graphics powered by **Plotly.js**.
- **Zoom & Pan**: Click and drag to box-zoom into resonance peaks, Rabi oscillations, or Ramsey fringes. Double-click to reset zoom.
- **Hover Inspection**: Move your cursor across data points to inspect exact frequencies, amplitudes, phases, and error bars.
- **Snapshot Export**: Use the camera icon in the Plotly toolbar to export publication-quality PNG images.

### 2. Formatted Results Tables
- Injected protocol summary tables displaying fitted parameters ($f_{01}$, $\pi$-pulse amplitude, $T_1$, $T_2^*$, readout fidelity).
- Styled natively with clean borders, monochromatic purple headers, and typography aligned with the application theme.

### 3. Direct Protocol Data Download
- Each protocol card includes an individual **Download Protocol Data (.zip)** button in its header.
- Compresses and downloads only the data directory associated with that specific routine for rapid offline analysis in Python or Jupyter.

---

## Qubit pairs

A protocol on a pair of qubits has the pair for a target: it reads as `Q2–Q3`
in the list, in the header of its report and in the filter chip, which filters
for that pair; filtering by a single qubit also finds the pairs it is in. Its
plots are generated like any other protocol's.

## Where the targets come from

The qubits a report lists are the session's `targets` in its `meta.json`,
which is often empty or stale; its protocols know better, each naming in its
`action.yml` what it was run on. **Infer targets**, in Server Administration —
or `qibocamp report targets` — gathers their union into the report's meta as
`targets-inferred`, which the list, the header and the filters then show
first; the session's own list is kept. It reads every report, so it runs on
request rather than at every load.

## The parameters of a protocol

Each protocol card folds, under its outputs, a **Parameters** line: the file
Qibocal wrote next to the protocol's data with the parameters it was run with —
`params.json`, or the `action.yml` of earlier runs — named on the line. Closed by default, it is read from the server when opened, and
**Download** at the end of the line saves the file itself.

---

## Session and protocol notes

**Session comments** below the report header and **Protocol comments** at the
foot of each protocol card fold under a single line — the count, and how long
ago the last one was written — so a laptop screen keeps its room; open, the
thread reads in the card's own small text, each comment with its author, where
there is one, and its time as a distance from now. Each protocol execution
(including its iteration) has its own history.

Comments are Markdown: emphasis, code, lists, links and tables render; raw HTML
and images do not. The field grows as you type, and `Cmd/Ctrl+Enter` sends.

Editors and administrators can add comments; viewers can read them. Open
servers also allow comments, with no author attached. On authenticated servers,
the server records the signed-in username. A comment can be changed or removed
by whoever wrote it, or by an administrator — the controls appear at the end of
its line on hover; on an open server, by anyone. A changed comment keeps its
author and its time. If the thread changed under you since it was read, the
change is refused and the thread reloaded, so nobody edits the wrong comment.

Notes use Qibocal's `notes.json` in the session folder and
`data/<task-id>/notes.json` for individual executions. Older reports without
these files remain viewable with empty histories. Adding a comment creates only
the corresponding history; it does not change acquisition data, fitting results,
or metadata. Notes remain available with cached plots or when plot generation
fails, and survive plot regeneration. Live mode refreshes comments without
regenerating plots.

---

## 💾 On-The-Fly ZIP Downloads & Inspection

The sidebar includes instant download actions generated dynamically on the fly:

| Button / Icon | Description | Endpoint |
| :--- | :--- | :--- |
| **Download Protocol (.zip)** | Full archive containing all data, logs, platform snapshots, and cached figures. | `GET /api/reports/{id}/download/full` |
| **Copy report path** *(Copy icon)* | Copy the server-side report folder path for use with `qq upload`. | `GET /api/reports/{id}/path` |
| **New Platform (.zip)** *(Sparkled chip)* | Calibrated hardware parameters resulting from the run, packaged as a platform folder. | `GET /api/reports/{id}/download/new-platform` |
| **Old Platform (.zip)** *(Plain chip)* | Initial pre-calibration hardware parameters used at the start of the experiment. | `GET /api/reports/{id}/download/old-platform` |
| **Meta.json (`{}`)** | View raw execution metadata: in a new browser tab, or — inside the desktop application, or when the browser refuses the tab — over the page, indented, with **Copy** and **Download**. | `GET /api/reports/{id}/meta.json` |

On authentication-enabled servers, these actions use your sign-in session,
including for remote instances. Protocol-data, platform, and archive downloads
use the same authenticated requests. If your browser blocks the metadata tab,
allow pop-ups for the report application and try again.

**Copy report path** is available in both expanded and collapsed sidebar modes.
It copies an absolute path on open servers and for signed-in administrators.
Viewers and editors receive a path relative to the server's currently selected
report root. The path refers to the report server, not the browser's computer;
use relative paths from that root when running `qq upload` on the server.

---

## Uploading to another server

A report can be copied to another report server, as `qq upload` copies one to a web page: **Upload to another server**, in the row of download buttons of a report's sidebar, or **Upload** in the bar that appears when reports are selected in the list. The dialog lists the other registered servers, each saying what it makes of uploads — *accepts uploads*, *sign in to this server*, *your account may not upload here* — and takes a server's address as well, under **By address**, but only one that accepts uploads without signing in: a server with authentication has to be registered, to sign in to it. Each report is downloaded from the server it is on — the same zip as the download button — and sent to the one chosen, where it lands at the top of the reports directory under its folder's name; a report of that name already there is left as it is, and said so. The dialog says how each report went.

From the command line, `qibocamp report upload PATH SERVER` sends a report folder of this machine, or the zip of one, the same way; see the [CLI reference](../reference/cli.md), and the [API reference](../reference/api.md) for the requests by hand.

A server without authentication accepts uploads from anyone who reaches it. One with authentication accepts them from its administrators, and from whom **Uploads**, in Server Administration, adds: editors, every signed-in user, or chosen individuals. A report is checked before anything is written — one folder holding a `meta.json`, no absolute path, no `..`, no link — and may not exceed 4 GiB once unpacked, or `QIBOCAMP_UPLOAD_LIMIT` bytes.

## 🔄 On-The-Fly Generation and Regeneration

A report opens at once, every protocol named, whether its plots exist yet or
not. Those not generated yet show *Generating plots…*, and the server generates
them a batch at a time — the ones on screen first — each card filling as soon
as its batch ends. The page stays usable meanwhile. What is generated is kept:
closing the page stops the generation after the batch under way, and opening
the report again goes on from there. Two pages on the same report share one
generation, and see the same cards fill.

A protocol that cannot be generated says why in its own card; the others carry
on.

To plot again — after installing another Qibocal version, say:

- **Regenerate**, in the sidebar, regenerates every protocol; **Regenerate**, on
  a protocol's card, that protocol alone. Both need editor access.
- A protocol being regenerated is marked *regenerating*, and keeps its previous
  plots until the new ones replace them, so the report stays readable
  throughout.
- A protocol whose regeneration fails keeps its previous plots, marked with the
  error.

### Live updates during calibration

Click **Live** in the report action bar to follow an execution automatically.
A slowly blinking red light indicates an active connection. The server checks
task data once per second and waits for two stable snapshots before generating
plots. Only new or changed protocols are evaluated; unchanged plots stay in
place. Metadata and removed protocols are updated as well.

When a new protocol arrives, the plots panel automatically shows its card if the
previous last card was visible. The sidebar index independently follows new
protocols only when it is scrolled to the bottom, in either expanded or collapsed
mode. Scroll away in either panel to stop following there; return to the last
card or the bottom of the index to resume. Automatic scrolling is disabled
outside Live mode.

Live mode requires editor or administrator access on authenticated servers,
and Qibocal must be installed on the server to generate plots. Click **Live**
again to stop. Leaving the report, switching servers, changing your sign-in
session, or manually regenerating plots also stops the subscription. A lost
connection is reported explicitly; click **Live** to reconnect.

Generation failures are shown without deleting previously cached good plots.
Incomplete data writes are retried when their inputs change. Regeneration already
in progress may finish after you stop Live, but no further updates are scheduled.

### Installing or switching Qibocal

Both the dashboard plot preview and the full report explicitly show generation
errors, including when Qibocal is not installed on the report server.
Pre-cached plots remain viewable without Qibocal.

Users of open (unauthenticated) servers and signed-in administrators of
authentication-enabled servers have a thin
grey handle with a small central accent marker at the very bottom of every
page. It starts fully collapsed.
Drag it upward to open the diagnostics footer, drag it to adjust the height,
and drag it back down to collapse. Clicking does not toggle the footer, and
there are no separate size or close controls. The **Server logs** tab shows
recent server output in full terminal color and follows live output. Uncheck
**Follow output** to read earlier messages. Logs are a bounded, in-memory history
for the current server process, not a persistent archive.

The **Qibocal** tab contains version management. Missing-package warnings
direct users with access to drag open the footer and select this tab. The button grid
offers five recent compatible stable PyPI releases, plus the latest compatible
release from the previous major line (the previous minor line for 0.x, such as
0.1.x alongside 0.2.x).
The full-width **Git** button below them uses the official
[Git repository](https://github.com/qiboteam/qibocal). Its dropdown arrow lets
you choose any available branch; the repository's default branch is selected
initially. A branch installation is pinned to the commit verified by GitHub.
The current server version is shown in the tab. Git installations also show
the installed branch as a link to its GitHub page, opening in a new tab.
The branch name is preserved for installations made through the branch picker.
Git installations made otherwise, whose metadata only records a commit, show
**branch unknown**; reinstall the desired branch from here to retain its name.
If PyPI is unavailable, the
error is displayed and the Git option remains available. Select a version using
the highlighted buttons (PyPI versions show their logo), then use **Install**
(the package-plus icon) to install or switch. **Refresh** (the circular-arrows
icon) reloads available versions.
Full source names and action labels are available as tooltips and accessible text.
If GitHub cannot list branches, the error is shown and installation from the
default Git branch remains available. The last cell, **Local**, installs from a
folder on the server — a checkout of Qibocal there — chosen by typing its path
or browsing the server's folders with the magnifier beside it; folders holding
a Python project are marked. The installation is editable: changes in the
folder are live at the next plot, with no reinstall. Administrators only.

**Install / Switch version** (the package-plus icon) changes Qibocal in the server's
Python environment for all users, then regenerates the current report's cached
plots. Qibocal itself is put back by force; its dependencies are left as they are
unless the new version asks for others, and a version that comes as a wheel is
preferred to a newer one that would have to be compiled — so a release published
minutes earlier, before its wheels, does not turn an installation into a build. Other reports' cached plots are not changed; use **Regenerate Plots** to
refresh them when needed. Future plot generation uses the installed version
without a server restart. Viewers and editors on authentication-enabled servers
cannot access the diagnostics footer or install or switch versions through the
application. Installation progress is streamed into the tab as colored pip
(or uv) output, including failures. You can collapse the footer or navigate
within the application without interrupting installation. Closing the browser
also does not stop the server-side installer; check server logs before retrying
after a lost connection.
The **Follow output** control is at the right of the installer output heading.
During installation, the small red cross beside it stops the server-side
installer after a confirmation prompt. Interrupting installation may leave
packages partially installed; reinstall Qibocal before generating plots if needed.

---

## 🖨️ Publication-Grade Print to PDF

To generate an archival PDF report or print a hard copy:

1. Click the **Print to PDF** button in the top action bar (or press `Cmd+P` / `Ctrl+P`).
2. The print media stylesheet automatically:
   - Hides the sidebar, search inputs, and interactive UI buttons.
   - Preserves full report header details and hardware metadata.
   - Formats Plotly charts and protocol tables for crisp printing.
   - Manages smart page breaks between protocol cards to prevent awkward mid-table cuts.
