use super::*;
use crate::Palette;
use std::sync::atomic::{AtomicI32, AtomicU32, AtomicU64, Ordering};
use windows::Win32::Storage::FileSystem::{MOVE_FILE_FLAGS, MoveFileExW};
use windows::Win32::System::Com::CoTaskMemFree;
use windows::Win32::UI::Shell::{FOLDERID_LocalAppData, KF_FLAG_DEFAULT, SHGetKnownFolderPath};
use windows::core::PCWSTR;
use windows_sys::Win32::System::LibraryLoader::{
    GetProcAddress, LOAD_LIBRARY_SEARCH_DEFAULT_DIRS, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR, LoadLibraryExW,
};
use windows_sys::Win32::System::Memory::{
    CreateFileMappingW, FILE_MAP_ALL_ACCESS, MEMORY_MAPPED_VIEW_ADDRESS, MapViewOfFile, OpenFileMappingW,
    PAGE_READWRITE, UnmapViewOfFile,
};
use windows_sys::Win32::System::SystemInformation::GetTickCount64;

const MAGIC: u32 = 0x53545259;
const VERSION: u32 = 1;
const PLACEMENT_MAGIC: u32 = 0x5354504c;
const STYLE_TIMEOUT: Duration = Duration::from_secs(4);
const MAX_COMPONENT_BYTES: u64 = 16 * 1024 * 1024;

#[repr(C)]
struct State {
    magic: u32,
    version: u32,
    host_pid: u32,
    explorer_pid: u32,
    host_created: u64,
    taskbar: u64,
    heartbeat: AtomicU64,
    active: AtomicI32,
    dark: AtomicI32,
    status: AtomicI32,
    error: AtomicI32,
    fill_count: AtomicU32,
    reserved: u32,
}

const _: [(); 64] = [(); size_of::<State>()];
const _: [(); 40] = [(); std::mem::offset_of!(State, active)];
const _: [(); 48] = [(); std::mem::offset_of!(State, status)];
const _: [(); 56] = [(); std::mem::offset_of!(State, fill_count)];

#[repr(C)]
struct PlacementState {
    magic: u32,
    version: u32,
    host_pid: u32,
    explorer_pid: u32,
    host_created: u64,
    taskbar: u64,
    sequence: AtomicI32,
    enabled: AtomicI32,
    x: AtomicI32,
    y: AtomicI32,
    width: AtomicI32,
    height: AtomicI32,
    dpi: AtomicI32,
    status: AtomicI32,
    error: AtomicI32,
    corrections: AtomicU32,
}

const _: [(); 72] = [(); size_of::<PlacementState>()];
const _: [(); 0] = [(); std::mem::offset_of!(PlacementState, magic)];
const _: [(); 4] = [(); std::mem::offset_of!(PlacementState, version)];
const _: [(); 8] = [(); std::mem::offset_of!(PlacementState, host_pid)];
const _: [(); 12] = [(); std::mem::offset_of!(PlacementState, explorer_pid)];
const _: [(); 16] = [(); std::mem::offset_of!(PlacementState, host_created)];
const _: [(); 24] = [(); std::mem::offset_of!(PlacementState, taskbar)];
const _: [(); 32] = [(); std::mem::offset_of!(PlacementState, sequence)];
const _: [(); 36] = [(); std::mem::offset_of!(PlacementState, enabled)];
const _: [(); 40] = [(); std::mem::offset_of!(PlacementState, x)];
const _: [(); 44] = [(); std::mem::offset_of!(PlacementState, y)];
const _: [(); 48] = [(); std::mem::offset_of!(PlacementState, width)];
const _: [(); 52] = [(); std::mem::offset_of!(PlacementState, height)];
const _: [(); 56] = [(); std::mem::offset_of!(PlacementState, dpi)];
const _: [(); 60] = [(); std::mem::offset_of!(PlacementState, status)];
const _: [(); 64] = [(); std::mem::offset_of!(PlacementState, error)];
const _: [(); 68] = [(); std::mem::offset_of!(PlacementState, corrections)];

struct Mapping {
    _handle: OwnedHandle,
    view: MEMORY_MAPPED_VIEW_ADDRESS,
}

