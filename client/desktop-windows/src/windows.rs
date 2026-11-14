use crate::{Bounds, Command, Palette, Result, TrayLayout, WindowRole, WindowSpec, commands, emit};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::fs::{self, File};
use std::hash::{DefaultHasher, Hash, Hasher};
use std::io::{BufRead, BufReader, Read, Write};
use std::mem::{size_of, zeroed};
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command as ProcessCommand, Stdio};
use std::ptr::{null, null_mut};
use std::sync::mpsc::{RecvTimeoutError, channel};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use windows_sys::Win32::Foundation::*;
use windows_sys::Win32::Graphics::Gdi::{
    GetMonitorInfoW, MONITOR_DEFAULTTONEAREST, MONITOR_DEFAULTTONULL, MONITORINFO, MonitorFromPoint,
    MonitorFromRect, MonitorFromWindow, ScreenToClient,
};
use windows_sys::Win32::System::Diagnostics::ToolHelp::*;
use windows_sys::Win32::System::Threading::*;
use windows_sys::Win32::UI::HiDpi::*;
use windows_sys::Win32::UI::Input::KeyboardAndMouse::*;
use windows_sys::Win32::UI::WindowsAndMessaging::*;

const MAX_JOURNAL_BYTES: u64 = 1_048_576;
const LEASE_DURATION: Duration = Duration::from_secs(10);
const OVERLAY_GEOMETRY_TIMEOUT: Duration = Duration::from_secs(2);
const DESKTOP_LAYER_TIMEOUT: Duration = Duration::from_secs(2);
const HOTKEY_ID: i32 = 0x5341;
const NON_RUDE_PROPERTY: &str = "NonRudeHWND";

mod workspace;
use workspace::{RestoreAppBar, Workspace};
mod layers;
use layers::DesktopLayers;
mod events;
use events::WinEvents;
mod applications;
use applications::Applications;
mod jump_lists;
mod regions;
use regions::SavedRegion;
mod tray;
use tray::Tray;
mod tray_placement;
use tray_placement::RestoreTrayPlacement;
mod tray_style;
use tray_style::{RestoreTrayStyle, TrayStyle};
mod command;
mod geometry;
mod identity;
mod journal;
mod session;
mod wallpaper;
use command::{execute_command, interrupt_session};
pub(crate) use geometry::{monitor_bounds, rectangle, window_on_display};
pub(crate) use identity::{
    Identity, actual_parent, class_name, created, is_explorer, normalize_target, owned_by, process, process_image,
    system_popup, window_pid,
};
pub(crate) use journal::{
    Journal, JournalPhase, RestoreWindow, RestoringSignal, atomic_write, read_journal, read_lease, read_state,
    require_session,
    sidecar, stop_matching_host,
};
use session::Session;
pub use geometry::display_bounds;
pub use identity::allow_foreground;
pub(crate) use wallpaper::refresh_static_wallpaper;

pub fn tray_probe(handle: u64) -> Result<()> {
    tray::probe(handle)
}

pub fn tray_style_attach(pid: u32, channel: &str) -> Result<()> {
    tray_style::attach(pid, channel)
}

pub(crate) fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(Some(0)).collect()
}

pub(crate) fn foreground_window() -> HWND {
    unsafe { GetForegroundWindow() }
}

pub(crate) fn failure(operation: &str) -> String {
    format!("{operation}: Windows error {}", unsafe { GetLastError() })
}

pub(crate) struct OwnedHandle(HANDLE);

impl Drop for OwnedHandle {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.0);
        }
    }
}

pub(crate) struct MutexGuard(OwnedHandle);

impl MutexGuard {
    fn exclusive(name: &str) -> Result<Self> {
        let handle = unsafe { CreateMutexW(null(), 1, wide(name).as_ptr()) };
        if handle.is_null() {
            return Err(failure("CreateMutexW"));
        }
        let owned = OwnedHandle(handle);
        if unsafe { GetLastError() } == ERROR_ALREADY_EXISTS {
            return Err("another desktop host or restoration is active".into());
        }
        Ok(Self(owned))
    }

