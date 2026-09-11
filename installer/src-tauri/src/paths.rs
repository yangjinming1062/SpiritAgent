//! SPIRITAGENT_HOME 路径与日志初始化；必须与 Python/JS/Bash 解析器完全一致，否则安装器与 install.ps1 会读写不同位置。
//!
//! Windows: %LOCALAPPDATA%\SpiritAgent
//! macOS:   ~/Library/Application Support/SpiritAgent

use std::path::{Path, PathBuf};
#[cfg(target_os = "macos")]
use std::process::Command;
use tracing_appender::non_blocking::WorkerGuard;

pub fn spiritagent_home() -> PathBuf {
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

    // Linux / fallback
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

/// 安装完成后将安装器自拷贝到的稳定位置；快捷方式可指向此处，便于重装/修复。位于 SPIRITAGENT_HOME 下，仓库删除后仍保留。
pub fn installer_dest() -> PathBuf {
    let name = if cfg!(target_os = "windows") {
        "spiritagent-setup.exe"
    } else {
        "spiritagent-setup"
    };
    spiritagent_home().join(name)
}

/// 把当前运行的安装器二进制拷贝到 `installer_dest()`，给快捷方式一个稳定目标。
///
/// 当运行位置已为最终位置时直接 no-op（自我拷贝在 Windows 下会触发共享冲突）。最佳努力：失败不应中断安装。
pub fn copy_self_to_spiritagent_home() -> std::io::Result<()> {
    let src = std::env::current_exe()?;
    let dest = installer_dest();

    // canonicalize 双方规避符号链接/8.3 短名/大小写差异引起的误判拷贝。
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
    // 拷贝后的文件可能继承下载安装器的 quarantine 属性；桌面快捷方式会再次启动它，需先祛除隔离属性。
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
                // 显式未签名（如本地开发/无证书构建）：补 ad-hoc 签名以保证 Apple Silicon / macOS 能直接执行
                tracing::info!(?path, "installer binary is unsigned; applying ad-hoc signature");
                let sign_res = Command::new("/usr/bin/codesign")
                    .args(["--force", "--sign", "-"])
                    .arg(path)
                    .status();
                if let Err(e) = sign_res {
                    tracing::warn!(?path, ?e, "failed to apply ad-hoc signature to installer copy");
                }
            } else if info.contains("Signature=adhoc") {
                // 显式 ad-hoc 签名：校验有效性，若损坏则重新补签
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
                // 显式具备权威证书签名（Developer ID / Authority 等）：严格校验，严禁静默降级覆盖为 ad-hoc
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

/// install.ps1 写入 bootstrap-complete 标记的位置（仅存在与否，供 macOS 启动快路径判断）。
#[allow(dead_code)]
pub fn likely_bootstrap_marker(install_root: &Path) -> PathBuf {
    install_root.join(".spiritagent-bootstrap-complete")
}

/// Runner uv venv 中的 Python 二进制路径。两者皆不存在时返回 `None`，调用方应视为"venv 不健康"并拒绝。
///
/// 与 `runner/utils/path_helpers.py::find_python()` 的候选列表保持一致，避免 Windows uv 仅产出 python3.exe 时被误判为缺失。
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

/// 初始化 tracing，输出到 SPIRITAGENT_HOME/logs/bootstrap-installer.log；返回的 guard 在 drop 时 flush，须在进程生命周期内持有。
pub fn init_logging() -> Option<WorkerGuard> {
    let dir = log_dir();
    if let Err(err) = std::fs::create_dir_all(&dir) {
        // 日志目录创建失败时仅向 stderr 输出，不 panic；安装器仍应在异常文件系统下可用。
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
