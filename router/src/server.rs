//! axum HTTP server: accepts the Anthropic Messages API, forwards a translated
//! request to the configured OpenAI-compatible upstream, and translates the
//! response back (streaming or not).

use std::sync::atomic::{AtomicU64, Ordering};

use axum::{
    body::{Body, Bytes},
    extract::State,
    http::{header, HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use futures::{SinkExt, StreamExt};
use serde_json::json;

use crate::translate;
use crate::translate::stream::StreamTranslator;
use crate::types::{ChatChunk, ChatResponse, MessagesRequest, StringOrBlocks, ContentBlock};

#[derive(Clone)]
pub struct AppState {
    pub upstream_url: String, // base, e.g. http://localhost:11434/v1
    pub upstream_key: Option<String>,
    pub api_key: String,
    pub client: reqwest::Client,
}

pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/health", get(|| async { StatusCode::OK }))
        .route("/v1/messages", post(messages))
        .route("/v1/messages/count_tokens", post(count_tokens))
        .with_state(state)
}

fn check_auth(headers: &HeaderMap, key: &str) -> Result<(), StatusCode> {
    let bearer = headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .map(|s| s == format!("Bearer {key}"))
        .unwrap_or(false);
    let xapikey = headers
        .get("x-api-key")
        .and_then(|v| v.to_str().ok())
        .map(|s| s == key)
        .unwrap_or(false);
    if bearer || xapikey {
        Ok(())
    } else {
        Err(StatusCode::UNAUTHORIZED)
    }
}

fn next_id() -> String {
    static N: AtomicU64 = AtomicU64::new(1);
    format!("msg_ccx{}", N.fetch_add(1, Ordering::Relaxed))
}

async fn messages(State(st): State<AppState>, headers: HeaderMap, body: Bytes) -> Response {
    if let Err(code) = check_auth(&headers, &st.api_key) {
        return code.into_response();
    }
    let req: MessagesRequest = match serde_json::from_slice(&body) {
        Ok(r) => r,
        Err(e) => return (StatusCode::BAD_REQUEST, format!("bad request: {e}")).into_response(),
    };
    let streaming = req.stream.unwrap_or(false);
    let model = req.model.clone();
    let chat = translate::request::to_openai(&req);

    let url = format!("{}/chat/completions", st.upstream_url.trim_end_matches('/'));
    let mut rb = st.client.post(&url).json(&chat);
    if let Some(k) = &st.upstream_key {
        rb = rb.bearer_auth(k);
    }
    let resp = match rb.send().await {
        Ok(r) => r,
        Err(e) => return (StatusCode::BAD_GATEWAY, format!("upstream error: {e}")).into_response(),
    };
    if !resp.status().is_success() {
        let code = StatusCode::from_u16(resp.status().as_u16()).unwrap_or(StatusCode::BAD_GATEWAY);
        let text = resp.text().await.unwrap_or_default();
        return (code, text).into_response();
    }

    if streaming {
        stream_response(resp, model)
    } else {
        let oresp: ChatResponse = match resp.json().await {
            Ok(r) => r,
            Err(e) => {
                return (StatusCode::BAD_GATEWAY, format!("bad upstream json: {e}")).into_response()
            }
        };
        let aresp = translate::response::to_anthropic(&oresp, &model);
        Json(aresp).into_response()
    }
}

fn stream_response(resp: reqwest::Response, model: String) -> Response {
    let (mut tx, rx) = futures::channel::mpsc::channel::<Result<Bytes, std::io::Error>>(32);
    tokio::spawn(async move {
        let mut tr = StreamTranslator::new(model, next_id());
        let _ = tx.send(Ok(Bytes::from(tr.start().to_wire()))).await;

        let mut upstream = resp.bytes_stream();
        let mut buf = String::new();
        let mut done = false;
        while let Some(item) = upstream.next().await {
            let chunk = match item {
                Ok(b) => b,
                Err(_) => break,
            };
            buf.push_str(&String::from_utf8_lossy(&chunk));
            while let Some(pos) = buf.find('\n') {
                let line: String = buf.drain(..=pos).collect();
                let line = line.trim();
                if let Some(data) = line.strip_prefix("data:") {
                    let data = data.trim();
                    if data == "[DONE]" {
                        done = true;
                        break;
                    }
                    if data.is_empty() {
                        continue;
                    }
                    if let Ok(c) = serde_json::from_str::<ChatChunk>(data) {
                        for ev in tr.push(&c) {
                            let _ = tx.send(Ok(Bytes::from(ev.to_wire()))).await;
                        }
                    }
                }
            }
            if done {
                break;
            }
        }
        for ev in tr.finish() {
            let _ = tx.send(Ok(Bytes::from(ev.to_wire()))).await;
        }
    });

    (
        [(header::CONTENT_TYPE, "text/event-stream")],
        Body::from_stream(rx),
    )
        .into_response()
}

async fn count_tokens(State(st): State<AppState>, headers: HeaderMap, body: Bytes) -> Response {
    if let Err(code) = check_auth(&headers, &st.api_key) {
        return code.into_response();
    }
    let req: MessagesRequest = match serde_json::from_slice(&body) {
        Ok(r) => r,
        Err(e) => return (StatusCode::BAD_REQUEST, format!("bad request: {e}")).into_response(),
    };
    let mut chars = 0usize;
    if let Some(sys) = &req.system {
        chars += text_len(sys);
    }
    for m in &req.messages {
        chars += text_len(&m.content);
    }
    // Rough heuristic; Claude Code only needs a non-crashing estimate.
    let tokens = (chars / 4).max(1);
    Json(json!({ "input_tokens": tokens })).into_response()
}

fn text_len(s: &StringOrBlocks) -> usize {
    match s {
        StringOrBlocks::Text(t) => t.len(),
        StringOrBlocks::Blocks(bs) => bs
            .iter()
            .map(|b| match b {
                ContentBlock::Text { text } => text.len(),
                ContentBlock::ToolResult { content: Some(c), .. } => text_len(c),
                _ => 0,
            })
            .sum(),
    }
}
