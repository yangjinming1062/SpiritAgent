use super::*;

pub(super) struct DesktopLayers {
    pub(super) icons: Identity,
    view: Identity,
    list: Identity,
    wallpaper: Identity,
    pub(super) background_parent: Identity,
    pub(super) layered: bool,
}

impl DesktopLayers {
    fn existing() -> Result<Option<Self>> {
        let icons = worker()?;
        let view = Identity::capture(child(icons.window(), "SHELLDLL_DefView"))?;
        let list = Identity::capture(child(view.window(), "SysListView32"))?;
        let layered = unsafe {
            GetWindowLongPtrW(icons.window(), GWL_EXSTYLE) as u32 & WS_EX_NOREDIRECTIONBITMAP != 0
                && GetWindowLongPtrW(view.window(), GWL_EXSTYLE) as u32 & WS_EX_LAYERED != 0
        };
        let wallpaper = if layered {
            // 分层样式不代表 DefView 已透明；壁纸 WorkerW 分离后才可在图标下绘制。
            let candidate = child(icons.window(), "WorkerW");
            if candidate.is_null() || unsafe { IsWindowVisible(candidate) } == 0 {
                return Ok(None);
            }
            Identity::capture(candidate)?
        } else {
            let mut candidate = unsafe { GetWindow(icons.window(), GW_HWNDNEXT) };
            let mut found = None;
            while !candidate.is_null() {
                if window_pid(candidate) == icons.pid
                    && class_name(candidate) == "WorkerW"
                    && unsafe { IsWindowVisible(candidate) } != 0
                    && child(candidate, "SHELLDLL_DefView").is_null()
                {
                    found = Some(Identity::capture(candidate)?);
                    break;
                }
                candidate = unsafe { GetWindow(candidate, GW_HWNDNEXT) };
            }
            let Some(parent) = found else {
                return Ok(None);
            };
            parent
        };
        let background_parent = if layered { icons.clone() } else { wallpaper.clone() };
        let layers = Self {
            icons,
            view,
            list,
            wallpaper,
            background_parent,
            layered,
        };
        if !layers.valid() {
            return Err("Explorer desktop layers changed during discovery".into());
        }
        Ok(Some(layers))
    }

    pub(super) fn discover(allow_create: bool) -> Result<Self> {
        if let Some(layers) = Self::existing()? {
            return Ok(layers);
        }
        if !allow_create {
            return Err("Explorer did not expose a wallpaper layer behind its icons".into());
        }
        let progman = unsafe { FindWindowW(wide("Progman").as_ptr(), null()) };
        let shell = unsafe { GetShellWindow() };
        if progman.is_null()
            || shell.is_null()
            || window_pid(progman) != window_pid(shell)
            || !is_explorer(window_pid(progman))
        {
            return Err("Explorer changed before wallpaper layer creation".into());
        }
        let mut response = 0;
        // 壁纸承载层缺失时请求 Explorer 创建；调用只发生在 guardian 就绪的事务内。
        if unsafe {
            SendMessageTimeoutW(
                progman,
                0x052C,
                0xD,
                1,
                SMTO_ABORTIFHUNG,
                1_000,
                &mut response,
            )
        } == 0
        {
            return Err(failure("prepare Explorer wallpaper layer"));
        }
        let deadline = Instant::now() + Duration::from_secs(1);
        loop {
            if let Some(layers) = Self::existing()? {
                return Ok(layers);
            }
            if Instant::now() >= deadline {
                return Err("Explorer wallpaper layer preparation timed out".into());
            }
            thread::sleep(Duration::from_millis(25));
        }
    }

