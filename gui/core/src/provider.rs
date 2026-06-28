use serde::{Deserialize, Serialize};
use std::fs;
use std::path::Path;

use crate::config;

#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProviderTemplate {
    pub name: String,
    pub isolate: bool,
    pub base_url: Option<String>,
    pub model: Option<String>,
    pub opus: Option<String>,
    pub sonnet: Option<String>,
    pub haiku: Option<String>,
    // OpenAI-compatible (router) templates carry these instead of base_url.
    #[serde(default)]
    pub router: Option<String>,
    #[serde(default)]
    pub upstream_url: Option<String>,
}

fn t(name: &str, isolate: bool, base: Option<&str>, model: Option<&str>, opus: Option<&str>, sonnet: Option<&str>, haiku: Option<&str>) -> ProviderTemplate {
    ProviderTemplate {
        name: name.into(),
        isolate,
        base_url: base.map(String::from),
        model: model.map(String::from),
        opus: opus.map(String::from),
        sonnet: sonnet.map(String::from),
        haiku: haiku.map(String::from),
        ..Default::default()
    }
}

// OpenAI-compatible router template (CCX_ROUTER=builtin).
fn r(name: &str, upstream: Option<&str>, model: Option<&str>) -> ProviderTemplate {
    ProviderTemplate {
        name: name.into(),
        isolate: true,
        router: Some("builtin".into()),
        upstream_url: upstream.map(String::from),
        model: model.map(String::from),
        ..Default::default()
    }
}

pub fn builtins() -> Vec<ProviderTemplate> {
    vec![
        t("claude", false, None, Some("opusplan"), None, None, None),
        t("minimax", true, Some("https://api.minimax.io/anthropic"), Some("MiniMax-M2"), Some("MiniMax-M2"), Some("MiniMax-M2"), Some("MiniMax-M2")),
        t("glm", true, Some("https://api.z.ai/api/anthropic"), Some("glm-4.6"), Some("glm-4.6"), Some("glm-4.6"), Some("glm-4.5-air")),
        t("deepseek", true, Some("https://api.deepseek.com/anthropic"), Some("deepseek-chat"), Some("deepseek-reasoner"), Some("deepseek-chat"), Some("deepseek-chat")),
        t("kimi", true, Some("https://api.moonshot.ai/anthropic"), Some("kimi-k2-0905-preview"), Some("kimi-k2-0905-preview"), Some("kimi-k2-0905-preview"), Some("kimi-k2-turbo-preview")),
        r("openai", Some("https://api.openai.com/v1"), Some("gpt-4o")),
        r("openrouter", Some("https://openrouter.ai/api/v1"), Some("qwen/qwen3-coder")),
        r("ollama", Some("http://localhost:11434/v1"), Some("qwen2.5-coder:32b")),
        r("vllm", Some("http://localhost:8000/v1"), None),
        r("lmstudio", Some("http://localhost:1234/v1"), None),
        r("sakana", Some("https://api.sakana.ai/v1"), Some("fugu")),
        r("custom-oai", None, None),
    ]
}

fn parse_template(name: &str, content: &str) -> ProviderTemplate {
    let mut tpl = ProviderTemplate { name: name.into(), ..Default::default() };
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
            "CCX_ISOLATE" => tpl.isolate = v == "true",
            "ANTHROPIC_BASE_URL" => tpl.base_url = Some(v.to_string()),
            "ANTHROPIC_MODEL" => tpl.model = Some(v.to_string()),
            "ANTHROPIC_DEFAULT_OPUS_MODEL" => tpl.opus = Some(v.to_string()),
            "ANTHROPIC_DEFAULT_SONNET_MODEL" => tpl.sonnet = Some(v.to_string()),
            "ANTHROPIC_DEFAULT_HAIKU_MODEL" => tpl.haiku = Some(v.to_string()),
            "CCX_ROUTER" => tpl.router = Some(v.to_string()),
            "CCX_UPSTREAM_BASE_URL" => tpl.upstream_url = Some(v.to_string()),
            _ => {}
        }
    }
    tpl
}

pub fn list_providers(home: &Path) -> Vec<ProviderTemplate> {
    let mut out = builtins();
    let dir = config::providers_dir(home);
    if let Ok(entries) = fs::read_dir(&dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) != Some("tmpl") {
                continue;
            }
            let name = match path.file_stem().and_then(|s| s.to_str()) {
                Some(n) => n.to_string(),
                None => continue,
            };
            let content = match fs::read_to_string(&path) {
                Ok(c) => c,
                Err(_) => continue,
            };
            let parsed = parse_template(&name, &content);
            match out.iter_mut().find(|x| x.name == name) {
                Some(existing) => *existing = parsed,
                None => out.push(parsed),
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builtins_include_the_five_with_claude_first() {
        let b = builtins();
        assert_eq!(b[0].name, "claude");
        let names: Vec<&str> = b.iter().map(|t| t.name.as_str()).collect();
        for want in ["claude", "minimax", "glm", "deepseek", "kimi"] {
            assert!(names.contains(&want), "missing {want}");
        }
        let glm = b.iter().find(|t| t.name == "glm").unwrap();
        assert_eq!(glm.base_url.as_deref(), Some("https://api.z.ai/api/anthropic"));
        assert_eq!(glm.haiku.as_deref(), Some("glm-4.5-air"));
        assert_eq!(b.iter().find(|t| t.name == "claude").unwrap().model.as_deref(), Some("opusplan"));
    }

    #[test]
    fn builtins_include_openai_compatible_router_providers() {
        let b = builtins();
        let names: Vec<&str> = b.iter().map(|t| t.name.as_str()).collect();
        for want in ["openai", "openrouter", "ollama", "vllm", "lmstudio", "custom-oai"] {
            assert!(names.contains(&want), "missing {want}");
        }
        let ollama = b.iter().find(|t| t.name == "ollama").unwrap();
        assert_eq!(ollama.router.as_deref(), Some("builtin"));
        assert_eq!(ollama.upstream_url.as_deref(), Some("http://localhost:11434/v1"));
        // anthropic providers carry no router marker
        assert_eq!(b.iter().find(|t| t.name == "glm").unwrap().router, None);
    }

    #[test]
    fn parses_router_template_from_disk() {
        let tpl = parse_template(
            "ollama",
            "CCX_ROUTER=builtin\nCCX_ISOLATE=true\nCCX_UPSTREAM_BASE_URL=http://localhost:11434/v1\nANTHROPIC_MODEL=qwen2.5-coder:7b\n",
        );
        assert_eq!(tpl.router.as_deref(), Some("builtin"));
        assert_eq!(tpl.upstream_url.as_deref(), Some("http://localhost:11434/v1"));
        assert_eq!(tpl.model.as_deref(), Some("qwen2.5-coder:7b"));
    }

    #[test]
    fn disk_templates_override_builtins() {
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        let pdir = config::providers_dir(home);
        fs::create_dir_all(&pdir).unwrap();
        fs::write(pdir.join("glm.tmpl"), "CCX_ISOLATE=true\nANTHROPIC_BASE_URL=https://custom.example/anthropic\nANTHROPIC_MODEL=glm-9\n").unwrap();
        let list = list_providers(home);
        let glm = list.iter().find(|t| t.name == "glm").unwrap();
        assert_eq!(glm.base_url.as_deref(), Some("https://custom.example/anthropic"));
        assert_eq!(glm.model.as_deref(), Some("glm-9"));
        assert!(list.iter().any(|t| t.name == "minimax"));
    }
}
