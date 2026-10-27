//! Install the `qibocamp` command line, locate it, and run its short-lived
//! subcommands.
//!
//! The launcher keeps an environment of its own, under the cache directory
//! the notebook sessions use for theirs, and installs `qibocamp` into
//! it on request — a Python 3.10 or newer on the machine is all it needs.
//! Developers who run a checkout can point it at their own environment
//! instead: that choice is stored in `launcher.json` and wins over the
//! managed one. Desktop applications started from a launcher do not inherit
//! the shell `PATH`, so a plain lookup comes last, after the usual virtual
//! environment layouts.

use std::collections::HashSet;
use std::fs;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::Command;

use serde::Serialize;
use serde_json::{json, Value};
use tauri::{AppHandle, Manager};

use crate::process::{ProcessInfo, Supervisor};
use crate::settings;

/// The commit this build was made from, when the build knew it (see build.rs).
pub const BUILD_COMMIT: Option<&str> = option_env!("QIBOCAMP_BUILD_COMMIT");
/// The release tag this build was made for, when it was one (see build.rs).
pub const BUILD_TAG: Option<&str> = option_env!("QIBOCAMP_BUILD_TAG");

/// What goes into the managed environment, unless a preference says otherwise:
/// the very commit this launcher is built from, installed from its archive —
/// no git needed, and a command line that takes exactly the flags the
/// launcher passes. Without a known commit, the main branch. A bare URL, so
/// it survives being split into words by the remote worker.
pub fn default_package() -> String {
    match BUILD_COMMIT {
        Some(commit) => format!("https://github.com/qiboteam/qibocamp/archive/{commit}.tar.gz"),
        None => "https://github.com/qiboteam/qibocamp/archive/refs/heads/main.tar.gz".to_string(),
    }
}

/// Written by `setup.py` next to the environment: which requirement it holds.
const MARKER: &str = "qibocamp.json";

/// The driver run to create or update the managed environment, and the
/// worker it borrows from: both written to the cache before every run, so
/// the files on disk are always the ones this build was made with.
const SETUP_SCRIPT: &str = include_str!("setup.py");
const WORKER_SOURCE: &str = include_str!("../../../src/qibocamp/remote/notebook_worker.py");

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct Environment {
    /// Absolute path of the resolved interpreter, when one was found.
    pub executable: Option<String>,
    /// How it was found: `preference`, `managed`, `environment`, `path` or `well-known`.
    pub source: Option<String>,
    pub version: Option<String>,
    pub config_directory: String,
    /// Locations that were probed, to make a missing installation diagnosable.
    pub searched: Vec<String>,
    /// The environment the launcher installs and updates itself.
    pub managed: String,
    pub managed_ready: bool,
    /// A Python the managed environment can be created with, if any.
    pub python: Option<String>,
    pub python_version: Option<String>,
    /// The requirement the managed environment is given on the next install.
    pub package: String,
    /// What that requirement is when nothing was chosen: this build's commit.
    pub default_package: String,
    pub build_commit: Option<String>,
    /// The release this build is, when it is one: its tag.
    pub build_tag: Option<String>,
    /// The requirement it was last installed from, if it exists.
    pub managed_package: Option<String>,
}

fn managed_package() -> Option<String> {
    let text = fs::read_to_string(managed_environment().join(MARKER)).ok()?;
    let value: Value = serde_json::from_str(&text).ok()?;
    value.get("package")?.as_str().map(str::to_string)
}

fn cache_home() -> PathBuf {
    std::env::var_os("XDG_CACHE_HOME")
        .map(PathBuf::from)
        .filter(|path| !path.as_os_str().is_empty())
        .or_else(|| dirs::home_dir().map(|home| home.join(".cache")))
        .unwrap_or_else(std::env::temp_dir)
}

/// Where the launcher keeps the environment it installs itself: the folder
/// the notebook sessions use for theirs, under the launcher's own name.
pub fn managed_environment() -> PathBuf {
    cache_home().join("qibocamp").join("envs").join("launcher")
}

fn launcher_cache() -> PathBuf {
    cache_home().join("qibocamp").join("launcher")
}

fn package_preference() -> String {
    settings::launcher_preferences()
        .get("package")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .unwrap_or_else(default_package)
}

