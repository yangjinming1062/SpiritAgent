use super::geometry::{monitor_bounds, rectangle};
use super::identity::{Identity, actual_parent};
use super::journal::{Journal, JournalPhase, RestoringSignal, require_session};
use super::*;

pub(crate) struct Session {
    pub(crate) journal: Journal,
    pub(crate) layers: DesktopLayers,
    pub(crate) guardian: Child,
    pub(crate) path: PathBuf,
    pub(crate) last_heartbeat: Instant,
    pub(crate) lease_number: u64,
    pub(crate) foreground: Option<(bool, bool, bool)>,
    pub(crate) overlay_geometry_pending: Option<Instant>,
    pub(crate) child_order_pending: Option<Instant>,
    pub(crate) last_work_area: Option<Bounds>,
    pub(crate) workspace: Workspace,
    pub(crate) events: WinEvents,
    pub(crate) applications: Applications,
    pub(crate) takeover: bool,
    pub(crate) last_shell_scan: Instant,
    pub(crate) stopped: bool,
    pub(crate) restoring: RestoringSignal,
    pub(crate) tray: Tray,
    pub(crate) last_tray: Option<tray::SystemTray>,
    pub(crate) tray_style: Option<TrayStyle>,
    pub(crate) palette: Palette,
}

pub(crate) struct InputAttachment {
    pub(crate) source: u32,
    pub(crate) target: u32,
}

impl Drop for InputAttachment {
    fn drop(&mut self) {
        if unsafe { AttachThreadInput(self.source, self.target, 0) } == 0 {
            emit(json!({ "event": "failure", "reason": failure("detach desktop input thread") }));
            // 线程附着不能遗留在循环内；退出自有 host，让独立 guardian 恢复。
            std::process::exit(1);
        }
    }
}

