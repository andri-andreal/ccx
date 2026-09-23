//! Axum HTTP server: accepts the Anthropic Messages API, forwards a translated
//! request to ordered OpenAI-compatible upstreams, and translates the response
//! back. Retries and fallback happen only before a successful response starts.

use std::net::IpAddr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant, SystemTime};

use axum::{
    body::{Body, Bytes},
    extract::{DefaultBodyLimit, State},
    http::{header, HeaderMap, HeaderName, HeaderValue, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use futures::{SinkExt, StreamExt};
use serde_json::{json, Value};

use crate::translate;
use crate::translate::sse::SseDecoder;
use crate::translate::stream::{SseEvent, StreamTranslator};
use crate::types::{
    ChatChunk, ChatRequest, ChatResponse, ContentBlock, MessagesRequest, ProviderPinning,
    StringOrBlocks,
};

const MAX_UPSTREAM_BODY_BYTES: usize = 16 * 1024 * 1024;
const MAX_ERROR_BODY_BYTES: usize = 64 * 1024;

#[derive(Clone, Debug)]
pub struct Upstream {
    pub base_url: String,
    pub api_key: Option<String>,
    /// OpenRouter routing controls for this upstream only. Pinning belongs on
    /// the upstream rather than on `RouterConfig` because a chain can mix a
    /// pinned OpenRouter endpoint with backends that do not know the field.
    pub provider: Option<ProviderPinning>,
}

#[derive(Clone, Debug)]
pub struct RouterConfig {
    /// Attempts for each configured upstream. Defaults to one: reliability can
    /// be opted into without changing existing request/billing behaviour.
    pub attempts_per_upstream: usize,
    /// Time allowed to receive upstream response headers and, for non-streaming
    /// calls, the complete response body.
    pub response_timeout: Duration,
    /// Maximum silence between chunks of an established upstream stream.
    pub stream_idle_timeout: Duration,
    /// Downstream keepalive interval while a provider is thinking silently.
    pub stream_ping_interval: Duration,
    pub retry_backoff: Duration,
    pub stream_usage: StreamUsageMode,
    pub structured_logs: bool,
    pub max_request_bytes: usize,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StreamUsageMode {
    /// Request usage, then retry once without `stream_options` when an older
    /// backend explicitly rejects that field.
    Auto,
    On,
    Off,
}

impl Default for RouterConfig {
    fn default() -> Self {
        Self {
            attempts_per_upstream: 1,
            response_timeout: Duration::from_secs(30),
            stream_idle_timeout: Duration::from_secs(120),
            stream_ping_interval: Duration::from_secs(15),
            retry_backoff: Duration::from_millis(100),
            stream_usage: StreamUsageMode::Auto,
            structured_logs: true,
            max_request_bytes: 16 * 1024 * 1024,
        }
    }
}

#[derive(Clone)]
pub struct AppState {
    pub upstreams: Vec<Upstream>,
    pub api_key: String,
    pub client: reqwest::Client,
    pub config: RouterConfig,
}

impl AppState {
    /// Convenience constructor preserving the original single-upstream API.
    pub fn single(
        upstream_url: impl Into<String>,
        upstream_key: Option<String>,
        api_key: impl Into<String>,
        client: reqwest::Client,
    ) -> Self {
        Self {
            upstreams: vec![Upstream {
                base_url: upstream_url.into(),
                api_key: upstream_key,
                provider: None,
            }],
            api_key: api_key.into(),
            client,
            config: RouterConfig::default(),
        }
    }
}

/// Only encrypted remote upstreams and explicit loopback HTTP development
/// endpoints are accepted. Exact IP parsing prevents hostnames such as
/// `127.evil` from being mistaken for loopback.
pub fn validate_upstream_url(url: &str, label: &str) -> Result<(), String> {
    let parsed = reqwest::Url::parse(url).map_err(|error| format!("invalid {label}: {error}"))?;
    if parsed.scheme() != "http" && parsed.scheme() != "https" {
        return Err(format!("{label} must use http or https"));
    }
    if parsed.host().is_none() {
        return Err(format!("{label} must include a host"));
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err(format!("{label} must not contain credentials"));
    }
    if parsed.query().is_some() || parsed.fragment().is_some() {
        return Err(format!("{label} must not contain a query or fragment"));
    }
    if parsed.scheme() == "http" && !is_loopback_host(parsed.host_str()) {
        return Err(format!(
            "{label} must use https unless its host is localhost, 127.0.0.0/8, or ::1"
        ));
    }
    Ok(())
}

fn is_loopback_host(host: Option<&str>) -> bool {
    match host {
        Some(domain) if domain.eq_ignore_ascii_case("localhost") => true,
        Some(address) => address
            .strip_prefix('[')
            .and_then(|address| address.strip_suffix(']'))
            .unwrap_or(address)
            .parse::<IpAddr>()
            .map(|address| address.is_loopback())
            .unwrap_or(false),
        None => false,
    }
}

pub fn app(state: AppState) -> Router {
    let max_request_bytes = state.config.max_request_bytes;
    Router::new()
        .route("/health", get(|| async { StatusCode::OK }))
        .route("/v1/messages", post(messages))
        .route("/v1/messages/count_tokens", post(count_tokens))
        .layer(DefaultBodyLimit::max(max_request_bytes))
        .with_state(state)
}

fn check_auth(headers: &HeaderMap, key: &str) -> bool {
    let bearer = headers
        .get(header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
        .map(|value| constant_time_eq(value.as_bytes(), key.as_bytes()))
        .unwrap_or(false);
    let x_api_key = headers
        .get("x-api-key")
        .and_then(|value| value.to_str().ok())
        .map(|value| constant_time_eq(value.as_bytes(), key.as_bytes()))
        .unwrap_or(false);
    bearer || x_api_key
}

fn constant_time_eq(left: &[u8], right: &[u8]) -> bool {
    let mut difference = left.len() ^ right.len();
    for index in 0..left.len().max(right.len()) {
        difference |= usize::from(
            left.get(index).copied().unwrap_or_default()
                ^ right.get(index).copied().unwrap_or_default(),
        );
    }
    difference == 0
}

fn next_request_id() -> String {
    static NEXT_ID: AtomicU64 = AtomicU64::new(1);
    format!("req_ccx{}", NEXT_ID.fetch_add(1, Ordering::Relaxed))
}

async fn messages(State(state): State<AppState>, headers: HeaderMap, body: Bytes) -> Response {
    let request_id = next_request_id();
    let started = Instant::now();
    if !check_auth(&headers, &state.api_key) {
        log_event(
            &state,
            "request_rejected",
            json!({"request_id": request_id, "status": 401, "reason": "authentication"}),
        );
        return api_error(
            StatusCode::UNAUTHORIZED,
            "authentication_error",
            "invalid CCX router API key",
            &request_id,
        );
    }

    let request: MessagesRequest = match serde_json::from_slice(&body) {
        Ok(request) => request,
        Err(error) => {
            log_event(
                &state,
                "request_rejected",
                json!({"request_id": request_id, "status": 400, "reason": "invalid_json"}),
            );
            return api_error(
                StatusCode::BAD_REQUEST,
                "invalid_request_error",
                &format!("invalid Anthropic Messages request: {error}"),
                &request_id,
            );
        }
    };
    let streaming = request.stream.unwrap_or(false);
    log_event(
        &state,
        "request_start",
        json!({
            "request_id": request_id,
            "model": request.model,
            "stream": streaming,
        }),
    );

    let model = request.model.clone();
    let chat = match translate::request::to_openai(&request) {
        Ok(chat) => chat,
        Err(error) => {
            log_event(
                &state,
                "request_rejected",
                json!({"request_id": request_id, "status": 400, "reason": "translation"}),
            );
            return api_error(
                StatusCode::BAD_REQUEST,
                "invalid_request_error",
                &error.to_string(),
                &request_id,
            );
        }
    };

    let selected = match send_with_failover(&state, &chat, &request_id).await {
        Ok(selected) => selected,
        Err(failure) => {
            log_event(
                &state,
                "request_error",
                json!({
                    "request_id": request_id,
                    "status": failure.status.as_u16(),
                    "attempts": failure.attempts,
                    "latency_ms": millis(started.elapsed()),
                    "error_type": failure.error_type,
                }),
            );
            let mut response = api_error(
                failure.status,
                failure.error_type,
                &failure.message,
                &request_id,
            );
            add_observability_headers(
                &mut response,
                &request_id,
                failure.upstream_request_id.as_deref(),
                failure.upstream_index,
                failure.attempts,
            );
            return response;
        }
    };

    if streaming {
        stream_response(state, selected, model, request_id, started)
    } else {
        non_stream_response(state, selected, model, request_id, started).await
    }
}

struct SelectedUpstream {
    response: reqwest::Response,
    attempt: usize,
    upstream_index: usize,
    upstream_request_id: Option<String>,
}

struct UpstreamObservation {
    attempt: usize,
    index: usize,
    request_id: Option<String>,
}

struct UpstreamFailure {
    status: StatusCode,
    error_type: &'static str,
    message: String,
    attempts: usize,
    upstream_index: Option<usize>,
    upstream_request_id: Option<String>,
}

async fn send_with_failover(
    state: &AppState,
    chat: &ChatRequest,
    request_id: &str,
) -> Result<SelectedUpstream, UpstreamFailure> {
    if state.upstreams.is_empty() {
        return Err(UpstreamFailure {
            status: StatusCode::SERVICE_UNAVAILABLE,
            error_type: "api_error",
            message: "no upstream is configured".into(),
            attempts: 0,
            upstream_index: None,
            upstream_request_id: None,
        });
    }
    for (index, upstream) in state.upstreams.iter().enumerate() {
        if let Err(message) =
            validate_upstream_url(&upstream.base_url, &format!("upstream #{}", index + 1))
        {
            return Err(UpstreamFailure {
                status: StatusCode::INTERNAL_SERVER_ERROR,
                error_type: "api_error",
                message,
                attempts: 0,
                upstream_index: Some(index),
                upstream_request_id: None,
            });
        }
    }

    let attempts_per_upstream = state.config.attempts_per_upstream.max(1);
    let mut total_attempts = 0usize;
    let mut last_failure = None;
    let mut request_body = chat.clone();
    if state.config.stream_usage == StreamUsageMode::Off {
        request_body.stream_options = None;
    }

    for (upstream_index, upstream) in state.upstreams.iter().enumerate() {
        // Swap only the pinning, never rebuild the body: `request_body` also
        // remembers a stream_options downgrade that must survive the move to
        // the next upstream.
        request_body.provider = upstream.provider.clone();
        'attempts: for local_attempt in 0..attempts_per_upstream {
            // An auto compatibility downgrade is intentionally outside the
            // configured retry budget: the first response is an explicit 400,
            // so no inference has started and retrying without the unsupported
            // optional field is safe.
            loop {
                total_attempts += 1;
                log_event(
                    state,
                    "upstream_attempt",
                    json!({
                        "request_id": request_id,
                        "upstream_index": upstream_index,
                        "attempt": total_attempts,
                        "stream_usage": request_body.stream_options.is_some(),
                    }),
                );
                let url = format!(
                    "{}/chat/completions",
                    upstream.base_url.trim_end_matches('/')
                );
                let mut builder = state
                    .client
                    .post(url)
                    .header("x-request-id", request_id)
                    .header(
                        header::USER_AGENT,
                        concat!("ccx-router/", env!("CARGO_PKG_VERSION")),
                    )
                    .json(&request_body);
                if let Some(key) = upstream.api_key.as_ref().filter(|key| !key.is_empty()) {
                    builder = builder.bearer_auth(key);
                }

                let response =
                    match tokio::time::timeout(state.config.response_timeout, builder.send()).await
                    {
                        Ok(Ok(response)) => response,
                        Ok(Err(error)) => {
                            let retryable = error.is_connect() || error.is_timeout();
                            let failure = UpstreamFailure {
                                status: if error.is_timeout() {
                                    StatusCode::GATEWAY_TIMEOUT
                                } else {
                                    StatusCode::BAD_GATEWAY
                                },
                                error_type: "api_error",
                                message: if error.is_timeout() {
                                    "upstream response timed out".into()
                                } else {
                                    "could not connect to upstream".into()
                                },
                                attempts: total_attempts,
                                upstream_index: Some(upstream_index),
                                upstream_request_id: None,
                            };
                            log_retry(state, request_id, upstream_index, total_attempts, &failure);
                            last_failure = Some(failure);
                            if retryable
                                && has_another_attempt(state, upstream_index, local_attempt)
                            {
                                retry_delay(state, local_attempt, None).await;
                                continue 'attempts;
                            }
                            return Err(last_failure.expect("failure was set"));
                        }
                        Err(_) => {
                            let failure = UpstreamFailure {
                                status: StatusCode::GATEWAY_TIMEOUT,
                                error_type: "api_error",
                                message: "upstream response timed out".into(),
                                attempts: total_attempts,
                                upstream_index: Some(upstream_index),
                                upstream_request_id: None,
                            };
                            log_retry(state, request_id, upstream_index, total_attempts, &failure);
                            last_failure = Some(failure);
                            if has_another_attempt(state, upstream_index, local_attempt) {
                                retry_delay(state, local_attempt, None).await;
                                continue 'attempts;
                            }
                            return Err(last_failure.expect("failure was set"));
                        }
                    };

                let upstream_request_id = upstream_request_id(response.headers());
                if response.status().is_success() {
                    log_event(
                        state,
                        "upstream_selected",
                        json!({
                            "request_id": request_id,
                            "upstream_index": upstream_index,
                            "attempt": total_attempts,
                            "status": response.status().as_u16(),
                        }),
                    );
                    return Ok(SelectedUpstream {
                        response,
                        attempt: total_attempts,
                        upstream_index,
                        upstream_request_id,
                    });
                }

                let status = response.status();
                let retryable = retryable_status(status);
                let retry_after = parse_retry_after(response.headers());
                let body = match tokio::time::timeout(
                    state.config.response_timeout,
                    read_body_limited(response, MAX_ERROR_BODY_BYTES),
                )
                .await
                {
                    Ok(Ok(body)) => body,
                    _ => Vec::new(),
                };

                if state.config.stream_usage == StreamUsageMode::Auto
                    && request_body.stream_options.is_some()
                    && stream_options_rejected(status, &body)
                {
                    request_body.stream_options = None;
                    log_event(
                        state,
                        "stream_usage_downgrade",
                        json!({
                            "request_id": request_id,
                            "upstream_index": upstream_index,
                            "attempt": total_attempts,
                        }),
                    );
                    continue;
                }

                let failure = failure_from_status(
                    status,
                    &body,
                    total_attempts,
                    upstream_index,
                    upstream_request_id,
                );
                log_retry(state, request_id, upstream_index, total_attempts, &failure);
                last_failure = Some(failure);
                if retryable && has_another_attempt(state, upstream_index, local_attempt) {
                    retry_delay(state, local_attempt, retry_after).await;
                    continue 'attempts;
                }
                return Err(last_failure.expect("failure was set"));
            }
        }
    }

    Err(last_failure.unwrap_or(UpstreamFailure {
        status: StatusCode::BAD_GATEWAY,
        error_type: "api_error",
        message: "all upstream attempts failed".into(),
        attempts: total_attempts,
        upstream_index: None,
        upstream_request_id: None,
    }))
}

fn has_another_attempt(state: &AppState, upstream_index: usize, local_attempt: usize) -> bool {
    local_attempt + 1 < state.config.attempts_per_upstream.max(1)
        || upstream_index + 1 < state.upstreams.len()
}

async fn retry_delay(state: &AppState, local_attempt: usize, retry_after: Option<Duration>) {
    let delay = retry_after.unwrap_or_else(|| {
        let factor = 1u32 << local_attempt.min(4);
        state.config.retry_backoff.saturating_mul(factor)
    });
    if delay.is_zero() {
        return;
    }
    tokio::time::sleep(delay).await;
}

fn parse_retry_after(headers: &HeaderMap) -> Option<Duration> {
    let raw = headers.get(header::RETRY_AFTER)?.to_str().ok()?.trim();
    let delay = if let Ok(seconds) = raw.parse::<u64>() {
        Duration::from_secs(seconds)
    } else {
        httpdate::parse_http_date(raw)
            .ok()?
            .duration_since(SystemTime::now())
            .unwrap_or(Duration::ZERO)
    };
    Some(delay.min(Duration::from_secs(30)))
}

fn stream_options_rejected(status: StatusCode, body: &[u8]) -> bool {
    if status != StatusCode::BAD_REQUEST && status != StatusCode::UNPROCESSABLE_ENTITY {
        return false;
    }
    let lowercase = String::from_utf8_lossy(body).to_ascii_lowercase();
    lowercase.contains("stream_options") || lowercase.contains("include_usage")
}

fn retryable_status(status: StatusCode) -> bool {
    matches!(
        status,
        StatusCode::REQUEST_TIMEOUT
            | StatusCode::CONFLICT
            | StatusCode::TOO_MANY_REQUESTS
            | StatusCode::INTERNAL_SERVER_ERROR
            | StatusCode::BAD_GATEWAY
            | StatusCode::SERVICE_UNAVAILABLE
            | StatusCode::GATEWAY_TIMEOUT
    ) || status.as_u16() == 529
}

fn failure_from_status(
    status: StatusCode,
    body: &[u8],
    attempts: usize,
    upstream_index: usize,
    upstream_request_id: Option<String>,
) -> UpstreamFailure {
    let (error_type, default_message) = match status.as_u16() {
        400 | 422 => ("invalid_request_error", "upstream rejected the request"),
        401 => ("authentication_error", "upstream authentication failed"),
        403 => ("permission_error", "upstream permission denied"),
        404 => (
            "not_found_error",
            "upstream model or endpoint was not found",
        ),
        408 | 504 => ("api_error", "upstream response timed out"),
        429 => ("rate_limit_error", "upstream rate limit exceeded"),
        529 => ("overloaded_error", "upstream is overloaded"),
        500..=599 => ("api_error", "upstream service failed"),
        _ => ("api_error", "upstream request failed"),
    };
    UpstreamFailure {
        status,
        error_type,
        message: extract_error_message(body).unwrap_or_else(|| default_message.into()),
        attempts,
        upstream_index: Some(upstream_index),
        upstream_request_id,
    }
}

async fn non_stream_response(
    state: AppState,
    selected: SelectedUpstream,
    model: String,
    request_id: String,
    started: Instant,
) -> Response {
    let SelectedUpstream {
        response: upstream_response,
        attempt,
        upstream_index,
        upstream_request_id,
    } = selected;
    let observation = UpstreamObservation {
        attempt,
        index: upstream_index,
        request_id: upstream_request_id,
    };
    let body = match tokio::time::timeout(
        state.config.response_timeout,
        read_body_limited(upstream_response, MAX_UPSTREAM_BODY_BYTES),
    )
    .await
    {
        Ok(Ok(body)) => body,
        Ok(Err(message)) => {
            return response_error_with_headers(
                &state,
                StatusCode::BAD_GATEWAY,
                "api_error",
                &message,
                &request_id,
                &observation,
                started,
            )
        }
        Err(_) => {
            return response_error_with_headers(
                &state,
                StatusCode::GATEWAY_TIMEOUT,
                "api_error",
                "upstream response body timed out",
                &request_id,
                &observation,
                started,
            )
        }
    };
    let openai: ChatResponse = match serde_json::from_slice(&body) {
        Ok(response) => response,
        Err(error) => {
            return response_error_with_headers(
                &state,
                StatusCode::BAD_GATEWAY,
                "api_error",
                &format!("invalid JSON response from upstream: {error}"),
                &request_id,
                &observation,
                started,
            )
        }
    };
    let anthropic = match translate::response::to_anthropic(&openai, &model) {
        Ok(response) => response,
        Err(error) => {
            return response_error_with_headers(
                &state,
                StatusCode::BAD_GATEWAY,
                "api_error",
                &error.to_string(),
                &request_id,
                &observation,
                started,
            )
        }
    };
    let input_tokens = anthropic.usage.input_tokens;
    let output_tokens = anthropic.usage.output_tokens;
    let mut response = Json(anthropic).into_response();
    add_observability_headers(
        &mut response,
        &request_id,
        observation.request_id.as_deref(),
        Some(observation.index),
        observation.attempt,
    );
    log_event(
        &state,
        "request_complete",
        json!({
            "request_id": request_id,
            "status": 200,
            "upstream_index": observation.index,
            "attempts": observation.attempt,
            "latency_ms": millis(started.elapsed()),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }),
    );
    response
}

fn stream_response(
    state: AppState,
    selected: SelectedUpstream,
    model: String,
    request_id: String,
    started: Instant,
) -> Response {
    let SelectedUpstream {
        response,
        attempt,
        upstream_index,
        upstream_request_id,
    } = selected;
    let response_request_id = upstream_request_id.clone();
    let (mut sender, receiver) =
        futures::channel::mpsc::channel::<Result<Bytes, std::io::Error>>(32);
    let task_request_id = request_id.clone();
    let task_state = state.clone();
    tokio::spawn(async move {
        let message_id = format!("msg_{}", task_request_id.trim_start_matches("req_"));
        let mut translator = StreamTranslator::new(model, message_id);
        if sender
            .send(Ok(Bytes::from(translator.start().to_wire())))
            .await
            .is_err()
        {
            log_stream_disconnect(&task_state, &task_request_id, upstream_index, started);
            return;
        }

        let mut decoder = SseDecoder::default();
        let mut upstream = response.bytes_stream();
        let mut saw_done = false;
        let mut last_upstream_activity = Instant::now();
        let mut last_downstream_activity = Instant::now();
        let stream_result: Result<(), String> = loop {
            let idle_elapsed = last_upstream_activity.elapsed();
            if idle_elapsed >= task_state.config.stream_idle_timeout {
                break Err("upstream stream idle timeout".into());
            }
            let remaining_idle = task_state.config.stream_idle_timeout - idle_elapsed;
            let wait_for = task_state.config.stream_ping_interval.min(remaining_idle);
            let item = match tokio::time::timeout(wait_for, upstream.next()).await {
                Ok(item) => item,
                Err(_) => {
                    if sender
                        .send(Ok(Bytes::from(SseEvent::ping().to_wire())))
                        .await
                        .is_err()
                    {
                        break Err(format!(
                            "client disconnected from request {task_request_id}"
                        ));
                    }
                    last_downstream_activity = Instant::now();
                    continue;
                }
            };
            let Some(item) = item else {
                let final_payloads = match decoder.finish() {
                    Ok(payloads) => payloads,
                    Err(error) => break Err(error.to_string()),
                };
                match process_payloads(
                    final_payloads,
                    &mut translator,
                    &mut sender,
                    &task_request_id,
                )
                .await
                {
                    Ok(done) => saw_done |= done,
                    Err(error) => break Err(error),
                }
                break Ok(());
            };
            let bytes = match item {
                Ok(bytes) => bytes,
                Err(_) => break Err("upstream stream connection failed".into()),
            };
            last_upstream_activity = Instant::now();
            let payloads = match decoder.push(&bytes) {
                Ok(payloads) => payloads,
                Err(error) => break Err(error.to_string()),
            };
            let produced_payload = !payloads.is_empty();
            match process_payloads(payloads, &mut translator, &mut sender, &task_request_id).await {
                Ok(done) => {
                    if produced_payload {
                        last_downstream_activity = Instant::now();
                    } else if last_downstream_activity.elapsed()
                        >= task_state.config.stream_ping_interval
                    {
                        if sender
                            .send(Ok(Bytes::from(SseEvent::ping().to_wire())))
                            .await
                            .is_err()
                        {
                            break Err(format!(
                                "client disconnected from request {task_request_id}"
                            ));
                        }
                        last_downstream_activity = Instant::now();
                    }
                    if done {
                        saw_done = true;
                        break Ok(());
                    }
                }
                Err(error) => break Err(error),
            }
        };

        let result = match stream_result {
            Ok(()) => translator.finish().map_err(|error| error.to_string()),
            Err(error) => Err(error),
        };
        match result {
            Ok(events) => {
                for event in events {
                    if sender.send(Ok(Bytes::from(event.to_wire()))).await.is_err() {
                        log_stream_disconnect(
                            &task_state,
                            &task_request_id,
                            upstream_index,
                            started,
                        );
                        return;
                    }
                }
                log_event(
                    &task_state,
                    "request_complete",
                    json!({
                        "request_id": task_request_id,
                        "status": 200,
                        "upstream_index": upstream_index,
                        "attempts": attempt,
                        "latency_ms": millis(started.elapsed()),
                        "upstream_done": saw_done,
                        "output_tokens": translator.output_tokens(),
                    }),
                );
            }
            Err(message) => {
                let safe = sanitize_message(&message);
                let event = SseEvent::error("api_error", &safe, &task_request_id);
                let _ = sender.send(Ok(Bytes::from(event.to_wire()))).await;
                log_event(
                    &task_state,
                    "request_error",
                    json!({
                        "request_id": task_request_id,
                        "status": 502,
                        "upstream_index": upstream_index,
                        "attempts": attempt,
                        "latency_ms": millis(started.elapsed()),
                        "error_type": "stream_error",
                    }),
                );
            }
        }
    });

    let mut response = Body::from_stream(receiver).into_response();
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("text/event-stream"),
    );
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-cache"));
    response.headers_mut().insert(
        HeaderName::from_static("x-accel-buffering"),
        HeaderValue::from_static("no"),
    );
    add_observability_headers(
        &mut response,
        &request_id,
        response_request_id.as_deref(),
        Some(upstream_index),
        attempt,
    );
    response
}

async fn process_payloads(
    payloads: Vec<String>,
    translator: &mut StreamTranslator,
    sender: &mut futures::channel::mpsc::Sender<Result<Bytes, std::io::Error>>,
    request_id: &str,
) -> Result<bool, String> {
    for payload in payloads {
        if payload.trim() == "[DONE]" {
            return Ok(true);
        }
        if payload.trim().is_empty() {
            continue;
        }
        let value: Value = serde_json::from_str(&payload)
            .map_err(|error| format!("invalid JSON in upstream SSE event: {error}"))?;
        if value.get("error").is_some() {
            return Err(extract_error_message(payload.as_bytes())
                .unwrap_or_else(|| "upstream returned a streaming error".into()));
        }
        let chunk: ChatChunk = serde_json::from_value(value)
            .map_err(|error| format!("invalid upstream streaming chunk: {error}"))?;
        for event in translator.push(&chunk).map_err(|error| error.to_string())? {
            if sender.send(Ok(Bytes::from(event.to_wire()))).await.is_err() {
                return Err(format!("client disconnected from request {request_id}"));
            }
        }
    }
    Ok(false)
}

async fn count_tokens(State(state): State<AppState>, headers: HeaderMap, body: Bytes) -> Response {
    let request_id = next_request_id();
    if !check_auth(&headers, &state.api_key) {
        return api_error(
            StatusCode::UNAUTHORIZED,
            "authentication_error",
            "invalid CCX router API key",
            &request_id,
        );
    }
    let request: MessagesRequest = match serde_json::from_slice(&body) {
        Ok(request) => request,
        Err(error) => {
            return api_error(
                StatusCode::BAD_REQUEST,
                "invalid_request_error",
                &format!("invalid Anthropic Messages request: {error}"),
                &request_id,
            )
        }
    };
    let mut chars = request.system.as_ref().map(text_len).unwrap_or_default();
    chars += request
        .messages
        .iter()
        .map(|message| text_len(&message.content))
        .sum::<usize>();
    chars += request
        .tools
        .as_ref()
        .map(|tools| {
            tools
                .iter()
                .map(|tool| {
                    tool.name.chars().count()
                        + tool
                            .description
                            .as_deref()
                            .map(str::chars)
                            .map(Iterator::count)
                            .unwrap_or(0)
                        + tool.input_schema.to_string().chars().count()
                })
                .sum::<usize>()
        })
        .unwrap_or_default();
    // This endpoint is necessarily an estimate for arbitrary OpenAI-compatible
    // tokenizers. Use Unicode scalar count and round up rather than truncating.
    let tokens = chars.div_ceil(4).max(1);
    let mut response = Json(json!({ "input_tokens": tokens })).into_response();
    add_observability_headers(&mut response, &request_id, None, None, 0);
    response.headers_mut().insert(
        HeaderName::from_static("x-ccx-token-count-estimated"),
        HeaderValue::from_static("true"),
    );
    response
}

fn text_len(content: &StringOrBlocks) -> usize {
    match content {
        StringOrBlocks::Text(text) => text.chars().count(),
        StringOrBlocks::Blocks(blocks) => blocks
            .iter()
            .map(|block| match block {
                ContentBlock::Text { text, .. } => text.chars().count(),
                ContentBlock::ToolUse {
                    id, name, input, ..
                } => id.chars().count() + name.chars().count() + input.to_string().chars().count(),
                ContentBlock::ToolResult {
                    tool_use_id,
                    content,
                    ..
                } => {
                    tool_use_id.chars().count() + content.as_ref().map(text_len).unwrap_or_default()
                }
                // A fixed overhead is more honest than counting base64 bytes,
                // whose size is unrelated to provider image tokenization.
                ContentBlock::Image { .. } => 340,
            })
            .sum(),
    }
}

async fn read_body_limited(response: reqwest::Response, limit: usize) -> Result<Vec<u8>, String> {
    if response
        .content_length()
        .is_some_and(|length| length > limit as u64)
    {
        return Err(format!("upstream response exceeds {limit} bytes"));
    }
    let mut stream = response.bytes_stream();
    let mut body = Vec::new();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|_| "could not read upstream response body".to_string())?;
        if body.len().saturating_add(chunk.len()) > limit {
            return Err(format!("upstream response exceeds {limit} bytes"));
        }
        body.extend_from_slice(&chunk);
    }
    Ok(body)
}

