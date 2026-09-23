//! Anthropic `MessagesRequest` -> OpenAI `ChatRequest`.

use std::collections::HashSet;
use std::fmt;

use serde_json::{json, Value};

use crate::types::*;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RequestTranslationError(pub String);

impl fmt::Display for RequestTranslationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for RequestTranslationError {}

pub fn to_openai(req: &MessagesRequest) -> Result<ChatRequest, RequestTranslationError> {
    validate(req)?;
    let mut messages: Vec<OpenAiMessage> = Vec::new();

    if let Some(sys) = &req.system {
        let text = flatten_text(sys)?;
        if !text.is_empty() {
            messages.push(OpenAiMessage {
                role: "system".into(),
                content: Some(MessageContent::Text(text)),
                ..Default::default()
            });
        }
    }

    for m in &req.messages {
        translate_message(m, &mut messages)?;
    }

    Ok(ChatRequest {
        model: req.model.clone(),
        messages,
        max_tokens: req.max_tokens,
        temperature: req.temperature,
        top_p: req.top_p,
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
        parallel_tool_calls: req
            .tool_choice
            .as_ref()
            .and_then(disable_parallel_tool_use)
            .map(|disabled| !disabled),
        user: req.metadata.as_ref().and_then(|m| m.user_id.clone()),
        // Pinning is a per-upstream routing concern, so the server fills this
        // in once it knows which upstream the body is going to.
        provider: None,
    })
}

fn validate(req: &MessagesRequest) -> Result<(), RequestTranslationError> {
    if req.model.trim().is_empty() {
        return Err(RequestTranslationError("model must not be empty".into()));
    }
    if req.messages.is_empty() {
        return Err(RequestTranslationError("messages must not be empty".into()));
    }
    if req.top_k.is_some() {
        return Err(RequestTranslationError(
            "top_k is not supported by the OpenAI Chat Completions adapter".into(),
        ));
    }
    if req.thinking.is_some() {
        return Err(RequestTranslationError(
            "extended thinking cannot be translated losslessly to OpenAI Chat Completions".into(),
        ));
    }

    if let Some(sys) = &req.system {
        ensure_text_only(sys, "system")?;
    }

    let mut names = HashSet::new();
    if let Some(tools) = &req.tools {
        for tool in tools {
            if tool.name.trim().is_empty() {
                return Err(RequestTranslationError(
                    "tool name must not be empty".into(),
                ));
            }
            if !names.insert(tool.name.as_str()) {
                return Err(RequestTranslationError(format!(
                    "duplicate tool name: {}",
                    tool.name
                )));
            }
            if !tool.input_schema.is_object() {
                return Err(RequestTranslationError(format!(
                    "input_schema for tool '{}' must be a JSON object",
                    tool.name
                )));
            }
        }
    }
    if let Some(AnthropicToolChoice::Tool { name, .. }) = &req.tool_choice {
        if !names.contains(name.as_str()) {
            return Err(RequestTranslationError(format!(
                "tool_choice references undeclared tool '{name}'"
            )));
        }
    }

    for message in &req.messages {
        if message.role != "user" && message.role != "assistant" {
            return Err(RequestTranslationError(format!(
                "unsupported message role '{}'; expected user or assistant",
                message.role
            )));
        }
        if let StringOrBlocks::Blocks(blocks) = &message.content {
            if blocks.is_empty() {
                return Err(RequestTranslationError(
                    "message content blocks must not be empty".into(),
                ));
            }
            let mut saw_tool_use = false;
            for block in blocks {
                match (message.role.as_str(), block) {
                    ("assistant", ContentBlock::Text { .. }) if saw_tool_use => {
                        return Err(RequestTranslationError(
                            "assistant text after tool_use cannot be represented losslessly".into(),
                        ));
                    }
                    ("assistant", ContentBlock::Text { .. }) => {}
                    ("assistant", ContentBlock::ToolUse { input, .. }) => {
                        saw_tool_use = true;
                        if !input.is_object() {
                            return Err(RequestTranslationError(
                                "tool_use input must be a JSON object".into(),
                            ));
                        }
                    }
                    ("assistant", _) => {
                        return Err(RequestTranslationError(
                            "assistant messages may only contain text and tool_use blocks".into(),
                        ));
                    }
                    ("user", ContentBlock::Text { .. })
                    | ("user", ContentBlock::ToolResult { .. }) => {}
                    ("user", ContentBlock::Image { source, .. }) => {
                        if source.kind != "base64" {
                            return Err(RequestTranslationError(
                                "only base64 image sources are supported".into(),
                            ));
                        }
                    }
                    ("user", ContentBlock::ToolUse { .. }) => {
                        return Err(RequestTranslationError(
                            "user messages cannot contain tool_use blocks".into(),
                        ));
                    }
                    _ => unreachable!("message role was validated"),
                }
            }
        }
    }
    Ok(())
}

