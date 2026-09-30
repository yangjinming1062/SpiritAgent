//! 驱动 install 脚本子进程：Windows 走 PowerShell，Unix 走 bash。

use anyhow::{Context, Result};
use std::path::Path;
use std::process::Stdio;
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, BufReader, Lines};
use tokio::process::{Child, ChildStderr, ChildStdout, Command};
use tokio::sync::mpsc;

/// 脚本退出后继续读残余输出的上限；后代进程持有管道时不会 EOF。
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

/// 取消信号：`cancel_tx.send(()).await` 中止脚本。
pub type CancelRx = mpsc::Receiver<()>;

/// bundle.resources 布局派生的可选上下文，经 `SPIRITAGENT_BUNDLED_*` 下发给子脚本。
#[derive(Debug, Clone, Default)]
pub struct BundleContext {
    /// runner wheel 与 `server.py`。
    pub bundled_runner_dir: Option<std::path::PathBuf>,
    /// 桌面安装器（dmg / nsis）。
    pub bundled_desktop_dir: Option<std::path::PathBuf>,
    /// Stage-InstallSkills 数据来源。
    pub bundled_skills_dir: Option<std::path::PathBuf>,
    /// 按语言子目录的引导音频。
    pub bundled_onboarding_audio_dir: Option<std::path::PathBuf>,
    /// `dmg` | `nsis`，unpack-desktop 据此选择安装方式。
    pub installer_format: Option<String>,
}

/// 启动 install 脚本并流式返回输出。取消终止整棵进程树；返回值第二项为未触发的取消通道，须归还 holder。
pub async fn run_script(
    script_path: &Path,
    args: &[String],
    sink: StreamSink,
    spiritagent_home_override: Option<&str>,
    bundle: &BundleContext,
    mut cancel_rx: Option<CancelRx>,
) -> Result<(ScriptResult, Option<CancelRx>)> {
    let mut cmd = build_command(script_path, args);

    // 稳定 cwd，避免安装器被替换后从已删除目录启动报 getcwd 错误
    if let Some(cwd) = stable_script_cwd(script_path, spiritagent_home_override) {
        cmd.current_dir(cwd);
    }

    if let Some(home) = spiritagent_home_override {
        cmd.env("SPIRITAGENT_HOME", home);
    }

    // SPIRITAGENT_BUNDLED_* 独立下发（None 即省略），不与 CLI 覆盖冲突
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

    // GUI 进程不弹 cmd 控制台
    #[cfg(target_os = "windows")]
    {
        // CREATE_NO_WINDOW = 0x08000000
        cmd.creation_flags(0x0800_0000);
    }
    // 脚本自成进程组，取消时整组终止
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
                cancel_rx = None;
                process_tree.kill(&mut child);
            }
        }
    };

    // 抽干残余输出；不等待持有管道的残留进程，避免拖住结果
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

    /// 转发下一行；EOF/读错关闭该管道，两端关闭后 pending，可安全用于 `select!`。
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

/// 处理一次读取，返回该管道是否仍可读。
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

/// 脚本及后代进程树：Unix 进程组 / Windows Job Object；只杀子进程会留下占用管道的后代。
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

    /// 启动后立即入 Job，后代自动归属。
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

    /// 尽力整树终止，失败退回只杀脚本进程。
    fn kill(&self, child: &mut Child) {
        #[cfg(unix)]
        if let Some(pgid) = self.pgid {
            // SAFETY: killpg 只向进程组发信号
            if unsafe { libc::killpg(pgid, libc::SIGKILL) } == 0 {
                return;
            }
            tracing::warn!(error = %std::io::Error::last_os_error(), "killpg failed; killing the script process only");
        }
        #[cfg(windows)]
        if let Some(job) = &self.job {
            use std::os::windows::io::AsRawHandle;
            // SAFETY: job 为本结构持有的有效句柄
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

    // SAFETY: 空指针为默认安全属性的匿名 Job；成功句柄归 OwnedHandle
    let job = unsafe { HandleOrNull::from_raw_handle(CreateJobObjectW(std::ptr::null(), std::ptr::null())) };
    let job = OwnedHandle::try_from(job).map_err(|_| std::io::Error::last_os_error())?;
    // SAFETY: job 有效，process 取自未回收的 Child
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
    // install.ps1 为 PS 5.1 兼容；优先 powershell.exe，不依赖 pwsh 7+
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
    // install.sh 按 bash 3.2 基线编写
    let mut cmd = Command::new("bash");
    cmd.arg(script_path);
    for a in args {
        cmd.arg(a);
    }
    cmd
}

/// `%SystemRoot%` 下 PowerShell 5.1 标准路径；独立函数便于任意主机单测。
#[cfg(any(target_os = "windows", test))]
fn powershell_under_root(root: &Path) -> std::path::PathBuf {
    root.join("System32")
        .join("WindowsPowerShell")
        .join("v1.0")
        .join("powershell.exe")
}

/// 解析 PowerShell 路径；不信任 PATH（过长会被静默丢弃），优先绝对路径再 PATH/bare name。
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

/// spawn 失败时的人类可读解释器名。
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

/// 解析 sentinel 前缀标记的阶段结果 JSON 行。
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
