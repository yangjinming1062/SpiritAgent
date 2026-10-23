use super::*;

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct RestoreTrayPlacement {
    pub identity: Identity,
    pub bounds: Bounds,
    pub monitor: Bounds,
    topmost: bool,
    dpi: u32,
}

impl RestoreTrayPlacement {
    pub(super) fn capture(identity: &Identity, monitor: Bounds) -> Result<Self> {
        if !identity.valid() || !is_explorer(identity.pid) || !tray::on_display(identity.window(), monitor)? {
            return Err("system tray placement owner changed".into());
        }
        let dpi = unsafe { GetDpiForWindow(identity.window()) };
        if dpi == 0 {
            return Err(failure("read original system tray DPI"));
        }
        Ok(Self {
            identity: identity.clone(),
            bounds: rectangle(identity.window())?,
            monitor,
            topmost: unsafe { GetWindowLongPtrW(identity.window(), GWL_EXSTYLE) } as u32 & WS_EX_TOPMOST != 0,
            dpi,
        })
    }

    pub(super) fn validate(&self, journal: &Journal) -> Result<()> {
        if !self.bounds.valid() || self.monitor != journal.desktop_bounds
            || !(48..=960).contains(&self.dpi)
            || !journal.shell.iter().any(|identity| identity.same_window(&self.identity)) {
            return Err("invalid system tray recovery placement".into());
        }
        Ok(())
    }

    pub(super) fn restore(&self) -> Result<()> {
        if !self.identity.valid() || !is_explorer(self.identity.pid) {
            return Ok(());
        }
        let window = self.identity.window();
        let current = rectangle(window)?;
        let topmost = unsafe { GetWindowLongPtrW(window, GWL_EXSTYLE) } as u32 & WS_EX_TOPMOST != 0;
        // 屏幕或 DPI 改变时由 Explorer 重新协商，不能强制回写旧像素高度。
        let original_display = monitor_bounds(self.monitor).is_ok()
            && unsafe { GetDpiForWindow(window) } == self.dpi;
        if original_display && current == self.bounds && topmost == self.topmost {
            return Ok(());
        }
        if unsafe { SetWindowPos(
            window,
            if self.topmost { HWND_TOPMOST } else { HWND_NOTOPMOST },
            self.bounds.x, self.bounds.y, self.bounds.width, self.bounds.height,
            SWP_NOACTIVATE | SWP_NOOWNERZORDER | if original_display { SWP_NOSENDCHANGING } else { 0 },
        ) } == 0 {
            return Err(failure("restore native system tray placement"));
        }
        let actual = rectangle(window)?;
        let actual_topmost = unsafe { GetWindowLongPtrW(window, GWL_EXSTYLE) } as u32 & WS_EX_TOPMOST != 0;
        if (original_display && !actual.within_tolerance(self.bounds, 2)) || actual_topmost != self.topmost {
            return Err("native system tray placement did not restore".into());
        }
        Ok(())
    }
}

pub(super) fn intersection(first: Bounds, second: Bounds) -> Option<Bounds> {
    let x = first.x.max(second.x);
    let y = first.y.max(second.y);
    let right = (first.x + first.width).min(second.x + second.width);
    let bottom = (first.y + first.height).min(second.y + second.height);
    (right > x && bottom > y).then_some(Bounds { x, y, width: right - x, height: bottom - y })
}
