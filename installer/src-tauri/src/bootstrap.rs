//! Bootstrap 编排：驱动 install.ps1 / install.sh 按阶段执行，并通过 Tauri `bootstrap` 通道推送进度事件；
//! forensic 日志写入 SPIRITAGENT_HOME/logs/bootstrap-installer.log。

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Instant;

use anyhow::{anyhow, Result};
use serde::{Deserialize, Serialize};
use tauri::ipc::Channel;
use tauri::{AppHandle, Manager, State};
use tokio::sync::{mpsc, Mutex};

use crate::events::{BootstrapEvent, LogStream, Manifest, StageState};
use crate::install_script::{self, ScriptKind, ScriptSource};
use crate::powershell::{self, BundleContext, StreamSink};
use crate::AppState;

#[derive(Debug, Deserialize)]
pub struct StartBootstrapArgs {
    /// 提交 pin 覆盖；缺省取构建期烘焙的 `BUILD_PIN_COMMIT`。
    pub commit: Option<String>,
    /// 分支 pin 覆盖；缺省取 `BUILD_PIN_BRANCH`。
    pub branch: Option<String>,
    /// 旧的 `Stage-Desktop` 流程使用；瘦身后的 5 阶段脚本里桌面端由 Tauri bundle 预置，已无对应阶段。
    /// 仅保留以兼容前端传参，不向下转发。
    #[serde(default)]
    pub include_desktop: bool,
    /// SPIRITAGENT_HOME 覆盖，仅测试使用；生产路径走 OS 默认。
    pub spiritagent_home: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct BootstrapStatus {
    pub running: bool,
    pub completed: bool,
    pub install_root: Option<String>,
    pub last_error: Option<String>,
}

/// bootstrap 运行期间的句柄，挂在 AppState 上：携带取消通道与最近终态，便于窗口刷新后重新查询。
pub struct BootstrapHandle {
    pub cancel_tx: mpsc::Sender<()>,
    pub started_at: Instant,
    pub status: BootstrapStatus,
}

#[tauri::command]
pub async fn start_bootstrap(
    app: AppHandle,
    state: State<'_, Arc<AppState>>,
    args: StartBootstrapArgs,
    on_event: Channel<BootstrapEvent>,
) -> Result<(), String> {
    let mut guard = state.bootstrap.lock().await;
    if let Some(h) = guard.as_ref() {
        if h.status.running {
            return Err("Bootstrap is already running".into());
        }
    }

    let (cancel_tx, cancel_rx) = mpsc::channel::<()>(1);
    let handle = BootstrapHandle {
        cancel_tx,
        started_at: Instant::now(),
        status: BootstrapStatus {
            running: true,
            completed: false,
            install_root: None,
            last_error: None,
        },
    };
    *guard = Some(handle);
    drop(guard);

    let app_for_task = app.clone();
    let state_for_task = state.inner().clone();
    let args_for_task = args;
    let cancel_rx = Arc::new(Mutex::new(Some(cancel_rx)));

    tokio::spawn(async move {
        let result = run_bootstrap(app_for_task.clone(), args_for_task, cancel_rx, on_event).await;

        // 把终态回写到 AppState，使 get_bootstrap_status() 在任务结束后仍可读。
        let mut guard = state_for_task.bootstrap.lock().await;
        if let Some(h) = guard.as_mut() {
            h.status.running = false;
            match &result {
                Ok(install_root) => {
                    h.status.completed = true;
                    h.status.install_root = Some(install_root.clone());
                    h.status.last_error = None;
                }
                Err(err) => {
                    h.status.completed = false;
                    h.status.last_error = Some(err.to_string());
                }
            }
        }
    });

    Ok(())
}

#[tauri::command]
pub async fn cancel_bootstrap(state: State<'_, Arc<AppState>>) -> Result<(), String> {
    let guard = state.bootstrap.lock().await;
    if let Some(h) = guard.as_ref() {
        let _ = h.cancel_tx.try_send(());
    }
    Ok(())
}

#[tauri::command]
pub async fn get_bootstrap_status(
    state: State<'_, Arc<AppState>>,
) -> Result<BootstrapStatus, String> {
    let guard = state.bootstrap.lock().await;
    Ok(match guard.as_ref() {
        Some(h) => BootstrapStatus {
            running: h.status.running,
            completed: h.status.completed,
            install_root: h.status.install_root.clone(),
            last_error: h.status.last_error.clone(),
        },
        None => BootstrapStatus {
            running: false,
            completed: false,
            install_root: None,
            last_error: None,
        },
    })
}

/// 启动已安装的 SpiritAgent 桌面端后关闭安装器窗口；路径由各平台规范安装位置解析。
/// 若二进制不存在（如跳过了 Stage-UnpackDesktop）返回可读错误，便于前端给出可操作的失败提示。
#[tauri::command]
pub async fn launch_spiritagent_desktop(app: AppHandle) -> Result<(), String> {
    let exe_path = resolve_spiritagent_desktop_exe().ok_or_else(|| {
        format!(
            "在预期的平台位置 ({}) 未找到已安装的 SpiritAgent 桌面应用。请重新运行 SpiritAgent-Setup 以安装桌面组件。",
            desktop_install_root().display()
        )
    })?;

    tracing::info!(?exe_path, "launching SpiritAgent desktop");

    // 启动器需要脱离安装器独立存在；macOS 走 LaunchServices，以匹配双击/open 行为并规避自更新重建后的 cwd/quarantine 异常。
    let mut cmd = desktop_launch_command(&exe_path);
    #[cfg(target_os = "windows")]
    {
        // DETACHED_PROCESS = 0x00000008
        cmd.creation_flags(0x0000_0008);
    }

    cmd.spawn().map_err(|e| {
        format!("failed to launch {}: {e}", exe_path.display())
    })?;

    // 给 Windows ~150ms 让子进程真正起来再退出。
    tokio::time::sleep(std::time::Duration::from_millis(150)).await;

    app.exit(0);
    Ok(())
}

/// 仅供测试覆写 `desktop_install_root()`：生产路径为平台规范路径，测试需要在 CI 环境下重定向到临时目录。
#[cfg(test)]
static DESKTOP_ROOT_OVERRIDE: std::sync::Mutex<Option<PathBuf>> = std::sync::Mutex::new(None);

#[cfg(test)]
pub(crate) fn set_desktop_root_override_for_test(p: Option<PathBuf>) {
    let mut guard = DESKTOP_ROOT_OVERRIDE.lock().unwrap();
    *guard = p;
}

/// 桌面端安装的规范路径；与 install.{sh,ps1} 的 Stage-UnpackDesktop 保持一致。
pub(crate) fn desktop_install_root() -> PathBuf {
    #[cfg(test)]
    {
        if let Some(p) = DESKTOP_ROOT_OVERRIDE.lock().unwrap().as_ref() {
            return p.clone();
        }
    }
    #[cfg(target_os = "macos")]
    {
        PathBuf::from("/Applications/SpiritAgent.app")
    }
    #[cfg(target_os = "windows")]
    {
        // %LOCALAPPDATA%\Programs\SpiritAgent，与 install.ps1 Stage-UnpackDesktop 中的 NSIS /D= 路径一致。
        dirs::data_local_dir()
            .map(|p| p.join("Programs").join("SpiritAgent"))
            .unwrap_or_else(|| PathBuf::from("C:/Program Files/SpiritAgent"))
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        // 不可达：安装器仅打包至 macOS / Windows；留空 PathBuf 让函数保持 total，但不假装路径存在。
        PathBuf::new()
    }
}

/// 解析各平台规范路径上的桌面端二进制；macOS 返回 .app bundle，Windows 返回 .exe。
pub(crate) fn resolve_spiritagent_desktop_exe() -> Option<PathBuf> {
    #[cfg(target_os = "macos")]
    {
        let exe = desktop_install_root().join("Contents").join("MacOS").join("SpiritAgent");
        if exe.exists() {
            return Some(exe);
        }
    }
    #[cfg(target_os = "windows")]
    {
        let exe = desktop_install_root().join("SpiritAgent.exe");
        if exe.exists() {
            return Some(exe);
        }
        // 兜底：解包到 $SPIRITAGENT_HOME/apps/SpiritAgent/SpiritAgent.exe 的 ZIP 布局。
        let zip_exe = crate::paths::spiritagent_home()
            .join("apps")
            .join("SpiritAgent")
            .join("SpiritAgent.exe");
        if zip_exe.exists() {
            return Some(zip_exe);
        }
    }
    None
}

#[allow(dead_code)]
pub(crate) fn resolve_spiritagent_desktop_app() -> Option<PathBuf> {
    let exe = resolve_spiritagent_desktop_exe()?;
    #[cfg(target_os = "macos")]
    {
        // .../SpiritAgent.app/Contents/MacOS/SpiritAgent -> .../SpiritAgent.app
        let app = exe.parent()?.parent()?.parent()?.to_path_buf();
        if app.extension().and_then(|e| e.to_str()) == Some("app") && app.is_dir() {
            return Some(app);
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        return Some(exe);
    }
    #[allow(unreachable_code)]
    None
}

/// 给 `spiritagent_is_installed` 上一道闸，避免 venv 损坏时被 macOS 启动快路径误判为已安装。
/// 导入链必须与 `client/main/runner-updater.cjs::_probeVenvIntegrity` 保持一致，确保两边对"健康 venv"的判定一致。
fn runner_venv_is_healthy() -> bool {
    use std::process::{Command, Stdio};

    let Some(venv_python) = crate::paths::runner_venv_python() else {
        return false;
    };

    Command::new(&venv_python)
        .arg("-c")
        .arg(
            "from typing_extensions import Sentinel; from annotated_types import BaseMetadata; from mcp.types import BaseModel",
        )
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|s| s.success())
}

/// 仅当同时具备（bootstrap-complete 标记 + 可启动桌面端 + Runner venv 健康 `runner_venv_is_healthy`）时返回 true。
/// 给安装器启动快路径使用；也防止陈旧标记 + 损坏 venv 静默跳过安装。
pub(crate) fn spiritagent_is_installed() -> bool {
    crate::paths::spiritagent_home()
        .join(".spiritagent-bootstrap-complete")
        .exists()
        && resolve_spiritagent_desktop_exe().is_some()
        && runner_venv_is_healthy()
}

/// 后台启动已安装的桌面端；无可用二进制或 spawn 失败时返回 Err，由调用方回退到安装器 UI。
pub(crate) fn spawn_installed_desktop() -> std::io::Result<()> {
    let exe = resolve_spiritagent_desktop_exe().ok_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::NotFound, "no installed SpiritAgent desktop app")
    })?;
    let mut cmd = desktop_launch_command_std(&exe);
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        // DETACHED_PROCESS = 0x00000008，保证桌面端在安装器退出后继续运行，与 launch_spiritagent_desktop 一致。
        cmd.creation_flags(0x0000_0008);
    }
    cmd.spawn().map(|_child| ())
}

