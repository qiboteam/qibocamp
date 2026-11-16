"""What a packager knows of this build, when pip's records do not say.

The Nix flake builds without pip, so no `direct_url.json` names the commit:
its derivation writes the commit here instead, and only when the source is a
clean checkout, so that an unrelated commit is never claimed.
"""

COMMIT: str | None = None
"""The commit this installation was built from, when its packager says so."""
