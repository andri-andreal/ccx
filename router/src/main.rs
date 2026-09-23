//! ccx-router binary entry point. Config comes from flags (non-secret) and env
//! (secrets): `--port`, `--upstream-url`, repeatable `--fallback-url`;
//! `CCX_ROUTER_UPSTREAM_KEY`, `CCX_ROUTER_FALLBACK_KEYS`, and
//! `CCX_ROUTER_API_KEY`.

use std::env;
use std::time::Duration;

use ccx_router::server::{
    app, validate_upstream_url, AppState, RouterConfig, StreamUsageMode, Upstream,
};
use ccx_router::types::ProviderPinning;

const HELP: &str = r#"ccx-router — Anthropic Messages to OpenAI Chat Completions bridge

Usage:
  ccx-router --port <PORT> --upstream-url <BASE_URL> [--fallback-url <BASE_URL>]...

Required environment:
  CCX_ROUTER_API_KEY                  local client authentication token

Upstream authentication and fallback:
  CCX_ROUTER_UPSTREAM_KEY             primary upstream bearer token
  CCX_ROUTER_FALLBACK_URLS            comma-separated additional fallback URLs
  CCX_ROUTER_FALLBACK_KEYS            positional fallback bearer tokens; empty sends no auth
  CCX_ROUTER_ATTEMPTS_PER_UPSTREAM    1..10 (default: 1)

