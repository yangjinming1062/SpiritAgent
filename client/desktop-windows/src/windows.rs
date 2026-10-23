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
    MonitorFromRect, MonitorFromWindow, RDW_ALLCHILDREN, RDW_ERASE, RDW_INVALIDATE, RedrawWindow,
    ScreenToClient,
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

pub fn tray_probe(handle: u64) -> Result<()> {
    tray::probe(handle)
}

pub fn tray_style_attach(pid: u32, channel: &str) -> Result<()> {
    tray_style::attach(pid, channel)
}

fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(Some(0)).collect()
}

fn foreground_window() -> HWND {
    unsafe { GetForegroundWindow() }
}

fn failure(operation: &str) -> String {
    format!("{operation}: Windows error {}", unsafe { GetLastError() })
}

struct OwnedHandle(HANDLE);

impl Drop for OwnedHandle {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.0);
        }
    }
}

struct MutexGuard(OwnedHandle);

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

struct RestoringSignal {
    handle: OwnedHandle,
    session: String,
    host_pid: u32,
    host_created: u64,
}

impl RestoringSignal {
    fn new(journal: &Journal) -> Result<Self> {
        let mut scope = DefaultHasher::new();
        journal.session.hash(&mut scope);
        journal.host_pid.hash(&mut scope);
        journal.host_created.hash(&mut scope);
        let name = format!("Local\\SpiritAgentDesktopRestoring-{:016x}", scope.finish());
        let handle = unsafe { CreateEventW(null(), 1, 0, wide(&name).as_ptr()) };
        if handle.is_null() {
            return Err(failure("CreateEventW desktop restoration"));
        }
        Ok(Self {
            handle: OwnedHandle(handle),
            session: journal.session.clone(),
            host_pid: journal.host_pid,
            host_created: journal.host_created,
        })
    }

    fn requested(&self) -> Result<bool> {
        match unsafe { WaitForSingleObject(self.handle.0, 0) } {
            WAIT_OBJECT_0 => Ok(true),
            WAIT_TIMEOUT => Ok(false),
            _ => Err(failure("desktop restoration state")),
        }
    }

    fn request(&self) -> Result<bool> {
        let previous = self.requested()?;
        if unsafe { SetEvent(self.handle.0) } == 0 {
            return Err(failure("signal desktop restoration"));
        }
        Ok(previous)
    }

    fn matches(&self, journal: &Journal) -> bool {
        journal.session == self.session
            && journal.host_pid == self.host_pid
            && journal.host_created == self.host_created
    }
}

fn stop_matching_host(journal: &Journal) -> Result<()> {
    if unsafe { GetCurrentProcessId() } == journal.host_pid {
        // host 的异常路径在恢复尝试后退出，不会返回命令循环。
        return Ok(());
    }
    if actual_parent()? != journal.host_pid {
        return Err("restoration cannot stop a process outside its guardian parent".into());
    }
    let host = unsafe {
        OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE | PROCESS_TERMINATE,
            0,
            journal.host_pid,
        )
    };
    if host.is_null() {
        return Err(failure("open restoration host"));
    }
    let host = OwnedHandle(host);
    match unsafe { WaitForSingleObject(host.0, 0) } {
        WAIT_OBJECT_0 => return Ok(()),
        WAIT_TIMEOUT => {}
        _ => return Err(failure("query restoration host state")),
    }
    if created(host.0)? != journal.host_created {
        return Err("restoration host process identity changed".into());
    }
    let image = fs::canonicalize(process_image(host.0)?)
        .map_err(|error| format!("resolve restoration host executable: {error}"))?;
    let own_image = fs::canonicalize(std::env::current_exe().map_err(|error| error.to_string())?)
        .map_err(|error| format!("resolve guardian executable: {error}"))?;
    if !image
        .to_string_lossy()
        .eq_ignore_ascii_case(&own_image.to_string_lossy())
    {
        return Err("restoration host executable does not match guardian".into());
    }
    if unsafe { TerminateProcess(host.0, 1) } == 0 {
        return Err(failure("stop restoration host"));
    }
    if unsafe { WaitForSingleObject(host.0, 1_000) } != WAIT_OBJECT_0 {
        return Err("restoration host did not stop".into());
    }
    Ok(())
}

fn process(pid: u32) -> Result<OwnedHandle> {
    let handle = unsafe {
        OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE,
            0,
            pid,
        )
    };
    if handle.is_null() {
        Err(failure("OpenProcess"))
    } else {
        Ok(OwnedHandle(handle))
    }
}

fn created(handle: HANDLE) -> Result<u64> {
    let (mut birth, mut exit, mut kernel, mut user) =
        unsafe { (zeroed(), zeroed(), zeroed(), zeroed()) };
    if unsafe { GetProcessTimes(handle, &mut birth, &mut exit, &mut kernel, &mut user) } == 0 {
        return Err(failure("GetProcessTimes"));
    }
    Ok((u64::from(birth.dwHighDateTime) << 32) | u64::from(birth.dwLowDateTime))
}

fn process_image(handle: HANDLE) -> Result<String> {
    let mut buffer = [0u16; 32_768];
    let mut length = buffer.len() as u32;
    if unsafe { QueryFullProcessImageNameW(handle, 0, buffer.as_mut_ptr(), &mut length) } == 0 {
        return Err(failure("QueryFullProcessImageNameW"));
    }
    Ok(String::from_utf16_lossy(&buffer[..length as usize]))
}

fn parent(pid: u32) -> Result<u32> {
    let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) };
    if snapshot == INVALID_HANDLE_VALUE {
        return Err(failure("CreateToolhelp32Snapshot"));
    }
    let _snapshot = OwnedHandle(snapshot);
    let mut entry: PROCESSENTRY32W = unsafe { zeroed() };
    entry.dwSize = size_of::<PROCESSENTRY32W>() as u32;
    let mut has_entry = unsafe { Process32FirstW(snapshot, &mut entry) };
    while has_entry != 0 {
        if entry.th32ProcessID == pid {
            return Ok(entry.th32ParentProcessID);
        }
        has_entry = unsafe { Process32NextW(snapshot, &mut entry) };
    }
    Err("could not establish process parent identity".into())
}

fn actual_parent() -> Result<u32> {
    parent(unsafe { GetCurrentProcessId() })
}

