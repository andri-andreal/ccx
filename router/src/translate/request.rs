//! Anthropic `MessagesRequest` -> OpenAI `ChatRequest`.

use serde_json::{json, Value};

use crate::types::*;

pub fn to_openai(req: &MessagesRequest) -> ChatRequest {
    let mut messages: Vec<OpenAiMessage> = Vec::new();

    if let Some(sys) = &req.system {
        let text = flatten_text(sys);
        if !text.is_empty() {
            messages.push(OpenAiMessage {
                role: "system".into(),
                content: Some(MessageContent::Text(text)),
                ..Default::default()
            });
        }
    }

    for m in &req.messages {
        translate_message(m, &mut messages);
    }

    ChatRequest {
        model: req.model.clone(),
        messages,
        max_tokens: req.max_tokens,
        temperature: req.temperature,
        stop: req.stop_sequences.clone(),
        // Always explicit: some upstreams (e.g. 9router) stream when the field is
        // absent, and Claude Code's non-streaming calls (auto mode classifier)
        // omit it, so the SSE body would fail to parse as a chat completion.
        stream: Some(req.stream.unwrap_or(false)),
        tools: req
            .tools
            .as_ref()
            .map(|ts| ts.iter().map(tool_to_openai).collect()),
        tool_choice: req.tool_choice.as_ref().map(tool_choice_to_openai),
    }
}

fn translate_message(m: &AnthropicMessage, out: &mut Vec<OpenAiMessage>) {
    let blocks = match &m.content {
        StringOrBlocks::Text(s) => {
            out.push(OpenAiMessage {
                role: m.role.clone(),
                content: Some(MessageContent::Text(s.clone())),
                ..Default::default()
            });
            return;
        }
        StringOrBlocks::Blocks(b) => b,
    };

    if m.role == "assistant" {
        // Concatenate text, collect tool_use -> tool_calls. One assistant message.
        let mut text = String::new();
        let mut tool_calls: Vec<ToolCall> = Vec::new();
        for b in blocks {
            match b {
                ContentBlock::Text { text: t } => text.push_str(t),
                ContentBlock::ToolUse { id, name, input } => tool_calls.push(ToolCall {
                    id: id.clone(),
                    kind: "function".into(),
                    function: FunctionCall {
                        name: name.clone(),
                        arguments: serde_json::to_string(input).unwrap_or_else(|_| "{}".into()),
                    },
                }),
                _ => {}
            }
        }
        out.push(OpenAiMessage {
            role: "assistant".into(),
            content: if text.is_empty() {
                None
            } else {
                Some(MessageContent::Text(text))
            },
            tool_calls: if tool_calls.is_empty() {
                None
            } else {
                Some(tool_calls)
            },
            ..Default::default()
        });
        return;
    }

    // role == "user" (or other): tool_result blocks become separate `tool`
    // messages first; remaining text/image blocks become one user message.
    let mut parts: Vec<ContentPart> = Vec::new();
    let mut plain_text = String::new();
    let mut has_image = false;
    for b in blocks {
        match b {
            ContentBlock::ToolResult {
                tool_use_id,
                content,
                ..
            } => {
                out.push(OpenAiMessage {
                    role: "tool".into(),
                    content: Some(MessageContent::Text(
                        content.as_ref().map(flatten_text).unwrap_or_default(),
                    )),
                    tool_call_id: Some(tool_use_id.clone()),
                    ..Default::default()
                });
            }
            ContentBlock::Text { text } => {
                plain_text.push_str(text);
                parts.push(ContentPart::Text { text: text.clone() });
            }
            ContentBlock::Image { source } => {
                has_image = true;
                parts.push(ContentPart::ImageUrl {
                    image_url: ImageUrl {
                        url: format!("data:{};base64,{}", source.media_type, source.data),
                    },
                });
            }
            ContentBlock::ToolUse { .. } => {}
        }
    }

    if has_image {
        out.push(OpenAiMessage {
            role: m.role.clone(),
            content: Some(MessageContent::Parts(parts)),
            ..Default::default()
        });
    } else if !plain_text.is_empty() {
        out.push(OpenAiMessage {
            role: m.role.clone(),
            content: Some(MessageContent::Text(plain_text)),
            ..Default::default()
        });
    }
}

fn flatten_text(s: &StringOrBlocks) -> String {
    match s {
        StringOrBlocks::Text(t) => t.clone(),
        StringOrBlocks::Blocks(blocks) => {
            let mut out = String::new();
            for b in blocks {
                if let ContentBlock::Text { text } = b {
                    out.push_str(text);
                }
            }
            out
        }
    }
}

fn tool_to_openai(t: &AnthropicTool) -> OpenAiTool {
    OpenAiTool {
        kind: "function".into(),
        function: OpenAiFunction {
            name: t.name.clone(),
            description: t.description.clone(),
            parameters: t.input_schema.clone(),
        },
    }
}

