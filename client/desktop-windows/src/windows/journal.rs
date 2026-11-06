use super::identity::{Identity, actual_parent, created, process_image};
use super::*;

pub(crate) struct RestoringSignal {
    pub(crate) handle: OwnedHandle,
    pub(crate) session: String,
    pub(crate) host_pid: u32,
    pub(crate) host_created: u64,
}

impl RestoringSignal {
    pub(crate) fn new(journal: &Journal) -> Result<Self> {
        let mut scope = DefaultHasher::new();
        journal.session.hash(&mut scope);
        journal.host_pid.hash(&mut scope);
        journal.host_created.hash(&mut scope);
        let name = format!("Local\\SpiritAgentDesktopRestoring-{:016x}", scope.finish());
        let handle = unsafe { CreateEventW(null(), 1, 0, wide(&name).as_ptr()) };
        if handle.is_null() {
            return Err(failure("CreateEventW desktop restoration"));
        }
        Ok(Self {
            handle: OwnedHandle(handle),
            session: journal.session.clone(),
            host_pid: journal.host_pid,
            host_created: journal.host_created,
        })
    }

    pub(crate) fn requested(&self) -> Result<bool> {
        match unsafe { WaitForSingleObject(self.handle.0, 0) } {
            WAIT_OBJECT_0 => Ok(true),
            WAIT_TIMEOUT => Ok(false),
            _ => Err(failure("desktop restoration state")),
        }
    }

    pub(crate) fn request(&self) -> Result<bool> {
        let previous = self.requested()?;
        if unsafe { SetEvent(self.handle.0) } == 0 {
            return Err(failure("signal desktop restoration"));
        }
        Ok(previous)
    }

    pub(crate) fn matches(&self, journal: &Journal) -> bool {
        journal.session == self.session
            && journal.host_pid == self.host_pid
            && journal.host_created == self.host_created
    }
}

pub(crate) fn stop_matching_host(journal: &Journal) -> Result<()> {
    if unsafe { GetCurrentProcessId() } == journal.host_pid {
        // host 的异常路径在恢复尝试后退出，不会返回命令循环。
        return Ok(());
    }
    if actual_parent()? != journal.host_pid {
        return Err("restoration cannot stop a process outside its guardian parent".into());
    }
    let host = unsafe {
        OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE | PROCESS_TERMINATE,
            0,
            journal.host_pid,
        )
    };
    if host.is_null() {
        return Err(failure("open restoration host"));
    }
    let host = OwnedHandle(host);
    match unsafe { WaitForSingleObject(host.0, 0) } {
        WAIT_OBJECT_0 => return Ok(()),
        WAIT_TIMEOUT => {}
        _ => return Err(failure("query restoration host state")),
    }
    if created(host.0)? != journal.host_created {
        return Err("restoration host process identity changed".into());
    }
    let image = fs::canonicalize(process_image(host.0)?)
        .map_err(|error| format!("resolve restoration host executable: {error}"))?;
    let own_image = fs::canonicalize(std::env::current_exe().map_err(|error| error.to_string())?)
        .map_err(|error| format!("resolve guardian executable: {error}"))?;
    if !image
        .to_string_lossy()
        .eq_ignore_ascii_case(&own_image.to_string_lossy())
    {
        return Err("restoration host executable does not match guardian".into());
    }
    if unsafe { TerminateProcess(host.0, 1) } == 0 {
        return Err(failure("stop restoration host"));
    }
    if unsafe { WaitForSingleObject(host.0, 1_000) } != WAIT_OBJECT_0 {
        return Err("restoration host did not stop".into());
    }
    Ok(())
}


#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct RestoreWindow {
    pub(crate) identity: Identity,
    pub(crate) parent: u64,
    pub(crate) style: i64,
    pub(crate) ex_style: i64,
    pub(crate) bounds: Bounds,
    pub(crate) role: WindowRole,
    // 旧 journal 未接管该属性，恢复时不改写未知原值。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) non_rude: Option<u64>,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum JournalPhase {
    Prepared,
    Attaching,
    Active,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Journal {
    pub(crate) phase: JournalPhase,
    pub(crate) session: String,
    pub(crate) parent_pid: u32,
    pub(crate) parent_created: u64,
    pub(crate) host_pid: u32,
    pub(crate) host_created: u64,
    pub(crate) shell: Vec<Identity>,
    pub(crate) windows: Vec<RestoreWindow>,
    pub(in crate::windows) appbar: Option<RestoreAppBar>,
    pub(crate) desktop_bounds: Bounds,
    pub(in crate::windows) regions: Vec<SavedRegion>,
    pub(in crate::windows) tray_style: Option<RestoreTrayStyle>,
    pub(in crate::windows) tray_placements: Vec<RestoreTrayPlacement>,
}

pub(crate) fn sidecar(path: &Path, suffix: &str) -> PathBuf {
    let mut name = path.as_os_str().to_os_string();
    name.push(suffix);
    PathBuf::from(name)
}

pub(crate) fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let pending = sidecar(
        path,
        &format!(".{}.pending", unsafe { GetCurrentProcessId() }),
    );
    let result = (|| {
        let mut file =
            File::create(&pending).map_err(|error| format!("create journal: {error}"))?;
        file.write_all(bytes)
            .map_err(|error| format!("write journal: {error}"))?;
        file.sync_all()
            .map_err(|error| format!("sync journal: {error}"))?;
        fs::rename(&pending, path).map_err(|error| format!("replace journal: {error}"))
    })();
    if result.is_err() {
        let _ = fs::remove_file(pending);
    }
    result
}

