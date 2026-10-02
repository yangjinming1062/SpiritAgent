use crate::{Bounds, Command, Result, WindowSpec, commands, emit};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::fs::{self, File};
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
use windows_sys::Win32::Graphics::Gdi::ScreenToClient;
use windows_sys::Win32::System::Diagnostics::ToolHelp::*;
use windows_sys::Win32::System::Threading::*;
use windows_sys::Win32::UI::HiDpi::*;
use windows_sys::Win32::UI::Input::KeyboardAndMouse::*;
use windows_sys::Win32::UI::WindowsAndMessaging::*;

const MAX_JOURNAL_BYTES: u64 = 1_048_576;
const LEASE_DURATION: Duration = Duration::from_secs(10);
const HOTKEY_ID: i32 = 0x5341;

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
    if journal.version != 1
        || journal.windows.is_empty()
        || journal.windows.len() > 32
        || journal.shell.len() > 128
    {
        return Err("unsupported desktop journal".into());
    }
    let mut handles = std::collections::HashSet::new();
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

fn require_session(path: &Path, session: &str) -> Result<()> {
    if read_journal(path)?.is_some_and(|journal| journal.session == session) {
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
        if !view.is_null() {
            let icons = child(view, "SysListView32");
            if !icons.is_null() {
                captured.push(Identity::capture(icons)?);
            }
        }
    }
    if !captured
        .iter()
        .any(|window| window.class == "SysListView32")
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
    let style = (saved.style as u32 & !WS_POPUP) | WS_CHILD;
    set_long(window, GWL_STYLE, style as isize)?;
    set_long(
        window,
        GWL_EXSTYLE,
        (saved.ex_style as u32 & !(WS_EX_APPWINDOW | WS_EX_TOPMOST)) as isize,
    )?;
    set_parent(window, layer)?;
    if unsafe { AreDpiAwarenessContextsEqual(original_dpi, GetWindowDpiAwarenessContext(window)) }
        == 0
    {
        return Err("Electron DPI awareness changed after Explorer attachment".into());
    }
    move_window(window, layer, bounds)?;
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
    if !matches!(journal.phase, JournalPhase::Prepared) {
        // 先恢复系统界面，避免应用窗口复位阻断桌面可用性。
        for item in &journal.shell {
            if !item.valid()
                || !is_explorer(item.pid)
                || !["SysListView32", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"]
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
            let parent = null_mut();
            let result = set_parent(window, parent)
                .and_then(|()| set_long(window, GWL_STYLE, saved.style as isize))
                .and_then(|()| set_long(window, GWL_EXSTYLE, saved.ex_style as isize))
                .and_then(|()| move_window(window, parent, saved.bounds));
            if let Err(error) = result {
                failures.push(error);
            }
            if let Err(error) = visibility(&saved.identity, saved.identity.visible) {
                failures.push(error);
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

fn restore_interrupted(path: &Path, session: &str, reason: &str) -> Result<bool> {
    let _lock = MutexGuard::restoration()?;
    let Some(journal) = read_journal(path)? else {
        return Ok(false);
    };
    if journal.session != session {
        return Err("interruption session changed".into());
    }
    if let Err(error) = atomic_write(&sidecar(path, ".interrupted"), reason.as_bytes()) {
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
    foreground: Option<bool>,
    takeover: bool,
    last_shell_scan: Instant,
    stopped: bool,
}

impl Session {
    fn start(path: &Path, parent_pid: u32, takeover: bool, specs: &[WindowSpec]) -> Result<Self> {
        if actual_parent()? != parent_pid {
            return Err("desktop windows must belong to the helper's real parent process".into());
        }
        if specs.is_empty() || specs.len() > 32 {
            return Err("desktop requires between 1 and 32 windows".into());
        }
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
            let bounds = spec.bounds;
            if !bounds.valid() {
                return Err("desktop physical bounds are out of range".into());
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
            });
        }
        let worker = worker()?;
        let mut journal = Journal {
            version: 1,
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
        };
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
            require_session(path, &journal.session)?;
            if !worker.visible {
                return Err("Explorer WorkerW is not visible".into());
            }
            journal.phase = JournalPhase::Attaching;
            journal.save(path)?;
            for (saved, spec) in journal.windows.iter().zip(specs) {
                attach(saved, &worker, spec.bounds)?;
            }
            if takeover {
                for item in &journal.shell {
                    if !item.valid() {
                        return Err("Explorer changed before shell takeover".into());
                    }
                    visibility(item, false)?;
                }
            }
            journal.phase = JournalPhase::Active;
            journal.save(path)?;
            Ok(())
        })();
        drop(transaction);
        if let Err(error) = attached {
            let restored = restore(path, Some(&journal.session));
            return Err(finish_failed_start(error, restored, &mut guardian, false));
        }
        Ok(Self {
            journal,
            worker,
            targets: specs.iter().map(|spec| spec.bounds).collect(),
            guardian,
            path: path.into(),
            last_heartbeat: Instant::now(),
            lease_number: 0,
            foreground: None,
            takeover,
            last_shell_scan: Instant::now(),
            stopped: false,
        })
    }

    fn heartbeat(&mut self) -> Result<()> {
        let _transaction = MutexGuard::restoration()?;
        require_session(&self.path, &self.journal.session)?;
        self.lease_number += 1;
        atomic_write(
            &sidecar(&self.path, ".lease"),
            format!("{}:{}", self.journal.session, self.lease_number).as_bytes(),
        )?;
        self.last_heartbeat = Instant::now();
        self.foreground = None;
        Ok(())
    }

    fn poll(&mut self) -> Result<()> {
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
            require_session(&self.path, &self.journal.session)?;
            for (saved, target) in self.journal.windows.iter().zip(&self.targets) {
                attach(saved, &replacement, *target)?;
            }
            self.refresh_shell()?;
            self.worker = replacement;
        }
        if self.takeover && self.last_shell_scan.elapsed() >= Duration::from_secs(1) {
            let _transaction = MutexGuard::restoration()?;
            require_session(&self.path, &self.journal.session)?;
            self.refresh_shell()?;
        }
        let foreground = unsafe { GetForegroundWindow() };
        let active = foreground == self.worker.window()
            || self.journal.windows.iter().any(|saved| {
                foreground == saved.identity.window()
                    || unsafe { IsChild(saved.identity.window(), foreground) } != 0
            });
        if self.foreground != Some(active) {
            self.foreground = Some(active);
            emit(json!({ "event": "foreground", "active": active }));
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
        match receiver.recv_timeout(Duration::from_millis(200)) {
            Ok(Ok(command)) => {
                let id = command.id();
                let mut exiting = false;
                let result = match command {
                    Command::Start {
                        parent_pid,
                        takeover,
                        windows,
                        ..
                    } => {
                        if session.is_some() {
                            Err("desktop host is already running".into())
                        } else {
                            Session::start(path, parent_pid, takeover, &windows).map(|started| {
                                session = Some(started);
                            })
                        }
                    }
                    Command::Heartbeat { .. } => session
                        .as_mut()
                        .ok_or_else(|| "desktop is not running".to_owned())
                        .and_then(Session::heartbeat),
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
                    ) {
                        return Err(format!("{reason}; restoration: {error}"));
                    }
                }
                return Err(reason);
            }
            Err(RecvTimeoutError::Disconnected) => {
                if let Some(active) = session.as_ref() {
                    if let Err(error) =
                        restore_interrupted(path, &active.journal.session, "command_channel_closed")
                    {
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
                    match restore_interrupted(path, &active.journal.session, "host_failure") {
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
                match restore_interrupted(path, session, reason) {
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
