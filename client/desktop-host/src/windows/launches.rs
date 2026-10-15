use super::*;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{Receiver, TryRecvError, channel};
use windows::Win32::System::Com::CoAllowSetForegroundWindow;
use windows::Win32::UI::Shell::{
    AO_NOERRORUI, ApplicationActivationManager, IApplicationActivationManager,
};
use windows::core::PCWSTR;
use windows_sys::Win32::UI::Shell::{
    SEE_MASK_FLAG_NO_UI, SEE_MASK_HMONITOR, SEE_MASK_NOASYNC, SEE_MASK_NOCLOSEPROCESS, SHELLEXECUTEINFOW,
    ShellExecuteExW,
};

const LAUNCH_TIMEOUT: Duration = Duration::from_secs(15);

pub(super) struct LaunchRequest {
    target: String,
    arguments: String,
    directory: String,
    app_id: String,
}

fn normalize(value: &str) -> String {
    value.replace('/', "\\").to_lowercase()
}

fn validate_target(target: &str) -> Result<()> {
    if target.is_empty() || target.len() > 32_768 || target.contains('\0') {
        return Err("程序目标无效。".into());
    }
    if let Some(id) = normalize(target).strip_prefix("shell:appsfolder\\") {
        let valid = id.split_once('!').is_some_and(|(family, app)| {
            family.rsplit_once('_').is_some_and(|(name, publisher)| {
                !name.is_empty()
                    && !publisher.is_empty()
                    && !app.is_empty()
                    && name
                        .bytes()
                        .all(|value| value.is_ascii_alphanumeric() || b".-".contains(&value))
                    && publisher.bytes().all(|value| value.is_ascii_alphanumeric())
                    && app
                        .bytes()
                        .all(|value| value.is_ascii_alphanumeric() || b"._-".contains(&value))
            })
        });
        if id.len() > 1024 || !valid {
            return Err("Windows 应用身份无效。".into());
        }
    } else {
        let path = Path::new(target);
        if !path.is_absolute()
            || !path
                .extension()
                .and_then(|extension| extension.to_str())
                .is_some_and(|extension| {
                    ["exe", "lnk"].contains(&extension.to_ascii_lowercase().as_str())
                })
        {
            return Err("请选择可用的程序或程序快捷方式。".into());
        }
    }
    Ok(())
}

impl LaunchRequest {
    pub(super) fn external(target: &str, app_id: &str) -> Result<Self> {
        validate_target(target)?;
        if app_id.is_empty() || app_id.len() > 32_768 || app_id.contains('\0') {
            return Err("程序身份无效。".into());
        }
        Ok(Self {
            target: target.into(),
            arguments: String::new(),
            directory: String::new(),
            app_id: normalize(app_id),
        })
    }

    pub(super) fn jump_list(task: &jump_lists::JumpListTask, target: &str) -> Result<Self> {
        let mut request = Self::external(&task.program, target)?;
        if task.args.len() > 32_768
            || task.args.contains('\0')
            || task.working_directory.contains('\0')
            || (!task.working_directory.is_empty()
                && !Path::new(&task.working_directory).is_absolute())
        {
            return Err("Jump List 启动参数无效。".into());
        }
        request.arguments = task.args.clone();
        request.directory = task.working_directory.clone();
        Ok(request)
    }

