#[cfg(windows)]
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::io::{self, Write};
#[cfg(windows)]
use std::io::{BufRead, Read};
use std::path::PathBuf;
#[cfg(windows)]
use std::sync::mpsc::{self, Receiver};
#[cfg(windows)]
use std::thread;

#[cfg(windows)]
mod windows;

type Result<T> = std::result::Result<T, String>;

#[cfg(windows)]
#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Bounds {
    x: i32,
    y: i32,
    width: i32,
    height: i32,
}

#[cfg(windows)]
impl Bounds {
    fn within_tolerance(self, other: Self, tolerance: u32) -> bool {
        self.x.abs_diff(other.x) <= tolerance
            && self.y.abs_diff(other.y) <= tolerance
            && self.width.abs_diff(other.width) <= tolerance
            && self.height.abs_diff(other.height) <= tolerance
    }

    fn valid(&self) -> bool {
        self.width > 0
            && self.height > 0
            && self.width <= 65_536
            && self.height <= 65_536
            && self.x.unsigned_abs() <= 1_000_000
            && self.y.unsigned_abs() <= 1_000_000
    }
}

#[cfg(windows)]
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct WindowSpec {
    handle: String,
    bounds: Bounds,
    role: WindowRole,
}

#[cfg(windows)]
#[derive(Clone, Copy, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum WindowRole {
    #[default]
    Background,
    Overlay,
    Companion,
}

#[cfg(windows)]
impl WindowRole {
    fn is_top_level(self, companion_always_on_top: bool) -> bool {
        self == Self::Overlay || (self == Self::Companion && companion_always_on_top)
    }
}

#[cfg(windows)]
#[derive(Deserialize)]
#[serde(tag = "command", rename_all = "snake_case", deny_unknown_fields)]
enum Command {
    Start {
        id: u32,
        parent_pid: u32,
        takeover: bool,
        windows: Vec<WindowSpec>,
        work_area: Bounds,
        companion_always_on_top: bool,
    },
    Heartbeat {
        id: u32,
    },
    Focus {
        id: u32,
        handle: String,
    },
    RefreshApplications {
        id: u32,
    },
    ActivateExternal {
        id: u32,
        window_id: String,
    },
    CloseExternal {
        id: u32,
        window_ids: Vec<String>,
    },
    CompanionLayer {
        id: u32,
        always_on_top: bool,
    },
    Stop {
        id: u32,
    },
}

#[cfg(windows)]
impl Command {
    fn id(&self) -> u32 {
        match self {
            Self::Start { id, .. }
            | Self::Heartbeat { id }
            | Self::Focus { id, .. }
            | Self::RefreshApplications { id }
            | Self::ActivateExternal { id, .. }
            | Self::CloseExternal { id, .. }
            | Self::CompanionLayer { id, .. }
            | Self::Stop { id } => *id,
        }
    }
}

fn emit(value: serde_json::Value) {
    let stdout = io::stdout();
    let mut output = stdout.lock();
    let _ = writeln!(output, "{value}");
    let _ = output.flush();
}

#[cfg(windows)]
fn commands() -> Receiver<Result<Command>> {
    let (sender, receiver) = mpsc::sync_channel(16);
    thread::spawn(move || {
        let stdin = io::stdin();
        let mut input = stdin.lock();
        loop {
            let mut bytes = Vec::new();
            let count = match input.by_ref().take(65_537).read_until(b'\n', &mut bytes) {
                Ok(count) => count,
                Err(error) => {
                    let _ = sender.send(Err(format!("stdin: {error}")));
                    break;
                }
            };
            if count == 0 {
                break;
            }
            if bytes.len() > 65_536 || bytes.last() != Some(&b'\n') {
                let _ = sender.send(Err("command exceeds 64 KiB or is incomplete".into()));
                break;
            }
            let parsed =
                serde_json::from_slice(&bytes).map_err(|error| format!("invalid command: {error}"));
            if sender.send(parsed).is_err() {
                break;
            }
        }
    });
    receiver
}

fn run() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mode = args
        .first()
        .ok_or("missing check-runtime, host, guardian, or recover mode")?;
    if mode == "check-runtime" && args.len() == 1 {
        #[cfg(windows)]
        {
            emit(json!({
                "event": "runtime_ready",
                "arch": std::env::consts::ARCH,
                "version": env!("CARGO_PKG_VERSION"),
            }));
            return Ok(());
        }
        #[cfg(not(windows))]
        return Err("Explorer desktop hosting is available on Windows only".into());
    }
    if args.get(1).map(String::as_str) != Some("--journal") {
        return Err("expected --journal followed by an absolute path".into());
    }
    let journal = PathBuf::from(args.get(2).ok_or("missing journal path")?);
    if !journal.is_absolute() {
        return Err("journal path must be absolute".into());
    }
    #[cfg(windows)]
    {
        match mode.as_str() {
            "host" if args.len() == 3 => windows::host(&journal),
            "recover" if args.len() == 3 => windows::recover(&journal),
            "guardian" if args.len() == 5 && args[3] == "--session" => {
                windows::guardian(&journal, &args[4])
            }
            _ => Err("invalid helper arguments".into()),
        }
    }
    #[cfg(not(windows))]
    {
        let _ = (mode, journal);
        Err("Explorer desktop hosting is available on Windows only".into())
    }
}

fn main() {
    if let Err(reason) = run() {
        emit(json!({ "event": "failure", "reason": reason }));
        std::process::exit(1);
    }
}