    fn restoration() -> Result<Self> {
        let handle =
            unsafe { CreateMutexW(null(), 0, wide("Local\\SpiritAgentDesktopRestore").as_ptr()) };
        if handle.is_null() {
            return Err(failure("CreateMutexW restoration"));
        }
        let owned = OwnedHandle(handle);
        let result = unsafe { WaitForSingleObject(handle, 5_000) };
        if result != WAIT_OBJECT_0 && result != WAIT_ABANDONED {
            return Err("timed out acquiring desktop restoration".into());
        }
        Ok(Self(owned))
    }
}

impl Drop for MutexGuard {
    fn drop(&mut self) {
        unsafe {
            ReleaseMutex(self.0.0);
        }
    }
}

unsafe extern "system" fn collect_window(window: HWND, data: LPARAM) -> i32 {
    let windows = unsafe { &mut *(data as *mut Vec<HWND>) };
    windows.push(window);
    1
}

pub(crate) fn top_windows() -> Result<Vec<HWND>> {
    let mut windows = Vec::new();
    if unsafe { EnumWindows(Some(collect_window), &mut windows as *mut _ as LPARAM) } == 0 {
        return Err(failure("EnumWindows"));
    }
    Ok(windows)
}

pub(crate) fn child(window: HWND, class: &str) -> HWND {
    unsafe { FindWindowExW(window, null_mut(), wide(class).as_ptr(), null()) }
}

pub(crate) fn shell_windows(display: Bounds) -> Result<Vec<Identity>> {
    let shell = unsafe { GetShellWindow() };
    if shell.is_null() || !is_explorer(window_pid(shell)) {
        return Err("Windows Explorer is not the active desktop shell".into());
    }
    let shell_pid = window_pid(shell);
    let mut captured = Vec::new();
    for window in top_windows()? {
        if window_pid(window) != shell_pid {
            continue;
        }
        if ["Shell_TrayWnd", "Shell_SecondaryTrayWnd"].contains(&class_name(window).as_str())
            && tray::on_display(window, display)?
        {
            captured.push(Identity::capture(window)?);
        }
    }
    Ok(captured)
}

pub(crate) fn worker() -> Result<Identity> {
    let progman = unsafe { FindWindowW(wide("Progman").as_ptr(), null()) };
    let shell = unsafe { GetShellWindow() };
    if progman.is_null()
        || shell.is_null()
        || window_pid(progman) != window_pid(shell)
        || !is_explorer(window_pid(progman))
    {
        return Err("Explorer desktop is unavailable".into());
    }
    // 图标层跨所有显示器共享，必须保留原窗口及其输入和显示偏好。
    let mut layers = top_windows()?;
    unsafe { EnumChildWindows(progman, Some(collect_window), &mut layers as *mut _ as LPARAM) };
    for window in layers {
        if window_pid(window) != window_pid(shell)
            || !["Progman", "WorkerW"].contains(&class_name(window).as_str())
            || unsafe { IsWindowEnabled(window) } == 0
        {
            continue;
        }
        let view = child(window, "SHELLDLL_DefView");
        if !view.is_null()
            && window_pid(view) == window_pid(shell)
            && unsafe { GetParent(view) } == window
            && !child(view, "SysListView32").is_null()
        {
            return Identity::capture(window);
        }
    }
    Err("Explorer did not expose the desktop icon parent layer".into())
}

pub(crate) fn visibility(window: &Identity, visible: bool) -> Result<()> {
    if !window.valid() {
        return Err("window identity changed before visibility update".into());
    }
    if (unsafe { IsWindowVisible(window.window()) } != 0) == visible {
        return Ok(());
    }
    if unsafe {
        SetWindowPos(
            window.window(),
            null_mut(),
            0,
            0,
            0,
            0,
            SWP_NOMOVE
                | SWP_NOSIZE
                | SWP_NOZORDER
                | SWP_NOACTIVATE
                | SWP_ASYNCWINDOWPOS
                | if visible {
                    SWP_SHOWWINDOW
                } else {
                    SWP_HIDEWINDOW
                },
        )
    } == 0
    {
        return Err(failure("update desktop window visibility"));
    }
    let deadline = Instant::now() + Duration::from_millis(1_500);
    while Instant::now() < deadline {
        if !window.valid() {
            return Err("window was destroyed during visibility update".into());
        }
        if (unsafe { IsWindowVisible(window.window()) } != 0) == visible {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(25));
    }
    Err("window visibility update timed out".into())
}