    fn launch(self, monitor: usize, cancelled: &AtomicBool) -> Result<ProcessIdentity> {
        let _apartment = Apartment::start()?;
        if cancelled.load(Ordering::Acquire) {
            return Err("桌面会话已结束。".into());
        }
        if normalize(&self.target).starts_with("shell:appsfolder\\") {
            let id = wide(&self.target[17..]);
            let arguments = wide(&self.arguments);
            let manager: IApplicationActivationManager = unsafe {
                CoCreateInstance(&ApplicationActivationManager, None, CLSCTX_ALL)
            }
            .map_err(|error| error.to_string())?;
            if cancelled.load(Ordering::Acquire) {
                return Err("桌面会话已结束。".into());
            }
            // 前台授权是尽力而为；应用仍可成功启动并由后续流程定位窗口。
            let _ = unsafe { CoAllowSetForegroundWindow(&manager, None) };
            let pid = unsafe {
                manager.ActivateApplication(
                    PCWSTR(id.as_ptr()),
                    PCWSTR(arguments.as_ptr()),
                    AO_NOERRORUI,
                )
            }
            .map_err(|error| format!("启动 Windows 应用失败: {error}"))?;
            return Ok(ProcessIdentity {
                pid,
                birth: created(process(pid)?.0)?,
            });
        }
        // UNC 路径检查可能等待网络，文件访问与 Shell 启动均留在工作线程。
        if !Path::new(&self.target).is_file() {
            return Err("程序文件不存在。".into());
        }
        let target = wide(&self.target);
        let arguments = wide(&self.arguments);
        let directory = wide(&self.directory);
        let verb = wide("open");
        let mut info: SHELLEXECUTEINFOW = unsafe { zeroed() };
        info.cbSize = size_of::<SHELLEXECUTEINFOW>() as u32;
        // Shell/DDE 等待仅发生在工作线程；NOCLOSEPROCESS 保留真实启动进程身份。
        info.fMask =
            SEE_MASK_NOCLOSEPROCESS | SEE_MASK_HMONITOR | SEE_MASK_FLAG_NO_UI | SEE_MASK_NOASYNC;
        info.lpVerb = verb.as_ptr();
        info.lpFile = target.as_ptr();
        info.lpParameters = arguments.as_ptr();
        info.lpDirectory = if self.directory.is_empty() {
            null()
        } else {
            directory.as_ptr()
        };
        info.nShow = SW_SHOWNORMAL;
        info.Anonymous.hMonitor = monitor as HANDLE;
        if cancelled.load(Ordering::Acquire) {
            return Err("桌面会话已结束。".into());
        }
        if unsafe { ShellExecuteExW(&mut info) } == 0 {
            return Err(failure("启动程序失败"));
        }
        if info.hProcess.is_null() {
            return Err("已提交打开，但 Windows 未提供可关联的程序进程。".into());
        }
        let owner = OwnedHandle(info.hProcess);
        Ok(ProcessIdentity {
            pid: unsafe { GetProcessId(owner.0) },
            birth: created(owner.0)?,
        })
    }
}

struct ProcessIdentity {
    pid: u32,
    birth: u64,
}

fn last_input() -> Option<u32> {
    let mut input = LASTINPUTINFO {
        cbSize: size_of::<LASTINPUTINFO>() as u32,
        dwTime: 0,
    };
    (unsafe { GetLastInputInfo(&mut input) } != 0).then_some(input.dwTime)
}

struct MovingWindow {
    identities: Vec<Identity>,
    relocations: Vec<WindowRelocation>,
    started: Instant,
    resolve_modal: bool,
}

struct PendingLaunch {
    app_id: String,
    started: Instant,
    started_filetime: u64,
    baseline: HashSet<(usize, u32, u64)>,
    result: Option<Receiver<Result<ProcessIdentity>>>,
    processes: HashMap<u32, u64>,
    launched_process: Option<ProcessIdentity>,
    launch_process_exited: bool,
    input_at_request: Option<u32>,
    handoff_allowed: bool,
    candidate: Option<(String, Instant)>,
    moving: Option<MovingWindow>,
    cancelled: Arc<AtomicBool>,
}

impl Drop for PendingLaunch {
    fn drop(&mut self) {
        self.cancelled.store(true, Ordering::Release);
    }
}