#[cfg(target_os = "macos")]
pub(crate) fn open_macos_app_detached(app_bundle: &std::path::Path) -> std::io::Result<()> {
    let mut cmd = std::process::Command::new("/usr/bin/open");
    cmd.arg(app_bundle);
    cmd.current_dir(crate::paths::spiritagent_home());
    cmd.spawn().map(|_child| ())
}

#[cfg(target_os = "macos")]
fn app_bundle_for_exe(exe: &std::path::Path) -> Option<PathBuf> {
    let app = exe.parent()?.parent()?.parent()?.to_path_buf();
    if app.extension().and_then(|e| e.to_str()) == Some("app") && app.is_dir() {
        Some(app)
    } else {
        None
    }
}

fn desktop_launch_command(exe_path: &std::path::Path) -> tokio::process::Command {
    #[cfg(target_os = "macos")]
    {
        if let Some(app_bundle) = app_bundle_for_exe(exe_path) {
            let mut cmd = tokio::process::Command::new("/usr/bin/open");
            cmd.arg(app_bundle);
            cmd.current_dir(crate::paths::spiritagent_home());
            return cmd;
        }
    }

    let mut cmd = tokio::process::Command::new(exe_path);
    cmd.current_dir(exe_path.parent().unwrap_or_else(|| Path::new(".")));
    cmd
}

