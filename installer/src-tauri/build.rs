use std::path::{Path, PathBuf};

fn main() {
    // 自包含安装器：把 ../payload/ 打包成单 zip 写入 OUT_DIR，
    // 由 embedded_payload.rs 通过 include_bytes! 嵌入 SpiritAgent-Setup.exe。
    // 运行时若 resource_dir/ 下找不到 payload/（单 exe 分发场景），解压这里嵌入的 zip。
    let payload_src = PathBuf::from("../payload");
    let out_dir = std::env::var("OUT_DIR").expect("OUT_DIR set by cargo");
    let zip_path = PathBuf::from(&out_dir).join("payload.zip");
    if payload_src.exists() {
        build_payload_zip(&payload_src, &zip_path);
        println!("cargo:rerun-if-changed={}", payload_src.display());
    } else {
        println!(
            "cargo:warning=spiritagent-bootstrap: ../payload not found; \
             embedded payload will be empty — single-exe distribution will fail"
        );
        // 仍写入空 zip：include_bytes! 需要该文件存在；空负载由 `payload_zip_is_non_empty` 测试与运行时报错。
        std::fs::File::create(&zip_path)
            .unwrap_or_else(|e| panic!("create empty payload.zip at {}: {e}", zip_path.display()));
    }

    // Windows manifest：level="asInvoker"，避免安装器启发式要求 UAC 提权。
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

/// 把 src 目录递归打包为 zip 写入 dst；arcname 用相对路径、POSIX 风格。
fn build_payload_zip(src: &Path, dst: &Path) {
    use std::fs::File;
    use std::io::Write;

    fn io_other<E: Into<Box<dyn std::error::Error + Send + Sync>>>(e: E) -> std::io::Error {
        std::io::Error::new(std::io::ErrorKind::Other, e)
    }

    // 递归遍历：options 显式作为参数传入以避免 fn 捕获环境（E0434）。
    fn visit(
        dir: &Path,
        base: &Path,
        options: zip::write::FileOptions,
        zip: &mut zip::ZipWriter<File>,
    ) -> std::io::Result<()> {
        for entry in std::fs::read_dir(dir)? {
            let entry = entry?;
            let path = entry.path();
            let file_type = entry.file_type()?;
            if file_type.is_dir() {
                visit(&path, base, options, zip)?;
            } else if file_type.is_file() {
                let rel = path
                    .strip_prefix(base)
                    .expect("entry under base")
                    .to_string_lossy()
                    .replace('\\', "/");
                zip.start_file(&rel, options).map_err(io_other)?;
                let bytes = std::fs::read(&path)?;
                zip.write_all(&bytes).map_err(io_other)?;
            }
            // 符号链接/特殊文件一律跳过，避免运行时 Windows 解压歧义
        }
        Ok(())
    }

    let file = File::create(dst).expect("create embedded payload zip");
    let mut zip = zip::ZipWriter::new(file);
    let options = zip::write::FileOptions::default();

    visit(src, src, options, &mut zip).expect("walk payload dir");
    zip.finish().expect("finish embedded payload zip");
}