pub(crate) fn read_state(path: &Path, limit: u64) -> Result<Option<Vec<u8>>> {
    let file = match File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("open desktop state: {error}")),
    };
    let metadata = file
        .metadata()
        .map_err(|error| format!("desktop state metadata: {error}"))?;
    if !metadata.is_file() || metadata.len() > limit {
        return Err("desktop state is not a bounded regular file".into());
    }
    let mut bytes = Vec::new();
    file.take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| format!("read desktop state: {error}"))?;
    if bytes.len() as u64 > limit {
        return Err("desktop state exceeds its byte limit".into());
    }
    Ok(Some(bytes))
}

pub(crate) fn read_lease(path: &Path) -> Result<String> {
    let bytes = read_state(&sidecar(path, ".lease"), 256)?.ok_or("desktop lease is missing")?;
    String::from_utf8(bytes).map_err(|_| "desktop lease is not UTF-8".into())
}

pub(crate) fn read_journal(path: &Path) -> Result<Option<Journal>> {
    let Some(bytes) = read_state(path, MAX_JOURNAL_BYTES)? else {
        return Ok(None);
    };
    let journal: Journal = serde_json::from_slice(&bytes)
        .map_err(|error| format!("invalid desktop journal: {error}"))?;
    validate_journal(&journal)?;
    Ok(Some(journal))
}

pub(crate) fn validate_journal(journal: &Journal) -> Result<()> {
    if journal.tray_placements.len() > journal.shell.len() {
        return Err("invalid system tray recovery placement count".into());
    }
    let mut placement_handles = std::collections::HashSet::new();
    for saved in &journal.tray_placements {
        saved.validate(journal)?;
        if !placement_handles.insert(saved.identity.handle) {
            return Err("duplicate system tray recovery placement".into());
        }
    }
    if let Some(style) = &journal.tray_style {
        style.validate(journal)?;
    }
    if journal.windows.is_empty()
        || journal.windows.len() > 32
        || journal.shell.len() > 128
        || journal.regions.len() > 160
    {
        return Err("invalid desktop journal window count".into());
    }
    let mut handles = std::collections::HashSet::new();
    let mut region_handles = std::collections::HashSet::new();
    for saved in &journal.regions {
        saved.validate()?;
        if !region_handles.insert(saved.identity.handle)
            || !journal
                .shell
                .iter()
                .chain(journal.windows.iter().map(|window| &window.identity))
                .any(|identity| identity.same_window(&saved.identity))
        {
            return Err("invalid recovery region ownership".into());
        }
    }
    if let Some(appbar) = &journal.appbar {
        appbar.validate(journal.host_pid, journal.host_created, journal.desktop_bounds)?;
    }
    if !journal.desktop_bounds.valid()
        || journal.shell.iter().any(|entry| {
            !["Shell_TrayWnd", "Shell_SecondaryTrayWnd"].contains(&entry.class.as_str())
        })
    {
        return Err("invalid display-scoped desktop journal".into());
    }
    if journal.session.is_empty()
        || journal.session.len() > 128
        || journal.windows.iter().any(|entry| {
            entry.parent != 0
                || entry.identity.handle == 0
                || !handles.insert(entry.identity.handle)
                || entry.identity.class.is_empty()
                || entry.identity.class.encode_utf16().count() > 255
                || entry.identity.pid != journal.parent_pid
                || entry.identity.created != journal.parent_created
                || !entry.bounds.valid()
                || entry.non_rude.is_some_and(|value| usize::try_from(value).is_err())
                || !(-2_147_483_648..=4_294_967_295).contains(&entry.style)
                || !(-2_147_483_648..=4_294_967_295).contains(&entry.ex_style)
        })
    {
        return Err("invalid desktop journal window ownership".into());
    }
    Ok(())
}

impl Journal {
    pub(crate) fn capture_shell(&mut self, identity: Identity) -> Result<()> {
        let placement = RestoreTrayPlacement::capture(&identity, self.desktop_bounds)?;
        let region = SavedRegion::capture(&identity)?;
        self.tray_placements.push(placement);
        self.regions.push(region);
        self.shell.push(identity);
        Ok(())
    }

    pub(in crate::windows) fn restore_region(&self, saved: &SavedRegion) -> Result<()> {
        match self.tray_placements.iter().find(|placement| {
            placement.identity.same_window(&saved.identity)
        }) {
            Some(placement) => saved.restore_resized(placement.bounds),
            None => saved.restore(),
        }
    }

    pub(crate) fn save(&self, path: &Path) -> Result<()> {
        validate_journal(self)?;
        let bytes = serde_json::to_vec(self).map_err(|error| error.to_string())?;
        if bytes.len() as u64 > MAX_JOURNAL_BYTES {
            return Err("desktop recovery journal exceeds byte budget".into());
        }
        atomic_write(path, &bytes)
    }
}

pub(crate) fn require_session(path: &Path, session: &str, restoring: &RestoringSignal) -> Result<()> {
    if restoring.requested()? {
        return Err("desktop session is restoring".into());
    }
    if read_journal(path)?
        .is_some_and(|journal| journal.session == session && restoring.matches(&journal))
    {
        Ok(())
    } else {
        Err("desktop session is missing or changed".into())
    }
}
