use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Write;
use std::net::IpAddr;
use std::path::Path;
use url::{Host, Url};

use crate::config;

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
    #[serde(default)]
    pub fallback_urls: Option<String>,
    #[serde(default)]
    pub fallback_keys: Option<String>,
    #[serde(default)]
    pub attempts_per_upstream: Option<u8>,
    // OpenRouter provider pinning, positional across the upstream chain: `;`
    // separates upstreams (position 0 is the primary), `,` separates provider
    // slugs within one upstream.
    #[serde(default)]
    pub provider_only: Option<String>,
    #[serde(default)]
    pub provider_order: Option<String>,
    #[serde(default)]
    pub require_parameters: Option<String>,
}

impl Profile {
    pub fn from_env_str(name: &str, content: &str) -> Profile {
        let mut p = Profile {
            name: name.to_string(),
            ..Default::default()
        };
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
                "CCX_ROUTER_FALLBACK_URLS" => p.fallback_urls = Some(v.to_string()),
                "CCX_ROUTER_FALLBACK_KEYS" => p.fallback_keys = Some(v.to_string()),
                "CCX_ROUTER_PROVIDER_ONLY" => p.provider_only = Some(v.to_string()),
                "CCX_ROUTER_PROVIDER_ORDER" => p.provider_order = Some(v.to_string()),
                "CCX_ROUTER_REQUIRE_PARAMETERS" => p.require_parameters = Some(v.to_string()),
                "CCX_ROUTER_ATTEMPTS_PER_UPSTREAM" => {
                    p.attempts_per_upstream = v.parse::<u8>().ok()
                }
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
            s.push_str(&format!(
                "CCX_ROUTER={}\n",
                self.router.as_deref().unwrap_or("builtin")
            ));
            if let Some(v) = &self.upstream_url {
                s.push_str(&format!("CCX_UPSTREAM_BASE_URL={}\n", v));
            }
            if let Some(v) = &self.upstream_key {
                if !v.is_empty() {
                    s.push_str(&format!("CCX_UPSTREAM_API_KEY={}\n", v));
                }
            }
            if let Some(v) = &self.fallback_urls {
                if !v.is_empty() {
                    s.push_str(&format!("CCX_ROUTER_FALLBACK_URLS={}\n", v));
                }
            }
            if let Some(v) = &self.fallback_keys {
                if !v.is_empty() {
                    s.push_str(&format!("CCX_ROUTER_FALLBACK_KEYS={}\n", v));
                }
            }
            if let Some(v) = self.attempts_per_upstream {
                s.push_str(&format!("CCX_ROUTER_ATTEMPTS_PER_UPSTREAM={}\n", v));
            }
            for (key, value) in [
                ("CCX_ROUTER_PROVIDER_ONLY", &self.provider_only),
                ("CCX_ROUTER_PROVIDER_ORDER", &self.provider_order),
                ("CCX_ROUTER_REQUIRE_PARAMETERS", &self.require_parameters),
            ] {
                if let Some(v) = value {
                    if !v.is_empty() {
                        s.push_str(&format!("{key}={v}\n"));
                    }
                }
            }
        } else {
            if let Some(v) = &self.base_url {
                s.push_str(&format!("ANTHROPIC_BASE_URL={}\n", v));
            }
            if let Some(v) = &self.token {
                s.push_str(&format!("ANTHROPIC_AUTH_TOKEN={}\n", v));
            }
        }
        // Model + slot mapping is shared by both kinds.
        if let Some(v) = &self.model {
            s.push_str(&format!("ANTHROPIC_MODEL={}\n", v));
        }
        if let Some(v) = &self.opus {
            s.push_str(&format!("ANTHROPIC_DEFAULT_OPUS_MODEL={}\n", v));
        }
        if let Some(v) = &self.sonnet {
            s.push_str(&format!("ANTHROPIC_DEFAULT_SONNET_MODEL={}\n", v));
        }
        if let Some(v) = &self.haiku {
            s.push_str(&format!("ANTHROPIC_DEFAULT_HAIKU_MODEL={}\n", v));
        }
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

pub fn valid_name(name: &str) -> bool {
    !name.is_empty()
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

fn validate_env_value(label: &str, value: &str, allow_empty: bool) -> Result<(), String> {
    if (!allow_empty && value.is_empty()) || value.contains(['\n', '\r', '\0']) {
        return Err(format!("{label} must be a non-empty single-line value"));
    }
    Ok(())
}

fn is_loopback_host(host: Host<&str>) -> bool {
    match host {
        Host::Domain(domain) => domain
            .trim_end_matches('.')
            .eq_ignore_ascii_case("localhost"),
        Host::Ipv4(address) => IpAddr::V4(address).is_loopback(),
        Host::Ipv6(address) => IpAddr::V6(address).is_loopback(),
    }
}

fn validate_endpoint(label: &str, value: &str) -> Result<(), String> {
    validate_env_value(label, value, false)?;
    if value.chars().any(char::is_whitespace) {
        return Err(format!("{label} must not contain whitespace"));
    }
    let parsed = Url::parse(value).map_err(|_| format!("{label} must be a valid URL"))?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.host().is_none() {
        return Err(format!("{label} must be an HTTP or HTTPS URL with a host"));
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err(format!("{label} must not contain credentials"));
    }
    if parsed.query().is_some() || parsed.fragment().is_some() {
        return Err(format!("{label} must not contain a query or fragment"));
    }
    if parsed.scheme() == "http" && !parsed.host().is_some_and(is_loopback_host) {
        return Err(format!("{label} must use HTTPS unless it is loopback"));
    }
    Ok(())
}

/// Validate the IPC/profile boundary before serializing environment variables.
/// Values are intentionally not echoed in errors because several are secrets.
/// Positions align to the upstream chain, so a value can never describe more
/// upstreams than the profile configures. Slugs themselves are opaque; only the
/// charset is checked, which catches typos before they become silent mis-routes.
fn validate_pinning(label: &str, value: &str, max_positions: usize) -> Result<(), String> {
    validate_env_value(label, value, true)?;
    let positions: Vec<&str> = value.split(';').collect();
    if positions.len() > max_positions {
        return Err(format!(
            "{label} has more positions ({}) than configured upstreams ({max_positions})",
            positions.len()
        ));
    }
    for slug in positions
        .iter()
        .flat_map(|position| position.split(','))
        .map(str::trim)
        .filter(|slug| !slug.is_empty())
    {
        if !slug
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
        {
            return Err(format!("{label} has an invalid provider slug: {slug}"));
        }
    }
    Ok(())
}

fn validate_require_parameters(
    label: &str,
    value: &str,
    max_positions: usize,
) -> Result<(), String> {
    validate_env_value(label, value, true)?;
    let positions: Vec<&str> = value.split(';').collect();
    if positions.len() > max_positions {
        return Err(format!(
            "{label} has more positions ({}) than configured upstreams ({max_positions})",
            positions.len()
        ));
    }
    for position in positions {
        if !matches!(position.trim(), "" | "1" | "true") {
            return Err(format!("{label} entries must be 1, true, or empty"));
        }
    }
    Ok(())
}

pub fn validate(p: &Profile) -> Result<(), String> {
    if !valid_name(&p.name) {
        return Err("invalid profile name".to_string());
    }
    validate_env_value("provider", &p.provider, false)?;
    for (label, value) in [
        ("model", p.model.as_deref()),
        ("opus model", p.opus.as_deref()),
        ("sonnet model", p.sonnet.as_deref()),
        ("haiku model", p.haiku.as_deref()),
    ] {
        if let Some(value) = value {
            validate_env_value(label, value, false)?;
        }
    }

    if p.is_router() {
        validate_env_value("router", p.router.as_deref().unwrap_or_default(), false)?;
        if p.base_url.is_some() || p.token.is_some() {
            return Err("router profiles cannot contain direct-provider credentials".to_string());
        }
        let upstream = p
            .upstream_url
            .as_deref()
            .ok_or_else(|| "router profile requires an upstream URL".to_string())?;
        validate_endpoint("upstream URL", upstream)?;
        if let Some(key) = p.upstream_key.as_deref() {
            validate_env_value("upstream key", key, false)?;
        }

        let fallback_count = if let Some(raw) = p.fallback_urls.as_deref() {
            validate_env_value("fallback URLs", raw, false)?;
            let mut count = 0;
            for value in raw.split(',') {
                if value.is_empty() {
                    return Err("fallback URLs cannot contain an empty entry".to_string());
                }
                validate_endpoint("fallback URL", value)?;
                count += 1;
            }
            count
        } else {
            0
        };
        if let Some(keys) = p.fallback_keys.as_deref() {
            validate_env_value("fallback keys", keys, false)?;
            if fallback_count == 0 {
                return Err("fallback keys require at least one fallback URL".to_string());
            }
            if keys.split(',').count() > fallback_count {
                return Err("more fallback keys than fallback URLs are configured".to_string());
            }
        }
        if let Some(attempts) = p.attempts_per_upstream {
            if !(1..=10).contains(&attempts) {
                return Err("attempts per upstream must be between 1 and 10".to_string());
            }
        }
        let max_positions = fallback_count + 1;
        if let Some(value) = p.provider_only.as_deref() {
            validate_pinning("CCX_ROUTER_PROVIDER_ONLY", value, max_positions)?;
        }
        if let Some(value) = p.provider_order.as_deref() {
            validate_pinning("CCX_ROUTER_PROVIDER_ORDER", value, max_positions)?;
        }
        if let Some(value) = p.require_parameters.as_deref() {
            validate_require_parameters("CCX_ROUTER_REQUIRE_PARAMETERS", value, max_positions)?;
        }
    } else {
        if p.upstream_url.is_some()
            || p.upstream_key.is_some()
            || p.fallback_urls.is_some()
            || p.fallback_keys.is_some()
            || p.attempts_per_upstream.is_some()
            || p.provider_only.is_some()
            || p.provider_order.is_some()
            || p.require_parameters.is_some()
        {
            return Err("direct profiles cannot contain router settings".to_string());
        }
        if let Some(base_url) = p.base_url.as_deref() {
            validate_endpoint("base URL", base_url)?;
        }
        if let Some(token) = p.token.as_deref() {
            validate_env_value("token", token, false)?;
        }
    }
    Ok(())
}

#[cfg(unix)]
fn set_private_file_permissions(path: &Path) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;

    fs::set_permissions(path, fs::Permissions::from_mode(0o600)).map_err(|e| e.to_string())
}

#[cfg(not(unix))]
fn set_private_file_permissions(_path: &Path) -> Result<(), String> {
    // Windows does not expose POSIX mode bits. The profile is created below the
    // current user's config directory; native credential storage is handled by
    // the launcher rather than emulating chmod semantics here.
    Ok(())
}

#[cfg(unix)]
fn set_private_dir_permissions(path: &Path) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;

