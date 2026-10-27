//! Supervise the long-running `qibocamp` processes started from the interface.
//!
//! Every managed process keeps a bounded log, streams its output to the
//! interface through events, and is terminated gracefully when the launcher
//! quits so notebook tunnels and report servers never outlive the window.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager, State};

use crate::cli;

/// Lines kept per process: enough to diagnose a failed start, bounded for memory.
const LOG_LIMIT: usize = 2000;
const POLL_INTERVAL: Duration = Duration::from_millis(400);
const STOP_GRACE: Duration = Duration::from_secs(6);

pub const LOG_EVENT: &str = "launcher://log";
pub const PROCESS_EVENT: &str = "launcher://process";
/// The launcher is closing and waits for these processes: the interface shows them.
pub const CLOSING_EVENT: &str = "launcher://closing";

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProcessInfo {
    pub id: String,
    /// `server` or `notebook`, used to pick the interface affordances.
    pub kind: String,
    pub label: String,
    pub detail: String,
    pub command: Vec<String>,
    pub url: Option<String>,
    /// `running`, `stopping`, `exited` or `failed`.
    pub status: String,
    pub exit_code: Option<i32>,
    pub started_at: u64,
    /// What the interface started it for — its options, the connection whose
    /// reports it serves — kept here so that every page and window knows it,
    /// a page loaded again included. Opaque to this side.
    pub launch: Option<serde_json::Value>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct LogEvent {
    id: String,
    line: String,
}

struct Entry {
    info: ProcessInfo,
    log: Vec<String>,
    child: Option<Child>,
}

#[derive(Default)]
pub struct Supervisor {
    entries: Mutex<HashMap<String, Entry>>,
    counter: AtomicU64,
    /// Set once the launcher was asked to close: the next request forces it.
    closing: AtomicBool,
}

/// What the interface is told when the launcher closes: whom it waits for.
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ClosingEvent {
    pub ids: Vec<String>,
}

fn now_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_millis() as u64)
        .unwrap_or(0)
}

/// Drop the ANSI colour sequences a styled console may have left in a line.
fn plain_text(line: &str) -> String {
    let mut text = String::with_capacity(line.len());
    let mut characters = line.chars().peekable();
    while let Some(character) = characters.next() {
        if character == '\u{1b}' && characters.peek() == Some(&'[') {
            characters.next();
            for following in characters.by_ref() {
                if following.is_ascii_alphabetic() {
                    break;
                }
            }
            continue;
        }
        text.push(character);
    }
    text
}

/// The address of a process, as the command line announces it: a URL on a
/// line of its own, on this machine — a tunnel's end, or a local server.
/// Addresses mentioned in passing — a notebook server naming its own host,
/// a worker logging what it reaches, pip's download of an archive, wrapped
/// onto a line of its own — are not it.
pub fn announced_url(line: &str) -> Option<String> {
    let text = plain_text(line);
    let candidate = text.trim();
    if candidate.contains(char::is_whitespace) {
        return None;
    }
    extract_url(candidate)
        .filter(|url| url.len() == candidate.trim_end_matches(['.', ',', ';', ':']).len())
        .filter(|url| on_this_machine(url))
}

/// Whether a URL's host is this machine: `localhost`, the loopback addresses.
fn on_this_machine(url: &str) -> bool {
    let authority = url
        .split_once("://")
        .map(|(_, rest)| rest)
        .unwrap_or(url)
        .split(['/', '?', '#'])
        .next()
        .unwrap_or("");
    let host = authority
        .rsplit_once('@')
        .map(|(_, host)| host)
        .unwrap_or(authority);
    let host = match host.strip_prefix('[') {
        Some(bracketed) => bracketed.split(']').next().unwrap_or(""),
        None => host.split(':').next().unwrap_or(""),
    }
    .to_ascii_lowercase();
    host == "localhost" || host == "::1" || host.starts_with("127.")
}

