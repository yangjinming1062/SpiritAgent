//! 驱动 install.ps1 / install.sh 的子进程：Windows 下走 PowerShell，Unix 下走 bash。
//! Windows: `-NoProfile -ExecutionPolicy Bypass -File <script>`；Unix: `bash <script>`。

use anyhow::{Context, Result};
use std::path::Path;
use std::process::Stdio;
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, BufReader, Lines};
use tokio::process::{Child, ChildStderr, ChildStdout, Command};
use tokio::sync::mpsc;

/// 脚本进程退出后继续读取残余输出的上限：仍存活的后代进程持有继承的管道写端时，管道不会 EOF。
const PIPE_DRAIN_TIMEOUT: Duration = Duration::from_secs(2);

pub struct StreamSink {
    pub on_stdout_line: Box<dyn Fn(&str) + Send + Sync>,
    pub on_stderr_line: Box<dyn Fn(&str) + Send + Sync>,
}

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
/// 作为 `SPIRITAGENT_BUNDLED_*` 等环境变量下发给子脚本，避免用大量 CLI 参数。
#[derive(Debug, Clone, Default)]
pub struct BundleContext {
    /// `<bundle>/payload/runner/`，承载 runner wheel 与 `server.py`。
    pub bundled_runner_dir: Option<std::path::PathBuf>,
    /// `<bundle>/payload/client/`，承载桌面安装器（dmg / nsis）。
    pub bundled_desktop_dir: Option<std::path::PathBuf>,
    /// `<bundle>/payload/skills/`，Stage-InstallSkills 数据来源。
    pub bundled_skills_dir: Option<std::path::PathBuf>,
    /// `<bundle>/payload/onboarding-audio/`，按语言子目录组织的引导音频。
    pub bundled_onboarding_audio_dir: Option<std::path::PathBuf>,
    /// `dmg` | `nsis`，unpack-desktop 阶段据此选择 hdiutil attach 或 NSIS /S。
    pub installer_format: Option<String>,
}

/// 启动 install.ps1 / install.sh 并流式返回输出。
///
/// `spiritagent_home_override` 作为 $SPIRITAGENT_HOME 传递给子脚本；`bundle` 作为 `SPIRITAGENT_BUNDLED_*` 等环境变量。
/// 取消时终止脚本及其全部后代进程；脚本退出后最多再读 `PIPE_DRAIN_TIMEOUT`，不等待仍持有管道的残留进程。
/// 返回值第二项是未触发的取消通道，调用方须归还给 holder，否则后续阶段无法再响应取消。
pub async fn run_script(
    script_path: &Path,
    args: &[String],
    sink: StreamSink,
    spiritagent_home_override: Option<&str>,
    bundle: &BundleContext,
    mut cancel_rx: Option<CancelRx>,
) -> Result<(ScriptResult, Option<CancelRx>)> {
    let mut cmd = build_command(script_path, args);

    // 安装器可能被自更新替换；固定一个稳定 cwd，避免 bash/zsh 从已删除目录启动时打印 getcwd 错误。
    if let Some(cwd) = stable_script_cwd(script_path, spiritagent_home_override) {
        cmd.current_dir(cwd);
    }

    if let Some(home) = spiritagent_home_override {
        cmd.env("SPIRITAGENT_HOME", home);
    }

    // 下发 SPIRITAGENT_BUNDLED_* 等变量，省去 CLI 长参数；各项独立（None 即省略），与 bootstrap 层的 CLI 覆盖不冲突。
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
    // 脚本自成进程组（组号即其 PID），后代进程默认留在组内，取消时整组终止。
    #[cfg(unix)]
    cmd.process_group(0);

    let mut child: Child = cmd
        .spawn()
        .with_context(|| format!("spawning {} via {}", script_path.display(), interpreter_label()))?;
    let process_tree = ProcessTree::attach(&child);

    let mut output = ScriptOutput::new(
        child.stdout.take().expect("stdout was piped"),
        child.stderr.take().expect("stderr was piped"),
    );
    let mut killed = false;

    let status = loop {
        tokio::select! {
            () = output.read_line(&sink) => {}
            exit = child.wait() => break exit.context("waiting for install script to exit")?,
            _ = recv_cancel(&mut cancel_rx) => {
                tracing::warn!("cancellation received — terminating install script process tree");
                killed = true;
                // 已触发的通道不再回收。
                cancel_rx = None;
                process_tree.kill(&mut child);
            }
        }
    };

    // 抽干脚本退出前写入的残余输出；后代进程仍持有管道写端时不等待 EOF，避免结果被其拖住。
    let drain = async {
        while output.is_open() {
            output.read_line(&sink).await;
        }
    };
    if tokio::time::timeout(PIPE_DRAIN_TIMEOUT, drain).await.is_err() {
        tracing::warn!("install script exited but its output pipes are still held open; stopped reading");
    }

    Ok((
        ScriptResult {
            stdout: output.stdout_text,
            stderr: output.stderr_text,
            exit_code: status.code(),
            killed,
        },
        cancel_rx,
    ))
}