    fs::set_permissions(path, fs::Permissions::from_mode(0o700)).map_err(|e| e.to_string())
}

#[cfg(not(unix))]
fn set_private_dir_permissions(_path: &Path) -> Result<(), String> {
    Ok(())
}

pub fn create(home: &Path, p: &Profile) -> Result<(), String> {
    validate(p)?;
    let profiles = config::profiles_dir(home);
    fs::create_dir_all(&profiles).map_err(|e| e.to_string())?;
    set_private_dir_permissions(&profiles)?;
    let dir = config::profile_dir(home, &p.name);
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    set_private_dir_permissions(&dir)?;
    let envp = config::profile_env_path(home, &p.name);
    let mut pending = tempfile::Builder::new()
        .prefix(".profile.env.")
        .tempfile_in(&dir)
        .map_err(|e| e.to_string())?;
    set_private_file_permissions(pending.path())?;
    pending
        .write_all(p.to_env_str().as_bytes())
        .map_err(|e| e.to_string())?;
    pending.as_file().sync_all().map_err(|e| e.to_string())?;
    pending.persist(&envp).map_err(|e| e.error.to_string())?;
    set_private_file_permissions(&envp)?;
    if p.isolate {
        let h = dir.join("home");
        fs::create_dir_all(&h).map_err(|e| e.to_string())?;
        set_private_dir_permissions(&h)?;
    }
    Ok(())
}

