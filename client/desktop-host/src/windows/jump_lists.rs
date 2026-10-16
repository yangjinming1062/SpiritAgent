use super::*;
use std::collections::HashMap;
use windows::Win32::Foundation::PROPERTYKEY;
use windows::Win32::System::Com::StructuredStorage::{PropVariantClear, PropVariantToString};
use windows::Win32::System::Com::{CLSCTX_INPROC_SERVER, CoCreateInstance, IPersistStream};
use windows::Win32::UI::Shell::PropertiesSystem::IPropertyStore;
use windows::Win32::UI::Shell::{CommandLineToArgvW, IShellLinkW, SHCreateMemStream, ShellLink};
use windows::core::{GUID, Interface, PCWSTR};

const MAX_FILES: usize = 256;
const MAX_FILE_BYTES: usize = 2 * 1024 * 1024;
const MAX_SCAN_BYTES: usize = 16 * 1024 * 1024;
const MAX_TASKS: usize = 64;
const MAX_ENTRIES: usize = 1024;
const MAX_STRING: usize = 32_768;
const SCAN_BUDGET: Duration = Duration::from_millis(750);
const LINK_CLASS: [u8; 16] = [1, 0x14, 2, 0, 0, 0, 0, 0, 0xc0, 0, 0, 0, 0, 0, 0, 0x46];
const TITLE: PROPERTYKEY = PROPERTYKEY {
    fmtid: GUID::from_u128(0xf29f85e0_4ff9_1068_ab91_08002b27b3d9),
    pid: 2,
};
const APP_ID: PROPERTYKEY = PROPERTYKEY {
    fmtid: GUID::from_u128(0x9f4c2855_9f79_4b39_a8d0_e1d42de1d5f3),
    pid: 5,
};

#[derive(Hash, Serialize)]
#[serde(rename_all = "camelCase")]
pub(super) struct JumpListTask {
    pub id: String,
    title: String,
    description: String,
    icon_path: String,
    icon_index: i32,
    #[serde(skip)]
    pub(super) program: String,
    #[serde(skip)]
    pub(super) args: String,
    #[serde(skip)]
    pub(super) working_directory: String,
}

#[derive(Default)]
pub(super) struct JumpListRead {
    pub tasks: Vec<JumpListTask>,
    pub diagnostics: Vec<String>,
}

struct Reader<'a> {
    bytes: &'a [u8],
    offset: usize,
}

impl<'a> Reader<'a> {
    fn take(&mut self, size: usize) -> Result<&'a [u8]> {
        let end = self.offset.checked_add(size).ok_or("Jump List 长度溢出")?;
        let value = self
            .bytes
            .get(self.offset..end)
            .ok_or("Jump List 数据不完整")?;
        self.offset = end;
        Ok(value)
    }

    fn word(&mut self) -> Result<usize> {
        Ok(u16::from_le_bytes(self.take(2)?.try_into().unwrap()) as usize)
    }

    fn dword(&mut self) -> Result<usize> {
        Ok(u32::from_le_bytes(self.take(4)?.try_into().unwrap()) as usize)
    }

    fn link(&mut self) -> Result<&'a [u8]> {
        if self.take(16)? != LINK_CLASS {
            return Err("Jump List 包含不支持的链接类型".into());
        }
        let start = self.offset;
        if self.dword()? != 0x4c || self.take(16)? != LINK_CLASS {
            return Err("Jump List 快捷方式头无效".into());
        }
        let flags = self.dword()?;
        self.take(0x4c - 24)?;
        if flags & 1 != 0 {
            let size = self.word()?;
            self.take(size)?;
        }
        if flags & 2 != 0 {
            let size = self.dword()?;
            self.take(size.checked_sub(4).ok_or("Jump List 路径块无效")?)?;
        }
        for flag in [4, 8, 16, 32, 64] {
            if flags & flag != 0 {
                let size = self.word()? * if flags & 0x80 != 0 { 2 } else { 1 };
                self.take(size)?;
            }
        }
        loop {
            let size = self.dword()?;
            if size == 0 {
                break;
            }
            if size < 8 {
                return Err("Jump List 扩展块无效".into());
            }
            self.take(size - 4)?;
        }
        Ok(&self.bytes[start..self.offset])
    }
}