pub(crate) fn set_long(window: HWND, index: i32, value: isize) -> Result<()> {
    unsafe {
        SetLastError(0);
    }
    if unsafe { SetWindowLongPtrW(window, index, value) } == 0 && unsafe { GetLastError() } != 0 {
        return Err(failure("SetWindowLongPtrW"));
    }
    Ok(())
}

pub(crate) fn set_parent(window: HWND, parent: HWND) -> Result<()> {
    unsafe {
        SetLastError(0);
    }
    if unsafe { SetParent(window, parent) }.is_null() && unsafe { GetLastError() } != 0 {
        return Err(failure("SetParent"));
    }
    let expected = if parent.is_null() {
        unsafe { GetDesktopWindow() }
    } else {
        parent
    };
    // GetParent 根据 WS_CHILD / WS_POPUP 返回不同结果，GA_PARENT 只核验实际父层。
    if unsafe { GetAncestor(window, GA_PARENT) } != expected {
        return Err("desktop window parent did not settle".into());
    }
    Ok(())
}

pub(crate) fn set_non_rude(window: HWND, value: u64) -> Result<()> {
    let value = usize::try_from(value).map_err(|_| "invalid desktop non-fullscreen property")?;
    let property = wide(NON_RUDE_PROPERTY);
    if value == 0 {
        unsafe { RemovePropW(window, property.as_ptr()) };
    } else if unsafe { SetPropW(window, property.as_ptr(), value as HANDLE) } == 0 {
        return Err(failure("set desktop non-fullscreen property"));
    }
    if unsafe { GetPropW(window, property.as_ptr()) } as usize != value {
        return Err("desktop non-fullscreen property did not settle".into());
    }
    Ok(())
}

pub(crate) fn move_window(window: HWND, parent: HWND, bounds: Bounds) -> Result<()> {
    let mut point = POINT {
        x: bounds.x,
        y: bounds.y,
    };
    if !parent.is_null() && unsafe { ScreenToClient(parent, &mut point) } == 0 {
        return Err(failure("ScreenToClient"));
    }
    if unsafe {
        SetWindowPos(
            window,
            null_mut(),
            point.x,
            point.y,
            bounds.width,
            bounds.height,
            SWP_NOACTIVATE | SWP_NOZORDER | SWP_FRAMECHANGED,
        )
    } == 0
    {
        return Err(failure("SetWindowPos"));
    }
    let actual = rectangle(window)?;
    if actual.x.abs_diff(bounds.x) > 2
        || actual.y.abs_diff(bounds.y) > 2
        || actual.width != bounds.width
        || actual.height != bounds.height
    {
        return Err("desktop geometry changed during cross-process DPI attachment".into());
    }
    Ok(())
}