/// Pick the first absolute URL in a line, as printed by the notebook launcher.
pub fn extract_url(line: &str) -> Option<String> {
    let start = line.find("http://").or_else(|| line.find("https://"))?;
    let rest = &line[start..];
    let end = rest
        .find(|character: char| character.is_whitespace() || "\"'<>()[]".contains(character))
        .unwrap_or(rest.len());
    let url = rest[..end].trim_end_matches(['.', ',', ';', ':']);
    if url.len() > "http://".len() {
        Some(url.to_string())
    } else {
        None
    }
}

impl Supervisor {
    fn next_id(&self) -> String {
        format!("process-{}", self.counter.fetch_add(1, Ordering::SeqCst) + 1)
    }

    fn snapshot(&self, id: &str) -> Option<ProcessInfo> {
        let entries = self.entries.lock().ok()?;
        entries.get(id).map(|entry| entry.info.clone())
    }

    pub fn list(&self) -> Vec<ProcessInfo> {
        let Ok(entries) = self.entries.lock() else {
            return Vec::new();
        };
        let mut processes: Vec<ProcessInfo> =
            entries.values().map(|entry| entry.info.clone()).collect();
        processes.sort_by_key(|process| process.started_at);
        processes
    }

    /// Record what a process was started for, and tell every window.
    pub fn set_launch(&self, app: &AppHandle, id: &str, launch: serde_json::Value) -> bool {
        let info = {
            let Ok(mut entries) = self.entries.lock() else {
                return false;
            };
            let Some(entry) = entries.get_mut(id) else {
                return false;
            };
            entry.info.launch = Some(launch);
            entry.info.clone()
        };
        let _ = app.emit(PROCESS_EVENT, info);
        true
    }

    pub fn log(&self, id: &str) -> Vec<String> {
        let Ok(entries) = self.entries.lock() else {
            return Vec::new();
        };
        entries
            .get(id)
            .map(|entry| entry.log.clone())
            .unwrap_or_default()
    }

    fn record(&self, app: &AppHandle, id: &str, line: String, detect_url: bool) {
        let mut changed = None;
        if let Ok(mut entries) = self.entries.lock() {
            let Some(entry) = entries.get_mut(id) else {
                return;
            };
            if entry.log.len() >= LOG_LIMIT {
                entry.log.remove(0);
            }
            entry.log.push(line.clone());
            if detect_url && entry.info.url.is_none() {
                if let Some(url) = announced_url(&line) {
                    entry.info.url = Some(url);
                    changed = Some(entry.info.clone());
                }
            }
        }
        let _ = app.emit(
            LOG_EVENT,
            LogEvent {
                id: id.to_string(),
                line,
            },
        );
        if let Some(info) = changed {
            let _ = app.emit(PROCESS_EVENT, info);
        }
    }

    fn finish(&self, app: &AppHandle, id: &str, code: Option<i32>) {
        let mut changed = None;
        if let Ok(mut entries) = self.entries.lock() {
            if let Some(entry) = entries.get_mut(id) {
                entry.child = None;
                entry.info.exit_code = code;
                entry.info.status = match code {
                    Some(0) | None => "exited".to_string(),
                    Some(_) => "failed".to_string(),
                };
                changed = Some(entry.info.clone());
            }
        }
        if let Some(info) = changed {
            let _ = app.emit(PROCESS_EVENT, info);
        }
        // The last process the closing launcher waited for: it may go now.
        if self.closing.load(Ordering::SeqCst) && self.running().is_empty() {
            app.exit(0);
        }
    }

    /// The processes still alive, by id.
    pub fn running(&self) -> Vec<String> {
        let Ok(entries) = self.entries.lock() else {
            return Vec::new();
        };
        entries
            .iter()
            .filter(|(_, entry)| entry.child.is_some())
            .map(|(id, _)| id.clone())
            .collect()
    }

