use super::*;
use std::sync::mpsc::{Receiver, TryRecvError};
use windows::Win32::Foundation::HWND as ComHwnd;
use windows::Win32::System::Com::{
    CLSCTX_INPROC_SERVER, COINIT_MULTITHREADED, CoCreateInstance, CoInitializeEx, CoUninitialize,
};
use windows::Win32::UI::Accessibility::{CUIAutomation, IUIAutomation, TreeScope_Descendants};
use windows_sys::Win32::Graphics::Gdi::{EnumDisplayMonitors, HDC, HMONITOR};
use windows_sys::Win32::UI::Shell::{ABM_GETAUTOHIDEBAREX, APPBARDATA, SHAppBarMessage};

const PROBE_TIMEOUT: Duration = Duration::from_secs(2);

pub(super) fn on_display(window: HWND, display: Bounds) -> Result<bool> {
    unsafe extern "system" fn collect_monitor(
        _: HMONITOR,
        _: HDC,
        rectangle: *mut RECT,
        context: LPARAM,
    ) -> i32 {
        let monitors = unsafe { &mut *(context as *mut Vec<RECT>) };
        if monitors.len() >= 32 || rectangle.is_null() {
            return 0;
        }
        monitors.push(unsafe { *rectangle });
        1
    }

    let mut monitors = Vec::new();
    if unsafe {
        EnumDisplayMonitors(
            null_mut(),
            null(),
            Some(collect_monitor),
            &mut monitors as *mut _ as LPARAM,
        )
    } == 0
    {
        return Err(failure("query system tray monitors"));
    }
    // 隐藏任务栏可移动到邻屏坐标；自动隐藏 AppBar 登记才是其所属屏幕。
    for rectangle in monitors {
        for edge in 0..4 {
            let mut data: APPBARDATA = unsafe { zeroed() };
            data.cbSize = size_of::<APPBARDATA>() as u32;
            data.uEdge = edge;
            data.rc = rectangle;
            if unsafe { SHAppBarMessage(ABM_GETAUTOHIDEBAREX, &mut data) } == window as usize {
                return Ok(display.x == rectangle.left
                    && display.y == rectangle.top
                    && display.width == rectangle.right - rectangle.left
                    && display.height == rectangle.bottom - rectangle.top);
            }
        }
    }
    window_on_display(window, display)
}

#[derive(Clone, Copy, PartialEq, Eq, Serialize)]
pub(super) struct SystemTray {
    pub bounds: Bounds,
    pub visible: bool,
}

struct Probe {
    child: Child,
    receiver: Receiver<Result<ProbeResult>>,
    started: Instant,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ProbeResult {
    identity: Identity,
    taskbar: Bounds,
    bounds: Option<Bounds>,
    stable: bool,
}

impl Probe {
    fn start(identity: &Identity) -> Result<Self> {
        let mut child = ProcessCommand::new(std::env::current_exe().map_err(|error| error.to_string())?)
            .args(["tray-probe", &identity.handle.to_string()])
            .creation_flags(CREATE_NO_WINDOW)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|error| format!("start system tray query: {error}"))?;
        let output = child
            .stdout
            .take()
            .ok_or("system tray query stdout unavailable")?;
        let (sender, receiver) = channel();
        thread::spawn(move || {
            let result = (|| {
                let mut bytes = Vec::new();
                output
                    .take(65_537)
                    .read_to_end(&mut bytes)
                    .map_err(|error| error.to_string())?;
                if bytes.len() > 65_536 {
                    return Err("system tray query output exceeded budget".into());
                }
                let value: serde_json::Value = serde_json::from_slice(&bytes)
                    .map_err(|error| format!("invalid system tray query response: {error}"))?;
                if value.get("event").and_then(|value| value.as_str()) != Some("tray_probe") {
                    return Err(value
                        .get("reason")
                        .and_then(|value| value.as_str())
                        .unwrap_or("system tray query failed")
                        .to_string());
                }
                let mut response = value.as_object().cloned().ok_or("invalid tray query object")?;
                response.remove("event");
                serde_json::from_value(serde_json::Value::Object(response))
                    .map_err(|error| error.to_string())
            })();
            let _ = sender.send(result);
        });
        Ok(Self {
            child,
            receiver,
            started: Instant::now(),
        })
    }

    fn poll(&mut self) -> Result<Option<ProbeResult>> {
        match self.receiver.try_recv() {
            Ok(result) => {
                let status = self.child.try_wait().map_err(|error| error.to_string())?;
                if status.is_some_and(|status| !status.success()) {
                    return Err("system tray query process failed".into());
                }
                result.map(Some)
            }
            Err(TryRecvError::Disconnected) => Err("system tray query disconnected".into()),
            Err(TryRecvError::Empty) if self.started.elapsed() >= PROBE_TIMEOUT => {
                Err("system tray query timed out".into())
            }
            Err(TryRecvError::Empty) => Ok(None),
        }
    }
}

