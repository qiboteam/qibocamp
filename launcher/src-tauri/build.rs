use std::process::Command;

/// The commit this launcher is built from, so that the `qibocamp` it
/// installs is the one whose command line it knows how to drive.
///
/// The build machine says so through `QIBOCAMP_BUILD_COMMIT`; a local build
/// asks git. Without either, the launcher falls back to the main branch.
fn build_commit() -> Option<String> {
    println!("cargo:rerun-if-env-changed=QIBOCAMP_BUILD_COMMIT");
    if let Some(commit) = std::env::var("QIBOCAMP_BUILD_COMMIT")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
    {
        return Some(commit);
    }
    let output = Command::new("git").args(["rev-parse", "HEAD"]).output().ok()?;
    if !output.status.success() {
        return None;
    }
    let commit = String::from_utf8_lossy(&output.stdout).trim().to_string();
    (!commit.is_empty()).then_some(commit)
}

/// The tag this launcher is released as, when it is: the build machine says
/// so through `QIBOCAMP_BUILD_TAG`; a local build asks git for a tag on HEAD.
fn build_tag() -> Option<String> {
    println!("cargo:rerun-if-env-changed=QIBOCAMP_BUILD_TAG");
    if let Some(tag) = std::env::var("QIBOCAMP_BUILD_TAG")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
    {
        return Some(tag);
    }
    let output = Command::new("git")
        .args(["describe", "--tags", "--exact-match", "HEAD"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let tag = String::from_utf8_lossy(&output.stdout).trim().to_string();
    (!tag.is_empty()).then_some(tag)
}

/// The command line a packager ships with this build, by its absolute path:
/// the Nix flake sets `QIBOCAMP_BUNDLED_EXECUTABLE`. Unset, the launcher
/// installs its own, as the installers do.
fn bundled_executable() -> Option<String> {
    println!("cargo:rerun-if-env-changed=QIBOCAMP_BUNDLED_EXECUTABLE");
    std::env::var("QIBOCAMP_BUNDLED_EXECUTABLE")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}

fn main() {
    if let Some(executable) = bundled_executable() {
        println!("cargo:rustc-env=QIBOCAMP_BUNDLED_EXECUTABLE={executable}");
    }
    if let Some(commit) = build_commit() {
        println!("cargo:rustc-env=QIBOCAMP_BUILD_COMMIT={commit}");
    }
    if let Some(tag) = build_tag() {
        println!("cargo:rustc-env=QIBOCAMP_BUILD_TAG={tag}");
    }
    tauri_build::build()
}
