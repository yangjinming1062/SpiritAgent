//! 解析安装脚本路径：dev checkout → bundle.resources → 嵌入 payload zip；不自联网。

use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context, Result};
use tauri::{AppHandle, Manager};

use crate::embedded_payload;

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

    pub(crate) fn filename(&self) -> &'static str {
        match self {
            Self::Ps1 => "install.ps1",
            Self::Sh => "install.sh",
        }
    }
}

/// 解析安装脚本：dev 入口 → bundle.resources → 嵌入 zip。
pub async fn resolve(app: &AppHandle, kind: ScriptKind) -> Result<PathBuf> {
    // 1) 开发入口：环境变量指向 checkout，免重打包
    if let Ok(repo_root) = std::env::var("SPIRITAGENT_SETUP_DEV_REPO_ROOT") {
        let candidate = PathBuf::from(repo_root).join("installer").join(kind.filename());
        if candidate.exists() {
            tracing::info!(?candidate, "dev mode — using local install script");
            return Ok(candidate);
        }
    }

    // 2) 生产路径：Tauri bundle.resources
    if let Ok(bundled) = app
        .path()
        .resource_dir()
        .map_err(anyhow::Error::from)
        .and_then(|dir| resolve_bundled(&dir, kind))
    {
        tracing::info!(?bundled, "using bundled install script");
        return Ok(bundled);
    }

    // 3) 单 exe：解压嵌入 zip 到 bootstrap-payload/payload/
    let script_path = embedded_payload::payload_dir()
        .with_context(|| "extracting embedded payload zip".to_string())?
        .join(kind.filename());
    if !script_path.is_file() {
        return Err(anyhow!(
            "embedded payload is missing {} (build.rs likely failed to package ../payload/)",
            script_path.display()
        ));
    }
    tracing::info!(?script_path, "using embedded install script");
    Ok(script_path)
}

/// 从 bundle.resources 读取安装脚本；纯函数便于临时目录单测。
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

    #[test]
    fn resolve_bundled_returns_script_in_payload() {
        let tmp = unique_tmp_dir("bundled-ok");
        let payload = tmp.join("payload");
        fs::create_dir_all(&payload).unwrap();
        fs::write(payload.join("install.sh"), b"#!/bin/sh\necho fake\n").unwrap();

        let resolved = resolve_bundled(&tmp, ScriptKind::Sh).unwrap();
        assert!(resolved.ends_with("install.sh"));

        let _ = fs::remove_dir_all(&tmp);
    }

    /// 缺 payload/ 时返回 Err，使上层 fallback 到 embedded_payload。
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

    #[test]
    fn script_kind_filename_round_trip() {
        assert_eq!(ScriptKind::Ps1.filename(), "install.ps1");
        assert_eq!(ScriptKind::Sh.filename(), "install.sh");
    }
}
