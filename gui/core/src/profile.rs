use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Profile {
    pub name: String,
    pub provider: String,
    pub isolate: bool,
    pub base_url: Option<String>,
    pub token: Option<String>,
    pub model: Option<String>,
    pub opus: Option<String>,
    pub sonnet: Option<String>,
    pub haiku: Option<String>,
    // OpenAI-compatible (router) profiles. When `router` is set (e.g. "builtin"),
    // the profile is launched through the local ccx-router translator: the
    // upstream endpoint/key live here, while the model uses the ANTHROPIC_*
    // slots above (ccx-router passes the model through).
    #[serde(default)]
    pub router: Option<String>,
    #[serde(default)]
    pub upstream_url: Option<String>,
    #[serde(default)]
    pub upstream_key: Option<String>,
}

impl Profile {
    pub fn from_env_str(name: &str, content: &str) -> Profile {
        let mut p = Profile { name: name.to_string(), ..Default::default() };
        for line in content.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let (k, v) = match line.split_once('=') {
                Some(kv) => kv,
                None => continue,
            };
            match k {
                "CCX_PROVIDER" => p.provider = v.to_string(),
                "CCX_ISOLATE" => p.isolate = v == "true",
                "ANTHROPIC_BASE_URL" => p.base_url = Some(v.to_string()),
                "ANTHROPIC_AUTH_TOKEN" => p.token = Some(v.to_string()),
                "ANTHROPIC_MODEL" => p.model = Some(v.to_string()),
                "ANTHROPIC_DEFAULT_OPUS_MODEL" => p.opus = Some(v.to_string()),
                "ANTHROPIC_DEFAULT_SONNET_MODEL" => p.sonnet = Some(v.to_string()),
                "ANTHROPIC_DEFAULT_HAIKU_MODEL" => p.haiku = Some(v.to_string()),
                "CCX_ROUTER" => p.router = Some(v.to_string()),
                "CCX_UPSTREAM_BASE_URL" => p.upstream_url = Some(v.to_string()),
                "CCX_UPSTREAM_API_KEY" => p.upstream_key = Some(v.to_string()),
                _ => {}
            }
        }
        p
    }

    pub fn is_router(&self) -> bool {
        self.router.as_deref().is_some_and(|r| !r.is_empty())
    }

    pub fn to_env_str(&self) -> String {
        let mut s = String::new();
        s.push_str(&format!("# ccx profile: {}\n", self.name));
        s.push_str(&format!("CCX_PROVIDER={}\n", self.provider));
        s.push_str(&format!("CCX_ISOLATE={}\n", self.isolate));
        if self.is_router() {
            // Router-specific keys (CCX_* — never exported to claude). The model
            // is written via the shared ANTHROPIC_* slots below.
            s.push_str(&format!("CCX_ROUTER={}\n", self.router.as_deref().unwrap_or("builtin")));
            if let Some(v) = &self.upstream_url { s.push_str(&format!("CCX_UPSTREAM_BASE_URL={}\n", v)); }
            if let Some(v) = &self.upstream_key { if !v.is_empty() { s.push_str(&format!("CCX_UPSTREAM_API_KEY={}\n", v)); } }
        } else {
            if let Some(v) = &self.base_url { s.push_str(&format!("ANTHROPIC_BASE_URL={}\n", v)); }
            if let Some(v) = &self.token { s.push_str(&format!("ANTHROPIC_AUTH_TOKEN={}\n", v)); }
        }
        // Model + slot mapping is shared by both kinds.
        if let Some(v) = &self.model { s.push_str(&format!("ANTHROPIC_MODEL={}\n", v)); }
        if let Some(v) = &self.opus { s.push_str(&format!("ANTHROPIC_DEFAULT_OPUS_MODEL={}\n", v)); }
        if let Some(v) = &self.sonnet { s.push_str(&format!("ANTHROPIC_DEFAULT_SONNET_MODEL={}\n", v)); }
        if let Some(v) = &self.haiku { s.push_str(&format!("ANTHROPIC_DEFAULT_HAIKU_MODEL={}\n", v)); }
        s
    }
}

pub fn masked_token(t: &str) -> String {
    let chars: Vec<char> = t.chars().collect();
    if chars.len() <= 8 {
        return "****".to_string();
    }
    let first: String = chars[..3].iter().collect();
    let last: String = chars[chars.len() - 4..].iter().collect();
    format!("{}…{}", first, last)
}

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;

use crate::config;

pub fn create(home: &Path, p: &Profile) -> Result<(), String> {
    let dir = config::profile_dir(home, &p.name);
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let envp = config::profile_env_path(home, &p.name);
    fs::write(&envp, p.to_env_str()).map_err(|e| e.to_string())?;
    fs::set_permissions(&envp, fs::Permissions::from_mode(0o600)).map_err(|e| e.to_string())?;
    if p.isolate {
        let h = dir.join("home");
        fs::create_dir_all(&h).map_err(|e| e.to_string())?;
        fs::set_permissions(&h, fs::Permissions::from_mode(0o700)).map_err(|e| e.to_string())?;
    }
    Ok(())
}

pub fn update(home: &Path, p: &Profile) -> Result<(), String> {
    create(home, p)
}

pub fn get(home: &Path, name: &str) -> Result<Profile, String> {
    let envp = config::profile_env_path(home, name);
    let content = fs::read_to_string(&envp).map_err(|_| format!("unknown profile: {}", name))?;
    Ok(Profile::from_env_str(name, &content))
}