    /// Start a `qibocamp` subcommand under supervision.
    pub fn spawn(
        &self,
        app: &AppHandle,
        kind: &str,
        label: String,
        detail: String,
        arguments: Vec<String>,
        working_directory: Option<PathBuf>,
        url: Option<String>,
    ) -> Result<ProcessInfo, String> {
        let program = cli::executable()?;
        let command = cli::command(&program);
        let display = std::iter::once(program.display().to_string())
            .chain(arguments.clone())
            .collect();
        self.spawn_command(
            app,
            kind,
            label,
            detail,
            command,
            arguments,
            display,
            working_directory,
            url,
        )
    }

    /// Start any program under supervision; `display` is the command as shown.
    #[allow(clippy::too_many_arguments)]
    pub fn spawn_command(
        &self,
        app: &AppHandle,
        kind: &str,
        label: String,
        detail: String,
        mut command: Command,
        arguments: Vec<String>,
        display: Vec<String>,
        working_directory: Option<PathBuf>,
        url: Option<String>,
    ) -> Result<ProcessInfo, String> {
        command
            .args(&arguments)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        if let Some(directory) = working_directory {
            command.current_dir(directory);
        }
        let program = display.first().cloned().unwrap_or_default();
        let mut child = command
            .spawn()
            .map_err(|error| format!("Could not start '{program}': {error}"))?;

        let stdout = child.stdout.take();
        let stderr = child.stderr.take();
        let id = self.next_id();
        let detect_url = url.is_none();
        let info = ProcessInfo {
            id: id.clone(),
            kind: kind.to_string(),
            label,
            detail,
            command: display,
            url,
            status: "running".to_string(),
            exit_code: None,
            started_at: now_millis(),
            launch: None,
        };

        if let Ok(mut entries) = self.entries.lock() {
            entries.insert(
                id.clone(),
                Entry {
                    info: info.clone(),
                    log: Vec::new(),
                    child: Some(child),
                },
            );
        } else {
            return Err("The process supervisor is unavailable.".to_string());
        }

        // The command line announces the final address on standard output;
        // standard error carries the workers' own output, where a notebook
        // server lists its remote addresses on bare lines of their own.
        if let Some(stream) = stdout {
            pump(app.clone(), id.clone(), stream, detect_url);
        }
        if let Some(stream) = stderr {
            pump(app.clone(), id.clone(), stream, false);
        }
        monitor(app.clone(), id);

        let _ = app.emit(PROCESS_EVENT, info.clone());
        Ok(info)
    }

    /// Ask the process to stop, leaving it time to clean up tunnels and workers.
    ///
    /// Nothing waits here: the process is told, the interface hears that it
    /// is stopping, and a thread of its own kills it should the grace period
    /// run out. The monitor reports the end, as for any other exit.
    pub fn stop(&self, app: &AppHandle, id: &str) -> Result<(), String> {
        let (pid, info) = {
            let mut entries = self
                .entries
                .lock()
                .map_err(|_| "The process supervisor is unavailable.".to_string())?;
            let entry = entries
                .get_mut(id)
                .ok_or_else(|| format!("Process '{id}' is not managed by the launcher."))?;
            match entry.child.as_ref() {
                Some(child) if entry.info.status == "stopping" => (child.id(), None),
                Some(child) => {
                    entry.info.status = "stopping".to_string();
                    (child.id(), Some(entry.info.clone()))
                }
                None => return Ok(()),
            }
        };
        let Some(info) = info else {
            // Asked twice: the first request is still being honoured.
            return Ok(());
        };
        let _ = app.emit(PROCESS_EVENT, info);

        terminate(pid);

        let app = app.clone();
        let id = id.to_string();
        std::thread::spawn(move || {
            let deadline = SystemTime::now() + STOP_GRACE;
            while SystemTime::now() < deadline {
                std::thread::sleep(Duration::from_millis(100));
                let supervisor = app.state::<Supervisor>();
                let Ok(entries) = supervisor.entries.lock() else {
                    return;
                };
                if entries.get(&id).map_or(true, |entry| entry.child.is_none()) {
                    return;
                }
            }
            app.state::<Supervisor>().kill(&id);
        });
        Ok(())
    }