impl Drop for Probe {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.try_wait();
    }
}

pub(super) struct Tray {
    owner: Option<u64>,
    raw: Option<Bounds>,
    probe: Option<Probe>,
    last_probe: Instant,
    last_taskbar: Option<Bounds>,
    layout: Option<TrayLayout>,
    suspended: bool,
    last_position: Option<Bounds>,
    corrections: Vec<Instant>,
    pub state: Option<SystemTray>,
}

impl Tray {
    pub(super) fn new(journal: &Journal) -> Result<Self> {
        let mut tray = Self {
            owner: None,
            raw: None,
            probe: None,
            last_probe: Instant::now() - PROBE_TIMEOUT,
            last_taskbar: None,
            layout: None,
            suspended: false,
            last_position: None,
            corrections: Vec::new(),
            state: None,
        };
        let started = Instant::now();
        tray.refresh(journal)?;
        // 初始化尚未进入命令循环；仅在 UIA 子进程内等待供应方，等待总量有界。
        while tray.probe.is_some() {
            if started.elapsed() >= PROBE_TIMEOUT {
                return Err("system tray geometry did not settle".into());
            }
            thread::sleep(Duration::from_millis(10));
            tray.refresh(journal)?;
        }
        Ok(tray)
    }

    pub(super) fn refresh(&mut self, journal: &Journal) -> Result<()> {
        let shell = &journal.shell;
        let display = journal.desktop_bounds;
        if shell.len() > 1 {
            return Err("multiple taskbars occupy the interaction display".into());
        }
        let Some(identity) = shell.first() else {
            self.owner = None;
            self.raw = None;
            self.probe = None;
            self.state = None;
            self.last_taskbar = None;
            self.last_position = None;
            self.corrections.clear();
            return Ok(());
        };
        let original = journal.tray_placements.iter()
            .find(|saved| saved.identity.handle == identity.handle)
            .ok_or("system tray has no recoverable placement")?;
        if !identity.valid()
            || !is_explorer(identity.pid)
            || original.monitor != display
        {
            return Err("system tray taskbar identity changed".into());
        }
        if self.owner != Some(identity.handle) {
            self.owner = Some(identity.handle);
            self.raw = None;
            self.probe = None;
            self.last_taskbar = None;
            self.last_position = None;
            self.corrections.clear();
            self.last_probe = Instant::now() - PROBE_TIMEOUT;
        }
        let taskbar = rectangle(identity.window())?;
        if let Some(previous) = self.last_taskbar {
            if previous.width == taskbar.width && previous.height == taskbar.height {
                self.raw = self.raw.map(|bounds| Bounds {
                    x: bounds.x + taskbar.x - previous.x,
                    y: bounds.y + taskbar.y - previous.y,
                    ..bounds
                });
            } else if previous != taskbar {
                self.raw = None;
                self.probe = None;
                self.last_probe = Instant::now() - PROBE_TIMEOUT;
            }
        }
        self.last_taskbar = Some(taskbar);
        let native = child(identity.window(), "TrayNotifyWnd");
        if !native.is_null() {
            if window_pid(native) != identity.pid || unsafe { IsChild(identity.window(), native) } == 0 {
                return Err("native system tray does not belong to taskbar".into());
            }
            self.raw = Some(validate(rectangle(native)?, taskbar)?);
            self.probe = None;
        } else if let Some(probe) = &mut self.probe {
            if let Some(result) = probe.poll()? {
                self.probe = None;
                if result.identity.handle != identity.handle
                    || result.identity.pid != identity.pid
                    || result.identity.created != identity.created
                    || result.identity.class != identity.class
                {
                    return Err("system tray query identity changed".into());
                }
                if result.stable
                    && result.taskbar.width == taskbar.width
                    && result.taskbar.height == taskbar.height
                {
                    self.raw = result.bounds.map(|bounds| {
                        validate(Bounds {
                            x: bounds.x + taskbar.x - result.taskbar.x,
                            y: bounds.y + taskbar.y - result.taskbar.y,
                            ..bounds
                        }, taskbar)
                    }).transpose()?;
                    self.last_probe = Instant::now();
                } else {
                    self.probe = Some(Probe::start(identity)?);
                }
            }
        } else if self.last_probe.elapsed() >= PROBE_TIMEOUT {
            self.probe = Some(Probe::start(identity)?);
        }
        if self.raw.is_some_and(|bounds| {
            bounds.width > display.width || bounds.height > display.height
        }) {
            return Err("system tray is larger than the interaction display".into());
        }
        self.state = self.raw.map(|bounds| {
            // 布局消费者只需要固有尺寸；不要把滚动后的原生窗口位置反馈成新的槽位。
            let normalized = Bounds {
                x: (original.bounds.x + bounds.x - taskbar.x)
                    .clamp(display.x, display.x + display.width - bounds.width),
                y: (original.bounds.y + bounds.y - taskbar.y)
                    .clamp(display.y, display.y + display.height - bounds.height),
                ..bounds
            };
            SystemTray {
                bounds: normalized,
                visible: self.layout.is_some_and(|layout| {
                    tray_placement::intersection(bounds, layout.clip).is_some()
                }) && unsafe { IsWindowVisible(identity.window()) } != 0,
            }
        });
        Ok(())
    }

