use std::path::{Path, PathBuf};

fn main() {
    // 打包 ../payload 为 OUT_DIR/payload.zip，由 embedded_payload.rs 嵌入单 exe
    let payload_src = PathBuf::from("../payload");
    let out_dir = std::env::var("OUT_DIR").expect("OUT_DIR set by cargo");
    let zip_path = PathBuf::from(&out_dir).join("payload.zip");

    // 载荷不完整会产出装不上的安装器（缺桌面产物时运行期才报错），构建期直接失败
    build_payload_zip(&payload_src, &zip_path).expect("build complete installer payload");
    println!("cargo:rerun-if-changed={}", payload_src.display());

    // asInvoker，避免启发式要求 UAC 提权
    #[cfg(target_os = "windows")]
    let attrs = {
        let manifest = include_str!("spiritagent-setup.manifest");
        let win = tauri_build::WindowsAttributes::new().app_manifest(manifest);
        tauri_build::Attributes::new().windows_attributes(win)
    };

    #[cfg(not(target_os = "windows"))]
    let attrs = tauri_build::Attributes::new();

    tauri_build::try_build(attrs).expect("failed to run tauri-build");
}

/// 与 Tauri resources 使用相同的载荷入口。忽略暂存根中不属于安装包的文件。
fn build_payload_zip(src: &Path, dst: &Path) -> std::io::Result<()> {
    use std::fs::{self, File};
    use std::io::{Error, ErrorKind};

    fn collect(path: &Path, files: &mut Vec<PathBuf>) -> std::io::Result<()> {
        let kind = path.symlink_metadata()?.file_type();
        if kind.is_dir() {
            for entry in fs::read_dir(path)? {
                collect(&entry?.path(), files)?;
            }
        } else if kind.is_file() {
            files.push(path.to_path_buf());
        }
        Ok(())
    }

    let mut files = Vec::new();
    for script in ["install.sh", "install.ps1"] {
        let path = src.join(script);
        if !path.symlink_metadata()?.is_file() {
            return Err(Error::new(
                ErrorKind::InvalidData,
                format!("expected regular script: {script}"),
            ));
        }
        files.push(path);
    }
    let desktop_ext = if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        "exe"
    } else {
        "dmg"
    };
    for dir in ["runner", "client", "skills"] {
        let root = src.join(dir);
        let start = files.len();
        collect(&root, &mut files)?;
        let mut entries = files[start..].iter();
        let valid = match dir {
            "runner" => {
                entries
                    .filter(|p| {
                        p.parent() == Some(root.as_path())
                            && p.extension().is_some_and(|e| e == "whl")
                            && p.file_name()
                                .and_then(|n| n.to_str())
                                .is_some_and(|n| n.starts_with("spirit_agent-"))
                    })
                    .count()
                    == 1
            }
            "client" => {
                entries
                    .filter(|p| {
                        p.parent() == Some(root.as_path())
                            && p.extension().is_some_and(|e| e == desktop_ext)
                    })
                    .count()
                    == 1
            }
            _ => entries.any(|p| p.file_name().is_some_and(|n| n == "SKILL.md")),
        };
        if !valid {
            return Err(Error::new(
                ErrorKind::InvalidData,
                format!(
                    "incomplete or ambiguous installer payload: {}",
                    root.display()
                ),
            ));
        }
    }

    files.sort_unstable();
    let mut zip = zip::ZipWriter::new(File::create(dst)?);
    let options = zip::write::FileOptions::default();
    for path in files {
        let name = path
            .strip_prefix(src)
            .expect("file under payload")
            .to_string_lossy()
            .replace('\\', "/");
        zip.start_file(name, options)?;
        std::io::copy(&mut File::open(path)?, &mut zip)?;
    }
    zip.finish()?;
    Ok(())
}
