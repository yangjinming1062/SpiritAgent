use super::*;
use std::cell::RefCell;
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle as RestorationHandle};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{Receiver, TryRecvError};
use std::thread::JoinHandle;

const MAX_OPERATIONS: usize = 16;

pub(super) enum NativeStep {
    Position(Bounds),
    Placement(WINDOWPLACEMENT),
    Focus { root: Identity, foreground_at_request: usize, root_placement: FocusPlacement, target_placement: FocusPlacement },
}

struct RegisteredOperation {
    window: usize,
    cancelled: Arc<AtomicBool>,
    worker: Option<JoinHandle<()>>,
    reserved: bool,
}

#[derive(Default)]
pub(super) struct Operations(
    RefCell<Vec<RegisteredOperation>>,
    RefCell<Option<Arc<RestorationHandle>>>,
);

impl Operations {
    pub(super) fn bind_restoration(&self, handle: HANDLE) -> Result<()> {
        if self.1.borrow().is_some() || self.0.borrow().iter().any(|operation| operation.worker.is_some() || operation.reserved) {
            return Err("窗口操作已绑定恢复信号或仍在执行。".into());
        }
        let process = unsafe { GetCurrentProcess() };
        let mut duplicate = null_mut();
        if unsafe { DuplicateHandle(process, handle, process, &mut duplicate, 0, 0, DUPLICATE_SAME_ACCESS) } == 0 {
            return Err(failure("复制桌面恢复信号失败"));
        }
        let owned = unsafe { RestorationHandle::from_raw_handle(duplicate) };
        *self.1.borrow_mut() = Some(Arc::new(owned));
        Ok(())
    }

    pub(super) fn reap(&self) {
        let mut operations = self.0.borrow_mut();
        for operation in operations.iter_mut() {
            if operation.worker.as_ref().is_some_and(JoinHandle::is_finished) {
                if let Some(worker) = operation.worker.take() {
                    let _ = worker.join();
                }
            }
        }
        operations.retain(|operation| {
            operation.worker.is_some() || !operation.cancelled.load(Ordering::Acquire)
        });
    }

    pub(super) fn active(&self, window: HWND) -> bool {
        self.reap();
        self.0.borrow().iter().any(|operation| operation.window == window as usize)
    }

    pub(super) fn cancel_window(&self, window: HWND) {
        for operation in self.0.borrow().iter() {
            if operation.window == window as usize {
                operation.cancelled.store(true, Ordering::Release);
            }
        }
        self.reap();
    }

    pub(super) fn cancel_all(&self) {
        for operation in self.0.borrow().iter() {
            operation.cancelled.store(true, Ordering::Release);
        }
        self.reap();
    }

    fn returned(&self, cancelled: &Arc<AtomicBool>) -> bool {
        self.reap();
        self.0.borrow().iter().all(|operation| {
            !Arc::ptr_eq(&operation.cancelled, cancelled) || operation.worker.is_none()
        })
    }

    fn reserve(&self, identity: &Identity) -> WindowDispatch<Reservation> {
        self.reap();
        if !identity.valid() {
            return Err("程序窗口已关闭或改变，停止调整位置。".into());
        }
        {
            let operations = self.0.borrow();
            if operations.iter().any(|operation| {
                operation.window == identity.window() as usize && (operation.worker.is_some() || operation.reserved)
            }) || operations.iter().filter(|operation| operation.worker.is_some() || operation.reserved).count() >= MAX_OPERATIONS {
                return Ok(None);
            }
            for operation in operations.iter().filter(|operation| operation.window == identity.window() as usize) {
                operation.cancelled.store(true, Ordering::Release);
            }
        }
        self.reap();
        let cancelled = Arc::new(AtomicBool::new(false));
        self.0.borrow_mut().push(RegisteredOperation {
            window: identity.window() as usize,
            cancelled: cancelled.clone(),
            worker: None,
            reserved: true,
        });
        Ok(Some(Reservation { identity: identity.clone(), cancelled, consumed: false }))
    }