fn wide_text(read: impl FnOnce(&mut [u16]) -> windows::core::Result<()>) -> Result<String> {
    let mut buffer = vec![0u16; MAX_STRING];
    read(&mut buffer).map_err(|error| error.to_string())?;
    let end = buffer
        .iter()
        .position(|value| *value == 0)
        .ok_or("Jump List 文本超过限制")?;
    String::from_utf16(&buffer[..end]).map_err(|_| "Jump List 文本编码无效".into())
}

fn property_text(store: &IPropertyStore, key: &PROPERTYKEY) -> String {
    let Ok(mut value) = (unsafe { store.GetValue(key) }) else {
        return String::new();
    };
    let text = wide_text(|buffer| unsafe { PropVariantToString(&value, buffer) });
    let _ = unsafe { PropVariantClear(&mut value) };
    text.unwrap_or_default()
}

fn normalize(value: &str) -> String {
    value.replace('/', "\\").to_lowercase()
}

fn explorer_program() -> String {
    std::env::var_os("SystemRoot")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(r"C:\Windows"))
        .join("explorer.exe")
        .to_string_lossy()
        .into_owned()
}

fn contains_utf16(bytes: &[u8], value: &str) -> bool {
    let haystack = String::from_utf16_lossy(
        &bytes
            .chunks_exact(2)
            .map(|item| u16::from_le_bytes([item[0], item[1]]))
            .collect::<Vec<_>>(),
    );
    haystack.to_lowercase().contains(&value.to_lowercase())
}

fn parse_task(bytes: &[u8], target: &str) -> Result<Option<JumpListTask>> {
    let link: IShellLinkW = unsafe { CoCreateInstance(&ShellLink, None, CLSCTX_INPROC_SERVER) }
        .map_err(|error| error.to_string())?;
    let stream = unsafe { SHCreateMemStream(Some(bytes)) }.ok_or("创建快捷方式流失败")?;
    let persist: IPersistStream = link.cast().map_err(|error| error.to_string())?;
    unsafe { persist.Load(&stream) }.map_err(|error| error.to_string())?;
    // 只读取，不调用 Resolve，避免修复快捷方式、网络寻址或打开系统对话框。
    let store: IPropertyStore = link.cast().map_err(|error| error.to_string())?;
    let app_id = property_text(&store, &APP_ID);
    let program =
        wide_text(|buffer| unsafe { link.GetPath(buffer, null_mut(), 0) }).unwrap_or_default();
    let app_target = target.strip_prefix("shell:appsfolder\\");
    let matches = if let Some(app_id_target) = app_target {
        normalize(&app_id) == app_id_target || contains_utf16(bytes, app_id_target)
    } else {
        normalize(&program) == target
    };
    if !matches {
        return Ok(None);
    }
    let description =
        wide_text(|buffer| unsafe { link.GetDescription(buffer) }).unwrap_or_default();
    let title = property_text(&store, &TITLE);
    let title = if title.is_empty() {
        &description
    } else {
        &title
    };
    if title.is_empty() {
        return Ok(None);
    }
    let args = wide_text(|buffer| unsafe { link.GetArguments(buffer) }).unwrap_or_default();
    let mut working_directory =
        wide_text(|buffer| unsafe { link.GetWorkingDirectory(buffer) }).unwrap_or_default();
    let mut icon_index = 0;
    let mut icon_path =
        wide_text(|buffer| unsafe { link.GetIconLocation(buffer, &mut icon_index) })
            .unwrap_or_default();
    let program = if let Some(app_id_target) = app_target {
        working_directory.clear();
        let launch_target = if app_id.is_empty() {
            app_id_target.to_owned()
        } else {
            app_id.clone()
        };
        let shell_target = format!("shell:AppsFolder\\{launch_target}");
        icon_index = 0;
        let explorer = explorer_program();
        icon_path = explorer.clone();
        shell_target
    } else {
        if !Path::new(&program).is_absolute() || !program.to_lowercase().ends_with(".exe") {
            return Ok(None);
        }
        if !Path::new(&program).is_file() {
            return Err("Jump List 目标程序不存在".into());
        }
        program
    };
    if icon_path.is_empty() {
        icon_index = 0;
        icon_path = program.clone();
    }
    Ok(Some(JumpListTask {
        id: String::new(),
        title: title.chars().take(255).collect(),
        description: description.chars().take(1024).collect(),
        icon_path,
        icon_index,
        program,
        args,
        working_directory,
    }))
}

