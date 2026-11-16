# Developer: Development Workflow

This guide details the local setup, tooling, testing routines, and packaging pipeline for contributing to Qibocamp.

---

## 🛠️ Reproducible Environment (`devenv`, `uv`, `pnpm`)

The project uses [devenv.sh](https://devenv.sh) to configure a hermetic, reproducible developer environment backed by Nix.

### 1. Activating the Environment

```bash
# Enter development shell
devenv shell
```

The shell provides:
- **Python 3.12** with `uv` package manager
- **Node.js 22** with `pnpm`
- **Pre-commit validation tools** via `prek`

---

## ⚡ Developer Mode with Live HMR

To develop both frontend and backend concurrently:

```bash
# The sample reports travel zipped: unpack them once
unzip -q sample_data/test-data.zip -d sample_data
# Start backend on :8000 and Vite dev server on :5173 with HMR
qibocamp report dev ./sample_data
```

- Any modifications in `frontend/src/` trigger instant Hot Module Replacement without reloading the page.
- Requests to `/api` from Vite are automatically proxied to the running backend on port 8000.
- Changes in Python files reload the FastAPI server automatically via Uvicorn reload.

---

## 🧪 Testing Suite

### Running Backend Unit & Integration Tests

```bash
# Run pytest directly
pytest tests/

# Or via devenv shell
devenv shell -- pytest tests/
```

Test coverage includes:
- Health check and server discovery endpoints
- Report scanning, metadata parsing, and facet generation
- On-the-fly zip packaging (full, platform, and protocol data)
- In-browser `meta.json` serving
- Dynamic plot regeneration and error handling
- Markdown documentation serving endpoints

---

## 🎨 Code Quality & Linting (`prek`)

Pre-commit hooks are configured via `prek.toml` and run automatically on commit:

```bash
# Run all pre-commit checks across the repository
devenv shell -- prek run --all-files
```

Checks enforce:
- Python code formatting and linting via **Ruff**
- Python 3.11+ syntax upgrades via **pyupgrade**
- JSON / TOML validation
- Trailing whitespace removal and EOF formatting

---

## 📦 Building & Packaging

The Python package is the server alone: headless, without the dashboard, which is deployed from `frontend/` to GitHub Pages by `.github/workflows/deploy.yml` and embedded in the desktop launcher — one build of the pages, in two places, rather than a third copy in every wheel.

```bash
# The wheel, with uv
devenv shell -- build-wheel
# or: uv build
```

The flake packages the application, the command line and the dashboard for Nix users; after changing a `pnpm-lock.yaml`, run `nix/update-hashes.sh` — or let CI say which hash to set. See [The Nix Package](nix.md).

---

## 🏷️ Versions & Releases

There is one version, the Python package's, in `pyproject.toml`, in [PEP 440](https://peps.python.org/pep-0440/) form: `0.1.0a1` for an alpha, `0.1.0b1`, `0.1.0rc1`, then `0.1.0`. The package, `qibocamp --version` and the API all read it from the installed metadata. The JavaScript packages, `frontend` and `launcher`, are private and declare none; the launcher's `tauri.conf.json` and `Cargo.toml` carry the application's `X.Y.Z`, which a release stamps into `tauri.conf.json` from the tag. The servers' `GET /api/version` answers it, along with the API version, which counts the API's breaking changes; see the [API reference](../reference/api.md).

A release is a GitHub release whose tag is that version prefixed with `v` — `v0.1.0a1`. Its launcher workflow refuses a tag that does not match `pyproject.toml`. The desktop application is `X.Y.Z` — `0.1.0` for `0.1.0a1` — which every installer format accepts, and the pre-release is recorded where each format allows it: the fourth field of the MSI version, alphas counted from 1, betas from 101, release candidates from 201 and a final release 1000 (`0.1.0.1` for `0.1.0a1`), and the RPM release tag, `0.a1` before a final's `1`. The installers are attached to the release. Bump `pyproject.toml` for the next version right after tagging.