pub fn allow_foreground(parent_pid: u32, host_pid: u32) -> Result<()> {
    if parent_pid == 0
        || host_pid == 0
        || host_pid == parent_pid
        || host_pid == unsafe { GetCurrentProcessId() }
        || actual_parent()? != parent_pid
    {
        return Err("foreground authorization requires the helper's real parent and sibling host".into());
    }
    let parent_owner = process(parent_pid)?;
    let host_owner = process(host_pid)?;
    let parent_created = created(parent_owner.0)?;
    if parent(host_pid)? != parent_pid
        || created(host_owner.0)? < parent_created
        || created(unsafe { GetCurrentProcess() })? < parent_created
    {
        return Err("foreground authorization process ownership changed".into());
    }
    let host_image = fs::canonicalize(process_image(host_owner.0)?)
        .map_err(|error| format!("resolve foreground host executable: {error}"))?;
    let own_image = fs::canonicalize(std::env::current_exe().map_err(|error| error.to_string())?)
        .map_err(|error| format!("resolve foreground grantor executable: {error}"))?;
    if !host_image.to_string_lossy().eq_ignore_ascii_case(&own_image.to_string_lossy()) {
        return Err("foreground host executable does not match grantor".into());
    }
    if unsafe { WaitForSingleObject(parent_owner.0, 0) } != WAIT_TIMEOUT
        || unsafe { WaitForSingleObject(host_owner.0, 0) } != WAIT_TIMEOUT
    {
        return Err("foreground authorization requires the active parent and a running host".into());
    }
    let foreground = foreground_window();
    if foreground.is_null() {
        emit(json!({ "event": "foreground_deferred", "parent_pid": parent_pid, "host_pid": host_pid }));
        return Ok(());
    }
    if window_pid(foreground) != parent_pid {
        emit(json!({ "event": "foreground_cancelled", "parent_pid": parent_pid, "host_pid": host_pid }));
        return Ok(());
    }
    if unsafe { AllowSetForegroundWindow(host_pid) } == 0 {
        return Err(failure("Windows 拒绝程序前台授权"));
    }
    emit(json!({ "event": "foreground_allowed", "parent_pid": parent_pid, "host_pid": host_pid }));
    Ok(())
}

fn class_name(window: HWND) -> String {
    let mut buffer = [0u16; 256];
    let length = unsafe { GetClassNameW(window, buffer.as_mut_ptr(), buffer.len() as i32) };
    String::from_utf16_lossy(&buffer[..length.max(0) as usize])
}

fn window_pid(window: HWND) -> u32 {
    let mut pid = 0;
    unsafe {
        GetWindowThreadProcessId(window, &mut pid);
    }
    pid
}

fn process_named(pid: u32, names: &[&str]) -> bool {
    process(pid)
        .and_then(|owner| process_image(owner.0))
        .is_ok_and(|path| {
            path.rsplit(['\\', '/'])
                .next()
                .is_some_and(|name| names.iter().any(|expected| name.eq_ignore_ascii_case(expected)))
        })
}

fn normalize_target(value: &str) -> String {
    value.replace('/', "\\").to_lowercase()
}

fn owned_by(mut window: HWND, owner: HWND) -> bool {
    for _ in 0..32 {
        if window == owner {
            return true;
        }
        if window.is_null() {
            return false;
        }
        window = unsafe { GetWindow(window, GW_OWNER) };
    }
    false
}

fn is_explorer(pid: u32) -> bool {
    process_named(pid, &["explorer.exe"])
}

