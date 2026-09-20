#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;

use ccx_core::profile::{self, Profile};
use ccx_core::provider::{self, ProviderTemplate};
use ccx_core::settings::{self, GuiSettings};
use ccx_core::{config, doctor, launcher};
use serde::Serialize;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

fn home() -> PathBuf {
    config::ccx_home()
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ProfileView {
    name: String,
    provider: String,
    isolate: bool,
    base_url: Option<String>,
    model: Option<String>,
    opus: Option<String>,
    sonnet: Option<String>,
    haiku: Option<String>,
    has_token: bool,
    // OpenAI-compatible (router) fields. The model uses the shared model/opus/
    // sonnet/haiku slots above.
    router: Option<String>,
    upstream_url: Option<String>,
    has_upstream_key: bool,
    fallback_urls: Option<String>,
    has_fallback_keys: bool,
    attempts_per_upstream: Option<u8>,
    // Pinning is not a secret, so it is echoed back in full for the form to
    // prefill, unlike the upstream and fallback keys above.
    provider_only: Option<String>,
    provider_order: Option<String>,
    require_parameters: Option<String>,
}

impl From<Profile> for ProfileView {
    fn from(p: Profile) -> Self {
        ProfileView {
            name: p.name,
            provider: p.provider,
            isolate: p.isolate,
            base_url: p.base_url,
            model: p.model,
            opus: p.opus,
            sonnet: p.sonnet,
            haiku: p.haiku,
            has_token: p.token.is_some(),
            router: p.router,
            upstream_url: p.upstream_url,
            has_upstream_key: p.upstream_key.is_some(),
            fallback_urls: p.fallback_urls,
            has_fallback_keys: p.fallback_keys.is_some(),
            attempts_per_upstream: p.attempts_per_upstream,
            provider_only: p.provider_only,
            provider_order: p.provider_order,
            require_parameters: p.require_parameters,
        }
    }
}

#[tauri::command]
fn list_profiles() -> Vec<ProfileView> {
    profile::list(&home())
        .into_iter()
        .map(ProfileView::from)
        .collect()
}

#[tauri::command]
fn get_profile(name: String) -> Result<ProfileView, String> {
    profile::get(&home(), &name).map(ProfileView::from)
}

#[tauri::command]
fn create_profile(profile: Profile) -> Result<(), String> {
    let h = home();
    if config::profile_dir(&h, &profile.name).exists() {
        return Err(format!("profile already exists: {}", profile.name));
    }
    profile::create(&h, &profile)
}

#[tauri::command]
fn update_profile(
    profile: Profile,
    preserve_secrets: bool,
    preserve_fallback_keys: bool,
) -> Result<(), String> {
    let h = home();
    profile::update_preserving_selected_secrets(
        &h,
        &profile,
        preserve_secrets,
        preserve_fallback_keys,
    )
}

#[tauri::command]
fn delete_profile(name: String) -> Result<(), String> {
    profile::delete(&home(), &name)
}

#[tauri::command]
fn list_providers() -> Vec<ProviderTemplate> {
    provider::list_providers(&home())
}

#[tauri::command]
fn launch_profile(name: String) -> Result<(), String> {
    let h = home();
    let p = profile::get(&h, &name)?;
    let s = settings::load(&h);
    launcher::launch(&p, &h, s.terminal_override)
}

#[tauri::command]
fn copy_command(name: String) -> Result<String, String> {
    profile::get(&home(), &name).map(|p| launcher::copy_command(&p))
}

#[tauri::command]
fn get_settings() -> GuiSettings {
    settings::load(&home())
}

#[tauri::command]
fn set_settings(settings: GuiSettings) -> Result<(), String> {
    settings::save(&home(), &settings)
}

#[tauri::command]
fn detect_terminal() -> Option<String> {
    launcher::detect_terminal(
        std::env::var("TERMINAL").ok().as_deref(),
        launcher::is_in_path,
    )
    .map(|t| t.bin)
}

#[tauri::command]
async fn doctor_profile(
    name: String,
    network: Option<bool>,
) -> Result<doctor::DoctorReport, String> {
    tauri::async_runtime::spawn_blocking(move || {
        if network.unwrap_or(true) {
            doctor::run(&name)
        } else {
            doctor::run_offline(&name)
        }
    })
    .await
    .map_err(|_| "ccx doctor task failed".to_string())?
}

fn after_certification_consent<F>(
    confirmed: bool,
    certify: F,
) -> Result<Option<doctor::DoctorReport>, String>
where
    F: FnOnce() -> Result<doctor::DoctorReport, String>,
{
    if !confirmed {
        return Ok(None);
    }
    certify().map(Some)
}

#[tauri::command]
async fn certify_profile(
    app: tauri::AppHandle,
    name: String,
) -> Result<Option<doctor::DoctorReport>, String> {
    // Keep consent inside this command: even a direct IPC invocation must pass
    // through an OS-native confirmation before any billable probe is sent.
    tauri::async_runtime::spawn_blocking(move || {
        let confirmed = app
            .dialog()
            .message(format!(
                "Certify profile \"{name}\" now?\n\nThis sends three minimal API requests to test basic, streaming, and tool capabilities and may incur a small provider charge."
            ))
            .title("Confirm compatibility certification")
            .kind(MessageDialogKind::Warning)
            .buttons(MessageDialogButtons::OkCancelCustom(
                "Certify".to_string(),
                "Cancel".to_string(),
            ))
            .blocking_show();
        after_certification_consent(confirmed, || doctor::certify(&name))
    })
    .await
    .map_err(|_| "ccx certification task failed".to_string())?
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            list_profiles,
            get_profile,
            create_profile,
            update_profile,
            delete_profile,
            list_providers,
            launch_profile,
            copy_command,
            get_settings,
            set_settings,
            detect_terminal,
            doctor_profile,
            certify_profile
        ])
        .run(tauri::generate_context!())
        .expect("error while running ccx-gui");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn profile_view_never_serializes_credentials_or_masked_fragments() {
        let profile = Profile {
            name: "private".into(),
            provider: "openrouter".into(),
            token: Some("token-secret-1234".into()),
            upstream_key: Some("upstream-secret-5678".into()),
            fallback_keys: Some("fallback-secret-9012".into()),
            ..Profile::default()
        };

        let json = serde_json::to_value(ProfileView::from(profile)).unwrap();
        let encoded = json.to_string();
        assert_eq!(json["hasToken"], true);
        assert_eq!(json["hasUpstreamKey"], true);
        assert_eq!(json["hasFallbackKeys"], true);
        assert!(!encoded.contains("secret"));
        assert!(json.get("tokenMasked").is_none());
        assert!(json.get("upstreamKeyMasked").is_none());
        assert!(json.get("fallbackKeysMasked").is_none());
    }

    #[test]
    fn cancelling_native_consent_never_runs_certification() {
        let result = after_certification_consent(false, || {
            panic!("certification must not run after cancellation")
        })
        .unwrap();
        assert!(result.is_none());
    }
}
