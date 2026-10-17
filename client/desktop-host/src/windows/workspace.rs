use super::*;
use std::collections::{HashMap, HashSet};
use windows_sys::Win32::Graphics::Dwm::{
    DWMWA_CLOAKED, DWMWA_EXTENDED_FRAME_BOUNDS, DwmGetWindowAttribute,
};
use windows_sys::Win32::Graphics::Gdi::{
    EnumDisplayMonitors, HDC, HMONITOR, MONITOR_DEFAULTTONEAREST, MONITORINFOEXW, MonitorFromWindow,
};

mod appbar;
pub(super) use appbar::RestoreAppBar;
use appbar::AppBar;

mod relocation;
pub(super) use relocation::{FocusOutcome, FocusRequest, FocusReservation, WindowRelocation, WindowRestoration};
use relocation::{NativeStep, Operations, RelocationPlan, WindowConstraint};

pub(super) type WindowDispatch<T> = Result<Option<T>>;

struct MonitorSnapshot {
    device: String,
    monitor: Bounds,
    work_area: Bounds,
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
        return Err(failure("enumerate desktop monitors"));
    }
    for monitor in monitors {
        let value = info(monitor)?;
        if device(&value) == name {
            return Ok(Some(value));
        }
    }
    Ok(None)
}

impl MonitorSnapshot {
    fn validate(&self) -> Result<()> {
        if self.device.is_empty()
            || self.device.len() > 128
            || !self.monitor.valid()
            || !self.work_area.valid()
            || !contains(self.monitor, self.work_area)
        {
            return Err("invalid desktop monitor snapshot".into());
        }
        Ok(())
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
    display: MonitorSnapshot,
    target: Bounds,
    appbar: Option<AppBar>,
    parent_pid: u32,
    enabled: bool,
    moving: HashSet<usize>,
    pending: HashMap<usize, WindowConstraint>,
    refused: HashSet<usize>,
    deferred: HashSet<usize>,
    operations: Operations,
}

impl Workspace {
    pub(super) fn monitor(&self) -> Result<HMONITOR> {
        let rectangle = rect(self.display.monitor);
        let monitor = unsafe { MonitorFromRect(&rectangle, MONITOR_DEFAULTTONULL) };
        if monitor.is_null() || device(&info(monitor)?) != self.display.device {
            return Err("桌面屏幕已断开，请重新选择屏幕。".into());
        }
        Ok(monitor)
    }

    pub(super) fn on_display(&self, window: HWND) -> bool {
        // MonitorFromWindow 对最小化窗口使用最小化前的矩形。
        info(unsafe { MonitorFromWindow(window, MONITOR_DEFAULTTONEAREST) })
            .is_ok_and(|value| device(&value) == self.display.device)
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

    pub(super) fn move_to_display(&self, window: HWND) -> WindowDispatch<WindowRelocation> {
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
            target.x -= self.target.x - self.display.monitor.x;
            target.y -= self.target.y - self.display.monitor.y;
            transition.x -= self.target.x - self.display.monitor.x;
            transition.y -= self.target.y - self.display.monitor.y;
        }
        let mut steps = Vec::new();
        // 最大化窗口仅修改还原矩形不会换屏；先迁移当前外框，再设置还原位置。
        if was_maximized && unsafe { IsIconic(window) } == 0 {
            let outer = rectangle(window)?;
            let visual = visible_bounds(window)?;
            steps.push(NativeStep::Position(Bounds {
                x: self.target.x + outer.x - visual.x,
                y: self.target.y + outer.y - visual.y,
                width: self.target.width + outer.width - visual.width,
                height: self.target.height + outer.height - visual.height,
            }));
        }
        placement.rcNormalPosition = rect(transition);
        placement.flags = 0;
        placement.showCmd = if was_maximized {
            SW_SHOWMAXIMIZED
        } else {
            SW_SHOWNOACTIVATE
        } as u32;
        steps.push(NativeStep::Placement(placement));
        WindowRelocation::start(RelocationPlan {
            identity,
            target,
            work_area: self.target,
            maximized: was_maximized,
            destination_dpi,
        }, steps, self)
    }

