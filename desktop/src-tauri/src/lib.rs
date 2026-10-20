mod bridge;

use bridge::BridgeState;
use serde_json::Value;
use tauri::{AppHandle, Manager, RunEvent, State};

#[tauri::command]
async fn bridge_request(
    app: AppHandle,
    state: State<'_, BridgeState>,
    method: String,
    params: Option<Value>,
) -> Result<Value, Value> {
    state
        .request(
            &app,
            &method,
            params.unwrap_or(Value::Object(Default::default())),
        )
        .await
}

#[tauri::command]
fn bridge_diagnostics(state: State<'_, BridgeState>) -> Value {
    state.diagnostics()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(BridgeState::default())
        .invoke_handler(tauri::generate_handler![bridge_request, bridge_diagnostics])
        .build(tauri::generate_context!())
        .expect("error while building Saarathi Desktop");
    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            handle.state::<BridgeState>().shutdown();
        }
    });
}
