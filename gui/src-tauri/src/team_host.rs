use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use serde_json::{json, Value};
use tauri::ipc::Channel;
use tauri::{Manager, State};

struct Session {
    child: Arc<Mutex<Child>>,
    input: ChildStdin,
}

#[derive(Default)]
pub struct TeamHost {
    sessions: Mutex<HashMap<u64, Session>>,
    next: AtomicU64,
}

fn runtime_root(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let mut candidates = Vec::new();
    if let Some(root) = std::env::var_os("CCX_SOURCE_ROOT").filter(|value| !value.is_empty()) {
        candidates.push(PathBuf::from(root));
    }
    if let Ok(resources) = app.path().resource_dir() {
        candidates.push(resources.join("runtime"));
    }
    candidates.extend(
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .map(Path::to_path_buf),
    );
    candidates.into_iter().find(|p| p.join("lib/gui_team.py").is_file())
        .ok_or_else(|| "Team runtime not found. Rebuild the desktop app or set CCX_SOURCE_ROOT to the CCX checkout.".into())
}

fn python(root: &Path) -> Command {
    let bin = std::env::var("CCX_PYTHON").unwrap_or_else(|_| {
        if cfg!(windows) {
            "python".into()
        } else {
            "python3".into()
        }
    });
    let mut command = Command::new(bin);
    command
        .args(["-X", "utf8", "-u"])
        .arg(root.join("lib/gui_team.py"));
    command.env("CCX_HOME", ccx_core::config::ccx_home());
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    command
}

fn write_message(input: &mut ChildStdin, value: &Value) -> Result<(), String> {
    serde_json::to_writer(&mut *input, value).map_err(|e| e.to_string())?;
    input
        .write_all(b"\n")
        .and_then(|_| input.flush())
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn team_request(
    app: tauri::AppHandle,
    action: String,
    data: Value,
) -> Result<Value, String> {
    let root = runtime_root(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        let mut child = python(&root)
            .args(["--request", &action])
            .spawn()
            .map_err(|e| format!("Cannot start Python 3.10+: {e}"))?;
        write_message(
            &mut child.stdin.take().ok_or("Missing runtime input")?,
            &data,
        )?;
        let result = child.wait_with_output().map_err(|e| e.to_string())?;
        let response: Value = String::from_utf8_lossy(&result.stdout)
            .lines()
            .rev()
            .find_map(|line| serde_json::from_str::<Value>(line).ok())
            .ok_or_else(|| {
                format!(
                    "Team runtime failed: {}",
                    String::from_utf8_lossy(&result.stderr)
                )
            })?;
        if response["ok"] == true {
            Ok(response["value"].clone())
        } else {
            Err(response["error"]
                .as_str()
                .unwrap_or("Team request failed")
                .to_string())
        }
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
pub fn start_team_session(
    app: tauri::AppHandle,
    host: State<'_, TeamHost>,
    data: Value,
    events: Channel<Value>,
) -> Result<u64, String> {
    let root = runtime_root(&app)?;
    let mut sessions = host.sessions.lock().map_err(|_| "Session lock failed")?;
    sessions.retain(|_, session| {
        session
            .child
            .lock()
            .ok()
            .is_some_and(|mut c| c.try_wait().ok().flatten().is_none())
    });
    if !sessions.is_empty() {
        return Err("Close the current Team session before opening another.".into());
    }
    let mut child = python(&root)
        .arg("--session")
        .spawn()
        .map_err(|e| e.to_string())?;
    let mut input = child.stdin.take().ok_or("Missing session input")?;
    write_message(&mut input, &data)?;
    let stdout = child.stdout.take().ok_or("Missing session output")?;
    let stderr = child.stderr.take().ok_or("Missing session errors")?;
    let errors = events.clone();
    std::thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            let _ = errors.send(json!({"kind": "log", "text": line}));
        }
    });
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            let value = serde_json::from_str(&line)
                .unwrap_or_else(|_| json!({"kind": "log", "text": line}));
            let _ = events.send(value);
        }
        let _ = events.send(json!({"kind": "disconnected"}));
    });
    let id = host.next.fetch_add(1, Ordering::Relaxed) + 1;
    sessions.insert(
        id,
        Session {
            child: Arc::new(Mutex::new(child)),
            input,
        },
    );
    Ok(id)
}

#[tauri::command]
pub fn send_team_input(host: State<'_, TeamHost>, id: u64, message: Value) -> Result<(), String> {
    let mut sessions = host.sessions.lock().map_err(|_| "Session lock failed")?;
    let session = sessions.get_mut(&id).ok_or("Session has closed")?;
    write_message(&mut session.input, &message)
}

fn kill_tree(child: &Arc<Mutex<Child>>) {
    if let Ok(mut child) = child.lock() {
        if child.try_wait().ok().flatten().is_some() {
            return;
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            let _ = Command::new("taskkill.exe")
                .args(["/PID", &child.id().to_string(), "/T", "/F"])
                .creation_flags(0x08000000)
                .output();
        }
        let _ = child.kill();
        let _ = child.wait();
    }
}

#[tauri::command]
pub fn close_team_session(host: State<'_, TeamHost>, id: u64) -> Result<(), String> {
    if let Some(mut session) = host
        .sessions
        .lock()
        .map_err(|_| "Session lock failed")?
        .remove(&id)
    {
        let _ = write_message(&mut session.input, &json!({"type": "close"}));
        std::thread::spawn(move || {
            for _ in 0..50 {
                if session
                    .child
                    .lock()
                    .ok()
                    .is_some_and(|mut c| c.try_wait().ok().flatten().is_some())
                {
                    return;
                }
                std::thread::sleep(std::time::Duration::from_millis(100));
            }
            kill_tree(&session.child);
        });
    }
    Ok(())
}

impl TeamHost {
    pub fn shutdown(&self) {
        if let Ok(mut sessions) = self.sessions.lock() {
            for (_, mut session) in sessions.drain() {
                let _ = write_message(&mut session.input, &json!({"type": "close"}));
                kill_tree(&session.child);
            }
        }
    }
}

#[tauri::command]
pub async fn pick_team_project(app: tauri::AppHandle) -> Result<Option<String>, String> {
    use tauri_plugin_dialog::DialogExt;
    tauri::async_runtime::spawn_blocking(move || {
        app.dialog()
            .file()
            .set_title("Choose Team project")
            .blocking_pick_folder()
            .map(|p| {
                p.into_path()
                    .map(|p| p.to_string_lossy().into_owned())
                    .map_err(|e| e.to_string())
            })
            .transpose()
    })
    .await
    .map_err(|e| e.to_string())?
}