fn extract_error_message(body: &[u8]) -> Option<String> {
    let value: Value = serde_json::from_slice(body).ok()?;
    let message = value
        .pointer("/error/message")
        .or_else(|| value.get("message"))
        .and_then(Value::as_str)?;
    Some(sanitize_message(message))
}

fn sanitize_message(message: &str) -> String {
    let one_line = message.replace(['\r', '\n'], " ");
    let mut output: String = one_line.chars().take(512).collect();
    for marker in ["Bearer ", "api_key=", "apikey=", "token="] {
        let mut search_from = 0usize;
        while let Some(relative_start) = output[search_from..]
            .to_ascii_lowercase()
            .find(&marker.to_ascii_lowercase())
        {
            let start = search_from + relative_start;
            let value_start = start + marker.len();
            let value_end = output[value_start..]
                .find(|character: char| character.is_whitespace() || character == '&')
                .map(|offset| value_start + offset)
                .unwrap_or(output.len());
            output.replace_range(value_start..value_end, "[REDACTED]");
            search_from = value_start + "[REDACTED]".len();
        }
    }
    output
}

fn upstream_request_id(headers: &HeaderMap) -> Option<String> {
    ["x-request-id", "request-id", "openai-request-id"]
        .iter()
        .find_map(|name| headers.get(*name))
        .and_then(|value| value.to_str().ok())
        .map(str::trim)
        .filter(|value| !value.is_empty() && value.len() <= 200)
        .map(str::to_owned)
}