fn desktop_launch_command_std(exe_path: &std::path::Path) -> std::process::Command {
    #[cfg(target_os = "macos")]
    {
        if let Some(app_bundle) = app_bundle_for_exe(exe_path) {
            let mut cmd = std::process::Command::new("/usr/bin/open");
            cmd.arg(app_bundle);
            cmd.current_dir(crate::paths::spiritagent_home());
            return cmd;
        }
    }

    let mut cmd = std::process::Command::new(exe_path);
    cmd.current_dir(exe_path.parent().unwrap_or_else(|| Path::new(".")));
    cmd
}

async fn run_bootstrap(
    app: AppHandle,
    args: StartBootstrapArgs,
    cancel_rx_holder: Arc<Mutex<Option<mpsc::Receiver<()>>>>,
    on_event: Channel<BootstrapEvent>,
) -> Result<String> {
    let kind = ScriptKind::for_current_os();

    // 安装标记事件中的 pin 元数据：脚本已 bundle，无需 pin 解析；这里只记录用户 pin 的版本。
    let pinned_commit = args
        .commit
        .clone()
        .or_else(|| option_env!("BUILD_PIN_COMMIT").map(|s| s.to_string()));
    let pinned_branch = args
        .branch
        .clone()
        .or_else(|| option_env!("BUILD_PIN_BRANCH").map(|s| s.to_string()));

    tracing::info!(
        pinned_commit = ?pinned_commit,
        pinned_branch = ?pinned_branch,
        kind = ?kind,
        include_desktop = args.include_desktop,
        "bootstrap starting"
    );

    let on_event_for_log = on_event.clone();
    let emit_log = move |line: &str| {
        emit_event(
            &on_event_for_log,
            BootstrapEvent::Log {
                stage: None,
                line: line.to_string(),
                stream: LogStream::Stdout,
            },
        );
        // 走 info!，确保默认 INFO 过滤下能落到 bootstrap-installer.log；此前 debug! 在 install.ps1 失败时只剩 "bootstrap starting" 一行。
        tracing::info!(target: "bootstrap.log", "{line}");
    };

    // 1) 解析 install.{ps1,sh}：dev 入口 → Tauri bundle.resources；安装器不自联网。
    let script = install_script::resolve(&app, kind, &emit_log)
        .await
        .map_err(|e| {
            let msg = format!("resolve install script failed: {e:#}");
            emit_event(
                &on_event,
                BootstrapEvent::Failed {
                    stage: None,
                    error: msg.clone(),
                },
            );
            anyhow!(msg)
        })?;

    let source_note = match &script.source {
        ScriptSource::DevCheckout => "dev checkout",
        ScriptSource::Bundled => "bundled",
        ScriptSource::Embedded => "embedded",
    };
    emit_log(&format!(
        "[bootstrap] script {} via {}",
        script.path.display(),
        source_note
    ));

    // 2) 拉取 manifest：脚本已 bundle，不再接收 -IncludeDesktop / -Commit / -Branch，这些参数仅在 marker 事件里使用。
    let manifest_args = vec!["-Manifest".to_string()];

    let bundle_ctx = build_bundle_context(&app);

    let manifest_result = run_install_script(
        &on_event,
        &script.path,
        &manifest_args,
        args.spiritagent_home.as_deref(),
        &bundle_ctx,
        None,
        Some("__manifest__".to_string()),
    )
    .await?;

    if manifest_result.exit_code != Some(0) {
        let err = format!(
            "install.ps1 -Manifest failed: exit {:?}\n{}",
            manifest_result.exit_code,
            manifest_result.stderr.trim()
        );
        emit_event(
            &on_event,
            BootstrapEvent::Failed {
                stage: None,
                error: err.clone(),
            },
        );
        return Err(anyhow!(err));
    }

    let manifest: Manifest = powershell::parse_manifest(&manifest_result.stdout).ok_or_else(|| {
        let err = format!(
            "install.ps1 -Manifest produced no parseable JSON payload\n{}",
            truncate(&manifest_result.stdout, MANIFEST_PREVIEW_CHARS)
        );
        emit_event(
            &on_event,
            BootstrapEvent::Failed {
                stage: None,
                error: err.clone(),
            },
        );
        anyhow!(err)
    })?;

    emit_event(
        &on_event,
        BootstrapEvent::Manifest {
            stages: manifest.stages.clone(),
            protocol_version: manifest.protocol_version,
        },
    );

    // 3) 顺序执行各阶段。
    for stage in &manifest.stages {
        if cancellation_signalled(&cancel_rx_holder).await {
            let err = "bootstrap cancelled by user".to_string();
            emit_event(
                &on_event,
                BootstrapEvent::Failed {
                    stage: Some(stage.name.clone()),
                    error: err.clone(),
                },
            );
            return Err(anyhow!(err));
        }

        let started = Instant::now();
        emit_event(
            &on_event,
            BootstrapEvent::Stage {
                name: stage.name.clone(),
                state: StageState::Running,
                duration_ms: None,
                result: None,
                error: None,
            },
        );

        let stage_args = vec![
            "-Stage".to_string(),
            stage.name.clone(),
            "-NonInteractive".to_string(),
            "-Json".to_string(),
        ];

        // 每个阶段独占 cancel 接收者：run_script 内的 tokio::select! 会消费它，所以经 Arc<Mutex> 取出/归还。
        let local_cancel_rx = cancel_rx_holder.lock().await.take();

        let stage_result = run_install_script(
            &on_event,
            &script.path,
            &stage_args,
            args.spiritagent_home.as_deref(),
            &bundle_ctx,
            local_cancel_rx,
            Some(stage.name.clone()),
        )
        .await?;

        let duration_ms = started.elapsed().as_millis() as u64;

        if stage_result.killed {
            emit_event(
                &on_event,
                BootstrapEvent::Stage {
                    name: stage.name.clone(),
                    state: StageState::Failed,
                    duration_ms: Some(duration_ms),
                    result: None,
                    error: Some("cancelled by user".into()),
                },
            );
            emit_event(
                &on_event,
                BootstrapEvent::Failed {
                    stage: Some(stage.name.clone()),
                    error: "cancelled by user".into(),
                },
            );
            return Err(anyhow!("cancelled by user"));
        }

        let result_frame = powershell::parse_stage_result(&stage_result.stdout);

        match result_frame {
            None => {
                let stdout_preview = truncate(&stage_result.stdout, STAGE_PREVIEW_CHARS);
                let stderr_preview = truncate(&stage_result.stderr, STAGE_PREVIEW_CHARS);
                tracing::error!(
                    stage = %stage.name,
                    exit = ?stage_result.exit_code,
                    stdout_len = stage_result.stdout.len(),
                    stderr_len = stage_result.stderr.len(),
                    stdout = %stdout_preview,
                    stderr = %stderr_preview,
                    "stage produced no JSON result frame"
                );
                let err = format!(
                    "install.ps1 -Stage {} produced no JSON result frame (exit={:?})\nstdout: {}\nstderr: {}",
                    stage.name, stage_result.exit_code, stdout_preview, stderr_preview
                );
                emit_event(
                    &on_event,
                    BootstrapEvent::Stage {
                        name: stage.name.clone(),
                        state: StageState::Failed,
                        duration_ms: Some(duration_ms),
                        result: None,
                        error: Some(err.clone()),
                    },
                );
                emit_event(
                    &on_event,
                    BootstrapEvent::Failed {
                        stage: Some(stage.name.clone()),
                        error: err.clone(),
                    },
                );
                return Err(anyhow!(err));
            }
            Some(frame) if frame.ok && frame.skipped => {
                emit_event(
                    &on_event,
                    BootstrapEvent::Stage {
                        name: stage.name.clone(),
                        state: StageState::Skipped,
                        duration_ms: Some(duration_ms),
                        result: Some(frame),
                        error: None,
                    },
                );
            }
            Some(frame) if frame.ok => {
                emit_event(
                    &on_event,
                    BootstrapEvent::Stage {
                        name: stage.name.clone(),
                        state: StageState::Succeeded,
                        duration_ms: Some(duration_ms),
                        result: Some(frame),
                        error: None,
                    },
                );
            }
            Some(frame) => {
                let err = frame
                    .reason
                    .clone()
                    .unwrap_or_else(|| format!("exit code {:?}", stage_result.exit_code));
                emit_event(
                    &on_event,
                    BootstrapEvent::Stage {
                        name: stage.name.clone(),
                        state: StageState::Failed,
                        duration_ms: Some(duration_ms),
                        result: Some(frame),
                        error: Some(err.clone()),
                    },
                );
                emit_event(
                    &on_event,
                    BootstrapEvent::Failed {
                        stage: Some(stage.name.clone()),
                        error: err.clone(),
                    },
                );
                return Err(anyhow!(err));
            }
        }
    }

    // 4) 解析 install_root。瘦身后的 5 阶段脚本不再向 `<spiritagent_home>/spiritagent-agent/` 克隆仓库，所有负载直接落 $SPIRITAGENT_HOME（bin/、skills/、.spiritagent-bootstrap-complete），所以 install_root 即 spiritagent_home。
    let spiritagent_home = args
        .spiritagent_home
        .clone()
        .unwrap_or_else(|| crate::paths::spiritagent_home().to_string_lossy().into_owned());
    let install_root = PathBuf::from(&spiritagent_home);

    // 自拷贝到 SPIRITAGENT_HOME/spiritagent-setup.exe，为快捷方式提供稳定目标；若已在目标位置会自动跳过。最佳努力，失败不中断安装。
    if let Err(err) = crate::paths::copy_self_to_spiritagent_home() {
        tracing::warn!(?err, "failed to copy installer into SPIRITAGENT_HOME (non-fatal)");
        emit_log(&format!(
            "[bootstrap] warning: could not stage installer binary: {err}"
        ));
    }

    emit_event(
        &on_event,
        BootstrapEvent::Complete {
            install_root: install_root.to_string_lossy().into_owned(),
            marker: Some(serde_json::json!({
                "pinnedCommit": pinned_commit,
                "pinnedBranch": pinned_branch,
            })),
        },
    );

    Ok(install_root.to_string_lossy().into_owned())
}