fn python_candidates() -> Vec<PathBuf> {
    let names: &[&str] = if cfg!(windows) {
        &["python.exe", "python3.exe"]
    } else {
        &["python3.13", "python3.12", "python3.11", "python3.10", "python3", "python"]
    };
    let mut directories: Vec<PathBuf> =
        std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default()).collect();
    for absolute in ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"] {
        directories.push(PathBuf::from(absolute));
    }
    if let Some(home) = dirs::home_dir() {
        directories.push(home.join(".pyenv/shims"));
        directories.push(home.join(".local/bin"));
    }
    // Installers from python.org, on macOS.
    if let Ok(entries) = fs::read_dir("/Library/Frameworks/Python.framework/Versions") {
        for entry in entries.flatten() {
            directories.push(entry.path().join("bin"));
        }
    }
    directories
        .into_iter()
        // The system Python of macOS is a stub that offers to install the
        // developer tools, and too old in any case.
        .filter(|directory| !(cfg!(target_os = "macos") && directory == Path::new("/usr/bin")))
        .flat_map(|directory| names.iter().map(move |name| directory.join(name)))
        .filter(|candidate| candidate.is_file())
        .collect()
}

fn python_version(path: &Path) -> Option<(u32, u32)> {
    let mut command = Command::new(path);
    configure(&mut command);
    let output = command
        .args(["-c", "import sys; print(sys.version_info[0], sys.version_info[1])"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let mut parts = text.split_whitespace();
    Some((parts.next()?.parse().ok()?, parts.next()?.parse().ok()?))
}

/// A Python new enough for qibocamp, with its version.
pub fn find_python() -> Result<(PathBuf, String), String> {
    let mut seen = HashSet::new();
    for candidate in python_candidates() {
        let Ok(real) = fs::canonicalize(&candidate) else {
            continue;
        };
        if !seen.insert(real) {
            continue;
        }
        if let Some((major, minor)) = python_version(&candidate) {
            if (major, minor) >= (3, 10) {
                return Ok((candidate, format!("{major}.{minor}")));
            }
        }
    }
    Err("No Python 3.10 or newer was found. Install one from python.org or with the \
         system package manager, then try again."
        .to_string())
}

/// Create the managed environment, or update what is installed in it.
#[tauri::command]
pub async fn install_cli(app: AppHandle, upgrade: Option<bool>) -> Result<ProcessInfo, String> {
    off_main(move || install(app, upgrade)).await?
}

/// Looking for a Python asks each candidate its version: off the main thread.
fn install(app: AppHandle, upgrade: Option<bool>) -> Result<ProcessInfo, String> {
    let supervisor = app.state::<Supervisor>();
    let (python, _) = find_python()?;
    let cache = launcher_cache();
    fs::create_dir_all(&cache)
        .map_err(|error| format!("Could not create '{}': {error}", cache.display()))?;
    for (name, contents) in [("notebook_worker.py", WORKER_SOURCE), ("setup.py", SETUP_SCRIPT)] {
        let path = cache.join(name);
        fs::write(&path, contents)
            .map_err(|error| format!("Could not write '{}': {error}", path.display()))?;
    }
    // One pip requirement, spaces included ("name @ url" is one argument).
    let package = package_preference();
    let options = json!({
        "path": managed_environment().display().to_string(),
        "packages": [package],
        "upgrade": upgrade.unwrap_or(false),
        "timeout": 1800,
    });
    let arguments = vec![
        "-u".to_string(),
        cache.join("setup.py").display().to_string(),
        options.to_string(),
    ];
    let mut command = Command::new(&python);
    configure(&mut command);
    let display = std::iter::once(python.display().to_string())
        .chain(arguments.clone())
        .collect();
    supervisor.spawn_command(
        &app,
        "setup",
        if upgrade.unwrap_or(false) {
            "Updating qibocamp".to_string()
        } else {
            "Installing qibocamp".to_string()
        },
        format!("{package} in {}", managed_environment().display()),
        command,
        arguments,
        display,
        None,
        None,
    )
}

fn program_names() -> Vec<&'static str> {
    if cfg!(windows) {
        vec!["qibocamp.exe", "qibocamp.cmd", "qibocamp.bat"]
    } else {
        vec!["qibocamp"]
    }
}

/// A file that can be run as the command line: never this application itself.
///
/// The desktop binary is `qibocamp-desktop`, but should a `qibocamp` on the
/// `PATH` ever be the application — a renamed or hand-made link — running it
/// as the command line would start another instance, which would do the same,
/// endlessly: windows and tray icons without end.
fn is_executable(path: &Path) -> bool {
    path.is_file() && !is_this_application(path)
}

fn is_this_application(path: &Path) -> bool {
    let canonical = |path: &Path| fs::canonicalize(path).ok();
    match (std::env::current_exe().ok().as_deref().and_then(canonical), canonical(path)) {
        (Some(this), Some(other)) => this == other,
        _ => false,
    }
}

/// Accept either the interpreter itself or the virtual environment holding it.
pub fn normalize_candidate(path: &Path) -> Option<PathBuf> {
    if is_executable(path) {
        return Some(path.to_path_buf());
    }
    if path.is_dir() {
        for directory in [path.join("bin"), path.join("Scripts"), path.to_path_buf()] {
            for name in program_names() {
                let candidate = directory.join(name);
                if is_executable(&candidate) {
                    return Some(candidate);
                }
            }
        }
    }
    None
}

fn well_known_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(home) = dirs::home_dir() {
        for relative in [
            ".local/bin",
            ".local/pipx/venvs/qibocamp/bin",
            ".venv/bin",
            "venv/bin",
            "AppData/Roaming/Python/Scripts",
            ".local/pipx/venvs/qibocamp/Scripts",
        ] {
            candidates.push(home.join(relative));
        }
    }
    for absolute in ["/usr/local/bin", "/opt/homebrew/bin", "/usr/bin"] {
        candidates.push(PathBuf::from(absolute));
    }
    if let Ok(current) = std::env::current_dir() {
        candidates.push(current.join(".venv/bin"));
        candidates.push(current.join(".venv/Scripts"));
    }
    candidates
}

