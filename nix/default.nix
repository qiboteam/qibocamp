# Qibocamp's packages, and the checks that go with them, for one nixpkgs.
{
  pkgs,
  version,
  commit ? null,
}:
let
  inherit (pkgs) lib;
  sources = import ./source.nix { inherit lib; };

  qibocamp = pkgs.callPackage ./qibocamp.nix {
    inherit version commit;
    src = sources.cli;
  };

  qibocamp-dashboard = pkgs.callPackage ./dashboard.nix {
    inherit version;
    src = sources.dashboard;
    pnpmSrc = sources.dashboardPnpm;
  };

  qibocamp-desktop = pkgs.callPackage ./desktop.nix {
    inherit version commit qibocamp;
    src = sources.desktop;
    pnpmSrc = sources.desktopPnpm;
  };
in
{
  inherit qibocamp qibocamp-dashboard qibocamp-desktop;
  default = qibocamp-desktop;

  # Beyond the packages, whose builds run pytest and the dashboard's tests.
  checks = {
    # The launcher's node --test suites, with the dependencies the desktop
    # package fetched: not run by its build, which skips its checks.
    launcher-tests = pkgs.stdenvNoCC.mkDerivation {
      pname = "qibocamp-launcher-tests";
      inherit version;
      src = sources.desktop;
      inherit (qibocamp-desktop) pnpmDeps pnpmRoot;
      nativeBuildInputs = [
        pkgs.nodejs
        pkgs.pnpm_11
        pkgs.pnpmConfigHook
      ];
      buildPhase = ''
        runHook preBuild
        pnpm --dir launcher run test
        runHook postBuild
      '';
      installPhase = "touch $out";
    };

    # The Nix files as `nix fmt` writes them.
    formatting =
      pkgs.runCommand "qibocamp-nix-formatting"
        {
          nativeBuildInputs = [ pkgs.nixfmt ];
          src = lib.fileset.toSource {
            root = ../.;
            fileset = lib.fileset.unions [
              ../flake.nix
              (lib.fileset.fileFilter (file: file.hasExt "nix") ./.)
            ];
          };
        }
        ''
          nixfmt --check "$src/flake.nix" "$src"/nix/*.nix
          touch "$out"
        '';
  };
}
