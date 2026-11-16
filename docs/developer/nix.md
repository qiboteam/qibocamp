# The Nix Package

Qibocamp is a Nix flake, for `x86_64-linux` and `aarch64-darwin`, built and tested by `.github/workflows/nix.yml`. It is a way to distribute the application, not to develop it: the development environment stays `devenv` (see the [Development Workflow](workflow.md)). Users' side: [Installation](../user-guide/launcher/installation.md#with-nix).

## Layout

| File | What it is |
| --- | --- |
| `flake.nix` | Inputs, systems, and the outputs: `packages`, `apps` (`default`, `server`), `overlays.default`, `checks`, `formatter` |
| `nix/default.nix` | The three packages and the extra checks, for one nixpkgs |
| `nix/source.nix` | Each package's sources, as filesets: a change to the docs never rebuilds the Rust application, and the pnpm dependencies are fetched from the manifests alone |
| `nix/qibocamp.nix` | The command line and report server, with pytest |
| `nix/dashboard.nix` | The dashboard as a static site, with its node tests; `.override { basePath = "/qibocamp/"; }` for a site under a prefix |
| `nix/desktop.nix` | The Tauri application, shipping `qibocamp` |
| `nix/update-hashes.sh` | Refreshes the pnpm dependency hashes |

The version is `pyproject.toml`'s: there is still one place to bump. The Python dependencies are nixpkgs', not `uv.lock`'s, on `nixos-unstable`; a version below a bound `pyproject.toml` declares is a reason to move the bound, or nixpkgs, not to relax it in the package.

## What the packages know of themselves

A Nix build has no `.git` and no pip records, so the two ways the commit is usually found fail there. The flake passes `self.rev` — the commit of a clean checkout, nothing for a dirty tree — to both packages:

- `qibocamp` writes it into `qibocamp/_build.py`, which `installed_commit()` reads when pip recorded nothing: `/api/version` names it, and a server started on another machine installs it rather than `main`;
- `qibocamp-desktop` gets it as `QIBOCAMP_BUILD_COMMIT`, as the installers do. Its tag is left empty: a flake does not know it, and the commit identifies the build, so the application's **About** reads *commit …*.

The application is built with `QIBOCAMP_BUNDLED_EXECUTABLE` set to the package's `qibocamp`: it runs that one (source `bundled`), after a preference and before its own installation, and refuses to install or update any. The installers set nothing, and behave as before.

## Qibocal, outside the store

The store's Python is read-only, and imports `qibocamp` only because the wrapper of `bin/qibocamp` adds its path: `sys.executable`, which the server installs Qibocal into and starts the plot worker with, can do neither. So the wrapper sets `QIBOCAMP_QIBOCAL_ENVIRONMENT=separate` (a default; a user's own value wins): the server then keeps Qibocal in a virtual environment of its own, `$XDG_CACHE_HOME/qibocamp/envs/qibocal-py3.X`, made from the store's Python, with a path file adding the package's `site-packages` after its own (`qibocal_environment.prepare_environment`). Installing, switching and reading Qibocal's version all happen there, and plots are generated with that environment's Python. A Python update of the package upgrades the environment in place (`venv --upgrade`), keeping Qibocal; a new minor version starts a new one. Elsewhere the variable is unset, and Qibocal lives in the server's own interpreter, as always.

## Hashes

The Rust crates need none: `cargoLock.lockFile` fetches each by the checksum in `Cargo.lock`. The pnpm dependencies of `launcher/` and `frontend/` are two fixed-output derivations, whose hashes, in `nix/desktop.nix` and `nix/dashboard.nix`, must follow `pnpm-lock.yaml`. After changing a lockfile:

```bash
nix/update-hashes.sh
```

fetches both sets and writes the new hashes in place. They are the same on every system. Without Nix, CI says what to do: a mismatch fails `nix.yml` with the file to change and the hash to put there.

## CI

`nix.yml` runs on the changes the packages depend on, on `main`, and on releases:

1. `nix flake check`: the three packages — pytest in `qibocamp`, the node tests in the dashboard — the launcher's node tests, and `nixfmt` on the Nix files;
2. the command line started as a report server, whose `/api/version` must name the commit built;
3. on Linux, the application opened under xvfb, which must still be running 20 seconds later; on macOS, the bundle and both executables checked.

The GitHub Actions cache keeps the store between runs, for speed; only `main` saves it. `tests.yml`, `launcher.yml` and `deploy.yml` are unchanged: the installers and the site do not come from Nix.

`flake-update.yml` opens a pull request updating `flake.lock` every Monday, and starts `nix.yml` on it: merge it when it is green.

## The binary cache

Users get the builds from `qiboteam.cachix.org`, filled by `nix.yml` from `main` and releases. It holds Qibocamp's own outputs and its fetched dependencies, not what `cache.nixos.org` already has — WebKitGTK, Python and the rest: a few hundred megabytes, well within the free plan's 5 GB, with the least recently used paths dropped first.

It belongs to the `qiboteam` Cachix organization. `nix.yml` writes to it with the repository secret `CACHIX_AUTH_TOKEN`; its public key, `qiboteam.cachix.org-1:uBKM05W3C7MNXwT1GxMa1c/juaBCSZe34iMokEu66gA=`, is in `flake.nix`'s `nixConfig` and in the NixOS snippet of the installation page. Without the secret — on pull requests from forks — the cache step is skipped and everything else builds. Pull requests and dispatched runs read the cache and never push.

Should the cache outgrow 5 GB, or Cachix go away, the same protocol works from any bucket: a Cloudflare R2 bucket, a signing key of ours (`nix key generate-secret`) as a secret, `nix store sign` and `nix copy --to "s3://…"` in place of the Cachix step, the bucket on a domain of ours, and the URL and key changed in `flake.nix` and the docs. The cleanup Cachix does would then be ours to write.
