//! Process bridge to `saarathi bridge --stdio` (ADR-0013).
//!
//! The UI calls `bridge_request`; this module forwards allowlisted JSON-RPC methods to the Python bridge over its
//! stdin, matches responses by id, and re-emits notifications as `bridge://notification` events. No network.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{Value, json};
use tauri::{AppHandle, Emitter};
use tokio::sync::oneshot;

/// Methods the UI may call. Anything else is refused before it reaches the engine.
pub const ALLOWED_METHODS: &[&str] = &[
    "system.initialize",
    "system.doctor",
    "projects.open",
    "projects.init",
    "sessions.list",
    "sessions.create",
    "sessions.rename",
    "sessions.archive",
    "sessions.restore",
    "sessions.get",
    "sessions.send",
    "runs.status",
    "runs.events",
    "runs.approve",
    "runs.answer",
    "runs.stop",
    "runs.cancel",
    "runs.resume",
    "runs.timeline",
    "tasks.diff",
    "tasks.logs",
    "tasks.inspect",
    "tasks.file",
    "models.list",
    "providers.status",
    "subscribe",
    "unsubscribe",
];
const REQUEST_TIMEOUT: Duration = Duration::from_secs(120);

type Pending = Arc<Mutex<HashMap<u64, oneshot::Sender<Value>>>>;

struct Running {
    child: Child,
    stdin: ChildStdin,
}

pub struct BridgeState {
    running: Mutex<Option<Running>>,
    pending: Pending,
    next_id: AtomicU64,
    stderr_tail: Arc<Mutex<Vec<String>>>,
}

impl Default for BridgeState {
    fn default() -> Self {
        Self {
            running: Mutex::new(None),
            pending: Arc::new(Mutex::new(HashMap::new())),
            next_id: AtomicU64::new(1),
            stderr_tail: Arc::new(Mutex::new(Vec::new())),
        }
    }
}

/// The command used to start the bridge: `SAARATHI_BRIDGE` (a path to a `saarathi` executable) or `saarathi`.
fn bridge_command() -> (String, Vec<String>) {
    let program = std::env::var("SAARATHI_BRIDGE").unwrap_or_else(|_| "saarathi".to_string());
    (program, vec!["bridge".into(), "--stdio".into()])
}

/// GUI apps on macOS and Linux do not inherit the login shell's PATH, so provider CLIs installed through
/// version managers (fnm, nvm) or ~/.local/bin would be invisible to the bridge and its drivers.
fn login_shell_path() -> Option<String> {
    if cfg!(windows) {
        return None;
    }
    let shell = std::env::var("SHELL").ok()?;
    let out = Command::new(shell)
        .args(["-ilc", "printf '%s' \"$PATH\""])
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    let path = String::from_utf8(out.stdout).ok()?;
    let path = path.trim().to_string();
    (!path.is_empty()).then_some(path)
}