async fn cancellation_signalled(holder: &Arc<Mutex<Option<mpsc::Receiver<()>>>>) -> bool {
    let mut guard = holder.lock().await;
    if let Some(rx) = guard.as_mut() {
        rx.try_recv().is_ok()
    } else {
        false
    }
}

async fn run_install_script(
    on_event: &Channel<BootstrapEvent>,
    script_path: &std::path::Path,
    args: &[String],
    spiritagent_home_override: Option<&str>,
    bundle: &BundleContext,
    cancel_rx: Option<mpsc::Receiver<()>>,
    stage_name: Option<String>,
) -> Result<powershell::ScriptResult> {
    let on_event_stdout = on_event.clone();
    let stage_for_stdout = stage_name.clone();
    let on_event_stderr = on_event.clone();
    let stage_for_stderr = stage_name.clone();
    let stage_for_stdout_log = stage_name.clone();
    let stage_for_stderr_log = stage_name.clone();

    let sink = StreamSink {
        on_stdout_line: Box::new(move |line: &str| {
            emit_event(
                &on_event_stdout,
                BootstrapEvent::Log {
                    stage: stage_for_stdout.clone(),
                    line: line.to_string(),
                    stream: LogStream::Stdout,
                },
            );
            // 同时落到滚动日志，便于排查失败：Tauri 事件流在失败页挂载后即被丢弃，缺乏持久记录。
            match &stage_for_stdout_log {
                Some(name) => {
                    tracing::info!(target: "bootstrap.log", stage = %name, "{line}")
                }
                None => tracing::info!(target: "bootstrap.log", "{line}"),
            }
        }),
        on_stderr_line: Box::new(move |line: &str| {
            emit_event(
                &on_event_stderr,
                BootstrapEvent::Log {
                    stage: stage_for_stderr.clone(),
                    line: line.to_string(),
                    stream: LogStream::Stderr,
                },
            );
            // stderr 走 warn!，便于在日志里和 stdout 区分。
            match &stage_for_stderr_log {
                Some(name) => {
                    tracing::warn!(target: "bootstrap.log", stage = %name, "stderr: {line}")
                }
                None => tracing::warn!(target: "bootstrap.log", "stderr: {line}"),
            }
        }),
    };

    powershell::run_script(script_path, args, sink, spiritagent_home_override, bundle, cancel_rx)
        .await
        .map_err(|e| {
            tracing::error!(?e, "install script invocation failed");
            anyhow!("install script invocation failed: {e:#}")
        })
}