    pub(super) fn start(&self, identity: &Identity, steps: Vec<NativeStep>) -> WindowDispatch<NativeTask> {
        let Some(reservation) = self.reserve(identity)? else { return Ok(None); };
        reservation.start(steps, self).map(Some)
    }

    fn spawn(
        &self,
        identity: &Identity,
        cancelled: &Arc<AtomicBool>,
        steps: Vec<NativeStep>,
        reserved: bool,
    ) -> WindowDispatch<Receiver<Result<NativeOutcome>>> {
        require_active(cancelled, self.1.borrow().as_deref())?;
        self.reap();
        let mut operations = self.0.borrow_mut();
        if !reserved && operations.iter().filter(|operation| operation.worker.is_some() || operation.reserved).count() >= MAX_OPERATIONS {
            return Ok(None);
        }
        let operation = operations.iter_mut()
            .find(|operation| Arc::ptr_eq(&operation.cancelled, cancelled))
            .ok_or("窗口位置请求已结束。")?;
        if operation.worker.is_some() {
            return Ok(None);
        }
        if operation.reserved != reserved {
            return Err("窗口操作执行资格已改变。".into());
        }
        let identity = identity.clone();
        let worker_cancelled = cancelled.clone();
        let restoring = self.1.borrow().clone();
        let (sender, receiver) = channel();
        let worker = thread::Builder::new().name("desktop-window-placement".into())
            .spawn(move || {
                let result = execute(&identity, &worker_cancelled, restoring.as_deref(), steps);
                let _ = sender.send(result);
            })
            .map_err(|error| format!("无法启动窗口位置处理: {error}"))?;
        operation.worker = Some(worker);
        operation.reserved = false;
        Ok(Some(receiver))
    }
}

struct Reservation {
    identity: Identity,
    cancelled: Arc<AtomicBool>,
    consumed: bool,
}

impl Reservation {
    fn start(mut self, steps: Vec<NativeStep>, operations: &Operations) -> Result<NativeTask> {
        let receiver = operations.spawn(&self.identity, &self.cancelled, steps, true)?
            .ok_or("预留的窗口操作执行资格已失效。")?;
        self.consumed = true;
        Ok(NativeTask { cancelled: self.cancelled.clone(), receiver, acknowledged: None })
    }
}

impl Drop for Reservation {
    fn drop(&mut self) {
        if !self.consumed {
            self.cancelled.store(true, Ordering::Release);
        }
    }
}

impl Drop for Operations {
    fn drop(&mut self) {
        for operation in self.0.get_mut().iter() {
            operation.cancelled.store(true, Ordering::Release);
        }
        // 外部线程可能不响应；只取消后续写入，存活线程随 host 进程退出。
    }
}

fn require_active(cancelled: &AtomicBool, restoring: Option<&RestorationHandle>) -> Result<()> {
    if cancelled.load(Ordering::Acquire)
        || restoring.is_some_and(|signal| unsafe { WaitForSingleObject(signal.as_raw_handle(), 0) } != WAIT_TIMEOUT)
    {
        cancelled.store(true, Ordering::Release);
        return Err("窗口操作已取消或桌面恢复已开始。".into());
    }
    Ok(())
}

