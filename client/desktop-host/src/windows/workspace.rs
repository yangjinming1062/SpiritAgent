use super::*;
use std::collections::{HashMap, HashSet};
use windows_sys::Win32::Graphics::Dwm::{
    DWMWA_CLOAKED, DWMWA_EXTENDED_FRAME_BOUNDS, DwmGetWindowAttribute,
};
use windows_sys::Win32::Graphics::Gdi::{
    EnumDisplayMonitors, HDC, HMONITOR, MONITOR_DEFAULTTONEAREST, MONITORINFOEXW, MonitorFromWindow,
};

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

struct DpiProbe(HWND);

impl Drop for DpiProbe {
    fn drop(&mut self) {
        unsafe {
            DestroyWindow(self.0);
        }
    }
}

fn monitor_dpi(monitor: HMONITOR) -> Result<u32> {
    if unsafe {
        AreDpiAwarenessContextsEqual(
            GetThreadDpiAwarenessContext(),
            DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
        )
    } == 0
    {
        return Err("读取屏幕缩放比例需要按显示器感知的桌面宿主。".into());
    }
    let area = bounds(info(monitor)?.monitorInfo.rcMonitor);
    // GetDpiForMonitor 不支持 PMv2 调用方；自有隐藏窗口可读取屏幕 DPI，且不受程序自身感知级别影响。
    let window = unsafe {
        CreateWindowExW(
            WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            wide("STATIC").as_ptr(),
            wide("").as_ptr(),
            WS_POPUP,
            area.x,
            area.y,
            1,
            1,
            null_mut(),
            null_mut(),
            null_mut(),
            null(),
        )
    };
    if window.is_null() {
        return Err(failure("读取显示器缩放比例失败"));
    }
    let probe = DpiProbe(window);
    if unsafe { IsWindowVisible(probe.0) } != 0
        || unsafe { MonitorFromWindow(probe.0, MONITOR_DEFAULTTONULL) } != monitor
    {
        return Err("读取缩放比例时显示器已改变。".into());
    }
    let dpi = unsafe { GetDpiForWindow(probe.0) };
    if dpi == 0 {
        return Err(failure("读取显示器缩放比例失败"));
    }
    Ok(dpi)
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
    moving: HashSet<usize>,
    pending: HashMap<usize, Instant>,
    refused: HashSet<usize>,
}

pub(super) struct WindowRelocation {
    identity: Identity,
    target: Bounds,
    maximized: bool,
    destination_dpi: u32,
    observed: Option<(Bounds, u32, Instant)>,
    corrected: bool,
}

impl WindowRelocation {
    pub(super) fn poll(&mut self, workspace: &Workspace) -> Result<bool> {
        if !self.identity.valid() {
            return Err("程序窗口已关闭或改变，无法完成移动。".into());
        }
        let window = self.identity.window();
        if !workspace.on_display(window) || unsafe { IsIconic(window) } != 0 {
            self.observed = None;
            return Ok(false);
        }
        let dpi = unsafe { GetDpiForWindow(window) };
        let awareness = unsafe {
            GetAwarenessFromDpiAwarenessContext(GetWindowDpiAwarenessContext(window))
        };
        if awareness == DPI_AWARENESS_PER_MONITOR_AWARE && dpi != self.destination_dpi {
            self.observed = None;
            return Ok(false);
        }
        let mut placement: WINDOWPLACEMENT = unsafe { zeroed() };
        placement.length = size_of::<WINDOWPLACEMENT>() as u32;
        if unsafe { GetWindowPlacement(window, &mut placement) } == 0 {
            return Err(failure("复核程序窗口位置失败"));
        }
        if (unsafe { IsZoomed(window) } != 0) != self.maximized {
            return Err("程序窗口状态已改变，未继续调整位置。".into());
        }
        let current = bounds(placement.rcNormalPosition);
        if self
            .observed
            .as_ref()
            .is_none_or(|(last, last_dpi, _)| *last != current || *last_dpi != dpi)
        {
            self.observed = Some((current, dpi, Instant::now()));
            return Ok(false);
        }
        if self
            .observed
            .as_ref()
            .is_some_and(|(_, _, started)| started.elapsed() < Duration::from_millis(100))
        {
            return Ok(false);
        }
        if current.within_tolerance(self.target, 2) && workspace.placed(window) {
            return Ok(true);
        }
        if self.corrected {
            return Ok(false);
        }
        // 目标程序已处理首次 WM_DPICHANGED；在相同 DPI 上校正最终物理尺寸，避免重复缩放。
        placement.rcNormalPosition = rect(self.target);
        placement.flags = WPF_ASYNCWINDOWPLACEMENT;
        placement.showCmd = SW_SHOWNA as u32;
        if unsafe { SetWindowPlacement(window, &placement) } == 0 {
            return Err(failure("程序拒绝调整移动后的窗口尺寸"));
        }
        self.corrected = true;
        self.observed = None;
        Ok(false)
    }
}

impl Workspace {
    pub(super) fn monitor(&self) -> Result<HMONITOR> {
        let rectangle = rect(self.original.monitor);
        let monitor = unsafe { MonitorFromRect(&rectangle, MONITOR_DEFAULTTONULL) };
        if monitor.is_null() || device(&info(monitor)?) != self.original.device {
            return Err("桌面屏幕已断开，请重新选择屏幕。".into());
        }
        Ok(monitor)
    }