pub fn update(home: &Path, p: &Profile) -> Result<(), String> {
    create(home, p)
}

/// Update editable profile fields while retaining credentials that were kept out
/// of the GUI payload because the user did not reveal them.
pub fn update_preserving_secrets(home: &Path, p: &Profile) -> Result<(), String> {
    update_preserving_selected_secrets(home, p, true, true)
}

pub fn update_preserving_selected_secrets(
    home: &Path,
    p: &Profile,
    preserve_primary: bool,
    preserve_fallback_keys: bool,
) -> Result<(), String> {
    let current = get(home, &p.name)?;
    let mut updated = p.clone();
    if preserve_primary {
        updated.token = current.token;
        updated.upstream_key = current.upstream_key;
    }
    if preserve_fallback_keys {
        updated.fallback_keys = current.fallback_keys;
    }
    create(home, &updated)
}

pub fn get(home: &Path, name: &str) -> Result<Profile, String> {
    if !valid_name(name) {
        return Err("invalid profile name".to_string());
    }
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
    if !valid_name(name) {
        return Err("invalid profile name".to_string());
    }
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
        assert!(!p.isolate);
        assert_eq!(p.model.as_deref(), Some("opusplan"));
        assert_eq!(p.token, None);
    }

    #[test]
    fn masks_tokens() {
        assert_eq!(masked_token("sk-test-123456789"), "sk-…6789");
        assert_eq!(masked_token("short"), "****");
        assert_eq!(masked_token(""), "****");
    }

    #[test]
    fn profile_names_cannot_escape_the_profiles_directory() {
        for invalid in ["", "../work", "work/dev", "work dev", "."] {
            assert!(!valid_name(invalid), "accepted {invalid:?}");
        }
        for valid in ["work", "openrouter_1", "local-dev"] {
            assert!(valid_name(valid), "rejected {valid:?}");
        }

        let tmp = tempfile::tempdir().unwrap();
        let mut p = sample();
        p.name = "../escape".into();
        assert_eq!(create(tmp.path(), &p).unwrap_err(), "invalid profile name");
        assert!(!tmp.path().join("escape").exists());
    }

    #[test]
    fn rejects_multiline_values_before_writing_env_files() {
        let tmp = tempfile::tempdir().unwrap();
        let mut p = sample();
        p.token = Some("secret\nCCX_ROUTER=builtin".into());
        assert!(create(tmp.path(), &p).unwrap_err().contains("single-line"));
        assert!(!config::profile_env_path(tmp.path(), "mm").exists());
    }

    #[test]
    fn validates_endpoint_security_at_the_backend_boundary() {
        let mut p = sample();
        p.base_url = Some("http://api.example.com/v1".into());
        assert!(validate(&p).unwrap_err().contains("HTTPS"));

        p.base_url = Some("http://127.0.0.2:8080/v1".into());
        validate(&p).unwrap();

        p.base_url = Some("https://user:secret@example.com/v1".into());
        assert!(validate(&p).unwrap_err().contains("credentials"));

        p.base_url = Some("https://api.example.com/v1?key=secret".into());
        assert!(validate(&p).unwrap_err().contains("query"));
    }

    #[test]
    fn validates_router_fallback_alignment_and_attempts() {
        let mut p = router_sample();
        p.fallback_urls = Some("https://one.example/v1".into());
        p.fallback_keys = Some("first,second".into());
        assert!(validate(&p).unwrap_err().contains("more fallback keys"));

        p.fallback_keys = Some(String::new());
        assert!(validate(&p).unwrap_err().contains("single-line"));

        p.fallback_keys = None;
        p.attempts_per_upstream = Some(0);
        assert!(validate(&p).unwrap_err().contains("between 1 and 10"));
    }

    fn router_sample() -> Profile {
        Profile {
            name: "local".into(),
            provider: "ollama".into(),
            isolate: true,
            router: Some("builtin".into()),
            upstream_url: Some("http://localhost:11434/v1".into()),
            upstream_key: None,
            fallback_urls: Some("https://fallback.example/v1".into()),
            fallback_keys: Some("sk-fallback-123456789".into()),
            attempts_per_upstream: Some(2),
            model: Some("qwen2.5-coder:32b".into()),
            opus: Some("qwen2.5-coder:32b".into()),
            sonnet: Some("qwen2.5-coder:32b".into()),
            haiku: Some("qwen2.5-coder:32b".into()),
            ..Default::default()
        }
    }

    #[test]
    fn provider_pinning_round_trips_through_the_env_file() {
        let mut p = router_sample();
        p.provider_only = Some("groq,fireworks;together".into());
        p.provider_order = Some("groq,fireworks".into());
        p.require_parameters = Some(";1".into());
        let s = p.to_env_str();
        assert!(
            s.contains("CCX_ROUTER_PROVIDER_ONLY=groq,fireworks;together"),
            "{s}"
        );
        assert!(
            s.contains("CCX_ROUTER_PROVIDER_ORDER=groq,fireworks"),
            "{s}"
        );
        assert!(s.contains("CCX_ROUTER_REQUIRE_PARAMETERS=;1"), "{s}");
        let parsed = Profile::from_env_str("local", &s);
        assert_eq!(parsed.provider_only, p.provider_only);
        assert_eq!(parsed.provider_order, p.provider_order);
        assert_eq!(parsed.require_parameters, p.require_parameters);
    }

    #[test]
    fn an_unpinned_router_profile_writes_no_pinning_keys() {
        let s = router_sample().to_env_str();
        assert!(!s.contains("CCX_ROUTER_PROVIDER_ONLY"), "{s}");
        assert!(!s.contains("CCX_ROUTER_PROVIDER_ORDER"), "{s}");
        assert!(!s.contains("CCX_ROUTER_REQUIRE_PARAMETERS"), "{s}");
    }

    #[test]
    fn validates_provider_pinning_against_the_upstream_chain() {
        let mut p = router_sample();
        // router_sample has one fallback, so two positions are the maximum.
        p.provider_only = Some("groq;together;deepinfra".into());
        assert!(validate(&p).is_err());

        p.provider_only = Some("groq;together".into());
        assert!(validate(&p).is_ok());

        p.provider_only = Some("bad slug".into());
        assert!(validate(&p).is_err());

        p.provider_only = None;
        p.require_parameters = Some("yes".into());
        assert!(validate(&p).is_err());
    }

    #[test]
    fn a_direct_profile_cannot_carry_provider_pinning() {
        let mut p = sample();
        p.provider_only = Some("groq".into());
        let error = validate(&p).err().unwrap();
        assert!(error.contains("router settings"), "{error}");
    }

    #[test]
    fn router_profile_serializes_upstream_and_shared_model_slots() {
        let s = router_sample().to_env_str();
        assert!(s.contains("CCX_ROUTER=builtin"));
        assert!(s.contains("CCX_UPSTREAM_BASE_URL=http://localhost:11434/v1"));
        // model uses the shared ANTHROPIC_* slots (pass-through)
        assert!(s.contains("ANTHROPIC_MODEL=qwen2.5-coder:32b"));
        assert!(s.contains("ANTHROPIC_DEFAULT_OPUS_MODEL=qwen2.5-coder:32b"));
        assert!(s.contains("CCX_ROUTER_FALLBACK_URLS=https://fallback.example/v1"));
        assert!(s.contains("CCX_ROUTER_FALLBACK_KEYS=sk-fallback-123456789"));
        assert!(s.contains("CCX_ROUTER_ATTEMPTS_PER_UPSTREAM=2"));
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
        assert!(
            !dir.join("router").exists(),
            "no router dir for builtin translator"
        );
    }

    #[cfg(unix)]
    fn mode(path: &std::path::Path) -> u32 {
        use std::os::unix::fs::PermissionsExt;

        std::fs::metadata(path).unwrap().permissions().mode() & 0o777
    }

    #[cfg(unix)]
    #[test]
    fn create_writes_600_and_isolate_home_700() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        create(home, &sample()).unwrap();
        let envp = crate::config::profile_env_path(home, "mm");
        assert!(envp.exists());
        assert_eq!(mode(&envp), 0o600);
        assert_eq!(
            mode(&crate::config::profile_dir(home, "mm").join("home")),
            0o700
        );
    }

    #[cfg(unix)]
    #[test]
    fn update_atomically_replaces_profile_symlink_without_following_it() {
        use std::os::unix::fs::symlink;

        let tmp = tempfile::tempdir().unwrap();
        let p = sample();
        let dir = config::profile_dir(tmp.path(), &p.name);
        fs::create_dir_all(&dir).unwrap();
        let target = tmp.path().join("must-survive");
        fs::write(&target, "keep").unwrap();
        let envp = config::profile_env_path(tmp.path(), &p.name);
        symlink(&target, &envp).unwrap();

        create(tmp.path(), &p).unwrap();

        assert_eq!(fs::read_to_string(target).unwrap(), "keep");
        assert!(!fs::symlink_metadata(&envp)
            .unwrap()
            .file_type()
            .is_symlink());
        assert!(fs::read_to_string(envp)
            .unwrap()
            .contains("CCX_PROVIDER=minimax"));
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
        assert_eq!(
            get(home, "mm").unwrap().model.as_deref(),
            Some("MiniMax-M2-Plus")
        );

        delete(home, "mm").unwrap();
        assert!(list(home).is_empty());
        assert!(get(home, "mm").is_err());
    }

    #[test]
    fn update_preserving_secrets_keeps_direct_token() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        create(home, &sample()).unwrap();

        let mut edited = sample();
        edited.model = Some("MiniMax-M2-Plus".into());
        edited.token = None; // Unrevealed credentials are absent from the GUI payload.
        update_preserving_secrets(home, &edited).unwrap();

        let saved = get(home, "mm").unwrap();
        assert_eq!(saved.model.as_deref(), Some("MiniMax-M2-Plus"));
        assert_eq!(saved.token.as_deref(), Some("sk-test-123456789"));
    }

    #[test]
    fn update_preserving_secrets_keeps_router_key() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        let mut original = router_sample();
        original.upstream_key = Some("sk-router-123456789".into());
        create(home, &original).unwrap();

        let mut edited = original;
        edited.model = Some("qwen3-coder".into());
        edited.upstream_key = None; // Unrevealed credentials are absent from the GUI payload.
        update_preserving_secrets(home, &edited).unwrap();

        let saved = get(home, "local").unwrap();
        assert_eq!(saved.model.as_deref(), Some("qwen3-coder"));
        assert_eq!(saved.upstream_key.as_deref(), Some("sk-router-123456789"));
    }

    #[test]
    fn regular_update_can_replace_and_clear_revealed_secrets() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        create(home, &sample()).unwrap();

        let mut edited = sample();
        edited.token = Some("sk-replacement-987654321".into());
        update(home, &edited).unwrap();
        assert_eq!(
            get(home, "mm").unwrap().token.as_deref(),
            Some("sk-replacement-987654321")
        );

        edited.token = None;
        update(home, &edited).unwrap();
        assert_eq!(get(home, "mm").unwrap().token, None);

        let mut router = router_sample();
        router.upstream_key = Some("sk-router-original".into());
        create(home, &router).unwrap();
        router.upstream_key = Some("sk-router-replacement".into());
        update(home, &router).unwrap();
        assert_eq!(
            get(home, "local").unwrap().upstream_key.as_deref(),
            Some("sk-router-replacement")
        );

        router.upstream_key = None;
        update(home, &router).unwrap();
        assert_eq!(get(home, "local").unwrap().upstream_key, None);
    }

    #[test]
    fn selected_secret_preservation_is_independent() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        let mut original = router_sample();
        original.upstream_key = Some("primary-old".into());
        create(home, &original).unwrap();

        let mut edited = original.clone();
        edited.upstream_key = Some("primary-new".into());
        edited.fallback_keys = None;
        update_preserving_selected_secrets(home, &edited, false, true).unwrap();
        let saved = get(home, "local").unwrap();
        assert_eq!(saved.upstream_key.as_deref(), Some("primary-new"));
        assert_eq!(saved.fallback_keys, original.fallback_keys);

        let mut edited = saved;
        edited.upstream_key = None;
        edited.fallback_keys = Some("fallback-new".into());
        update_preserving_selected_secrets(home, &edited, true, false).unwrap();
        let saved = get(home, "local").unwrap();
        assert_eq!(saved.upstream_key.as_deref(), Some("primary-new"));
        assert_eq!(saved.fallback_keys.as_deref(), Some("fallback-new"));
    }
}