/// 由当前安装器的 Tauri 资源目录构建 `BundleContext`；路径以 `<bundle.resources>/payload/` 为锚点（见 `tauri.conf.json#bundle.resources`）。
/// 单 exe 自包含场景下 Tauri `resource_dir` 不带 payload/，回退到 `embedded_payload` 解压目录。
fn build_bundle_context(app: &AppHandle) -> BundleContext {
    let bundle_dir = app.path().resource_dir().ok();
    let mut payload = bundle_dir.as_ref().map(|d| d.join("payload"));

    if !payload.as_ref().map(|p| p.is_dir()).unwrap_or(false) {
        // 单 exe 分发：resource_dir/payload 不存在时退回嵌入 zip 解压目录。
        if let Ok(embedded) = crate::embedded_payload::payload_dir() {
            payload = Some(embedded);
        }
    }

    let installer_format = if cfg!(target_os = "macos") {
        "dmg"
    } else {
        "nsis"
    }
    .to_string();

    let bundle_dir_final = payload
        .as_ref()
        .and_then(|p| p.parent().map(|p| p.to_path_buf()))
        .or(bundle_dir);

    BundleContext {
        bundle_dir: bundle_dir_final,
        bundled_runner_dir: payload.as_ref().map(|d| d.join("runner")),
        bundled_desktop_dir: payload.as_ref().map(|d| d.join("client")),
        bundled_skills_dir: payload.as_ref().map(|d| d.join("skills")),
        bundled_onboarding_audio_dir: payload.as_ref().map(|d| d.join("onboarding-audio")),
        installer_format: Some(installer_format),
    }
}