    pub(super) fn on_display(&self, window: HWND) -> bool {
        // MonitorFromWindow 对最小化窗口使用最小化前的矩形。
        info(unsafe { MonitorFromWindow(window, MONITOR_DEFAULTTONEAREST) })
            .is_ok_and(|value| device(&value) == self.original.device)
    }

    pub(super) fn placed(&self, window: HWND) -> bool {
        self.on_display(window)
            && unsafe { IsIconic(window) } == 0
            && visible_bounds(window).is_ok_and(|area| {
                contains(
                    Bounds {
                        x: self.target.x - 2,
                        y: self.target.y - 2,
                        width: self.target.width + 4,
                        height: self.target.height + 4,
                    },
                    area,
                )
            })
    }

    pub(super) fn move_to_display(&self, window: HWND) -> Result<WindowRelocation> {
        if !self.enabled {
            return Err("桌面探测模式不能移动程序窗口。".into());
        }
        if unsafe { IsWindow(window) } == 0 {
            return Err("窗口已关闭，请重新选择程序。".into());
        }
        let identity = Identity::capture(window)?;
        let source_monitor = unsafe { MonitorFromWindow(window, MONITOR_DEFAULTTONEAREST) };
        let source = info(source_monitor)?;
        let destination = self.monitor()?;
        let mut placement: WINDOWPLACEMENT = unsafe { zeroed() };
        placement.length = size_of::<WINDOWPLACEMENT>() as u32;
        if unsafe { GetWindowPlacement(window, &mut placement) } == 0 {
            return Err(failure("读取程序窗口位置失败"));
        }
        let was_maximized = placement.showCmd == SW_SHOWMAXIMIZED as u32
            || (unsafe { IsIconic(window) } != 0 && placement.flags & WPF_RESTORETOMAXIMIZED != 0);
        let source_area = bounds(source.monitorInfo.rcMonitor);
        let source_work = bounds(source.monitorInfo.rcWork);
        if !was_maximized && unsafe { IsIconic(window) } == 0 {
            let area = visible_bounds(window)?;
            if unsafe { GetWindowLongPtrW(window, GWL_STYLE) as u32 & WS_CAPTION == 0 }
                && area.x <= source_area.x + 2
                && area.y <= source_area.y + 2
                && area.x + area.width >= source_area.x + source_area.width - 2
                && area.y + area.height >= source_area.y + source_area.height - 2
            {
                return Err("该程序正在独占全屏，请先退出全屏再移动。".into());
            }
        }
        let source_dpi = monitor_dpi(source_monitor)?;
        let destination_dpi = monitor_dpi(destination)?;
        let scale = |value: i32| -> i32 {
            (i64::from(value) * i64::from(destination_dpi) / i64::from(source_dpi))
                .clamp(i64::from(i32::MIN), i64::from(i32::MAX)) as i32
        };
        // WINDOWPLACEMENT 普通窗口使用工作区坐标，工具窗口使用屏幕坐标。
        let tool = unsafe { GetWindowLongPtrW(window, GWL_EXSTYLE) as u32 & WS_EX_TOOLWINDOW != 0 };
        let mut normal = bounds(placement.rcNormalPosition);
        if !tool {
            normal.x += source_work.x - source_area.x;
            normal.y += source_work.y - source_area.y;
        }
        let width = scale(normal.width).clamp(1, self.target.width);
        let height = scale(normal.height).clamp(1, self.target.height);
        let x = (self.target.x + scale(normal.x - source_work.x))
            .clamp(self.target.x, self.target.x + self.target.width - width);
        let y = (self.target.y + scale(normal.y - source_work.y))
            .clamp(self.target.y, self.target.y + self.target.height - height);
        let mut target = Bounds {
            x,
            y,
            width,
            height,
        };
        // 首次只进入目标显示器；程序可能按 WM_DPICHANGED 建议矩形自行缩放。
        let mut transition = Bounds {
            x,
            y,
            width: normal.width.clamp(1, self.target.width),
            height: normal.height.clamp(1, self.target.height),
        };
        transition.x = transition.x.clamp(
            self.target.x,
            self.target.x + self.target.width - transition.width,
        );
        transition.y = transition.y.clamp(
            self.target.y,
            self.target.y + self.target.height - transition.height,
        );
        if !tool {
            target.x -= self.target.x - self.original.monitor.x;
            target.y -= self.target.y - self.original.monitor.y;
            transition.x -= self.target.x - self.original.monitor.x;
            transition.y -= self.target.y - self.original.monitor.y;
        }
        placement.rcNormalPosition = rect(transition);
        placement.flags = WPF_ASYNCWINDOWPLACEMENT;
        placement.showCmd = if was_maximized {
            SW_SHOWMAXIMIZED
        } else {
            SW_SHOWNOACTIVATE
        } as u32;
        if unsafe { SetWindowPlacement(window, &placement) } == 0 {
            return Err(failure("Windows 拒绝移动程序窗口"));
        }
        Ok(WindowRelocation {
            identity,
            target,
            maximized: was_maximized,
            destination_dpi,
            observed: None,
            corrected: false,
        })
    }

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
            moving: HashSet::new(),
            pending: HashMap::new(),
            refused: HashSet::new(),
        })
    }

    pub(super) fn activate(&mut self) -> Result<()> {
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

    pub(super) fn poll(&mut self, events: &[(u32, usize)]) -> Result<()> {
        self.ensure_work_area()?;
        let mut changed = HashSet::new();
        for &(event, handle) in events {
            match event {
                0 => {
                    self.moving.clear();
                    self.pending.clear();
                    self.refused.clear();
                    changed.extend(top_windows()?.into_iter().map(|window| window as usize));
                }
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
