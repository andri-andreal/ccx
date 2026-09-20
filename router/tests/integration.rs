//! End-to-end conformance tests: ccx-router forwarding to mock
//! OpenAI-compatible upstreams.

use std::convert::Infallible;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use axum::{
    body::{Body, Bytes},
    http::{header, HeaderMap, HeaderValue, StatusCode},
    response::{IntoResponse, Response},
    routing::post,
    Json, Router,
};
use ccx_router::server::{app, AppState, RouterConfig, Upstream};
use ccx_router::types::ProviderPinning;
use futures::stream;
use serde_json::{json, Value};

async fn spawn(router: Router) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    tokio::spawn(async move {
        axum::serve(listener, router).await.unwrap();
    });
    format!("http://127.0.0.1:{port}")
}

fn success(text: &str) -> Response {
    let mut response = Json(json!({
        "id": "u1",
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2}
    }))
    .into_response();
    response.headers_mut().insert(
        "x-request-id",
        HeaderValue::from_static("upstream-request-1"),
    );
    response
}

fn mock_upstream() -> Router {
    Router::new().route(
        "/v1/chat/completions",
        post(|body: Bytes| async move {
            let value: Value = serde_json::from_slice(&body).unwrap_or_else(|_| json!({}));
            if value["stream"].as_bool().unwrap_or(false) {
                assert_eq!(value["stream_options"]["include_usage"], true);
                let sse = concat!(
                    "data: {\"choices\":[{\"delta\":{\"content\":\"hi\"}}]}\n\n",
                    "data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n",
                    "data: {\"choices\":[],\"usage\":{\"prompt_tokens\":3,\"completion_tokens\":1}}\n\n",
                    "data: [DONE]\n\n"
                );
                let mut response =
                    ([(header::CONTENT_TYPE, "text/event-stream")], sse).into_response();
                response.headers_mut().insert(
                    "x-request-id",
                    HeaderValue::from_static("upstream-stream-1"),
                );
                response
            } else {
                success("hello from upstream")
            }
        }),
    )
}

async fn start_router_with(urls: Vec<String>, config: RouterConfig) -> String {
    let upstreams = urls
        .into_iter()
        .map(|base_url| Upstream {
            base_url,
            api_key: None,
            provider: None,
        })
        .collect();
    start_router_with_upstreams(upstreams, config).await
}

async fn start_router_with_upstreams(upstreams: Vec<Upstream>, mut config: RouterConfig) -> String {
    config.structured_logs = false;
    let state = AppState {
        upstreams,
        api_key: "secret".into(),
        client: reqwest::Client::new(),
        config,
    };
    spawn(app(state)).await
}

async fn observed_fallback_authorization(fallback_key: Option<String>) -> Option<String> {
    let primary = spawn(Router::new().route(
        "/v1/chat/completions",
        post(|| async { StatusCode::SERVICE_UNAVAILABLE }),
    ))
    .await;
    let observed = Arc::new(Mutex::new(None));
    let handler_observed = observed.clone();
    let fallback = spawn(Router::new().route(
        "/v1/chat/completions",
        post(move |headers: HeaderMap| {
            let handler_observed = handler_observed.clone();
            async move {
                *handler_observed.lock().unwrap() = headers
                    .get(header::AUTHORIZATION)
                    .and_then(|value| value.to_str().ok())
                    .map(str::to_owned);
                success("fallback auth observed")
            }
        }),
    ))
    .await;
    let config = RouterConfig {
        retry_backoff: Duration::ZERO,
        ..RouterConfig::default()
    };
    let base = start_router_with_upstreams(
        vec![
            Upstream {
                base_url: format!("{primary}/v1"),
                api_key: Some("primary-secret".into()),
                provider: None,
            },
            Upstream {
                base_url: format!("{fallback}/v1"),
                api_key: fallback_key,
                provider: None,
            },
        ],
        config,
    )
    .await;
    let response = post_message(&base, basic_request(false)).await;
    assert_eq!(response.status(), 200);
    let authorization = observed.lock().unwrap().clone();
    authorization
}

