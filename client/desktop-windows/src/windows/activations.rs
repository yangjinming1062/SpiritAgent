use super::*;
use super::super::workspace::{FocusOutcome, FocusRequest, FocusReservation, WindowRestoration};

#[derive(Clone, Copy)]
enum PlacementGoal {
    OnDisplay,
    WorkArea,
}

impl PlacementGoal {
    fn for_window(window: HWND, workspace: &Workspace) -> Self {
        if workspace.on_display(window) { Self::OnDisplay } else { Self::WorkArea }
    }

    fn satisfied(self, window: HWND, workspace: &Workspace) -> bool {
        match self {
            Self::OnDisplay => workspace.on_display(window) && unsafe { IsIconic(window) } == 0,
            Self::WorkArea => workspace.placed(window),
        }
    }

    fn refresh(&mut self, window: HWND, workspace: &Workspace) {
        if !workspace.on_display(window) {
            *self = Self::WorkArea;
        }
    }
}

struct ActivationTarget {
    identity: Identity,
    goal: PlacementGoal,
}

enum Phase {
    PreparingRoot,
    MovingRoot(WindowRelocation),
    RestoringRoot(WindowRestoration),
    ResolvingTarget,
    PreparingTarget,
    MovingTarget(WindowRelocation),
    RestoringTarget(WindowRestoration),
    ReservingFocus,
    Authorizing(FocusReservation),
    Focusing(FocusRequest),
    AwaitingForeground,
}

pub(in super::super) enum FocusAuthorization {
    Accepted,
    Deferred,
    Stale,
    Completed,
}

impl FocusAuthorization {
    pub(in super::super) fn as_str(&self) -> &'static str {
        match self {
            Self::Accepted => "accepted",
            Self::Deferred => "deferred",
            Self::Stale => "stale",
            Self::Completed => "completed",
        }
    }
}

pub(super) struct PendingActivation {
    root: Identity,
    root_goal: PlacementGoal,
    target: Option<ActivationTarget>,
    activation_id: String,
    authorization_revision: u64,
    authorization_id: Option<String>,
    window_id: String,
    parent_pid: u32,
    foreground_at_request: usize,
    phase: Phase,
}

impl PendingActivation {
    pub(super) fn start(
        root: Identity,
        activation_id: String,
        window_id: String,
        parent_pid: u32,
        workspace: &Workspace,
    ) -> Result<Self> {
        Ok(Self {
            root_goal: PlacementGoal::for_window(root.window(), workspace),
            root,
            target: None,
            activation_id,
            authorization_revision: 0,
            authorization_id: None,
            window_id,
            parent_pid,
            foreground_at_request: foreground_window() as usize,
            phase: Phase::PreparingRoot,
        })
    }

    pub(super) fn root(&self) -> &Identity {
        &self.root
    }

    fn replan(&mut self, phase: Phase) {
        // 任何重新准备都释放原预留并废弃授权；重试必须取得新的前台授权。
        self.authorization_id = None;
        self.phase = phase;
    }

    pub(super) fn authorize(&mut self, activation_id: &str, granted: bool, workspace: &Workspace) -> Result<FocusAuthorization> {
        if self.authorization_id.as_deref() != Some(activation_id) {
            return Ok(FocusAuthorization::Stale);
        }
        // 授权往返期间窗口、模态分支和用户前台都可能变化，执行前复用同一状态检查。
        if self.poll(workspace)? {
            return Ok(FocusAuthorization::Completed);
        }
        if self.authorization_id.as_deref() != Some(activation_id) {
            return Ok(FocusAuthorization::Deferred);
        }
        if !granted || foreground_window().is_null() {
            self.replan(Phase::ReservingFocus);
            return Ok(FocusAuthorization::Deferred);
        }
        let phase = std::mem::replace(&mut self.phase, Phase::ReservingFocus);
        let Phase::Authorizing(reservation) = phase else {
            self.phase = phase;
            return Ok(FocusAuthorization::Stale);
        };
        self.authorization_id = None;
        let target = self.target.as_ref().ok_or("程序激活目标丢失。")?;
        self.phase = Phase::Focusing(reservation.start(&self.root, &target.identity, self.foreground_at_request, workspace)?);
        Ok(FocusAuthorization::Accepted)
    }

