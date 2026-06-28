#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;

use ccx_core::profile::{self, masked_token, Profile};
use ccx_core::provider::{self, ProviderTemplate};
use ccx_core::settings::{self, GuiSettings};
use ccx_core::{config, launcher};
use serde::Serialize;

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
    token_masked: Option<String>,
    has_token: bool,
    // OpenAI-compatible (router) fields. The model uses the shared model/opus/
    // sonnet/haiku slots above.
    router: Option<String>,
    upstream_url: Option<String>,
    upstream_key_masked: Option<String>,
    has_upstream_key: bool,
}

impl From<Profile> for ProfileView {
    fn from(p: Profile) -> Self {
        let token_masked = p.token.as_deref().map(masked_token);
        let upstream_key_masked = p.upstream_key.as_deref().map(masked_token);
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
            token_masked,
            router: p.router,
            upstream_url: p.upstream_url,
            has_upstream_key: p.upstream_key.is_some(),
            upstream_key_masked,
        }
    }
}

#[tauri::command]
fn list_profiles() -> Vec<ProfileView> {
    profile::list(&home()).into_iter().map(ProfileView::from).collect()
}

#[tauri::command]
fn get_profile(name: String) -> Result<ProfileView, String> {
    profile::get(&home(), &name).map(ProfileView::from)
}

#[tauri::command]
fn reveal_token(name: String) -> Result<Option<String>, String> {
    profile::get(&home(), &name).map(|p| p.token)
}

#[tauri::command]
fn reveal_upstream_key(name: String) -> Result<Option<String>, String> {
    profile::get(&home(), &name).map(|p| p.upstream_key)
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
fn update_profile(profile: Profile) -> Result<(), String> {
    profile::update(&home(), &profile)
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
    launcher::detect_terminal(std::env::var("TERMINAL").ok().as_deref(), launcher::is_in_path)
        .map(|t| t.bin)
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            list_profiles,
            get_profile,
            reveal_token,
            reveal_upstream_key,
            create_profile,
            update_profile,
            delete_profile,
            list_providers,
            launch_profile,
            copy_command,
            get_settings,
            set_settings,
            detect_terminal
        ])
        .run(tauri::generate_context!())
        .expect("error while running ccx-gui");
}