    pub(super) fn restore_window(&self, window: HWND) -> WindowDispatch<WindowRestoration> {
        if !self.enabled {
            return Err("桌面探测模式不能还原程序窗口。".into());
        }
        let identity = Identity::capture(window)?;
        if !self.on_display(window) {
            return Err("程序窗口已离开所选屏幕，停止还原。".into());
        }
        let mut placement: WINDOWPLACEMENT = unsafe { zeroed() };
        placement.length = size_of::<WINDOWPLACEMENT>() as u32;
        if unsafe { GetWindowPlacement(window, &mut placement) } == 0 {
            return Err(failure("读取程序窗口还原位置失败"));
        }
        let maximized = placement.showCmd == SW_SHOWMAXIMIZED as u32
            || (unsafe { IsIconic(window) } != 0 && placement.flags & WPF_RESTORETOMAXIMIZED != 0);
        placement.flags = 0;
        placement.showCmd = if maximized { SW_SHOWMAXIMIZED } else { SW_SHOWNOACTIVATE } as u32;
        WindowRestoration::start(identity, placement, maximized, self)
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
        if !contains(display, requested)
            || requested.x != display.x
            || requested.y != display.y
            || requested.width != display.width
        {
            return Err(format!(
                "desktop work area must reserve only the bottom edge: {requested:?}, display {display:?}"
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
        let display = MonitorSnapshot {
            device: device(&value),
            monitor: bounds(value.monitorInfo.rcMonitor),
            work_area: bounds(value.monitorInfo.rcWork),
        };
        display.validate()?;
        if !contains(display.monitor, target) {
            return Err("desktop monitor changed during work area validation".into());
        }
        let appbar = if enabled {
            Some(AppBar::prepare(&display, target.y + target.height)?)
        } else {
            None
        };
        let target = if enabled { target } else { display.work_area };
        Ok(Self {
            display,
            target,
            appbar,
            parent_pid,
            enabled,
            moving: HashSet::new(),
            pending: HashMap::new(),
            refused: HashSet::new(),
            deferred: HashSet::new(),
            operations: Operations::default(),
        })
    }

    pub(super) fn activate(&mut self) -> Result<()> {
        self.ensure_work_area()?;
        for window in top_windows()? {
            self.constrain(window);
        }
        Ok(())
    }

    pub(super) fn appbar_restore(&self) -> Option<RestoreAppBar> {
        self.appbar.as_ref().map(AppBar::restore_record)
    }

    pub(super) fn current(&self) -> Bounds {
        self.target
    }

    pub(super) fn user_moving(&self, window: HWND) -> bool {
        self.moving.contains(&(window as usize))
    }

    pub(super) fn bind_restoration(&self, handle: HANDLE) -> Result<()> {
        self.operations.bind_restoration(handle)
    }

    pub(super) fn reserve_focus(&self, target: &Identity) -> WindowDispatch<FocusReservation> {
        FocusReservation::reserve(target, self)
    }

    pub(super) fn cancel_window_operations(&mut self, window: HWND) {
        self.operations.cancel_window(window);
        self.pending.remove(&(window as usize));
        self.deferred.remove(&(window as usize));
    }

    pub(super) fn cancel_operations(&mut self) {
        self.operations.cancel_all();
        self.pending.clear();
        self.deferred.clear();
    }

    fn ensure_work_area(&mut self) -> Result<()> {
        let current = find_monitor(&self.display.device)?.ok_or("desktop monitor was removed")?;
        if bounds(current.monitorInfo.rcMonitor) != self.display.monitor {
            return Err("desktop monitor geometry changed".into());
        }
        if !self.enabled {
            self.target = bounds(current.monitorInfo.rcWork);
            return Ok(());
        }
        self.target = self
            .appbar
            .as_mut()
            .ok_or("desktop AppBar is missing")?
            .ensure(&current)?;
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
        if device(&value) != self.display.device {
            return false;
        }
        let Ok(area) = visible_bounds(window) else {
            return false;
        };
        let monitor = self.display.monitor;
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
            .is_ok_and(|value| device(&value) == self.display.device)
    }

    fn warn(&mut self, handle: usize, reason: String) {
        self.pending.remove(&handle);
        if self.refused.insert(handle) {
            emit(
                json!({ "event": "warning", "reason": format!("部分程序窗口未能调整到桌面工作区: {reason}") }),
            );
        }
    }

    fn constrain(&mut self, window: HWND) {
        let handle = window as usize;
        self.deferred.remove(&handle);
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
        if self.operations.active(window) {
            self.deferred.insert(handle);
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
        let result = Identity::capture(window).and_then(|identity| {
            let Some(task) = self.operations.start(&identity, vec![NativeStep::Position(Bounds {
                x: x + outer.x - visual.x,
                y: y + outer.y - visual.y,
                width: width + outer.width - visual.width,
                height: height + outer.height - visual.height,
            })])? else { return Ok(None); };
            Ok(Some(WindowConstraint { identity, task }))
        });
        match result {
            Ok(Some(pending)) => { self.pending.insert(handle, pending); },
            Ok(None) => { self.deferred.insert(handle); },
            Err(error) => self.warn(handle, error),
        }
    }

    pub(super) fn poll(&mut self, events: &[(u32, usize)]) -> Result<()> {
        self.operations.reap();
        let previous = self.target;
        self.ensure_work_area()?;
        let mut changed = HashSet::new();
        if self.target != previous {
            self.cancel_operations();
            self.refused.clear();
            changed.extend(top_windows()?.into_iter().map(|window| window as usize));
        }
        for &(event, handle) in events {
            match event {
                0 => {
                    self.moving.clear();
                    self.cancel_operations();
                    self.refused.clear();
                    changed.extend(top_windows()?.into_iter().map(|window| window as usize));
                }
                EVENT_SYSTEM_MOVESIZESTART => {
                    self.moving.insert(handle);
                    self.cancel_window_operations(handle as HWND);
                    self.refused.remove(&handle);
                }
                EVENT_SYSTEM_MOVESIZEEND => {
                    self.moving.remove(&handle);
                    changed.insert(handle);
                }
                EVENT_OBJECT_DESTROY => {
                    self.moving.remove(&handle);
                    self.cancel_window_operations(handle as HWND);
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
        for handle in self.pending.keys().copied().collect::<Vec<_>>() {
            let Some(mut pending) = self.pending.remove(&handle) else { continue; };
            if pending.task.cancelled() || !pending.identity.valid() {
                continue;
            }
            match pending.task.poll(&self.operations) {
                Ok(true) if self.placed(pending.identity.window()) => {},
                Ok(_) => { self.pending.insert(handle, pending); },
                Err(error) => self.warn(handle, error),
            }
        }
        changed.extend(self.deferred.drain());
        for handle in changed {
            self.constrain(handle as HWND);
        }
        Ok(())
    }
}
