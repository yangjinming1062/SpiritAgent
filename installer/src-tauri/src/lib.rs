//! SpiritAgent Setup 的 Tauri 入口；安装期工作集中于 `bootstrap.rs`，通过 `run()` 注册的命令调用。
//! Windows 子系统剥离（windows_subsystem）位于二进制 crate 的 main.rs，而不在此处——lib 上的属性不会下传到链接期。

mod bootstrap;
mod embedded_payload;
mod events;
mod install_script;
mod powershell;
mod paths;

use std::sync::Arc;
use tokio::sync::Mutex;

/// 当传入 `--reinstall` 或 `--repair` 时强制进入安装器 UI（绕过 macOS 启动快路径，便于修复已坏安装）。
pub fn force_setup_from_args<I, S>(args: I) -> bool
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    args.into_iter()
        .any(|a| a.as_ref() == "--reinstall" || a.as_ref() == "--repair")
}

/// 进程级安装状态，跨 Tauri 命令共享。
/// bootstrap 为一次性单租户流程，每个窗口仅需一份；`Arc<Mutex<...>>` 避免命令处理器处理生命周期问题。
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
    // tracing 输出到 bootstrap-installer.log，debug 构建同时输出到控制台。
    let _guard = paths::init_logging();

    // `--reinstall` / `--repair` 兜底：已安装时也强制回到安装器，避免快路径再次启动已坏的应用。
    let force_setup = force_setup_from_args(std::env::args().skip(1));
    tracing::info!(force_setup, "唤生 installer starting");

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_shell::init())
        .manage(Arc::new(AppState::new()))
        .setup(move |app| {
            use tauri::Manager;

            // macOS 启动快路径：已安装时不再弹窗，直接打开应用。窗口通过 `"visible": false` 推迟显形，避免闪烁。
            // 仅限 macOS：Windows 由 install.ps1 创建的快捷方式启动，需要 launch_spiritagent_desktop 的 DETACHED_PROCESS + 启动 grace；这里在 Windows 上为纯 no-op。
            // `--reinstall` / `--repair` 强制退出快路径，以便重新跑一遍安装来修复。
            if cfg!(target_os = "macos") && !force_setup {
                if bootstrap::spiritagent_is_installed() {
                    match bootstrap::spawn_installed_desktop() {
                        Ok(()) => {
                            // 短暂等待确保子进程被系统注册后再退出（与 launch_spiritagent_desktop 保持一致）。
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
            // 首次安装或修复安装：显形主窗口。
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
            bootstrap::get_bootstrap_status,
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
