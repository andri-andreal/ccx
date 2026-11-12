import { invoke, Channel } from "@tauri-apps/api/core";

export const listProfiles = () => invoke("list_profiles");
export const getProfile = (name) => invoke("get_profile", { name });
export const createProfile = (profile) => invoke("create_profile", { profile });
export const updateProfile = (profile, preserveSecrets, preserveFallbackKeys) =>
  invoke("update_profile", { profile, preserveSecrets, preserveFallbackKeys });
export const deleteProfile = (name) => invoke("delete_profile", { name });
export const listProviders = () => invoke("list_providers");
export const launchProfile = (name) => invoke("launch_profile", { name });
export const copyCommand = (name) => invoke("copy_command", { name });
export const getSettings = () => invoke("get_settings");
export const setSettings = (settings) => invoke("set_settings", { settings });
export const detectTerminal = () => invoke("detect_terminal");
export const doctorProfile = (name, network = true) => invoke("doctor_profile", { name, network });
export const certifyProfile = (name) => invoke("certify_profile", { name });

export const teamRequest = (action, data = {}) => invoke("team_request", { action, data });
export const pickTeamProject = () => invoke("pick_team_project");
export const startTeamSession = (data, onmessage) => {
  const events = new Channel();
  events.onmessage = onmessage;
  return invoke("start_team_session", { data, events });
};
export const sendTeamInput = (id, message) => invoke("send_team_input", { id, message });
export const closeTeamSession = (id) => invoke("close_team_session", { id });