/// Records the body each upstream actually received, so per-upstream settings
/// can be asserted on the wire rather than on the config that produced them.
fn body_recording_upstream(
    observed: Arc<Mutex<Option<Value>>>,
    reply: impl Fn() -> Response + Clone + Send + 'static,
) -> Router {
    Router::new().route(
        "/v1/chat/completions",
        post(move |body: Bytes| {
            let observed = observed.clone();
            let reply = reply.clone();
            async move {
                *observed.lock().unwrap() = Some(serde_json::from_slice::<Value>(&body).unwrap());
                reply()
            }
        }),
    )
}

#[tokio::test]
async fn provider_pinning_is_scoped_to_the_upstream_that_configured_it() {
    let primary_observed: Arc<Mutex<Option<Value>>> = Arc::new(Mutex::new(None));
    let fallback_observed: Arc<Mutex<Option<Value>>> = Arc::new(Mutex::new(None));
    let primary = spawn(body_recording_upstream(primary_observed.clone(), || {
        StatusCode::SERVICE_UNAVAILABLE.into_response()
    }))
    .await;
    let fallback = spawn(body_recording_upstream(fallback_observed.clone(), || {
        success("unpinned fallback answered")
    }))
    .await;

    let base = start_router_with_upstreams(
        vec![
            Upstream {
                base_url: format!("{primary}/v1"),
                api_key: None,
                provider: Some(ProviderPinning {
                    only: Some(vec!["groq".into()]),
                    order: None,
                    require_parameters: Some(true),
                }),
            },
            Upstream {
                base_url: format!("{fallback}/v1"),
                api_key: None,
                provider: None,
            },
        ],
        RouterConfig {
            retry_backoff: Duration::ZERO,
            ..RouterConfig::default()
        },
    )
    .await;
    let response = post_message(&base, basic_request(false)).await;
    assert_eq!(response.status(), 200);

    let primary_body = primary_observed.lock().unwrap().clone().unwrap();
    assert_eq!(
        primary_body["provider"],
        json!({"only": ["groq"], "require_parameters": true})
    );
    // The Ollama-style fallback must never see an OpenRouter-only field.
    let fallback_body = fallback_observed.lock().unwrap().clone().unwrap();
    assert!(
        fallback_body.get("provider").is_none(),
        "unpinned upstream received: {fallback_body}"
    );
}

#[tokio::test]
async fn a_stream_options_downgrade_survives_the_move_to_a_differently_pinned_upstream() {
    let calls = Arc::new(AtomicUsize::new(0));
    let counter = calls.clone();
    let primary = spawn(Router::new().route(
        "/v1/chat/completions",
        post(move |body: Bytes| {
            let counter = counter.clone();
            async move {
                counter.fetch_add(1, Ordering::SeqCst);
                let value: Value = serde_json::from_slice(&body).unwrap();
                if value.get("stream_options").is_some() {
                    return (
                        StatusCode::BAD_REQUEST,
                        Json(json!({"error": {"message": "unknown field stream_options"}})),
                    )
                        .into_response();
                }
                StatusCode::SERVICE_UNAVAILABLE.into_response()
            }
        }),
    ))
    .await;
    let fallback_observed: Arc<Mutex<Option<Value>>> = Arc::new(Mutex::new(None));
    let fallback = spawn(body_recording_upstream(fallback_observed.clone(), || {
        (
            [(header::CONTENT_TYPE, "text/event-stream")],
            concat!(
                "data: {\"choices\":[{\"delta\":{\"content\":\"pinned fallback\"}}]}\n\n",
                "data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n",
                "data: [DONE]\n\n"
            ),
        )
            .into_response()
    }))
    .await;

    let base = start_router_with_upstreams(
        vec![
            Upstream {
                base_url: format!("{primary}/v1"),
                api_key: None,
                provider: Some(ProviderPinning {
                    only: Some(vec!["groq".into()]),
                    ..Default::default()
                }),
            },
            Upstream {
                base_url: format!("{fallback}/v1"),
                api_key: None,
                provider: Some(ProviderPinning {
                    order: Some(vec!["together".into()]),
                    ..Default::default()
                }),
            },
        ],
        RouterConfig {
            retry_backoff: Duration::ZERO,
            ..RouterConfig::default()
        },
    )
    .await;

    let response = post_message(&base, basic_request(true)).await;
    assert_eq!(response.status(), 200);
    assert!(response.text().await.unwrap().contains("pinned fallback"));
    assert_eq!(calls.load(Ordering::SeqCst), 2);

    let body = fallback_observed.lock().unwrap().clone().unwrap();
    // The downgrade the primary forced still holds here: pinning must not
    // rebuild the body and lose what earlier attempts learned.
    assert!(body.get("stream_options").is_none(), "{body}");
    // ...and the pinning is this upstream's own, not the primary's.
    assert_eq!(body["provider"], json!({"order": ["together"]}));
}

