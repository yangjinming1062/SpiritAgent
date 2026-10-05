use super::*;
use std::collections::{HashMap, HashSet};
use windows::Win32::Foundation::{HWND as ComHwnd, PROPERTYKEY};
use windows::Win32::System::Com::StructuredStorage::{PropVariantClear, PropVariantToStringAlloc};
use windows::Win32::System::Com::{
    CLSCTX_ALL, COINIT_APARTMENTTHREADED, CoCreateInstance, CoInitializeEx, CoTaskMemFree,
    CoUninitialize,
};
use windows::Win32::UI::Shell::PropertiesSystem::{IPropertyStore, SHGetPropertyStoreForWindow};
use windows::Win32::UI::Shell::{IVirtualDesktopManager, VirtualDesktopManager};
use windows_sys::Win32::Graphics::Dwm::{DWMWA_CLOAKED, DwmGetWindowAttribute};
use windows_sys::Win32::Storage::Packaging::Appx::GetApplicationUserModelId;

const APP_ID: PROPERTYKEY = PROPERTYKEY {
    fmtid: windows::core::GUID::from_u128(0x9f4c2855_9f79_4b39_a8d0_e1d42de1d5f3),
    pid: 5,
};

struct Apartment;
impl Apartment {
    fn start() -> Result<Self> {
        unsafe { CoInitializeEx(None, COINIT_APARTMENTTHREADED) }
            .ok()
            .map_err(|e| e.to_string())?;
        Ok(Self)
    }
}
impl Drop for Apartment {
    fn drop(&mut self) {
        unsafe {
            CoUninitialize();
        }
    }
}

#[derive(Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
struct RunningWindow {
    id: String,
    app_id: String,
    target: Option<String>,
    name: String,
    title: String,
    minimized: bool,
    last_active: u64,
}

struct TrackedWindow {
    identity: Identity,
    value: RunningWindow,
}

fn packaged_id(pid: u32) -> Option<String> {
    let owner = process(pid).ok()?;
    let mut size = 0;
    if unsafe { GetApplicationUserModelId(owner.0, &mut size, null_mut()) }
        != ERROR_INSUFFICIENT_BUFFER
        || size == 0
        || size > 1024
    {
        return None;
    }
    let mut buffer = vec![0u16; size as usize];
    if unsafe { GetApplicationUserModelId(owner.0, &mut size, buffer.as_mut_ptr()) }
        != ERROR_SUCCESS
    {
        return None;
    }
    Some(String::from_utf16_lossy(
        &buffer[..buffer.iter().position(|c| *c == 0).unwrap_or(buffer.len())],
    ))
}

fn window_app_id(window: HWND) -> Option<String> {
    let store: IPropertyStore = unsafe { SHGetPropertyStoreForWindow(ComHwnd(window)) }.ok()?;
    let mut value = unsafe { store.GetValue(&APP_ID) }.ok()?;
    let text = unsafe { PropVariantToStringAlloc(&value) }
        .ok()
        .and_then(|text| {
            let result = unsafe { text.to_string() }.ok();
            unsafe {
                CoTaskMemFree(Some(text.0.cast()));
            }
            result
        });
    let _ = unsafe { PropVariantClear(&mut value) };
    text.filter(|id| id.len() <= 1024 && id.contains('!') && id.contains('_'))
}

unsafe extern "system" fn collect_child_pid(window: HWND, data: LPARAM) -> i32 {
    let pid = window_pid(window);
    let pids = unsafe { &mut *(data as *mut Vec<u32>) };
    if !pids.contains(&pid) {
        pids.push(pid);
    }
    1
}

fn application_target(identity: &Identity) -> Option<String> {
    let mut app_id = window_app_id(identity.window()).or_else(|| packaged_id(identity.pid));
    if app_id.is_none() && identity.class == "ApplicationFrameWindow" {
        let mut pids = Vec::<u32>::new();
        unsafe {
            EnumChildWindows(
                identity.window(),
                Some(collect_child_pid),
                &mut pids as *mut _ as LPARAM,
            );
        }
        app_id = pids
            .into_iter()
            .filter(|pid| *pid != identity.pid)
            .find_map(packaged_id);
    }
    if let Some(id) = app_id {
        return Some(format!("shell:AppsFolder\\{id}"));
    }
    if identity.class == "ApplicationFrameWindow" {
        return None;
    }
    let owner = process(identity.pid).ok()?;
    let mut buffer = [0u16; 32_768];
    let mut length = buffer.len() as u32;
    if unsafe { QueryFullProcessImageNameW(owner.0, 0, buffer.as_mut_ptr(), &mut length) } == 0 {
        return None;
    }
    let target = String::from_utf16_lossy(&buffer[..length as usize]);
    (target.len() < 16_384).then_some(target)
}