/// Resolve the interpreter, reporting every location that was inspected.
pub fn resolve() -> (Option<(PathBuf, String)>, Vec<String>) {
    let mut searched = Vec::new();

    let preference = settings::launcher_preferences()
        .get("executable")
        .and_then(Value::as_str)
        .map(PathBuf::from);
    if let Some(path) = preference {
        searched.push(path.display().to_string());
        if let Some(resolved) = normalize_candidate(&path) {
            return (Some((resolved, "preference".to_string())), searched);
        }
    }

    let managed = managed_environment();
    searched.push(managed.display().to_string());
    if let Some(resolved) = normalize_candidate(&managed) {
        return (Some((resolved, "managed".to_string())), searched);
    }

    if let Some(value) = std::env::var("QIBOCAMP_EXECUTABLE")
        .ok()
        .filter(|value| !value.is_empty())
    {
        let path = PathBuf::from(value);
        searched.push(path.display().to_string());
        if let Some(resolved) = normalize_candidate(&path) {
            return (Some((resolved, "environment".to_string())), searched);
        }
    }

    for directory in std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default()) {
        for name in program_names() {
            let candidate = directory.join(name);
            if is_executable(&candidate) {
                return (Some((candidate, "path".to_string())), searched);
            }
        }
    }

    for directory in well_known_candidates() {
        for name in program_names() {
            let candidate = directory.join(name);
            searched.push(candidate.display().to_string());
            if is_executable(&candidate) {
                return (Some((candidate, "well-known".to_string())), searched);
            }
        }
    }

    (None, searched)
}

pub fn executable() -> Result<PathBuf, String> {
    resolve()
        .0
        .map(|(path, _)| path)
        .ok_or_else(|| {
            "qibocamp is not installed yet. The launcher installs it when it is first \
             needed; the Configuration section can also point it at an environment that has it."
                .to_string()
        })
}

/// The environment every Python process is started with.
fn configure(command: &mut Command) {
    command.env("PYTHONUNBUFFERED", "1");
    command.env("PYTHONIOENCODING", "utf-8");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // CREATE_NO_WINDOW: never flash a console behind the interface.
        command.creation_flags(0x0800_0000);
    }
}

/// Build a command that can find its own siblings (python, uvicorn, pnpm).
pub fn command(program: &Path) -> Command {
    let mut command = Command::new(program);
    if let Some(directory) = program.parent() {
        let mut entries = vec![directory.to_path_buf()];
        entries.extend(std::env::split_paths(
            &std::env::var_os("PATH").unwrap_or_default(),
        ));
        if let Ok(path) = std::env::join_paths(entries) {
            command.env("PATH", path);
        }
    }
    configure(&mut command);
    command
}