impl BridgeState {
    fn spawn(&self, app: &AppHandle) -> Result<(), String> {
        let (program, args) = bridge_command();
        let mut cmd = Command::new(&program);
        cmd.args(&args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        if let Some(path) = login_shell_path() {
            cmd.env("PATH", path);
        }
        let mut child = cmd
            .spawn()
            .map_err(|e| format!("could not start `{program} bridge --stdio`: {e}. Install saarathi or set SAARATHI_BRIDGE."))?;
        let stdin = child.stdin.take().ok_or("bridge stdin unavailable")?;
        let stdout = child.stdout.take().ok_or("bridge stdout unavailable")?;
        let stderr = child.stderr.take().ok_or("bridge stderr unavailable")?;

        let pending = self.pending.clone();
        let app_out = app.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                let Ok(msg) = serde_json::from_str::<Value>(&line) else {
                    continue;
                };
                let is_response = msg.get("id").is_some() && msg.get("method").is_none();
                if is_response {
                    if let Some(id) = msg.get("id").and_then(Value::as_u64)
                        && let Some(tx) = pending.lock().unwrap().remove(&id)
                    {
                        let _ = tx.send(msg);
                    }
                } else {
                    let _ = app_out.emit("bridge://notification", msg);
                }
            }
            // stdout closed: the bridge exited. Fail every waiting request and tell the UI.
            for (_, tx) in pending.lock().unwrap().drain() {
                let _ = tx.send(
                    json!({"error": {"code": -32603, "message": "the bridge exited",
                                                  "data": {"kind": "bridge_exited"}}}),
                );
            }
            let _ = app_out.emit("bridge://status", json!({"state": "exited"}));
        });
        let tail = self.stderr_tail.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                let mut t = tail.lock().unwrap();
                t.push(line);
                let excess = t.len().saturating_sub(200);
                t.drain(..excess);
            }
        });
        *self.running.lock().unwrap() = Some(Running { child, stdin });
        Ok(())
    }

    fn alive(&self) -> bool {
        let mut guard = self.running.lock().unwrap();
        match guard.as_mut() {
            Some(r) => matches!(r.child.try_wait(), Ok(None)),
            None => false,
        }
    }

    pub async fn request(
        &self,
        app: &AppHandle,
        method: &str,
        params: Value,
    ) -> Result<Value, Value> {
        if !ALLOWED_METHODS.contains(&method) {
            return Err(
                json!({"code": -32601, "message": format!("method {method} is not allowed"),
                              "data": {"kind": "not_allowed"}}),
            );
        }
        if !self.alive() {
            self.spawn(app).map_err(
                |e| json!({"code": -32603, "message": e, "data": {"kind": "bridge_unavailable"}}),
            )?;
            let _ = app.emit("bridge://status", json!({"state": "started"}));
        }
        let id = self.next_id.fetch_add(1, Ordering::SeqCst);
        let (tx, rx) = oneshot::channel();
        self.pending.lock().unwrap().insert(id, tx);
        let line = serde_json::to_string(
            &json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params}),
        )
        .map_err(|e| json!({"code": -32603, "message": e.to_string()}))?;
        {
            let mut guard = self.running.lock().unwrap();
            let running = guard
                .as_mut()
                .ok_or_else(|| json!({"code": -32603, "message": "bridge not running"}))?;
            writeln!(running.stdin, "{line}")
                .and_then(|_| running.stdin.flush())
                .map_err(|e| {
                    json!({"code": -32603, "message": format!("write to bridge failed: {e}"),
                                    "data": {"kind": "bridge_exited"}})
                })?;
        }
        let response = match tokio::time::timeout(REQUEST_TIMEOUT, rx).await {
            Ok(Ok(msg)) => msg,
            _ => {
                self.pending.lock().unwrap().remove(&id);
                return Err(
                    json!({"code": -32603, "message": format!("{method} timed out"), "data": {"kind": "timeout"}}),
                );
            }
        };
        match response.get("error") {
            Some(err) => Err(err.clone()),
            None => Ok(response.get("result").cloned().unwrap_or(Value::Null)),
        }
    }

    pub fn diagnostics(&self) -> Value {
        json!({"running": self.alive(), "command": bridge_command().0,
               "stderr_tail": self.stderr_tail.lock().unwrap().clone()})
    }

    pub fn shutdown(&self) {
        // Closing stdin ends the bridge. Detached run drivers are separate processes and keep running.
        if let Some(mut r) = self.running.lock().unwrap().take() {
            drop(r.stdin);
            let _ = r.child.wait();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::ALLOWED_METHODS;

    #[test]
    fn allowlist_covers_the_protocol_and_nothing_dangerous() {
        for m in [
            "system.initialize",
            "sessions.send",
            "runs.approve",
            "runs.cancel",
            "subscribe",
        ] {
            assert!(ALLOWED_METHODS.contains(&m), "{m} should be allowed");
        }
        assert!(
            ALLOWED_METHODS
                .iter()
                .all(|m| !m.contains("shell") && !m.contains("exec"))
        );
        let mut sorted = ALLOWED_METHODS.to_vec();
        sorted.sort_unstable();
        sorted.dedup();
        assert_eq!(sorted.len(), ALLOWED_METHODS.len(), "no duplicate entries");
    }
}