    pub(super) fn poll(&mut self, workspace: &Workspace) -> Result<bool> {
        let root = self.root.window();
        if !self.root.valid() {
            return Err("程序窗口已关闭或改变，已停止窗口操作。".into());
        }
        workspace.monitor()?;
        let foreground = foreground_window();
        // Windows 切换前台期间允许暂时没有前台窗口；这不是用户选择了其他程序。
        if foreground.is_null() {
            return Ok(false);
        }
        if self.foreground_at_request == 0 {
            if window_pid(foreground) != self.parent_pid && !owned_by(foreground, root) {
                return Ok(true);
            }
            self.foreground_at_request = foreground as usize;
        }
        if (foreground as usize != self.foreground_at_request && !owned_by(foreground, root))
            || workspace.user_moving(root)
        {
            return Ok(true);
        }

        let preparing_root = matches!(self.phase, Phase::PreparingRoot | Phase::MovingRoot(_) | Phase::RestoringRoot(_));
        if !preparing_root && !self.root_goal.satisfied(root, workspace) {
            self.root_goal.refresh(root, workspace);
            self.target = None;
            self.replan(Phase::PreparingRoot);
        }
        if let Some(target) = &self.target {
            if workspace.user_moving(target.identity.window()) {
                return Ok(true);
            }
            if activation_target(root)? != Some(target.identity.window()) {
                self.target = None;
                self.replan(Phase::ResolvingTarget);
            }
        }

        match &mut self.phase {
            Phase::PreparingRoot => {
                self.root_goal.refresh(root, workspace);
                if self.root_goal.satisfied(root, workspace) {
                    self.phase = Phase::ResolvingTarget;
                } else if matches!(self.root_goal, PlacementGoal::OnDisplay) {
                    if let Some(restoration) = workspace.restore_window(root)? {
                        self.phase = Phase::RestoringRoot(restoration);
                    }
                } else if let Some(relocation) = workspace.move_to_display(root)? {
                    self.phase = Phase::MovingRoot(relocation);
                }
            }
            Phase::MovingRoot(relocation) => {
                if relocation.poll(workspace)? {
                    self.phase = Phase::ResolvingTarget;
                }
            }
            Phase::RestoringRoot(restoration) => {
                if restoration.poll(workspace)? {
                    self.phase = Phase::ResolvingTarget;
                }
            }
            Phase::ResolvingTarget => {
                let Some(target) = activation_target(root)? else { return Ok(false); };
                self.target = Some(ActivationTarget {
                    identity: Identity::capture(target)?,
                    goal: if target == root { self.root_goal } else { PlacementGoal::for_window(target, workspace) },
                });
                self.phase = Phase::PreparingTarget;
            }
            Phase::PreparingTarget => {
                let target = self.target.as_mut().ok_or("程序激活目标丢失。")?;
                let window = target.identity.window();
                target.goal.refresh(window, workspace);
                if target.goal.satisfied(window, workspace) {
                    self.phase = Phase::ReservingFocus;
                } else if matches!(target.goal, PlacementGoal::OnDisplay) {
                    if let Some(restoration) = workspace.restore_window(window)? {
                        self.phase = Phase::RestoringTarget(restoration);
                    }
                } else if let Some(relocation) = workspace.move_to_display(window)? {
                    self.phase = Phase::MovingTarget(relocation);
                }
            }
            Phase::MovingTarget(relocation) => {
                if relocation.poll(workspace)? {
                    self.phase = Phase::ReservingFocus;
                }
            }
            Phase::RestoringTarget(restoration) => {
                if restoration.poll(workspace)? {
                    self.phase = Phase::ReservingFocus;
                }
            }
            Phase::ReservingFocus | Phase::Authorizing(_) | Phase::Focusing(_) | Phase::AwaitingForeground => {
                let target = self.target.as_ref().ok_or("程序激活目标丢失。")?;
                let window = target.identity.window();
                if !target.identity.valid() {
                    return Err("程序窗口已关闭或改变，已停止激活。".into());
                }
                if !target.goal.satisfied(window, workspace) {
                    self.target.as_mut().unwrap().goal.refresh(window, workspace);
                    self.replan(Phase::PreparingTarget);
                    return Ok(false);
                }
                if owned_by(foreground, window) {
                    return Ok(true);
                }
                match &mut self.phase {
                    Phase::ReservingFocus => {
                        let Some(reservation) = workspace.reserve_focus(&target.identity)? else { return Ok(false); };
                        self.authorization_revision += 1;
                        let authorization_id = format!("{}:{}", self.activation_id, self.authorization_revision);
                        self.authorization_id = Some(authorization_id.clone());
                        self.phase = Phase::Authorizing(reservation);
                        emit(json!({ "event": "external_focus", "activation_id": authorization_id, "operation_id": self.activation_id, "authorization_revision": self.authorization_revision, "window_id": self.window_id }));
                    }
                    Phase::Focusing(request) => {
                        if let Some(outcome) = request.poll()? {
                            match outcome {
                                FocusOutcome::Submitted => self.phase = Phase::AwaitingForeground,
                                FocusOutcome::Deferred => self.replan(Phase::ReservingFocus),
                                FocusOutcome::Cancelled => return Ok(true),
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
        Ok(false)
    }
}
