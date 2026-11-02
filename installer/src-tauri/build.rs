use std::path::{Path, PathBuf};

fn main() {
    // 打包 ../payload 为 OUT_DIR/payload.zip，由 embedded_payload.rs 嵌入单 exe
    let payload_src = PathBuf::from("../payload");
    let out_dir = std::env::var("OUT_DIR").expect("OUT_DIR set by cargo");
    let zip_path = PathBuf::from(&out_dir).join("payload.zip");

    // 载荷不完整会产出装不上的安装器（缺桌面产物时运行期才报错），构建期直接失败
    require_payload(&payload_src);
    build_payload_zip(&payload_src, &zip_path);
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

/// 单 exe 与 bundle 共用同一份载荷，缺任一项都会在安装阶段失败。
fn require_payload(payload: &Path) {
    let non_empty_dir = |dir: &Path| {
        std::fs::read_dir(dir)
            .map(|mut entries| entries.next().is_some())
            .unwrap_or(false)
    };

    let mut missing: Vec<&str> = ["install.sh", "install.ps1"]
        .into_iter()
        .filter(|name| !payload.join(name).is_file())
        .collect();
    for dir in ["runner", "client", "skills", "onboarding-audio"] {
        if !non_empty_dir(&payload.join(dir)) {
            missing.push(dir);
        }
    }

    if !missing.is_empty() {
        panic!(
            "installer payload incomplete at {}: missing or empty {:?}\n\
             run `python scripts/build.py --version <version> --target <win|mac>` to stage a complete payload",
            payload.display(),
            missing
        );
    }
}

/// 递归打包 src 为 zip；arcname 用相对 POSIX 路径。
fn build_payload_zip(src: &Path, dst: &Path) {
    use std::fs::File;

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
                zip.start_file(&rel, options)?;
                std::io::copy(&mut File::open(&path)?, zip)?;
            }
            // 跳过符号链接/特殊文件，避免 Windows 解压歧义
        }
        Ok(())
    }

    let file = File::create(dst).expect("create embedded payload zip");
    let mut zip = zip::ZipWriter::new(file);
    let options = zip::write::FileOptions::default();

    visit(src, src, options, &mut zip).expect("walk payload dir");
    zip.finish().expect("finish embedded payload zip");
}
