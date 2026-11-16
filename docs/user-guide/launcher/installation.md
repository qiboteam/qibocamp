# Desktop Launcher: Installation

Installers for the three desktop platforms are attached to every [release](https://github.com/qiboteam/qibocamp/releases):

| Platform | File | Notes |
| --- | --- | --- |
| macOS | `.dmg` | Apple silicon (M1 and later); Intel Macs are not supported |
| Linux | `.deb`, `.rpm`, `.AppImage` | One artifact each; the AppImage needs no installation |
| Windows | `-setup.exe`, `.msi` | The installer sets the application up for the current user |

The launcher drives the `qibocamp` command line, it does not replace it — and it installs it itself, in an environment of its own, the first time something needs it (see [Installing Qibocamp](user-guide/launcher/qibocamp)). A Python 3.10 or newer on the machine is all it needs.

## Opening an unsigned build

Signing an application for macOS requires a paid Apple Developer ID, and one for Windows a certificate from a commercial authority. Until the project holds them, the published installers are unsigned, and both systems refuse them on first launch — the application is intact, the operating system simply has nobody to check it with.

On **macOS**, the first attempt is met with *"Qibocamp" Not Opened — Apple could not verify "Qibocamp" is free of malware*. Only **Done** and **Move to Bin** are offered, and the application is *not* the one to insist with:

1. press **Done**;
2. open **System Settings → Privacy & Security** and scroll to the security section, where *"Qibocamp" was blocked to protect your Mac* now appears;
3. press **Open Anyway**, and confirm.

The same can be done in one command, which drops the quarantine flag the browser attached to the download:

```bash
xattr -dr com.apple.quarantine /Applications/Qibocamp.app
```

Either way the answer is remembered: subsequent launches open directly.

On **Windows**, SmartScreen shows *Windows protected your PC*: choose **More info → Run anyway**. On **Linux** nothing is in the way — the `.deb` and the AppImage install and run as they are. The application's binary is `qibocamp-desktop`, so `qibocamp` in a terminal stays the command line.

Maintainers who do hold the credentials only have to set the repository secrets `APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD` and `APPLE_SIGNING_IDENTITY` — plus `APPLE_ID`, `APPLE_PASSWORD` and `APPLE_TEAM_ID` for notarization: the build workflow picks them up on its own, and the installers it publishes then open with a double click.

## With Nix

On Linux (x86_64) and macOS (Apple silicon), the application is also a [Nix](https://nixos.org) flake:

```bash
nix run github:qiboteam/qibocamp            # the latest main
nix run github:qiboteam/qibocamp/v0.1.0a2   # a release, pinned by its tag
nix profile install github:qiboteam/qibocamp
```

The last puts both `qibocamp-desktop`, the application, and `qibocamp`, the command line, on the `PATH`. On macOS, the application bundle is in the package's `Applications/Qibocamp.app`; Nix runs it from the store, so no quarantine stands in the way. `nix run github:qiboteam/qibocamp#server` runs the command line alone, and the flake's packages are `qibocamp-desktop`, `qibocamp` and `qibocamp-dashboard` — the dashboard as a static site — also available as an overlay.

The Nix application **ships with its command line**: it runs that one, installs none, and **Configuration** says so. It is updated with the application, by Nix — `nix profile upgrade`, or a newer tag — and a server started on another machine installs, there, the very commit the package was built from, as the installers' does. **Another environment**, in the developer options, still wins over it, to work on a checkout.

The project's binary cache, `qiboteam.cachix.org`, holds what the flake builds from `main` and from releases, so that nothing is compiled. The flake offers it on first use — accept it, or set `accept-flake-config = true` — though Nix only listens to a trusted user; otherwise `cachix use qiboteam` adds it once and for all, or, on NixOS,

```nix
nix.settings = {
  extra-substituters = [ "https://qiboteam.cachix.org" ];
  extra-trusted-public-keys = [ "qiboteam.cachix.org-1:uBKM05W3C7MNXwT1GxMa1c/juaBCSZe34iMokEu66gA=" ];
};
```

Without it, Nix builds the application, which takes a while the first time.

**Qibocal**, which report servers plot with, is not part of the package: the store is read-only. A server from the flake keeps it in an environment of its own, `~/.cache/qibocamp/envs/qibocal-py3.X`, made from the package's Python on first need and seeing its very `qibocamp`; the **Qibocal** tab of the diagnostics footer installs and switches it there, as on any other machine. `QIBOCAMP_QIBOCAL_ENVIRONMENT` names another environment, by its path.

A few things depend on the system rather than on the package:

- **The tray icon**, on GNOME, needs the AppIndicator extension, as with the `.deb`; KDE shows it as it is.
- **Notebook kernels** are created with `uv`, or `venv` and `pip`, from PyPI's wheels, as everywhere else. On NixOS, wheels with compiled code — numpy, and most of Qibocal's stack — need [`programs.nix-ld.enable = true`](https://wiki.nixos.org/wiki/Nix-ld) to load.
- **A blank or flickering window**, with NVIDIA drivers or in a virtual machine, is WebKitGTK's: start the application with `WEBKIT_DISABLE_DMABUF_RENDERER=1`.