OpenRouter provider pinning (positional; `;` separates upstreams, `,` separates
slugs within one upstream; position 0 is the primary upstream):
  CCX_ROUTER_PROVIDER_ONLY            e.g. groq,fireworks;;together
  CCX_ROUTER_PROVIDER_ORDER           e.g. groq,fireworks
  CCX_ROUTER_REQUIRE_PARAMETERS       e.g. 1;;1  (only providers supporting the
                                      request's parameters, tools included)

Timeouts and streaming:
  CCX_ROUTER_CONNECT_TIMEOUT_MS       default: 5000
  CCX_ROUTER_RESPONSE_TIMEOUT_MS      default: 30000
  CCX_ROUTER_STREAM_IDLE_TIMEOUT_MS   default: 120000
  CCX_ROUTER_STREAM_PING_INTERVAL_MS  default: 15000
  CCX_ROUTER_STREAM_USAGE             auto|on|off (default: auto)
  CCX_ROUTER_LOG                      json|off (default: json)
"#;

struct Config {
    port: u16,
    upstream_url: String,
    fallback_urls: Vec<String>,
}

fn parse_args() -> Result<Config, String> {
    parse_args_from(env::args().skip(1))
}

fn parse_args_from(args: impl IntoIterator<Item = String>) -> Result<Config, String> {
    let mut port: Option<u16> = None;
    let mut upstream_url: Option<String> = None;
    let mut fallback_urls = Vec::new();
    let mut args = args.into_iter();
    while let Some(a) = args.next() {
        match a.as_str() {
            "--port" => {
                port = Some(
                    args.next()
                        .ok_or("--port needs a value")?
                        .parse()
                        .map_err(|_| "invalid --port")?,
                );
            }
            "--upstream-url" => {
                upstream_url = Some(args.next().ok_or("--upstream-url needs a value")?);
            }
            "--fallback-url" => {
                fallback_urls.push(args.next().ok_or("--fallback-url needs a value")?);
            }
            other => return Err(format!("unknown argument: {other}")),
        }
    }
    let port = port.ok_or("--port is required")?;
    if port == 0 {
        return Err("--port must be greater than zero".into());
    }
    Ok(Config {
        port,
        upstream_url: upstream_url.ok_or("--upstream-url is required")?,
        fallback_urls,
    })
}

fn env_duration_ms(name: &str, default_ms: u64) -> Result<Duration, String> {
    let raw = match env::var(name) {
        Ok(raw) if !raw.trim().is_empty() => raw,
        _ => return Ok(Duration::from_millis(default_ms)),
    };
    let milliseconds: u64 = raw
        .parse()
        .map_err(|_| format!("{name} must be a positive integer in milliseconds"))?;
    if milliseconds == 0 {
        return Err(format!("{name} must be greater than zero"));
    }
    Ok(Duration::from_millis(milliseconds))
}

fn parse_fallback_keys(raw: Option<String>) -> Vec<String> {
    match raw {
        Some(raw) if !raw.is_empty() => raw.split(',').map(str::to_owned).collect(),
        _ => Vec::new(),
    }
}

fn fallback_key(keys: &[String], index: usize) -> Option<String> {
    keys.get(index).filter(|key| !key.is_empty()).cloned()
}

#[derive(Clone, Copy)]
enum PinControl {
    Only,
    Order,
    RequireParameters,
}

/// Positions are separated by `;` and align to the upstream chain: position 0
/// is the primary upstream, position N the Nth fallback. Slugs within one
/// position are comma-separated, which is why a comma cannot also separate
/// positions the way it does for `CCX_ROUTER_FALLBACK_KEYS`. An empty position
/// leaves that upstream unpinned, so a pinned OpenRouter primary can sit in
/// front of a plain Ollama fallback that must not receive the field at all.
fn parse_provider_pinning(
    only: Option<&str>,
    order: Option<&str>,
    require_parameters: Option<&str>,
    upstream_count: usize,
) -> Result<Vec<Option<ProviderPinning>>, String> {
    let mut pins: Vec<Option<ProviderPinning>> = vec![None; upstream_count];
    for (raw, name, control) in [
        (only, "CCX_ROUTER_PROVIDER_ONLY", PinControl::Only),
        (order, "CCX_ROUTER_PROVIDER_ORDER", PinControl::Order),
        (
            require_parameters,
            "CCX_ROUTER_REQUIRE_PARAMETERS",
            PinControl::RequireParameters,
        ),
    ] {
        let Some(raw) = raw.filter(|raw| !raw.is_empty()) else {
            continue;
        };
        let positions: Vec<&str> = raw.split(';').collect();
        if positions.len() > upstream_count {
            return Err(format!(
                "{name} has more positions than configured upstreams ({} vs {upstream_count})",
                positions.len()
            ));
        }
        for (index, slot) in positions.into_iter().enumerate() {
            match control {
                PinControl::Only | PinControl::Order => {
                    let slugs = parse_slugs(slot, name)?;
                    if slugs.is_empty() {
                        continue;
                    }
                    let pin = pins[index].get_or_insert_with(ProviderPinning::default);
                    match control {
                        PinControl::Only => pin.only = Some(slugs),
                        _ => pin.order = Some(slugs),
                    }
                }
                PinControl::RequireParameters => match slot.trim() {
                    "" => continue,
                    "1" | "true" => {
                        pins[index]
                            .get_or_insert_with(ProviderPinning::default)
                            .require_parameters = Some(true);
                    }
                    other => {
                        return Err(format!(
                            "{name} entries must be 1, true, or empty; got '{other}'"
                        ))
                    }
                },
            }
        }
    }
    Ok(pins)
}

/// Slugs are opaque to the router, so the charset is the only thing worth
/// checking: it keeps a typo from travelling to the provider as a silent
/// mis-route, and keeps the value printable in the structured logs.
fn parse_slugs(slot: &str, name: &str) -> Result<Vec<String>, String> {
    let mut slugs = Vec::new();
    for slug in slot.split(',').map(str::trim).filter(|s| !s.is_empty()) {
        if !slug
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
        {
            return Err(format!(
                "{name} contains an invalid provider slug: '{slug}'"
            ));
        }
        slugs.push(slug.to_owned());
    }
    Ok(slugs)
}

fn fail(message: impl std::fmt::Display) -> ! {
    eprintln!("ccx-router: {message}");
    std::process::exit(2);
}

#[tokio::main]
async fn main() {
    if env::args().skip(1).any(|a| a == "--version" || a == "-V") {
        println!("ccx-router {}", env!("CARGO_PKG_VERSION"));
        return;
    }
    if env::args().skip(1).any(|a| a == "--help" || a == "-h") {
        print!("{HELP}");
        return;
    }
    let cfg = match parse_args() {
        Ok(c) => c,
        Err(e) => fail(e),
    };

    let api_key = env::var("CCX_ROUTER_API_KEY").unwrap_or_default();
    if api_key.is_empty() {
        fail("CCX_ROUTER_API_KEY must be set");
    }
    let upstream_key = match env::var("CCX_ROUTER_UPSTREAM_KEY") {
        Ok(k) if !k.is_empty() => Some(k),
        _ => None,
    };

    let mut fallback_urls = cfg.fallback_urls;
    if let Ok(urls) = env::var("CCX_ROUTER_FALLBACK_URLS") {
        fallback_urls.extend(
            urls.split(',')
                .map(str::trim)
                .filter(|url| !url.is_empty())
                .map(str::to_owned),
        );
    }
    if let Err(error) = validate_upstream_url(&cfg.upstream_url, "--upstream-url") {
        fail(error);
    }
    for (index, url) in fallback_urls.iter().enumerate() {
        if let Err(error) = validate_upstream_url(url, &format!("fallback URL #{}", index + 1)) {
            fail(error);
        }
    }

    let fallback_keys = parse_fallback_keys(env::var("CCX_ROUTER_FALLBACK_KEYS").ok());
    if fallback_keys.len() > fallback_urls.len() {
        fail("CCX_ROUTER_FALLBACK_KEYS has more entries than configured fallback URLs");
    }
    let mut upstreams = vec![Upstream {
        base_url: cfg.upstream_url,
        api_key: upstream_key.clone(),
        provider: None,
    }];
    upstreams.extend(fallback_urls.into_iter().enumerate().map(|(index, url)| {
        // Never reuse a credential across origins. Fallback credentials must
        // be explicitly configured at the matching position.
        let key = fallback_key(&fallback_keys, index);
        Upstream {
            base_url: url,
            api_key: key,
            provider: None,
        }
    }));

    let pinning_only = env::var("CCX_ROUTER_PROVIDER_ONLY").ok();
    let pinning_order = env::var("CCX_ROUTER_PROVIDER_ORDER").ok();
    let pinning_require = env::var("CCX_ROUTER_REQUIRE_PARAMETERS").ok();
    let pinning = parse_provider_pinning(
        pinning_only.as_deref(),
        pinning_order.as_deref(),
        pinning_require.as_deref(),
        upstreams.len(),
    )
    .unwrap_or_else(|error| fail(error));
    for (upstream, pin) in upstreams.iter_mut().zip(pinning) {
        upstream.provider = pin;
    }

    let attempts_per_upstream = env::var("CCX_ROUTER_ATTEMPTS_PER_UPSTREAM")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .map(|value| {
            value
                .parse::<usize>()
                .map_err(|_| "CCX_ROUTER_ATTEMPTS_PER_UPSTREAM must be an integer".to_owned())
        })
        .transpose()
        .unwrap_or_else(|error| fail(error))
        .unwrap_or(1);
    if !(1..=10).contains(&attempts_per_upstream) {
        fail("CCX_ROUTER_ATTEMPTS_PER_UPSTREAM must be between 1 and 10");
    }
    let connect_timeout =
        env_duration_ms("CCX_ROUTER_CONNECT_TIMEOUT_MS", 5_000).unwrap_or_else(|error| fail(error));
    let response_timeout = env_duration_ms("CCX_ROUTER_RESPONSE_TIMEOUT_MS", 30_000)
        .unwrap_or_else(|error| fail(error));
    let stream_idle_timeout = env_duration_ms("CCX_ROUTER_STREAM_IDLE_TIMEOUT_MS", 120_000)
        .unwrap_or_else(|error| fail(error));
    let stream_ping_interval = env_duration_ms("CCX_ROUTER_STREAM_PING_INTERVAL_MS", 15_000)
        .unwrap_or_else(|error| fail(error));
    if stream_ping_interval >= stream_idle_timeout {
        fail("CCX_ROUTER_STREAM_PING_INTERVAL_MS must be less than the stream idle timeout");
    }
    let stream_usage = match env::var("CCX_ROUTER_STREAM_USAGE")
        .unwrap_or_else(|_| "auto".into())
        .to_ascii_lowercase()
        .as_str()
    {
        "auto" => StreamUsageMode::Auto,
        "on" => StreamUsageMode::On,
        "off" => StreamUsageMode::Off,
        _ => fail("CCX_ROUTER_STREAM_USAGE must be auto, on, or off"),
    };
    let structured_logs = match env::var("CCX_ROUTER_LOG")
        .unwrap_or_else(|_| "json".into())
        .to_ascii_lowercase()
        .as_str()
    {
        "json" | "info" | "1" | "true" => true,
        "off" | "0" | "false" => false,
        _ => fail("CCX_ROUTER_LOG must be json or off"),
    };
    let client = reqwest::Client::builder()
        .connect_timeout(connect_timeout)
        .build()
        .unwrap_or_else(|error| fail(format!("cannot create HTTP client: {error}")));

    let state = AppState {
        upstreams,
        api_key,
        client,
        config: RouterConfig {
            attempts_per_upstream,
            response_timeout,
            stream_idle_timeout,
            stream_ping_interval,
            stream_usage,
            structured_logs,
            ..RouterConfig::default()
        },
    };

    let addr = format!("127.0.0.1:{}", cfg.port);
    let listener = match tokio::net::TcpListener::bind(&addr).await {
        Ok(l) => l,
        Err(e) => {
            eprintln!("ccx-router: cannot bind {addr}: {e}");
            std::process::exit(1);
        }
    };
    if structured_logs {
        eprintln!(
            "{}",
            serde_json::json!({
                "component": "ccx-router",
                "event": "listening",
                "address": addr,
                "upstream_count": state.upstreams.len(),
            })
        );
    }
    if let Err(e) = axum::serve(listener, app(state)).await {
        eprintln!("ccx-router: server error: {e}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unset_or_empty_fallback_keys_is_an_empty_list() {
        assert!(parse_fallback_keys(None).is_empty());
        assert!(parse_fallback_keys(Some(String::new())).is_empty());
    }

    #[test]
    fn fallback_keys_preserve_empty_positional_entries() {
        assert_eq!(
            parse_fallback_keys(Some("first,,third".into())),
            vec!["first", "", "third"]
        );
    }

    #[test]
    fn missing_or_blank_fallback_key_never_inherits_primary() {
        let keys = parse_fallback_keys(Some(",explicit".into()));
        assert_eq!(fallback_key(&keys, 0), None);
        assert_eq!(fallback_key(&keys, 1).as_deref(), Some("explicit"));
        assert_eq!(fallback_key(&keys, 2), None);
    }

    #[test]
    fn remote_http_is_rejected_but_strict_loopback_is_allowed() {
        assert!(validate_upstream_url("http://example.com/v1", "url").is_err());
        assert!(validate_upstream_url("http://127.evil/v1", "url").is_err());
        assert!(validate_upstream_url("http://localhost:11434/v1", "url").is_ok());
        assert!(validate_upstream_url("http://127.42.0.9:8000/v1", "url").is_ok());
        assert!(validate_upstream_url("http://[::1]:8000/v1", "url").is_ok());
        assert!(validate_upstream_url("https://example.com/v1", "url").is_ok());
    }

    #[test]
    fn repeated_fallback_flags_preserve_order() {
        let config = parse_args_from(
            [
                "--port",
                "8080",
                "--upstream-url",
                "https://primary.test/v1",
                "--fallback-url",
                "https://first.test/v1",
                "--fallback-url",
                "http://127.0.0.1:11434/v1",
            ]
            .into_iter()
            .map(str::to_owned),
        )
        .unwrap();
        assert_eq!(config.port, 8080);
        assert_eq!(
            config.fallback_urls,
            vec!["https://first.test/v1", "http://127.0.0.1:11434/v1"]
        );
    }

    #[test]
    fn positional_pinning_aligns_to_upstreams_and_skips_empty_positions() {
        let pins = parse_provider_pinning(Some("groq,fireworks;;together"), None, None, 3).unwrap();
        assert_eq!(
            serde_json::to_value(&pins).unwrap(),
            serde_json::json!([
                {"only": ["groq", "fireworks"]},
                null,
                {"only": ["together"]},
            ])
        );
    }

    #[test]
    fn all_three_controls_merge_into_one_provider_object() {
        let pins =
            parse_provider_pinning(Some("groq"), Some("groq,fireworks"), Some("1"), 1).unwrap();
        assert_eq!(
            serde_json::to_value(&pins).unwrap(),
            serde_json::json!([{
                "only": ["groq"],
                "order": ["groq", "fireworks"],
                "require_parameters": true,
            }])
        );
    }

    #[test]
    fn require_parameters_is_positional_like_the_slug_lists() {
        let pins = parse_provider_pinning(None, None, Some("1;;1"), 3).unwrap();
        assert_eq!(
            serde_json::to_value(&pins).unwrap(),
            serde_json::json!([
                {"require_parameters": true},
                null,
                {"require_parameters": true},
            ])
        );
    }

    #[test]
    fn more_pinning_positions_than_upstreams_is_rejected() {
        let error = parse_provider_pinning(Some("groq;together"), None, None, 1)
            .err()
            .unwrap();
        assert!(
            error.contains("more positions than configured upstreams"),
            "{error}"
        );
    }

    #[test]
    fn a_pinning_position_past_the_chain_is_rejected_for_every_control() {
        assert!(parse_provider_pinning(None, Some("groq;together"), None, 1).is_err());
        assert!(parse_provider_pinning(None, None, Some("1;1"), 1).is_err());
    }

    #[test]
    fn provider_slugs_outside_the_allowed_charset_are_rejected() {
        let error = parse_provider_pinning(Some("groq,bad slug"), None, None, 1)
            .err()
            .unwrap();
        assert!(error.contains("invalid provider slug"), "{error}");
    }

    #[test]
    fn a_non_boolean_require_parameters_entry_is_rejected() {
        assert!(parse_provider_pinning(None, None, Some("yes"), 1).is_err());
    }

    #[test]
    fn unset_pinning_leaves_every_upstream_unpinned() {
        let pins = parse_provider_pinning(None, None, None, 2).unwrap();
        assert_eq!(
            serde_json::to_value(&pins).unwrap(),
            serde_json::json!([null, null])
        );
    }

    #[test]
    fn zero_port_is_rejected() {
        let error = parse_args_from(
            ["--port", "0", "--upstream-url", "https://primary.test/v1"]
                .into_iter()
                .map(str::to_owned),
        )
        .err()
        .unwrap();
        assert!(error.contains("greater than zero"));
    }
}
