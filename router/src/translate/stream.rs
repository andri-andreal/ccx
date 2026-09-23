//! Streaming translation: OpenAI Chat Completions SSE chunks -> Anthropic
//! Messages SSE events.
//!
//! The correctness crux is tracking the currently-open content block and its
//! type, and emitting `content_block_stop` before opening a different block.
//! Models that interleave text and tool_use in one response broke ccr
//! (PR #1356, "Content block is not a text block") precisely because it failed
//! to do this.

use std::collections::{BTreeMap, HashSet};
use std::fmt;

use serde_json::{json, Value};

use super::response::map_stop_reason;
use crate::types::ChatChunk;

/// One Anthropic SSE event: an `event:` name and its `data:` JSON.
#[derive(Debug, Clone, PartialEq)]
pub struct SseEvent {
    pub event: String,
    pub data: Value,
}

impl SseEvent {
    fn new(event: &str, data: Value) -> Self {
        SseEvent {
            event: event.into(),
            data,
        }
    }
    /// Wire format for an SSE stream.
    pub fn to_wire(&self) -> String {
        format!("event: {}\ndata: {}\n\n", self.event, self.data)
    }

    pub fn error(error_type: &str, message: &str, request_id: &str) -> Self {
        SseEvent::new(
            "error",
            json!({
                "type": "error",
                "error": {"type": error_type, "message": message},
                "request_id": request_id
            }),
        )
    }