    pub(super) fn set_layout(&mut self, layout: Option<TrayLayout>, display: Bounds) -> Result<()> {
        if let Some(layout) = layout {
            if !layout.bounds.valid() || !layout.clip.valid()
                || layout.bounds.height > display.height
                || tray_placement::intersection(layout.clip, display) != Some(layout.clip)
            {
                return Err("native system tray layout is outside the interaction display".into());
            }
        }
        self.layout = layout;
        Ok(())
    }

    pub(super) fn suspend(&mut self, suspended: bool) -> bool {
        let changed = self.suspended != suspended;
        self.suspended = suspended;
        changed
    }

    pub(super) fn apply(&mut self, journal: &Journal, style: Option<&TrayStyle>) -> Result<()> {
        let mut visible = None;
        if let Some(original) = journal.tray_placements.first() {
            if !original.identity.valid() || !is_explorer(original.identity.pid) {
                return Err("system tray placement owner changed".into());
            }
            let window = original.identity.window();
            let before = rectangle(window)?;
            let layout = self.layout.filter(|_| !self.suspended);
            if layout.is_some() && style.is_none() {
                return Err("native system tray position synchronization is missing".into());
            }
            // Explorer 重建时等待异步 guard 就绪，不能先露出尚未保护的位置。
            let layout = layout.filter(|_| style.is_some_and(TrayStyle::placement_ready));
            if let (Some(layout), Some(native)) = (layout, self.raw) {
                let target = Bounds {
                    x: layout.bounds.x - (native.x - before.x),
                    y: layout.bounds.y + (layout.bounds.height - native.height) / 2 - (native.y - before.y),
                    width: before.width,
                    height: before.height,
                };
                if !target.valid() {
                    return Err("native system tray placement exceeds coordinate budget".into());
                }
                let style = style.ok_or("native system tray position synchronization is missing")?;
                style.set_placement(Some(target))?;
                let topmost = unsafe { GetWindowLongPtrW(window, GWL_EXSTYLE) } as u32 & WS_EX_TOPMOST != 0;
                if before != target || !topmost {
                    if self.last_position == Some(target) && before != target {
                        self.corrections.retain(|started| started.elapsed() < PROBE_TIMEOUT);
                        self.corrections.push(Instant::now());
                        if self.corrections.len() > 8 {
                            return Err("Windows repeatedly reset the inline system tray placement".into());
                        }
                    }
                    // Explorer 的 WM_WINDOWPOSCHANGING 会把任务栏同步拉回屏幕边缘。
                    if unsafe { SetWindowPos(window, HWND_TOPMOST, target.x, target.y, target.width, target.height,
                        SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_NOSENDCHANGING,
                    ) } == 0 {
                        return Err(failure("position inline native system tray"));
                    }
                    if !rectangle(window)?.within_tolerance(target, 2) {
                        return Err("Windows rejected inline system tray placement".into());
                    }
                }
                self.last_position = Some(target);
                let after = rectangle(window)?;
                let translated = Bounds {
                    x: native.x + after.x - before.x,
                    y: native.y + after.y - before.y,
                    ..native
                };
                self.raw = Some(translated);
                self.last_taskbar = Some(after);
                visible = tray_placement::intersection(translated, layout.clip);
                if unsafe { IsWindowVisible(window) } == 0 {
                    visibility(&original.identity, true)?;
                }
            } else {
                if let Some(style) = style {
                    style.set_placement(None)?;
                }
                // 先收起区域，再恢复 Shell 几何，避免切换时短暂露出完整任务栏。
                if let Some(saved) = journal.regions.iter().find(|saved| saved.identity.handle == original.identity.handle) {
                    saved.clip(None)?;
                }
                original.restore()?;
                let after = rectangle(window)?;
                self.raw = self.raw.map(|bounds| Bounds {
                    x: bounds.x + after.x - before.x,
                    y: bounds.y + after.y - before.y,
                    ..bounds
                });
                self.last_taskbar = Some(after);
                self.last_position = None;
                self.corrections.clear();
            }
        }
        for saved in &journal.regions {
            if journal.shell.iter().any(|identity| identity.handle == saved.identity.handle) {
                saved.clip(visible)?;
            }
        }
        if let Some(state) = &mut self.state {
            state.visible = visible.is_some();
        }
        Ok(())
    }
}

