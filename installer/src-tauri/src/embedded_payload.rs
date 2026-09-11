//! 把 build.rs 嵌入的 payload zip 视为单一可信源：保证 `SpiritAgent-Setup.exe` 单文件可分发。
//!
//! 分发形态：
//!   - 资源就绪态：`resource_dir/payload/install.ps1` 已存在（开发模式或 `_up_/payload/` 旁路）→ 直接使用
//!   - 单 exe 形态：仅 `SpiritAgent-Setup.exe`，无 `payload/` 邻居 → 首次访问时把嵌入的 zip 解压到 SPIRITAGENT_HOME/bootstrap-payload/
//!
//! 解压目录内容由 build.rs 打包时的相对路径决定，根目录即为 `payload/`（与 Tauri `bundle.resources` 布局对齐）。

use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use anyhow::{Context, Result};

/// build.rs 把 `../payload/` 打包成 `OUT_DIR/payload.zip` 后用 `include_bytes!` 嵌入。
const PAYLOAD_ZIP: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/payload.zip"));

/// 全局缓存：避免每次 stage 调用都重新解压 100MB+ zip。
static EXTRACT_ROOT: OnceLock<PathBuf> = OnceLock::new();

/// 把嵌入的 zip 解压到 `dest`。覆盖式写入（先清空 dest 内旧文件）。
fn extract_to(dest: &Path) -> Result<()> {
    if dest.exists() {
        // 只清空 build.rs 关注的 payload/ 子目录内容，保留同级其它 SPIRITAGENT_HOME 数据
        let payload_dir = dest.join("payload");
        if payload_dir.is_dir() {
            for entry in std::fs::read_dir(&payload_dir)? {
                let entry = entry?;
                let p = entry.path();
                if p.is_dir() {
                    std::fs::remove_dir_all(&p)?;
                } else {
                    std::fs::remove_file(&p)?;
                }
            }
        }
    } else {
        std::fs::create_dir_all(dest)
            .with_context(|| format!("create payload cache root: {}", dest.display()))?;
    }

    let cursor = std::io::Cursor::new(PAYLOAD_ZIP);
    let mut archive = zip::ZipArchive::new(cursor).context("open embedded payload zip")?;

    let dest_payload = dest.join("payload");
    std::fs::create_dir_all(&dest_payload)
        .with_context(|| format!("create payload dir: {}", dest_payload.display()))?;

    for i in 0..archive.len() {
        let mut entry = archive.by_index(i)?;
        let raw_name = entry.name().to_string();
        // 防御：拒绝绝对路径或 `..` 跳出 dest 的条目（zip slip）
        if raw_name.contains("..") || Path::new(&raw_name).is_absolute() {
            anyhow::bail!("refusing unsafe zip entry path: {raw_name}");
        }
        let out_path = dest_payload.join(&raw_name);
        if entry.is_dir() {
            std::fs::create_dir_all(&out_path)?;
            continue;
        }
        if let Some(parent) = out_path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let mut buf = Vec::with_capacity(entry.size() as usize);
        entry.read_to_end(&mut buf)?;
        // 写临时文件再 rename，规避解压中途 exe 被部分写入导致 Stage-Unpack 失败
        let tmp = out_path.with_extension(format!(
            "{}.part",
            out_path
                .extension()
                .and_then(|e| e.to_str())
                .unwrap_or("bin")
        ));
        std::fs::write(&tmp, &buf)
            .with_context(|| format!("write {}", tmp.display()))?;
        // 已有同名文件时 std::fs::rename 在 Windows 上会失败，先 remove
        if out_path.exists() {
            // Windows 上 exe 正在被运行会让 remove 失败；这里覆盖式打开替代
            if out_path
                .extension()
                .and_then(|e| e.to_str())
                .map(|e| e.eq_ignore_ascii_case("exe"))
                .unwrap_or(false)
                && is_file_locked(&out_path)
            {
                anyhow::bail!(
                    "cannot replace locked target exe: {} (is the bundled desktop app running?)",
                    out_path.display()
                );
            }
            std::fs::remove_file(&out_path).ok();
        }
        std::fs::rename(&tmp, &out_path)
            .with_context(|| format!("rename {} -> {}", tmp.display(), out_path.display()))?;
    }

    Ok(())
}

/// Windows 上 `*.exe` 被持有时的探测；非 Windows 永远返回 false。
#[cfg(target_os = "windows")]
fn is_file_locked(path: &Path) -> bool {
    use std::os::windows::fs::OpenOptionsExt;
    std::fs::OpenOptions::new()
        .read(true)
        .share_mode(0) // 不允许其它进程共享
        .open(path)
        .is_err()
}

#[cfg(not(target_os = "windows"))]
fn is_file_locked(_path: &Path) -> bool {
    false
}

/// 返回 payload 解压根目录（首次访问时解压嵌入 zip），路径为 `<SPIRITAGENT_HOME>/bootstrap-payload/`。
pub fn ensure_extracted() -> Result<&'static Path> {
    if let Some(p) = EXTRACT_ROOT.get() {
        return Ok(p.as_path());
    }

    let root = crate::paths::spiritagent_home().join("bootstrap-payload");
    extract_to(&root).with_context(|| format!("extract embedded payload to {}", root.display()))?;
    let _ = EXTRACT_ROOT.set(root.clone());
    Ok(EXTRACT_ROOT.get().unwrap().as_path())
}

/// payload 的实际目录，等价于 `<ensure_extracted()>/payload/`。
pub fn payload_dir() -> Result<PathBuf> {
    Ok(ensure_extracted()?.join("payload"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn payload_zip_is_non_empty() {
        assert!(
            !PAYLOAD_ZIP.is_empty(),
            "embedded payload zip is empty — build.rs likely failed to package ../payload/"
        );
    }

    #[test]
    fn payload_zip_parses() {
        let cursor = std::io::Cursor::new(PAYLOAD_ZIP);
        let mut archive = zip::ZipArchive::new(cursor).expect("zip should parse");
        // 至少要有 install.ps1 + install.sh + runner/server.py + client/*.exe
        let names: Vec<String> = (0..archive.len())
            .map(|i| archive.by_index(i).unwrap().name().to_string())
            .collect();
        assert!(
            names.iter().any(|n| n.ends_with("install.ps1")),
            "expected install.ps1 in payload zip, got {names:?}"
        );
        assert!(
            names.iter().any(|n| n.ends_with("install.sh")),
            "expected install.sh in payload zip"
        );
        assert!(
            names.iter().any(|n| n.ends_with("server.py")),
            "expected runner/server.py in payload zip"
        );
    }
}