fn api_error(status: StatusCode, error_type: &str, message: &str, request_id: &str) -> Response {
    let mut response = (
        status,
        Json(json!({
            "type": "error",
            "error": {
                "type": error_type,
                "message": sanitize_message(message),
            },
            "request_id": request_id,
        })),
    )
        .into_response();
    add_observability_headers(&mut response, request_id, None, None, 0);
    response
}

fn response_error_with_headers(
    state: &AppState,
    status: StatusCode,
    error_type: &str,
    message: &str,
    request_id: &str,
    observation: &UpstreamObservation,
    started: Instant,
) -> Response {
    log_event(
        state,
        "request_error",
        json!({
            "request_id": request_id,
            "status": status.as_u16(),
            "upstream_index": observation.index,
            "attempts": observation.attempt,
            "latency_ms": millis(started.elapsed()),
            "error_type": error_type,
        }),
    );
    let mut response = api_error(status, error_type, message, request_id);
    add_observability_headers(
        &mut response,
        request_id,
        observation.request_id.as_deref(),
        Some(observation.index),
        observation.attempt,
    );
    response
}

fn add_observability_headers(
    response: &mut Response,
    request_id: &str,
    upstream_id: Option<&str>,
    upstream_index: Option<usize>,
    attempt: usize,
) {
    if let Ok(value) = HeaderValue::from_str(request_id) {
        response
            .headers_mut()
            .insert(HeaderName::from_static("x-request-id"), value);
    }
    if let Some(upstream_id) = upstream_id {
        if let Ok(value) = HeaderValue::from_str(upstream_id) {
            response
                .headers_mut()
                .insert(HeaderName::from_static("x-ccx-upstream-request-id"), value);
        }
    }
    if attempt > 0 {
        if let Ok(value) = HeaderValue::from_str(&attempt.to_string()) {
            response
                .headers_mut()
                .insert(HeaderName::from_static("x-ccx-attempt"), value);
        }
    }
    if let Some(upstream_index) = upstream_index {
        if let Ok(value) = HeaderValue::from_str(&upstream_index.to_string()) {
            response
                .headers_mut()
                .insert(HeaderName::from_static("x-ccx-upstream-index"), value);
        }
    }
}