fn parse_file(bytes: &[u8], target: &str, started: Instant) -> Result<Vec<JumpListTask>> {
    let mut reader = Reader { bytes, offset: 0 };
    if reader.dword()? != 2 {
        return Err("不支持的 Jump List 版本".into());
    }
    let categories = reader.dword()?;
    reader.take(4)?;
    if categories > 64 {
        return Err("Jump List 分类数量超过限制".into());
    }
    let mut tasks = Vec::new();
    let mut entries = 0;
    for _ in 0..categories {
        let kind = reader.dword()?;
        let count = match kind {
            0 => {
                let size = reader.word()? * 2;
                reader.take(size)?;
                reader.dword()?
            }
            1 => {
                reader.take(4)?;
                0
            }
            2 => reader.dword()?,
            _ => return Err("Jump List 包含未知分类".into()),
        };
        entries += count;
        if entries > MAX_ENTRIES || (kind == 2 && count > MAX_TASKS) {
            return Err("Jump List 条目数量超过限制".into());
        }
        for _ in 0..count {
            if started.elapsed() > SCAN_BUDGET {
                return Err("Jump List 读取超时".into());
            }
            let bytes = reader.link()?;
            if kind == 2 {
                if let Some(task) = parse_task(bytes, target)? {
                    tasks.push(task);
                }
            }
        }
        if reader.dword()? != 0xbabffbab {
            return Err("Jump List 分类结尾无效".into());
        }
    }
    if reader.offset != bytes.len() {
        return Err("Jump List 包含未解析的数据".into());
    }
    Ok(tasks)
}

fn task_key(task: &JumpListTask) -> (Vec<String>, u8) {
    let command = wide(&format!("task.exe {}", task.args));
    let mut count = 0;
    let argv = unsafe { CommandLineToArgvW(PCWSTR(command.as_ptr()), &mut count) };
    if argv.is_null() || !(1..=1024).contains(&count) {
        return (
            vec![task.program.clone(), task.title.clone(), task.args.clone()],
            0,
        );
    }
    let tokens = unsafe { std::slice::from_raw_parts(argv, count as usize) }
        .iter()
        .skip(1)
        .map(|token| unsafe { token.to_string() }.unwrap_or_default())
        .collect::<Vec<_>>();
    unsafe { LocalFree(argv.cast()) };
    let merge_profiles = Path::new(&task.program)
        .file_stem()
        .and_then(|name| name.to_str())
        .is_some_and(|name| {
            matches!(
                name.to_ascii_lowercase().as_str(),
                "brave" | "chrome" | "chromium" | "msedge" | "opera" | "vivaldi"
            )
        });
    let mut key = vec![
        normalize(&task.program),
        task.title.clone(),
        task.description.clone(),
    ];
    let mut preference = 2; // 优先无专用数据目录的任务，其次为数据目录仍存在的任务。
    let mut tokens = tokens.into_iter();
    while let Some(token) = tokens.next() {
        let lower = token.to_ascii_lowercase();
        let directory = if merge_profiles && lower == "--user-data-dir" {
            tokens.next()
        } else if merge_profiles && lower.starts_with("--user-data-dir=") {
            token.split_once('=').map(|(_, value)| value.to_owned())
        } else {
            key.push(token);
            continue;
        };
        preference = u8::from(directory.is_some_and(|value| Path::new(&value).is_dir()));
    }
    (key, preference)
}