fn validate(bounds: Bounds, taskbar: Bounds) -> Result<Bounds> {
    if !bounds.valid()
        || bounds.x < taskbar.x - 2
        || bounds.y < taskbar.y - 2
        || bounds.x + bounds.width > taskbar.x + taskbar.width + 2
        || bounds.y + bounds.height > taskbar.y + taskbar.height + 2
    {
        return Err("system tray geometry is outside its taskbar".into());
    }
    Ok(bounds)
}

pub(super) fn probe(handle: u64) -> Result<()> {
    if unsafe { SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) } == 0 {
        return Err(failure("system tray query DPI context"));
    }
    let identity = Identity::capture(handle as usize as HWND)?;
    if !["Shell_TrayWnd", "Shell_SecondaryTrayWnd"].contains(&identity.class.as_str())
        || !is_explorer(identity.pid)
    {
        return Err("system tray query requires an Explorer taskbar".into());
    }
    let taskbar = rectangle(identity.window())?;
    unsafe { CoInitializeEx(None, COINIT_MULTITHREADED) }
        .ok()
        .map_err(|error| error.to_string())?;
    struct Com;
    impl Drop for Com {
        fn drop(&mut self) {
            unsafe { CoUninitialize() };
        }
    }
    let _com = Com;
    let automation: IUIAutomation = unsafe {
        CoCreateInstance(&CUIAutomation, None, CLSCTX_INPROC_SERVER)
    }
    .map_err(|error| error.to_string())?;
    let root = unsafe { automation.ElementFromHandle(ComHwnd(identity.window())) }
        .map_err(|error| error.to_string())?;
    let condition = unsafe { automation.CreateTrueCondition() }
        .map_err(|error| error.to_string())?;
    let elements = unsafe { root.FindAll(TreeScope_Descendants, &condition) }
        .map_err(|error| error.to_string())?;
    let length = unsafe { elements.Length() }.map_err(|error| error.to_string())?;
    if length > 1024 {
        return Err("system tray query exceeded element budget".into());
    }
    let mut bounds: Option<Bounds> = None;
    let mut recognized = false;
    for index in 0..length {
        let element = unsafe { elements.GetElement(index) }.map_err(|error| error.to_string())?;
        if unsafe { element.CurrentProcessId() }.map_err(|error| error.to_string())?
            != identity.pid as i32
        {
            continue;
        }
        let class = unsafe { element.CurrentClassName() }
            .map_err(|error| error.to_string())?
            .to_string();
        let id = unsafe { element.CurrentAutomationId() }
            .map_err(|error| error.to_string())?
            .to_string();
        recognized |= id == "TaskbarFrame" || class == "TrayClockWClass";
        if !(class.starts_with("SystemTray.")
            && ["SystemTrayIcon", "NotifyItemIcon"].contains(&id.as_str()))
            && class != "TrayClockWClass"
        {
            continue;
        }
        let rect = unsafe { element.CurrentBoundingRectangle() }
            .map_err(|error| error.to_string())?;
        let item = Bounds {
            x: rect.left,
            y: rect.top,
            width: rect.right - rect.left,
            height: rect.bottom - rect.top,
        };
        if !item.valid() {
            continue;
        }
        bounds = Some(match bounds {
            None => item,
            Some(old) => {
                let x = old.x.min(item.x);
                let y = old.y.min(item.y);
                Bounds {
                    x,
                    y,
                    width: (old.x + old.width).max(item.x + item.width) - x,
                    height: (old.y + old.height).max(item.y + item.height) - y,
                }
            }
        });
    }
    if !identity.valid()
        || (!recognized && bounds.is_none())
        || (identity.class == "Shell_TrayWnd" && bounds.is_none())
    {
        return Err("Windows did not expose a recognized system tray structure".into());
    }
    let stable = taskbar == rectangle(identity.window())?;
    if stable {
        if let Some(bounds) = bounds {
            validate(bounds, taskbar)?;
        }
    }
    emit(json!({
        "event": "tray_probe",
        "identity": identity,
        "taskbar": taskbar,
        "bounds": bounds,
        "stable": stable,
    }));
    Ok(())
}