impl Mapping {
    fn create(name: &str, size: usize) -> Result<Self> {
        let handle = unsafe {
            CreateFileMappingW(
                INVALID_HANDLE_VALUE,
                null(),
                PAGE_READWRITE,
                0,
                size as u32,
                wide(name).as_ptr(),
            )
        };
        if handle.is_null() {
            return Err(failure("create native tray session"));
        }
        let handle = OwnedHandle(handle);
        if unsafe { GetLastError() } == ERROR_ALREADY_EXISTS {
            return Err("native tray session already exists".into());
        }
        Self::from_handle_sized(handle, size)
    }

    fn from_handle_sized(handle: OwnedHandle, size: usize) -> Result<Self> {
        let view = unsafe { MapViewOfFile(handle.0, FILE_MAP_ALL_ACCESS, 0, 0, size) };
        if view.Value.is_null() {
            return Err(failure("map tray style session"));
        }
        Ok(Self {
            _handle: handle,
            view,
        })
    }

    fn open(name: &str) -> Result<Option<Self>> {
        Self::open_sized(name, size_of::<State>())
    }

    fn open_sized(name: &str, size: usize) -> Result<Option<Self>> {
        let handle = unsafe { OpenFileMappingW(FILE_MAP_ALL_ACCESS, 0, wide(name).as_ptr()) };
        if handle.is_null() {
            return if unsafe { GetLastError() } == ERROR_FILE_NOT_FOUND {
                Ok(None)
            } else {
                Err(failure("open tray style session"))
            };
        }
        Self::from_handle_sized(OwnedHandle(handle), size).map(Some)
    }

    fn state(&self) -> &State {
        unsafe { &*self.view.Value.cast::<State>() }
    }

    fn placement(&self) -> &PlacementState {
        unsafe { &*self.view.Value.cast::<PlacementState>() }
    }
}

impl Drop for Mapping {
    fn drop(&mut self) {
        unsafe { UnmapViewOfFile(self.view) };
    }
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct RestoreTrayStyle {
    channel: String,
    host_pid: u32,
    host_created: u64,
    target: Identity,
}

impl RestoreTrayStyle {
    pub(super) fn validate(&self, journal: &Journal) -> Result<()> {
        let prefix = format!("Local\\SpiritAgentTrayStyle-{}-", journal.session);
        if self.host_pid != journal.host_pid
            || self.host_created != journal.host_created
            || !self.channel.starts_with(&prefix)
            || self.channel.len() != prefix.len() + 16
            || !self.channel[prefix.len()..]
                .bytes()
                .all(|value| value.is_ascii_hexdigit())
            || self.target.handle == 0
            || self.target.handle > usize::MAX as u64
            || self.target.pid == 0
            || self.target.created == 0
            || !["Shell_TrayWnd", "Shell_SecondaryTrayWnd"].contains(&self.target.class.as_str())
        {
            return Err("invalid tray style recovery owner".into());
        }
        Ok(())
    }

    fn matches(&self, state: &State) -> bool {
        state.magic == MAGIC
            && state.version == VERSION
            && state.host_pid == self.host_pid
            && state.host_created == self.host_created
            && state.explorer_pid == self.target.pid
            && state.taskbar == self.target.handle
            && state.reserved == 0
    }

    fn matches_placement(&self, state: &PlacementState) -> bool {
        state.magic == PLACEMENT_MAGIC
            && state.version == VERSION
            && state.host_pid == self.host_pid
            && state.host_created == self.host_created
            && state.explorer_pid == self.target.pid
            && state.taskbar == self.target.handle
    }

    fn placement_channel(&self) -> String {
        format!("{}-placement", self.channel)
    }

    fn explorer_alive(&self) -> bool {
        process(self.target.pid)
            .and_then(|owner| created(owner.0))
            .is_ok_and(|birth| birth == self.target.created)
            && is_explorer(self.target.pid)
    }