pub(in crate::windows) fn attach(saved: &RestoreWindow, layers: &DesktopLayers, bounds: Bounds) -> Result<()> {
    if !saved.identity.valid() || !layers.valid() {
        return Err("desktop attachment window identity changed".into());
    }
    let window = saved.identity.window();
    let layer = layers.background_parent().window();
    let original_dpi = unsafe { GetWindowDpiAwarenessContext(window) };
    if original_dpi.is_null()
        || unsafe {
            AreDpiAwarenessContextsEqual(original_dpi, GetWindowDpiAwarenessContext(layer))
        } == 0
    {
        return Err(
            "Electron and Explorer DPI awareness differ; desktop hosting was rejected".into(),
        );
    }
    let style = (saved.style as u32
        & !(WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX))
        | WS_CHILD;
    set_long(window, GWL_STYLE, style as isize)?;
    set_long(
        window,
        GWL_EXSTYLE,
        ((saved.ex_style as u32 & !(WS_EX_APPWINDOW | WS_EX_TOPMOST))
            | WS_EX_TRANSPARENT
            | WS_EX_NOACTIVATE
            | if layers.layered { WS_EX_LAYERED } else { 0 }) as isize,
    )?;
    if layers.layered && unsafe { SetLayeredWindowAttributes(window, 0, 255, LWA_ALPHA) } == 0 {
        return Err(failure("enable wallpaper composition below desktop icons"));
    }
    set_parent(window, layer)?;
    if saved.role == WindowRole::Background {
        // 图标视图覆盖整屏，壁纸表面不能按这个透明兄弟窗口裁空。
        let attached_style = unsafe { GetWindowLongPtrW(window, GWL_STYLE) } as u32;
        set_long(
            window,
            GWL_STYLE,
            (attached_style & !WS_CLIPSIBLINGS) as isize,
        )?;
    }
    if unsafe { AreDpiAwarenessContextsEqual(original_dpi, GetWindowDpiAwarenessContext(window)) }
        == 0
    {
        return Err("Electron DPI awareness changed after Explorer attachment".into());
    }
    move_window(window, layer, bounds)?;
    let mut client: RECT = unsafe { zeroed() };
    if unsafe { GetClientRect(window, &mut client) } == 0 {
        return Err(failure("GetClientRect"));
    }
    if client.right - client.left != bounds.width || client.bottom - client.top != bounds.height {
        return Err(format!(
            "desktop client area does not fill its monitor: {}x{} instead of {}x{} (style {:#x}, ex-style {:#x})",
            client.right - client.left,
            client.bottom - client.top,
            bounds.width,
            bounds.height,
            unsafe { GetWindowLongPtrW(window, GWL_STYLE) },
            unsafe { GetWindowLongPtrW(window, GWL_EXSTYLE) },
        ));
    }
    let mut ui_state = 0;
    if unsafe {
        SendMessageTimeoutW(
            layer,
            WM_QUERYUISTATE,
            0,
            0,
            SMTO_ABORTIFHUNG,
            1_000,
            &mut ui_state,
        )
    } == 0
    {
        return Err(failure("Explorer UI state query"));
    }
    let mut result = 0;
    let clear =
        (UIS_CLEAR as usize) | (((UISF_ACTIVE | UISF_HIDEACCEL | UISF_HIDEFOCUS) as usize) << 16);
    let set = (UIS_SET as usize) | (ui_state << 16);
    for state in [clear, set] {
        if unsafe {
            SendMessageTimeoutW(
                window,
                WM_UPDATEUISTATE,
                state,
                0,
                SMTO_ABORTIFHUNG,
                1_000,
                &mut result,
            )
        } == 0
        {
            return Err(failure("desktop UI state synchronization"));
        }
    }
    visibility(&saved.identity, true)?;
    Ok(())
}