fn log_event(state: &AppState, event: &str, fields: Value) {
    if !state.config.structured_logs {
        return;
    }
    let mut record = json!({"component": "ccx-router", "event": event});
    if let (Some(record), Some(fields)) = (record.as_object_mut(), fields.as_object()) {
        for (key, value) in fields {
            record.insert(key.clone(), value.clone());
        }
    }
    eprintln!("{record}");
}

fn log_retry(
    state: &AppState,
    request_id: &str,
    upstream_index: usize,
    attempt: usize,
    failure: &UpstreamFailure,
) {
    log_event(
        state,
        "upstream_failure",
        json!({
            "request_id": request_id,
            "upstream_index": upstream_index,
            "attempt": attempt,
            "status": failure.status.as_u16(),
            "error_type": failure.error_type,
        }),
    );
}

fn log_stream_disconnect(
    state: &AppState,
    request_id: &str,
    upstream_index: usize,
    started: Instant,
) {
    log_event(
        state,
        "client_disconnect",
        json!({
            "request_id": request_id,
            "upstream_index": upstream_index,
            "latency_ms": millis(started.elapsed()),
        }),
    );
}

fn millis(duration: Duration) -> u128 {
    duration.as_millis()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn authentication_comparison_works_for_equal_and_different_lengths() {
        assert!(constant_time_eq(b"secret", b"secret"));
        assert!(!constant_time_eq(b"secret", b"secrex"));
        assert!(!constant_time_eq(b"secret", b"short"));
    }

    #[test]
    fn sanitizes_common_secret_shapes() {
        assert_eq!(
            sanitize_message("failed Bearer sk-secret\nnext"),
            "failed Bearer [REDACTED] next"
        );
        assert_eq!(
            sanitize_message("https://x.test?a=1&api_key=secret&b=2"),
            "https://x.test?a=1&api_key=[REDACTED]&b=2"
        );
    }

    #[test]
    fn canonical_status_mapping() {
        let failure = failure_from_status(
            StatusCode::TOO_MANY_REQUESTS,
            br#"{"error":{"message":"slow down"}}"#,
            2,
            0,
            None,
        );
        assert_eq!(failure.error_type, "rate_limit_error");
        assert_eq!(failure.message, "slow down");
    }

    #[test]
    fn retry_after_seconds_are_bounded() {
        let mut headers = HeaderMap::new();
        headers.insert(header::RETRY_AFTER, HeaderValue::from_static("999"));
        assert_eq!(parse_retry_after(&headers), Some(Duration::from_secs(30)));
        headers.insert(header::RETRY_AFTER, HeaderValue::from_static("invalid"));
        assert_eq!(parse_retry_after(&headers), None);
    }

    #[test]
    fn compatibility_retry_requires_an_explicit_stream_options_error() {
        assert!(stream_options_rejected(
            StatusCode::BAD_REQUEST,
            br#"{"error":{"message":"unknown field stream_options"}}"#
        ));
        assert!(!stream_options_rejected(
            StatusCode::BAD_REQUEST,
            br#"{"error":{"message":"invalid model"}}"#
        ));
    }
}