impl PendingLaunch {
    fn expand_processes(&mut self) -> Result<()> {
        let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) };
        if snapshot == INVALID_HANDLE_VALUE {
            return Err(failure("读取启动进程列表失败"));
        }
        let _snapshot = OwnedHandle(snapshot);
        let mut entry: PROCESSENTRY32W = unsafe { zeroed() };
        entry.dwSize = size_of::<PROCESSENTRY32W>() as u32;
        let mut entries = Vec::new();
        let mut available = unsafe { Process32FirstW(snapshot, &mut entry) };
        while available != 0 {
            entries.push((entry.th32ProcessID, entry.th32ParentProcessID));
            available = unsafe { Process32NextW(snapshot, &mut entry) };
        }
        if let Some(launch) = &self.launched_process {
            self.launch_process_exited = !entries.iter().any(|(pid, _)| *pid == launch.pid);
        }
        // PID 可复用；已知进程若仍存在，必须与捕获的创建时间一致。
        self.processes.retain(|pid, birth| {
            process(*pid).and_then(|owner| created(owner.0)).map_or(
                !entries.iter().any(|(current, _)| current == pid),
                |current| current == *birth,
            )
        });
        for _ in 0..8 {
            let mut changed = false;
            for &(pid, parent) in &entries {
                if self.processes.contains_key(&pid) || self.processes.len() >= 256 {
                    continue;
                }
                let Some(parent_birth) = self.processes.get(&parent) else {
                    continue;
                };
                if let Ok(birth) = process(pid).and_then(|owner| created(owner.0)) {
                    if birth >= *parent_birth && birth >= self.started_filetime {
                        self.processes.insert(pid, birth);
                        changed = true;
                    }
                }
            }
            if !changed {
                break;
            }
        }
        Ok(())
    }

    fn new_application_window(&self, tracked: &TrackedWindow) -> bool {
        if normalize(&tracked.value.app_id) != self.app_id
            || self.baseline.contains(&(
                tracked.identity.window() as usize,
                tracked.identity.pid,
                tracked.identity.created,
            ))
        {
            return false;
        }
        true
    }

    fn associated(&self, tracked: &TrackedWindow) -> bool {
        if !self.new_application_window(tracked) {
            return false;
        }
        if self.processes.get(&tracked.identity.pid) == Some(&tracked.identity.created) {
            return true;
        }
        // UWP 的可见顶层窗口可能由 ApplicationFrameHost 承载，核对其子窗口进程。
        let mut children = Vec::new();
        unsafe {
            EnumChildWindows(
                tracked.identity.window(),
                Some(collect_child_pid),
                &mut children as *mut Vec<u32> as LPARAM,
            );
        }
        children.into_iter().any(|pid| {
            self.processes.get(&pid).is_some_and(|birth| {
                process(pid)
                    .and_then(|owner| created(owner.0))
                    .is_ok_and(|current| current == *birth)
            })
        })
    }

    fn handoff_candidate<'a>(
        &self,
        windows: &'a HashMap<usize, TrackedWindow>,
        foreground: HWND,
        input: Option<u32>,
    ) -> Result<Option<&'a TrackedWindow>> {
        if !self.handoff_allowed
            || !self.launch_process_exited
            || self.input_at_request.is_none()
            || input != self.input_at_request
        {
            return Ok(None);
        }
        let mut matching = windows
            .values()
            .filter(|tracked| self.new_application_window(tracked));
        let candidate = matching.next();
        if matching.next().is_some() {
            return Err("已提交打开，但匹配到多个程序窗口，未能定位窗口。".into());
        }
        Ok(candidate.filter(|tracked| owned_by(foreground, tracked.identity.window())))
    }

    fn poll(&mut self, windows: &HashMap<usize, TrackedWindow>, workspace: &Workspace) -> Result<bool> {
        if self.started.elapsed() >= LAUNCH_TIMEOUT {
            return Err("已提交打开，但未能定位窗口；请在 Windows 中查看程序。".into());
        }
        if let Some(moving) = &mut self.moving {
            if moving.identities.iter().any(|identity| !identity.valid()) {
                return Err("已提交打开，但程序窗口已改变，未能定位窗口。".into());
            }
            let mut relocated = true;
            for relocation in &mut moving.relocations {
                relocated &= relocation.poll(workspace)?;
            }
            if relocated
                && moving
                .identities
                .iter()
                .all(|identity| workspace.placed(identity.window()))
            {
                if moving.resolve_modal {
                    let root = moving.identities[0].window();
                    let target = match activation_target(root) {
                        Ok(target) => target,
                        Err(_) if moving.started.elapsed() < Duration::from_secs(1) => {
                            return Ok(false);
                        }
                        Err(error) => return Err(error),
                    };
                    moving.resolve_modal = false;
                    if target != root {
                        let identity = Identity::capture(target)?;
                        if !workspace.placed(target) {
                            moving.relocations.push(workspace.move_to_display(target)?);
                        }
                        moving.identities.push(identity);
                        moving.started = Instant::now();
                        return Ok(false);
                    }
                }
                return Ok(true);
            }
            if moving.started.elapsed() >= Duration::from_secs(1) {
                return Err("已提交打开，但程序窗口拒绝移动到所选屏幕。".into());
            }
            return Ok(false);
        }
        if let Some(receiver) = &self.result {
            match receiver.try_recv() {
                Ok(result) => {
                    let process = result?;
                    if process.pid == 0 {
                        return Err("已提交打开，但未能识别启动进程。".into());
                    }
                    self.processes.insert(process.pid, process.birth);
                    self.handoff_allowed = process.birth >= self.started_filetime;
                    self.launched_process = Some(process);
                    self.result = None;
                }
                Err(TryRecvError::Empty) => return Ok(false),
                Err(TryRecvError::Disconnected) => return Err("启动程序的操作意外结束。".into()),
            }
        }
        self.expand_processes()?;
        self.handoff_allowed &=
            self.input_at_request.is_some() && last_input() == self.input_at_request;
        let mut candidates = windows
            .values()
            .filter(|tracked| self.associated(tracked))
            .collect::<Vec<_>>();
        if candidates.is_empty() {
            // 单实例程序可由短命启动进程转交给旧进程；只接受无后续输入且唯一的新增前台窗口。
            if let Some(candidate) =
                self.handoff_candidate(windows, unsafe { GetForegroundWindow() }, last_input())?
            {
                candidates.push(candidate);
            }
        }
        if candidates.len() > 1 {
            return Err("已提交打开，但匹配到多个程序窗口，未能定位窗口。".into());
        }
        let Some(tracked) = candidates.first() else {
            self.candidate = None;
            return Ok(false);
        };
        if self
            .candidate
            .as_ref()
            .is_none_or(|(id, _)| id != &tracked.value.id)
        {
            self.candidate = Some((tracked.value.id.clone(), Instant::now()));
            return Ok(false);
        }
        if self
            .candidate
            .as_ref()
            .is_some_and(|(_, started)| started.elapsed() < Duration::from_millis(200))
        {
            return Ok(false);
        }
        if !tracked.identity.valid() {
            return Err("已提交打开，但程序窗口已改变，未能定位窗口。".into());
        }
        let root = tracked.identity.window();
        let mut relocations = Vec::new();
        if !workspace.placed(root) {
            relocations.push(workspace.move_to_display(root)?);
        }
        self.moving = Some(MovingWindow {
            identities: vec![tracked.identity.clone()],
            relocations,
            started: Instant::now(),
            resolve_modal: true,
        });
        Ok(false)
    }
}