/// 脚本 stdout / stderr 的逐行转发与累计。
struct ScriptOutput {
    stdout: Lines<BufReader<ChildStdout>>,
    stderr: Lines<BufReader<ChildStderr>>,
    stdout_open: bool,
    stderr_open: bool,
    stdout_text: String,
    stderr_text: String,
}

impl ScriptOutput {
    fn new(stdout: ChildStdout, stderr: ChildStderr) -> Self {
        Self {
            stdout: BufReader::new(stdout).lines(),
            stderr: BufReader::new(stderr).lines(),
            stdout_open: true,
            stderr_open: true,
            stdout_text: String::new(),
            stderr_text: String::new(),
        }
    }

    fn is_open(&self) -> bool {
        self.stdout_open || self.stderr_open
    }

    /// 转发任一管道的下一行；EOF 或读错即关闭该管道，两端都关闭后不再完成。可安全用于 `select!` 分支。
    async fn read_line(&mut self, sink: &StreamSink) {
        tokio::select! {
            line = self.stdout.next_line(), if self.stdout_open => {
                self.stdout_open = accept_line(line, &*sink.on_stdout_line, &mut self.stdout_text, "stdout");
            }
            line = self.stderr.next_line(), if self.stderr_open => {
                self.stderr_open = accept_line(line, &*sink.on_stderr_line, &mut self.stderr_text, "stderr");
            }
            else => std::future::pending::<()>().await,
        }
    }
}

/// 处理一次读取结果，返回该管道是否仍可继续读取。
fn accept_line(
    line: std::io::Result<Option<String>>,
    forward: &(dyn Fn(&str) + Send + Sync),
    text: &mut String,
    stream: &str,
) -> bool {
    match line {
        Ok(Some(l)) => {
            forward(&l);
            text.push_str(&l);
            text.push('\n');
            true
        }
        Ok(None) => false,
        Err(e) => {
            tracing::warn!("{stream} read error: {e}");
            false
        }
    }
}

/// 脚本进程及其后代：Unix 为脚本自建的进程组，Windows 为容纳脚本进程的 Job Object。
/// 取消只终止直接子进程时，uv、NSIS、curl 等后代会继续运行并占用继承的输出管道。
struct ProcessTree {
    #[cfg(unix)]
    pgid: Option<libc::pid_t>,
    #[cfg(windows)]
    job: Option<std::os::windows::io::OwnedHandle>,
}

impl ProcessTree {
    #[cfg(unix)]
    fn attach(child: &Child) -> Self {
        Self {
            pgid: child.id().and_then(|pid| libc::pid_t::try_from(pid).ok()),
        }
    }

    /// 脚本启动后立即入 Job，此后它创建的后代进程自动归属同一 Job。
    #[cfg(windows)]
    fn attach(child: &Child) -> Self {
        let job = child.raw_handle().and_then(|process| match assign_to_new_job(process) {
            Ok(job) => Some(job),
            Err(e) => {
                tracing::warn!(error = %e, "failed to put install script into a job object; cancel will only kill the script process");
                None
            }
        });
        Self { job }
    }

