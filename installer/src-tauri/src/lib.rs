//! SpiritAgent Setup 的 Tauri 入口；安装期工作集中于 `bootstrap.rs`，通过 `run()` 注册的命令调用。
//! windows_subsystem 必须写在二进制 crate 的 main.rs，写在 lib 不会作用到链接期。

mod bootstrap;
mod embedded_payload;
mod events;
mod install_script;
mod paths;
mod powershell;

use std::sync::Arc;
use tokio::sync::Mutex;

/// 传入 `--reinstall` 或 `--repair` 时强制进入安装器 UI（绕过 macOS 启动快路径）。
pub fn force_setup_from_args<I, S>(args: I) -> bool
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    args.into_iter()
        .any(|a| a.as_ref() == "--reinstall" || a.as_ref() == "--repair")
}

/// 进程级安装状态。bootstrap 为单次流程；`Arc` 便于命令处理器克隆持有。
pub struct AppState {
    pub bootstrap: Mutex<Option<bootstrap::BootstrapHandle>>,
}

impl AppState {
    fn new() -> Self {
        Self {
            bootstrap: Mutex::new(None),
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let _guard = paths::init_logging();

    let force_setup = force_setup_from_args(std::env::args().skip(1));
    tracing::info!(force_setup, "唤生 installer starting");

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(Arc::new(AppState::new()))
        .setup(move |app| {
            use tauri::Manager;

            // macOS 已安装时直接拉起桌面端并退出。窗口 `"visible": false` 推迟显形，避免闪烁。
            // Windows 由快捷方式启动桌面端，此分支仅限 macOS。`--reinstall` / `--repair` 强制进入修复安装。
            if cfg!(target_os = "macos") && !force_setup {
                if bootstrap::spiritagent_is_installed() {
                    match bootstrap::spawn_installed_desktop() {
                        Ok(()) => {
                            std::thread::sleep(std::time::Duration::from_millis(200));
                            tracing::info!(
                                "spiritagent already installed — relaunched desktop; exiting installer"
                            );
                            app.handle().exit(0);
                            return Ok(());
                        }
                        Err(err) => {
                            tracing::warn!(
                                ?err,
                                "relaunch of installed desktop failed; showing installer UI"
                            );
                        }
                    }
                }
            }
            match app.get_webview_window("main") {
                Some(win) => {
                    if let Err(err) = win.show() {
                        tracing::error!(?err, "failed to show main installer window");
                    }
                }
                None => {
                    tracing::error!("main installer window not found; installer UI will not appear");
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            bootstrap::start_bootstrap,
            bootstrap::cancel_bootstrap,
            bootstrap::launch_spiritagent_desktop,
            paths::get_log_path,
            paths::get_spiritagent_home,
            paths::open_log_dir,
        ])
        .run(tauri::generate_context!())
        .expect("error while running 唤生 installer");
}

#[cfg(test)]
mod tests {
    use super::force_setup_from_args;

    #[test]
    fn reinstall_and_repair_flags_force_setup() {
        assert!(force_setup_from_args(["--reinstall"]));
        assert!(force_setup_from_args(["--repair"]));
        assert!(force_setup_from_args(["--foo", "--repair", "--bar"]));
    }

    #[test]
    fn bare_or_unrelated_args_do_not_force_setup() {
        assert!(!force_setup_from_args(Vec::<String>::new()));
        assert!(!force_setup_from_args(["--foo", "bar"]));
    }
}
