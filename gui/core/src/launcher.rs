use std::path::Path;
use std::process::Command;

use crate::config;
use crate::profile::Profile;

#[cfg(unix)]
fn set_private_dir_permissions(path: &Path) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;

    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700))
        .map_err(|e| e.to_string())
}

#[cfg(not(unix))]
fn set_private_dir_permissions(_path: &Path) -> Result<(), String> {
    Ok(())
}

#[derive(Debug, Clone, PartialEq)]
pub struct TerminalSpec {
    pub bin: String,
    pub exec_args: Vec<String>,
}

fn known_terminals() -> Vec<(&'static str, Vec<&'static str>)> {
    vec![
        ("kitty", vec![]),
        ("alacritty", vec!["-e"]),
        ("konsole", vec!["-e"]),
        ("foot", vec![]),
        ("wezterm", vec!["start", "--"]),
        ("ghostty", vec!["-e"]),
        ("gnome-terminal", vec!["--"]),
        ("xterm", vec!["-e"]),
    ]
}

pub fn build_env(p: &Profile, home: &Path) -> Vec<(String, String)> {
    let mut e = Vec::new();
    if let Some(v) = &p.base_url {
        e.push(("ANTHROPIC_BASE_URL".into(), v.clone()));
    }
    if let Some(v) = &p.token {
        e.push(("ANTHROPIC_AUTH_TOKEN".into(), v.clone()));
    }
    if let Some(v) = &p.model {
        e.push(("ANTHROPIC_MODEL".into(), v.clone()));
    }
    if let Some(v) = &p.opus {
        e.push(("ANTHROPIC_DEFAULT_OPUS_MODEL".into(), v.clone()));
    }
    if let Some(v) = &p.sonnet {
        e.push(("ANTHROPIC_DEFAULT_SONNET_MODEL".into(), v.clone()));
    }
    if let Some(v) = &p.haiku {
        e.push(("ANTHROPIC_DEFAULT_HAIKU_MODEL".into(), v.clone()));
    }
    if p.isolate {
        let h = config::profile_dir(home, &p.name).join("home");
        e.push(("CLAUDE_CONFIG_DIR".into(), h.to_string_lossy().to_string()));
    }
    e
}

pub fn detect_terminal(
    term_env: Option<&str>,
    available: impl Fn(&str) -> bool,
) -> Option<TerminalSpec> {
    let table = known_terminals();
    if let Some(t) = term_env.filter(|s| !s.is_empty()) {
        if available(t) {
            let args = table
                .iter()
                .find(|(b, _)| *b == t)
                .map(|(_, a)| a.iter().map(|s| s.to_string()).collect())
                .unwrap_or_else(|| vec!["-e".to_string()]);
            return Some(TerminalSpec {
                bin: t.to_string(),
                exec_args: args,
            });
        }
    }
    for (b, a) in table {
        if available(b) {
            return Some(TerminalSpec {
                bin: b.to_string(),
                exec_args: a.iter().map(|s| s.to_string()).collect(),
            });
        }
    }
    None
}

pub fn is_in_path(bin: &str) -> bool {
    std::env::var("PATH")
        .map(|paths| std::env::split_paths(&paths).any(|d| d.join(bin).is_file()))
        .unwrap_or(false)
}

pub fn copy_command(p: &Profile) -> String {
    format!("ccx {}", p.name)
}

// Always delegate profile parsing, validation, environment setup, and router
// lifecycle to the CLI. This keeps secrets out of the GUI/terminal process
// arguments and gives direct and routed profiles one security boundary.
pub fn child_command(p: &Profile) -> Vec<String> {
    vec!["ccx".to_string(), p.name.clone()]
}

pub fn launch(p: &Profile, home: &Path, term_override: Option<Vec<String>>) -> Result<(), String> {
    use std::fs;

    if p.isolate {
        let h = config::profile_dir(home, &p.name).join("home");
        fs::create_dir_all(&h).map_err(|e| e.to_string())?;
        set_private_dir_permissions(&h)?;
    }

    let (bin, args): (String, Vec<String>) = match term_override {
        Some(ov) if !ov.is_empty() => (ov[0].clone(), ov[1..].to_vec()),
        _ => {
            let spec = detect_terminal(std::env::var("TERMINAL").ok().as_deref(), is_in_path)
                .ok_or("no supported terminal found; use Copy command instead")?;
            (spec.bin, spec.exec_args)
        }
    };

    let mut cmd = Command::new(&bin);
    cmd.args(&args).args(child_command(p));
    cmd.spawn().map_err(|e| e.to_string())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn prof(isolate: bool) -> Profile {
        Profile {
            name: "mm".into(),
            provider: "minimax".into(),
            isolate,
            base_url: Some("https://api.minimax.io/anthropic".into()),
            token: Some("sk-1".into()),
            model: Some("MiniMax-M2".into()),
            opus: None,
            sonnet: None,
            haiku: None,
            ..Default::default()
        }
    }

    #[test]
    fn build_env_includes_keys_and_isolation() {
        let home = Path::new("/c");
        let e = build_env(&prof(true), home);
        assert!(e.contains(&(
            "ANTHROPIC_BASE_URL".into(),
            "https://api.minimax.io/anthropic".into()
        )));
        assert!(e.contains(&("ANTHROPIC_AUTH_TOKEN".into(), "sk-1".into())));
        assert!(e.contains(&("CLAUDE_CONFIG_DIR".into(), "/c/profiles/mm/home".into())));
    }

    #[test]
    fn build_env_omits_isolation_when_not_isolated() {
        let e = build_env(&prof(false), Path::new("/c"));
        assert!(!e.iter().any(|(k, _)| k == "CLAUDE_CONFIG_DIR"));
    }

    #[test]
    fn detect_prefers_terminal_env_when_available() {
        let spec = detect_terminal(Some("kitty"), |b| b == "kitty" || b == "xterm").unwrap();
        assert_eq!(spec.bin, "kitty");
        assert_eq!(spec.exec_args, Vec::<String>::new());
    }

    #[test]
    fn detect_falls_back_to_priority_order() {
        let spec = detect_terminal(None, |b| b == "alacritty").unwrap();
        assert_eq!(spec.bin, "alacritty");
        assert_eq!(spec.exec_args, vec!["-e".to_string()]);
    }

    #[test]
    fn detect_returns_none_when_nothing_available() {
        assert!(detect_terminal(None, |_| false).is_none());
    }

    #[test]
    fn copy_command_is_ccx_name() {
        assert_eq!(copy_command(&prof(false)), "ccx mm");
    }

    #[test]
    fn child_command_for_anthropic_delegates_to_ccx() {
        assert_eq!(
            child_command(&prof(false)),
            vec!["ccx".to_string(), "mm".to_string()]
        );
    }

    #[test]
    fn child_command_for_router_delegates_to_ccx() {
        let mut p = prof(true);
        p.provider = "ollama".into();
        p.router = Some("ccr".into());
        assert_eq!(child_command(&p), vec!["ccx".to_string(), "mm".to_string()]);
    }
}