    /// End a process at once, without the grace period.
    fn kill(&self, id: &str) {
        if let Ok(mut entries) = self.entries.lock() {
            if let Some(child) = entries.get_mut(id).and_then(|entry| entry.child.as_mut()) {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }

    /// Stop everything gracefully; nothing waits.
    pub fn stop_all(&self, app: &AppHandle) {
        for id in self.running() {
            let _ = self.stop(app, &id);
        }
    }

    /// Kill everything at once.
    pub fn kill_all(&self) {
        for id in self.running() {
            self.kill(&id);
        }
    }

    /// Whether the launcher may close now.
    ///
    /// The first request stops everything gracefully and tells the interface
    /// whom it waits for, answering no: the launcher goes when the last
    /// process has gone. A second request — the window closed again, the
    /// application quit again — is read as "force close": everything is
    /// killed, and the launcher goes now.
    pub fn request_close(&self, app: &AppHandle) -> bool {
        let running = self.running();
        if running.is_empty() {
            return true;
        }
        if self.closing.swap(true, Ordering::SeqCst) {
            self.kill_all();
            return true;
        }
        let _ = app.emit(CLOSING_EVENT, ClosingEvent { ids: running });
        self.stop_all(app);
        false
    }

    pub fn forget(&self, id: &str) -> Result<(), String> {
        let mut entries = self
            .entries
            .lock()
            .map_err(|_| "The process supervisor is unavailable.".to_string())?;
        match entries.get(id) {
            Some(entry) if entry.child.is_some() => {
                Err("Stop the process before removing it from the list.".to_string())
            }
            _ => {
                entries.remove(id);
                Ok(())
            }
        }
    }

    /// Nothing outlives the window: what is still there when the launcher
    /// exits — after the graceful stop, or a forced close — is killed.
    pub fn shutdown(&self) {
        self.kill_all();
    }
}

#[cfg(unix)]
fn terminate(pid: u32) {
    // SIGTERM lets the CLI close SSH tunnels and remote notebook workers.
    unsafe {
        libc::kill(pid as i32, libc::SIGTERM);
    }
}

#[cfg(windows)]
fn terminate(pid: u32) {
    let _ = std::process::Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

fn pump<R: Read + Send + 'static>(app: AppHandle, id: String, stream: R, detect_url: bool) {
    std::thread::spawn(move || {
        let reader = BufReader::new(stream);
        for line in reader.lines() {
            let Ok(line) = line else { break };
            let supervisor = app.state::<Supervisor>();
            supervisor.record(&app, &id, line, detect_url);
        }
    });
}

fn monitor(app: AppHandle, id: String) {
    std::thread::spawn(move || loop {
        std::thread::sleep(POLL_INTERVAL);
        let supervisor = app.state::<Supervisor>();
        let status = {
            let Ok(mut entries) = supervisor.entries.lock() else {
                return;
            };
            match entries.get_mut(&id) {
                Some(entry) => match entry.child.as_mut() {
                    Some(child) => child.try_wait(),
                    None => return,
                },
                None => return,
            }
        };
        match status {
            Ok(Some(exit)) => {
                supervisor.finish(&app, &id, exit.code());
                return;
            }
            Ok(None) => {}
            Err(_) => {
                supervisor.finish(&app, &id, None);
                return;
            }
        }
    });
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ServerOptions {
    /// `server`, `dev` or `client`.
    pub mode: String,
    /// None serves the shared reports directory of the machine it runs on.
    pub directory: Option<String>,
    pub host: String,
    pub port: u16,
    pub auth: bool,
    pub frontend_port: Option<u16>,
    /// How the server is called, in the process list and in `servers.json`.
    pub name: Option<String>,
    /// False starts it for a look, without touching `servers.json`.
    pub register: Option<bool>,
    /// Set to run `report server` on another machine, over SSH.
    pub ssh: Option<String>,
    pub workdir: Option<String>,
    pub venv: Option<String>,
    pub install: Option<String>,
    pub timeout: Option<f64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UpgradeOptions {
    /// How the server is called, for the process list.
    pub name: Option<String>,
    pub ssh: String,
    pub workdir: Option<String>,
    pub venv: Option<String>,
    pub install: Option<String>,
    pub timeout: Option<f64>,
}

/// Bring a remote server's qibocamp up to date: `report server upgrade`,
/// supervised like any other process, so its output is in Running and the
/// launcher knows when it is done.
#[tauri::command]
pub async fn upgrade_server(app: AppHandle, options: UpgradeOptions) -> Result<ProcessInfo, String> {
    cli::off_main(move || upgrade(app, options)).await?
}

fn upgrade(app: AppHandle, options: UpgradeOptions) -> Result<ProcessInfo, String> {
    let supervisor = app.state::<Supervisor>();
    let ssh = options.ssh.trim().to_string();
    if ssh.is_empty() {
        return Err("Only a server on another machine has an environment to update.".to_string());
    }
    let mut arguments = vec!["report".to_string(), "server".to_string(), "upgrade".to_string()];
    push_text(&mut arguments, "--ssh", Some(ssh.clone()));
    push_text(&mut arguments, "--workdir", options.workdir.clone());
    push_text(&mut arguments, "--venv", options.venv.clone());
    push_text(
        &mut arguments,
        "--install",
        options
            .install
            .clone()
            .filter(|value| !value.trim().is_empty())
            .or_else(|| Some(cli::default_package())),
    );
    if let Some(timeout) = options.timeout {
        arguments.push("--timeout".to_string());
        arguments.push(format!("{timeout}"));
    }
    let label = format!("Update {}", options.name.clone().filter(|n| !n.trim().is_empty()).unwrap_or_else(|| format!("ssh {ssh}")));
    let detail = format!("ssh {ssh} · {}", options.venv.clone().filter(|v| !v.trim().is_empty()).unwrap_or_else(|| "qibocamp".to_string()));
    supervisor.spawn(&app, "update", label, detail, arguments, None, None)
}

#[tauri::command]
pub fn start_server(
    app: AppHandle,
    supervisor: State<Supervisor>,
    options: ServerOptions,
) -> Result<ProcessInfo, String> {
    let ssh = options
        .ssh
        .clone()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty());
    if ssh.is_some() && options.mode != "server" {
        return Err("Only 'report server' can run on another machine.".to_string());
    }

    let directory = options
        .directory
        .clone()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty());
    let name = options
        .name
        .clone()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty());

    let mut arguments = vec!["report".to_string(), options.mode.clone()];
    match options.mode.as_str() {
        "server" | "dev" => {
            // No directory is the shared one, which the command line locates
            // and creates on the machine it runs on. A named one has to be
            // there — unless it belongs to the other machine over SSH, where
            // only the worker can tell.
            if let Some(directory) = &directory {
                if ssh.is_none() && !PathBuf::from(directory).is_dir() {
                    return Err(format!("'{directory}' is not a directory."));
                }
                arguments.push(directory.clone());
            }
        }
        other => return Err(format!("Unknown server mode '{other}'.")),
    }
    // A local server writes nothing where it starts: a fresh temporary
    // directory keeps it out of the reports and out of the home folder, as
    // the worker does on a remote host.
    let working_directory = if ssh.is_none() {
        Some(scratch_directory()?)
    } else {
        None
    };
    if ssh.is_none() {
        arguments.push("--host".to_string());
        arguments.push(options.host.clone());
        arguments.push("--port".to_string());
        arguments.push(options.port.to_string());
    }
    arguments.push(if options.auth { "--auth" } else { "--no-auth" }.to_string());
    push_text(&mut arguments, "--name", name.clone());
    if options.register == Some(false) {
        arguments.push("--no-register".to_string());
    }
    if options.mode == "dev" {
        if let Some(port) = options.frontend_port {
            arguments.push("--frontend-port".to_string());
            arguments.push(port.to_string());
        }
    }
    // Without a destination the rest of the remote options mean nothing, and
    // the command line refuses them: they go together or not at all.
    if ssh.is_some() {
        push_text(&mut arguments, "--ssh", ssh.clone());
        push_text(&mut arguments, "--workdir", options.workdir.clone());
        push_text(&mut arguments, "--venv", options.venv.clone());
        // The remote environment gets the same qibocamp as this
        // launcher, unless told otherwise: the command line's own default is
        // the PyPI release, which may not exist yet.
        push_text(
            &mut arguments,
            "--install",
            options
                .install
                .clone()
                .filter(|value| !value.trim().is_empty())
                .or_else(|| Some(cli::default_package())),
        );
        if let Some(timeout) = options.timeout {
            arguments.push("--timeout".to_string());
            arguments.push(format!("{timeout}"));
        }
    }

    let served = directory
        .clone()
        .unwrap_or_else(|| "shared reports directory".to_string());
    match ssh {
        // The tunnel picks its own local port, so wait for the address the
        // command prints instead of guessing one.
        Some(ssh) => supervisor.spawn(
            &app,
            "server",
            name.unwrap_or_else(|| format!("ssh {ssh}")),
            format!("ssh {ssh} · {served}"),
            arguments,
            None,
            None,
        ),
        None => {
            let url = format!("http://{}:{}", options.host, options.port);
            supervisor.spawn(
                &app,
                "server",
                name.unwrap_or_else(|| url.clone()),
                format!("{url} · {served}"),
                arguments,
                working_directory,
                Some(url),
            )
        }
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct NotebookOptions {
    pub connection: Option<String>,
    pub ssh: Option<String>,
    pub queue: Option<String>,
    pub slurm: Option<String>,
    pub workdir: Option<String>,
    pub venv: Option<String>,
    pub marimo: Option<bool>,
    pub timeout: Option<f64>,
    pub subnet: Option<String>,
    /// What the kernel environment must hold, in requirements.txt format.
    pub requirements: Option<String>,
    /// Where the session keeps its reports, exported as QIBOCAMP_REPORTS_DIR.
    pub reports: Option<String>,
    /// The remote execution connection, exported as QIBOCAL_WIDGETS_CONNECTION.
    pub remote: Option<String>,
    /// Regenerate a SLURM connection's environment on a computing node, not the access node.
    #[serde(alias = "rebuild_on_compute")]
    pub rebuild_on_compute: Option<bool>,
}

/// A fresh directory under the system temporary folder, for a process to start from.
fn scratch_directory() -> Result<PathBuf, String> {
    let path = std::env::temp_dir().join(format!(
        "qibocamp-{}-{}",
        std::process::id(),
        now_millis()
    ));
    std::fs::create_dir_all(&path)
        .map_err(|error| format!("Could not create '{}': {error}", path.display()))?;
    Ok(path)
}

fn push_text(arguments: &mut Vec<String>, flag: &str, value: Option<String>) {
    if let Some(value) = value.map(|value| value.trim().to_string()) {
        if !value.is_empty() {
            arguments.push(flag.to_string());
            arguments.push(value);
        }
    }
}

/// `qibocamp notebook <subcommand> [CONNECTION] <options>`, and the connection's name.
fn notebook_arguments(subcommand: &str, options: &NotebookOptions) -> (Vec<String>, Option<String>) {
    let mut arguments = vec!["notebook".to_string(), subcommand.to_string()];
    let connection = options
        .connection
        .clone()
        .map(|name| name.trim().to_string())
        .filter(|name| !name.is_empty());
    if let Some(name) = connection.clone() {
        arguments.push(name);
    }
    push_text(&mut arguments, "--ssh", options.ssh.clone());
    push_text(&mut arguments, "--queue", options.queue.clone());
    push_text(&mut arguments, "--slurm", options.slurm.clone());
    push_text(&mut arguments, "--workdir", options.workdir.clone());
    push_text(&mut arguments, "--venv", options.venv.clone());
    push_text(&mut arguments, "--subnet", options.subnet.clone());
    push_text(&mut arguments, "--requirements", options.requirements.clone());
    push_text(&mut arguments, "--reports", options.reports.clone());
    push_text(&mut arguments, "--remote", options.remote.clone());
    if options.rebuild_on_compute == Some(true) {
        arguments.push("--rebuild-on-compute".to_string());
    }
    if let Some(marimo) = options.marimo {
        arguments.push(if marimo { "--marimo" } else { "--jupyter" }.to_string());
    }
    if let Some(timeout) = options.timeout {
        arguments.push("--timeout".to_string());
        arguments.push(format!("{timeout}"));
    }
    (arguments, connection)
}

/// Destroy a connection's kernel environment and make it again from qibocal
/// and its requirements: `qibocamp notebook rebuild`, supervised like any
/// other process, so its output is in Running.
#[tauri::command]
pub fn rebuild_notebook(
    app: AppHandle,
    supervisor: State<Supervisor>,
    options: NotebookOptions,
) -> Result<ProcessInfo, String> {
    let (arguments, connection) = notebook_arguments("rebuild", &options);
    let label = format!("Regenerate env {}", connection.unwrap_or_else(|| "kernel environment".to_string()));
    let detail = format!(
        "{} · {}",
        options.ssh.clone().map(|ssh| format!("ssh {ssh}")).unwrap_or_else(|| "this machine".to_string()),
        options.venv.clone().filter(|venv| !venv.trim().is_empty()).unwrap_or_else(|| "qibocal".to_string())
    );
    supervisor.spawn(&app, "update", label, detail, arguments, None, None)
}

/// Install qibocal-agent where the launcher keeps it, at a branch, tag or
/// commit: `qibocamp agents install`, as a process of its own in Running.
#[tauri::command]
pub fn install_agent_tools(
    app: AppHandle,
    supervisor: State<Supervisor>,
    reference: Option<String>,
) -> Result<ProcessInfo, String> {
    let reference = reference
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "main".to_string());
    let arguments = vec![
        "agents".to_string(),
        "install".to_string(),
        "--ref".to_string(),
        reference.clone(),
    ];
    let detail = format!("qibocal-mcp-servers {reference} · {}", cli::agent_tools_directory().display());
    supervisor.spawn(&app, "update", "Install qibocal-agent".to_string(), detail, arguments, None, None)
}

#[tauri::command]
pub fn start_notebook(
    app: AppHandle,
    supervisor: State<Supervisor>,
    options: NotebookOptions,
) -> Result<ProcessInfo, String> {
    let (mut arguments, connection) = notebook_arguments("connect", &options);
    // The launcher opens the URL itself, once the session reports it.
    arguments.push("--no-interactive".to_string());

    let label = connection.unwrap_or_else(|| {
        if options.marimo.unwrap_or(false) {
            "Marimo".to_string()
        } else {
            "JupyterLab".to_string()
        }
    });
    let detail = options
        .ssh
        .clone()
        .map(|ssh| format!("ssh {ssh}"))
        .unwrap_or_else(|| "local session".to_string());
    supervisor.spawn(&app, "notebook", label, detail, arguments, None, None)
}

#[tauri::command]
pub fn processes(supervisor: State<Supervisor>) -> Vec<ProcessInfo> {
    supervisor.list()
}

#[tauri::command]
pub fn set_process_launch(app: AppHandle, supervisor: State<Supervisor>, id: String, launch: serde_json::Value) -> bool {
    supervisor.set_launch(&app, &id, launch)
}

#[tauri::command]
pub fn process_log(supervisor: State<Supervisor>, id: String) -> Vec<String> {
    supervisor.log(&id)
}

#[tauri::command]
pub fn stop_process(
    app: AppHandle,
    supervisor: State<Supervisor>,
    id: String,
) -> Result<Option<ProcessInfo>, String> {
    supervisor.stop(&app, &id)?;
    Ok(supervisor.snapshot(&id))
}

/// Close the launcher from the interface: "Force close" kills what is left.
#[tauri::command]
pub fn quit(app: AppHandle, supervisor: State<Supervisor>, force: bool) {
    if force {
        supervisor.kill_all();
    }
    app.exit(0);
}

#[tauri::command]
pub fn forget_process(supervisor: State<Supervisor>, id: String) -> Result<(), String> {
    supervisor.forget(&id)
}