    pub(super) fn restore(&self) -> Result<()> {
        let mut failures = Vec::new();
        match Mapping::open_sized(&self.placement_channel(), size_of::<PlacementState>()) {
            Ok(Some(mapping)) => {
                let state = mapping.placement();
                if self.matches_placement(state) {
                    state.enabled.store(0, Ordering::Release);
                } else {
                    failures.push("tray placement recovery session changed".to_owned());
                }
            }
            Ok(None) => {}
            Err(error) => failures.push(error),
        }
        let restored = (|| {
            let Some(mapping) = Mapping::open(&self.channel)? else {
                return Ok(());
            };
            let state = mapping.state();
            if !self.matches(state) {
                return Err("tray style recovery session changed".into());
            }
            state.active.store(0, Ordering::Release);
            let deadline = Instant::now() + Duration::from_secs(2);
            loop {
                let status = state.status.load(Ordering::Acquire);
                if status == 2 || state.fill_count.load(Ordering::Acquire) == 0 || !self.explorer_alive() {
                    return Ok(());
                }
                if Instant::now() >= deadline {
                    return Err("系统托盘样式尚未恢复，恢复组件将继续重试。".into());
                }
                thread::sleep(Duration::from_millis(20));
            }
        })();
        if let Err(error) = restored {
            failures.push(error);
        }
        if failures.is_empty() {
            Ok(())
        } else {
            Err(failures.join("; "))
        }
    }
}

pub(super) struct TrayStyle {
    record: RestoreTrayStyle,
    mapping: Mapping,
    placement: Mapping,
    monitor: Bounds,
    dpi: u32,
    child: Option<Child>,
    started: Option<Instant>,
    ready: bool,
}

impl TrayStyle {
    pub(super) fn prepare(journal: &Journal, palette: Palette) -> Result<Option<Self>> {
        let Some(target) = journal.shell.first() else {
            return Ok(None);
        };
        if !target.valid() || !is_explorer(target.pid) {
            return Err("tray style requires a live Explorer taskbar".into());
        }
        let dpi = unsafe { GetDpiForWindow(target.window()) };
        if !(48..=960).contains(&dpi) {
            return Err("native tray DPI is unavailable".into());
        }
        component_path()?;
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|error| error.to_string())?
            .as_nanos() as u64;
        let record = RestoreTrayStyle {
            channel: format!("Local\\SpiritAgentTrayStyle-{}-{nonce:016x}", journal.session),
            host_pid: journal.host_pid,
            host_created: journal.host_created,
            target: target.clone(),
        };
        record.validate(journal)?;
        let mapping = Mapping::create(&record.channel, size_of::<State>())?;
        unsafe {
            mapping.view.Value.cast::<State>().write(State {
                magic: MAGIC,
                version: VERSION,
                host_pid: journal.host_pid,
                explorer_pid: target.pid,
                host_created: journal.host_created,
                taskbar: target.handle,
                heartbeat: AtomicU64::new(GetTickCount64()),
                active: AtomicI32::new(1),
                dark: AtomicI32::new(i32::from(palette == Palette::Night)),
                status: AtomicI32::new(0),
                error: AtomicI32::new(0),
                fill_count: AtomicU32::new(0),
                reserved: 0,
            });
        }
        let placement = Mapping::create(&record.placement_channel(), size_of::<PlacementState>())?;
        unsafe {
            placement.view.Value.cast::<PlacementState>().write(PlacementState {
                magic: PLACEMENT_MAGIC,
                version: VERSION,
                host_pid: journal.host_pid,
                explorer_pid: target.pid,
                host_created: journal.host_created,
                taskbar: target.handle,
                sequence: AtomicI32::new(0),
                enabled: AtomicI32::new(0),
                x: AtomicI32::new(0),
                y: AtomicI32::new(0),
                width: AtomicI32::new(0),
                height: AtomicI32::new(0),
                dpi: AtomicI32::new(0),
                status: AtomicI32::new(0),
                error: AtomicI32::new(0),
                corrections: AtomicU32::new(0),
            });
        }
        Ok(Some(Self {
            record,
            mapping,
            placement,
            monitor: journal.desktop_bounds,
            dpi,
            child: None,
            started: None,
            ready: false,
        }))
    }

    pub(super) fn record(&self) -> RestoreTrayStyle {
        self.record.clone()
    }

    pub(super) fn matches_target(&self, shell: &[Identity]) -> bool {
        shell.first().is_some_and(|target| target.same_window(&self.record.target))
    }