/// Run a short-lived subcommand and return its standard output.
pub fn run(arguments: &[String]) -> Result<String, String> {
    let program = executable()?;
    let output = command(&program)
        .args(arguments)
        .output()
        .map_err(|error| format!("Could not run '{}': {error}", program.display()))?;
    if output.status.success() {
        return Ok(String::from_utf8_lossy(&output.stdout).into_owned());
    }
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let detail = if stderr.is_empty() { stdout } else { stderr };
    Err(if detail.is_empty() {
        format!("qibocamp {} failed.", arguments.join(" "))
    } else {
        detail
    })
}

/// Run blocking work away from the main thread. A synchronous command runs
/// on it, so a subprocess it waits for — Python asked its version, an SSH
/// round trip to a queue — freezes the whole window until it answers.
pub async fn off_main<T: Send + 'static>(
    work: impl FnOnce() -> T + Send + 'static,
) -> Result<T, String> {
    tauri::async_runtime::spawn_blocking(work)
        .await
        .map_err(|error| format!("The work could not finish: {error}"))
}

#[tauri::command]
pub async fn environment() -> Result<Environment, String> {
    off_main(describe_environment).await
}

/// What the launcher runs, and what it would install: asks Python, so slow.
pub fn describe_environment() -> Environment {
    let (resolved, searched) = resolve();
    let (executable, source) = match resolved {
        Some((path, source)) => (Some(path), Some(source)),
        None => (None, None),
    };
    let version = executable.as_ref().and_then(|path| {
        command(path)
            .arg("--version")
            .output()
            .ok()
            .filter(|output| output.status.success())
            .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_string())
            .filter(|version| !version.is_empty())
    });
    let managed = managed_environment();
    let python = find_python().ok();
    Environment {
        executable: executable.map(|path| path.display().to_string()),
        source,
        version,
        config_directory: settings::config_dir().display().to_string(),
        searched,
        managed_ready: normalize_candidate(&managed).is_some(),
        managed: managed.display().to_string(),
        python: python.as_ref().map(|(path, _)| path.display().to_string()),
        python_version: python.map(|(_, version)| version),
        package: package_preference(),
        default_package: default_package(),
        build_commit: BUILD_COMMIT.map(str::to_string),
        build_tag: BUILD_TAG.map(str::to_string),
        managed_package: managed_package(),
    }
}

#[tauri::command]
pub async fn set_executable(path: Option<String>) -> Result<Environment, String> {
    off_main(move || choose_executable(path)).await?
}

fn choose_executable(path: Option<String>) -> Result<Environment, String> {
    let mut preferences = settings::launcher_preferences();
    match path.map(PathBuf::from) {
        Some(requested) => {
            let resolved = normalize_candidate(&requested).ok_or_else(|| {
                format!(
                    "No 'qibocamp' command line found at '{}'.",
                    requested.display()
                )
            })?;
            preferences.insert(
                "executable".to_string(),
                Value::String(resolved.display().to_string()),
            );
        }
        None => {
            preferences.remove("executable");
        }
    }
    settings::store_launcher_preferences(preferences)?;
    Ok(describe_environment())
}

/// Pick a port the operating system is willing to hand out right now.
#[tauri::command]
pub fn free_port() -> Result<u16, String> {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|listener| listener.local_addr())
        .map(|address| address.port())
        .map_err(|error| format!("Could not reserve a local port: {error}"))
}

/// Where a server's users are: the machine it runs on, over SSH when it is another.
#[derive(serde::Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct ServerMachine {
    pub ssh: Option<String>,
    pub venv: Option<String>,
}

/// `--ssh DEST --venv ENV`, when the server runs on another machine.
fn machine_arguments(arguments: &mut Vec<String>, ssh: Option<String>, venv: Option<String>) {
    if let Some(ssh) = ssh.map(|value| value.trim().to_string()).filter(|value| !value.is_empty()) {
        arguments.push("--ssh".to_string());
        arguments.push(ssh);
        if let Some(venv) = venv.map(|value| value.trim().to_string()).filter(|value| !value.is_empty()) {
            arguments.push("--venv".to_string());
            arguments.push(venv);
        }
    }
}

#[tauri::command]
pub async fn administrators(machine: Option<ServerMachine>) -> Result<Value, String> {
    off_main(move || list_administrators(machine.unwrap_or_default())).await?
}

