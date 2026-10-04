use super::*;
use std::collections::{HashMap, HashSet};
use std::sync::Mutex;
use windows_sys::Win32::Graphics::Dwm::{
    DWMWA_CLOAKED, DWMWA_EXTENDED_FRAME_BOUNDS, DwmGetWindowAttribute,
};
use windows_sys::Win32::Graphics::Gdi::{
    EnumDisplayMonitors, HDC, HMONITOR, MONITOR_DEFAULTTONEAREST, MONITORINFOEXW, MonitorFromWindow,
};
use windows_sys::Win32::UI::Accessibility::{HWINEVENTHOOK, SetWinEventHook, UnhookWinEvent};

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct RestoreWorkArea {
    device: String,
    monitor: Bounds,
    original: Bounds,
}

fn bounds(rect: RECT) -> Bounds {
    Bounds {
        x: rect.left,
        y: rect.top,
        width: rect.right - rect.left,
        height: rect.bottom - rect.top,
    }
}

fn rect(area: Bounds) -> RECT {
    RECT {
        left: area.x,
        top: area.y,
        right: area.x + area.width,
        bottom: area.y + area.height,
    }
}

fn info(monitor: HMONITOR) -> Result<MONITORINFOEXW> {
    let mut value: MONITORINFOEXW = unsafe { zeroed() };
    value.monitorInfo.cbSize = size_of::<MONITORINFOEXW>() as u32;
    if unsafe { GetMonitorInfoW(monitor, &mut value.monitorInfo) } == 0 {
        return Err(failure("read monitor work area"));
    }
    Ok(value)
}

fn device(value: &MONITORINFOEXW) -> String {
    let end = value
        .szDevice
        .iter()
        .position(|c| *c == 0)
        .unwrap_or(value.szDevice.len());
    String::from_utf16_lossy(&value.szDevice[..end])
}

unsafe extern "system" fn collect_monitor(
    monitor: HMONITOR,
    _: HDC,
    _: *mut RECT,
    data: LPARAM,
) -> i32 {
    unsafe {
        (&mut *(data as *mut Vec<HMONITOR>)).push(monitor);
    }
    1
}

fn find_monitor(name: &str) -> Result<Option<MONITORINFOEXW>> {
    let mut monitors = Vec::new();
    if unsafe {
        EnumDisplayMonitors(
            null_mut(),
            null(),
            Some(collect_monitor),
            &mut monitors as *mut Vec<HMONITOR> as LPARAM,
        )
    } == 0
    {
        return Err(failure("enumerate recovery monitors"));
    }
    for monitor in monitors {
        let value = info(monitor)?;
        if device(&value) == name {
            return Ok(Some(value));
        }
    }
    Ok(None)
}

fn set_work_area(area: Bounds) -> Result<()> {
    let rectangle = rect(area);
    // 仅当前会话生效；不持久化或广播，避免 Explorer 重算覆盖预留区域。
    if unsafe { SystemParametersInfoW(SPI_SETWORKAREA, 0, &rectangle as *const RECT as *mut _, 0) }
        == 0
    {
        return Err(failure("set desktop work area"));
    }
    Ok(())
}

impl RestoreWorkArea {
    pub(super) fn validate(&self) -> Result<()> {
        if self.device.is_empty()
            || self.device.len() > 128
            || !self.monitor.valid()
            || !self.original.valid()
            || !contains(self.monitor, self.original)
        {
            return Err("invalid work area recovery record".into());
        }
        Ok(())
    }

    pub(super) fn restore(&self) -> Result<()> {
        let Some(current) = find_monitor(&self.device)? else {
            return Ok(());
        };
        let monitor = bounds(current.monitorInfo.rcMonitor);
        let original = Bounds {
            x: monitor.x + (self.original.x - self.monitor.x).min(monitor.width - 1),
            y: monitor.y + (self.original.y - self.monitor.y).min(monitor.height - 1),
            width: (monitor.width - (self.monitor.width - self.original.width)).max(1),
            height: (monitor.height - (self.monitor.height - self.original.height)).max(1),
        };
        set_work_area(original)
    }
}

static EVENTS: Mutex<Vec<(u32, usize)>> = Mutex::new(Vec::new());

unsafe extern "system" fn window_event(
    _: HWINEVENTHOOK,
    event: u32,
    hwnd: HWND,
    object: i32,
    child: i32,
    _: u32,
    _: u32,
) {
    if hwnd.is_null() || (event >= EVENT_OBJECT_CREATE && (object != OBJID_WINDOW || child != 0)) {
        return;
    }
    if let Ok(mut queue) = EVENTS.lock() {
        let item = (event, hwnd as usize);
        if queue.len() < 512 && !queue.contains(&item) {
            queue.push(item);
        }
    }
}