fn emit_event(on_event: &Channel<BootstrapEvent>, event: BootstrapEvent) {
    // 关键状态翻转也落到滚动日志，避免只剩 "starting" + 最终摘要；日志行已在 sink 回调内自处理，这里只覆盖生命周期帧。
    match &event {
        BootstrapEvent::Manifest { stages, .. } => {
            tracing::info!(
                stage_count = stages.len(),
                names = ?stages.iter().map(|s| s.name.as_str()).collect::<Vec<_>>(),
                "manifest received"
            );
        }
        BootstrapEvent::Stage {
            name,
            state,
            duration_ms,
            error,
            ..
        } => {
            tracing::info!(
                stage = %name,
                ?state,
                duration_ms = ?duration_ms,
                error = ?error,
                "stage transition"
            );
        }
        BootstrapEvent::Complete { install_root, .. } => {
            tracing::info!(install_root = %install_root, "bootstrap complete");
        }
        BootstrapEvent::Failed { stage, error } => {
            tracing::error!(stage = ?stage, error = %error, "bootstrap FAILED");
        }
        BootstrapEvent::Log { .. } => {
            // 日志行已通过 run_install_script 的 sink 回调落日志，此处不再重复。
        }
    }
    if let Err(e) = on_event.send(event) {
        tracing::warn!(?e, "failed to send bootstrap event via ipc channel");
    }
}

