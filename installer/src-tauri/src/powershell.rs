//! 驱动 install.ps1 / install.sh 的子进程：Windows 下走 PowerShell，Unix 下走 bash。
//! Windows: `-NoProfile -ExecutionPolicy Bypass -File <script>`；Unix: `bash <script>`。

use anyhow::{Context, Result};
use std::path::Path;
use std::process::Stdio;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::mpsc;

pub struct StreamSink {
    pub on_stdout_line: Box<dyn Fn(&str) + Send + Sync>,
    pub on_stderr_line: Box<dyn Fn(&str) + Send + Sync>,
}

/// 脚本执行结果；字段与 bootstrap-runner.cjs 中的 `{stdout, stderr, code, signal, killed}` 一致。
#[derive(Debug)]
pub struct ScriptResult {
    pub stdout: String,
    pub stderr: String,
    pub exit_code: Option<i32>,
    pub killed: bool,
}

/// 取消信号：`cancel_tx.send(()).await` 中止运行中的脚本。
pub type CancelRx = mpsc::Receiver<()>;

/// 安装脚本可选上下文，由安装器 `bundle.resources` 布局派生；
/// 作为 SPIRITAGENT_BUNDLE_* 环境变量下发给子脚本，避免用大量 CLI 参数。
#[derive(Debug, Clone, Default)]
pub struct BundleContext {
    /// Tauri bundle.resources 解压根路径。
    pub bundle_dir: Option<std::path::PathBuf>,
    /// `<bundle>/payload/runner/`，承载 runner wheel 与 `server.py`。
    pub bundled_runner_dir: Option<std::path::PathBuf>,
    /// `<bundle>/payload/desktop/`，承载桌面安装器（dmg / nsis）。
    pub bundled_desktop_dir: Option<std::path::PathBuf>,
    /// `<bundle>/payload/skills/`，Stage-InstallSkills 数据来源。
    pub bundled_skills_dir: Option<std::path::PathBuf>,
    /// `<bundle>/payload/onboarding-audio/<lang>/`，按语言组织的云端 TTS 引导音频。
    pub bundled_onboarding_audio_dir: Option<std::path::PathBuf>,
    /// `dmg` | `nsis`，unpack-desktop 阶段据此选择 hdiutil attach 或 NSIS /S。
    pub installer_format: Option<String>,
}

