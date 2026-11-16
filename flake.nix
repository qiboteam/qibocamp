{
  description = "Qibocamp: the desktop application for Qibocal calibrations";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      inherit (nixpkgs) lib;
      systems = [
        "x86_64-linux"
        "aarch64-darwin"
      ];
      forAll = f: lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
      # One place to bump: the Python package's version.
      version = (lib.importTOML ./pyproject.toml).project.version;
      # A clean checkout only: a dirty tree never claims an unrelated commit.
      commit = self.rev or null;
      build = pkgs: import ./nix { inherit pkgs version commit; };
    in
    {
      packages = forAll (pkgs: lib.filterAttrs (name: _: name != "checks") (build pkgs));

      overlays.default =
        final: _: lib.filterAttrs (name: _: name != "checks" && name != "default") (build final);

      apps = forAll (
        pkgs:
        let
          packages = self.packages.${pkgs.stdenv.hostPlatform.system};
        in
        {
          default = {
            type = "app";
            program = lib.getExe packages.qibocamp-desktop;
            meta.description = "The Qibocamp desktop application";
          };
          server = {
            type = "app";
            program = lib.getExe packages.qibocamp;
            meta.description = "The qibocamp command line and report server";
          };
        }
      );

      checks = forAll (
        pkgs:
        let
          built = build pkgs;
        in
        lib.filterAttrs (name: _: name != "checks" && name != "default") built // built.checks
      );

      formatter = forAll (pkgs: pkgs.nixfmt);
    };
}
