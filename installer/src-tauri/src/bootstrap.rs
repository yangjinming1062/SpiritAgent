//! Bootstrap 编排：按阶段驱动 install 脚本，经 Tauri `bootstrap` 通道推送进度；日志见 bootstrap-installer.log。

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Instant;

use anyhow::{anyhow, Result};
use serde::Deserialize;
use tauri::ipc::Channel;
use tauri::{AppHandle, Manager, State};
use tokio::sync::{mpsc, Mutex};

use crate::events::{BootstrapEvent, LogStream, Manifest, StageState};
use crate::install_script::{self, ScriptKind, ScriptSource};
use crate::powershell::{self, BundleContext, StreamSink};
use crate::AppState;

#[derive(Debug, Deserialize)]
pub struct StartBootstrapArgs {
    /// SPIRITAGENT_HOME 覆盖，仅测试使用。
    pub spiritagent_home: Option<String>,
}

/// bootstrap 运行句柄；供取消与防重入。
pub struct BootstrapHandle {
    pub cancel_tx: mpsc::Sender<()>,
    pub running: bool,
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
        if h.running {
            return Err("Bootstrap is already running".into());
        }
    }

    let (cancel_tx, cancel_rx) = mpsc::channel::<()>(1);
    *guard = Some(BootstrapHandle {
        cancel_tx,
        running: true,
    });
    drop(guard);

    let app_for_task = app.clone();
    let state_for_task = state.inner().clone();
    let args_for_task = args;
    let cancel_rx = Arc::new(Mutex::new(Some(cancel_rx)));

    tokio::spawn(async move {
        let _ = run_bootstrap(app_for_task, args_for_task, cancel_rx, on_event).await;
        let mut guard = state_for_task.bootstrap.lock().await;
        if let Some(h) = guard.as_mut() {
            h.running = false;
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

/// 启动已安装桌面端后退出安装器；二进制缺失时返回可读错误供前端提示。
#[tauri::command]
pub async fn launch_spiritagent_desktop(app: AppHandle) -> Result<(), String> {
    let exe_path = resolve_spiritagent_desktop_exe().ok_or_else(|| {
        format!(
            "在预期的平台位置 ({}) 未找到已安装的 SpiritAgent 桌面应用。请重新运行 SpiritAgent-Setup 以安装桌面组件。",
            desktop_install_root().display()
        )
    })?;

    tracing::info!(?exe_path, "launching SpiritAgent desktop");

    // 脱离安装器独立运行；macOS 走 LaunchServices 以规避 cwd/quarantine 异常。
    let mut cmd = desktop_launch_command(&exe_path);
    #[cfg(target_os = "windows")]
    {
        // DETACHED_PROCESS = 0x00000008
        cmd.creation_flags(0x0000_0008);
    }

    cmd.spawn().map_err(|e| {
        format!("failed to launch {}: {e}", exe_path.display())
    })?;

    // 留 ~150ms 让子进程真正起来再退出
    tokio::time::sleep(std::time::Duration::from_millis(150)).await;

    app.exit(0);
    Ok(())
}

/// 测试覆写 `desktop_install_root()` 用；生产走平台规范路径。
#[cfg(test)]
static DESKTOP_ROOT_OVERRIDE: std::sync::Mutex<Option<PathBuf>> = std::sync::Mutex::new(None);

#[cfg(test)]
pub(crate) fn set_desktop_root_override_for_test(p: Option<PathBuf>) {
    let mut guard = DESKTOP_ROOT_OVERRIDE.lock().unwrap();
    *guard = p;
}

/// 桌面端规范安装路径；与 install 脚本 Stage-UnpackDesktop 一致。
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
        // 与 install.ps1 NSIS /D= 路径一致
        dirs::data_local_dir()
            .map(|p| p.join("Programs").join("SpiritAgent"))
            .unwrap_or_else(|| PathBuf::from("C:/Program Files/SpiritAgent"))
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        // 安装器仅打包 macOS/Windows；空 Path 保持函数 total
        PathBuf::new()
    }
}

/// 解析规范路径上的桌面端二进制；macOS 返回 .app 内 exe，Windows 返回 .exe。
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
        // 兜底 ZIP 布局：$SPIRITAGENT_HOME/apps/SpiritAgent/SpiritAgent.exe
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

/// venv 健康探针；导入链须与 `client/main/runner/updater.ts::probeVenvIntegrity` 一致。
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

/// 须同时具备完成标记、可启动桌面端与健康 venv，防止陈旧标记误判已安装。
pub(crate) fn spiritagent_is_installed() -> bool {
    crate::paths::spiritagent_home()
        .join(".spiritagent-bootstrap-complete")
        .exists()
        && resolve_spiritagent_desktop_exe().is_some()
        && runner_venv_is_healthy()
}

/// 后台启动桌面端；失败由调用方回退安装 UI。
pub(crate) fn spawn_installed_desktop() -> std::io::Result<()> {
    let exe = resolve_spiritagent_desktop_exe().ok_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::NotFound, "no installed SpiritAgent desktop app")
    })?;
    let mut cmd = desktop_launch_command_std(&exe);
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        // DETACHED_PROCESS，与 launch_spiritagent_desktop 一致
        cmd.creation_flags(0x0000_0008);
    }
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

    tracing::info!(?kind, "bootstrap starting");

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
        // info! 保证默认过滤下写入 bootstrap-installer.log
        tracing::info!(target: "bootstrap.log", "{line}");
    };

    // 1) 解析 install 脚本：dev → bundle.resources → 嵌入 zip
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

    // 2) 拉取 manifest，解析后广播阶段列表
    let manifest_args = vec!["-Manifest".to_string()];

    let bundle_ctx = build_bundle_context(&app);

    let (manifest_result, _) = run_install_script(
        &on_event,
        &script.path,
        &manifest_args,
        args.spiritagent_home.as_deref(),
        &bundle_ctx,
        None,
        Some("__manifest__".to_string()),
    )
    .await
    .map_err(|e| fail_bootstrap(&on_event, None, e.to_string()))?;

    if manifest_result.exit_code != Some(0) {
        let err = format!(
            "{} -Manifest failed: exit {:?}\n{}",
            kind.filename(),
            manifest_result.exit_code,
            manifest_result.stderr.trim()
        );
        return Err(fail_bootstrap(&on_event, None, err));
    }

    let manifest: Manifest = powershell::parse_manifest(&manifest_result.stdout).ok_or_else(|| {
        let err = format!(
            "{} -Manifest produced no parseable JSON payload\n{}",
            kind.filename(),
            truncate(&manifest_result.stdout, MANIFEST_PREVIEW_CHARS)
        );
        fail_bootstrap(&on_event, None, err)
    })?;

    emit_event(
        &on_event,
        BootstrapEvent::Manifest {
            stages: manifest.stages.clone(),
            protocol_version: manifest.protocol_version,
        },
    );

    // 3) 顺序执行各阶段
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

        // 每阶段独占 cancel 接收者；结束后把未触发通道归还 holder，否则后续阶段无法取消。
        let local_cancel_rx = cancel_rx_holder.lock().await.take();

        let (stage_result, unused_cancel_rx) = run_install_script(
            &on_event,
            &script.path,
            &stage_args,
            args.spiritagent_home.as_deref(),
            &bundle_ctx,
            local_cancel_rx,
            Some(stage.name.clone()),
        )
        .await
        .map_err(|e| fail_bootstrap(&on_event, Some(stage.name.clone()), e.to_string()))?;

        *cancel_rx_holder.lock().await = unused_cancel_rx;

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
                    "{} -Stage {} produced no JSON result frame (exit={:?})\nstdout: {}\nstderr: {}",
                    kind.filename(),
                    stage.name,
                    stage_result.exit_code,
                    stdout_preview,
                    stderr_preview
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

    // install_root 即 spiritagent_home，负载直接落 $SPIRITAGENT_HOME
    let spiritagent_home = args
        .spiritagent_home
        .clone()
        .unwrap_or_else(|| crate::paths::spiritagent_home().to_string_lossy().into_owned());
    let install_root = PathBuf::from(&spiritagent_home);

    // 自拷贝到稳定路径供快捷方式指向；最佳努力，失败不中断安装
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
) -> Result<(powershell::ScriptResult, Option<mpsc::Receiver<()>>)> {
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
            // Tauri 事件流在失败页挂载后即丢弃，需同时落滚动日志
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
            // stderr 用 warn! 以便与 stdout 区分
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

/// 失败同时送达前端事件流与返回值；缺 Failed 事件会让前端卡在 running。
fn fail_bootstrap(
    on_event: &Channel<BootstrapEvent>,
    stage: Option<String>,
    err: String,
) -> anyhow::Error {
    emit_event(
        &on_event,
        BootstrapEvent::Failed {
            stage,
            error: err.clone(),
        },
    );
    anyhow!(err)
}

/// 以 `<bundle.resources>/payload/` 为锚点构建 BundleContext；单 exe 时回退 embedded 解压目录。
fn build_bundle_context(app: &AppHandle) -> BundleContext {
    let mut payload = app.path().resource_dir().ok().map(|d| d.join("payload"));

    if !payload.as_ref().map(|p| p.is_dir()).unwrap_or(false) {
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

    BundleContext {
        bundled_runner_dir: payload.as_ref().map(|d| d.join("runner")),
        bundled_desktop_dir: payload.as_ref().map(|d| d.join("client")),
        bundled_skills_dir: payload.as_ref().map(|d| d.join("skills")),
        bundled_onboarding_audio_dir: payload.as_ref().map(|d| d.join("onboarding-audio")),
        installer_format: Some(installer_format),
    }
}

fn emit_event(on_event: &Channel<BootstrapEvent>, event: BootstrapEvent) {
    // 生命周期帧落滚动日志；脚本日志行由 sink 回调处理
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
        BootstrapEvent::Log { .. } => {}
    }
    if let Err(e) = on_event.send(event) {
        tracing::warn!(?e, "failed to send bootstrap event via ipc channel");
    }
}

// 截断上限：manifest 可能多行 JSON，给更大窗口
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

    /// 构造伪已安装桌面端；布局对齐 Stage-UnpackDesktop 产物。
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

    /// 快路径与 launch 共用 resolve；加锁防 override 干扰。
    #[test]
    fn resolve_spiritagent_desktop_exe_finds_installed_desktop() {
        let _lock = TEST_MUTEX.lock().unwrap();
        let root = unique_tmp_dir("app-ok");
        set_desktop_root_override_for_test(Some(root.clone()));
        let installed = make_installed_desktop(&root);

        let resolved = resolve_spiritagent_desktop_exe()
            .expect("should resolve the installed desktop executable");

        assert!(
            resolved.is_file(),
            "resolved desktop target must be the executable file, got {resolved:?}"
        );
        assert!(resolved.starts_with(&installed));
        let _ = std::fs::remove_dir_all(&root);
        set_desktop_root_override_for_test(None);
    }

    #[test]
    fn resolve_spiritagent_desktop_exe_is_none_without_install() {
        let _lock = TEST_MUTEX.lock().unwrap();
        let root = unique_tmp_dir("app-none");
        set_desktop_root_override_for_test(Some(root.clone()));
        assert!(
            resolve_spiritagent_desktop_exe().is_none(),
            "no resolved desktop when nothing has been installed"
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
