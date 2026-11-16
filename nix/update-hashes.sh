#!/usr/bin/env bash
# Refresh the Nix hashes of the pnpm dependencies, after a pnpm-lock.yaml
# changed: each set is fetched, and the hash Nix finds replaces the one in
# its file. The same on every system: the fetcher takes every platform's
# packages. The Rust crates need nothing: Cargo.lock holds their checksums.
set -euo pipefail
cd "$(dirname "$0")/.."

update() {
  local attribute=$1 file=$2 current output got
  current=$(sed -n 's/.*hash = "\(sha256-[A-Za-z0-9+/=]*\)".*/\1/p' "$file" | head -n 1)
  if output=$(nix build --no-link -L ".#$attribute.pnpmDeps" 2>&1); then
    echo "$attribute: up to date"
    return
  fi
  got=$(printf '%s\n' "$output" | sed -n 's/.*got: *\(sha256-[A-Za-z0-9+/=]*\).*/\1/p' | head -n 1)
  if [ -z "$got" ]; then
    printf '%s\n' "$output" >&2
    echo "$attribute: the dependencies could not be fetched; see above." >&2
    exit 1
  fi
  # Portable in place, GNU and BSD sed alike.
  sed "s#$current#$got#" "$file" >"$file.new" && mv "$file.new" "$file"
  echo "$attribute: $current -> $got"
}

update qibocamp-desktop nix/desktop.nix
update qibocamp-dashboard nix/dashboard.nix