fn ensure_text_only(
    content: &StringOrBlocks,
    location: &str,
) -> Result<(), RequestTranslationError> {
    if let StringOrBlocks::Blocks(blocks) = content {
        if blocks
            .iter()
            .any(|block| !matches!(block, ContentBlock::Text { .. }))
        {
            return Err(RequestTranslationError(format!(
                "{location} may only contain text blocks for this adapter"
            )));
        }
    }
    Ok(())
}

fn translate_message(
    m: &AnthropicMessage,
    out: &mut Vec<OpenAiMessage>,
) -> Result<(), RequestTranslationError> {
    let blocks = match &m.content {
        StringOrBlocks::Text(s) => {
            out.push(OpenAiMessage {
                role: m.role.clone(),
                content: Some(MessageContent::Text(s.clone())),
                ..Default::default()
            });
            return Ok(());
        }
        StringOrBlocks::Blocks(b) => b,
    };

    if m.role == "assistant" {
        // Concatenate text, collect tool_use -> tool_calls. One assistant message.
        let mut text = String::new();
        let mut tool_calls: Vec<ToolCall> = Vec::new();
        for b in blocks {
            match b {
                ContentBlock::Text { text: t, .. } => text.push_str(t),
                ContentBlock::ToolUse {
                    id, name, input, ..
                } => tool_calls.push(ToolCall {
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
        return Ok(());
    }

    // Preserve the order of user content around tool results by flushing each
    // run of regular content before emitting a separate OpenAI `tool` message.
    let mut parts: Vec<ContentPart> = Vec::new();
    let flush_parts = |parts: &mut Vec<ContentPart>, out: &mut Vec<OpenAiMessage>| {
        if parts.is_empty() {
            return;
        }
        let content = if parts.len() == 1 {
            match parts.pop().expect("one part") {
                ContentPart::Text { text } => MessageContent::Text(text),
                image => MessageContent::Parts(vec![image]),
            }
        } else {
            MessageContent::Parts(std::mem::take(parts))
        };
        out.push(OpenAiMessage {
            role: "user".into(),
            content: Some(content),
            ..Default::default()
        });
    };
    for b in blocks {
        match b {
            ContentBlock::ToolResult {
                tool_use_id,
                content,
                is_error,
                ..
            } => {
                flush_parts(&mut parts, out);
                let mut text = match content {
                    Some(content) => flatten_text(content)?,
                    None => String::new(),
                };
                // Chat Completions has no is_error flag for tool messages. A
                // deterministic textual marker preserves the information for
                // the model instead of silently dropping it.
                if is_error.unwrap_or(false) {
                    text = format!("[tool_error]\n{text}");
                }
                out.push(OpenAiMessage {
                    role: "tool".into(),
                    content: Some(MessageContent::Text(text)),
                    tool_call_id: Some(tool_use_id.clone()),
                    ..Default::default()
                });
            }
            ContentBlock::Text { text, .. } => {
                parts.push(ContentPart::Text { text: text.clone() });
            }
            ContentBlock::Image { source, .. } => {
                parts.push(ContentPart::ImageUrl {
                    image_url: ImageUrl {
                        url: format!("data:{};base64,{}", source.media_type, source.data),
                    },
                });
            }
            ContentBlock::ToolUse { .. } => {}
        }
    }
    flush_parts(&mut parts, out);
    Ok(())
}

fn flatten_text(s: &StringOrBlocks) -> Result<String, RequestTranslationError> {
    match s {
        StringOrBlocks::Text(t) => Ok(t.clone()),
        StringOrBlocks::Blocks(blocks) => {
            let mut out = String::new();
            for b in blocks {
                match b {
                    ContentBlock::Text { text, .. } => out.push_str(text),
                    _ => {
                        return Err(RequestTranslationError(
                            "nested content may only contain text blocks".into(),
                        ))
                    }
                }
            }
            Ok(out)
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
            strict: t.strict,
        },
    }
}

fn tool_choice_to_openai(tc: &AnthropicToolChoice) -> Value {
    match tc {
        AnthropicToolChoice::Auto { .. } => json!("auto"),
        AnthropicToolChoice::Any { .. } => json!("required"),
        AnthropicToolChoice::Tool { name, .. } => {
            json!({"type": "function", "function": {"name": name}})
        }
        AnthropicToolChoice::None => json!("none"),
    }
}

fn disable_parallel_tool_use(choice: &AnthropicToolChoice) -> Option<bool> {
    match choice {
        AnthropicToolChoice::Auto {
            disable_parallel_tool_use,
        }
        | AnthropicToolChoice::Any {
            disable_parallel_tool_use,
        }
        | AnthropicToolChoice::Tool {
            disable_parallel_tool_use,
            ..
        } => *disable_parallel_tool_use,
        AnthropicToolChoice::None => None,
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
        let out = to_openai(&req).unwrap();
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
        let out = to_openai(&req).unwrap();
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
        let out = to_openai(&req).unwrap();
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
        let out = to_openai(&req).unwrap();
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
            "tools":[{"name":"read","description":"read a file","input_schema":{"type":"object"},"strict":true,"cache_control":{"type":"ephemeral"}}],
            "tool_choice":{"type":"any","disable_parallel_tool_use":true}}"#,
        );
        let out = to_openai(&req).unwrap();
        let tools = out.tools.as_ref().unwrap();
        assert_eq!(tools[0].kind, "function");
        assert_eq!(tools[0].function.name, "read");
        assert_eq!(tools[0].function.strict, Some(true));
        assert_eq!(out.tool_choice.as_ref().unwrap(), &json!("required"));
        assert_eq!(out.parallel_tool_calls, Some(false));
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
        let out = to_openai(&req).unwrap();
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
        let out = to_openai(&req).unwrap();
        assert_eq!(out.stop, Some(vec!["X".to_string()]));
        assert_eq!(out.stream, Some(true));
        assert!(out.stream_options.unwrap().include_usage);
    }

    #[test]
    fn prompt_cache_markers_are_accepted_but_not_forwarded() {
        let req = parse(
            r#"{"model":"m","system":[{"type":"text","text":"cached","cache_control":{"type":"ephemeral"}}],"messages":[{"role":"user","content":[{"type":"text","text":"hi","cache_control":{"type":"ephemeral"}}]}]}"#,
        );
        let out = to_openai(&req).unwrap();
        let encoded = serde_json::to_string(&out).unwrap();
        assert!(!encoded.contains("cache_control"));
        assert_eq!(out.messages.len(), 2);
    }

    #[test]
    fn lossy_top_k_and_thinking_are_rejected() {
        let top_k =
            parse(r#"{"model":"m","top_k":40,"messages":[{"role":"user","content":"hi"}]}"#);
        assert!(to_openai(&top_k).unwrap_err().to_string().contains("top_k"));
        let thinking = parse(
            r#"{"model":"m","thinking":{"type":"enabled","budget_tokens":1000},"messages":[{"role":"user","content":"hi"}]}"#,
        );
        assert!(to_openai(&thinking)
            .unwrap_err()
            .to_string()
            .contains("losslessly"));
    }

    #[test]
    fn missing_stream_sent_as_explicit_false() {
        let req = parse(r#"{"model":"m","messages":[{"role":"user","content":"hi"}]}"#);
        let v = serde_json::to_value(to_openai(&req)).unwrap();
        assert_eq!(v["stream"], json!(false));
    }
}
