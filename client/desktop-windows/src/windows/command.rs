use super::session::Session;
use super::*;

pub(crate) fn warn_jump_list(diagnostics: &[String]) {
    for diagnostic in diagnostics {
        emit(json!({ "event": "warning", "notify": false, "reason": format!("Jump List: {diagnostic}") }));
    }
}

pub(crate) fn execute_command(
    path: &Path,
    session: &mut Option<Session>,
    command: Command,
) -> Result<serde_json::Value> {
    match (command, session.as_mut()) {
        (Command::Start { parent_pid, takeover, palette, windows, work_area, .. }, current) => {
            if current.is_some() {
                return Err("desktop host is already running".into());
            }
            *session = Some(Session::start(path, parent_pid, takeover, palette, &windows, work_area)?);
            Ok(json!(null))
        }
        (Command::Stop { .. }, active) => active.map_or(Ok(()), Session::stop).map(|_| json!(null)),
        (Command::CancelExternalInteraction { .. }, active) => {
            if let Some(active) = active {
                active.applications.cancel_interaction();
            }
            Ok(json!(null))
        }
        (_, None) => Err("desktop is not running".into()),
        (Command::Heartbeat { .. }, Some(active)) => active.heartbeat().map(|_| json!(null)),
        (Command::SetPalette { palette, .. }, Some(active)) => active.set_palette(palette).map(|_| json!(null)),
        (Command::SetTrayLayout { layout, .. }, Some(active)) => active.set_tray_layout(layout).map(|_| json!(null)),
        (Command::Focus { handle, .. }, Some(active)) => active.focus(&handle).map(|_| json!(null)),
        (Command::RefreshApplications { .. }, Some(active)) => active
            .refresh_applications(true)
            .map(|_| json!(null)),
        (Command::ActivateExternal { window_id, allow_remote, .. }, Some(active)) => active
            .activate_external(&window_id, allow_remote)
            .map(|activation_id| json!({ "activation_id": activation_id })),
        (Command::AuthorizeExternalFocus { activation_id, granted, .. }, Some(active)) => {
            require_session(&active.path, &active.journal.session, &active.restoring)?;
            active.applications.authorize_focus(&activation_id, granted, &active.workspace)
                .map(|status| json!({ "status": status.as_str() }))
        }
        (Command::LaunchExternal { target, app_id, .. }, Some(active)) => active
            .launch_external(&target, &app_id)
            .map(|_| json!(null)),
        (Command::CloseExternal { window_ids, .. }, Some(active)) => active
            .close_external(&window_ids)
            .map(|_| json!(null)),
        (Command::GetJumpListTasks { target, .. }, Some(active)) => {
            let read = active.get_jump_list_tasks(&target)?;
            warn_jump_list(&read.diagnostics);
            Ok(json!({ "tasks": read.tasks }))
        }
        (Command::ActivateJumpListTask { target, task_id, expires_at, .. }, Some(active)) => {
            let diagnostics = active.activate_jump_list_task(&target, &task_id, expires_at)?;
            warn_jump_list(&diagnostics);
            Ok(json!(null))
        }
    }
}

pub(crate) fn interrupt_session(active: Option<&mut Session>, marker: &str, reason: String) -> String {
    let Some(active) = active else {
        return reason;
    };
    active.cancel_operations();
    match restore_interrupted(&active.path, &active.journal.session, marker, &active.restoring) {
        Ok(_) => reason,
        Err(error) => format!("{reason}; restoration: {error}"),
    }
}