    pub(super) fn set_placement(&self, bounds: Option<Bounds>) -> Result<()> {
        let state = self.placement.placement();
        if !self.record.matches_placement(state) || !self.record.matches(self.mapping.state()) {
            return Err("native tray placement session changed".into());
        }
        let Some(bounds) = bounds else {
            state.enabled.store(0, Ordering::Release);
            return Ok(());
        };
        self.require_placement_environment()?;
        let dpi = unsafe { GetDpiForWindow(self.record.target.window()) };
        if !bounds.valid() || !(48..=960).contains(&dpi) || !self.record.target.valid()
            || self.mapping.state().active.load(Ordering::Acquire) != 1
        {
            return Err("native tray placement target changed".into());
        }
        if state.sequence.load(Ordering::Acquire) & 1 != 0 {
            return Err("native tray placement publication was interrupted".into());
        }
        if state.enabled.load(Ordering::Acquire) == 1
            && state.x.load(Ordering::Relaxed) == bounds.x
            && state.y.load(Ordering::Relaxed) == bounds.y
            && state.width.load(Ordering::Relaxed) == bounds.width
            && state.height.load(Ordering::Relaxed) == bounds.height
            && state.dpi.load(Ordering::Relaxed) == dpi as i32
        {
            return Ok(());
        }
        state.sequence.fetch_add(1, Ordering::AcqRel);
        state.x.store(bounds.x, Ordering::Relaxed);
        state.y.store(bounds.y, Ordering::Relaxed);
        state.width.store(bounds.width, Ordering::Relaxed);
        state.height.store(bounds.height, Ordering::Relaxed);
        state.dpi.store(dpi as i32, Ordering::Relaxed);
        state.enabled.store(1, Ordering::Relaxed);
        state.sequence.fetch_add(1, Ordering::Release);
        Ok(())
    }

    fn require_placement_environment(&self) -> Result<()> {
        if unsafe { GetDpiForWindow(self.record.target.window()) } != self.dpi
            || monitor_bounds(self.monitor).is_err()
        {
            self.placement.placement().enabled.store(0, Ordering::Release);
            return Err("系统托盘所在屏幕或缩放比例已变化，请重新进入桌面模式。".into());
        }
        Ok(())
    }

    pub(super) fn placement_ready(&self) -> bool {
        let state = self.mapping.state();
        let placement = self.placement.placement();
        self.record.matches(state)
            && self.record.matches_placement(placement)
            && self.require_placement_environment().is_ok()
            && state.active.load(Ordering::Acquire) == 1
            && state.status.load(Ordering::Acquire) == 1
            && placement.status.load(Ordering::Acquire) == 1
    }

    pub(super) fn begin(&mut self) -> Result<()> {
        if self.started.is_some() {
            return Err("native tray style component has already started".into());
        }
        let child = ProcessCommand::new(std::env::current_exe().map_err(|error| error.to_string())?)
            .args([
                "tray-style-attach",
                &self.record.target.pid.to_string(),
                &self.record.channel,
            ])
            .creation_flags(CREATE_NO_WINDOW)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|error| format!("start native tray style component: {error}"))?;
        self.child = Some(child);
        self.started = Some(Instant::now());
        Ok(())
    }

    pub(super) fn start(&mut self) -> Result<()> {
        self.begin()?;
        while !self.poll_ready()? {
            thread::sleep(Duration::from_millis(20));
        }
        Ok(())
    }

    fn poll_ready(&mut self) -> Result<bool> {
        let state = self.mapping.state();
        if !self.record.matches(state) {
            return Err("native tray style owner changed".into());
        }
        state
            .heartbeat
            .store(unsafe { GetTickCount64() }, Ordering::Release);
        let status = state.status.load(Ordering::Acquire);
        let placement = self.placement.placement();
        if !self.record.matches_placement(placement) {
            return Err("native tray placement owner changed".into());
        }
        let placement_status = placement.status.load(Ordering::Acquire);
        if status == 1 && placement_status == 1 {
            self.ready = true;
            return Ok(true);
        }
        if status < 0 {
            return Err(format!(
                "系统托盘透明样式未能启用（0x{:08x}）。",
                state.error.load(Ordering::Acquire) as u32,
            ));
        }
        if placement_status < 0 {
            return Err(format!(
                "系统托盘位置同步未能启用（0x{:08x}）。",
                placement.error.load(Ordering::Acquire) as u32,
            ));
        }
        if self.ready || ![0, 1].contains(&status) || ![0, 1].contains(&placement_status) {
            return Err("系统托盘透明样式已失效，已恢复窗口模式。".into());
        }
        let started = self
            .started
            .ok_or("native tray style component has not started")?;
        let stopped = self
            .child
            .as_mut()
            .map(Child::try_wait)
            .transpose()
            .map_err(|error| format!("query native tray style component: {error}"))?
            .flatten()
            .is_some();
        if stopped || started.elapsed() >= STYLE_TIMEOUT {
            return Err("系统托盘透明组件未就绪，已恢复窗口模式。".into());
        }
        Ok(false)
    }

    pub(super) fn heartbeat(&mut self, palette: Palette) -> Result<()> {
        if !self.record.target.valid() {
            return Ok(());
        }
        self.require_placement_environment()?;
        let state = self.mapping.state();
        state
            .dark
            .store(i32::from(palette == Palette::Night), Ordering::Release);
        self.poll_ready()?;
        Ok(())
    }
}

