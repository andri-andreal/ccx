//! ccx-router binary entry point. Config comes from flags (non-secret) and env
//! (secrets): `--port`, `--upstream-url`; `CCX_ROUTER_UPSTREAM_KEY`,
//! `CCX_ROUTER_API_KEY`.

use std::env;

use ccx_router::server::{app, AppState};

struct Config {
    port: u16,
    upstream_url: String,
}

fn parse_args() -> Result<Config, String> {
    let mut port: Option<u16> = None;
    let mut upstream_url: Option<String> = None;
    let mut args = env::args().skip(1);
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
            other => return Err(format!("unknown argument: {other}")),
        }
    }
    Ok(Config {
        port: port.ok_or("--port is required")?,
        upstream_url: upstream_url.ok_or("--upstream-url is required")?,
    })
}

#[tokio::main]
async fn main() {
    let cfg = match parse_args() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("ccx-router: {e}");
            std::process::exit(2);
        }
    };

    let api_key = env::var("CCX_ROUTER_API_KEY").unwrap_or_default();
    if api_key.is_empty() {
        eprintln!("ccx-router: CCX_ROUTER_API_KEY must be set");
        std::process::exit(2);
    }
    let upstream_key = match env::var("CCX_ROUTER_UPSTREAM_KEY") {
        Ok(k) if !k.is_empty() => Some(k),
        _ => None,
    };

    let state = AppState {
        upstream_url: cfg.upstream_url,
        upstream_key,
        api_key,
        client: reqwest::Client::new(),
    };

    let addr = format!("127.0.0.1:{}", cfg.port);
    let listener = match tokio::net::TcpListener::bind(&addr).await {
        Ok(l) => l,
        Err(e) => {
            eprintln!("ccx-router: cannot bind {addr}: {e}");
            std::process::exit(1);
        }
    };
    eprintln!("ccx-router: listening on http://{addr}");
    if let Err(e) = axum::serve(listener, app(state)).await {
        eprintln!("ccx-router: server error: {e}");
        std::process::exit(1);
    }
}
