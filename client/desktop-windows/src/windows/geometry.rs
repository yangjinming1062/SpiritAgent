use super::*;

pub(crate) fn window_on_display(window: HWND, display: Bounds) -> Result<bool> {
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


pub(crate) fn rectangle(window: HWND) -> Result<Bounds> {
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

pub(crate) fn monitor_bounds(requested: Bounds) -> Result<Bounds> {
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
