# The sources of each package, and only those: a change to one package's
# files never rebuilds another, nor the documentation the Rust application.
{ lib }:
let
  root = ../.;
  fs = lib.fileset;
  # The pnpm project of a directory, as `fetchPnpmDeps` reads it: its manifest
  # and lockfiles alone, so that the dependencies are fetched again only when
  # those change.
  pnpmProject = directory: {
    root = directory;
    fileset = fs.unions [
      (directory + "/package.json")
      (directory + "/pnpm-lock.yaml")
      (directory + "/pnpm-workspace.yaml")
    ];
  };
  # A JavaScript project's sources, without what pnpm and Vite leave behind.
  javascript =
    directory:
    fs.difference directory (
      fs.unions [
        (fs.maybeMissing (directory + "/node_modules"))
        (fs.maybeMissing (directory + "/dist"))
      ]
    );
in
{
  # The command line and report server, with what its tests read.
  cli = fs.toSource {
    inherit root;
    fileset = fs.unions [
      ../pyproject.toml
      ../README.md
      ../LICENSE
      ../src
      ../docs
      ../data
      ../tests
      ../sample_data
      # tests/test_names.py checks the command line names servers as the dashboard does.
      ../frontend/src/utils/names.js
    ];
  };

  # The static dashboard: frontend/, and the documentation it embeds.
  dashboard = fs.toSource {
    inherit root;
    fileset = fs.unions [
      (javascript ../frontend)
      ../docs
    ];
  };
  dashboardPnpm = fs.toSource (pnpmProject ../frontend);

  # The desktop application: launcher/, the dashboard's sources it bundles,
  # the documentation both embed, and the notebook worker it ships.
  desktop = fs.toSource {
    inherit root;
    fileset = fs.unions [
      (fs.difference (javascript ../launcher) (
        fs.unions [
          (fs.maybeMissing ../launcher/src-tauri/target)
          (fs.maybeMissing ../launcher/src-tauri/gen)
          (fs.maybeMissing ../launcher/src-tauri/icons)
        ]
      ))
      ../frontend/src
      ../docs
      ../src/qibocamp/remote/notebook_worker.py
      ../pyproject.toml
    ];
  };
  desktopPnpm = fs.toSource (pnpmProject ../launcher);
}
