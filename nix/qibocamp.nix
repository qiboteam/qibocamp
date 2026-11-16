# The qibocamp command line and report server.
{
  lib,
  python3Packages,
  uv,
  versionCheckHook,
  src,
  version,
  # The commit of a clean checkout, or null: then the package claims none.
  commit ? null,
}:
python3Packages.buildPythonApplication {
  pname = "qibocamp";
  inherit version src;
  pyproject = true;

  # Built without pip, so no direct_url.json names the commit: write it where
  # `qibocamp.version.installed_commit()` looks next, for /api/version and for
  # the qibocamp a server on another machine installs.
  postPatch = lib.optionalString (commit != null) ''
    substituteInPlace src/qibocamp/_build.py \
      --replace-fail 'COMMIT: str | None = None' 'COMMIT: str | None = "${commit}"'
  '';

  build-system = [ python3Packages.uv-build ];

  # As pyproject.toml declares them, from nixpkgs. A version below a declared
  # bound is a reason to move the bound or nixpkgs, not to relax it here.
  dependencies =
    with python3Packages;
    [
      fastapi
      uvicorn
      pydantic
      pyyaml
      click
      httpx
      rich
      pyjwt
      packaging
      strawberry-graphql
    ]
    ++ uvicorn.optional-dependencies.standard;

  # uv: the tests of the Qibocal installer ask for an installer on the PATH,
  # and nixpkgs' Python has no pip.
  nativeCheckInputs = [
    python3Packages.pytestCheckHook
    python3Packages.pytest-asyncio
    uv
  ];

  # The configuration and caches go under the home directory.
  preCheck = ''
    export HOME="$(mktemp -d)"
  '';

  nativeInstallCheckInputs = [ versionCheckHook ];
  versionCheckProgramArg = "--version";

  pythonImportsCheck = [ "qibocamp" ];

  meta = {
    description = "The command line and report server of Qibocamp";
    homepage = "https://github.com/qiboteam/qibocamp";
    license = lib.licenses.asl20;
    mainProgram = "qibocamp";
    platforms = [
      "x86_64-linux"
      "aarch64-darwin"
    ];
  };
}