pub(super) fn read_tasks(target: &str, session: &str) -> JumpListRead {
    let mut result = JumpListRead::default();
    let target = normalize(target);
    let scan = (|| -> Result<()> {
        if !target.starts_with("shell:appsfolder\\") && !Path::new(&target).is_file() {
            return Err("Jump List 目标程序不存在".into());
        }
        let Some(app_data) = std::env::var_os("APPDATA") else {
            return Ok(());
        };
        let directory =
            PathBuf::from(app_data).join("Microsoft\\Windows\\Recent\\CustomDestinations");
        let entries = match fs::read_dir(directory) {
            Ok(entries) => entries,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
            Err(error) => return Err(error.to_string()),
        };
        let started = Instant::now();
        let mut files = Vec::new();
        for entry in entries {
            let path = entry.map_err(|error| error.to_string())?.path();
            if path
                .extension()
                .is_some_and(|extension| extension == "customDestinations-ms")
            {
                files.push(path);
            }
            if files.len() > MAX_FILES || started.elapsed() > SCAN_BUDGET {
                return Err("Jump List 扫描超过限制".into());
            }
        }
        files.sort();
        let mut total_bytes = 0;
        let mut positions = HashMap::<Vec<String>, (usize, u8)>::new();
        for file in files {
            if started.elapsed() > SCAN_BUDGET {
                return Err("Jump List 读取超时".into());
            }
            let read = (|| -> Result<Vec<JumpListTask>> {
                let mut bytes = Vec::new();
                File::open(&file)
                    .map_err(|error| error.to_string())?
                    .take(MAX_FILE_BYTES as u64 + 1)
                    .read_to_end(&mut bytes)
                    .map_err(|error| error.to_string())?;
                total_bytes += bytes.len();
                if bytes.len() > MAX_FILE_BYTES || total_bytes > MAX_SCAN_BYTES {
                    return Err("Jump List 文件大小超过限制".into());
                }
                parse_file(&bytes, &target, started)
            })();
            match read {
                Ok(tasks) => {
                    for task in tasks {
                        let (key, preference) = task_key(&task);
                        if let Some((index, previous)) = positions.get_mut(&key) {
                            if preference > *previous {
                                result.tasks[*index] = task;
                                *previous = preference;
                            }
                        } else {
                            positions.insert(key, (result.tasks.len(), preference));
                            result.tasks.push(task);
                        }
                        if result.tasks.len() > MAX_TASKS {
                            return Err("Jump List 任务数量超过限制".into());
                        }
                    }
                }
                Err(error) => {
                    // 文件独立提交结果，损坏或不支持的其他应用不能清空当前应用任务。
                    if result.diagnostics.len() < 16 {
                        let name = file.file_name().unwrap_or_default().to_string_lossy();
                        result
                            .diagnostics
                            .push(format!("{name}: {error}").chars().take(512).collect());
                    }
                }
            }
            if total_bytes > MAX_SCAN_BYTES {
                return Err("Jump List 扫描大小超过限制".into());
            }
        }
        let mut revision = DefaultHasher::new();
        session.hash(&mut revision);
        target.hash(&mut revision);
        result.tasks.hash(&mut revision);
        let revision = revision.finish();
        for (index, task) in result.tasks.iter_mut().enumerate() {
            task.id = format!("{revision:016x}:{index}");
        }
        if serde_json::to_vec(&result.tasks)
            .map_err(|error| error.to_string())?
            .len()
            > 60 * 1024
        {
            return Err("Jump List 响应超过限制".into());
        }
        Ok(())
    })();
    if let Err(error) = scan {
        result.tasks.clear();
        result.diagnostics.push(error);
    }
    result
}