#[derive(Default)]
pub(super) struct Launches(Vec<PendingLaunch>);

impl Launches {
    pub(super) fn pending(&self) -> bool {
        !self.0.is_empty()
    }

    pub(super) fn start(
        &mut self,
        request: LaunchRequest,
        workspace: &Workspace,
    ) -> Result<()> {
        let input_at_request = last_input();
        let started = Instant::now();
        if self.0.len() >= 8 || self.0.iter().any(|pending| pending.app_id == request.app_id) {
            return Err("该程序仍在打开，请稍候。".into());
        }
        let monitor = workspace.monitor()? as usize;
        let baseline = top_windows()?
            .into_iter()
            .filter_map(|window| {
                Identity::capture(window)
                    .ok()
                    .map(|identity| (window as usize, identity.pid, identity.created))
            })
            .collect();
        let started_filetime = u64::try_from(
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_err(|error| error.to_string())?
                .as_nanos()
                / 100,
        )
        .map_err(|_| "系统时间超出 Windows 时间范围。")?
            + 116_444_736_000_000_000;
        let (sender, receiver) = channel();
        let cancelled = Arc::new(AtomicBool::new(false));
        let pending = PendingLaunch {
            app_id: request.app_id.clone(),
            started,
            started_filetime,
            baseline,
            result: Some(receiver),
            processes: HashMap::new(),
            launched_process: None,
            launch_process_exited: false,
            input_at_request,
            handoff_allowed: false,
            candidate: None,
            moving: None,
            cancelled: cancelled.clone(),
        };
        thread::Builder::new()
            .name("desktop-application-launch".into())
            .spawn(move || {
                let _ = sender.send(request.launch(monitor, &cancelled));
            })
            .map_err(|error| format!("无法启动程序: {error}"))?;
        self.0.push(pending);
        Ok(())
    }

    pub(super) fn poll(&mut self, windows: &HashMap<usize, TrackedWindow>, workspace: &Workspace) {
        self.0.retain_mut(|pending| match pending.poll(windows, workspace) {
            Ok(completed) => !completed,
            Err(reason) => {
                emit(json!({ "event": "warning", "reason": reason }));
                false
            }
        });
    }
}