/// 启动 install.ps1 / install.sh 并流式返回输出。
///
/// `spiritagent_home_override` 作为 $SPIRITAGENT_HOME 传递给子脚本；`bundle` 作为 SPIRITAGENT_BUNDLE_* 环境变量。
pub async fn run_script(
    script_path: &Path,
    args: &[String],
    sink: StreamSink,
    spiritagent_home_override: Option<&str>,
    bundle: &BundleContext,
    mut cancel_rx: Option<CancelRx>,
) -> Result<ScriptResult> {
    let mut cmd = build_command(script_path, args);

    // 安装器可能被自更新替换；固定一个稳定 cwd，避免 bash/zsh 从已删除目录启动时打印 getcwd 错误。
    if let Some(cwd) = stable_script_cwd(script_path, spiritagent_home_override) {
        cmd.current_dir(cwd);
    }

    if let Some(home) = spiritagent_home_override {
        cmd.env("SPIRITAGENT_HOME", home);
    }

    // 下发 SPIRITAGENT_BUNDLE_* 变量，省去 CLI 长参数；各项独立（None 即省略），与 bootstrap 层的 CLI 覆盖不冲突。
    if let Some(p) = &bundle.bundle_dir {
        cmd.env("SPIRITAGENT_BUNDLE_DIR", p);
    }
    if let Some(p) = &bundle.bundled_runner_dir {
        cmd.env("SPIRITAGENT_BUNDLED_RUNNER_DIR", p);
    }
    if let Some(p) = &bundle.bundled_desktop_dir {
        cmd.env("SPIRITAGENT_BUNDLED_DESKTOP_DIR", p);
    }
    if let Some(p) = &bundle.bundled_skills_dir {
        cmd.env("SPIRITAGENT_BUNDLED_SKILLS_DIR", p);
    }
    if let Some(p) = &bundle.bundled_onboarding_audio_dir {
        cmd.env("SPIRITAGENT_BUNDLED_ONBOARDING_AUDIO_DIR", p);
    }
    if let Some(fmt) = &bundle.installer_format {
        cmd.env("SPIRITAGENT_INSTALLER_FORMAT", fmt);
    }

    cmd.stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    // 避免在 GUI 进程中弹出多余的 cmd 控制台窗口。
    #[cfg(target_os = "windows")]
    {
        // CREATE_NO_WINDOW = 0x08000000
        cmd.creation_flags(0x0800_0000);
    }

    let mut child: Child = cmd
        .spawn()
        .with_context(|| format!("spawning {} via {}", script_path.display(), interpreter_label()))?;

    let stdout = child.stdout.take().expect("stdout was piped");
    let stderr = child.stderr.take().expect("stderr was piped");

    let mut stdout_reader = BufReader::new(stdout).lines();
    let mut stderr_reader = BufReader::new(stderr).lines();

    let mut combined_stdout = String::new();
    let mut combined_stderr = String::new();
    let mut killed = false;

    loop {
        tokio::select! {
            line = stdout_reader.next_line() => {
                match line {
                    Ok(Some(l)) => {
                        (sink.on_stdout_line)(&l);
                        combined_stdout.push_str(&l);
                        combined_stdout.push('\n');
                    }
                    Ok(None) => break,
                    Err(e) => {
                        tracing::warn!("stdout read error: {e}");
                        break;
                    }
                }
            }
            line = stderr_reader.next_line() => {
                match line {
                    Ok(Some(l)) => {
                        (sink.on_stderr_line)(&l);
                        combined_stderr.push_str(&l);
                        combined_stderr.push('\n');
                    }
                    Ok(None) => {}
                    Err(e) => {
                        tracing::warn!("stderr read error: {e}");
                    }
                }
            }
            _ = recv_cancel(&mut cancel_rx) => {
                tracing::warn!("cancellation received — killing child");
                killed = true;
                // 尽力杀掉子进程，不向上传播错误。
                let _ = child.start_kill();
                break;
            }
        }
    }

    // 主循环退出后继续把残余行抽干，避免上层遗漏末尾输出。
    while let Ok(Some(l)) = stdout_reader.next_line().await {
        (sink.on_stdout_line)(&l);
        combined_stdout.push_str(&l);
        combined_stdout.push('\n');
    }
    while let Ok(Some(l)) = stderr_reader.next_line().await {
        (sink.on_stderr_line)(&l);
        combined_stderr.push_str(&l);
        combined_stderr.push('\n');
    }

    let status = child
        .wait()
        .await
        .context("waiting for install script to exit")?;

    Ok(ScriptResult {
        stdout: combined_stdout,
        stderr: combined_stderr,
        exit_code: status.code(),
        killed,
    })
}

fn stable_script_cwd<'a>(script_path: &'a Path, spiritagent_home_override: Option<&'a str>) -> Option<&'a Path> {
    if let Some(home) = spiritagent_home_override {
        let path = Path::new(home);
        if path.is_dir() {
            return Some(path);
        }
    }
    script_path.parent().filter(|p| p.is_dir())
}

async fn recv_cancel(rx: &mut Option<CancelRx>) {
    match rx {
        Some(r) => {
            let _ = r.recv().await;
        }
        None => std::future::pending::<()>().await,
    }
}

#[cfg(target_os = "windows")]
fn build_command(script_path: &Path, args: &[String]) -> Command {
    // install.ps1 全部使用 5.1 兼容语法；优先 powershell.exe（5.1 基线，Win7+ 均提供），不依赖 pwsh 7+。
    let mut cmd = Command::new(windows_powershell_exe());
    cmd.arg("-NoProfile");
    cmd.arg("-ExecutionPolicy").arg("Bypass");
    cmd.arg("-File").arg(script_path);
    for a in args {
        cmd.arg(a);
    }
    cmd
}