fn list_administrators(machine: ServerMachine) -> Result<Value, String> {
    let mut arguments = vec![
        "report".to_string(),
        "admin".to_string(),
        "list".to_string(),
        "--all".to_string(),
        "--json".to_string(),
    ];
    machine_arguments(&mut arguments, machine.ssh, machine.venv);
    let output = run(&arguments)?;
    serde_json::from_str(&output)
        .map_err(|error| format!("Could not read the administrator list: {error}"))
}

#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MachineRequest {
    pub ssh: Option<String>,
    #[serde(default)]
    pub partitions: Vec<String>,
    pub timeout: Option<f64>,
}

/// Ask a machine's queues once, through `qibocamp notebook queues --json`:
/// the jobs of the partitions given, and a line for every partition there.
#[tauri::command]
pub async fn slurm_machine(request: MachineRequest) -> Result<Value, String> {
    off_main(move || ask_machine(request)).await?
}

fn ask_machine(request: MachineRequest) -> Result<Value, String> {
    let mut arguments = vec!["notebook".to_string(), "queues".to_string()];
    if let Some(ssh) = request.ssh.filter(|value| !value.trim().is_empty()) {
        arguments.push("--ssh".to_string());
        arguments.push(ssh);
    }
    for partition in request.partitions {
        arguments.push("--queue".to_string());
        arguments.push(partition);
    }
    if let Some(timeout) = request.timeout {
        arguments.push("--timeout".to_string());
        arguments.push(format!("{timeout}"));
    }
    arguments.push("--json".to_string());
    let output = run(&arguments)?;
    serde_json::from_str(&output).map_err(|error| format!("Could not read the queues answer: {error}"))
}

#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InviteRequest {
    pub username: Option<String>,
    pub server_url: String,
    pub expires_in_hours: i64,
    /// The server's machine, when it is another: the invitation is made in its users.
    pub ssh: Option<String>,
    pub venv: Option<String>,
}

/// Write agent configurations into agents.json on another machine:
/// `qibocamp agents push`, which merges them there and leaves the rest.
#[tauri::command]
pub async fn push_agents(ssh: String, agents: Value, remove: Vec<String>) -> Result<Value, String> {
    off_main(move || {
        let mut arguments = vec![
            "agents".to_string(),
            "push".to_string(),
            ssh,
            "--agents".to_string(),
            agents.to_string(),
        ];
        for name in remove {
            arguments.push("--remove".to_string());
            arguments.push(name);
        }
        let output = run(&arguments)?;
        serde_json::from_str(output.trim()).map_err(|error| format!("Could not read the push answer: {error}"))
    })
    .await?
}

/// The environment the launcher installs `qibocal-agent` in, with
/// `qibocamp agents install`: in qibocal-agent's own cache folder.
pub fn agent_tools_directory() -> PathBuf {
    cache_home().join("qibocal").join("agents").join("tools")
}

/// Where `qibocal-agent` is: the environment or executable chosen on the
/// Agents page, the one the launcher installs, or the PATH.
pub fn agent_executable() -> Result<PathBuf, String> {
    let name = if cfg!(windows) { "qibocal-agent.exe" } else { "qibocal-agent" };
    let chosen = settings::launcher_preferences()
        .get("agentTools")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from);
    if let Some(path) = chosen {
        for candidate in [path.clone(), path.join("bin").join(name), path.join("Scripts").join(name), path.join(name)] {
            if candidate.is_file() {
                return Ok(candidate);
            }
        }
        return Err(format!("No qibocal-agent at '{}', chosen in Configuration.", path.display()));
    }
    let managed = agent_tools_directory()
        .join(if cfg!(windows) { "Scripts" } else { "bin" })
        .join(name);
    if managed.is_file() {
        return Ok(managed);
    }
    for directory in std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default()) {
        let candidate = directory.join(name);
        if candidate.is_file() {
            return Ok(candidate);
        }
    }
    Err("qibocal-agent is not installed: install it from Agents, or choose an environment holding it.".to_string())
}

#[derive(serde::Serialize)]
pub struct AgentTools {
    /// The qibocal-agent the launcher uses, by absolute path; none when there is none.
    pub executable: Option<String>,
    /// Whether it is the one the launcher installed.
    pub managed: bool,
    /// Where the launcher installs it.
    pub directory: String,
    /// What the launcher last installed there, as pip took it.
    pub installed: Option<String>,
    /// What `qibocal-agent --version` answers.
    pub version: Option<String>,
    pub error: Option<String>,
}