    pub fn ping() -> Self {
        SseEvent::new("ping", json!({"type": "ping"}))
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StreamTranslationError(pub String);

impl fmt::Display for StreamTranslationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for StreamTranslationError {}

#[derive(Clone, Copy, PartialEq)]
enum Open {
    None,
    Text(usize),
}

#[derive(Default)]
struct PendingTool {
    id: Option<String>,
    name: Option<String>,
    arguments: String,
}

pub struct StreamTranslator {
    model: String,
    id: String,
    open: Open,
    next_index: usize,
    // OpenAI may interleave argument deltas for multiple tool calls. Anthropic
    // content blocks are emitted sequentially, so calls are buffered by their
    // upstream index and flushed in index order once complete.
    pending_tools: BTreeMap<u32, PendingTool>,
    flushed_tools: HashSet<u32>,
    finish_reason: Option<String>,
    saw_tool_call: bool,
    output_tokens: u32,
}

impl StreamTranslator {
    pub fn new(model: impl Into<String>, id: impl Into<String>) -> Self {
        StreamTranslator {
            model: model.into(),
            id: id.into(),
            open: Open::None,
            next_index: 0,
            pending_tools: BTreeMap::new(),
            flushed_tools: HashSet::new(),
            finish_reason: None,
            saw_tool_call: false,
            output_tokens: 0,
        }
    }

    pub fn start(&self) -> SseEvent {
        SseEvent::new(
            "message_start",
            json!({
                "type": "message_start",
                "message": {
                    "id": self.id,
                    "type": "message",
                    "role": "assistant",
                    "model": self.model,
                    "content": [],
                    "stop_reason": null,
                    "stop_sequence": null,
                    "usage": {"input_tokens": 0, "output_tokens": 0}
                }
            }),
        )
    }

    fn close_open(&mut self, out: &mut Vec<SseEvent>) {
        let idx = match self.open {
            Open::None => return,
            Open::Text(i) => i,
        };
        out.push(SseEvent::new(
            "content_block_stop",
            json!({"type": "content_block_stop", "index": idx}),
        ));
        self.open = Open::None;
    }

    pub fn push(&mut self, chunk: &ChatChunk) -> Result<Vec<SseEvent>, StreamTranslationError> {
        let mut out = Vec::new();

        if let Some(u) = &chunk.usage {
            if let Some(o) = u.completion_tokens {
                self.output_tokens = o;
            }
        }

        if chunk.choices.len() > 1 {
            return Err(StreamTranslationError(
                "upstream returned multiple streaming choices; only n=1 is supported".into(),
            ));
        }

        for choice in &chunk.choices {
            // Text delta.
            if let Some(text) = &choice.delta.content {
                if !text.is_empty() {
                    if !self.pending_tools.is_empty() {
                        self.flush_tools(&mut out)?;
                    }
                    if !matches!(self.open, Open::Text(_)) {
                        self.close_open(&mut out);
                        let idx = self.next_index;
                        self.next_index += 1;
                        self.open = Open::Text(idx);
                        out.push(SseEvent::new(
                            "content_block_start",
                            json!({"type":"content_block_start","index":idx,"content_block":{"type":"text","text":""}}),
                        ));
                    }
                    let idx = match self.open {
                        Open::Text(i) => i,
                        _ => unreachable!(),
                    };
                    out.push(SseEvent::new(
                        "content_block_delta",
                        json!({"type":"content_block_delta","index":idx,"delta":{"type":"text_delta","text":text}}),
                    ));
                }
            }

            // Tool-call deltas.
            if let Some(tcs) = &choice.delta.tool_calls {
                if !tcs.is_empty() {
                    self.close_open(&mut out);
                }
                for tc in tcs {
                    self.saw_tool_call = true;
                    if self.flushed_tools.contains(&tc.index) {
                        return Err(StreamTranslationError(format!(
                            "upstream sent another delta for completed tool index {}",
                            tc.index
                        )));
                    }
                    let pending = self.pending_tools.entry(tc.index).or_default();
                    merge_field(&mut pending.id, tc.id.as_deref(), "tool id", tc.index)?;
                    if let Some(f) = &tc.function {
                        merge_field(
                            &mut pending.name,
                            f.name.as_deref(),
                            "function name",
                            tc.index,
                        )?;
                        if let Some(args) = &f.arguments {
                            pending.arguments.push_str(args);
                        }
                    }
                }
            }

            if let Some(fr) = &choice.finish_reason {
                if let Some(existing) = &self.finish_reason {
                    if existing != fr {
                        return Err(StreamTranslationError(format!(
                            "conflicting upstream finish reasons: {existing} and {fr}"
                        )));
                    }
                }
                self.finish_reason = Some(fr.clone());
            }
        }

        Ok(out)
    }

    fn flush_tools(&mut self, out: &mut Vec<SseEvent>) -> Result<(), StreamTranslationError> {
        self.close_open(out);
        let tools = std::mem::take(&mut self.pending_tools);
        for (upstream_index, tool) in tools {
            let name = tool.name.filter(|name| !name.is_empty()).ok_or_else(|| {
                StreamTranslationError(format!(
                    "upstream tool index {upstream_index} is missing a function name"
                ))
            })?;
            let arguments = if tool.arguments.trim().is_empty() {
                "{}".to_owned()
            } else {
                tool.arguments
            };
            let input: Value = serde_json::from_str(&arguments).map_err(|error| {
                StreamTranslationError(format!(
                    "upstream returned invalid JSON arguments for tool '{name}': {error}"
                ))
            })?;
            if !input.is_object() {
                return Err(StreamTranslationError(format!(
                    "upstream arguments for tool '{name}' must be a JSON object"
                )));
            }

            let index = self.next_index;
            self.next_index += 1;
            let id = tool
                .id
                .filter(|id| !id.is_empty())
                .unwrap_or_else(|| format!("call_ccx{upstream_index}"));
            out.push(SseEvent::new(
                "content_block_start",
                json!({"type":"content_block_start","index":index,"content_block":{"type":"tool_use","id":id,"name":name,"input":{}}}),
            ));
            out.push(SseEvent::new(
                "content_block_delta",
                json!({"type":"content_block_delta","index":index,"delta":{"type":"input_json_delta","partial_json":arguments}}),
            ));
            out.push(SseEvent::new(
                "content_block_stop",
                json!({"type": "content_block_stop", "index": index}),
            ));
            self.flushed_tools.insert(upstream_index);
        }
        Ok(())
    }

    pub fn finish(&mut self) -> Result<Vec<SseEvent>, StreamTranslationError> {
        let mut out = Vec::new();
        self.close_open(&mut out);
        self.flush_tools(&mut out)?;
        if self.finish_reason.is_none() {
            return Err(StreamTranslationError(
                "upstream stream ended without a finish_reason".into(),
            ));
        }
        let stop_reason = map_stop_reason(self.finish_reason.as_deref(), self.saw_tool_call);
        out.push(SseEvent::new(
            "message_delta",
            json!({"type":"message_delta","delta":{"stop_reason":stop_reason,"stop_sequence":null},"usage":{"output_tokens":self.output_tokens}}),
        ));
        out.push(SseEvent::new(
            "message_stop",
            json!({"type":"message_stop"}),
        ));
        Ok(out)
    }

    pub fn output_tokens(&self) -> u32 {
        self.output_tokens
    }
}

fn merge_field(
    current: &mut Option<String>,
    incoming: Option<&str>,
    field: &str,
    index: u32,
) -> Result<(), StreamTranslationError> {
    let Some(incoming) = incoming.filter(|value| !value.is_empty()) else {
        return Ok(());
    };
    match current {
        Some(existing) if existing != incoming => Err(StreamTranslationError(format!(
            "upstream changed {field} for tool index {index}"
        ))),
        Some(_) => Ok(()),
        None => {
            *current = Some(incoming.to_owned());
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn chunk(j: &str) -> ChatChunk {
        serde_json::from_str(j).unwrap()
    }

    /// Drive the translator through a list of chunk JSONs and return the full
    /// ordered list of emitted event names.
    fn run(model: &str, chunks: &[&str]) -> (Vec<SseEvent>, Vec<String>) {
        let mut t = StreamTranslator::new(model, "msg_1");
        let mut events = vec![t.start()];
        for c in chunks {
            events.extend(t.push(&chunk(c)).unwrap());
        }
        events.extend(t.finish().unwrap());
        let names: Vec<String> = events.iter().map(|e| e.event.clone()).collect();
        (events, names)
    }

    #[test]
    fn text_only_stream() {
        let (events, names) = run(
            "m",
            &[
                r#"{"choices":[{"delta":{"role":"assistant"}}]}"#,
                r#"{"choices":[{"delta":{"content":"Hel"}}]}"#,
                r#"{"choices":[{"delta":{"content":"lo"}}]}"#,
                r#"{"choices":[{"delta":{},"finish_reason":"stop"}]}"#,
            ],
        );
        assert_eq!(
            names,
            vec![
                "message_start",
                "content_block_start",
                "content_block_delta",
                "content_block_delta",
                "content_block_stop",
                "message_delta",
                "message_stop",
            ]
        );
        // stop_reason end_turn
        let md = events.iter().find(|e| e.event == "message_delta").unwrap();
        assert_eq!(md.data["delta"]["stop_reason"], "end_turn");
    }

    #[test]
    fn tool_call_stream_accumulates_partial_json() {
        let (events, names) = run(
            "m",
            &[
                r#"{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"read","arguments":""}}]}}]}"#,
                r#"{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"path\":"}}]}}]}"#,
                r#"{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\"a.txt\"}"}}]}}]}"#,
                r#"{"choices":[{"delta":{},"finish_reason":"tool_calls"}]}"#,
            ],
        );
        assert_eq!(
            names,
            vec![
                "message_start",
                "content_block_start",
                "content_block_delta",
                "content_block_stop",
                "message_delta",
                "message_stop",
            ]
        );
        let start = events
            .iter()
            .find(|e| e.event == "content_block_start")
            .unwrap();
        assert_eq!(start.data["content_block"]["type"], "tool_use");
        assert_eq!(start.data["content_block"]["name"], "read");
        let deltas: Vec<&SseEvent> = events
            .iter()
            .filter(|e| e.event == "content_block_delta")
            .collect();
        assert_eq!(deltas[0].data["delta"]["type"], "input_json_delta");
        assert_eq!(
            deltas[0].data["delta"]["partial_json"],
            "{\"path\":\"a.txt\"}"
        );
        let md = events.iter().find(|e| e.event == "message_delta").unwrap();
        assert_eq!(md.data["delta"]["stop_reason"], "tool_use");
    }

    #[test]
    fn interleaved_text_then_tool_then_text_closes_blocks_correctly() {
        // The PR #1356 regression: text after a tool block must open a NEW
        // text block, not be sent into the still-open tool_use block.
        let (events, _names) = run(
            "m",
            &[
                r#"{"choices":[{"delta":{"content":"thinking"}}]}"#,
                r#"{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"read","arguments":"{}"}}]}}]}"#,
                r#"{"choices":[{"delta":{"content":"done"}}]}"#,
                r#"{"choices":[{"delta":{},"finish_reason":"stop"}]}"#,
            ],
        );
        // Expect three blocks at indices 0 (text), 1 (tool), 2 (text), each
        // opened and closed, never overlapping.
        let starts: Vec<&SseEvent> = events
            .iter()
            .filter(|e| e.event == "content_block_start")
            .collect();
        let stops: Vec<&SseEvent> = events
            .iter()
            .filter(|e| e.event == "content_block_stop")
            .collect();
        assert_eq!(starts.len(), 3, "three blocks opened");
        assert_eq!(stops.len(), 3, "three blocks closed");
        assert_eq!(starts[0].data["index"], 0);
        assert_eq!(starts[0].data["content_block"]["type"], "text");
        assert_eq!(starts[1].data["index"], 1);
        assert_eq!(starts[1].data["content_block"]["type"], "tool_use");
        assert_eq!(starts[2].data["index"], 2);
        assert_eq!(starts[2].data["content_block"]["type"], "text");
        // Every block is closed before the next opens: stop index k precedes start k+1.
        let order: Vec<(&str, i64)> = events
            .iter()
            .filter(|e| e.event == "content_block_start" || e.event == "content_block_stop")
            .map(|e| (e.event.as_str(), e.data["index"].as_i64().unwrap()))
            .collect();
        assert_eq!(
            order,
            vec![
                ("content_block_start", 0),
                ("content_block_stop", 0),
                ("content_block_start", 1),
                ("content_block_stop", 1),
                ("content_block_start", 2),
                ("content_block_stop", 2),
            ]
        );
    }

    #[test]
    fn final_usage_chunk_sets_output_tokens() {
        let (events, _) = run(
            "m",
            &[
                r#"{"choices":[{"delta":{"content":"hi"}}]}"#,
                r#"{"choices":[{"delta":{},"finish_reason":"stop"}]}"#,
                r#"{"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":7}}"#,
            ],
        );
        let md = events.iter().find(|e| e.event == "message_delta").unwrap();
        assert_eq!(md.data["usage"]["output_tokens"], 7);
    }

    #[test]
    fn wire_format_has_event_and_data_lines() {
        let ev = SseEvent::new("message_stop", json!({"type":"message_stop"}));
        assert_eq!(
            ev.to_wire(),
            "event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n"
        );
    }

    #[test]
    fn interleaved_parallel_tools_are_buffered_and_emitted_sequentially() {
        let (events, _) = run(
            "m",
            &[
                r#"{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"a","function":{"name":"read","arguments":"{\"p\":"}},{"index":1,"id":"b","function":{"name":"glob","arguments":"{\"g\":"}}]}}]}"#,
                r#"{"choices":[{"delta":{"tool_calls":[{"index":1,"function":{"arguments":"\"*.rs\"}"}},{"index":0,"function":{"arguments":"\"a.rs\"}"}}]},"finish_reason":"tool_calls"}]}"#,
            ],
        );
        let starts: Vec<_> = events
            .iter()
            .filter(|event| event.event == "content_block_start")
            .collect();
        assert_eq!(starts.len(), 2);
        assert_eq!(starts[0].data["content_block"]["id"], "a");
        assert_eq!(starts[1].data["content_block"]["id"], "b");
        let order: Vec<_> = events
            .iter()
            .filter(|event| {
                event.event == "content_block_start" || event.event == "content_block_stop"
            })
            .map(|event| (event.event.as_str(), event.data["index"].as_u64().unwrap()))
            .collect();
        assert_eq!(
            order,
            vec![
                ("content_block_start", 0),
                ("content_block_stop", 0),
                ("content_block_start", 1),
                ("content_block_stop", 1),
            ]
        );
    }

    #[test]
    fn malformed_complete_tool_json_is_rejected() {
        let mut translator = StreamTranslator::new("m", "id");
        translator
            .push(&chunk(
                r#"{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"read","arguments":"not-json"}}]},"finish_reason":"tool_calls"}]}"#,
            ))
            .unwrap();
        assert!(translator
            .finish()
            .unwrap_err()
            .to_string()
            .contains("invalid JSON arguments"));
    }
}