fn execute(identity: &Identity, cancelled: &AtomicBool, restoring: Option<&RestorationHandle>, steps: Vec<NativeStep>) -> Result<NativeOutcome> {
    if unsafe { SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) }
        .is_null()
    {
        return Err(failure("设置窗口位置线程 DPI 失败"));
    }
    for step in steps {
        require_active(cancelled, restoring)?;
        if !identity.valid() {
            return Err("程序窗口已关闭或改变，停止调整位置。".into());
        }
        require_active(cancelled, restoring)?;
        // 不带 ASYNC：调用本身返回后才确认该阶段完成，等待留在独立线程。
        let accepted = match step {
            NativeStep::Position(area) => unsafe {
                SetWindowPos(
                    identity.window(),
                    null_mut(),
                    area.x,
                    area.y,
                    area.width,
                    area.height,
                    SWP_NOACTIVATE | SWP_NOZORDER,
                )
            },
            NativeStep::Placement(mut placement) => {
                placement.flags &= !WPF_ASYNCWINDOWPLACEMENT;
                unsafe { SetWindowPlacement(identity.window(), &placement) }
            }
            NativeStep::Focus { root, foreground_at_request, root_placement, target_placement } => {
                return focus(identity, &root, foreground_at_request, &root_placement, &target_placement, cancelled, restoring);
            }
        };
        let error = (accepted == 0).then(|| failure("Windows 未能调整程序窗口位置"));
        require_active(cancelled, restoring)?;
        if let Some(error) = error {
            return Err(error);
        }
    }
    require_active(cancelled, restoring)?;
    Ok(NativeOutcome::Submitted)
}

#[derive(Clone, Copy)]
enum NativeOutcome {
    Submitted,
    Deferred,
    Cancelled,
}

pub(super) struct NativeTask {
    cancelled: Arc<AtomicBool>,
    receiver: Receiver<Result<NativeOutcome>>,
    acknowledged: Option<NativeOutcome>,
}

impl NativeTask {
    pub(super) fn cancelled(&self) -> bool {
        self.cancelled.load(Ordering::Acquire)
    }

    fn poll_acknowledgement(&mut self) -> Result<Option<NativeOutcome>> {
        if self.cancelled() {
            return Err("窗口位置请求已取消。".into());
        }
        if self.acknowledged.is_none() {
            match self.receiver.try_recv() {
                Ok(result) => self.acknowledged = Some(result?),
                Err(TryRecvError::Empty) => return Ok(None),
                Err(TryRecvError::Disconnected) => return Err("窗口位置处理线程意外结束。".into()),
            }
        }
        Ok(self.acknowledged)
    }

    pub(super) fn poll(&mut self, operations: &Operations) -> Result<bool> {
        Ok(self.poll_acknowledgement()?.is_some() && operations.returned(&self.cancelled))
    }

    fn next(&mut self, identity: &Identity, operations: &Operations, step: NativeStep) -> Result<bool> {
        let Some(receiver) = operations.spawn(identity, &self.cancelled, vec![step], false)? else { return Ok(false); };
        self.receiver = receiver;
        self.acknowledged = None;
        Ok(true)
    }
}

fn owned_by(mut window: HWND, root: HWND) -> bool {
    for _ in 0..32 {
        if window == root {
            return true;
        }
        if window.is_null() {
            break;
        }
        window = unsafe { GetWindow(window, GW_OWNER) };
    }
    false
}

pub(super) struct FocusPlacement {
    device: String,
    work_area: Option<Bounds>,
}

impl FocusPlacement {
    fn capture(window: HWND, workspace: &Workspace) -> Self {
        Self {
            device: workspace.display.device.clone(),
            work_area: workspace.placed(window).then_some(workspace.current()),
        }
    }

    fn satisfied(&self, window: HWND) -> bool {
        (unsafe { IsIconic(window) } == 0)
            && info(unsafe { MonitorFromWindow(window, MONITOR_DEFAULTTONEAREST) })
                .is_ok_and(|value| device(&value) == self.device)
            && self.work_area.is_none_or(|area| visible_bounds(window).is_ok_and(|visual| contains(Bounds {
                x: area.x - 2, y: area.y - 2, width: area.width + 4, height: area.height + 4,
            }, visual)))
    }
}

enum FocusReadiness {
    Ready,
    Deferred,
    Cancelled,
}