impl Drop for TrayStyle {
    fn drop(&mut self) {
        self.placement.placement().enabled.store(0, Ordering::Release);
        let state = self.mapping.state();
        state.active.store(0, Ordering::Release);
        if state.status.load(Ordering::Acquire) == 2
            || state.fill_count.load(Ordering::Acquire) == 0
            || !self.record.explorer_alive()
        {
            if let Some(child) = &mut self.child {
                let _ = child.kill();
                let _ = child.try_wait();
            }
        }
        // 仍有样式待恢复时保留连接；子进程观察 active 后有界退出。
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    version: u32,
    file: String,
}

fn read_component(path: &Path) -> Result<Option<Vec<u8>>> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("read native tray style component metadata: {error}")),
    };
    if !metadata.is_file()
        || metadata.file_type().is_symlink()
        || metadata.len() == 0
        || metadata.len() > MAX_COMPONENT_BYTES
    {
        return Err("native tray style component is not a bounded regular file".into());
    }
    read_state(path, MAX_COMPONENT_BYTES)
}

fn local_cache() -> Result<PathBuf> {
    let raw = unsafe { SHGetKnownFolderPath(&FOLDERID_LocalAppData, KF_FLAG_DEFAULT, None) }
        .map_err(|error| format!("resolve native tray style cache: {error}"))?;
    let value = unsafe { raw.to_string() };
    unsafe { CoTaskMemFree(Some(raw.0.cast())) };
    let path = PathBuf::from(value.map_err(|error| error.to_string())?);
    if !path.is_absolute() {
        return Err("native tray style cache requires an absolute local profile path".into());
    }
    Ok(path.join("SpiritAgent").join("native").join("tray-style"))
}

fn cache_component(file: &str, bytes: &[u8]) -> Result<PathBuf> {
    let mut fingerprint = DefaultHasher::new();
    bytes.hash(&mut fingerprint);
    let stem = Path::new(file)
        .file_stem()
        .and_then(|value| value.to_str())
        .ok_or("invalid native tray style cache filename")?;
    let directory = local_cache()?.join(format!("{stem}-{:016x}", fingerprint.finish()));
    let library = directory.join(file);
    if let Some(cached) = read_component(&library)? {
        if cached != bytes {
            return Err("native tray style cache differs from the selected component".into());
        }
        return Ok(library);
    }
    fs::create_dir_all(&directory)
        .map_err(|error| format!("create native tray style cache: {error}"))?;
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| error.to_string())?
        .as_nanos();
    let temporary = directory.join(format!(".{file}.{}-{nonce:x}.tmp", unsafe {
        GetCurrentProcessId()
    }));
    let mut pending = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)
        .map_err(|error| format!("create native tray style cache temporary file: {error}"))?;
    let published = (|| {
        pending
            .write_all(bytes)
            .map_err(|error| format!("write native tray style cache: {error}"))?;
        pending
            .sync_all()
            .map_err(|error| format!("sync native tray style cache: {error}"))?;
        drop(pending);
        let source = wide(&temporary.to_string_lossy());
        let destination = wide(&library.to_string_lossy());
        // 不替换已映射的模块；竞争发布者只能复用字节完全相同的成品。
        let moved = unsafe {
            MoveFileExW(
                PCWSTR(source.as_ptr()),
                PCWSTR(destination.as_ptr()),
                MOVE_FILE_FLAGS(0),
            )
        };
        let cached = read_component(&library)?;
        if cached.as_deref() != Some(bytes) {
            return Err(match moved {
                Ok(()) => "native tray style cache differs after publication".into(),
                Err(error) => format!("publish native tray style cache: {error}"),
            });
        }
        Ok(library)
    })();
    let cleanup = fs::remove_file(&temporary);
    if let Err(error) = cleanup {
        if error.kind() != std::io::ErrorKind::NotFound && published.is_ok() {
            return Err(format!("remove native tray style cache temporary file: {error}"));
        }
    }
    published
}