async fn start_router() -> String {
    let upstream = spawn(mock_upstream()).await;
    start_router_with(vec![format!("{upstream}/v1")], RouterConfig::default()).await
}

async fn post_message(base: &str, body: Value) -> reqwest::Response {
    reqwest::Client::new()
        .post(format!("{base}/v1/messages"))
        .bearer_auth("secret")
        .json(&body)
        .send()
        .await
        .unwrap()
}

fn basic_request(streaming: bool) -> Value {
    json!({
        "model": "m",
        "max_tokens": 100,
        "stream": streaming,
        "messages": [{"role": "user", "content": "hi"}]
    })
}

#[tokio::test(flavor = "multi_thread")]
async fn non_streaming_round_trip_has_usage_and_request_ids() {
    let base = start_router().await;
    let response = post_message(&base, basic_request(false)).await;
    assert_eq!(response.status(), 200);
    assert!(response.headers().contains_key("x-request-id"));
    assert_eq!(
        response.headers()["x-ccx-upstream-request-id"],
        "upstream-request-1"
    );
    assert_eq!(response.headers()["x-ccx-upstream-index"], "0");
    assert_eq!(response.headers()["x-ccx-attempt"], "1");
    let body: Value = response.json().await.unwrap();
    assert_eq!(body["type"], "message");
    assert_eq!(body["content"][0]["type"], "text");
    assert_eq!(body["content"][0]["text"], "hello from upstream");
    assert_eq!(body["stop_reason"], "end_turn");
    assert_eq!(body["usage"]["input_tokens"], 3);
    assert_eq!(body["usage"]["output_tokens"], 2);
}

#[tokio::test(flavor = "multi_thread")]
async fn streaming_round_trip_has_final_usage() {
    let base = start_router().await;
    let response = post_message(&base, basic_request(true)).await;
    assert_eq!(response.status(), 200);
    assert_eq!(
        response.headers()[header::CONTENT_TYPE],
        "text/event-stream"
    );
    assert_eq!(
        response.headers()["x-ccx-upstream-request-id"],
        "upstream-stream-1"
    );
    let text = response.text().await.unwrap();
    assert!(text.contains("event: message_start"), "got: {text}");
    assert!(text.contains("event: content_block_start"));
    assert!(text.contains("text_delta"));
    assert!(text.contains("\"output_tokens\":1"));
    assert!(text.contains("event: message_stop"));
}

#[tokio::test(flavor = "multi_thread")]
async fn authentication_errors_are_canonical() {
    let base = start_router().await;
    let response = reqwest::Client::new()
        .post(format!("{base}/v1/messages"))
        .json(&basic_request(false))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), 401);
    assert!(response.headers().contains_key("x-request-id"));
    let body: Value = response.json().await.unwrap();
    assert_eq!(body["type"], "error");
    assert_eq!(body["error"]["type"], "authentication_error");
}

#[tokio::test(flavor = "multi_thread")]
async fn health_ok() {
    let base = start_router().await;
    let response = reqwest::get(format!("{base}/health")).await.unwrap();
    assert_eq!(response.status(), 200);
}