fn tool_choice_to_openai(tc: &AnthropicToolChoice) -> Value {
    match tc {
        AnthropicToolChoice::Auto => json!("auto"),
        AnthropicToolChoice::Any => json!("required"),
        AnthropicToolChoice::Tool { name } => {
            json!({"type": "function", "function": {"name": name}})
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(j: &str) -> MessagesRequest {
        serde_json::from_str(j).unwrap()
    }

    #[test]
    fn plain_text_turn() {
        let req = parse(
            r#"{"model":"m","max_tokens":100,"messages":[{"role":"user","content":"hello"}]}"#,
        );
        let out = to_openai(&req);
        assert_eq!(out.model, "m");
        assert_eq!(out.max_tokens, Some(100));
        assert_eq!(out.messages.len(), 1);
        assert_eq!(out.messages[0].role, "user");
        let v = serde_json::to_value(&out.messages[0].content).unwrap();
        assert_eq!(v, json!("hello"));
    }

    #[test]
    fn system_prepended() {
        let req = parse(
            r#"{"model":"m","system":"be terse","messages":[{"role":"user","content":"hi"}]}"#,
        );
        let out = to_openai(&req);
        assert_eq!(out.messages[0].role, "system");
        assert_eq!(out.messages[1].role, "user");
    }

    #[test]
    fn assistant_tool_use_becomes_tool_calls() {
        let req = parse(
            r#"{"model":"m","messages":[
            {"role":"assistant","content":[
                {"type":"text","text":"checking"},
                {"type":"tool_use","id":"t1","name":"read","input":{"path":"a.txt"}}
            ]}
        ]}"#,
        );
        let out = to_openai(&req);
        assert_eq!(out.messages.len(), 1);
        let msg = &out.messages[0];
        assert_eq!(msg.role, "assistant");
        let tc = msg.tool_calls.as_ref().unwrap();
        assert_eq!(tc.len(), 1);
        assert_eq!(tc[0].function.name, "read");
        assert_eq!(tc[0].function.arguments, "{\"path\":\"a.txt\"}");
    }

    #[test]
    fn tool_result_becomes_separate_tool_message() {
        let req = parse(
            r#"{"model":"m","messages":[
            {"role":"user","content":[
                {"type":"tool_result","tool_use_id":"t1","content":"file body"},
                {"type":"text","text":"now what?"}
            ]}
        ]}"#,
        );
        let out = to_openai(&req);
        // one tool message + one user message
        assert_eq!(out.messages.len(), 2);
        assert_eq!(out.messages[0].role, "tool");
        assert_eq!(out.messages[0].tool_call_id.as_deref(), Some("t1"));
        assert_eq!(
            serde_json::to_value(&out.messages[0].content).unwrap(),
            json!("file body")
        );
        assert_eq!(out.messages[1].role, "user");
        assert_eq!(
            serde_json::to_value(&out.messages[1].content).unwrap(),
            json!("now what?")
        );
    }

    #[test]
    fn tools_and_tool_choice_translated() {
        let req = parse(
            r#"{"model":"m","messages":[{"role":"user","content":"hi"}],
            "tools":[{"name":"read","description":"read a file","input_schema":{"type":"object"}}],
            "tool_choice":{"type":"any"}}"#,
        );
        let out = to_openai(&req);
        let tools = out.tools.as_ref().unwrap();
        assert_eq!(tools[0].kind, "function");
        assert_eq!(tools[0].function.name, "read");
        assert_eq!(out.tool_choice.as_ref().unwrap(), &json!("required"));
    }

    #[test]
    fn image_block_becomes_image_url_part() {
        let req = parse(
            r#"{"model":"m","messages":[
            {"role":"user","content":[
                {"type":"text","text":"look"},
                {"type":"image","source":{"type":"base64","media_type":"image/png","data":"AAAA"}}
            ]}
        ]}"#,
        );
        let out = to_openai(&req);
        assert_eq!(out.messages.len(), 1);
        let v = serde_json::to_value(&out.messages[0].content).unwrap();
        assert_eq!(v[0]["type"], "text");
        assert_eq!(v[1]["type"], "image_url");
        assert_eq!(v[1]["image_url"]["url"], "data:image/png;base64,AAAA");
    }

    #[test]
    fn stop_sequences_mapped_to_stop() {
        let req = parse(
            r#"{"model":"m","messages":[{"role":"user","content":"hi"}],"stop_sequences":["X"],"stream":true}"#,
        );
        let out = to_openai(&req);
        assert_eq!(out.stop, Some(vec!["X".to_string()]));
        assert_eq!(out.stream, Some(true));
    }

    #[test]
    fn missing_stream_sent_as_explicit_false() {
        let req = parse(r#"{"model":"m","messages":[{"role":"user","content":"hi"}]}"#);
        let v = serde_json::to_value(to_openai(&req)).unwrap();
        assert_eq!(v["stream"], json!(false));
    }
}
