//! OpenAI `ChatResponse` -> Anthropic `MessagesResponse` (non-streaming).

use serde_json::{json, Value};

use crate::types::*;

pub fn to_anthropic(resp: &ChatResponse, model: &str) -> MessagesResponse {
    let mut content: Vec<ContentBlock> = Vec::new();
    let mut stop_reason = "end_turn".to_string();

    if let Some(choice) = resp.choices.first() {
        if let Some(text) = &choice.message.content {
            if !text.is_empty() {
                content.push(ContentBlock::Text { text: text.clone() });
            }
        }
        let mut has_tool_calls = false;
        if let Some(tcs) = &choice.message.tool_calls {
            for tc in tcs {
                has_tool_calls = true;
                let input: Value =
                    serde_json::from_str(&tc.function.arguments).unwrap_or_else(|_| json!({}));
                content.push(ContentBlock::ToolUse {
                    id: tc.id.clone(),
                    name: tc.function.name.clone(),
                    input,
                });
            }
        }
        stop_reason = map_stop_reason(choice.finish_reason.as_deref(), has_tool_calls);
    }

    MessagesResponse {
        id: resp.id.clone().unwrap_or_else(|| "msg_ccxrouter".into()),
        kind: "message".into(),
        role: "assistant".into(),
        model: model.into(),
        content,
        stop_reason: Some(stop_reason),
        stop_sequence: None,
        usage: AnthropicUsage {
            input_tokens: resp
                .usage
                .as_ref()
                .and_then(|u| u.prompt_tokens)
                .unwrap_or(0),
            output_tokens: resp
                .usage
                .as_ref()
                .and_then(|u| u.completion_tokens)
                .unwrap_or(0),
        },
    }
}

pub fn map_stop_reason(finish: Option<&str>, has_tool_calls: bool) -> String {
    if has_tool_calls {
        return "tool_use".into();
    }
    match finish {
        Some("length") => "max_tokens".into(),
        Some("tool_calls") => "tool_use".into(),
        _ => "end_turn".into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(j: &str) -> ChatResponse {
        serde_json::from_str(j).unwrap()
    }

    #[test]
    fn text_only_response() {
        let resp = parse(
            r#"{"id":"cmpl-1","choices":[{"message":{"content":"hi there"},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":2}}"#,
        );
        let out = to_anthropic(&resp, "qwen");
        assert_eq!(out.id, "cmpl-1");
        assert_eq!(out.model, "qwen");
        assert_eq!(out.stop_reason.as_deref(), Some("end_turn"));
        assert_eq!(out.usage.input_tokens, 5);
        assert_eq!(out.usage.output_tokens, 2);
        let v = serde_json::to_value(&out.content).unwrap();
        assert_eq!(v[0]["type"], "text");
        assert_eq!(v[0]["text"], "hi there");
    }

    #[test]
    fn tool_call_response_maps_to_tool_use() {
        let resp = parse(
            r#"{"choices":[{"message":{"content":null,"tool_calls":[{"id":"c1","type":"function","function":{"name":"read","arguments":"{\"path\":\"a.txt\"}"}}]},"finish_reason":"tool_calls"}]}"#,
        );
        let out = to_anthropic(&resp, "m");
        assert_eq!(out.stop_reason.as_deref(), Some("tool_use"));
        let v = serde_json::to_value(&out.content).unwrap();
        assert_eq!(v[0]["type"], "tool_use");
        assert_eq!(v[0]["id"], "c1");
        assert_eq!(v[0]["name"], "read");
        assert_eq!(v[0]["input"]["path"], "a.txt");
    }

    #[test]
    fn mixed_text_and_tool_call() {
        let resp = parse(
            r#"{"choices":[{"message":{"content":"let me read it","tool_calls":[{"id":"c1","type":"function","function":{"name":"read","arguments":"{}"}}]},"finish_reason":"tool_calls"}]}"#,
        );
        let out = to_anthropic(&resp, "m");
        let v = serde_json::to_value(&out.content).unwrap();
        assert_eq!(v[0]["type"], "text");
        assert_eq!(v[1]["type"], "tool_use");
        assert_eq!(out.stop_reason.as_deref(), Some("tool_use"));
    }

    #[test]
    fn length_finish_maps_to_max_tokens() {
        let resp =
            parse(r#"{"choices":[{"message":{"content":"truncated"},"finish_reason":"length"}]}"#);
        let out = to_anthropic(&resp, "m");
        assert_eq!(out.stop_reason.as_deref(), Some("max_tokens"));
    }

    #[test]
    fn bad_tool_arguments_fall_back_to_empty_object() {
        let resp = parse(
            r#"{"choices":[{"message":{"tool_calls":[{"id":"c1","type":"function","function":{"name":"x","arguments":"not json"}}]},"finish_reason":"tool_calls"}]}"#,
        );
        let out = to_anthropic(&resp, "m");
        let v = serde_json::to_value(&out.content).unwrap();
        assert_eq!(v[0]["input"], json!({}));
    }
}
