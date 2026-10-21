use super::*;
use std::sync::Mutex;
use windows_sys::Win32::UI::Accessibility::{HWINEVENTHOOK, SetWinEventHook, UnhookWinEvent};

static EVENTS: Mutex<Vec<(u32, usize)>> = Mutex::new(Vec::new());

unsafe extern "system" fn window_event(
    _: HWINEVENTHOOK,
    event: u32,
    hwnd: HWND,
    object: i32,
    child: i32,
    _: u32,
    _: u32,
) {
    if !matches!(
        event,
        EVENT_SYSTEM_FOREGROUND
            | EVENT_SYSTEM_MOVESIZESTART
            | EVENT_SYSTEM_MOVESIZEEND
            | EVENT_SYSTEM_MINIMIZESTART
            | EVENT_SYSTEM_MINIMIZEEND
            | EVENT_OBJECT_CREATE
            | EVENT_OBJECT_DESTROY
            | EVENT_OBJECT_SHOW
            | EVENT_OBJECT_HIDE
            | EVENT_OBJECT_LOCATIONCHANGE
            | EVENT_OBJECT_NAMECHANGE
            | EVENT_OBJECT_CLOAKED
            | EVENT_OBJECT_UNCLOAKED
    ) || hwnd.is_null()
        || (event >= EVENT_OBJECT_CREATE && (object != OBJID_WINDOW || child != 0))
    {
        return;
    }
    if let Ok(mut queue) = EVENTS.lock() {
        let item = (event, hwnd as usize);
        if queue.len() >= 512 {
            queue.clear();
            // 溢出时使窗口句柄失效并触发全量校准，不能丢掉销毁事件后继续激活旧窗口。
            queue.push((0, 0));
        }
        // 只合并相邻重复通知，保留移动边界与窗口销毁的先后顺序。
        if queue.last() != Some(&item) {
            queue.push(item);
        }
    }
}

pub(super) struct WinEvents(Vec<HWINEVENTHOOK>);

impl WinEvents {
    pub(super) fn start() -> Result<Self> {
        let mut hooks = Self(Vec::new());
        for (start, end) in [
            (EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_MINIMIZEEND),
            (EVENT_OBJECT_CREATE, EVENT_OBJECT_NAMECHANGE),
            (EVENT_OBJECT_CLOAKED, EVENT_OBJECT_UNCLOAKED),
        ] {
            let handle = unsafe {
                SetWinEventHook(
                    start,
                    end,
                    null_mut(),
                    Some(window_event),
                    0,
                    0,
                    WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
                )
            };
            if handle.is_null() {
                return Err(failure("listen for desktop window changes"));
            }
            hooks.0.push(handle);
        }
        Ok(hooks)
    }

    pub(super) fn drain(&self) -> Vec<(u32, usize)> {
        EVENTS
            .lock()
            .map(|mut queue| std::mem::take(&mut *queue))
            .unwrap_or_default()
    }
}

impl Drop for WinEvents {
    fn drop(&mut self) {
        for hook in &self.0 {
            unsafe {
                UnhookWinEvent(*hook);
            }
        }
        if let Ok(mut queue) = EVENTS.lock() {
            queue.clear();
        }
    }
}