#[tokio::test(flavor = "multi_thread")]
async fn token_count_is_explicitly_marked_as_estimated() {
    let base = start_router().await;
    let response = reqwest::Client::new()
        .post(format!("{base}/v1/messages/count_tokens"))
        .bearer_auth("secret")
        .json(&basic_request(false))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), 200);
    assert_eq!(response.headers()["x-ccx-token-count-estimated"], "true");
    assert!(
        response.json::<Value>().await.unwrap()["input_tokens"]
            .as_u64()
            .unwrap()
            > 0
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn ordered_fallback_uses_second_upstream_and_exposes_index() {
    let first_calls = Arc::new(AtomicUsize::new(0));
    let first_counter = first_calls.clone();
    let first = spawn(Router::new().route(
        "/v1/chat/completions",
        post(move || {
            let counter = first_counter.clone();
            async move {
                counter.fetch_add(1, Ordering::SeqCst);
                (
                    StatusCode::SERVICE_UNAVAILABLE,
                    [(header::RETRY_AFTER, "0")],
                    Json(json!({"error": {"message": "temporarily unavailable"}})),
                )
            }
        }),
    ))
    .await;
    let second = spawn(Router::new().route(
        "/v1/chat/completions",
        post(|| async { success("fallback worked") }),
    ))
    .await;
    let config = RouterConfig {
        retry_backoff: Duration::ZERO,
        ..RouterConfig::default()
    };
    let base = start_router_with(vec![format!("{first}/v1"), format!("{second}/v1")], config).await;

    let response = post_message(&base, basic_request(false)).await;
    assert_eq!(response.status(), 200);
    assert_eq!(response.headers()["x-ccx-upstream-index"], "1");
    assert_eq!(response.headers()["x-ccx-attempt"], "2");
    assert_eq!(first_calls.load(Ordering::SeqCst), 1);
    assert_eq!(
        response.json::<Value>().await.unwrap()["content"][0]["text"],
        "fallback worked"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn fallback_credentials_are_origin_scoped_and_must_be_explicit() {
    assert_eq!(observed_fallback_authorization(None).await, None);
    assert_eq!(
        observed_fallback_authorization(Some("fallback-secret".into())).await,
        Some("Bearer fallback-secret".into())
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn configured_retry_succeeds_before_using_a_fallback() {
    let calls = Arc::new(AtomicUsize::new(0));
    let counter = calls.clone();
    let upstream = spawn(Router::new().route(
        "/v1/chat/completions",
        post(move || {
            let attempt = counter.fetch_add(1, Ordering::SeqCst);
            async move {
                if attempt == 0 {
                    (StatusCode::SERVICE_UNAVAILABLE, "busy").into_response()
                } else {
                    success("retry worked")
                }
            }
        }),
    ))
    .await;
    let config = RouterConfig {
        attempts_per_upstream: 2,
        retry_backoff: Duration::ZERO,
        ..RouterConfig::default()
    };
    let base = start_router_with(vec![format!("{upstream}/v1")], config).await;

    let response = post_message(&base, basic_request(false)).await;
    assert_eq!(response.status(), 200);
    assert_eq!(response.headers()["x-ccx-upstream-index"], "0");
    assert_eq!(response.headers()["x-ccx-attempt"], "2");
    assert_eq!(calls.load(Ordering::SeqCst), 2);
}

#[tokio::test(flavor = "multi_thread")]
async fn non_retryable_request_error_does_not_fallback() {
    let second_calls = Arc::new(AtomicUsize::new(0));
    let second_counter = second_calls.clone();
    let first = spawn(Router::new().route(
        "/v1/chat/completions",
        post(|| async {
            (
                StatusCode::BAD_REQUEST,
                Json(json!({"error": {"message": "bad model"}})),
            )
        }),
    ))
    .await;
    let second = spawn(Router::new().route(
        "/v1/chat/completions",
        post(move || {
            let counter = second_counter.clone();
            async move {
                counter.fetch_add(1, Ordering::SeqCst);
                success("must not run")
            }
        }),
    ))
    .await;
    let base = start_router_with(
        vec![format!("{first}/v1"), format!("{second}/v1")],
        RouterConfig::default(),
    )
    .await;

    let response = post_message(&base, basic_request(false)).await;
    assert_eq!(response.status(), 400);
    assert_eq!(response.headers()["x-ccx-upstream-index"], "0");
    assert_eq!(second_calls.load(Ordering::SeqCst), 0);
    let body: Value = response.json().await.unwrap();
    assert_eq!(body["error"]["type"], "invalid_request_error");
    assert_eq!(body["error"]["message"], "bad model");
}

#[tokio::test(flavor = "multi_thread")]
async fn stalled_error_body_times_out_then_falls_back() {
    let first = spawn(Router::new().route(
        "/v1/chat/completions",
        post(|| async {
            Response::builder()
                .status(StatusCode::SERVICE_UNAVAILABLE)
                .body(Body::from_stream(stream::pending::<
                    Result<Bytes, Infallible>,
                >()))
                .unwrap()
        }),
    ))
    .await;
    let second = spawn(Router::new().route(
        "/v1/chat/completions",
        post(|| async { success("after stalled error") }),
    ))
    .await;
    let config = RouterConfig {
        response_timeout: Duration::from_millis(30),
        retry_backoff: Duration::ZERO,
        ..RouterConfig::default()
    };
    let base = start_router_with(vec![format!("{first}/v1"), format!("{second}/v1")], config).await;

    let started = Instant::now();
    let response = post_message(&base, basic_request(false)).await;
    assert_eq!(response.status(), 200);
    assert!(started.elapsed() < Duration::from_secs(1));
    assert_eq!(response.headers()["x-ccx-upstream-index"], "1");
}

#[tokio::test(flavor = "multi_thread")]
async fn stream_options_auto_downgrades_for_legacy_backend() {
    let calls = Arc::new(AtomicUsize::new(0));
    let counter = calls.clone();
    let upstream = spawn(Router::new().route(
        "/v1/chat/completions",
        post(move |body: Bytes| {
            let counter = counter.clone();
            async move {
                counter.fetch_add(1, Ordering::SeqCst);
                let value: Value = serde_json::from_slice(&body).unwrap();
                if value.get("stream_options").is_some() {
                    return (
                        StatusCode::BAD_REQUEST,
                        Json(json!({"error": {"message": "unknown field stream_options"}})),
                    )
                        .into_response();
                }
                (
                    [(header::CONTENT_TYPE, "text/event-stream")],
                    concat!(
                        "data: {\"choices\":[{\"delta\":{\"content\":\"legacy\"}}]}\n\n",
                        "data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n",
                        "data: [DONE]\n\n"
                    ),
                )
                    .into_response()
            }
        }),
    ))
    .await;
    let base = start_router_with(vec![format!("{upstream}/v1")], RouterConfig::default()).await;

    let response = post_message(&base, basic_request(true)).await;
    assert_eq!(response.status(), 200);
    assert_eq!(response.headers()["x-ccx-attempt"], "2");
    assert_eq!(calls.load(Ordering::SeqCst), 2);
    assert!(response.text().await.unwrap().contains("legacy"));
}

#[tokio::test(flavor = "multi_thread")]
async fn fragmented_utf8_crlf_comments_and_multiline_sse_are_supported() {
    let wire = concat!(
        ": upstream keepalive\r\n",
        "data: {\"choices\":\r\n",
        "data: [{\"delta\":{\"content\":\"halo 👋\"}}]}\r\n\r\n",
        "data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}\r\n\r\n",
        "data: [DONE]"
    )
    .as_bytes()
    .to_vec();
    let emoji = wire
        .windows(4)
        .position(|window| window == "👋".as_bytes())
        .unwrap();
    let chunks = vec![
        Bytes::copy_from_slice(&wire[..emoji + 1]),
        Bytes::copy_from_slice(&wire[emoji + 1..emoji + 3]),
        Bytes::copy_from_slice(&wire[emoji + 3..]),
    ];
    let upstream = spawn(Router::new().route(
        "/v1/chat/completions",
        post(move || {
            let chunks = chunks.clone();
            async move {
                (
                    [(header::CONTENT_TYPE, "text/event-stream")],
                    Body::from_stream(stream::iter(
                        chunks.into_iter().map(Ok::<Bytes, Infallible>),
                    )),
                )
            }
        }),
    ))
    .await;
    let base = start_router_with(vec![format!("{upstream}/v1")], RouterConfig::default()).await;

    let text = post_message(&base, basic_request(true))
        .await
        .text()
        .await
        .unwrap();
    assert!(text.contains("halo 👋"), "{text}");
    assert!(text.contains("event: message_stop"), "{text}");
}

#[tokio::test(flavor = "multi_thread")]
async fn interleaved_parallel_tool_calls_become_sequential_anthropic_blocks() {
    let upstream = spawn(Router::new().route(
        "/v1/chat/completions",
        post(|| async {
            (
                [(header::CONTENT_TYPE, "text/event-stream")],
                concat!(
                    "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"call_a\",\"function\":{\"name\":\"read\",\"arguments\":\"{\\\"path\\\":\"}},{\"index\":1,\"id\":\"call_b\",\"function\":{\"name\":\"glob\",\"arguments\":\"{\\\"pattern\\\":\"}}]}}]}\n\n",
                    "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":1,\"function\":{\"arguments\":\"\\\"*.rs\\\"}\"}},{\"index\":0,\"function\":{\"arguments\":\"\\\"a.rs\\\"}\"}}]},\"finish_reason\":\"tool_calls\"}]}\n\n",
                    "data: [DONE]\n\n"
                ),
            )
        }),
    ))
    .await;
    let base = start_router_with(vec![format!("{upstream}/v1")], RouterConfig::default()).await;

    let text = post_message(&base, basic_request(true))
        .await
        .text()
        .await
        .unwrap();
    let block_events: Vec<_> = text
        .lines()
        .filter(|line| {
            *line == "event: content_block_start" || *line == "event: content_block_stop"
        })
        .collect();
    assert_eq!(
        block_events,
        vec![
            "event: content_block_start",
            "event: content_block_stop",
            "event: content_block_start",
            "event: content_block_stop",
        ],
        "{text}"
    );
    assert!(text.contains("\"name\":\"read\""), "{text}");
    assert!(text.contains("\"name\":\"glob\""), "{text}");
    assert!(text.contains("\"stop_reason\":\"tool_use\""), "{text}");
}

#[tokio::test(flavor = "multi_thread")]
async fn silent_upstream_gets_downstream_ping_keepalives() {
    let upstream = spawn(Router::new().route(
        "/v1/chat/completions",
        post(|| async {
            let delayed = stream::once(async {
                tokio::time::sleep(Duration::from_millis(45)).await;
                Ok::<Bytes, Infallible>(Bytes::from_static(
                    concat!(
                        "data: {\"choices\":[{\"delta\":{\"content\":\"done\"}}]}\n\n",
                        "data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n",
                        "data: [DONE]\n\n"
                    )
                    .as_bytes(),
                ))
            });
            (
                [(header::CONTENT_TYPE, "text/event-stream")],
                Body::from_stream(delayed),
            )
        }),
    ))
    .await;
    let config = RouterConfig {
        stream_ping_interval: Duration::from_millis(10),
        stream_idle_timeout: Duration::from_millis(200),
        ..RouterConfig::default()
    };
    let base = start_router_with(vec![format!("{upstream}/v1")], config).await;

    let text = post_message(&base, basic_request(true))
        .await
        .text()
        .await
        .unwrap();
    assert!(text.contains("event: ping"), "{text}");
    assert!(text.contains("event: message_stop"), "{text}");
}

#[tokio::test(flavor = "multi_thread")]
async fn malformed_stream_becomes_anthropic_error_event() {
    let upstream = spawn(Router::new().route(
        "/v1/chat/completions",
        post(|| async {
            (
                [(header::CONTENT_TYPE, "text/event-stream")],
                "data: {not-json}\n\n",
            )
        }),
    ))
    .await;
    let base = start_router_with(vec![format!("{upstream}/v1")], RouterConfig::default()).await;

    let text = post_message(&base, basic_request(true))
        .await
        .text()
        .await
        .unwrap();
    assert!(text.contains("event: error"), "{text}");
    assert!(text.contains("\"type\":\"api_error\""), "{text}");
    assert!(!text.contains("event: message_stop"), "{text}");
}

#[tokio::test(flavor = "multi_thread")]
async fn unsupported_lossy_features_are_rejected_explicitly() {
    let base = start_router().await;
    let mut request = basic_request(false);
    request["thinking"] = json!({"type": "enabled", "budget_tokens": 1024});
    let response = post_message(&base, request).await;
    assert_eq!(response.status(), 400);
    let body: Value = response.json().await.unwrap();
    assert_eq!(body["error"]["type"], "invalid_request_error");
    assert!(body["error"]["message"]
        .as_str()
        .unwrap()
        .contains("cannot be translated losslessly"));
}
