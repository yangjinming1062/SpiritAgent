//! SPIRITAGENT_HOME 与日志初始化；须与 Python/JS/Bash 解析器一致（Windows `%LOCALAPPDATA%\SpiritAgent`，macOS `~/Library/Application Support/SpiritAgent`）。

use std::path::{Path, PathBuf};
#[cfg(target_os = "macos")]
use std::process::Command;
use tracing_appender::non_blocking::WorkerGuard;

pub fn spiritagent_home() -> PathBuf {
    // 与 install 脚本、runner 一致：环境变量 > 平台默认。
    if let Ok(home) = std::env::var("SPIRITAGENT_HOME") {
        if !home.is_empty() {
            return PathBuf::from(home);
        }
    }

    #[cfg(target_os = "windows")]
    {
        if let Some(local_app_data) = dirs::data_local_dir() {
            return local_app_data.join("SpiritAgent");
        }
    }

    #[cfg(target_os = "macos")]
    {
        if let Some(home) = dirs::home_dir() {
            return home.join("Library/Application Support/SpiritAgent");
        }
    }

    // 其它平台 fallback
    if let Some(home) = dirs::home_dir() {
        return home.join(".spiritagent");
    }

    PathBuf::from(".spiritagent")
}

pub fn log_dir() -> PathBuf {
    spiritagent_home().join("logs")
}

pub fn log_path() -> PathBuf {
    log_dir().join("bootstrap-installer.log")
}

/// 自拷贝稳定目标（`$SPIRITAGENT_HOME/spiritagent-setup[.exe]`），快捷方式可指向此处。
pub fn installer_dest() -> PathBuf {
    let name = if cfg!(target_os = "windows") {
        "spiritagent-setup.exe"
    } else {
        "spiritagent-setup"
    };
    spiritagent_home().join(name)
}

/// 自拷贝到 `installer_dest()`；已在目标位置则 no-op（Windows 自拷贝会共享冲突）。最佳努力，失败不中断安装。
pub fn copy_self_to_spiritagent_home() -> std::io::Result<()> {
    let src = std::env::current_exe()?;
    let dest = installer_dest();

    // canonicalize 规避符号链接/8.3 短名/大小写差异。
    let same = match (src.canonicalize(), dest.canonicalize()) {
        (Ok(a), Ok(b)) => a == b,
        _ => src == dest,
    };
    if same {
        tracing::info!(?dest, "installer already at destination; skipping self-copy");
        return Ok(());
    }

    if let Some(parent) = dest.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::copy(&src, &dest)?;
    repair_macos_installer_helper(&dest);
    tracing::info!(?src, ?dest, "copied installer to SPIRITAGENT_HOME");
    Ok(())
}

#[cfg(target_os = "macos")]
fn repair_macos_installer_helper(path: &Path) {
    // 清 quarantine，避免快捷方式启动被系统拦截。
    let xattr_status = Command::new("/usr/bin/xattr")
        .args(["-cr"])
        .arg(path)
        .status();
    if let Err(e) = xattr_status {
        tracing::warn!(?path, ?e, "failed to execute xattr -cr on installer copy");
    }

    let display_out = Command::new("/usr/bin/codesign")
        .args(["-d", "--verbose=2"])
        .arg(path)
        .output();

    match display_out {
        Ok(out) => {
            let info = String::from_utf8_lossy(&out.stderr);
            if !out.status.success() || info.contains("code object is not signed at all") {
                // 未签名：补 ad-hoc 以便执行
                tracing::info!(?path, "installer binary is unsigned; applying ad-hoc signature");
                let sign_res = Command::new("/usr/bin/codesign")
                    .args(["--force", "--sign", "-"])
                    .arg(path)
                    .status();
                if let Err(e) = sign_res {
                    tracing::warn!(?path, ?e, "failed to apply ad-hoc signature to installer copy");
                }
            } else if info.contains("Signature=adhoc") {
                // ad-hoc 损坏则重签
                let verify = Command::new("/usr/bin/codesign")
                    .arg("--verify")
                    .arg(path)
                    .status();
                if !matches!(verify, Ok(status) if status.success()) {
                    tracing::info!(?path, "installer ad-hoc signature invalid; re-signing ad-hoc");
                    let _ = Command::new("/usr/bin/codesign")
                        .args(["--force", "--sign", "-"])
                        .arg(path)
                        .status();
                }
            } else {
                // 证书签名：只校验，禁止降级为 ad-hoc
                let verify = Command::new("/usr/bin/codesign")
                    .arg("--verify")
                    .arg(path)
                    .status();
                match verify {
                    Ok(status) if status.success() => {
                        tracing::debug!(?path, "installer binary developer signature is valid");
                    }
                    Ok(status) => {
                        tracing::error!(
                            ?path,
                            ?status,
                            "installer binary has developer signature but failed verification; preserving signature without ad-hoc downgrade"
                        );
                    }
                    Err(e) => {
                        tracing::error!(
                            ?path,
                            ?e,
                            "failed to verify installer developer signature"
                        );
                    }
                }
            }
        }
        Err(e) => {
            tracing::warn!(?path, ?e, "could not query installer codesign status");
        }
    }
}

#[cfg(not(target_os = "macos"))]
fn repair_macos_installer_helper(_path: &Path) {}

/// Runner uv venv 中的 Python；与 `path_helpers.py::find_python()` 候选一致，皆无则视为 venv 不健康。
pub fn runner_venv_python() -> Option<PathBuf> {
    let root = spiritagent_home().join("runner").join(".venv");
    let candidates: [PathBuf; 2] = if cfg!(target_os = "windows") {
        [
            root.join("Scripts").join("python.exe"),
            root.join("Scripts").join("python3.exe"),
        ]
    } else {
        [
            root.join("bin").join("python"),
            root.join("bin").join("python3"),
        ]
    };
    candidates.into_iter().find(|p| p.is_file())
}

/// 初始化 tracing 到 bootstrap-installer.log；返回的 guard 须在进程生命周期内持有以 flush。
pub fn init_logging() -> Option<WorkerGuard> {
    let dir = log_dir();
    if let Err(err) = std::fs::create_dir_all(&dir) {
        // 日志目录失败只打 stderr，安装器仍需可用
        eprintln!("[spiritagent-setup] could not create log dir {dir:?}: {err}");
        return None;
    }

    let file_appender = tracing_appender::rolling::never(&dir, "bootstrap-installer.log");
    let (non_blocking, guard) = tracing_appender::non_blocking(file_appender);

    let env_filter = tracing_subscriber::EnvFilter::try_from_env("SPIRITAGENT_BOOTSTRAP_LOG")
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info"));

    tracing_subscriber::fmt()
        .with_env_filter(env_filter)
        .with_writer(non_blocking)
        .with_ansi(false)
        .with_target(true)
        .init();

    Some(guard)
}

#[tauri::command]
pub fn get_log_path() -> String {
    log_path().to_string_lossy().into_owned()
}

#[tauri::command]
pub fn get_spiritagent_home() -> String {
    spiritagent_home().to_string_lossy().into_owned()
}

#[tauri::command]
pub fn open_log_dir(app: tauri::AppHandle) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    let path = log_dir();
    app.opener()
        .open_path(path.to_string_lossy(), None::<&str>)
        .map_err(|e| e.to_string())
}
