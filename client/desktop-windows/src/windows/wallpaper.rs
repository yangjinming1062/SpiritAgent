use super::*;
use std::ffi::OsString;
use std::os::windows::{ffi::OsStringExt, io::AsRawHandle};
use windows_sys::Win32::Graphics::Gdi::{
    IntersectRect, RDW_ALLCHILDREN, RDW_ERASE, RDW_INVALIDATE, RedrawWindow,
};
use windows_sys::Win32::System::Registry::{HKEY_CURRENT_USER, RRF_RT_REG_DWORD, RegGetValueW};

pub(super) fn redraw_released_display(target: Bounds) -> Result<()> {
    let shell = unsafe { GetShellWindow() };
    let pid = window_pid(shell);
    if shell.is_null() || !is_explorer(pid) {
        // Explorer 重启时会自行重建壁纸。
        return Ok(());
    }
    let mut layers = top_windows()?;
    // 新版 Explorer 将实际壁纸绘制在 Progman 的 WorkerW 子窗。
    let wallpaper = child(shell, "WorkerW");
    if !wallpaper.is_null() {
        layers.push(wallpaper);
    }
    for window in layers {
        if window_pid(window) != pid
            || unsafe { IsWindowVisible(window) } == 0
            || !["Progman", "WorkerW"].contains(&class_name(window).as_str())
        {
            continue;
        }
        let mut origin = POINT {
            x: target.x,
            y: target.y,
        };
        let mut client = RECT::default();
        if unsafe { ScreenToClient(window, &mut origin) } == 0
            || unsafe { GetClientRect(window, &mut client) } == 0
        {
            return Err(failure("released desktop redraw coordinates"));
        }
        let region = RECT {
            left: origin.x,
            top: origin.y,
            right: origin.x + target.width,
            bottom: origin.y + target.height,
        };
        let mut update = RECT::default();
        if unsafe { IntersectRect(&mut update, &region, &client) } != 0
            && unsafe {
                RedrawWindow(
                    window,
                    &update,
                    null_mut(),
                    RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN,
                )
            } == 0
        {
            return Err(failure("redraw released desktop display"));
        }
    }
    Ok(())
}

pub(super) fn reload_after_release() -> Result<()> {
    if !single_static_picture() {
        return Ok(());
    }
    // SPI 会读取壁纸源文件。独立进程限制恢复锁内的等待时间。
    let mut child =
        ProcessCommand::new(std::env::current_exe().map_err(|error| error.to_string())?)
            .arg("refresh-static-wallpaper")
            .creation_flags(CREATE_NO_WINDOW)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|error| format!("start Windows wallpaper refresh: {error}"))?;
    match unsafe { WaitForSingleObject(child.as_raw_handle(), 1_000) } {
        WAIT_OBJECT_0 => {}
        wait => {
            let reason = if wait == WAIT_TIMEOUT {
                "Windows wallpaper refresh timed out".into()
            } else {
                failure("wait for Windows wallpaper refresh")
            };
            child
                .kill()
                .map_err(|error| format!("{reason}; stop refresh: {error}"))?;
            return Err(reason);
        }
    }
    let output = child
        .wait_with_output()
        .map_err(|error| format!("collect Windows wallpaper refresh: {error}"))?;
    if output.status.success() {
        return Ok(());
    }
    let event: serde_json::Value = serde_json::from_slice(&output.stdout)
        .map_err(|_| format!("Windows wallpaper refresh exited with {}", output.status))?;
    Err(event
        .get("reason")
        .and_then(|value| value.as_str())
        .unwrap_or("Windows wallpaper refresh failed")
        .to_owned())
}

fn single_static_picture() -> bool {
    if unsafe { GetSystemMetrics(SM_CMONITORS) } != 1 {
        return false;
    }
    let mut mode = u32::MAX;
    let mut size = size_of::<u32>() as u32;
    let status = unsafe {
        RegGetValueW(
            HKEY_CURRENT_USER,
            wide(r"Software\Microsoft\Windows\CurrentVersion\Explorer\Wallpapers").as_ptr(),
            wide("BackgroundType").as_ptr(),
            RRF_RT_REG_DWORD,
            null_mut(),
            (&mut mode as *mut u32).cast(),
            &mut size,
        )
    };
    status == ERROR_SUCCESS && mode == 0
}

pub(crate) fn refresh_static_wallpaper() -> Result<()> {
    let shell = unsafe { GetShellWindow() };
    if shell.is_null() || !is_explorer(window_pid(shell)) || !single_static_picture() {
        return Ok(());
    }
    let mut source = [0u16; MAX_PATH as usize];
    if unsafe {
        SystemParametersInfoW(
            SPI_GETDESKWALLPAPER,
            MAX_PATH,
            source.as_mut_ptr().cast(),
            0,
        )
    } == 0
    {
        return Err(failure("read current Windows wallpaper"));
    }
    let Some(end) = source.iter().position(|&unit| unit == 0) else {
        return Ok(());
    };
    let path = PathBuf::from(OsString::from_wide(&source[..end]));
    if !path.is_absolute() || !path.is_file() || !single_static_picture() {
        return Ok(());
    }
    // 重载当前图片以恢复合成表面，SPI 标志为零时不写用户配置、不广播设置变更。
    if unsafe { SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, source.as_mut_ptr().cast(), 0) } == 0
    {
        return Err(failure("reload current Windows wallpaper"));
    }
    Ok(())
}