impl Session {
    pub(crate) fn start(
        path: &Path,
        parent_pid: u32,
        takeover: bool,
        palette: Palette,
        specs: &[WindowSpec],
        work_area: Bounds,
    ) -> Result<Self> {
        if actual_parent()? != parent_pid {
            return Err("desktop windows must belong to the helper's real parent process".into());
        }
        let events = WinEvents::start()?;
        if specs.len() != 2 {
            return Err("desktop requires one background and one overlay".into());
        }
        if specs
            .iter()
            .filter(|w| w.role == WindowRole::Overlay)
            .count()
            != 1
            || specs
                .iter()
                .filter(|w| w.role == WindowRole::Background)
                .count()
                != 1
        {
            return Err("desktop requires one background and one overlay".into());
        }
        let overlay = specs
            .iter()
            .find(|spec| spec.role == WindowRole::Overlay)
            .ok_or("desktop overlay is missing")?;
        if specs.iter().any(|spec| spec.bounds != overlay.bounds) {
            return Err("desktop windows must share the interaction display".into());
        }
        if !overlay.bounds.valid() {
            return Err("desktop physical bounds are out of range".into());
        }
        let target = monitor_bounds(overlay.bounds)?;
        let mut workspace = Workspace::prepare(work_area, overlay.bounds, parent_pid, takeover)?;
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
            let window = value as usize as HWND;
            let identity = Identity::capture(window)?;
            if identity.pid != parent_pid || identity.created != created(parent.0)? {
                return Err("desktop window does not belong to Electron main".into());
            }
            if !unsafe { GetParent(window) }.is_null() {
                return Err("desktop attachment requires an independent top-level window".into());
            }
            let non_rude = spec.role.is_top_level().then(|| {
                (unsafe { GetPropW(window, wide(NON_RUDE_PROPERTY).as_ptr()) }) as usize as u64
            });
            windows.push(RestoreWindow {
                identity,
                parent: 0,
                style: unsafe { GetWindowLongPtrW(window, GWL_STYLE) } as i64,
                ex_style: unsafe { GetWindowLongPtrW(window, GWL_EXSTYLE) } as i64,
                bounds: rectangle(window)?,
                role: spec.role,
                non_rude,
            });
        }
        worker()?;
        let mut journal = Journal {
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
            shell: Vec::new(),
            windows,
            appbar: workspace.appbar_restore(),
            desktop_bounds: target,
            regions: Vec::new(),
            tray_style: None,
            tray_placements: Vec::new(),
        };
        if takeover {
            for identity in shell_windows(target)? {
                journal.capture_shell(identity)?;
            }
        }
        let mut tray = Tray::new(&journal)?;
        let mut tray_style = TrayStyle::prepare(&journal, palette)?;
        journal.tray_style = tray_style.as_ref().map(TrayStyle::record);
        let restoring = RestoringSignal::new(&journal)?;
        workspace.bind_restoration(restoring.handle.0)?;
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
            require_session(path, &journal.session, &restoring)?;
            workspace.monitor()?;
            monitor_bounds(target)?;
            journal.phase = JournalPhase::Attaching;
            journal.save(path)?;
            let layers = DesktopLayers::discover(takeover)?;
            if !layers.icons.visible || !layers.background_parent().visible {
                return Err("Explorer desktop layer is not visible".into());
            }
            for saved in &journal.windows {
                if saved.role.is_top_level() {
                    set_top_level(saved, target, false)?;
                } else {
                    attach(saved, &layers, target)?;
                }
            }
            if takeover {
                for item in &journal.shell {
                    if !item.valid() || !tray::on_display(item.window(), target)? {
                        return Err("Explorer changed before shell takeover".into());
                    }
                }
                if let Some(style) = &mut tray_style {
                    style.start()?;
                }
                tray.apply(&journal, tray_style.as_ref())?;
                workspace.activate()?;
            }
            layers.order(&journal.windows)?;
            journal.phase = JournalPhase::Active;
            journal.save(path)?;
            Ok(layers)
        })();
        drop(transaction);
        let layers = match attached {
            Ok(layers) => layers,
            Err(error) => {
                let restored = restore(path, Some(&journal.session));
                return Err(finish_failed_start(error, restored, &mut guardian, false));
            }
        };
        let applications = match Applications::new(&journal.session, parent_pid) {
            Ok(applications) => applications,
            Err(error) => {
                let restored = restore(path, Some(&journal.session));
                return Err(finish_failed_start(error, restored, &mut guardian, false));
            }
        };
        emit(json!({ "event": "system_tray", "tray": tray.state }));
        let last_tray = tray.state;
        Ok(Self {
            journal,
            layers,
            guardian,
            path: path.into(),
            last_heartbeat: Instant::now(),
            lease_number: 0,
            foreground: None,
            overlay_geometry_pending: None,
            child_order_pending: None,
            last_work_area: None,
            workspace,
            events,
            applications,
            takeover,
            last_shell_scan: Instant::now(),
            stopped: false,
            restoring,
            tray,
            last_tray,
            tray_style,
            palette,
        })
    }

    pub(crate) fn set_palette(&mut self, palette: Palette) -> Result<()> {
        let _transaction = MutexGuard::restoration()?;
        require_session(&self.path, &self.journal.session, &self.restoring)?;
        self.palette = palette;
        if let Some(style) = &mut self.tray_style {
            style.heartbeat(palette)?;
        }
        Ok(())
    }

    pub(crate) fn set_tray_layout(&mut self, layout: Option<TrayLayout>) -> Result<()> {
        let _transaction = MutexGuard::restoration()?;
        require_session(&self.path, &self.journal.session, &self.restoring)?;
        if !self.takeover {
            return if layout.is_none() { Ok(()) } else { Err("native system tray is not enabled".into()) };
        }
        self.tray.set_layout(layout, self.journal.desktop_bounds)?;
        self.tray.apply(&self.journal, self.tray_style.as_ref())?;
        self.publish_tray();
        Ok(())
    }

    pub(crate) fn publish_tray(&mut self) {
        if self.last_tray != self.tray.state {
            emit(json!({ "event": "system_tray", "tray": self.tray.state }));
            self.last_tray = self.tray.state;
        }
    }

    pub(crate) fn heartbeat(&mut self) -> Result<()> {
        let _transaction = MutexGuard::restoration()?;
        require_session(&self.path, &self.journal.session, &self.restoring)?;
        self.lease_number += 1;
        atomic_write(
            &sidecar(&self.path, ".lease"),
            format!("{}:{}", self.journal.session, self.lease_number).as_bytes(),
        )?;
        self.last_heartbeat = Instant::now();
        Ok(())
    }

    pub(crate) fn refresh_applications(&mut self, force: bool) -> Result<()> {
        let events = self.events.drain();
        let shell_changed = events.iter().any(|(event, handle)| {
            *event == 0
                || self.journal.shell.iter().any(|identity| identity.handle == *handle as u64)
                || ["TrayNotifyWnd", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"]
                    .contains(&class_name(*handle as HWND).as_str())
        });
        {
            let _transaction = MutexGuard::restoration()?;
            require_session(&self.path, &self.journal.session, &self.restoring)?;
            self.require_display()?;
            if self.takeover && shell_changed {
                self.refresh_shell()?;
            }
            let changed = self.layers.order(&self.journal.windows)?;
            let verification = self.layers.verify(&self.journal.windows, self.journal.desktop_bounds);
            if changed || verification.is_err() {
                if self
                    .child_order_pending
                    .get_or_insert_with(Instant::now)
                    .elapsed()
                    >= DESKTOP_LAYER_TIMEOUT
                {
                    return Err(verification.err().unwrap_or_else(|| {
                        "desktop child window ordering did not settle".into()
                    }));
                }
            } else {
                self.child_order_pending = None;
            }
            self.workspace.poll(&events)?;
            let area = self.workspace.current();
            if self.last_work_area != Some(area) {
                emit(json!({ "event": "work_area", "work_area": area }));
                self.last_work_area = Some(area);
            }
            self.applications.poll(&events, force, &self.workspace);
        }
        Ok(())
    }

    pub(crate) fn require_display(&self) -> Result<()> {
        self.workspace.monitor()?;
        monitor_bounds(self.journal.desktop_bounds)?;
        Ok(())
    }

    pub(crate) fn activate_external(&mut self, window_id: &str, allow_remote: bool) -> Result<String> {
        self.refresh_applications(false)?;
        let _transaction = MutexGuard::restoration()?;
        require_session(&self.path, &self.journal.session, &self.restoring)?;
        self.require_external_interaction()?;
        self.applications.activate(window_id, allow_remote, &self.workspace)
    }

    pub(crate) fn close_external(&mut self, window_ids: &[String]) -> Result<()> {
        self.refresh_applications(false)?;
        let _transaction = MutexGuard::restoration()?;
        require_session(&self.path, &self.journal.session, &self.restoring)?;
        self.require_external_interaction()?;
        self.applications.close_windows(window_ids, &self.workspace)
    }

    pub(crate) fn get_jump_list_tasks(&self, target: &str) -> Result<jump_lists::JumpListRead> {
        require_session(&self.path, &self.journal.session, &self.restoring)?;
        let read = jump_lists::read_tasks(target, &self.journal.session);
        require_session(&self.path, &self.journal.session, &self.restoring)?;
        Ok(read)
    }

    pub(crate) fn activate_jump_list_task(
        &mut self,
        target: &str,
        task_id: &str,
        expires_at: u64,
    ) -> Result<Vec<String>> {
        let read = self.get_jump_list_tasks(target)?;
        let task = read
            .tasks
            .iter()
            .find(|task| task.id == task_id)
            .ok_or("Jump List 任务已失效，请重新打开菜单。")?;
        let _transaction = MutexGuard::restoration()?;
        require_session(&self.path, &self.journal.session, &self.restoring)?;
        self.require_external_interaction()?;
        if SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|error| error.to_string())?
            .as_millis()
            >= u128::from(expires_at)
        {
            return Err("Jump List 任务请求已超时，请重新打开菜单。".into());
        }
        self.applications.launch_jump_list(task, target, &self.workspace)?;
        Ok(read.diagnostics)
    }

    pub(crate) fn launch_external(&mut self, target: &str, app_id: &str) -> Result<()> {
        self.refresh_applications(false)?;
        let _transaction = MutexGuard::restoration()?;
        require_session(&self.path, &self.journal.session, &self.restoring)?;
        self.require_external_interaction()?;
        self.applications.launch_external(target, app_id, &self.workspace)
    }

    pub(crate) fn shared_desktop_foreground(&self, foreground: HWND) -> bool {
        if foreground.is_null()
            || window_pid(foreground) != self.layers.icons.pid
            || !["Progman", "WorkerW", "SHELLDLL_DefView", "SysListView32"]
                .contains(&class_name(foreground).as_str())
        {
            return false;
        }
        // Explorer 的前台 HWND 跨显示器共享；鼠标所在屏决定当前操作属于哪个桌面。
        let mut point: POINT = unsafe { zeroed() };
        if unsafe { GetCursorPos(&mut point) } == 0 {
            return false;
        }
        let target = self.journal.desktop_bounds;
        let rect = RECT {
            left: target.x,
            top: target.y,
            right: target.x + target.width,
            bottom: target.y + target.height,
        };
        let display = unsafe { MonitorFromRect(&rect, MONITOR_DEFAULTTONULL) };
        !display.is_null()
            && unsafe { MonitorFromPoint(point, MONITOR_DEFAULTTONULL) } == display
    }

    pub(crate) fn require_external_interaction(&self) -> Result<()> {
        let foreground = unsafe { GetForegroundWindow() };
        if !self.takeover
            || (!self.shared_desktop_foreground(foreground)
                && !self.journal.windows.iter().any(|saved| {
                    saved.identity.valid()
                        && (foreground == saved.identity.window()
                            || unsafe { GetAncestor(foreground, GA_ROOTOWNER) }
                                == saved.identity.window())
                }))
        {
            return Err("外部窗口操作需要当前桌面交互。".into());
        }
        for key in [
            VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN, VK_LBUTTON, VK_RBUTTON, VK_MBUTTON,
        ] {
            if unsafe { GetAsyncKeyState(i32::from(key)) } < 0 {
                return Err("请释放按键后重新操作窗口。".into());
            }
        }
        Ok(())
    }

    pub(crate) fn focus(&self, handle: &str) -> Result<()> {
        if handle.is_empty()
            || handle.len() > 16
            || !handle.bytes().all(|byte| byte.is_ascii_hexdigit())
        {
            return Err("invalid desktop focus handle".into());
        }
        let value = u64::from_str_radix(handle, 16).map_err(|_| "invalid desktop focus handle")?;
        let saved = self
            .journal
            .windows
            .iter()
            .find(|saved| saved.role == WindowRole::Overlay && saved.identity.handle == value)
            .ok_or("desktop focus target is not registered in this session")?;
        let window = saved.identity.window();
        let validate = || -> Result<()> {
            require_session(&self.path, &self.journal.session, &self.restoring)?;
            if !self.takeover
                || !saved.identity.valid()
                || !self.layers.valid()
                || !unsafe { GetParent(window) }.is_null()
                || unsafe { IsWindowVisible(window) } == 0
            {
                return Err("desktop focus target is no longer an active stage".into());
            }
            let foreground = unsafe { GetForegroundWindow() };
            if !self.shared_desktop_foreground(foreground)
                && !self.journal.windows.iter().any(|registered| {
                    foreground == registered.identity.window()
                        || unsafe { IsChild(registered.identity.window(), foreground) } != 0
                })
            {
                return Err("desktop focus requires the desktop to be foreground".into());
            }
            Ok(())
        };
        let _transaction = MutexGuard::restoration()?;
        validate()?;
        if unsafe {
            SetWindowPos(
                window,
                HWND_TOP,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        } == 0
        {
            return Err(failure("bring desktop interface forward"));
        }
        let foreground = unsafe { GetForegroundWindow() };
        let foreground_thread = unsafe { GetWindowThreadProcessId(foreground, null_mut()) };
        let mut info: GUITHREADINFO = unsafe { zeroed() };
        info.cbSize = size_of::<GUITHREADINFO>() as u32;
        if unsafe { GetGUIThreadInfo(foreground_thread, &mut info) } == 0 {
            return Err(failure("desktop focus query"));
        }
        if (info.hwndFocus == window || unsafe { IsChild(window, info.hwndFocus) } != 0)
            && foreground == window
        {
            return Ok(());
        }
        let keys_up = || -> Result<()> {
            for key in [
                VK_CONTROL,
                VK_MENU,
                VK_SHIFT,
                VK_LWIN,
                VK_RWIN,
                VK_LBUTTON,
                VK_RBUTTON,
                VK_MBUTTON,
                VK_XBUTTON1,
                VK_XBUTTON2,
            ] {
                if unsafe { GetAsyncKeyState(i32::from(key)) } < 0 {
                    return Err(
                        "desktop focus cannot change while modifiers or mouse buttons are held"
                            .into(),
                    );
                }
            }
            Ok(())
        };
        keys_up()?;
        let source = unsafe { GetCurrentThreadId() };
        let target = unsafe { GetWindowThreadProcessId(window, null_mut()) };
        if target == 0 || target == source {
            return Err("desktop focus target thread is invalid".into());
        }
        let mut message: MSG = unsafe { zeroed() };
        unsafe {
            PeekMessageW(&mut message, null_mut(), 0, 0, PM_NOREMOVE);
        }
        validate()?;
        keys_up()?;
        if unsafe { AttachThreadInput(source, target, 1) } == 0 {
            return Err(failure("attach desktop input thread"));
        }
        let attached = InputAttachment { source, target };
        let focused = (|| {
            validate()?;
            keys_up()?;
            unsafe {
                SetLastError(0);
                if SetForegroundWindow(window) == 0 {
                    return Err(failure("activate desktop interface"));
                }
                SetFocus(window);
            }
            let focus = unsafe { GetFocus() };
            if focus != window && unsafe { IsChild(window, focus) } == 0 {
                return Err(failure("set desktop input focus"));
            }
            Ok(())
        })();
        drop(attached);
        focused?;
        validate()?;
        if unsafe { GetForegroundWindow() } != window {
            return Err("desktop interface did not become foreground".into());
        }
        let active_thread = unsafe { GetWindowThreadProcessId(GetForegroundWindow(), null_mut()) };
        if unsafe { GetGUIThreadInfo(active_thread, &mut info) } == 0 {
            return Err(failure("desktop focus verification"));
        }
        if info.hwndFocus != window && unsafe { IsChild(window, info.hwndFocus) } == 0 {
            return Err("desktop input focus was lost after detaching helper thread".into());
        }
        Ok(())
    }

    pub(crate) fn poll(&mut self) -> Result<()> {
        if self.restoring.requested()? {
            return Err("desktop restoration has started".into());
        }
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
        self.require_display()?;
        if !self.layers.valid() {
            let _transaction = MutexGuard::restoration()?;
            require_session(&self.path, &self.journal.session, &self.restoring)?;
            let replacement = DesktopLayers::discover(self.takeover)?;
            for saved in &self.journal.windows {
                if !saved.role.is_top_level() {
                    attach(saved, &replacement, self.journal.desktop_bounds)?;
                }
            }
            self.refresh_shell()?;
            self.layers = replacement;
        }
        self.refresh_applications(false)?;
        if self.takeover && self.last_shell_scan.elapsed() >= Duration::from_secs(1) {
            let _transaction = MutexGuard::restoration()?;
            require_session(&self.path, &self.journal.session, &self.restoring)?;
            self.refresh_shell()?;
        }
        let foreground = unsafe { GetForegroundWindow() };
        let desktop_active = (self.takeover && self.shared_desktop_foreground(foreground))
            || self.journal.windows.iter().any(|saved| {
                saved.role != WindowRole::Overlay
                    && (foreground == saved.identity.window()
                        || unsafe { IsChild(saved.identity.window(), foreground) } != 0)
            });
        let active = self.journal.windows.iter().any(|saved| {
            saved.role == WindowRole::Overlay
                && (foreground == saved.identity.window()
                    || unsafe { GetAncestor(foreground, GA_ROOTOWNER) } == saved.identity.window())
        });
        let fullscreen = self.workspace.fullscreen(foreground);
        if self.takeover && self.tray.suspend(fullscreen) {
            let _transaction = MutexGuard::restoration()?;
            require_session(&self.path, &self.journal.session, &self.restoring)?;
            self.tray.apply(&self.journal, self.tray_style.as_ref())?;
            self.publish_tray();
        }
        let stage_available = !fullscreen && (desktop_active || active);
        let state = (active && !fullscreen, stage_available, fullscreen);
        // 显示与位置消息会交错；跨轮复验异步校正的结果，沿用显示器的两像素 DPI 容差。
        let mut overlays_changed = self.foreground.map(|value| value.2) != Some(fullscreen);
        let target = self.journal.desktop_bounds;
        let pending = &mut self.overlay_geometry_pending;
        for saved in &self.journal.windows {
            if !saved.role.is_top_level() {
                continue;
            }
            overlays_changed |=
                (unsafe { IsWindowVisible(saved.identity.window()) } != 0) == fullscreen;
            if fullscreen || unsafe { IsIconic(saved.identity.window()) } != 0 {
                *pending = None;
                continue;
            }
            let actual = rectangle(saved.identity.window())?;
            if actual.within_tolerance(target, 2) {
                *pending = None;
            } else {
                if pending.get_or_insert_with(Instant::now).elapsed() >= OVERLAY_GEOMETRY_TIMEOUT {
                    return Err(format!(
                        "desktop overlay geometry did not settle: actual {actual:?}, expected {target:?}"
                    ));
                }
                overlays_changed = true;
            }
        }
        if overlays_changed {
            let _transaction = MutexGuard::restoration()?;
            require_session(&self.path, &self.journal.session, &self.restoring)?;
            for saved in &self.journal.windows {
                if saved.role.is_top_level() {
                    visibility(&saved.identity, !fullscreen)?;
                    if !fullscreen
                        && unsafe {
                            SetWindowPos(
                                saved.identity.window(),
                                null_mut(),
                                target.x,
                                target.y,
                                target.width,
                                target.height,
                                SWP_NOACTIVATE | SWP_NOZORDER | SWP_ASYNCWINDOWPOS,
                            )
                        } == 0
                    {
                        return Err(failure("restore desktop overlay geometry"));
                    }
                }
            }
        }
        if self.foreground != Some(state) {
            self.foreground = Some(state);
            emit(
                json!({ "event": "foreground", "active": state.0, "stage_available": state.1, "fullscreen": state.2 }),
            );
        }
        if let Some(style) = &mut self.tray_style {
            style.heartbeat(self.palette)?;
        }
        Ok(())
    }

    pub(crate) fn cancel_operations(&mut self) {
        self.applications.cancel_operations();
        self.workspace.cancel_operations();
    }

    pub(crate) fn stop(&mut self) -> Result<()> {
        if self.stopped {
            return Ok(());
        }
        self.cancel_operations();
        restore(&self.path, Some(&self.journal.session))?;
        retire_guardian(&mut self.guardian)?;
        self.stopped = true;
        Ok(())
    }

    pub(crate) fn refresh_shell(&mut self) -> Result<()> {
        if !self.takeover {
            return Ok(());
        }
        self.require_display()?;
        let original_size = self.journal.shell.len();
        self.journal.shell.retain(Identity::valid);
        self.journal.regions.retain(|saved| saved.identity.valid());
        self.journal.tray_placements.retain(|saved| saved.identity.valid());
        let mut changed = self.journal.shell.len() != original_size;
        let mut released = Vec::new();
        for item in &self.journal.shell {
            let saved_placement = self.journal.tray_placements.iter().find(|saved| saved.identity.handle == item.handle);
            if !saved_placement.is_some_and(|saved| saved.monitor == self.journal.desktop_bounds)
                && !tray::on_display(item.window(), self.journal.desktop_bounds)?
            {
                if let Some(saved) = saved_placement {
                    saved.restore()?;
                }
                if let Some(saved) = self
                    .journal
                    .regions
                    .iter()
                    .find(|saved| saved.identity.handle == item.handle)
                {
                    self.journal.restore_region(saved)?;
                }
                visibility(item, item.visible)?;
                released.push(item.handle);
            }
        }
        if !released.is_empty() {
            self.journal.shell.retain(|item| !released.contains(&item.handle));
            self.journal
                .regions
                .retain(|saved| !released.contains(&saved.identity.handle));
            self.journal.tray_placements.retain(|saved| !released.contains(&saved.identity.handle));
            changed = true;
        }
        for item in shell_windows(self.journal.desktop_bounds)? {
            if !self.journal.shell.iter().any(|saved| saved.same_window(&item)) {
                self.journal.capture_shell(item)?;
                changed = true;
            }
        }
        let style_matches = self.tray_style.as_ref().map_or(self.journal.shell.is_empty(),
            |style| style.matches_target(&self.journal.shell));
        if !style_matches {
            if let Some(record) = &self.journal.tray_style {
                record.restore()?;
            }
            self.tray_style = None;
            self.tray_style = TrayStyle::prepare(&self.journal, self.palette)?;
            self.journal.tray_style = self.tray_style.as_ref().map(TrayStyle::record);
            changed = true;
        }
        if changed {
            self.journal.save(&self.path)?;
        }
        if !style_matches {
            if let Some(style) = &mut self.tray_style {
                style.begin()?;
            }
        }
        self.tray.refresh(&self.journal)?;
        self.tray.apply(&self.journal, self.tray_style.as_ref())?;
        self.publish_tray();
        self.last_shell_scan = Instant::now();
        Ok(())
    }
}

impl Drop for Session {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

