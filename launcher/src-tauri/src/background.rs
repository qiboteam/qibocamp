//! The launcher in the background.
//!
//! Closing the last window hides it: the launcher keeps running, its servers
//! and sessions with it, and comes back from the tray icon, from the dock on
//! macOS, or from the next window opened. Quitting is explicit — the menu,
//! `Cmd/Ctrl+Q`, the tray — and waits for what runs, as closing did before.
//! What happens meanwhile is said with a system notification, when the
//! interface asks for one.

use tauri::image::Image;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, EventTarget, Manager};
use tauri_plugin_notification::NotificationExt;

use crate::process::Supervisor;

/// How many windows are on screen: a window closed while others show simply closes.
pub fn shown_windows(app: &AppHandle) -> usize {
    app.webview_windows()
        .values()
        .filter(|window| window.is_visible().unwrap_or(false))
        .count()
}

/// Bring the windows back: every hidden one shown, the first of them focused.
pub fn show_windows(app: &AppHandle) {
    let mut first = true;
    for window in app.webview_windows().values() {
        let _ = window.show();
        let _ = window.unminimize();
        if first {
            let _ = window.set_focus();
            first = false;
        }
    }
}

/// The event a page receives when an item of the application menu is chosen.
pub const MENU_EVENT: &str = "qibocamp://menu";

/// An item of the application menu chosen: handed to the window in front —
/// else one on screen, else the main one — whose page runs its action.
///
/// The pages build the menu, but its actions are not theirs to keep: a page
/// that installed it may have closed, and reinstalling it whenever a window
/// takes the focus loops on Linux, where setting the menu replaces the menu
/// bar of every window and moves the focus again. So the menu is installed
/// when its content changes, and its items are routed here.
pub fn route_menu_event(app: &AppHandle, id: &str) {
    if !id.starts_with("menu-") {
        return;
    }
    let windows = app.webview_windows();
    let target = windows
        .values()
        .find(|window| window.is_focused().unwrap_or(false))
        .or_else(|| windows.values().find(|window| window.is_visible().unwrap_or(false)))
        .or_else(|| windows.get(crate::MAIN_WINDOW));
    if let Some(window) = target {
        let _ = app.emit_to(EventTarget::webview_window(window.label()), MENU_EVENT, id);
    }
}

/// The tray glyph: the Q alone on nothing, never the dock's plate. On macOS
/// a template image — black, its shape all that counts — which the system
/// paints to suit a light or dark menu bar. Elsewhere nothing repaints it:
/// white for the dark panels of Linux and of Windows in dark mode, black
/// when Windows says its taskbar is light.
fn tray_icon() -> tauri::Result<(Image<'static>, bool)> {
    if cfg!(target_os = "macos") {
        return Ok((Image::from_bytes(include_bytes!("../tray/tray-template.png"))?, true));
    }
    let glyph: &'static [u8] = if light_taskbar() {
        include_bytes!("../tray/tray-black.png")
    } else {
        include_bytes!("../tray/tray-white.png")
    };
    Ok((Image::from_bytes(glyph)?, false))
}

/// Whether the Windows taskbar is light: `SystemUsesLightTheme` is 1. Read
/// once, when the tray is made; anywhere else, and when unsure, it is dark.
fn light_taskbar() -> bool {
    if !cfg!(target_os = "windows") {
        return false;
    }
    std::process::Command::new("reg")
        .args([
            "query",
            r"HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            "/v",
            "SystemUsesLightTheme",
        ])
        .output()
        .ok()
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).contains("0x1"))
        .unwrap_or(false)
}

/// The icon in the tray — the menu bar on macOS — with the two things it
/// offers: the windows back, and quitting.
pub fn install_tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "tray-open", "Open Qibocamp", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "tray-quit", "Quit Qibocamp", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &PredefinedMenuItem::separator(app)?, &quit])?;
    let mut tray = TrayIconBuilder::with_id("main")
        .tooltip("Qibocamp")
        .menu(&menu)
        // A menu bar extra opens its menu on macOS; elsewhere a click is the window.
        .show_menu_on_left_click(cfg!(target_os = "macos"))
        .on_menu_event(|app, event| match event.id().as_ref() {
            "tray-open" => show_windows(app),
            "tray-quit" => quit_after_processes(app),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if cfg!(target_os = "macos") {
                return;
            }
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_windows(tray.app_handle());
            }
        });
    let (icon, template) = tray_icon()?;
    tray = tray.icon(icon).icon_as_template(template);
    tray.build(app)?;
    Ok(())
}

/// Quit, waiting for what runs: the windows come back first, so that the
/// closing dialog — whom it waits for, and the way to force it — is seen.
fn quit_after_processes(app: &AppHandle) {
    show_windows(app);
    if app.state::<Supervisor>().request_close(app) {
        app.exit(0);
    }
}

/// `Cmd/Ctrl+Q`, the Quit item: the one way out that waits for the processes.
#[tauri::command]
pub fn request_quit(app: AppHandle) {
    quit_after_processes(&app);
}

/// A system notification: for what happens while the user is not looking —
/// a server up, a session ready, something failed, new reports — decided
/// by the interface, which knows what is on screen.
#[tauri::command]
pub fn system_notify(app: AppHandle, title: String, body: String) -> Result<(), String> {
    app.notification()
        .builder()
        .title(title)
        .body(body)
        .show()
        .map_err(|error| error.to_string())
}