/// The qibocal-agent the Agents page registers and runs: where it is, what it is.
#[tauri::command]
pub async fn agent_tools() -> Result<AgentTools, String> {
    off_main(|| {
        let directory = agent_tools_directory();
        let installed = std::fs::read_to_string(directory.join("qibocal-tools.txt"))
            .ok()
            .map(|text| text.trim().to_string())
            .filter(|text| !text.is_empty());
        let (executable, error) = match agent_executable() {
            Ok(path) => (Some(path), None),
            Err(error) => (None, Some(error)),
        };
        let version = executable.as_ref().and_then(|path| {
            let mut command = Command::new(path);
            configure(&mut command);
            let output = command.arg("--version").output().ok()?;
            let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
            output.status.success().then(|| text.rsplit(' ').next().unwrap_or(&text).to_string())
        });
        Ok(AgentTools {
            managed: executable.as_ref().is_some_and(|path| path.starts_with(&directory)),
            executable: executable.map(|path| path.display().to_string()),
            directory: directory.display().to_string(),
            installed,
            version,
            error,
        })
    })
    .await?
}

#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentRun {
    /// The configuration; `default` when none is given.
    pub name: Option<String>,
    pub objective: Option<String>,
}

/// Dispatch an autonomous agent: `qibocal-agent run NAME [OBJECTIVE]`, through
/// the login shell, so the model's credentials set in the shell profile reach
/// it — an application opened from the Dock gets none of them otherwise.
#[tauri::command]
pub async fn start_agent(app: AppHandle, request: AgentRun) -> Result<ProcessInfo, String> {
    off_main(move || {
        let program = agent_executable()?;
        // Always named: a lone argument would otherwise be read as an objective for default.
        let name = request
            .name
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| "default".to_string());
        let mut words = vec![program.display().to_string(), "run".to_string(), name.clone()];
        if let Some(objective) = request.objective.map(|value| value.trim().to_string()).filter(|value| !value.is_empty()) {
            words.push(objective);
        }
        let (mut command, arguments) = if cfg!(windows) {
            (Command::new(&program), words[1..].to_vec())
        } else {
            let shell = std::env::var("SHELL").ok().filter(|value| !value.is_empty()).unwrap_or_else(|| "/bin/sh".to_string());
            let mut arguments = vec!["-lc".to_string(), "exec \"$0\" \"$@\"".to_string()];
            arguments.extend(words.clone());
            (Command::new(shell), arguments)
        };
        configure(&mut command);
        let label = format!("Agent {name}");
        let supervisor = app.state::<Supervisor>();
        supervisor.spawn_command(&app, "agent", label, "qibocal-agent".to_string(), command, arguments, words, None, None)
    })
    .await?
}

/// Re-issue an administrator invitation for a locally managed server.
#[tauri::command]
pub async fn reissue_invite(request: InviteRequest) -> Result<Value, String> {
    off_main(move || invite(request)).await?
}

fn invite(request: InviteRequest) -> Result<Value, String> {
    let mut arguments = vec![
        "report".to_string(),
        "admin".to_string(),
        "invite".to_string(),
    ];
    if let Some(username) = request
        .username
        .map(|name| name.trim().to_string())
        .filter(|name| !name.is_empty())
    {
        arguments.push(username);
    }
    arguments.push("--server-url".to_string());
    arguments.push(request.server_url);
    arguments.push("--expires-in-hours".to_string());
    arguments.push(request.expires_in_hours.to_string());
    arguments.push("--no-interactive".to_string());
    arguments.push("--json".to_string());
    machine_arguments(&mut arguments, request.ssh, request.venv);

    let output = run(&arguments)?;
    let start = output
        .find('{')
        .ok_or_else(|| format!("Unexpected invitation output: {}", output.trim()))?;
    serde_json::from_str(&output[start..])
        .map_err(|error| format!("Could not read the generated invitation: {error}"))
}

/// Describe the launcher host, used by the interface for platform hints.
#[tauri::command]
pub fn platform_summary() -> Value {
    json!({
        "os": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
        "home": dirs::home_dir().map(|path| path.display().to_string()),
    })
}