pub(crate) fn set_top_level(saved: &RestoreWindow, bounds: Bounds, topmost: bool) -> Result<()> {
    if !saved.identity.valid() {
        return Err("desktop window identity changed".into());
    }
    let window = saved.identity.window();
    // 整屏交互层仍是桌面，不能触发 Shell 的全屏勿扰与任务栏降层。
    set_non_rude(window, 1)?;
    let mouse_passthrough =
        unsafe { GetWindowLongPtrW(window, GWL_EXSTYLE) } as u32 & WS_EX_TRANSPARENT;
    set_long(
        window,
        GWL_STYLE,
        (saved.style as u32 & !WS_CHILD | WS_POPUP) as isize,
    )?;
    set_parent(window, null_mut())?;
    set_long(
        window,
        GWL_EXSTYLE,
        ((saved.ex_style as u32 & !(WS_EX_APPWINDOW | WS_EX_TRANSPARENT | WS_EX_TOPMOST))
            | WS_EX_TOOLWINDOW
            | mouse_passthrough) as isize,
    )?;
    if unsafe {
        SetWindowPos(
            window,
            if topmost {
                HWND_TOPMOST
            } else {
                HWND_NOTOPMOST
            },
            bounds.x,
            bounds.y,
            bounds.width,
            bounds.height,
            SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
    } == 0
    {
        return Err(failure("set desktop top-level window"));
    }
    Ok(())
}

pub(crate) fn restore(path: &Path, expected_session: Option<&str>) -> Result<bool> {
    let _lock = MutexGuard::restoration()?;
    restore_locked(path, expected_session)
}

pub(crate) fn restore_locked(path: &Path, expected_session: Option<&str>) -> Result<bool> {
    let Some(journal) = read_journal(path)? else {
        return Ok(false);
    };
    if expected_session.is_some_and(|session| session != journal.session) {
        return Err("restoration session changed".into());
    }
    let mut failures = Vec::new();
    if !matches!(journal.phase, JournalPhase::Prepared) {
        if let Some(style) = &journal.tray_style {
            if let Err(error) = style.restore() {
                failures.push(error);
            }
        }
        if let Some(appbar) = &journal.appbar {
            if let Err(error) = appbar.restore() {
                failures.push(error);
            }
        }
        // 先恢复系统界面，避免应用窗口复位阻断桌面可用性。
        for saved in &journal.tray_placements {
            if let Err(error) = saved.restore() {
                failures.push(error);
            }
        }
        for saved in &journal.regions {
            if journal.shell.iter().any(|identity| identity.handle == saved.identity.handle)
                && !is_explorer(saved.identity.pid)
            {
                continue;
            }
            if let Err(error) = journal.restore_region(saved) {
                failures.push(error);
            }
        }
        for item in &journal.shell {
            if !item.valid()
                || !is_explorer(item.pid)
                || !["Shell_TrayWnd", "Shell_SecondaryTrayWnd"]
                .contains(&item.class.as_str())
            {
                continue;
            }
            if let Err(error) = visibility(item, item.visible) {
                failures.push(error);
            }
        }
        for saved in &journal.windows {
            if !saved.identity.valid() {
                continue;
            }
            let window = saved.identity.window();
            if let Err(error) = visibility(&saved.identity, false) {
                failures.push(error);
            }
            let parent = saved.parent as usize as HWND;
            // 先从 Explorer 摘除子窗，再撤销子窗和分层样式。
            let result = set_parent(window, parent)
                .and_then(|()| set_long(window, GWL_STYLE, saved.style as isize))
                .and_then(|()| set_long(window, GWL_EXSTYLE, saved.ex_style as isize))
                .and_then(|()| move_window(window, parent, saved.bounds))
                .and_then(|()| {
                    // 置顶属性属于层序状态，仅恢复 GWL_EXSTYLE 不能清除其残留。
                    if unsafe {
                        SetWindowPos(
                            window,
                            if saved.ex_style as u32 & WS_EX_TOPMOST != 0 {
                                HWND_TOPMOST
                            } else {
                                HWND_NOTOPMOST
                            },
                            0,
                            0,
                            0,
                            0,
                            SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE,
                        )
                    } == 0
                    {
                        return Err(failure("restore desktop window topmost state"));
                    }
                    Ok(())
                });
            if let Err(error) = result {
                failures.push(error);
            }
            if let Some(value) = saved.non_rude {
                if let Err(error) = set_non_rude(window, value) {
                    failures.push(error);
                }
            }
            if let Err(error) = visibility(&saved.identity, saved.identity.visible) {
                failures.push(error);
            }
        }
        if let Err(error) = wallpaper::redraw_released_display(journal.desktop_bounds) {
            failures.push(error);
        }
        if failures.is_empty() {
            if let Err(error) = wallpaper::reload_after_release() {
                log_restoration_diagnostic(path, &error);
            }
        }
    }
    if !failures.is_empty() {
        return Err(failures.join("; "));
    }
    match fs::remove_file(sidecar(path, ".lease")) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(format!("remove desktop lease: {error}")),
    }
    fs::remove_file(path).map_err(|error| format!("remove restored journal: {error}"))?;
    Ok(true)
}