    pub(super) fn valid(&self) -> bool {
        if !self.icons.valid()
            || !self.view.valid()
            || !self.list.valid()
            || !self.wallpaper.valid()
            || !self.background_parent.valid()
            || self.view.pid != self.icons.pid
            || self.list.pid != self.icons.pid
            || self.wallpaper.pid != self.icons.pid
            || self.background_parent.pid != self.icons.pid
            || unsafe { GetParent(self.view.window()) } != self.icons.window()
            || unsafe { GetParent(self.list.window()) } != self.view.window()
            || child(self.icons.window(), "SHELLDLL_DefView") != self.view.window()
            || child(self.view.window(), "SysListView32") != self.list.window()
        {
            return false;
        }
        if self.layered {
            let mut next = unsafe { GetWindow(self.view.window(), GW_HWNDNEXT) };
            while !next.is_null() && next != self.wallpaper.window() {
                next = unsafe { GetWindow(next, GW_HWNDNEXT) };
            }
            return self.background_parent.window() == self.icons.window()
                && unsafe { GetParent(self.wallpaper.window()) } == self.icons.window()
                && child(self.icons.window(), "WorkerW") == self.wallpaper.window()
                && unsafe { IsWindowVisible(self.wallpaper.window()) } != 0
                && next == self.wallpaper.window()
                && unsafe {
                    GetWindowLongPtrW(self.icons.window(), GWL_EXSTYLE) as u32
                        & WS_EX_NOREDIRECTIONBITMAP
                        != 0
                        && GetWindowLongPtrW(self.view.window(), GWL_EXSTYLE) as u32 & WS_EX_LAYERED
                            != 0
                };
        }
        if !unsafe { GetParent(self.background_parent.window()) }.is_null()
            || !child(self.background_parent.window(), "SHELLDLL_DefView").is_null()
        {
            return false;
        }
        let mut next = unsafe { GetWindow(self.icons.window(), GW_HWNDNEXT) };
        while !next.is_null() {
            if next == self.background_parent.window() {
                return true;
            }
            next = unsafe { GetWindow(next, GW_HWNDNEXT) };
        }
        false
    }

    pub(super) fn order(&self, windows: &[RestoreWindow]) -> Result<bool> {
        if !self.valid() {
            return Err("Explorer icon or wallpaper layer changed before ordering".into());
        }
        let mut previous = if self.layered { self.view.window() } else { HWND_TOP };
        let mut changed = false;
        for saved in windows.iter().filter(|saved| saved.role == WindowRole::Background) {
            let window = saved.identity.window();
            if !saved.identity.valid()
                || unsafe { GetParent(window) } != self.background_parent.window()
            {
                return Err("desktop background left its wallpaper layer".into());
            }
            if changed || unsafe { GetWindow(window, GW_HWNDPREV) } != previous {
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
                    return Err(failure("order desktop wallpaper below icons"));
                }
                changed = true;
            }
            previous = window;
        }
        Ok(changed)
    }

    pub(super) fn verify(&self, windows: &[RestoreWindow], target: Bounds) -> Result<()> {
        let saved = windows
            .iter()
            .find(|saved| saved.role == WindowRole::Background)
            .ok_or("desktop background is missing")?;
        let window = saved.identity.window();
        if !self.valid()
            || !saved.identity.valid()
            || unsafe { GetParent(window) } != self.background_parent.window()
            || !rectangle(window)?.within_tolerance(target, 2)
            || unsafe { GetWindowLongPtrW(window, GWL_EXSTYLE) as u32 & WS_EX_TRANSPARENT == 0 }
        {
            return Err("desktop wallpaper geometry, layer or mouse passthrough changed".into());
        }
        let mut client: RECT = unsafe { zeroed() };
        if unsafe { GetClientRect(window, &mut client) } == 0
            || client.right - client.left != target.width
            || client.bottom - client.top != target.height
        {
            return Err("desktop background client bounds changed".into());
        }
        if unsafe { IsWindowVisible(self.view.window()) == 0 || IsWindowEnabled(self.view.window()) == 0 }
        {
            return Ok(());
        }
        for (x, y) in [
            (target.x + 3, target.y + 3),
            (target.x + target.width / 2, target.y + target.height / 2),
            (target.x + target.width - 4, target.y + target.height - 4),
        ] {
            let mut point = POINT { x, y };
            if unsafe { ScreenToClient(self.icons.window(), &mut point) } == 0 {
                return Err(failure("verify native desktop input coordinates"));
            }
            let hit = unsafe {
                ChildWindowFromPointEx(
                    self.icons.window(),
                    point,
                    CWP_SKIPINVISIBLE | CWP_SKIPDISABLED | CWP_SKIPTRANSPARENT,
                )
            };
            if hit != self.view.window() {
                return Err("Explorer icon view no longer receives native desktop input".into());
            }
        }
        Ok(())
    }
}
