use crate::{Bounds, Command, Result, WindowRole, WindowSpec, commands, emit};
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
    GetMonitorInfoW, MONITOR_DEFAULTTONULL, MONITORINFO, MonitorFromRect, ScreenToClient,
};
use windows_sys::Win32::System::Diagnostics::ToolHelp::*;
use windows_sys::Win32::System::Registry::{
    HKEY, HKEY_CURRENT_USER, KEY_READ, RegCloseKey, RegEnumKeyExW, RegOpenKeyExW, RegQueryValueExW,
};
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
use workspace::{RestoreWorkArea, Workspace};
mod events;
use events::WinEvents;
mod applications;
use applications::Applications;

fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(Some(0)).collect()
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
    let mut image = [0u16; 32_768];
    let mut length = image.len() as u32;
    if unsafe { QueryFullProcessImageNameW(host.0, 0, image.as_mut_ptr(), &mut length) } == 0 {
        return Err(failure("restoration host executable identity"));
    }
    let image = fs::canonicalize(String::from_utf16_lossy(&image[..length as usize]))
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

fn actual_parent() -> Result<u32> {
    let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) };
    if snapshot == INVALID_HANDLE_VALUE {
        return Err(failure("CreateToolhelp32Snapshot"));
    }
    let _snapshot = OwnedHandle(snapshot);
    let mut entry: PROCESSENTRY32W = unsafe { zeroed() };
    entry.dwSize = size_of::<PROCESSENTRY32W>() as u32;
    let pid = unsafe { GetCurrentProcessId() };
    let mut has_entry = unsafe { Process32FirstW(snapshot, &mut entry) };
    while has_entry != 0 {
        if entry.th32ProcessID == pid {
            return Ok(entry.th32ParentProcessID);
        }
        has_entry = unsafe { Process32NextW(snapshot, &mut entry) };
    }
    Err("could not establish helper parent identity".into())
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

