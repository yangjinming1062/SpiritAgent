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
#[derive(Clone, Copy, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Bounds {
    x: i32,
    y: i32,
    width: i32,
    height: i32,
}

#[cfg(windows)]
impl Bounds {
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
    },
    Heartbeat {
        id: u32,
    },
    Stop {
        id: u32,
    },
}

#[cfg(windows)]
impl Command {
    fn id(&self) -> u32 {
        match self {
            Self::Start { id, .. } | Self::Heartbeat { id } | Self::Stop { id } => *id,
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
        .ok_or("missing host, guardian, or recover mode")?;
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