pub(crate) fn restore_interrupted(
    path: &Path,
    session: &str,
    reason: &str,
    restoring: &RestoringSignal,
) -> Result<bool> {
    let _lock = MutexGuard::restoration()?;
    let Some(journal) = read_journal(path)? else {
        return Ok(false);
    };
    if journal.session != session || !restoring.matches(&journal) {
        return Err("interruption session changed".into());
    }
    // 信号与接管写操作共享锁；恢复失败保留 journal 时，host 也不能再次隐藏系统界面。
    let requested = restoring.request();
    let marker = sidecar(path, ".interrupted");
    let recording = match requested {
        Ok(true) if unsafe { GetCurrentProcessId() } == journal.host_pid => Ok(()),
        _ => atomic_write(&marker, reason.as_bytes()),
    };
    // 信号失败时 guardian 只停止身份仍匹配的自有 parent host，随后仍优先恢复系统。
    let stop_error = if requested.is_err() {
        stop_matching_host(&journal).err()
    } else {
        None
    };
    for error in requested
        .err()
        .into_iter()
        .chain(recording.err())
        .chain(stop_error)
    {
        log_restoration_diagnostic(path, &format!("interruption marker write failed: {error}"));
    }
    // 标记失败也必须恢复系统界面。
    restore_locked(path, Some(session))
}

fn log_restoration_diagnostic(path: &Path, error: &str) {
    let diagnostic = format!("{error}\n");
    if let Ok(mut log) = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(sidecar(path, ".guardian.log"))
    {
        if log
            .metadata()
            .is_ok_and(|metadata| metadata.len() + diagnostic.len() as u64 <= 65_536)
        {
            let _ = log.write_all(diagnostic.as_bytes());
        }
    }
    let _ = std::io::stderr().write_all(diagnostic.as_bytes());
}

pub(crate) fn finish_failed_start(
    reason: String,
    restored: Result<bool>,
    guardian: &mut Child,
    prepared: bool,
) -> String {
    let mut reason = reason;
    if restored.is_ok() || prepared {
        if let Err(error) = retire_guardian(guardian) {
            reason = format!("{reason}; guardian cleanup: {error}");
        }
    }
    match restored {
        Ok(_) => reason,
        Err(rollback) => format!("{reason}; restoration: {rollback}"),
    }
}

pub(crate) fn retire_guardian(guardian: &mut Child) -> Result<()> {
    let deadline = Instant::now() + Duration::from_millis(250);
    while guardian
        .try_wait()
        .map_err(|error| error.to_string())?
        .is_none()
        && Instant::now() < deadline
    {
        thread::sleep(Duration::from_millis(25));
    }
    if guardian
        .try_wait()
        .map_err(|error| error.to_string())?
        .is_some()
    {
        return Ok(());
    }
    guardian
        .kill()
        .map_err(|error| format!("stop guardian: {error}"))?;
    let deadline = Instant::now() + Duration::from_secs(2);
    while guardian
        .try_wait()
        .map_err(|error| error.to_string())?
        .is_none()
    {
        if Instant::now() >= deadline {
            return Err("guardian exit timed out".into());
        }
        thread::sleep(Duration::from_millis(25));
    }
    Ok(())
}

pub fn recover(path: &Path) -> Result<()> {
    if unsafe { SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) } == 0 {
        return Err(failure("SetProcessDpiAwarenessContext"));
    }
    let _host = MutexGuard::exclusive("Local\\SpiritAgentDesktopHost")?;
    let restored = restore(path, None)?;
    emit(json!({ "event": "recovered", "restored": restored }));
    Ok(())
}