fn component_path() -> Result<PathBuf> {
    let executable = std::env::current_exe().map_err(|error| error.to_string())?;
    let folder = executable
        .parent()
        .ok_or("tray style component folder is missing")?;
    let bytes = read_state(&folder.join("desktop-tray-style.json"), 4096)?
        .ok_or("系统托盘透明组件缺失，请重新构建或安装客户端。")?;
    let manifest: Manifest = serde_json::from_slice(&bytes).map_err(|error| error.to_string())?;
    let hash = manifest
        .file
        .strip_prefix("desktop-tray-style-")
        .and_then(|name| name.strip_suffix(".dll"))
        .ok_or("invalid tray style filename")?;
    if manifest.version != VERSION
        || hash.len() != 32
        || !hash
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err("invalid tray style manifest".into());
    }
    let library = folder.join(&manifest.file);
    let component = read_component(&library)?
        .ok_or("系统托盘透明组件缺失，请重新构建或安装客户端。")?;
    cache_component(&manifest.file, &component)
}

pub(super) fn attach(explorer_pid: u32, channel: &str) -> Result<()> {
    if channel.len() > 220 || !channel.starts_with("Local\\SpiritAgentTrayStyle-") {
        return Err("invalid tray style channel".into());
    }
    let mapping = Mapping::open(channel)?.ok_or("tray style session is missing")?;
    let state = mapping.state();
    if state.magic != MAGIC
        || state.version != VERSION
        || state.explorer_pid != explorer_pid
        || state.host_pid != actual_parent()?
        || !is_explorer(explorer_pid)
        || !process(state.host_pid)
            .and_then(|owner| created(owner.0))
            .is_ok_and(|birth| birth == state.host_created)
    {
        return Err("tray style process ownership changed".into());
    }
    let library = component_path()?;
    let module = unsafe {
        LoadLibraryExW(
            wide(&library.to_string_lossy()).as_ptr(),
            null_mut(),
            LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS,
        )
    };
    if module.is_null() {
        return Err(failure("load native tray style component"));
    }
    let address = unsafe { GetProcAddress(module, c"SpiritAgentAttach".as_ptr().cast()) }
        .ok_or("native tray style entry is missing")?;
    let initialize: unsafe extern "system" fn(u32, *const u16) -> i32 =
        unsafe { std::mem::transmute(address) };
    let result = unsafe { initialize(explorer_pid, wide(channel).as_ptr()) };
    if result < 0 {
        state.error.store(result, Ordering::Release);
        state.status.store(-1, Ordering::Release);
        return Err(format!(
            "native tray style initialization failed: 0x{:08x}",
            result as u32,
        ));
    }
    // 保留诊断连接，直到 Explorer 确认还原或宿主退出。
    let mut restoring_since = None;
    loop {
        if state.status.load(Ordering::Acquire) == 2 {
            return Ok(());
        }
        let owner_alive = process(state.host_pid)
            .and_then(|owner| created(owner.0))
            .is_ok_and(|birth| birth == state.host_created);
        if !owner_alive || state.active.load(Ordering::Acquire) == 0 {
            if restoring_since.get_or_insert_with(Instant::now).elapsed() > Duration::from_secs(3)
            {
                return Ok(());
            }
        }
        thread::sleep(Duration::from_millis(100));
    }
}