// 各阶段输出的截断上限：阶段通常输出数十行，manifest 是单个（可能多行）JSON，给更大窗口。
const STAGE_PREVIEW_CHARS: usize = 2000;
const MANIFEST_PREVIEW_CHARS: usize = 4000;

fn truncate(s: &str, max: usize) -> String {
    if s.len() <= max {
        s.to_string()
    } else {
        format!("{}...", &s[..max])
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn unique_tmp_dir(tag: &str) -> PathBuf {
        let base = std::env::temp_dir().join(format!(
            "spiritagent-bootstrap-test-{tag}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&base).unwrap();
        base
    }

    /// 在平台规范位置（测试通过 `set_desktop_root_override_for_test` 重定向到 `install_root`）构造一个伪"已安装桌面端"；布局对齐 install 脚本 Stage-UnpackDesktop 的产物。
    fn make_installed_desktop(install_root: &Path) -> PathBuf {
        if cfg!(target_os = "macos") {
            let macos_dir = install_root
                .join("Contents")
                .join("MacOS");
            std::fs::create_dir_all(&macos_dir).unwrap();
            std::fs::write(macos_dir.join("SpiritAgent"), b"#!/bin/sh\n").unwrap();
        } else {
            std::fs::write(install_root.join("SpiritAgent.exe"), b"stub").unwrap();
        }
        install_root.to_path_buf()
    }

    static TEST_MUTEX: std::sync::Mutex<()> = std::sync::Mutex::new(());

    /// 重新启动目标是平台规范路径上的桌面端：macOS 必须是 .app bundle（`open` 与 electron-updater 都以此为目标）；这里加锁防止回归。
    #[test]
    fn resolve_spiritagent_desktop_app_finds_installed_bundle() {
        let _lock = TEST_MUTEX.lock().unwrap();
        let root = unique_tmp_dir("app-ok");
        set_desktop_root_override_for_test(Some(root.clone()));
        make_installed_desktop(&root);

        let resolved = resolve_spiritagent_desktop_app()
            .expect("should resolve the installed desktop app");

        #[cfg(target_os = "macos")]
        {
            assert_eq!(
                resolved.extension().and_then(|e| e.to_str()),
                Some("app"),
                "relaunch target must be a .app bundle on macOS"
            );
            assert!(resolved.is_dir(), "macOS resolution must be a directory");
        }
        #[cfg(not(target_os = "macos"))]
        {
            assert!(resolved.is_file(), "non-macOS resolution must be a file");
        }
        let _ = std::fs::remove_dir_all(&root);
        set_desktop_root_override_for_test(None);
    }

    #[test]
    fn resolve_spiritagent_desktop_app_is_none_without_install() {
        let _lock = TEST_MUTEX.lock().unwrap();
        let root = unique_tmp_dir("app-none");
        set_desktop_root_override_for_test(Some(root.clone()));
        // 不构造已安装桌面：未安装时应返回 None。
        assert!(
            resolve_spiritagent_desktop_app().is_none(),
            "no resolved app when nothing has been installed"
        );
        let _ = std::fs::remove_dir_all(&root);
        set_desktop_root_override_for_test(None);
    }

    #[test]
    #[cfg(target_os = "windows")]
    fn test_resolve_spiritagent_desktop_exe_zip_fallback() {
        let _lock = TEST_MUTEX.lock().unwrap();
        let root = unique_tmp_dir("app-zip-none");
        set_desktop_root_override_for_test(Some(root.clone()));
        let home = crate::paths::spiritagent_home();
        let zip_dir = home.join("apps").join("SpiritAgent");
        let _ = std::fs::create_dir_all(&zip_dir);
        let zip_exe = zip_dir.join("SpiritAgent.exe");
        let _ = std::fs::write(&zip_exe, b"stub");

        let resolved = resolve_spiritagent_desktop_exe();
        assert!(resolved.is_some(), "should resolve desktop exe in zip_layout path");
        assert_eq!(resolved.unwrap(), zip_exe);

        let _ = std::fs::remove_file(&zip_exe);
        let _ = std::fs::remove_dir_all(&root);
        set_desktop_root_override_for_test(None);
    }
}
