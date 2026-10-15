use super::*;
use std::cell::Cell;
use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
use windows_sys::Win32::UI::Shell::{
    ABE_BOTTOM, ABM_ACTIVATE, ABM_NEW, ABM_QUERYPOS, ABM_REMOVE, ABM_SETPOS,
    ABM_WINDOWPOSCHANGED, ABN_POSCHANGED, ABN_STATECHANGE, APPBARDATA, SHAppBarMessage,
};

const CALLBACK: u32 = WM_APP + 0x534;
const TOKEN_PROPERTY: &str = "SpiritAgentDesktopAppBarToken";

fn data(window: HWND) -> APPBARDATA {
    APPBARDATA {
        cbSize: size_of::<APPBARDATA>() as u32,
        hWnd: window,
        ..unsafe { zeroed() }
    }
}

struct State {
    registered: Cell<bool>,
    registration_reset: Cell<bool>,
    dirty: Cell<bool>,
    taskbar_created: u32,
}

unsafe extern "system" fn window_proc(
    window: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    if message == WM_NCCREATE {
        let create = unsafe { &*(lparam as *const CREATESTRUCTW) };
        unsafe {
            SetWindowLongPtrW(window, GWLP_USERDATA, create.lpCreateParams as isize);
        }
    }
    let pointer = unsafe { GetWindowLongPtrW(window, GWLP_USERDATA) } as *const State;
    if let Some(state) = unsafe { pointer.as_ref() } {
        if message == state.taskbar_created {
            // DPI 变化也会发送此消息，不能假设 Shell 已移除旧登记。
            state.registration_reset.set(true);
            state.dirty.set(true);
            return 0;
        }
        match message {
            CALLBACK => {
                if wparam as u32 == ABN_POSCHANGED || wparam as u32 == ABN_STATECHANGE {
                    // Shell 回调只登记变化；协商由持有恢复锁的会话轮询执行。
                    state.dirty.set(true);
                }
                return 0;
            }
            WM_WINDOWPOSCHANGED if state.registered.get() => unsafe {
                SHAppBarMessage(ABM_WINDOWPOSCHANGED, &mut data(window));
            },
            WM_ACTIVATE if state.registered.get() => {
                let mut value = data(window);
                value.lParam = (wparam as u32 & 0xffff != WA_INACTIVE) as LPARAM;
                unsafe {
                    SHAppBarMessage(ABM_ACTIVATE, &mut value);
                }
            }
            WM_DESTROY if state.registered.replace(false) => unsafe {
                SHAppBarMessage(ABM_REMOVE, &mut data(window));
            },
            WM_NCHITTEST => return HTTRANSPARENT as LRESULT,
            WM_MOUSEACTIVATE => return MA_NOACTIVATEANDEAT as LRESULT,
            WM_ERASEBKGND => return 1,
            WM_NCDESTROY => unsafe {
                SetWindowLongPtrW(window, GWLP_USERDATA, 0);
            },
            _ => {}
        }
    }
    unsafe { DefWindowProcW(window, message, wparam, lparam) }
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(in super::super) struct RestoreAppBar {
    identity: Identity,
    token: u64,
    device: String,
    monitor: Bounds,
    original: Bounds,
}

impl RestoreAppBar {
    pub(in super::super) fn validate(
        &self,
        host_pid: u32,
        host_created: u64,
        display: Bounds,
    ) -> Result<()> {
        if self.identity.handle == 0
            || self.identity.handle > usize::MAX as u64
            || self.identity.pid != host_pid
            || self.identity.created != host_created
            || self.token == 0
            || self.token > usize::MAX as u64
            || self.identity.class != format!("SpiritAgentDesktopAppBar-{:x}", self.token)
            || self.device.is_empty()
            || self.device.len() > 128
            || self.monitor != display
            || !self.monitor.valid()
            || !self.original.valid()
            || !contains(self.monitor, self.original)
        {
            return Err("invalid AppBar recovery record".into());
        }
        Ok(())
    }

    fn live(&self) -> bool {
        self.identity.valid()
            && unsafe {
                GetPropW(self.identity.window(), wide(TOKEN_PROPERTY).as_ptr()) as usize as u64
            } == self.token
    }

    pub(in super::super) fn restore(&self) -> Result<()> {
        let window = self.identity.window();
        if self.live() {
            if unsafe { SHAppBarMessage(ABM_REMOVE, &mut data(window)) } == 0 {
                return Err("Windows rejected desktop AppBar removal".into());
            }
            unsafe {
                RemovePropW(window, wide(TOKEN_PROPERTY).as_ptr());
            }
        } else if unsafe { IsWindow(window) } == 0
            && !process(self.identity.pid)
                .and_then(|owner| created(owner.0))
                .is_ok_and(|birth| birth == self.identity.created)
            && unsafe { IsWindow(window) } == 0
        {
            // 进程退出已销毁 HWND；只清理无窗口占用的旧登记，不能操作复用的 HWND。
            unsafe {
                SHAppBarMessage(ABM_REMOVE, &mut data(window));
            }
        }
        Ok(())
    }
}

pub(super) struct AppBar {
    record: RestoreAppBar,
    state: Box<State>,
    class: Vec<u16>,
    instance: HINSTANCE,
    owner_thread: u32,
    bottom: i32,
    position: Option<Bounds>,
    work_area: Option<Bounds>,
}

impl AppBar {
    pub(super) fn prepare(original: &RestoreWorkArea, bottom: i32) -> Result<Self> {
        let token = ((SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|error| error.to_string())?
            .as_nanos() as u64
            ^ u64::from(unsafe { GetCurrentProcessId() }))
            & usize::MAX as u64)
        .max(1);
        let class = wide(&format!("SpiritAgentDesktopAppBar-{token:x}"));
        let instance = unsafe { GetModuleHandleW(null()) };
        if instance.is_null() {
            return Err(failure("load desktop AppBar module"));
        }
        let definition = WNDCLASSW {
            lpfnWndProc: Some(window_proc),
            hInstance: instance,
            lpszClassName: class.as_ptr(),
            ..unsafe { zeroed() }
        };
        if unsafe { RegisterClassW(&definition) } == 0 {
            return Err(failure("register desktop AppBar window class"));
        }
        let mut state = Box::new(State {
            registered: Cell::new(false),
            registration_reset: Cell::new(false),
            dirty: Cell::new(true),
            taskbar_created: unsafe { RegisterWindowMessageW(wide("TaskbarCreated").as_ptr()) },
        });
        if state.taskbar_created == 0 {
            unsafe {
                UnregisterClassW(class.as_ptr(), instance);
            }
            return Err(failure("register Explorer restart notification"));
        }
        let monitor = original.monitor;
        let window = unsafe {
            CreateWindowExW(
                WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
                class.as_ptr(),
                wide("").as_ptr(),
                WS_POPUP,
                monitor.x,
                bottom,
                monitor.width,
                (monitor.y + monitor.height - bottom).max(1),
                null_mut(),
                null_mut(),
                instance,
                state.as_mut() as *mut State as *const _,
            )
        };
        if window.is_null() {
            unsafe {
                UnregisterClassW(class.as_ptr(), instance);
            }
            return Err(failure("create desktop AppBar window"));
        }
        let captured = Identity::capture(window);
        let initialized = unsafe {
            SetLayeredWindowAttributes(window, 0, 0, LWA_ALPHA) != 0
                && SetPropW(window, wide(TOKEN_PROPERTY).as_ptr(), token as usize as HANDLE) != 0
        };
        let record = match captured {
            Ok(identity) if initialized => RestoreAppBar {
                identity,
                token,
                device: original.device.clone(),
                monitor,
                original: original.original,
            },
            result => {
                let reason = result.err().unwrap_or_else(|| failure("initialize desktop AppBar"));
                unsafe {
                    DestroyWindow(window);
                    UnregisterClassW(class.as_ptr(), instance);
                }
                return Err(reason);
            }
        };
        Ok(Self {
            record,
            state,
            class,
            instance,
            owner_thread: unsafe { GetCurrentThreadId() },
            bottom,
            position: None,
            work_area: None,
        })
    }

    pub(super) fn restore_record(&self) -> RestoreAppBar {
        self.record.clone()
    }

    pub(super) fn ensure(&mut self, current: &MONITORINFOEXW) -> Result<Bounds> {
        if !self.record.live() {
            return Err("desktop AppBar window was destroyed or restored".into());
        }
        let window = self.record.identity.window();
        if self.state.registration_reset.replace(false) {
            // 与 guardian 共用恢复锁的调用方在此幂等注销，再恢复登记。
            unsafe {
                SHAppBarMessage(ABM_REMOVE, &mut data(window));
            }
            self.state.registered.set(false);
            self.position = None;
            self.work_area = None;
        }
        if !self.state.registered.get() {
            let mut value = data(window);
            value.uCallbackMessage = CALLBACK;
            if unsafe { SHAppBarMessage(ABM_NEW, &mut value) } == 0 {
                return Err("Windows rejected desktop AppBar registration".into());
            }
            self.state.registered.set(true);
            self.state.dirty.set(true);
            self.position = None;
        }
        if self.state.dirty.replace(false)
            || self.work_area != Some(bounds(current.monitorInfo.rcWork))
        {
            let monitor = self.record.monitor;
            let mut value = data(window);
            value.uEdge = ABE_BOTTOM;
            value.rc = RECT {
                left: monitor.x,
                top: self.bottom,
                right: monitor.x + monitor.width,
                bottom: monitor.y + monitor.height,
            };
            unsafe {
                SHAppBarMessage(ABM_QUERYPOS, &mut value);
            }
            // Query 已扣除任务栏和其他 AppBar；只补足到产品底栏上沿。
            value.rc.top = self.bottom.min(value.rc.bottom);
            let proposed = bounds(value.rc);
            if self.position != Some(proposed) {
                unsafe {
                    SHAppBarMessage(ABM_SETPOS, &mut value);
                }
                let position = bounds(value.rc);
                if position.width <= 0 || position.height < 0 {
                    return Err("Windows returned an invalid desktop AppBar rectangle".into());
                }
                if unsafe {
                    SetWindowPos(
                        window,
                        HWND_BOTTOM,
                        position.x,
                        position.y,
                        position.width,
                        position.height,
                        SWP_NOACTIVATE | SWP_SHOWWINDOW,
                    )
                } == 0
                {
                    return Err(failure("position desktop AppBar window"));
                }
                self.position = Some(position);
            }
        }
        let current = find_monitor(&self.record.device)?.ok_or("desktop monitor was removed")?;
        let actual = bounds(current.monitorInfo.rcWork);
        if bounds(current.monitorInfo.rcMonitor) != self.record.monitor
            || !actual.valid()
            || !contains(self.record.monitor, actual)
            || actual.y + actual.height > self.bottom
        {
            return Err(format!("Windows rejected the desktop bottom reservation: {actual:?}"));
        }
        self.work_area = Some(actual);
        Ok(actual)
    }
}

impl Drop for AppBar {
    fn drop(&mut self) {
        // HWND 与回调状态由创建线程持有，销毁后才释放 Box 和窗口类。
        if unsafe { GetCurrentThreadId() } != self.owner_thread {
            emit(json!({ "event": "failure", "reason": "desktop AppBar moved to another thread" }));
            std::process::exit(1);
        }
        if self.state.registered.replace(false) {
            let _ = self.record.restore();
        }
        if self.record.identity.valid() {
            unsafe {
                DestroyWindow(self.record.identity.window());
            }
        }
        unsafe {
            UnregisterClassW(self.class.as_ptr(), self.instance);
        }
    }
}