struct WinEvents(Vec<HWINEVENTHOOK>);
impl WinEvents {
    fn start() -> Result<Self> {
        let mut hooks = Self(Vec::new());
        for (start, end) in [
            (EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_MINIMIZEEND),
            (EVENT_OBJECT_DESTROY, EVENT_OBJECT_LOCATIONCHANGE),
        ] {
            let handle = unsafe {
                SetWinEventHook(
                    start,
                    end,
                    null_mut(),
                    Some(window_event),
                    0,
                    0,
                    WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
                )
            };
            if handle.is_null() {
                return Err(failure("listen for desktop window changes"));
            }
            hooks.0.push(handle);
        }
        Ok(hooks)
    }
}
impl Drop for WinEvents {
    fn drop(&mut self) {
        for hook in &self.0 {
            unsafe {
                UnhookWinEvent(*hook);
            }
        }
        if let Ok(mut queue) = EVENTS.lock() {
            queue.clear();
        }
    }
}

fn visible_bounds(window: HWND) -> Result<Bounds> {
    let mut rectangle: RECT = unsafe { zeroed() };
    if unsafe {
        DwmGetWindowAttribute(
            window,
            DWMWA_EXTENDED_FRAME_BOUNDS as u32,
            &mut rectangle as *mut RECT as *mut _,
            size_of::<RECT>() as u32,
        )
    } >= 0
        && rectangle.right > rectangle.left
        && rectangle.bottom > rectangle.top
    {
        return Ok(bounds(rectangle));
    }
    super::rectangle(window)
}

fn uncloaked(window: HWND) -> bool {
    let mut cloaked = 0u32;
    unsafe {
        DwmGetWindowAttribute(
            window,
            DWMWA_CLOAKED as u32,
            &mut cloaked as *mut u32 as *mut _,
            size_of::<u32>() as u32,
        );
    }
    cloaked == 0
}

fn contains(area: Bounds, window: Bounds) -> bool {
    window.x >= area.x
        && window.y >= area.y
        && window.x + window.width <= area.x + area.width
        && window.y + window.height <= area.y + area.height
}

pub(super) struct Workspace {
    pub(super) original: RestoreWorkArea,
    target: Bounds,
    parent_pid: u32,
    enabled: bool,
    hooks: Option<WinEvents>,
    moving: HashSet<usize>,
    pending: HashMap<usize, Instant>,
    refused: HashSet<usize>,
}

impl Workspace {
    pub(super) fn prepare(
        requested: Bounds,
        display: Bounds,
        parent_pid: u32,
        enabled: bool,
    ) -> Result<Self> {
        if !requested.valid() || !display.valid() {
            return Err("invalid desktop work area".into());
        }
        if !contains(display, requested) {
            return Err(format!(
                "desktop work area crosses its display: {requested:?}, display {display:?}"
            ));
        }
        let normalized = monitor_bounds(display)?;
        // Electron 的 DIP 往返矩形会向外取整；沿用完整显示器校验，保留四侧预留量。
        let target = Bounds {
            x: normalized.x + requested.x - display.x,
            y: normalized.y + requested.y - display.y,
            width: normalized.width - (display.width - requested.width),
            height: normalized.height - (display.height - requested.height),
        };
        if !target.valid() || !contains(normalized, target) {
            return Err(format!(
                "desktop work area crosses monitors: {target:?}, monitor {normalized:?}"
            ));
        }
        let rectangle = rect(normalized);
        let monitor = unsafe { MonitorFromRect(&rectangle, MONITOR_DEFAULTTONULL) };
        if monitor.is_null() {
            return Err("desktop work area has no monitor".into());
        }
        let value = info(monitor)?;
        let original = RestoreWorkArea {
            device: device(&value),
            monitor: bounds(value.monitorInfo.rcMonitor),
            original: bounds(value.monitorInfo.rcWork),
        };
        original.validate()?;
        if !contains(original.monitor, target) {
            return Err("desktop monitor changed during work area validation".into());
        }
        Ok(Self {
            original,
            target,
            parent_pid,
            enabled,
            hooks: None,
            moving: HashSet::new(),
            pending: HashMap::new(),
            refused: HashSet::new(),
        })
    }

    pub(super) fn activate(&mut self) -> Result<()> {
        self.hooks = Some(WinEvents::start()?);
        self.ensure_work_area()?;
        for window in top_windows()? {
            self.constrain(window);
        }
        Ok(())
    }

    fn ensure_work_area(&self) -> Result<()> {
        if !self.enabled {
            return Ok(());
        }
        let current = find_monitor(&self.original.device)?.ok_or("desktop monitor was removed")?;
        if bounds(current.monitorInfo.rcMonitor) != self.original.monitor {
            return Err("desktop monitor geometry changed".into());
        }
        if bounds(current.monitorInfo.rcWork) == self.target {
            return Ok(());
        }
        set_work_area(self.target)?;
        let current = find_monitor(&self.original.device)?.ok_or("desktop monitor was removed")?;
        if bounds(current.monitorInfo.rcWork) != self.target {
            return Err(format!(
                "desktop work area was rejected: {:?}, expected {:?}",
                bounds(current.monitorInfo.rcWork),
                self.target
            ));
        }
        Ok(())
    }