pub fn host(path: &Path) -> Result<()> {
    if unsafe { SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) } == 0 {
        return Err(failure("SetProcessDpiAwarenessContext"));
    }
    let _lock = MutexGuard::exclusive("Local\\SpiritAgentDesktopHost")?;
    let receiver = commands();
    let mut session: Option<Session> = None;
    emit(json!({ "event": "host_ready" }));
    loop {
        let command = receiver.recv_timeout(Duration::from_millis(200));
        let mut message: MSG = unsafe { zeroed() };
        while unsafe { PeekMessageW(&mut message, null_mut(), 0, 0, PM_REMOVE) } != 0 {
            unsafe {
                TranslateMessage(&message);
                DispatchMessageW(&message);
            }
        }
        match command {
            Ok(Ok(command)) => {
                let id = command.id();
                let exiting = matches!(command, Command::Stop { .. });
                match execute_command(path, &mut session, command) {
                    Ok(result) => emit(json!({ "id": id, "ok": true, "result": result })),
                    Err(reason) => emit(json!({ "id": id, "ok": false, "reason": reason })),
                }
                if exiting {
                    return Ok(());
                }
            }
            Ok(Err(reason)) => {
                return Err(interrupt_session(session.as_mut(), "command_channel_failure", reason));
            }
            Err(RecvTimeoutError::Disconnected) => {
                return Err(interrupt_session(
                    session.as_mut(),
                    "command_channel_closed",
                    "desktop parent closed command channel".into(),
                ));
            }
            Err(RecvTimeoutError::Timeout) => {}
        }
        if let Some(active) = session.as_mut() {
            if let Err(reason) = active.poll() {
                return Err(interrupt_session(Some(active), "host_failure", reason));
            }
        }
    }
}

pub fn guardian(path: &Path, session: &str) -> Result<()> {
    if unsafe { SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) } == 0 {
        return Err(failure("SetProcessDpiAwarenessContext"));
    }
    let journal = read_journal(path)?.ok_or("guardian journal is missing")?;
    if journal.session != session || actual_parent()? != journal.host_pid {
        return Err("guardian session ownership does not match".into());
    }
    let parent = process(journal.parent_pid)?;
    let host = process(journal.host_pid)?;
    if created(parent.0)? != journal.parent_created || created(host.0)? != journal.host_created {
        return Err("guardian process identity changed".into());
    }
    let restoring = RestoringSignal::new(&journal)?;
    // 紧急键由独立守护进程持有，宿主无响应时仍可请求恢复。
    if unsafe {
        RegisterHotKey(
            null_mut(),
            HOTKEY_ID,
            MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_NOREPEAT,
            b'D' as u32,
        )
    } == 0
    {
        return Err("Ctrl+Alt+Shift+D is unavailable; desktop takeover was rejected".into());
    }
    struct Hotkey;
    impl Drop for Hotkey {
        fn drop(&mut self) {
            unsafe {
                UnregisterHotKey(null_mut(), HOTKEY_ID);
            }
        }
    }
    let _hotkey = Hotkey;
    let mut lease = read_lease(path)?;
    let mut last_change = Instant::now();
    emit(json!({ "event": "guardian_ready" }));
    loop {
        if !path.exists() {
            return Ok(());
        }
        let mut message: MSG = unsafe { zeroed() };
        let mut emergency = false;
        while unsafe { PeekMessageW(&mut message, null_mut(), 0, 0, PM_REMOVE) } != 0 {
            if message.message == WM_HOTKEY && message.wParam == HOTKEY_ID as usize {
                emergency = true;
            }
        }
        let new_lease = read_lease(path).unwrap_or_default();
        if new_lease.starts_with(&format!("{session}:")) && new_lease != lease {
            lease = new_lease;
            last_change = Instant::now();
        }
        let lost_parent = unsafe { WaitForSingleObject(parent.0, 0) } != WAIT_TIMEOUT;
        let lost_host = unsafe { WaitForSingleObject(host.0, 0) } != WAIT_TIMEOUT;
        let lost_window = journal.windows.iter().any(|saved| !saved.identity.valid());
        let reason = if emergency {
            Some("emergency_shortcut")
        } else if lost_parent {
            Some("main_process_exit")
        } else if lost_host {
            Some("host_process_exit")
        } else if lost_window {
            Some("desktop_window_lost")
        } else if last_change.elapsed() > LEASE_DURATION {
            Some("heartbeat_expired")
        } else {
            None
        };
        if let Some(reason) = reason {
            loop {
                match restore_interrupted(path, session, reason, &restoring) {
                    Ok(_) => return Ok(()),
                    Err(error) => match read_journal(path) {
                        Ok(None) => return Ok(()),
                        Ok(Some(saved)) if saved.session != session => return Ok(()),
                        _ if path.exists() => thread::sleep(Duration::from_millis(500)),
                        _ => return Err(error),
                    },
                }
            }
        }
        thread::sleep(Duration::from_millis(200));
    }
}