fn is_explorer(pid: u32) -> bool {
    let Ok(owner) = process(pid) else {
        return false;
    };
    let mut path = [0u16; 32_768];
    let mut length = path.len() as u32;
    (unsafe { QueryFullProcessImageNameW(owner.0, 0, path.as_mut_ptr(), &mut length) }) != 0
        && String::from_utf16_lossy(&path[..length as usize])
            .rsplit(['\\', '/'])
            .next()
            .is_some_and(|name| name.eq_ignore_ascii_case("explorer.exe"))
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
    #[serde(default)]
    role: WindowRole,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum JournalPhase {
    Prepared,
    Attaching,
    Active,
}

impl Default for JournalPhase {
    fn default() -> Self {
        // 旧记录可能已经隐藏系统界面，不能按尚未改动处理。
        Self::Attaching
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Journal {
    version: u32,
    #[serde(default)]
    phase: JournalPhase,
    session: String,
    parent_pid: u32,
    parent_created: u64,
    host_pid: u32,
    host_created: u64,
    shell: Vec<Identity>,
    windows: Vec<RestoreWindow>,
    #[serde(default)]
    work_area: Option<RestoreWorkArea>,
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
    if ![1, 2].contains(&journal.version)
        || journal.windows.is_empty()
        || journal.windows.len() > 32
        || journal.shell.len() > 128
    {
        return Err("unsupported desktop journal".into());
    }
    let mut handles = std::collections::HashSet::new();
    if let Some(area) = &journal.work_area {
        area.validate()?;
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
    fn save(&self, path: &Path) -> Result<()> {
        validate_journal(self)?;
        atomic_write(
            path,
            &serde_json::to_vec(self).map_err(|error| error.to_string())?,
        )
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

fn slideshow_wallpaper() -> bool {
    std::env::var_os("APPDATA")
        .map(|root| Path::new(&root).join(r"Microsoft\Windows\Themes\slideshow.ini"))
        .and_then(|config| fs::read_to_string(config).ok())
        .is_some_and(|content| !content.trim().is_empty())
}

fn per_monitor_wallpaper() -> bool {
    let mut settings: HKEY = null_mut();
    if unsafe {
        RegOpenKeyExW(
            HKEY_CURRENT_USER,
            wide(r"Control Panel\Desktop\PerMonitorSettings").as_ptr(),
            0,
            KEY_READ,
            &mut settings,
        )
    } != 0
    {
        return false;
    }
    let _close = OwnedKey(settings);
    let mut index = 0u32;
    loop {
        let mut name = [0u16; 256];
        let mut length = name.len() as u32;
        let result = unsafe {
            RegEnumKeyExW(
                settings,
                index,
                name.as_mut_ptr(),
                &mut length,
                null(),
                null_mut(),
                null_mut(),
                null_mut(),
            )
        };
        if result == ERROR_NO_MORE_ITEMS {
            return false;
        }
        if result != 0 {
            return true;
        }
        let mut monitor = null_mut();
        let subkey = String::from_utf16_lossy(&name[..length as usize]);
        if unsafe { RegOpenKeyExW(settings, wide(&subkey).as_ptr(), 0, KEY_READ, &mut monitor) }
            == 0
        {
            let _close = OwnedKey(monitor);
            let mut value = [0u16; 256];
            let mut size = (value.len() * 2) as u32;
            if unsafe {
                RegQueryValueExW(
                    monitor,
                    wide("WallpaperSRC").as_ptr(),
                    null(),
                    null_mut(),
                    value.as_mut_ptr().cast(),
                    &mut size,
                )
            } == 0
            {
                return true;
            }
        }
        index += 1;
    }
}

struct OwnedKey(HKEY);

impl Drop for OwnedKey {
    fn drop(&mut self) {
        unsafe {
            RegCloseKey(self.0);
        }
    }
}

fn refresh_wallpaper() {
    // 全屏子窗长期遮挡会令 DWM 逐出壁纸合成表面，摘除后偶尔不重建而留黑桌面；
    // 重应用同一壁纸可促 Explorer/DWM 重建该表面。逐显示器或幻灯片壁纸无法用
    // SPI 无损刷新（会退化为单张静态壁纸），保留系统自身重绘。
    const MAX_WALLPAPER: usize = 260;
    let mut source = [0u16; MAX_WALLPAPER];
    if unsafe {
        SystemParametersInfoW(
            SPI_GETDESKWALLPAPER,
            MAX_WALLPAPER as u32,
            source.as_mut_ptr().cast(),
            0,
        )
    } == 0
    {
        return;
    }
    let end = source
        .iter()
        .position(|&unit| unit == 0)
        .unwrap_or(MAX_WALLPAPER);
    let path = PathBuf::from(String::from_utf16_lossy(&source[..end]));
    if slideshow_wallpaper() || per_monitor_wallpaper() || !path.is_absolute() || !path.is_file() {
        return;
    }
    unsafe {
        SystemParametersInfoW(
            SPI_SETDESKWALLPAPER,
            0,
            source.as_mut_ptr().cast(),
            SPIF_UPDATEINIFILE | SPIF_SENDCHANGE,
        );
    }
}

fn shell_windows() -> Result<Vec<Identity>> {
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
        if ["Shell_TrayWnd", "Shell_SecondaryTrayWnd"].contains(&class_name(window).as_str()) {
            captured.push(Identity::capture(window)?);
        }
        let view = child(window, "SHELLDLL_DefView");
        if !view.is_null()
            && window_pid(view) == shell_pid
            && !child(view, "SysListView32").is_null()
        {
            captured.push(Identity::capture(view)?);
        }
    }
    if !captured
        .iter()
        .any(|window| window.class == "SHELLDLL_DefView")
    {
        return Err("Explorer desktop icons could not be identified".into());
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
    let mut result = 0;
    if unsafe { SendMessageTimeoutW(progman, 0x052c, 0, 0, SMTO_ABORTIFHUNG, 2_000, &mut result) }
        == 0
    {
        return Err(failure("Explorer WorkerW request"));
    }
    // 新版背景 WorkerW 带 WS_DISABLED，不能承载交互窗口；自有舞台挂在其 Progman 父窗。
    let candidate = child(progman, "WorkerW");
    if !candidate.is_null()
        && window_pid(candidate) == window_pid(shell)
        && class_name(candidate) == "WorkerW"
        && unsafe { GetParent(candidate) } == progman
        && child(candidate, "SHELLDLL_DefView").is_null()
        && !child(progman, "SHELLDLL_DefView").is_null()
    {
        return Identity::capture(progman);
    }
    for window in top_windows()? {
        if child(window, "SHELLDLL_DefView").is_null() {
            continue;
        }
        let candidate =
            unsafe { FindWindowExW(null_mut(), window, wide("WorkerW").as_ptr(), null()) };
        if !candidate.is_null()
            && window_pid(candidate) == window_pid(shell)
            && child(candidate, "SHELLDLL_DefView").is_null()
        {
            return Identity::capture(candidate);
        }
    }
    Err("Explorer did not expose a usable WorkerW desktop layer".into())
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

fn visibility(window: &Identity, visible: bool) -> Result<()> {
    if !window.valid() {
        return Err("window identity changed before visibility update".into());
    }
    if (unsafe { IsWindowVisible(window.window()) } != 0) == visible {
        return Ok(());
    }
    if unsafe {
        ShowWindowAsync(
            window.window(),
            if visible { SW_SHOWNOACTIVATE } else { SW_HIDE },
        )
    } == 0
    {
        return Err(failure("ShowWindowAsync"));
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

fn attach(saved: &RestoreWindow, worker: &Identity, bounds: Bounds) -> Result<()> {
    if !saved.identity.valid() || !worker.valid() {
        return Err("desktop attachment window identity changed".into());
    }
    let window = saved.identity.window();
    let layer = worker.window();
    let mouse_passthrough =
        unsafe { GetWindowLongPtrW(window, GWL_EXSTYLE) } as u32 & WS_EX_TRANSPARENT;
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
        ((saved.ex_style as u32 & !(WS_EX_APPWINDOW | WS_EX_TOPMOST | WS_EX_TRANSPARENT))
            | mouse_passthrough) as isize,
    )?;
    set_parent(window, layer)?;
    if saved.role == WindowRole::Background {
        // SetParent 会添加兄弟裁剪；整屏透明伙伴会将背景表面裁空，须在挂载后清除。
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

fn order_desktop_children(
    windows: &[RestoreWindow],
    worker: &Identity,
    companion_always_on_top: bool,
    takeover: bool,
) -> Result<bool> {
    let layer = worker.window();
    let mut previous = HWND_TOP;
    if worker.class == "Progman" && !takeover {
        let view = child(layer, "SHELLDLL_DefView");
        if view.is_null() || window_pid(view) != worker.pid || unsafe { GetParent(view) } != layer {
            return Err("Explorer icon view changed before desktop ordering".into());
        }
        previous = view;
    }
    // 接管时自有子窗连续排列在 Explorer 子窗前方，伙伴保持在背景上方。
    let children = windows
        .iter()
        .filter(|saved| saved.role == WindowRole::Companion && !companion_always_on_top)
        .chain(
            windows
                .iter()
                .filter(|saved| saved.role == WindowRole::Background),
        );
    let mut changed = false;
    for saved in children {
        let window = saved.identity.window();
        if unsafe { GetParent(window) } != layer {
            return Err("desktop child window left its Explorer layer".into());
        }
        if changed || unsafe { GetWindow(window, GW_HWNDPREV) } != previous {
            if !saved.identity.valid() || !worker.valid() {
                return Err("desktop child ordering window identity changed".into());
            }
            if unsafe {
                SetWindowPos(
                    window,
                    previous,
                    0,
                    0,
                    0,
                    0,
                    SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE | SWP_ASYNCWINDOWPOS,
                )
            } == 0
            {
                return Err(failure("desktop child window ordering"));
            }
            changed = true;
        }
        previous = window;
    }
    Ok(changed)
}

fn raise_window(saved: &RestoreWindow, bounds: Bounds) -> Result<()> {
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
        ((saved.ex_style as u32 & !(WS_EX_APPWINDOW | WS_EX_TRANSPARENT))
            | WS_EX_TOOLWINDOW
            | mouse_passthrough) as isize,
    )?;
    if unsafe {
        SetWindowPos(
            window,
            HWND_TOPMOST,
            bounds.x,
            bounds.y,
            bounds.width,
            bounds.height,
            SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
    } == 0
    {
        return Err(failure("raise desktop window"));
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
        if let Some(area) = &journal.work_area {
            if let Err(error) = area.restore() {
                failures.push(error);
            }
        }
        // 先恢复系统界面，避免应用窗口复位阻断桌面可用性。
        for item in &journal.shell {
            if !item.valid()
                || !is_explorer(item.pid)
                || ![
                    "SHELLDLL_DefView",
                    "SysListView32",
                    "Shell_TrayWnd",
                    "Shell_SecondaryTrayWnd",
                ]
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
    if !failures.is_empty() {
        return Err(failures.join("; "));
    }
    if hosted {
        refresh_wallpaper();
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
    worker: Identity,
    targets: Vec<Bounds>,
    guardian: Child,
    path: PathBuf,
    last_heartbeat: Instant,
    lease_number: u64,
    foreground: Option<(bool, bool, bool)>,
    overlay_geometry_pending: Vec<Option<Instant>>,
    child_order_pending: Option<Instant>,
    workspace: Workspace,
    events: WinEvents,
    applications: Applications,
    companion_always_on_top: bool,
    takeover: bool,
    last_shell_scan: Instant,
    stopped: bool,
    restoring: RestoringSignal,
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
        specs: &[WindowSpec],
        work_area: Bounds,
        companion_always_on_top: bool,
    ) -> Result<Self> {
        if actual_parent()? != parent_pid {
            return Err("desktop windows must belong to the helper's real parent process".into());
        }
        let events = WinEvents::start()?;
        if specs.is_empty() || specs.len() > 32 {
            return Err("desktop requires between 1 and 32 windows".into());
        }
        if specs
            .iter()
            .filter(|w| w.role == WindowRole::Overlay)
            .count()
            != 1
            || specs
                .iter()
                .filter(|w| w.role == WindowRole::Companion)
                .count()
                != 1
            || !specs.iter().any(|w| w.role == WindowRole::Background)
        {
            return Err("desktop requires backgrounds, one overlay and one companion".into());
        }
        let overlay = specs
            .iter()
            .find(|spec| spec.role == WindowRole::Overlay)
            .ok_or("desktop overlay is missing")?;
        let mut workspace = Workspace::prepare(work_area, overlay.bounds, parent_pid, takeover)?;
        if read_journal(path)?.is_some() {
            return Err("an unrecovered desktop journal exists".into());
        }
        let parent = process(parent_pid)?;
        let host_pid = unsafe { GetCurrentProcessId() };
        let host = process(host_pid)?;
        let mut windows = Vec::new();
        let mut targets = Vec::new();
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
            let bounds = spec.bounds;
            if !bounds.valid() {
                return Err("desktop physical bounds are out of range".into());
            }
            targets.push(monitor_bounds(bounds)?);
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
        let worker = worker()?;
        let mut journal = Journal {
            version: 2,
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
            shell: if takeover {
                shell_windows()?
            } else {
                Vec::new()
            },
            windows,
            work_area: takeover.then(|| workspace.original.clone()),
        };
        let restoring = RestoringSignal::new(&journal)?;
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
            if !worker.visible {
                return Err("Explorer WorkerW is not visible".into());
            }
            journal.phase = JournalPhase::Attaching;
            journal.save(path)?;
            for (saved, target) in journal.windows.iter().zip(&targets) {
                if saved.role != WindowRole::Overlay {
                    if saved.role == WindowRole::Companion && companion_always_on_top {
                        raise_window(saved, *target)?;
                    } else {
                        attach(saved, &worker, *target)?;
                    }
                }
            }
            for (saved, target) in journal.windows.iter().zip(&targets) {
                if saved.role == WindowRole::Overlay {
                    raise_window(saved, *target)?;
                }
            }
            if takeover {
                for item in &journal.shell {
                    if !item.valid() {
                        return Err("Explorer changed before shell takeover".into());
                    }
                    visibility(item, false)?;
                }
                workspace.activate()?;
            }
            order_desktop_children(&journal.windows, &worker, companion_always_on_top, takeover)?;
            journal.phase = JournalPhase::Active;
            journal.save(path)?;
            Ok(())
        })();
        drop(transaction);
        if let Err(error) = attached {
            let restored = restore(path, Some(&journal.session));
            return Err(finish_failed_start(error, restored, &mut guardian, false));
        }
        let overlay_geometry_pending = vec![None; targets.len()];
        let applications = match Applications::new(&journal.session, parent_pid) {
            Ok(applications) => applications,
            Err(error) => {
                let restored = restore(path, Some(&journal.session));
                return Err(finish_failed_start(error, restored, &mut guardian, false));
            }
        };
        Ok(Self {
            journal,
            worker,
            targets,
            guardian,
            path: path.into(),
            last_heartbeat: Instant::now(),
            lease_number: 0,
            foreground: None,
            overlay_geometry_pending,
            child_order_pending: None,
            workspace,
            events,
            applications,
            companion_always_on_top,
            takeover,
            last_shell_scan: Instant::now(),
            stopped: false,
            restoring,
        })
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
        {
            let _transaction = MutexGuard::restoration()?;
            require_session(&self.path, &self.journal.session, &self.restoring)?;
            if order_desktop_children(
                &self.journal.windows,
                &self.worker,
                self.companion_always_on_top,
                self.takeover,
            )? {
                if self
                    .child_order_pending
                    .get_or_insert_with(Instant::now)
                    .elapsed()
                    >= DESKTOP_LAYER_TIMEOUT
                {
                    return Err("desktop child window ordering did not settle".into());
                }
            } else {
                self.child_order_pending = None;
            }
            self.workspace.poll(&events)?;
        }
        self.applications.poll(&events, force);
        Ok(())
    }

    fn activate_external(&mut self, window_id: &str) -> Result<()> {
        self.refresh_applications(false)?;
        let _transaction = MutexGuard::restoration()?;
        require_session(&self.path, &self.journal.session, &self.restoring)?;
        self.require_external_interaction()?;
        self.applications.activate(window_id)
    }

    fn close_external(&mut self, window_ids: &[String]) -> Result<()> {
        self.refresh_applications(false)?;
        let _transaction = MutexGuard::restoration()?;
        require_session(&self.path, &self.journal.session, &self.restoring)?;
        self.require_external_interaction()?;
        self.applications.close_windows(window_ids)
    }

    fn require_external_interaction(&self) -> Result<()> {
        let foreground = unsafe { GetForegroundWindow() };
        if !self.takeover
            || (foreground != self.worker.window()
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

    fn companion_layer(&mut self, always_on_top: bool) -> Result<()> {
        let _transaction = MutexGuard::restoration()?;
        require_session(&self.path, &self.journal.session, &self.restoring)?;
        for (saved, target) in self.journal.windows.iter().zip(&self.targets) {
            if saved.role == WindowRole::Companion {
                if always_on_top {
                    raise_window(saved, *target)?;
                } else {
                    attach(saved, &self.worker, *target)?;
                }
            }
        }
        self.companion_always_on_top = always_on_top;
        self.foreground = None;
        self.poll()
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
            .find(|saved| saved.identity.handle == value)
            .ok_or("desktop focus target is not registered in this session")?;
        let window = saved.identity.window();
        let validate = || -> Result<()> {
            require_session(&self.path, &self.journal.session, &self.restoring)?;
            if !self.takeover
                || !saved.identity.valid()
                || !self.worker.valid()
                || unsafe { GetParent(window) }
                    != if saved.role.is_topmost(self.companion_always_on_top) {
                        null_mut()
                    } else {
                        self.worker.window()
                    }
                || unsafe { IsWindowVisible(window) } == 0
            {
                return Err("desktop focus target is no longer an active stage".into());
            }
            let foreground = unsafe { GetForegroundWindow() };
            if foreground != self.worker.window()
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
        let foreground = unsafe { GetForegroundWindow() };
        let foreground_thread = unsafe { GetWindowThreadProcessId(foreground, null_mut()) };
        let mut info: GUITHREADINFO = unsafe { zeroed() };
        info.cbSize = size_of::<GUITHREADINFO>() as u32;
        if unsafe { GetGUIThreadInfo(foreground_thread, &mut info) } == 0 {
            return Err(failure("desktop focus query"));
        }
        if info.hwndFocus == window || unsafe { IsChild(window, info.hwndFocus) } != 0 {
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
                if saved.role == WindowRole::Overlay && SetForegroundWindow(window) == 0 {
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
        if !self.worker.valid() {
            let replacement = worker()?;
            let _transaction = MutexGuard::restoration()?;
            require_session(&self.path, &self.journal.session, &self.restoring)?;
            for (saved, target) in self.journal.windows.iter().zip(&self.targets) {
                if !saved.role.is_topmost(self.companion_always_on_top) {
                    attach(saved, &replacement, *target)?;
                }
            }
            self.refresh_shell()?;
            self.worker = replacement;
        }
        self.refresh_applications(false)?;
        if self.takeover && self.last_shell_scan.elapsed() >= Duration::from_secs(1) {
            let _transaction = MutexGuard::restoration()?;
            require_session(&self.path, &self.journal.session, &self.restoring)?;
            self.refresh_shell()?;
        }
        let foreground = unsafe { GetForegroundWindow() };
        let desktop_active = (foreground == self.worker.window()
            && (self.takeover || self.worker.class != "Progman"))
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
        let stage_available =
            !fullscreen && (self.companion_always_on_top || desktop_active || active);
        let state = (active && !fullscreen, stage_available, fullscreen);
        // 显示与位置消息会交错；跨轮复验异步校正的结果，沿用显示器的两像素 DPI 容差。
        let mut overlays_changed = self.foreground.map(|value| value.2) != Some(fullscreen);
        for ((saved, target), pending) in self
            .journal
            .windows
            .iter()
            .zip(&self.targets)
            .zip(&mut self.overlay_geometry_pending)
        {
            if !saved.role.is_topmost(self.companion_always_on_top) {
                *pending = None;
                continue;
            }
            overlays_changed |=
                (unsafe { IsWindowVisible(saved.identity.window()) } != 0) == fullscreen;
            if fullscreen || unsafe { IsIconic(saved.identity.window()) } != 0 {
                *pending = None;
                continue;
            }
            let actual = rectangle(saved.identity.window())?;
            if actual.within_tolerance(*target, 2) {
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
            for (saved, target) in self.journal.windows.iter().zip(&self.targets) {
                if saved.role.is_topmost(self.companion_always_on_top) {
                    visibility(&saved.identity, !fullscreen)?;
                    if !fullscreen
                        && unsafe {
                            SetWindowPos(
                                saved.identity.window(),
                                HWND_TOPMOST,
                                target.x,
                                target.y,
                                target.width,
                                target.height,
                                SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS,
                            )
                        } == 0
                    {
                        return Err(failure("restore desktop overlay layer"));
                    }
                }
            }
        }
        if !fullscreen && self.companion_always_on_top {
            let overlay = self
                .journal
                .windows
                .iter()
                .find(|saved| saved.role == WindowRole::Overlay)
                .ok_or("desktop overlay is missing")?
                .identity
                .window();
            let companion = self
                .journal
                .windows
                .iter()
                .find(|saved| saved.role == WindowRole::Companion)
                .ok_or("desktop companion is missing")?
                .identity
                .window();
            if (foreground == companion || unsafe { IsChild(companion, foreground) } != 0)
                && unsafe { GetWindow(companion, GW_HWNDPREV) } != overlay
            {
                let _transaction = MutexGuard::restoration()?;
                require_session(&self.path, &self.journal.session, &self.restoring)?;
                if unsafe {
                    SetWindowPos(
                        companion,
                        overlay,
                        0,
                        0,
                        0,
                        0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
                    )
                } == 0
                {
                    return Err(failure("order desktop companion below interface"));
                }
            }
        }
        if self.foreground != Some(state) {
            self.foreground = Some(state);
            emit(
                json!({ "event": "foreground", "active": state.0, "stage_available": state.1, "fullscreen": state.2 }),
            );
        }
        Ok(())
    }

    fn stop(&mut self) -> Result<()> {
        if self.stopped {
            return Ok(());
        }
        restore(&self.path, Some(&self.journal.session))?;
        retire_guardian(&mut self.guardian)?;
        self.stopped = true;
        Ok(())
    }

    fn refresh_shell(&mut self) -> Result<()> {
        if !self.takeover {
            return Ok(());
        }
        let original_size = self.journal.shell.len();
        self.journal.shell.retain(Identity::valid);
        let mut changed = self.journal.shell.len() != original_size;
        for item in shell_windows()? {
            if !self.journal.shell.iter().any(|saved| {
                saved.handle == item.handle
                    && saved.pid == item.pid
                    && saved.created == item.created
            }) {
                self.journal.shell.push(item);
                changed = true;
            }
        }
        if changed {
            self.journal.save(&self.path)?;
        }
        for item in &self.journal.shell {
            visibility(item, false)?;
        }
        self.last_shell_scan = Instant::now();
        Ok(())
    }
}

impl Drop for Session {
    fn drop(&mut self) {
        let _ = self.stop();
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
        let mut message: MSG = unsafe { zeroed() };
        while unsafe { PeekMessageW(&mut message, null_mut(), 0, 0, PM_REMOVE) } != 0 {
            unsafe {
                TranslateMessage(&message);
                DispatchMessageW(&message);
            }
        }
        match receiver.recv_timeout(Duration::from_millis(200)) {
            Ok(Ok(command)) => {
                let id = command.id();
                let mut exiting = false;
                let result = match command {
                    Command::Start {
                        parent_pid,
                        takeover,
                        windows,
                        work_area,
                        companion_always_on_top,
                        ..
                    } => {
                        if session.is_some() {
                            Err("desktop host is already running".into())
                        } else {
                            Session::start(
                                path,
                                parent_pid,
                                takeover,
                                &windows,
                                work_area,
                                companion_always_on_top,
                            )
                            .map(|started| {
                                session = Some(started);
                            })
                        }
                    }
                    Command::Heartbeat { .. } => session
                        .as_mut()
                        .ok_or_else(|| "desktop is not running".to_owned())
                        .and_then(Session::heartbeat),
                    Command::Focus { handle, .. } => session
                        .as_ref()
                        .ok_or_else(|| "desktop is not running".to_owned())
                        .and_then(|active| active.focus(&handle)),
                    Command::RefreshApplications { .. } => session
                        .as_mut()
                        .ok_or_else(|| "desktop is not running".to_owned())
                        .and_then(|active| active.refresh_applications(true)),
                    Command::ActivateExternal { window_id, .. } => session
                        .as_mut()
                        .ok_or_else(|| "desktop is not running".to_owned())
                        .and_then(|active| active.activate_external(&window_id)),
                    Command::CloseExternal { window_ids, .. } => session
                        .as_mut()
                        .ok_or_else(|| "desktop is not running".to_owned())
                        .and_then(|active| active.close_external(&window_ids)),
                    Command::CompanionLayer { always_on_top, .. } => session
                        .as_mut()
                        .ok_or("desktop host is not running".into())
                        .and_then(|active| active.companion_layer(always_on_top)),
                    Command::Stop { .. } => {
                        exiting = true;
                        session.as_mut().map_or(Ok(()), Session::stop)
                    }
                };
                match result {
                    Ok(()) => emit(json!({ "id": id, "ok": true })),
                    Err(reason) => emit(json!({ "id": id, "ok": false, "reason": reason })),
                }
                if exiting {
                    return Ok(());
                }
            }
            Ok(Err(reason)) => {
                if let Some(active) = session.as_ref() {
                    if let Err(error) = restore_interrupted(
                        path,
                        &active.journal.session,
                        "command_channel_failure",
                        &active.restoring,
                    ) {
                        return Err(format!("{reason}; restoration: {error}"));
                    }
                }
                return Err(reason);
            }
            Err(RecvTimeoutError::Disconnected) => {
                if let Some(active) = session.as_ref() {
                    if let Err(error) = restore_interrupted(
                        path,
                        &active.journal.session,
                        "command_channel_closed",
                        &active.restoring,
                    ) {
                        return Err(format!(
                            "desktop parent closed command channel; restoration: {error}"
                        ));
                    }
                }
                return Err("desktop parent closed command channel".into());
            }
            Err(RecvTimeoutError::Timeout) => {}
        }
        if let Some(active) = session.as_mut() {
            if let Err(reason) = active.poll() {
                return Err(
                    match restore_interrupted(
                        path,
                        &active.journal.session,
                        "host_failure",
                        &active.restoring,
                    ) {
                        Ok(_) => reason,
                        Err(error) => format!("{reason}; restoration: {error}"),
                    },
                );
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