#[cfg(not(target_os = "windows"))]
fn build_command(script_path: &Path, args: &[String]) -> Command {
    // install.sh 要求 bash；macOS 自带的 bash 3.2 即可，脚本按该基线编写。
    let mut cmd = Command::new("bash");
    cmd.arg(script_path);
    for a in args {
        cmd.arg(a);
    }
    cmd
}

/// Windows 根（`%SystemRoot%`）下的 PowerShell 5.1 标准位置；独立函数（并对 test 开放）便于在任何主机上单测路径布局。
#[cfg(any(target_os = "windows", test))]
fn powershell_under_root(root: &Path) -> std::path::PathBuf {
    root.join("System32")
        .join("WindowsPowerShell")
        .join("v1.0")
        .join("powershell.exe")
}

/// 解析 PowerShell 解释器路径。
///
/// 不信任 PATH，因 Windows 会在过长时静默丢弃条目，导致 "program not found"；优先用绝对路径，再走 PATH / powershell 5.1 / pwsh 7，最后兜底 bare name。
#[cfg(target_os = "windows")]
fn windows_powershell_exe() -> std::path::PathBuf {
    for var in ["SystemRoot", "windir"] {
        if let Ok(root) = std::env::var(var) {
            let candidate = powershell_under_root(Path::new(&root));
            if candidate.is_file() {
                return candidate;
            }
        }
    }

    for exe in ["powershell.exe", "pwsh.exe"] {
        if let Ok(found) = which::which(exe) {
            return found;
        }
    }

    std::path::PathBuf::from("powershell.exe")
}

/// spawn 失败上下文中的人类可读解释器名；Windows 下走解析后的绝对路径，避免误以为脚本本身丢失。
#[cfg(target_os = "windows")]
fn interpreter_label() -> String {
    windows_powershell_exe().display().to_string()
}

#[cfg(not(target_os = "windows"))]
fn interpreter_label() -> String {
    "bash".to_string()
}

pub const STAGE_RESULT_SENTINEL: &str = "__SPIRITAGENT_STAGE_RESULT__:";
pub const MANIFEST_SENTINEL: &str = "__SPIRITAGENT_MANIFEST__:";

/// 解析 stdout 中由 sentinel 前缀标记的阶段结果 JSON 行 `{ok: bool, stage: string, ...}`。
/// 优先按 sentinel 精准匹配，未匹配时向前回退至普通 JSON 行以保持兼容。
pub fn parse_stage_result(stdout: &str) -> Option<crate::events::StageResultPayload> {
    for line in stdout.lines().rev() {
        let trimmed = line.trim();
        if let Some(payload_str) = trimmed.strip_prefix(STAGE_RESULT_SENTINEL) {
            if let Ok(parsed) = serde_json::from_str::<crate::events::StageResultPayload>(payload_str.trim()) {
                return Some(parsed);
            }
        }
    }

    // 兼容兜底：未找到 sentinel 时按裸 JSON 行解析
    for line in stdout.lines().rev() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        if let Ok(value) = serde_json::from_str::<serde_json::Value>(trimmed) {
            if value.get("ok").and_then(|v| v.as_bool()).is_some()
                && value.get("stage").and_then(|v| v.as_str()).is_some()
            {
                if let Ok(parsed) =
                    serde_json::from_value::<crate::events::StageResultPayload>(value)
                {
                    return Some(parsed);
                }
            }
        }
    }
    None
}

