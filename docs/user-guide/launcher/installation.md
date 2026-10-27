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
