//! End-to-end: ccx-router app forwarding to a mock OpenAI-compatible upstream.

use axum::{
    body::Bytes,
    http::header,
    response::IntoResponse,
    routing::post,
    Json, Router,
};
use ccx_router::server::{app, AppState};
use serde_json::{json, Value};

async fn spawn(router: Router) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    tokio::spawn(async move {
        axum::serve(listener, router).await.unwrap();
    });
    format!("http://127.0.0.1:{port}")
}

fn mock_upstream() -> Router {
    Router::new().route(
        "/v1/chat/completions",
        post(|body: Bytes| async move {
            let v: Value = serde_json::from_slice(&body).unwrap_or_else(|_| json!({}));
            if v["stream"].as_bool().unwrap_or(false) {
                let sse = concat!(
                    "data: {\"choices\":[{\"delta\":{\"content\":\"hi\"}}]}\n\n",
                    "data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n",
                    "data: [DONE]\n\n"
                );
                ([(header::CONTENT_TYPE, "text/event-stream")], sse).into_response()
            } else {
                Json(json!({
                    "id": "u1",
                    "choices": [{"message": {"content": "hello from upstream"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2}
                }))
                .into_response()
            }
        }),
    )
}

async fn start_router() -> String {
    let upstream = spawn(mock_upstream()).await;
    let state = AppState {
        upstream_url: format!("{upstream}/v1"),
        upstream_key: None,
        api_key: "secret".into(),
        client: reqwest::Client::new(),
    };
    spawn(app(state)).await
}

#[tokio::test(flavor = "multi_thread")]
async fn non_streaming_round_trip() {
    let base = start_router().await;
    let client = reqwest::Client::new();
    let resp = client
        .post(format!("{base}/v1/messages"))
        .bearer_auth("secret")
        .json(&json!({"model":"m","max_tokens":100,"messages":[{"role":"user","content":"hi"}]}))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200);
    let body: Value = resp.json().await.unwrap();
    assert_eq!(body["type"], "message");
    assert_eq!(body["content"][0]["type"], "text");
    assert_eq!(body["content"][0]["text"], "hello from upstream");
    assert_eq!(body["stop_reason"], "end_turn");
    assert_eq!(body["usage"]["input_tokens"], 3);
}

#[tokio::test(flavor = "multi_thread")]
async fn streaming_round_trip() {
    let base = start_router().await;
    let client = reqwest::Client::new();
    let resp = client
        .post(format!("{base}/v1/messages"))
        .bearer_auth("secret")
        .json(&json!({"model":"m","max_tokens":100,"stream":true,"messages":[{"role":"user","content":"hi"}]}))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200);
    assert_eq!(
        resp.headers().get(header::CONTENT_TYPE).unwrap(),
        "text/event-stream"
    );
    let text = resp.text().await.unwrap();
    assert!(text.contains("event: message_start"), "got: {text}");
    assert!(text.contains("event: content_block_start"));
    assert!(text.contains("text_delta"));
    assert!(text.contains("event: message_stop"));
}

#[tokio::test(flavor = "multi_thread")]
async fn rejects_missing_auth() {
    let base = start_router().await;
    let client = reqwest::Client::new();
    let resp = client
        .post(format!("{base}/v1/messages"))
        .json(&json!({"model":"m","messages":[{"role":"user","content":"hi"}]}))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 401);
}

#[tokio::test(flavor = "multi_thread")]
async fn health_ok() {
    let base = start_router().await;
    let resp = reqwest::get(format!("{base}/health")).await.unwrap();
    assert_eq!(resp.status(), 200);
}