/// `-Manifest` 负载解析：找由 sentinel 前缀标记的单行 NDJSON 负载。
/// 优先按 sentinel 精准匹配，未匹配时向前回退至裸 JSON 或多行 JSON。
pub fn parse_manifest(stdout: &str) -> Option<crate::events::Manifest> {
    for line in stdout.lines().rev() {
        let trimmed = line.trim();
        if let Some(payload_str) = trimmed.strip_prefix(MANIFEST_SENTINEL) {
            if let Ok(parsed) = serde_json::from_str::<crate::events::Manifest>(payload_str.trim()) {
                return Some(parsed);
            }
        }
    }

    fn try_parse(blob: &str) -> Option<crate::events::Manifest> {
        let value = serde_json::from_str::<serde_json::Value>(blob).ok()?;
        if value.get("stages")?.as_array().is_none() {
            return None;
        }
        serde_json::from_value(value).ok()
    }

    // 先按行匹配（处理单行 JSON 与尾部 banner）。
    for line in stdout.lines().rev() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        if let Some(m) = try_parse(trimmed) {
            return Some(m);
        }
    }
    // 兜底：把整个 stdout 当作一个 JSON 对象解析（处理 PowerShell 多行 here-string 输出）。
    try_parse(stdout.trim())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_stage_result_with_sentinel_picks_last_sentinel_line() {
        let stdout = r#"
[bootstrap] some info
__SPIRITAGENT_STAGE_RESULT__:{"ok": false, "stage": "venv", "reason": "bad python"}
__SPIRITAGENT_STAGE_RESULT__:{"ok": true, "stage": "venv"}
final non-json banner
"#;
        let result = parse_stage_result(stdout).unwrap();
        assert_eq!(result.stage, "venv");
        assert!(result.ok);
    }

    #[test]
    fn parse_stage_result_fallback_picks_last_json_line() {
        let stdout = r#"
[bootstrap] some info
{"ok": false, "stage": "venv", "reason": "bad python"}
{"ok": true, "stage": "venv"}
final non-json banner
"#;
        let result = parse_stage_result(stdout).unwrap();
        assert_eq!(result.stage, "venv");
        assert!(result.ok);
    }

    #[test]
    fn parse_manifest_with_sentinel_finds_stages_array() {
        let stdout = r#"
info line
__SPIRITAGENT_MANIFEST__:{"stages": [{"name": "uv", "title": "uv", "category": "prereqs", "needs_user_input": false}], "protocol_version": 1}
trailing info
"#;
        let m = parse_manifest(stdout).unwrap();
        assert_eq!(m.stages.len(), 1);
        assert_eq!(m.stages[0].name, "uv");
        assert_eq!(m.protocol_version, Some(1));
    }

    #[test]
    fn parse_manifest_fallback_finds_stages_array() {
        let stdout = r#"
info line
{"stages": [{"name": "uv", "title": "uv", "category": "prereqs", "needs_user_input": false}], "protocol_version": 1}
"#;
        let m = parse_manifest(stdout).unwrap();
        assert_eq!(m.stages.len(), 1);
        assert_eq!(m.stages[0].name, "uv");
        assert_eq!(m.protocol_version, Some(1));
    }

    #[test]
    fn parse_manifest_handles_multiline_here_string() {
        let stdout = r#"{"protocol_version": 2, "stages": [
  {"name": "welcome", "title": "Preparing install", "category": "setup", "needs_user_input": false},
  {"name": "install-python", "title": "Installing Python runtime", "category": "prereqs", "needs_user_input": false}
]}
"#;
        let m = parse_manifest(stdout).unwrap();
        assert_eq!(m.stages.len(), 2);
        assert_eq!(m.protocol_version, Some(2));
    }

    #[test]
    fn parse_returns_none_when_no_match() {
        assert!(parse_stage_result("just banner\n").is_none());
        assert!(parse_manifest("just banner\n").is_none());
    }

    #[test]
    fn stable_script_cwd_prefers_existing_spiritagent_home() {
        let script = Path::new("/tmp/install.sh");
        let cwd = stable_script_cwd(script, Some("/"));
        assert_eq!(cwd, Some(Path::new("/")));
    }

    #[test]
    fn powershell_under_root_uses_system32_v1_layout() {
        let resolved = powershell_under_root(Path::new("C:\\Windows"));
        let normalized = resolved.to_string_lossy().replace('\\', "/");
        assert!(
            normalized.ends_with("System32/WindowsPowerShell/v1.0/powershell.exe"),
            "unexpected powershell path: {normalized}"
        );
    }
}