pub fn list(home: &Path) -> Vec<Profile> {
    let mut out = Vec::new();
    let pd = config::profiles_dir(home);
    let entries = match fs::read_dir(&pd) {
        Ok(e) => e,
        Err(_) => return out,
    };
    for entry in entries.flatten() {
        if !entry.path().is_dir() {
            continue;
        }
        let name = entry.file_name().to_string_lossy().to_string();
        if let Ok(p) = get(home, &name) {
            out.push(p);
        }
    }
    out.sort_by(|a, b| a.name.cmp(&b.name));
    out
}

pub fn delete(home: &Path, name: &str) -> Result<(), String> {
    let dir = config::profile_dir(home, name);
    if !dir.is_dir() {
        return Err(format!("unknown profile: {}", name));
    }
    fs::remove_dir_all(&dir).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> Profile {
        Profile {
            name: "mm".into(),
            provider: "minimax".into(),
            isolate: true,
            base_url: Some("https://api.minimax.io/anthropic".into()),
            token: Some("sk-test-123456789".into()),
            model: Some("MiniMax-M2".into()),
            opus: Some("MiniMax-M2".into()),
            sonnet: Some("MiniMax-M2".into()),
            haiku: Some("MiniMax-M2".into()),
            ..Default::default()
        }
    }

    #[test]
    fn round_trips_through_env_string() {
        let p = sample();
        let s = p.to_env_str();
        let back = Profile::from_env_str("mm", &s);
        assert_eq!(p, back);
    }

    #[test]
    fn serializes_expected_keys() {
        let s = sample().to_env_str();
        assert!(s.contains("CCX_PROVIDER=minimax"));
        assert!(s.contains("CCX_ISOLATE=true"));
        assert!(s.contains("ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic"));
        assert!(s.contains("ANTHROPIC_DEFAULT_HAIKU_MODEL=MiniMax-M2"));
        assert!(!s.contains("CCX_PROVIDER=\n"));
    }

    #[test]
    fn parse_ignores_comments_and_blanks() {
        let p = Profile::from_env_str("work", "# ccx profile: work\n\nCCX_PROVIDER=claude\nCCX_ISOLATE=false\nANTHROPIC_MODEL=opusplan\n");
        assert_eq!(p.provider, "claude");
        assert_eq!(p.isolate, false);
        assert_eq!(p.model.as_deref(), Some("opusplan"));
        assert_eq!(p.token, None);
    }

    #[test]
    fn masks_tokens() {
        assert_eq!(masked_token("sk-test-123456789"), "sk-…6789");
        assert_eq!(masked_token("short"), "****");
        assert_eq!(masked_token(""), "****");
    }

    fn router_sample() -> Profile {
        Profile {
            name: "local".into(),
            provider: "ollama".into(),
            isolate: true,
            router: Some("builtin".into()),
            upstream_url: Some("http://localhost:11434/v1".into()),
            upstream_key: None,
            model: Some("qwen2.5-coder:32b".into()),
            opus: Some("qwen2.5-coder:32b".into()),
            sonnet: Some("qwen2.5-coder:32b".into()),
            haiku: Some("qwen2.5-coder:32b".into()),
            ..Default::default()
        }
    }

    #[test]
    fn router_profile_serializes_upstream_and_shared_model_slots() {
        let s = router_sample().to_env_str();
        assert!(s.contains("CCX_ROUTER=builtin"));
        assert!(s.contains("CCX_UPSTREAM_BASE_URL=http://localhost:11434/v1"));
        // model uses the shared ANTHROPIC_* slots (pass-through)
        assert!(s.contains("ANTHROPIC_MODEL=qwen2.5-coder:32b"));
        assert!(s.contains("ANTHROPIC_DEFAULT_OPUS_MODEL=qwen2.5-coder:32b"));
        assert!(!s.contains("ANTHROPIC_BASE_URL"));
        assert!(!s.contains("CCX_TRANSFORMER"));
        assert!(!s.contains("CCX_MODEL_"));
        // no upstream key line when the key is absent (local server)
        assert!(!s.contains("CCX_UPSTREAM_API_KEY"));
    }

    #[test]
    fn router_profile_round_trips() {
        let p = router_sample();
        let back = Profile::from_env_str("local", &p.to_env_str());
        assert_eq!(p, back);
        assert!(back.is_router());
    }

    #[test]
    fn router_create_makes_no_router_dir() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        create(home, &router_sample()).unwrap();
        let dir = crate::config::profile_dir(home, "local");
        assert!(dir.join("home").is_dir(), "isolated home created");
        assert!(!dir.join("router").exists(), "no router dir for builtin translator");
    }

    use std::os::unix::fs::PermissionsExt;

    fn mode(path: &std::path::Path) -> u32 {
        std::fs::metadata(path).unwrap().permissions().mode() & 0o777
    }

    #[test]
    fn create_writes_600_and_isolate_home_700() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        create(home, &sample()).unwrap();
        let envp = crate::config::profile_env_path(home, "mm");
        assert!(envp.exists());
        assert_eq!(mode(&envp), 0o600);
        assert_eq!(mode(&crate::config::profile_dir(home, "mm").join("home")), 0o700);
    }

    #[test]
    fn list_get_update_delete_cycle() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        create(home, &sample()).unwrap();
        let listed = list(home);
        assert_eq!(listed.len(), 1);
        assert_eq!(listed[0].name, "mm");

        let mut p = get(home, "mm").unwrap();
        assert_eq!(p.token.as_deref(), Some("sk-test-123456789"));
        p.model = Some("MiniMax-M2-Plus".into());
        update(home, &p).unwrap();
        assert_eq!(get(home, "mm").unwrap().model.as_deref(), Some("MiniMax-M2-Plus"));

        delete(home, "mm").unwrap();
        assert!(list(home).is_empty());
        assert!(get(home, "mm").is_err());
    }
}