fn title(window: HWND) -> String {
    let mut buffer = [0u16; 513];
    let size = unsafe { GetWindowTextW(window, buffer.as_mut_ptr(), buffer.len() as i32) };
    String::from_utf16_lossy(&buffer[..size.max(0) as usize])
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

fn activation_target(window: HWND) -> Result<HWND> {
    let root = unsafe { GetAncestor(window, GA_ROOTOWNER) };
    let popup = unsafe { GetLastActivePopup(root) };
    if !popup.is_null()
        && owned_by(popup, window)
        && unsafe { IsWindowVisible(popup) != 0 && IsWindowEnabled(popup) != 0 }
    {
        return Ok(popup);
    }
    // 带隐藏 owner 的主窗口不能直接用 GetLastActivePopup 查弹窗；只在其所属分支内找可交互窗口。
    if unsafe { IsWindowEnabled(window) } == 0 {
        if let Some(popup) = top_windows()?.into_iter().find(|popup| {
            *popup != window
                && owned_by(*popup, window)
                && unsafe { IsWindowVisible(*popup) != 0 && IsWindowEnabled(*popup) != 0 }
        }) {
            return Ok(popup);
        }
        return Err("该窗口正在等待对话框，但对话框暂时不可用。".into());
    }
    Ok(window)
}

pub(super) struct Applications {
    desktop: IVirtualDesktopManager,
    // COM 接口须先释放，再退出 apartment。
    _apartment: Apartment,
    session: String,
    parent_pid: u32,
    windows: HashMap<usize, TrackedWindow>,
    next_window: u64,
    activity: u64,
    last_foreground: usize,
    last_scan: Instant,
    revision: u64,
    published: Option<(Vec<RunningWindow>, Option<String>)>,
}

impl Applications {
    pub(super) fn new(session: &str, parent_pid: u32) -> Result<Self> {
        let apartment = Apartment::start()?;
        let desktop = unsafe { CoCreateInstance(&VirtualDesktopManager, None, CLSCTX_ALL) }
            .map_err(|e| e.to_string())?;
        Ok(Self {
            desktop,
            _apartment: apartment,
            session: session.into(),
            parent_pid,
            windows: HashMap::new(),
            next_window: 0,
            activity: 0,
            last_foreground: 0,
            last_scan: Instant::now() - Duration::from_secs(2),
            revision: 0,
            published: None,
        })
    }

    fn eligible(&self, window: HWND) -> Result<bool> {
        if unsafe {
            IsWindow(window) == 0
                || IsWindowVisible(window) == 0
                || GetAncestor(window, GA_ROOT) != window
        } || window_pid(window) == self.parent_pid
        {
            return Ok(false);
        }
        let style = unsafe { GetWindowLongPtrW(window, GWL_STYLE) } as u32;
        let ex = unsafe { GetWindowLongPtrW(window, GWL_EXSTYLE) } as u32;
        if style & WS_CHILD != 0
            || ex & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) != 0
            || (!unsafe { GetWindow(window, GW_OWNER) }.is_null() && ex & WS_EX_APPWINDOW == 0)
            || [
                "Progman",
                "WorkerW",
                "Shell_TrayWnd",
                "Shell_SecondaryTrayWnd",
                "#32768",
                "tooltips_class32",
                "IME",
                "MSCTFIME UI",
            ]
            .contains(&class_name(window).as_str())
        {
            return Ok(false);
        }
        let mut cloaked = 0u32;
        unsafe {
            DwmGetWindowAttribute(
                window,
                DWMWA_CLOAKED as u32,
                &mut cloaked as *mut _ as *mut _,
                size_of::<u32>() as u32,
            );
        }
        if cloaked != 0 && unsafe { IsIconic(window) } == 0 {
            return Ok(false);
        }
        unsafe {
            self.desktop
                .IsWindowOnCurrentVirtualDesktop(ComHwnd(window))
        }
        .map(|value| value.as_bool())
        .map_err(|e| e.to_string())
    }

    fn scan(&mut self) -> Result<Vec<RunningWindow>> {
        let handles = top_windows()?;
        let _ = unsafe {
            self.desktop
                .IsWindowOnCurrentVirtualDesktop(ComHwnd(GetShellWindow()))
        }
        .map_err(|e| e.to_string())?;
        let initial = self.published.is_none();
        if initial {
            self.activity = handles.len() as u64;
        }
        let foreground = unsafe { GetAncestor(GetForegroundWindow(), GA_ROOT) } as usize;
        if foreground != self.last_foreground {
            self.last_foreground = foreground;
            self.activity += 1;
        }
        let mut seen = HashSet::new();
        let mut output = Vec::new();
        let count = handles.len();
        for (index, window) in handles.into_iter().enumerate() {
            match self.eligible(window) {
                Ok(true) => {}
                Ok(false) => continue,
                Err(_) => continue,
            }
            let handle = window as usize;
            if self
                .windows
                .get(&handle)
                .is_some_and(|tracked| !tracked.identity.valid())
            {
                self.windows.remove(&handle);
            }
            if !self.windows.contains_key(&handle) {
                let Ok(identity) = Identity::capture(window) else {
                    continue;
                };
                let target = application_target(&identity);
                let name = target
                    .as_ref()
                    .and_then(|target| Path::new(target).file_stem())
                    .map(|name| name.to_string_lossy().into_owned())
                    .unwrap_or_else(|| title(window));
                let app_id = target
                    .as_ref()
                    .map(|target| target.to_lowercase())
                    .unwrap_or_else(|| format!("window:{}:{}", self.session, self.next_window + 1));
                self.next_window += 1;
                self.windows.insert(
                    handle,
                    TrackedWindow {
                        identity,
                        value: RunningWindow {
                            id: format!("{}:{}", self.session, self.next_window),
                            app_id,
                            target,
                            name,
                            title: String::new(),
                            minimized: false,
                            last_active: if initial { (count - index) as u64 } else { 0 },
                        },
                    },
                );
            }
            let tracked = self
                .windows
                .get_mut(&handle)
                .expect("registered running window");
            // 打包窗口可能先显示框架，随后才挂上实际应用；未识别的宿主不能永久按进程归并。
            if tracked.value.target.is_none() {
                if let Some(target) = application_target(&tracked.identity) {
                    tracked.value.app_id = target.to_lowercase();
                    tracked.value.name = Path::new(&target)
                        .file_stem()
                        .map(|name| name.to_string_lossy().into_owned())
                        .unwrap_or_default();
                    tracked.value.target = Some(target);
                }
            }
            tracked.value.title = title(window);
            tracked.value.minimized = unsafe { IsIconic(window) } != 0;
            if owned_by(foreground as HWND, window) {
                tracked.value.last_active = self.activity;
            }
            seen.insert(handle);
            output.push(tracked.value.clone());
        }
        self.windows.retain(|handle, _| seen.contains(handle));
        // EnumWindows 的层序不应使快照和图标顺序在每次切换时变化。
        output.sort_by(|a, b| a.id.cmp(&b.id));
        Ok(output)
    }

    pub(super) fn poll(&mut self, events: &[(u32, usize)], force: bool) {
        let mut changed = false;
        for &(event, handle) in events {
            match event {
                0 => {
                    self.windows.clear();
                    changed = true;
                }
                EVENT_OBJECT_CREATE | EVENT_OBJECT_DESTROY => {
                    self.windows.remove(&handle);
                    changed = true;
                }
                EVENT_OBJECT_SHOW
                | EVENT_OBJECT_HIDE
                | EVENT_OBJECT_NAMECHANGE
                | EVENT_OBJECT_CLOAKED
                | EVENT_OBJECT_UNCLOAKED
                | EVENT_SYSTEM_FOREGROUND
                | EVENT_SYSTEM_MINIMIZESTART
                | EVENT_SYSTEM_MINIMIZEEND => changed = true,
                _ => {}
            }
        }
        if !force && !changed && self.last_scan.elapsed() < Duration::from_secs(2) {
            return;
        }
        self.last_scan = Instant::now();
        let (mut windows, mut error) = match self.scan() {
            Ok(windows) => (windows, None),
            Err(error) => {
                if let Ok(desktop) =
                    unsafe { CoCreateInstance(&VirtualDesktopManager, None, CLSCTX_ALL) }
                {
                    self.desktop = desktop;
                }
                (Vec::new(), Some(error))
            }
        };
        let encoded_sizes: Vec<usize> = windows
            .iter()
            .map(|window| {
                serde_json::to_vec(window)
                    .map(|value| value.len())
                    .unwrap_or(32_768)
            })
            .collect();
        if encoded_sizes.iter().sum::<usize>() > 2 * 1024 * 1024 - 32_768 {
            windows.clear();
            error = Some("运行窗口列表超过传输上限。".into());
        }
        if self
            .published
            .as_ref()
            .is_some_and(|(last_windows, last_error)| {
                last_windows == &windows && last_error == &error
            })
        {
            return;
        }
        self.published = Some((windows.clone(), error.clone()));
        self.revision += 1;
        let mut parts = vec![Vec::new()];
        let mut bytes = 256;
        for (window, size) in windows.into_iter().zip(encoded_sizes) {
            if bytes + size > 32_768 && !parts.last().is_some_and(Vec::is_empty) {
                parts.push(Vec::new());
                bytes = 256;
            }
            bytes += size;
            parts.last_mut().expect("snapshot part").push(window);
        }
        for (part, windows) in parts.iter().enumerate() {
            emit(
                json!({ "event": "applications", "revision": self.revision, "part": part,
                "parts": parts.len(), "status": if error.is_some() { "unavailable" } else { "ready" },
                "error": error, "windows": windows }),
            );
        }
    }

    pub(super) fn activate(&self, id: &str) -> Result<()> {
        let tracked = self
            .windows
            .values()
            .find(|tracked| tracked.value.id == id)
            .ok_or("窗口已关闭或桌面已改变，请重新选择。")?;
        let window = tracked.identity.window();
        if !tracked.identity.valid() || !self.eligible(window)? {
            return Err("窗口已关闭或已离开当前桌面。".into());
        }
        if unsafe { IsIconic(window) } != 0 {
            unsafe {
                ShowWindowAsync(window, SW_RESTORE);
            }
        }
        let target = activation_target(window)?;
        let source = unsafe { GetCurrentThreadId() };
        let foreground = unsafe { GetForegroundWindow() };
        let foreground_thread = unsafe { GetWindowThreadProcessId(foreground, null_mut()) };
        let target_thread = unsafe { GetWindowThreadProcessId(target, null_mut()) };
        if target_thread == 0 {
            return Err("窗口已关闭，请重新选择程序。".into());
        }
        // 已显示窗口不会经由还原自动激活；目标线程也须加入当前桌面的输入队列。
        let mut attachments = Vec::<InputAttachment>::new();
        for thread in [foreground_thread, target_thread] {
            if thread == 0
                || thread == source
                || attachments.iter().any(|attached| attached.target == thread)
            {
                continue;
            }
            if unsafe { AttachThreadInput(source, thread, 1) } == 0 {
                return Err(failure("attach external window input"));
            }
            attachments.push(InputAttachment {
                source,
                target: thread,
            });
        }
        let accepted = unsafe { SetForegroundWindow(target) } != 0;
        while let Some(attached) = attachments.pop() {
            drop(attached);
        }
        let started = Instant::now();
        while started.elapsed() < Duration::from_millis(350) {
            let active = unsafe { GetForegroundWindow() };
            if active == target || active == window || owned_by(active, window) {
                return Ok(());
            }
            thread::sleep(Duration::from_millis(10));
        }
        Err(if accepted {
            "窗口没有获得前台焦点，请重试。"
        } else {
            "Windows 拒绝切换到该窗口，请重试。"
        }
        .into())
    }
}
