use super::*;

pub(crate) fn process(pid: u32) -> Result<OwnedHandle> {
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

pub(crate) fn created(handle: HANDLE) -> Result<u64> {
    let (mut birth, mut exit, mut kernel, mut user) =
        unsafe { (zeroed(), zeroed(), zeroed(), zeroed()) };
    if unsafe { GetProcessTimes(handle, &mut birth, &mut exit, &mut kernel, &mut user) } == 0 {
        return Err(failure("GetProcessTimes"));
    }
    Ok((u64::from(birth.dwHighDateTime) << 32) | u64::from(birth.dwLowDateTime))
}

pub(crate) fn process_image(handle: HANDLE) -> Result<String> {
    let mut buffer = [0u16; 32_768];
    let mut length = buffer.len() as u32;
    if unsafe { QueryFullProcessImageNameW(handle, 0, buffer.as_mut_ptr(), &mut length) } == 0 {
        return Err(failure("QueryFullProcessImageNameW"));
    }
    Ok(String::from_utf16_lossy(&buffer[..length as usize]))
}

pub(crate) fn parent(pid: u32) -> Result<u32> {
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

pub(crate) fn actual_parent() -> Result<u32> {
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

pub(crate) fn class_name(window: HWND) -> String {
    let mut buffer = [0u16; 256];
    let length = unsafe { GetClassNameW(window, buffer.as_mut_ptr(), buffer.len() as i32) };
    String::from_utf16_lossy(&buffer[..length.max(0) as usize])
}

pub(crate) fn window_pid(window: HWND) -> u32 {
    let mut pid = 0;
    unsafe {
        GetWindowThreadProcessId(window, &mut pid);
    }
    pid
}

pub(crate) fn process_named(pid: u32, names: &[&str]) -> bool {
    process(pid)
        .and_then(|owner| process_image(owner.0))
        .is_ok_and(|path| {
            path.rsplit(['\\', '/'])
                .next()
                .is_some_and(|name| names.iter().any(|expected| name.eq_ignore_ascii_case(expected)))
        })
}

pub(crate) fn normalize_target(value: &str) -> String {
    value.replace('/', "\\").to_lowercase()
}

pub(crate) fn owned_by(mut window: HWND, owner: HWND) -> bool {
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

pub(crate) fn is_explorer(pid: u32) -> bool {
    process_named(pid, &["explorer.exe"])
}

pub(crate) fn system_popup(window: HWND) -> bool {
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
pub(crate) struct Identity {
    pub(crate) handle: u64,
    pub(crate) pid: u32,
    pub(crate) created: u64,
    pub(crate) class: String,
    pub(crate) visible: bool,
}

impl Identity {
    pub(crate) fn same_window(&self, other: &Self) -> bool {
        self.handle == other.handle
            && self.pid == other.pid
            && self.created == other.created
            && self.class == other.class
    }

    pub(crate) fn capture(window: HWND) -> Result<Self> {
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

    pub(crate) fn window(&self) -> HWND {
        self.handle as usize as HWND
    }

    pub(crate) fn valid(&self) -> bool {
        let window = self.window();
        (unsafe { IsWindow(window) }) != 0
            && window_pid(window) == self.pid
            && class_name(window) == self.class
            && process(self.pid)
                .and_then(|owner| created(owner.0))
                .is_ok_and(|birth| birth == self.created)
    }
}

