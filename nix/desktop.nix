# The desktop application: the Tauri launcher with the dashboard it bundles,
# shipping its command line, which it runs instead of installing its own.
{
  lib,
  stdenv,
  rustPlatform,
  cargo-tauri,
  nodejs,
  pnpm_11,
  fetchPnpmDeps,
  pnpmConfigHook,
  pkg-config,
  makeWrapper,
  wrapGAppsHook3,
  webkitgtk_4_1,
  glib-networking,
  libayatana-appindicator,
  librsvg,
  openssl,
  uv,
  src,
  pnpmSrc,
  version,
  commit ? null,
  qibocamp,
}:
rustPlatform.buildRustPackage (finalAttrs: {
  pname = "qibocamp-desktop";
  inherit version src;

  cargoRoot = "launcher/src-tauri";
  buildAndTestSubdir = finalAttrs.cargoRoot;
  # Every crate is fetched by its checksum in Cargo.lock: no hash to keep.
  cargoLock.lockFile = ../launcher/src-tauri/Cargo.lock;

  pnpmRoot = "launcher";
  pnpmDeps = fetchPnpmDeps {
    inherit (finalAttrs) pname version;
    src = pnpmSrc;
    pnpm = pnpm_11;
    fetcherVersion = 5;
    hash = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=";
  };

  nativeBuildInputs = [
    cargo-tauri.hook
    nodejs
    pnpm_11
    pnpmConfigHook
    pkg-config
    makeWrapper
  ]
  ++ lib.optionals stdenv.hostPlatform.isLinux [ wrapGAppsHook3 ];

  buildInputs = lib.optionals stdenv.hostPlatform.isLinux [
    webkitgtk_4_1
    glib-networking
    libayatana-appindicator
    librsvg
    openssl
  ];

  env = {
    # What build.rs would ask git, which the sandbox has not.
    QIBOCAMP_BUILD_COMMIT = lib.optionalString (commit != null) commit;
    # The command line the application runs: this one, never one it installs.
    QIBOCAMP_BUNDLED_EXECUTABLE = lib.getExe qibocamp;
  };

  # The application is X.Y.Z, as launcher.yml makes it: 0.1.0a2 is 0.1.0.
  postPatch = ''
    node -e '
      const fs = require("fs")
      const path = "launcher/src-tauri/tauri.conf.json"
      const config = JSON.parse(fs.readFileSync(path))
      config.version = process.argv[1]
      fs.writeFileSync(path, JSON.stringify(config, null, 2) + "\n")
    ' "${lib.head (builtins.match "([0-9]+\\.[0-9]+\\.[0-9]+).*" version)}"
  '';

  # The icons are generated, not committed. With nixpkgs' Tauri, rather than
  # the npm one `pnpm run icons` runs: its prebuilt binary does not run here.
  preBuild = ''
    (cd launcher && cargo tauri icon src-tauri/icon.svg)
  '';

  # As in launcher.yml, which does not run them either: a second compile of
  # the crate, in test mode, for two unit tests.
  doCheck = false;

  # One wrapper, around the application alone: the command line next to it
  # is qibocamp's own. uv speeds up the environments of the notebook kernels;
  # a suffix, so that the user's own comes first.
  dontWrapGApps = true;
  postInstall = lib.optionalString stdenv.hostPlatform.isDarwin ''
    makeWrapper "$out/Applications/Qibocamp.app/Contents/MacOS/qibocamp-desktop" \
      "$out/bin/qibocamp-desktop" \
      --suffix PATH : ${lib.makeBinPath [ uv ]}
  '';
  postFixup =
    lib.optionalString stdenv.hostPlatform.isLinux ''
      # The tray icon loads libayatana-appindicator when it starts.
      wrapGApp "$out/bin/qibocamp-desktop" \
        --prefix LD_LIBRARY_PATH : ${lib.makeLibraryPath [ libayatana-appindicator ]} \
        --suffix PATH : ${lib.makeBinPath [ uv ]}
    ''
    + ''
      # The command line on the PATH too, for a `nix profile install`.
      ln -s ${lib.getExe qibocamp} "$out/bin/qibocamp"
    '';

  passthru = { inherit qibocamp; };

  meta = {
    description = "The Qibocamp desktop application, for Qibocal calibrations";
    homepage = "https://qibo.science/qibocamp";
    license = lib.licenses.asl20;
    mainProgram = "qibocamp-desktop";
    platforms = [
      "x86_64-linux"
      "aarch64-darwin"
    ];
  };
})