fn system_popup(window: HWND) -> bool {
    if [
        "NotifyIconOverflowWindow",
        "TopLevelWindowForOverflowXamlIsland",
        "XamlExplorerHostIslandWindow",
        "ControlCenterWindow",
    ]
    .contains(&class_name(window).as_str())
    {
        return true;
    }
    process_named(window_pid(window), &[
        "ShellExperienceHost.exe",
        "ShellHost.exe",
        "StartMenuExperienceHost.exe",
        "TextInputHost.exe",
        "SearchHost.exe",
        "LockApp.exe",
    ])
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Identity {
    handle: u64,
    pid: u32,
    created: u64,
    class: String,
    visible: bool,
}

impl Identity {
    fn same_window(&self, other: &Self) -> bool {
        self.handle == other.handle
            && self.pid == other.pid
            && self.created == other.created
            && self.class == other.class
    }

    fn capture(window: HWND) -> Result<Self> {
        if unsafe { IsWindow(window) } == 0 {
            return Err("invalid window handle".into());
        }
        let pid = window_pid(window);
        Ok(Self {
            handle: window as usize as u64,
            pid,
            created: created(process(pid)?.0)?,
            class: class_name(window),
            visible: unsafe { IsWindowVisible(window) } != 0,
        })
    }

    fn window(&self) -> HWND {
        self.handle as usize as HWND
    }

    fn valid(&self) -> bool {
        let window = self.window();
        (unsafe { IsWindow(window) }) != 0
            && window_pid(window) == self.pid
            && class_name(window) == self.class
            && process(self.pid)
                .and_then(|owner| created(owner.0))
                .is_ok_and(|birth| birth == self.created)
    }
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct RestoreWindow {
    identity: Identity,
    parent: u64,
    style: i64,
    ex_style: i64,
    bounds: Bounds,
    role: WindowRole,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum JournalPhase {
    Prepared,
    Attaching,
    Active,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Journal {
    phase: JournalPhase,
    session: String,
    parent_pid: u32,
    parent_created: u64,
    host_pid: u32,
    host_created: u64,
    shell: Vec<Identity>,
    windows: Vec<RestoreWindow>,
    appbar: Option<RestoreAppBar>,
    desktop_bounds: Bounds,
    regions: Vec<SavedRegion>,
    tray_style: Option<RestoreTrayStyle>,
    tray_placements: Vec<RestoreTrayPlacement>,
}

fn sidecar(path: &Path, suffix: &str) -> PathBuf {
    let mut name = path.as_os_str().to_os_string();
    name.push(suffix);
    PathBuf::from(name)
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let pending = sidecar(
        path,
        &format!(".{}.pending", unsafe { GetCurrentProcessId() }),
    );
    let result = (|| {
        let mut file =
            File::create(&pending).map_err(|error| format!("create journal: {error}"))?;
        file.write_all(bytes)
            .map_err(|error| format!("write journal: {error}"))?;
        file.sync_all()
            .map_err(|error| format!("sync journal: {error}"))?;
        fs::rename(&pending, path).map_err(|error| format!("replace journal: {error}"))
    })();
    if result.is_err() {
        let _ = fs::remove_file(pending);
    }
    result
}

fn read_state(path: &Path, limit: u64) -> Result<Option<Vec<u8>>> {
    let file = match File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("open desktop state: {error}")),
    };
    let metadata = file
        .metadata()
        .map_err(|error| format!("desktop state metadata: {error}"))?;
    if !metadata.is_file() || metadata.len() > limit {
        return Err("desktop state is not a bounded regular file".into());
    }
    let mut bytes = Vec::new();
    file.take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| format!("read desktop state: {error}"))?;
    if bytes.len() as u64 > limit {
        return Err("desktop state exceeds its byte limit".into());
    }
    Ok(Some(bytes))
}

fn read_lease(path: &Path) -> Result<String> {
    let bytes = read_state(&sidecar(path, ".lease"), 256)?.ok_or("desktop lease is missing")?;
    String::from_utf8(bytes).map_err(|_| "desktop lease is not UTF-8".into())
}

fn read_journal(path: &Path) -> Result<Option<Journal>> {
    let Some(bytes) = read_state(path, MAX_JOURNAL_BYTES)? else {
        return Ok(None);
    };
    let journal: Journal = serde_json::from_slice(&bytes)
        .map_err(|error| format!("invalid desktop journal: {error}"))?;
    validate_journal(&journal)?;
    Ok(Some(journal))
}

fn validate_journal(journal: &Journal) -> Result<()> {
    if journal.tray_placements.len() > journal.shell.len() {
        return Err("invalid system tray recovery placement count".into());
    }
    let mut placement_handles = std::collections::HashSet::new();
    for saved in &journal.tray_placements {
        saved.validate(journal)?;
        if !placement_handles.insert(saved.identity.handle) {
            return Err("duplicate system tray recovery placement".into());
        }
    }
    if let Some(style) = &journal.tray_style {
        style.validate(journal)?;
    }
    if journal.windows.is_empty()
        || journal.windows.len() > 32
        || journal.shell.len() > 128
        || journal.regions.len() > 160
    {
        return Err("invalid desktop journal window count".into());
    }
    let mut handles = std::collections::HashSet::new();
    let mut region_handles = std::collections::HashSet::new();
    for saved in &journal.regions {
        saved.validate()?;
        if !region_handles.insert(saved.identity.handle)
            || !journal
                .shell
                .iter()
                .chain(journal.windows.iter().map(|window| &window.identity))
                .any(|identity| identity.same_window(&saved.identity))
        {
            return Err("invalid recovery region ownership".into());
        }
    }
    if let Some(appbar) = &journal.appbar {
        appbar.validate(journal.host_pid, journal.host_created, journal.desktop_bounds)?;
    }
    if !journal.desktop_bounds.valid()
        || journal.shell.iter().any(|entry| {
            !["Shell_TrayWnd", "Shell_SecondaryTrayWnd"].contains(&entry.class.as_str())
        })
    {
        return Err("invalid display-scoped desktop journal".into());
    }
    if journal.session.is_empty()
        || journal.session.len() > 128
        || journal.windows.iter().any(|entry| {
            entry.parent != 0
                || entry.identity.handle == 0
                || !handles.insert(entry.identity.handle)
                || entry.identity.class.is_empty()
                || entry.identity.class.encode_utf16().count() > 255
                || entry.identity.pid != journal.parent_pid
                || entry.identity.created != journal.parent_created
                || !entry.bounds.valid()
                || !(-2_147_483_648..=4_294_967_295).contains(&entry.style)
                || !(-2_147_483_648..=4_294_967_295).contains(&entry.ex_style)
        })
    {
        return Err("invalid desktop journal window ownership".into());
    }
    Ok(())
}

impl Journal {
    fn capture_shell(&mut self, identity: Identity) -> Result<()> {
        let placement = RestoreTrayPlacement::capture(&identity, self.desktop_bounds)?;
        let region = SavedRegion::capture(&identity)?;
        self.tray_placements.push(placement);
        self.regions.push(region);
        self.shell.push(identity);
        Ok(())
    }

    fn restore_region(&self, saved: &SavedRegion) -> Result<()> {
        match self.tray_placements.iter().find(|placement| {
            placement.identity.same_window(&saved.identity)
        }) {
            Some(placement) => saved.restore_resized(placement.bounds),
            None => saved.restore(),
        }
    }

    fn save(&self, path: &Path) -> Result<()> {
        validate_journal(self)?;
        let bytes = serde_json::to_vec(self).map_err(|error| error.to_string())?;
        if bytes.len() as u64 > MAX_JOURNAL_BYTES {
            return Err("desktop recovery journal exceeds byte budget".into());
        }
        atomic_write(path, &bytes)
    }
}

fn require_session(path: &Path, session: &str, restoring: &RestoringSignal) -> Result<()> {
    if restoring.requested()? {
        return Err("desktop session is restoring".into());
    }
    if read_journal(path)?
        .is_some_and(|journal| journal.session == session && restoring.matches(&journal))
    {
        Ok(())
    } else {
        Err("desktop session is missing or changed".into())
    }
}

unsafe extern "system" fn collect_window(window: HWND, data: LPARAM) -> i32 {
    let windows = unsafe { &mut *(data as *mut Vec<HWND>) };
    windows.push(window);
    1
}

fn top_windows() -> Result<Vec<HWND>> {
    let mut windows = Vec::new();
    if unsafe { EnumWindows(Some(collect_window), &mut windows as *mut _ as LPARAM) } == 0 {
        return Err(failure("EnumWindows"));
    }
    Ok(windows)
}

fn child(window: HWND, class: &str) -> HWND {
    unsafe { FindWindowExW(window, null_mut(), wide(class).as_ptr(), null()) }
}

fn window_on_display(window: HWND, display: Bounds) -> Result<bool> {
    let rect = RECT {
        left: display.x,
        top: display.y,
        right: display.x + display.width,
        bottom: display.y + display.height,
    };
    let target = unsafe { MonitorFromRect(&rect, MONITOR_DEFAULTTONULL) };
    if target.is_null() {
        return Err("desktop display is unavailable".into());
    }
    Ok(unsafe { MonitorFromWindow(window, MONITOR_DEFAULTTONEAREST) } == target)
}

fn shell_windows(display: Bounds) -> Result<Vec<Identity>> {
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

fn worker() -> Result<Identity> {
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

fn rectangle(window: HWND) -> Result<Bounds> {
    let mut rect: RECT = unsafe { zeroed() };
    if unsafe { GetWindowRect(window, &mut rect) } == 0 {
        return Err(failure("GetWindowRect"));
    }
    Ok(Bounds {
        x: rect.left,
        y: rect.top,
        width: rect
            .right
            .checked_sub(rect.left)
            .ok_or("invalid window width")?,
        height: rect
            .bottom
            .checked_sub(rect.top)
            .ok_or("invalid window height")?,
    })
}

fn monitor_bounds(requested: Bounds) -> Result<Bounds> {
    let rect = RECT {
        left: requested.x,
        top: requested.y,
        right: requested
            .x
            .checked_add(requested.width)
            .ok_or("invalid desktop right edge")?,
        bottom: requested
            .y
            .checked_add(requested.height)
            .ok_or("invalid desktop bottom edge")?,
    };
    let monitor = unsafe { MonitorFromRect(&rect, MONITOR_DEFAULTTONULL) };
    if monitor.is_null() {
        return Err("desktop bounds do not identify an available monitor".into());
    }
    let mut info: MONITORINFO = unsafe { zeroed() };
    info.cbSize = size_of::<MONITORINFO>() as u32;
    if unsafe { GetMonitorInfoW(monitor, &mut info) } == 0 {
        return Err(failure("GetMonitorInfoW"));
    }
    let bounds = Bounds {
        x: info.rcMonitor.left,
        y: info.rcMonitor.top,
        width: info
            .rcMonitor
            .right
            .checked_sub(info.rcMonitor.left)
            .ok_or("invalid monitor width")?,
        height: info
            .rcMonitor
            .bottom
            .checked_sub(info.rcMonitor.top)
            .ok_or("invalid monitor height")?,
    };
    if !bounds.valid() || !bounds.within_tolerance(requested, 2) {
        return Err("desktop bounds must cover one complete physical monitor".into());
    }
    Ok(bounds)
}

pub fn display_bounds(requested: &[Bounds]) -> Result<Vec<Option<Bounds>>> {
    if unsafe { SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) } == 0 {
        return Err(failure("SetProcessDpiAwarenessContext"));
    }
    Ok(requested
        .iter()
        .map(|bounds| monitor_bounds(*bounds).ok())
        .collect())
}

fn visibility(window: &Identity, visible: bool) -> Result<()> {
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

fn set_long(window: HWND, index: i32, value: isize) -> Result<()> {
    unsafe {
        SetLastError(0);
    }
    if unsafe { SetWindowLongPtrW(window, index, value) } == 0 && unsafe { GetLastError() } != 0 {
        return Err(failure("SetWindowLongPtrW"));
    }
    Ok(())
}

fn set_parent(window: HWND, parent: HWND) -> Result<()> {
    unsafe {
        SetLastError(0);
        SetParent(window, parent);
    }
    if unsafe { GetLastError() } != 0 || unsafe { GetParent(window) } != parent {
        return Err(failure("SetParent"));
    }
    Ok(())
}

fn move_window(window: HWND, parent: HWND, bounds: Bounds) -> Result<()> {
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

fn attach(saved: &RestoreWindow, layers: &DesktopLayers, bounds: Bounds) -> Result<()> {
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

fn redraw_released_display(target: Bounds) -> Result<()> {
    let shell = unsafe { GetShellWindow() };
    if shell.is_null() || !is_explorer(window_pid(shell)) {
        // Explorer 启动后会重建桌面，无现存合成表面需要刷新。
        return Ok(());
    }
    for window in top_windows()? {
        if window_pid(window) != window_pid(shell)
            || !["Progman", "WorkerW"].contains(&class_name(window).as_str())
        {
            continue;
        }
        let mut origin = POINT { x: target.x, y: target.y };
        if unsafe { ScreenToClient(window, &mut origin) } == 0 {
            return Err(failure("released desktop redraw coordinates"));
        }
        let region = RECT {
            left: origin.x,
            top: origin.y,
            right: origin.x + target.width,
            bottom: origin.y + target.height,
        };
        if unsafe {
            RedrawWindow(
                window,
                &region,
                null_mut(),
                RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN,
            )
        } == 0
        {
            return Err(failure("redraw released desktop display"));
        }
    }
    Ok(())
}

fn set_top_level(saved: &RestoreWindow, bounds: Bounds, topmost: bool) -> Result<()> {
    if !saved.identity.valid() {
        return Err("desktop window identity changed".into());
    }
    let window = saved.identity.window();
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

fn restore(path: &Path, expected_session: Option<&str>) -> Result<bool> {
    let _lock = MutexGuard::restoration()?;
    restore_locked(path, expected_session)
}

fn restore_locked(path: &Path, expected_session: Option<&str>) -> Result<bool> {
    let Some(journal) = read_journal(path)? else {
        return Ok(false);
    };
    if expected_session.is_some_and(|session| session != journal.session) {
        return Err("restoration session changed".into());
    }
    let mut failures = Vec::new();
    let mut hosted = false;
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
            let result = set_long(window, GWL_STYLE, saved.style as isize)
                .and_then(|()| set_long(window, GWL_EXSTYLE, saved.ex_style as isize))
                .and_then(|()| set_parent(window, parent))
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
            if let Err(error) = visibility(&saved.identity, saved.identity.visible) {
                failures.push(error);
            }
        }
        hosted = true;
    }
    if hosted {
        if let Err(error) = redraw_released_display(journal.desktop_bounds) {
            failures.push(error);
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

fn restore_interrupted(
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
        let diagnostic = format!("interruption marker write failed: {error}\n");
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
    // 标记失败也必须恢复系统界面。
    restore_locked(path, Some(session))
}

fn finish_failed_start(
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

fn retire_guardian(guardian: &mut Child) -> Result<()> {
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

struct Session {
    journal: Journal,
    layers: DesktopLayers,
    guardian: Child,
    path: PathBuf,
    last_heartbeat: Instant,
    lease_number: u64,
    foreground: Option<(bool, bool, bool)>,
    overlay_geometry_pending: Option<Instant>,
    child_order_pending: Option<Instant>,
    last_work_area: Option<Bounds>,
    workspace: Workspace,
    events: WinEvents,
    applications: Applications,
    takeover: bool,
    last_shell_scan: Instant,
    stopped: bool,
    restoring: RestoringSignal,
    tray: Tray,
    last_tray: Option<tray::SystemTray>,
    tray_style: Option<TrayStyle>,
    palette: Palette,
}

struct InputAttachment {
    source: u32,
    target: u32,
}

impl Drop for InputAttachment {
    fn drop(&mut self) {
        if unsafe { AttachThreadInput(self.source, self.target, 0) } == 0 {
            emit(json!({ "event": "failure", "reason": failure("detach desktop input thread") }));
            // 线程附着不能遗留在循环内；退出自有 host，让独立 guardian 恢复。
            std::process::exit(1);
        }
    }
}

impl Session {
    fn start(
        path: &Path,
        parent_pid: u32,
        takeover: bool,
        palette: Palette,
        specs: &[WindowSpec],
        work_area: Bounds,
    ) -> Result<Self> {
        if actual_parent()? != parent_pid {
            return Err("desktop windows must belong to the helper's real parent process".into());
        }
        let events = WinEvents::start()?;
        if specs.len() != 2 {
            return Err("desktop requires one background and one overlay".into());
        }
        if specs
            .iter()
            .filter(|w| w.role == WindowRole::Overlay)
            .count()
            != 1
            || specs
                .iter()
                .filter(|w| w.role == WindowRole::Background)
                .count()
                != 1
        {
            return Err("desktop requires one background and one overlay".into());
        }
        let overlay = specs
            .iter()
            .find(|spec| spec.role == WindowRole::Overlay)
            .ok_or("desktop overlay is missing")?;
        if specs.iter().any(|spec| spec.bounds != overlay.bounds) {
            return Err("desktop windows must share the interaction display".into());
        }
        if !overlay.bounds.valid() {
            return Err("desktop physical bounds are out of range".into());
        }
        let target = monitor_bounds(overlay.bounds)?;
        let mut workspace = Workspace::prepare(work_area, overlay.bounds, parent_pid, takeover)?;
        if read_journal(path)?.is_some() {
            return Err("an unrecovered desktop journal exists".into());
        }
        let parent = process(parent_pid)?;
        let host_pid = unsafe { GetCurrentProcessId() };
        let host = process(host_pid)?;
        let mut windows = Vec::new();
        for spec in specs {
            if spec.handle.len() > 16
                || spec.handle.is_empty()
                || !spec.handle.bytes().all(|byte| byte.is_ascii_hexdigit())
            {
                return Err("invalid desktop window handle".into());
            }
            let value =
                u64::from_str_radix(&spec.handle, 16).map_err(|_| "invalid desktop handle")?;
            if value == 0
                || value > usize::MAX as u64
                || windows
                    .iter()
                    .any(|entry: &RestoreWindow| entry.identity.handle == value)
            {
                return Err("invalid or duplicate desktop window handle".into());
            }
            let window = value as usize as HWND;
            let identity = Identity::capture(window)?;
            if identity.pid != parent_pid || identity.created != created(parent.0)? {
                return Err("desktop window does not belong to Electron main".into());
            }
            if !unsafe { GetParent(window) }.is_null() {
                return Err("desktop attachment requires an independent top-level window".into());
            }
            windows.push(RestoreWindow {
                identity,
                parent: 0,
                style: unsafe { GetWindowLongPtrW(window, GWL_STYLE) } as i64,
                ex_style: unsafe { GetWindowLongPtrW(window, GWL_EXSTYLE) } as i64,
                bounds: rectangle(window)?,
                role: spec.role,
            });
        }
        worker()?;
        let mut journal = Journal {
            phase: JournalPhase::Prepared,
            session: format!(
                "{host_pid}-{}",
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .map_err(|error| error.to_string())?
                    .as_nanos()
            ),
            parent_pid,
            parent_created: created(parent.0)?,
            host_pid,
            host_created: created(host.0)?,
            shell: Vec::new(),
            windows,
            appbar: workspace.appbar_restore(),
            desktop_bounds: target,
            regions: Vec::new(),
            tray_style: None,
            tray_placements: Vec::new(),
        };
        if takeover {
            for identity in shell_windows(target)? {
                journal.capture_shell(identity)?;
            }
        }
        let mut tray = Tray::new(&journal)?;
        let mut tray_style = TrayStyle::prepare(&journal, palette)?;
        journal.tray_style = tray_style.as_ref().map(TrayStyle::record);
        let restoring = RestoringSignal::new(&journal)?;
        workspace.bind_restoration(restoring.handle.0)?;
        if let Some(directory) = path.parent() {
            fs::create_dir_all(directory)
                .map_err(|error| format!("create desktop journal directory: {error}"))?;
        }
        journal.save(path)?;
        atomic_write(
            &sidecar(path, ".lease"),
            format!("{}:0", journal.session).as_bytes(),
        )?;
        let executable = std::env::current_exe().map_err(|error| error.to_string())?;
        let mut guardian = match ProcessCommand::new(executable)
            .args(["guardian", "--journal"])
            .arg(path)
            .args(["--session", &journal.session])
            .creation_flags(CREATE_NO_WINDOW)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
        {
            Ok(child) => child,
            Err(error) => {
                let reason = format!("start desktop guardian: {error}");
                return Err(match restore(path, Some(&journal.session)) {
                    Ok(_) => reason,
                    Err(rollback) => format!("{reason}; restoration: {rollback}"),
                });
            }
        };
        let output = guardian
            .stdout
            .take()
            .ok_or("guardian stdout unavailable")?;
        let (sender, receiver) = channel();
        thread::spawn(move || {
            let mut line = String::new();
            if BufReader::new(output)
                .take(65_537)
                .read_line(&mut line)
                .is_err()
                || line.len() > 65_536
            {
                line.clear();
            }
            let _ = sender.send(line);
        });
        let ready = receiver
            .recv_timeout(Duration::from_secs(5))
            .ok()
            .and_then(|line| serde_json::from_str::<serde_json::Value>(&line).ok());
        if ready
            .as_ref()
            .and_then(|value| value.get("event"))
            .and_then(|value| value.as_str())
            != Some("guardian_ready")
        {
            let reason = ready
                .and_then(|value| {
                    value
                        .get("reason")
                        .and_then(|value| value.as_str())
                        .map(str::to_owned)
                })
                .unwrap_or_else(|| "desktop guardian did not become ready".into());
            let prepared = match read_journal(path) {
                Ok(None) => true,
                Ok(Some(saved)) => {
                    saved.session == journal.session
                        && matches!(saved.phase, JournalPhase::Prepared)
                }
                Err(_) => false,
            };
            let restored = restore(path, Some(&journal.session));
            return Err(finish_failed_start(
                reason,
                restored,
                &mut guardian,
                prepared,
            ));
        }
        // 挂载和紧急恢复共享锁，防止恢复后又隐藏系统界面。
        let transaction = MutexGuard::restoration()?;
        let attached = (|| {
            require_session(path, &journal.session, &restoring)?;
            workspace.monitor()?;
            monitor_bounds(target)?;
            journal.phase = JournalPhase::Attaching;
            journal.save(path)?;
            let layers = DesktopLayers::discover(takeover)?;
            if !layers.icons.visible || !layers.background_parent().visible {
                return Err("Explorer desktop layer is not visible".into());
            }
            for saved in &journal.windows {
                if saved.role.is_top_level() {
                    set_top_level(saved, target, false)?;
                } else {
                    attach(saved, &layers, target)?;
                }
            }
            if takeover {
                for item in &journal.shell {
                    if !item.valid() || !tray::on_display(item.window(), target)? {
                        return Err("Explorer changed before shell takeover".into());
                    }
                }
                if let Some(style) = &mut tray_style {
                    style.start()?;
                }
                tray.apply(&journal, tray_style.as_ref())?;
                workspace.activate()?;
            }
            layers.order(&journal.windows)?;
            journal.phase = JournalPhase::Active;
            journal.save(path)?;
            Ok(layers)
        })();
        drop(transaction);
        let layers = match attached {
            Ok(layers) => layers,
            Err(error) => {
                let restored = restore(path, Some(&journal.session));
                return Err(finish_failed_start(error, restored, &mut guardian, false));
            }
        };
        let applications = match Applications::new(&journal.session, parent_pid) {
            Ok(applications) => applications,
            Err(error) => {
                let restored = restore(path, Some(&journal.session));
                return Err(finish_failed_start(error, restored, &mut guardian, false));
            }
        };
        emit(json!({ "event": "system_tray", "tray": tray.state }));
        let last_tray = tray.state;
        Ok(Self {
            journal,
            layers,
            guardian,
            path: path.into(),
            last_heartbeat: Instant::now(),
            lease_number: 0,
            foreground: None,
            overlay_geometry_pending: None,
            child_order_pending: None,
            last_work_area: None,
            workspace,
            events,
            applications,
            takeover,
            last_shell_scan: Instant::now(),
            stopped: false,
            restoring,
            tray,
            last_tray,
            tray_style,
            palette,
        })
    }

    fn set_palette(&mut self, palette: Palette) -> Result<()> {
        let _transaction = MutexGuard::restoration()?;
        require_session(&self.path, &self.journal.session, &self.restoring)?;
        self.palette = palette;
        if let Some(style) = &mut self.tray_style {
            style.heartbeat(palette)?;
        }
        Ok(())
    }

    fn set_tray_layout(&mut self, layout: Option<TrayLayout>) -> Result<()> {
        let _transaction = MutexGuard::restoration()?;
        require_session(&self.path, &self.journal.session, &self.restoring)?;
        if !self.takeover {
            return if layout.is_none() { Ok(()) } else { Err("native system tray is not enabled".into()) };
        }
        self.tray.set_layout(layout, self.journal.desktop_bounds)?;
        self.tray.apply(&self.journal, self.tray_style.as_ref())?;
        self.publish_tray();
        Ok(())
    }

    fn publish_tray(&mut self) {
        if self.last_tray != self.tray.state {
            emit(json!({ "event": "system_tray", "tray": self.tray.state }));
            self.last_tray = self.tray.state;
        }
    }

    fn heartbeat(&mut self) -> Result<()> {
        let _transaction = MutexGuard::restoration()?;
        require_session(&self.path, &self.journal.session, &self.restoring)?;
        self.lease_number += 1;
        atomic_write(
            &sidecar(&self.path, ".lease"),
            format!("{}:{}", self.journal.session, self.lease_number).as_bytes(),
        )?;
        self.last_heartbeat = Instant::now();
        Ok(())
    }

    fn refresh_applications(&mut self, force: bool) -> Result<()> {
        let events = self.events.drain();
        let shell_changed = events.iter().any(|(event, handle)| {
            *event == 0
                || self.journal.shell.iter().any(|identity| identity.handle == *handle as u64)
                || ["TrayNotifyWnd", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"]
                    .contains(&class_name(*handle as HWND).as_str())
        });
        {
            let _transaction = MutexGuard::restoration()?;
            require_session(&self.path, &self.journal.session, &self.restoring)?;
            self.require_display()?;
            if self.takeover && shell_changed {
                self.refresh_shell()?;
            }
            let changed = self.layers.order(&self.journal.windows)?;
            let verification = self.layers.verify(&self.journal.windows, self.journal.desktop_bounds);
            if changed || verification.is_err() {
                if self
                    .child_order_pending
                    .get_or_insert_with(Instant::now)
                    .elapsed()
                    >= DESKTOP_LAYER_TIMEOUT
                {
                    return Err(verification.err().unwrap_or_else(|| {
                        "desktop child window ordering did not settle".into()
                    }));
                }
            } else {
                self.child_order_pending = None;
            }
            self.workspace.poll(&events)?;
            let area = self.workspace.current();
            if self.last_work_area != Some(area) {
                emit(json!({ "event": "work_area", "work_area": area }));
                self.last_work_area = Some(area);
            }
            self.applications.poll(&events, force, &self.workspace);
        }
        Ok(())
    }

    fn require_display(&self) -> Result<()> {
        self.workspace.monitor()?;
        monitor_bounds(self.journal.desktop_bounds)?;
        Ok(())
    }

    fn activate_external(&mut self, window_id: &str, allow_remote: bool) -> Result<String> {
        self.refresh_applications(false)?;
        let _transaction = MutexGuard::restoration()?;
        require_session(&self.path, &self.journal.session, &self.restoring)?;
        self.require_external_interaction()?;
        self.applications.activate(window_id, allow_remote, &self.workspace)
    }

    fn close_external(&mut self, window_ids: &[String]) -> Result<()> {
        self.refresh_applications(false)?;
        let _transaction = MutexGuard::restoration()?;
        require_session(&self.path, &self.journal.session, &self.restoring)?;
        self.require_external_interaction()?;
        self.applications.close_windows(window_ids, &self.workspace)
    }

    fn get_jump_list_tasks(&self, target: &str) -> Result<jump_lists::JumpListRead> {
        require_session(&self.path, &self.journal.session, &self.restoring)?;
        let read = jump_lists::read_tasks(target, &self.journal.session);
        require_session(&self.path, &self.journal.session, &self.restoring)?;
        Ok(read)
    }

    fn activate_jump_list_task(
        &mut self,
        target: &str,
        task_id: &str,
        expires_at: u64,
    ) -> Result<Vec<String>> {
        let read = self.get_jump_list_tasks(target)?;
        let task = read
            .tasks
            .iter()
            .find(|task| task.id == task_id)
            .ok_or("Jump List 任务已失效，请重新打开菜单。")?;
        let _transaction = MutexGuard::restoration()?;
        require_session(&self.path, &self.journal.session, &self.restoring)?;
        self.require_external_interaction()?;
        if SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|error| error.to_string())?
            .as_millis()
            >= u128::from(expires_at)
        {
            return Err("Jump List 任务请求已超时，请重新打开菜单。".into());
        }
        self.applications.launch_jump_list(task, target, &self.workspace)?;
        Ok(read.diagnostics)
    }

    fn launch_external(&mut self, target: &str, app_id: &str) -> Result<()> {
        self.refresh_applications(false)?;
        let _transaction = MutexGuard::restoration()?;
        require_session(&self.path, &self.journal.session, &self.restoring)?;
        self.require_external_interaction()?;
        self.applications.launch_external(target, app_id, &self.workspace)
    }

    fn shared_desktop_foreground(&self, foreground: HWND) -> bool {
        if foreground.is_null()
            || window_pid(foreground) != self.layers.icons.pid
            || !["Progman", "WorkerW", "SHELLDLL_DefView", "SysListView32"]
                .contains(&class_name(foreground).as_str())
        {
            return false;
        }
        // Explorer 的前台 HWND 跨显示器共享；鼠标所在屏决定当前操作属于哪个桌面。
        let mut point: POINT = unsafe { zeroed() };
        if unsafe { GetCursorPos(&mut point) } == 0 {
            return false;
        }
        let target = self.journal.desktop_bounds;
        let rect = RECT {
            left: target.x,
            top: target.y,
            right: target.x + target.width,
            bottom: target.y + target.height,
        };
        let display = unsafe { MonitorFromRect(&rect, MONITOR_DEFAULTTONULL) };
        !display.is_null()
            && unsafe { MonitorFromPoint(point, MONITOR_DEFAULTTONULL) } == display
    }

    fn require_external_interaction(&self) -> Result<()> {
        let foreground = unsafe { GetForegroundWindow() };
        if !self.takeover
            || (!self.shared_desktop_foreground(foreground)
                && !self.journal.windows.iter().any(|saved| {
                    saved.identity.valid()
                        && (foreground == saved.identity.window()
                            || unsafe { GetAncestor(foreground, GA_ROOTOWNER) }
                                == saved.identity.window())
                }))
        {
            return Err("外部窗口操作需要当前桌面交互。".into());
        }
        for key in [
            VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN, VK_LBUTTON, VK_RBUTTON, VK_MBUTTON,
        ] {
            if unsafe { GetAsyncKeyState(i32::from(key)) } < 0 {
                return Err("请释放按键后重新操作窗口。".into());
            }
        }
        Ok(())
    }

    fn focus(&self, handle: &str) -> Result<()> {
        if handle.is_empty()
            || handle.len() > 16
            || !handle.bytes().all(|byte| byte.is_ascii_hexdigit())
        {
            return Err("invalid desktop focus handle".into());
        }
        let value = u64::from_str_radix(handle, 16).map_err(|_| "invalid desktop focus handle")?;
        let saved = self
            .journal
            .windows
            .iter()
            .find(|saved| saved.role == WindowRole::Overlay && saved.identity.handle == value)
            .ok_or("desktop focus target is not registered in this session")?;
        let window = saved.identity.window();
        let validate = || -> Result<()> {
            require_session(&self.path, &self.journal.session, &self.restoring)?;
            if !self.takeover
                || !saved.identity.valid()
                || !self.layers.valid()
                || !unsafe { GetParent(window) }.is_null()
                || unsafe { IsWindowVisible(window) } == 0
            {
                return Err("desktop focus target is no longer an active stage".into());
            }
            let foreground = unsafe { GetForegroundWindow() };
            if !self.shared_desktop_foreground(foreground)
                && !self.journal.windows.iter().any(|registered| {
                    foreground == registered.identity.window()
                        || unsafe { IsChild(registered.identity.window(), foreground) } != 0
                })
            {
                return Err("desktop focus requires the desktop to be foreground".into());
            }
            Ok(())
        };
        let _transaction = MutexGuard::restoration()?;
        validate()?;
        if unsafe {
            SetWindowPos(
                window,
                HWND_TOP,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        } == 0
        {
            return Err(failure("bring desktop interface forward"));
        }
        let foreground = unsafe { GetForegroundWindow() };
        let foreground_thread = unsafe { GetWindowThreadProcessId(foreground, null_mut()) };
        let mut info: GUITHREADINFO = unsafe { zeroed() };
        info.cbSize = size_of::<GUITHREADINFO>() as u32;
        if unsafe { GetGUIThreadInfo(foreground_thread, &mut info) } == 0 {
            return Err(failure("desktop focus query"));
        }
        if (info.hwndFocus == window || unsafe { IsChild(window, info.hwndFocus) } != 0)
            && foreground == window
        {
            return Ok(());
        }
        let keys_up = || -> Result<()> {
            for key in [
                VK_CONTROL,
                VK_MENU,
                VK_SHIFT,
                VK_LWIN,
                VK_RWIN,
                VK_LBUTTON,
                VK_RBUTTON,
                VK_MBUTTON,
                VK_XBUTTON1,
                VK_XBUTTON2,
            ] {
                if unsafe { GetAsyncKeyState(i32::from(key)) } < 0 {
                    return Err(
                        "desktop focus cannot change while modifiers or mouse buttons are held"
                            .into(),
                    );
                }
            }
            Ok(())
        };
        keys_up()?;
        let source = unsafe { GetCurrentThreadId() };
        let target = unsafe { GetWindowThreadProcessId(window, null_mut()) };
        if target == 0 || target == source {
            return Err("desktop focus target thread is invalid".into());
        }
        let mut message: MSG = unsafe { zeroed() };
        unsafe {
            PeekMessageW(&mut message, null_mut(), 0, 0, PM_NOREMOVE);
        }
        validate()?;
        keys_up()?;
        if unsafe { AttachThreadInput(source, target, 1) } == 0 {
            return Err(failure("attach desktop input thread"));
        }
        let attached = InputAttachment { source, target };
        let focused = (|| {
            validate()?;
            keys_up()?;
            unsafe {
                SetLastError(0);
                if SetForegroundWindow(window) == 0 {
                    return Err(failure("activate desktop interface"));
                }
                SetFocus(window);
            }
            let focus = unsafe { GetFocus() };
            if focus != window && unsafe { IsChild(window, focus) } == 0 {
                return Err(failure("set desktop input focus"));
            }
            Ok(())
        })();
        drop(attached);
        focused?;
        validate()?;
        if unsafe { GetForegroundWindow() } != window {
            return Err("desktop interface did not become foreground".into());
        }
        let active_thread = unsafe { GetWindowThreadProcessId(GetForegroundWindow(), null_mut()) };
        if unsafe { GetGUIThreadInfo(active_thread, &mut info) } == 0 {
            return Err(failure("desktop focus verification"));
        }
        if info.hwndFocus != window && unsafe { IsChild(window, info.hwndFocus) } == 0 {
            return Err("desktop input focus was lost after detaching helper thread".into());
        }
        Ok(())
    }

    fn poll(&mut self) -> Result<()> {
        if self.restoring.requested()? {
            return Err("desktop restoration has started".into());
        }
        if !self.path.exists() {
            return Err("desktop restored by guardian or emergency shortcut".into());
        }
        if self.last_heartbeat.elapsed() > LEASE_DURATION {
            return Err("desktop main process heartbeat expired".into());
        }
        if self
            .guardian
            .try_wait()
            .map_err(|error| error.to_string())?
            .is_some()
        {
            return Err("desktop guardian exited".into());
        }
        if self
            .journal
            .windows
            .iter()
            .any(|saved| !saved.identity.valid())
        {
            return Err("desktop window was destroyed".into());
        }
        self.require_display()?;
        if !self.layers.valid() {
            let _transaction = MutexGuard::restoration()?;
            require_session(&self.path, &self.journal.session, &self.restoring)?;
            let replacement = DesktopLayers::discover(self.takeover)?;
            for saved in &self.journal.windows {
                if !saved.role.is_top_level() {
                    attach(saved, &replacement, self.journal.desktop_bounds)?;
                }
            }
            self.refresh_shell()?;
            self.layers = replacement;
        }
        self.refresh_applications(false)?;
        if self.takeover && self.last_shell_scan.elapsed() >= Duration::from_secs(1) {
            let _transaction = MutexGuard::restoration()?;
            require_session(&self.path, &self.journal.session, &self.restoring)?;
            self.refresh_shell()?;
        }
        let foreground = unsafe { GetForegroundWindow() };
        let desktop_active = (self.takeover && self.shared_desktop_foreground(foreground))
            || self.journal.windows.iter().any(|saved| {
                saved.role != WindowRole::Overlay
                    && (foreground == saved.identity.window()
                        || unsafe { IsChild(saved.identity.window(), foreground) } != 0)
            });
        let active = self.journal.windows.iter().any(|saved| {
            saved.role == WindowRole::Overlay
                && (foreground == saved.identity.window()
                    || unsafe { GetAncestor(foreground, GA_ROOTOWNER) } == saved.identity.window())
        });
        let fullscreen = self.workspace.fullscreen(foreground);
        if self.takeover && self.tray.suspend(fullscreen) {
            let _transaction = MutexGuard::restoration()?;
            require_session(&self.path, &self.journal.session, &self.restoring)?;
            self.tray.apply(&self.journal, self.tray_style.as_ref())?;
            self.publish_tray();
        }
        let stage_available = !fullscreen && (desktop_active || active);
        let state = (active && !fullscreen, stage_available, fullscreen);
        // 显示与位置消息会交错；跨轮复验异步校正的结果，沿用显示器的两像素 DPI 容差。
        let mut overlays_changed = self.foreground.map(|value| value.2) != Some(fullscreen);
        let target = self.journal.desktop_bounds;
        let pending = &mut self.overlay_geometry_pending;
        for saved in &self.journal.windows {
            if !saved.role.is_top_level() {
                continue;
            }
            overlays_changed |=
                (unsafe { IsWindowVisible(saved.identity.window()) } != 0) == fullscreen;
            if fullscreen || unsafe { IsIconic(saved.identity.window()) } != 0 {
                *pending = None;
                continue;
            }
            let actual = rectangle(saved.identity.window())?;
            if actual.within_tolerance(target, 2) {
                *pending = None;
            } else {
                if pending.get_or_insert_with(Instant::now).elapsed() >= OVERLAY_GEOMETRY_TIMEOUT {
                    return Err(format!(
                        "desktop overlay geometry did not settle: actual {actual:?}, expected {target:?}"
                    ));
                }
                overlays_changed = true;
            }
        }
        if overlays_changed {
            let _transaction = MutexGuard::restoration()?;
            require_session(&self.path, &self.journal.session, &self.restoring)?;
            for saved in &self.journal.windows {
                if saved.role.is_top_level() {
                    visibility(&saved.identity, !fullscreen)?;
                    if !fullscreen
                        && unsafe {
                            SetWindowPos(
                                saved.identity.window(),
                                null_mut(),
                                target.x,
                                target.y,
                                target.width,
                                target.height,
                                SWP_NOACTIVATE | SWP_NOZORDER | SWP_ASYNCWINDOWPOS,
                            )
                        } == 0
                    {
                        return Err(failure("restore desktop overlay geometry"));
                    }
                }
            }
        }
        if self.foreground != Some(state) {
            self.foreground = Some(state);
            emit(
                json!({ "event": "foreground", "active": state.0, "stage_available": state.1, "fullscreen": state.2 }),
            );
        }
        if let Some(style) = &mut self.tray_style {
            style.heartbeat(self.palette)?;
        }
        Ok(())
    }

    fn cancel_operations(&mut self) {
        self.applications.cancel_operations();
        self.workspace.cancel_operations();
    }

    fn stop(&mut self) -> Result<()> {
        if self.stopped {
            return Ok(());
        }
        self.cancel_operations();
        restore(&self.path, Some(&self.journal.session))?;
        retire_guardian(&mut self.guardian)?;
        self.stopped = true;
        Ok(())
    }

    fn refresh_shell(&mut self) -> Result<()> {
        if !self.takeover {
            return Ok(());
        }
        self.require_display()?;
        let original_size = self.journal.shell.len();
        self.journal.shell.retain(Identity::valid);
        self.journal.regions.retain(|saved| saved.identity.valid());
        self.journal.tray_placements.retain(|saved| saved.identity.valid());
        let mut changed = self.journal.shell.len() != original_size;
        let mut released = Vec::new();
        for item in &self.journal.shell {
            let saved_placement = self.journal.tray_placements.iter().find(|saved| saved.identity.handle == item.handle);
            if !saved_placement.is_some_and(|saved| saved.monitor == self.journal.desktop_bounds)
                && !tray::on_display(item.window(), self.journal.desktop_bounds)?
            {
                if let Some(saved) = saved_placement {
                    saved.restore()?;
                }
                if let Some(saved) = self
                    .journal
                    .regions
                    .iter()
                    .find(|saved| saved.identity.handle == item.handle)
                {
                    self.journal.restore_region(saved)?;
                }
                visibility(item, item.visible)?;
                released.push(item.handle);
            }
        }
        if !released.is_empty() {
            self.journal.shell.retain(|item| !released.contains(&item.handle));
            self.journal
                .regions
                .retain(|saved| !released.contains(&saved.identity.handle));
            self.journal.tray_placements.retain(|saved| !released.contains(&saved.identity.handle));
            changed = true;
        }
        for item in shell_windows(self.journal.desktop_bounds)? {
            if !self.journal.shell.iter().any(|saved| saved.same_window(&item)) {
                self.journal.capture_shell(item)?;
                changed = true;
            }
        }
        let style_matches = self.tray_style.as_ref().map_or(self.journal.shell.is_empty(),
            |style| style.matches_target(&self.journal.shell));
        if !style_matches {
            if let Some(record) = &self.journal.tray_style {
                record.restore()?;
            }
            self.tray_style = None;
            self.tray_style = TrayStyle::prepare(&self.journal, self.palette)?;
            self.journal.tray_style = self.tray_style.as_ref().map(TrayStyle::record);
            changed = true;
        }
        if changed {
            self.journal.save(&self.path)?;
        }
        if !style_matches {
            if let Some(style) = &mut self.tray_style {
                style.begin()?;
            }
        }
        self.tray.refresh(&self.journal)?;
        self.tray.apply(&self.journal, self.tray_style.as_ref())?;
        self.publish_tray();
        self.last_shell_scan = Instant::now();
        Ok(())
    }
}

impl Drop for Session {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

fn warn_jump_list(diagnostics: &[String]) {
    for diagnostic in diagnostics {
        emit(json!({ "event": "warning", "notify": false, "reason": format!("Jump List: {diagnostic}") }));
    }
}

fn execute_command(
    path: &Path,
    session: &mut Option<Session>,
    command: Command,
) -> Result<serde_json::Value> {
    match (command, session.as_mut()) {
        (Command::Start { parent_pid, takeover, palette, windows, work_area, .. }, current) => {
            if current.is_some() {
                return Err("desktop host is already running".into());
            }
            *session = Some(Session::start(path, parent_pid, takeover, palette, &windows, work_area)?);
            Ok(json!(null))
        }
        (Command::Stop { .. }, active) => active.map_or(Ok(()), Session::stop).map(|_| json!(null)),
        (Command::CancelExternalInteraction { .. }, active) => {
            if let Some(active) = active {
                active.applications.cancel_interaction();
            }
            Ok(json!(null))
        }
        (_, None) => Err("desktop is not running".into()),
        (Command::Heartbeat { .. }, Some(active)) => active.heartbeat().map(|_| json!(null)),
        (Command::SetPalette { palette, .. }, Some(active)) => active.set_palette(palette).map(|_| json!(null)),
        (Command::SetTrayLayout { layout, .. }, Some(active)) => active.set_tray_layout(layout).map(|_| json!(null)),
        (Command::Focus { handle, .. }, Some(active)) => active.focus(&handle).map(|_| json!(null)),
        (Command::RefreshApplications { .. }, Some(active)) => active
            .refresh_applications(true)
            .map(|_| json!(null)),
        (Command::ActivateExternal { window_id, allow_remote, .. }, Some(active)) => active
            .activate_external(&window_id, allow_remote)
            .map(|activation_id| json!({ "activation_id": activation_id })),
        (Command::AuthorizeExternalFocus { activation_id, granted, .. }, Some(active)) => {
            require_session(&active.path, &active.journal.session, &active.restoring)?;
            active.applications.authorize_focus(&activation_id, granted, &active.workspace)
                .map(|status| json!({ "status": status.as_str() }))
        }
        (Command::LaunchExternal { target, app_id, .. }, Some(active)) => active
            .launch_external(&target, &app_id)
            .map(|_| json!(null)),
        (Command::CloseExternal { window_ids, .. }, Some(active)) => active
            .close_external(&window_ids)
            .map(|_| json!(null)),
        (Command::GetJumpListTasks { target, .. }, Some(active)) => {
            let read = active.get_jump_list_tasks(&target)?;
            warn_jump_list(&read.diagnostics);
            Ok(json!({ "tasks": read.tasks }))
        }
        (Command::ActivateJumpListTask { target, task_id, expires_at, .. }, Some(active)) => {
            let diagnostics = active.activate_jump_list_task(&target, &task_id, expires_at)?;
            warn_jump_list(&diagnostics);
            Ok(json!(null))
        }
    }
}

fn interrupt_session(active: Option<&mut Session>, marker: &str, reason: String) -> String {
    let Some(active) = active else {
        return reason;
    };
    active.cancel_operations();
    match restore_interrupted(&active.path, &active.journal.session, marker, &active.restoring) {
        Ok(_) => reason,
        Err(error) => format!("{reason}; restoration: {error}"),
    }
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