    pub(super) fn fullscreen(&self, window: HWND) -> bool {
        if window.is_null()
            || window_pid(window) == self.parent_pid
            || unsafe { GetAncestor(window, GA_ROOT) } != window
            // Explorer 桌面同样铺满屏幕，焦点切回桌面不能触发全屏避让。
            || window == unsafe { GetShellWindow() }
            || ["Progman", "WorkerW"].contains(&class_name(window).as_str())
            || unsafe { IsWindowVisible(window) == 0 || IsIconic(window) != 0 }
            || !uncloaked(window)
        {
            return false;
        }
        let Ok(value) = info(unsafe { MonitorFromWindow(window, MONITOR_DEFAULTTONEAREST) }) else {
            return false;
        };
        if device(&value) != self.original.device {
            return false;
        }
        let Ok(area) = visible_bounds(window) else {
            return false;
        };
        let monitor = self.original.monitor;
        (unsafe { GetWindowLongPtrW(window, GWL_STYLE) as u32 & WS_CAPTION == 0 })
            && area.x <= monitor.x + 2
            && area.y <= monitor.y + 2
            && area.x + area.width >= monitor.x + monitor.width - 2
            && area.y + area.height >= monitor.y + monitor.height - 2
    }

    fn eligible(&self, window: HWND) -> bool {
        if unsafe {
            IsWindow(window) == 0
                || IsWindowVisible(window) == 0
                || IsIconic(window) != 0
                || GetAncestor(window, GA_ROOT) != window
        } || window_pid(window) == self.parent_pid
            || !uncloaked(window)
            || self.fullscreen(window)
        {
            return false;
        }
        let style = unsafe { GetWindowLongPtrW(window, GWL_STYLE) } as u32;
        let ex = unsafe { GetWindowLongPtrW(window, GWL_EXSTYLE) } as u32;
        if style & WS_CHILD != 0
            || ex & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) != 0
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
            return false;
        }
        info(unsafe { MonitorFromWindow(window, MONITOR_DEFAULTTONEAREST) })
            .is_ok_and(|value| device(&value) == self.original.device)
    }

    fn warn(&mut self, handle: usize) {
        self.pending.remove(&handle);
        if self.refused.insert(handle) {
            emit(
                json!({ "event": "warning", "reason": "部分程序窗口拒绝调整位置或大小，无法完整限制在桌面工作区。" }),
            );
        }
    }

    fn constrain(&mut self, window: HWND) {
        let handle = window as usize;
        if !self.enabled
            || self.moving.contains(&handle)
            || self.refused.contains(&handle)
            || self.pending.contains_key(&handle)
            || !self.eligible(window)
        {
            return;
        }
        let (Ok(visual), Ok(outer)) = (visible_bounds(window), rectangle(window)) else {
            return;
        };
        if contains(self.target, visual) {
            return;
        }
        let width = visual.width.min(self.target.width);
        let height = visual.height.min(self.target.height);
        let x = visual
            .x
            .clamp(self.target.x, self.target.x + self.target.width - width);
        let y = visual
            .y
            .clamp(self.target.y, self.target.y + self.target.height - height);
        if unsafe {
            SetWindowPos(
                window,
                null_mut(),
                x + outer.x - visual.x,
                y + outer.y - visual.y,
                width + outer.width - visual.width,
                height + outer.height - visual.height,
                SWP_NOACTIVATE | SWP_NOZORDER | SWP_ASYNCWINDOWPOS,
            )
        } == 0
        {
            self.warn(handle);
        } else {
            self.pending.insert(handle, Instant::now());
        }
    }

    pub(super) fn poll(&mut self) -> Result<()> {
        self.ensure_work_area()?;
        let events = EVENTS
            .lock()
            .map(|mut queue| std::mem::take(&mut *queue))
            .unwrap_or_default();
        let mut changed = HashSet::new();
        for (event, handle) in events {
            match event {
                EVENT_SYSTEM_MOVESIZESTART => {
                    self.moving.insert(handle);
                    self.pending.remove(&handle);
                    self.refused.remove(&handle);
                }
                EVENT_SYSTEM_MOVESIZEEND => {
                    self.moving.remove(&handle);
                    changed.insert(handle);
                }
                EVENT_OBJECT_DESTROY => {
                    self.moving.remove(&handle);
                    self.pending.remove(&handle);
                    self.refused.remove(&handle);
                }
                EVENT_OBJECT_SHOW
                | EVENT_OBJECT_LOCATIONCHANGE
                | EVENT_SYSTEM_FOREGROUND
                | EVENT_SYSTEM_MINIMIZEEND => {
                    changed.insert(handle);
                }
                _ => {}
            }
        }
        let completed: Vec<usize> = self
            .pending
            .iter()
            .filter_map(|(handle, started)| {
                (started.elapsed() >= Duration::from_millis(250)).then_some(*handle)
            })
            .collect();
        for handle in completed {
            self.pending.remove(&handle);
            let window = handle as HWND;
            if self.eligible(window)
                && visible_bounds(window).is_ok_and(|value| !contains(self.target, value))
            {
                self.warn(handle);
            }
        }
        for handle in changed {
            self.constrain(handle as HWND);
        }
        Ok(())
    }
}