fn require_focus(identity: &Identity, root: &Identity, foreground_at_request: usize, root_placement: &FocusPlacement, target_placement: &FocusPlacement, cancelled: &AtomicBool, restoring: Option<&RestorationHandle>) -> Result<FocusReadiness> {
    require_active(cancelled, restoring)?;
    if !identity.valid() || !root.valid() {
        return Err("程序窗口已关闭或改变，停止激活。".into());
    }
    if unsafe { IsWindowVisible(identity.window()) == 0 || IsWindowEnabled(identity.window()) == 0 } {
        return Ok(FocusReadiness::Deferred);
    }
    let foreground = foreground_window();
    if foreground.is_null() {
        return Ok(FocusReadiness::Deferred);
    }
    if foreground as usize != foreground_at_request && !owned_by(foreground, root.window()) {
        return Ok(FocusReadiness::Cancelled);
    }
    if !owned_by(identity.window(), root.window()) {
        return Err("程序窗口所属分支已改变，停止激活。".into());
    }
    if !root_placement.satisfied(root.window()) || !target_placement.satisfied(identity.window()) {
        return Ok(FocusReadiness::Deferred);
    }
    require_active(cancelled, restoring)?;
    Ok(FocusReadiness::Ready)
}

fn focus(identity: &Identity, root: &Identity, foreground_at_request: usize, root_placement: &FocusPlacement, target_placement: &FocusPlacement, cancelled: &AtomicBool, restoring: Option<&RestorationHandle>) -> Result<NativeOutcome> {
    match require_focus(identity, root, foreground_at_request, root_placement, target_placement, cancelled, restoring)? {
        FocusReadiness::Ready => {},
        FocusReadiness::Deferred => return Ok(NativeOutcome::Deferred),
        FocusReadiness::Cancelled => return Ok(NativeOutcome::Cancelled),
    }
    let accepted = unsafe { SetForegroundWindow(identity.window()) } != 0;
    require_active(cancelled, restoring)?;
    if !accepted {
        match require_focus(identity, root, foreground_at_request, root_placement, target_placement, cancelled, restoring)? {
            FocusReadiness::Deferred => return Ok(NativeOutcome::Deferred),
            FocusReadiness::Cancelled => return Ok(NativeOutcome::Cancelled),
            FocusReadiness::Ready if owned_by(foreground_window(), identity.window()) => return Ok(NativeOutcome::Submitted),
            FocusReadiness::Ready => {},
        }
        return Err("Windows 拒绝切换到该程序窗口。".into());
    }
    Ok(NativeOutcome::Submitted)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(in super::super) enum FocusOutcome {
    Submitted,
    Deferred,
    Cancelled,
}

pub(in super::super) struct FocusReservation(Reservation);

impl FocusReservation {
    pub(super) fn reserve(identity: &Identity, workspace: &Workspace) -> WindowDispatch<Self> {
        workspace.operations.reserve(identity).map(|reservation| reservation.map(Self))
    }

    pub(in super::super) fn start(self, root: &Identity, target: &Identity, foreground_at_request: usize, workspace: &Workspace) -> Result<FocusRequest> {
        if self.0.identity.handle != target.handle || self.0.identity.pid != target.pid || self.0.identity.created != target.created {
            return Err("预留的程序窗口身份已改变。".into());
        }
        self.0.start(vec![NativeStep::Focus {
            root: root.clone(),
            foreground_at_request,
            root_placement: FocusPlacement::capture(root.window(), workspace),
            target_placement: FocusPlacement::capture(target.window(), workspace),
        }], &workspace.operations).map(FocusRequest)
    }
}

pub(in super::super) struct FocusRequest(NativeTask);

impl FocusRequest {
    pub(in super::super) fn poll(&mut self) -> Result<Option<FocusOutcome>> {
        self.0.poll_acknowledgement().map(|outcome| outcome.map(|outcome| match outcome {
            NativeOutcome::Submitted => FocusOutcome::Submitted,
            NativeOutcome::Deferred => FocusOutcome::Deferred,
            NativeOutcome::Cancelled => FocusOutcome::Cancelled,
        }))
    }
}

impl Drop for NativeTask {
    fn drop(&mut self) {
        self.cancelled.store(true, Ordering::Release);
    }
}

pub(super) struct RelocationPlan {
    pub(super) identity: Identity,
    pub(super) target: Bounds,
    pub(super) work_area: Bounds,
    pub(super) maximized: bool,
    pub(super) destination_dpi: u32,
}

pub(in super::super) struct WindowRelocation {
    pub(super) plan: RelocationPlan,
    task: NativeTask,
    corrected: bool,
}

impl WindowRelocation {
    pub(super) fn start(plan: RelocationPlan, steps: Vec<NativeStep>, workspace: &Workspace) -> WindowDispatch<Self> {
        let Some(task) = workspace.operations.start(&plan.identity, steps)? else { return Ok(None); };
        Ok(Some(Self { plan, task, corrected: false }))
    }

    pub(in super::super) fn poll(&mut self, workspace: &Workspace) -> Result<bool> {
        if !self.plan.identity.valid() {
            return Err("程序窗口已关闭或改变，无法完成移动。".into());
        }
        if workspace.current() != self.plan.work_area {
            return Err("桌面工作区已改变，停止调整窗口位置。".into());
        }
        workspace.monitor()?;
        if !self.task.poll(&workspace.operations)? {
            return Ok(false);
        }
        let window = self.plan.identity.window();
        if !workspace.on_display(window) || unsafe { IsIconic(window) } != 0 {
            return Ok(false);
        }
        let dpi = unsafe { GetDpiForWindow(window) };
        let awareness = unsafe {
            GetAwarenessFromDpiAwarenessContext(GetWindowDpiAwarenessContext(window))
        };
        if awareness == DPI_AWARENESS_PER_MONITOR_AWARE && dpi != self.plan.destination_dpi {
            return Ok(false);
        }
        if (unsafe { IsZoomed(window) } != 0) != self.plan.maximized {
            return Err("程序窗口状态已改变，未继续调整位置。".into());
        }
        let mut placement: WINDOWPLACEMENT = unsafe { zeroed() };
        placement.length = size_of::<WINDOWPLACEMENT>() as u32;
        if unsafe { GetWindowPlacement(window, &mut placement) } == 0 {
            return Err(failure("复核程序窗口位置失败"));
        }
        if (bounds(placement.rcNormalPosition).within_tolerance(self.plan.target, 2) || self.corrected)
            && workspace.placed(window)
        {
            return Ok(true);
        }
        if !self.corrected {
            placement.rcNormalPosition = rect(self.plan.target);
            placement.flags = 0;
            placement.showCmd = SW_SHOWNA as u32;
            self.corrected = self.task.next(&self.plan.identity, &workspace.operations, NativeStep::Placement(placement))?;
        }
        Ok(false)
    }
}

pub(super) struct WindowConstraint {
    pub(super) identity: Identity,
    pub(super) task: NativeTask,
}


pub(in super::super) struct WindowRestoration {
    identity: Identity,
    maximized: bool,
    task: Option<NativeTask>,
}

impl WindowRestoration {
    pub(super) fn start(identity: Identity, placement: WINDOWPLACEMENT, maximized: bool, workspace: &Workspace) -> WindowDispatch<Self> {
        let task = if unsafe { IsIconic(identity.window()) } != 0 {
            let Some(task) = workspace.operations.start(&identity, vec![NativeStep::Placement(placement)])? else { return Ok(None); };
            Some(task)
        } else {
            None
        };
        Ok(Some(Self { identity, maximized, task }))
    }

    pub(in super::super) fn poll(&mut self, workspace: &Workspace) -> Result<bool> {
        if !self.identity.valid() {
            return Err("程序窗口已关闭或改变，无法完成还原。".into());
        }
        workspace.monitor()?;
        if let Some(task) = &mut self.task {
            if !task.poll(&workspace.operations)? {
                return Ok(false);
            }
        }
        if unsafe { IsIconic(self.identity.window()) } != 0 {
            return Ok(false);
        }
        if !workspace.on_display(self.identity.window()) {
            return Err("程序窗口已离开所选屏幕，停止还原。".into());
        }
        if (unsafe { IsZoomed(self.identity.window()) } != 0) != self.maximized {
            return Err("程序窗口还原状态已改变。".into());
        }
        Ok(true)
    }
}