    /// 尽力终止整棵进程树，失败时退回只杀脚本进程；错误只记日志。
    fn kill(&self, child: &mut Child) {
        #[cfg(unix)]
        if let Some(pgid) = self.pgid {
            // SAFETY: killpg 只向进程组发送信号，不涉及内存访问。
            if unsafe { libc::killpg(pgid, libc::SIGKILL) } == 0 {
                return;
            }
            tracing::warn!(error = %std::io::Error::last_os_error(), "killpg failed; killing the script process only");
        }
        #[cfg(windows)]
        if let Some(job) = &self.job {
            use std::os::windows::io::AsRawHandle;
            // SAFETY: job 是本结构持有的有效 Job Object 句柄。
            if unsafe { windows_sys::Win32::System::JobObjects::TerminateJobObject(job.as_raw_handle(), 1) } != 0 {
                return;
            }
            tracing::warn!(error = %std::io::Error::last_os_error(), "TerminateJobObject failed; killing the script process only");
        }
        let _ = child.start_kill();
    }
}

#[cfg(windows)]
fn assign_to_new_job(process: std::os::windows::io::RawHandle) -> std::io::Result<std::os::windows::io::OwnedHandle> {
    use std::os::windows::io::{AsRawHandle, HandleOrNull, OwnedHandle};
    use windows_sys::Win32::System::JobObjects::{AssignProcessToJobObject, CreateJobObjectW};

    // SAFETY: 空指针表示默认安全属性的匿名 Job；失败返回 NULL，由 HandleOrNull 转为错误，成功时句柄归 OwnedHandle 关闭。
    let job = unsafe { HandleOrNull::from_raw_handle(CreateJobObjectW(std::ptr::null(), std::ptr::null())) };
    let job = OwnedHandle::try_from(job).map_err(|_| std::io::Error::last_os_error())?;
    // SAFETY: job 有效；process 取自仍未回收的 Child，句柄在调用期间有效。
    if unsafe { AssignProcessToJobObject(job.as_raw_handle(), process) } == 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(job)
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
pub fn parse_stage_result(stdout: &str) -> Option<crate::events::StageResultPayload> {
    for line in stdout.lines().rev() {
        let trimmed = line.trim();
        if let Some(payload_str) = trimmed.strip_prefix(STAGE_RESULT_SENTINEL) {
            if let Ok(parsed) = serde_json::from_str::<crate::events::StageResultPayload>(payload_str.trim()) {
                return Some(parsed);
            }
        }
    }
    None
}

/// `-Manifest` 负载解析：找由 sentinel 前缀标记的单行 NDJSON 负载。
pub fn parse_manifest(stdout: &str) -> Option<crate::events::Manifest> {
    for line in stdout.lines().rev() {
        let trimmed = line.trim();
        if let Some(payload_str) = trimmed.strip_prefix(MANIFEST_SENTINEL) {
            if let Ok(parsed) = serde_json::from_str::<crate::events::Manifest>(payload_str.trim()) {
                return Some(parsed);
            }
        }
    }
    None
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
    fn parse_stage_result_rejects_bare_json_without_sentinel() {
        let stdout = r#"
[bootstrap] some info
{"ok": false, "stage": "venv", "reason": "bad python"}
{"ok": true, "stage": "venv"}
final non-json banner
"#;
        let result = parse_stage_result(stdout);
        assert!(result.is_none(), "bare JSON without sentinel must not match");
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
    fn parse_manifest_rejects_bare_json_without_sentinel() {
        let stdout = r#"
info line
{"stages": [{"name": "uv", "title": "uv", "category": "prereqs", "needs_user_input": false}], "protocol_version": 1}
"#;
        let m = parse_manifest(stdout);
        assert!(m.is_none(), "bare JSON without sentinel must not match");
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
