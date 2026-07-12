//! Streaming translation: OpenAI Chat Completions SSE chunks -> Anthropic
//! Messages SSE events.
//!
//! The correctness crux is tracking the currently-open content block and its
//! type, and emitting `content_block_stop` before opening a different block.
//! Models that interleave text and tool_use in one response broke ccr
//! (PR #1356, "Content block is not a text block") precisely because it failed
//! to do this.

use std::collections::HashMap;

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
}

#[derive(Clone, Copy, PartialEq)]
enum Open {
    None,
    Text(usize),
    Tool(usize),
}

pub struct StreamTranslator {
    model: String,
    id: String,
    open: Open,
    next_index: usize,
    tool_index: HashMap<u32, usize>, // OpenAI tool_call index -> Anthropic block index
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
            tool_index: HashMap::new(),
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
            Open::Text(i) | Open::Tool(i) => i,
        };
        out.push(SseEvent::new(
            "content_block_stop",
            json!({"type": "content_block_stop", "index": idx}),
        ));
        self.open = Open::None;
    }

    pub fn push(&mut self, chunk: &ChatChunk) -> Vec<SseEvent> {
        let mut out = Vec::new();

        if let Some(u) = &chunk.usage {
            if let Some(o) = u.completion_tokens {
                self.output_tokens = o;
            }
        }

        for choice in &chunk.choices {
            // Text delta.
            if let Some(text) = &choice.delta.content {
                if !text.is_empty() {
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
                for tc in tcs {
                    self.saw_tool_call = true;
                    let aidx = match self.tool_index.get(&tc.index) {
                        Some(&i) => i,
                        None => {
                            // New tool call: close whatever is open, open a tool block.
                            self.close_open(&mut out);
                            let idx = self.next_index;
                            self.next_index += 1;
                            self.tool_index.insert(tc.index, idx);
                            self.open = Open::Tool(idx);
                            let name = tc
                                .function
                                .as_ref()
                                .and_then(|f| f.name.clone())
                                .unwrap_or_default();
                            let id = tc.id.clone().unwrap_or_else(|| format!("call_{idx}"));
                            out.push(SseEvent::new(
                                "content_block_start",
                                json!({"type":"content_block_start","index":idx,"content_block":{"type":"tool_use","id":id,"name":name,"input":{}}}),
                            ));
                            idx
                        }
                    };
                    if let Some(f) = &tc.function {
                        if let Some(args) = &f.arguments {
                            if !args.is_empty() {
                                out.push(SseEvent::new(
                                    "content_block_delta",
                                    json!({"type":"content_block_delta","index":aidx,"delta":{"type":"input_json_delta","partial_json":args}}),
                                ));
                            }
                        }
                    }
                }
            }

            if let Some(fr) = &choice.finish_reason {
                self.finish_reason = Some(fr.clone());
            }
        }

        out
    }

    pub fn finish(&mut self) -> Vec<SseEvent> {
        let mut out = Vec::new();
        self.close_open(&mut out);
        let stop_reason = map_stop_reason(self.finish_reason.as_deref(), self.saw_tool_call);
        out.push(SseEvent::new(
            "message_delta",
            json!({"type":"message_delta","delta":{"stop_reason":stop_reason,"stop_sequence":null},"usage":{"output_tokens":self.output_tokens}}),
        ));
        out.push(SseEvent::new(
            "message_stop",
            json!({"type":"message_stop"}),
        ));
        out
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
            events.extend(t.push(&chunk(c)));
        }
        events.extend(t.finish());
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
        assert_eq!(deltas[0].data["delta"]["partial_json"], "{\"path\":");
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
}
