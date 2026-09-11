//! 解析安装脚本路径：dev checkout → Tauri bundle.resources → build.rs 嵌入的 payload zip。
//! 安装器不联网，脚本版本即安装器构建版本。

use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context, Result};
use tauri::{AppHandle, Manager};

use crate::embedded_payload;

#[derive(Debug, Clone)]
pub struct ResolvedScript {
    pub path: PathBuf,
    pub source: ScriptSource,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ScriptSource {
    /// 来自 $SPIRITAGENT_SETUP_DEV_REPO_ROOT 指向的本地 checkout。
    DevCheckout,
    /// 来自 Tauri bundle.resources（payload/ 与 exe 同级，常见于 `--no-bundle` 的 _up_/payload/ 旁路）。
    Bundled,
    /// 来自 build.rs 嵌入的 payload zip（单 exe 分发场景）。
    Embedded,
}

#[derive(Debug, Clone, Copy)]
pub enum ScriptKind {
    Ps1,
    Sh,
}

impl ScriptKind {
    pub fn for_current_os() -> Self {
        if cfg!(target_os = "windows") {
            Self::Ps1
        } else {
            Self::Sh
        }
    }

    fn filename(&self) -> &'static str {
        match self {
            Self::Ps1 => "install.ps1",
            Self::Sh => "install.sh",
        }
    }
}

/// 解析安装脚本：dev 入口 → Tauri bundle.resources → 嵌入 zip；不自联网。
pub async fn resolve(
    app: &AppHandle,
    kind: ScriptKind,
    emit_log: &impl Fn(&str),
) -> Result<ResolvedScript> {
    // 1) 开发入口：通过环境变量指向 checkout，避免每次脚本改动都要重打包。
    if let Ok(repo_root) = std::env::var("SPIRITAGENT_SETUP_DEV_REPO_ROOT") {
        let candidate = PathBuf::from(repo_root).join("installer").join(kind.filename());
        if candidate.exists() {
            emit_log(&format!(
                "[bootstrap] dev mode — using local {} at {}",
                kind.filename(),
                candidate.display()
            ));
            return Ok(ResolvedScript {
                path: candidate,
                source: ScriptSource::DevCheckout,
            });
        }
    }

    // 2) 生产路径：Tauri bundle.resources（payload/ 与 exe 同级）。
    if let Ok(resource_dir) = app.path().resource_dir() {
        if let Ok(bundled) = resolve_bundled(&resource_dir, kind) {
            emit_log(&format!(
                "[bootstrap] using bundled {} at {}",
                kind.filename(),
                bundled.display()
            ));
            return Ok(ResolvedScript {
                path: bundled,
                source: ScriptSource::Bundled,
            });
        }
    }

    // 3) 单 exe 自包含：解压嵌入的 payload zip 到 SPIRITAGENT_HOME/bootstrap-payload/payload/。
    let payload_dir = embedded_payload::payload_dir()
        .with_context(|| "extracting embedded payload zip".to_string())?;
    let script_path = payload_dir.join(kind.filename());
    if !script_path.is_file() {
        return Err(anyhow!(
            "embedded payload is missing {} (build.rs likely failed to package ../payload/)",
            script_path.display()
        ));
    }
    emit_log(&format!(
        "[bootstrap] using embedded {} at {}",
        kind.filename(),
        script_path.display()
    ));
    Ok(ResolvedScript {
        path: script_path,
        source: ScriptSource::Embedded,
    })
}

/// 从 Tauri bundle.resources 读取安装脚本；纯函数，便于用临时目录直接单测。
fn resolve_bundled(resource_dir: &Path, kind: ScriptKind) -> Result<PathBuf> {
    let script_path = resource_dir.join("payload").join(kind.filename());
    if script_path.is_file() {
        return Ok(script_path);
    }
    Err(anyhow!(
        "install script not found in Tauri bundle: {}",
        script_path.display()
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn unique_tmp_dir(tag: &str) -> PathBuf {
        let base = std::env::temp_dir().join(format!(
            "spiritagent-install-script-test-{tag}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&base).unwrap();
        base
    }

    fn write_fake_bundled_script(install_root: &Path, kind: ScriptKind) {
        let payload = install_root.join("payload");
        fs::create_dir_all(&payload).unwrap();
        fs::write(payload.join(kind.filename()), b"#!/bin/sh\necho fake\n").unwrap();
    }

    /// 生产环境下 bundle.resources 中的脚本才是正解；本测试就是验证移除 GitHub 回退后解析器的首选路径。
    #[test]
    fn resolve_bundled_returns_script_in_payload() {
        let tmp = unique_tmp_dir("bundled-ok");
        write_fake_bundled_script(&tmp, ScriptKind::Sh);

        let resolved = resolve_bundled(&tmp, ScriptKind::Sh).unwrap();
        assert!(resolved.ends_with("install.sh"));
        assert_eq!(
            fs::read_to_string(&resolved).unwrap(),
            "#!/bin/sh\necho fake\n"
        );

        let _ = fs::remove_dir_all(&tmp);
    }

    /// resource_dir 下没有 payload/ 时 resolve_bundled 应返回 Err，使上层 fallback 到 embedded_payload。
    #[test]
    fn resolve_bundled_errors_when_payload_missing() {
        let tmp = unique_tmp_dir("bundled-missing");
        let err = resolve_bundled(&tmp, ScriptKind::Ps1).unwrap_err();
        let msg = format!("{err:#}");
        assert!(
            msg.contains("install script not found in Tauri bundle"),
            "unexpected error: {msg}"
        );

        let _ = fs::remove_dir_all(&tmp);
    }

    /// 防止 .ps1 / .sh 映射被写错，从而在错误的平台加载到错误的脚本。
    #[test]
    fn script_kind_filename_round_trip() {
        assert_eq!(ScriptKind::Ps1.filename(), "install.ps1");
        assert_eq!(ScriptKind::Sh.filename(), "install.sh");
    }
}
