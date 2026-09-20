//! Incremental Server-Sent Events decoder used for upstream OpenAI streams.
//!
//! Decoding is byte-oriented until a complete line is available. This is
//! important: a network chunk may split a multi-byte UTF-8 codepoint and must
//! never be passed through `from_utf8_lossy` independently.

use std::fmt;

const DEFAULT_MAX_EVENT_BYTES: usize = 4 * 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SseDecodeError(pub String);

impl fmt::Display for SseDecodeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for SseDecodeError {}

/// Incrementally decodes SSE records and returns the joined `data:` payload of
/// each completed event. Comments and fields other than `data` are ignored.
pub struct SseDecoder {
    pending: Vec<u8>,
    data_lines: Vec<String>,
    event_bytes: usize,
    max_event_bytes: usize,
    first_line: bool,
}

impl Default for SseDecoder {
    fn default() -> Self {
        Self::new(DEFAULT_MAX_EVENT_BYTES)
    }
}

impl SseDecoder {
    pub fn new(max_event_bytes: usize) -> Self {
        Self {
            pending: Vec::new(),
            data_lines: Vec::new(),
            event_bytes: 0,
            max_event_bytes,
            first_line: true,
        }
    }

    pub fn push(&mut self, bytes: &[u8]) -> Result<Vec<String>, SseDecodeError> {
        self.pending.extend_from_slice(bytes);

        let mut out = Vec::new();
        while let Some(newline) = self.pending.iter().position(|byte| *byte == b'\n') {
            let mut rest = self.pending.split_off(newline + 1);
            std::mem::swap(&mut rest, &mut self.pending);
            // `rest` is now the complete line including LF; `pending` is the
            // unconsumed suffix.
            rest.pop();
            if rest.last() == Some(&b'\r') {
                rest.pop();
            }
            self.process_line(&rest, &mut out)?;
        }
        if self.pending.len().saturating_add(self.event_bytes) > self.max_event_bytes {
            return Err(SseDecodeError(format!(
                "upstream SSE event exceeds {} bytes",
                self.max_event_bytes
            )));
        }
        Ok(out)
    }

    /// Flush a final unterminated line and dispatch the last record. Several
    /// compatible servers close directly after `[DONE]` without a blank line.
    pub fn finish(&mut self) -> Result<Vec<String>, SseDecodeError> {
        let mut out = Vec::new();
        if !self.pending.is_empty() {
            let line = std::mem::take(&mut self.pending);
            self.process_line(&line, &mut out)?;
        }
        self.dispatch(&mut out);
        Ok(out)
    }

    fn process_line(&mut self, bytes: &[u8], out: &mut Vec<String>) -> Result<(), SseDecodeError> {
        self.event_bytes = self.event_bytes.saturating_add(bytes.len() + 1);
        if self.event_bytes > self.max_event_bytes {
            return Err(SseDecodeError(format!(
                "upstream SSE event exceeds {} bytes",
                self.max_event_bytes
            )));
        }
        let mut line = std::str::from_utf8(bytes)
            .map_err(|_| SseDecodeError("upstream SSE contains invalid UTF-8".into()))?;
        if self.first_line {
            self.first_line = false;
            line = line.strip_prefix('\u{feff}').unwrap_or(line);
        }
        if line.is_empty() {
            self.dispatch(out);
            return Ok(());
        }
        if line.starts_with(':') {
            return Ok(());
        }

        let (field, raw_value) = line.split_once(':').unwrap_or((line, ""));
        let value = raw_value.strip_prefix(' ').unwrap_or(raw_value);
        if field == "data" {
            self.data_lines.push(value.to_owned());
        }
        Ok(())
    }

    fn dispatch(&mut self, out: &mut Vec<String>) {
        if !self.data_lines.is_empty() {
            out.push(self.data_lines.join("\n"));
            self.data_lines.clear();
        }
        self.event_bytes = 0;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn handles_crlf_comments_and_multiline_data() {
        let mut decoder = SseDecoder::default();
        let events = decoder
            .push(b": keepalive\r\ndata: {\r\ndata: \"x\":1}\r\n\r\n")
            .unwrap();
        assert_eq!(events, vec!["{\n\"x\":1}"]);
    }

    #[test]
    fn preserves_utf8_split_across_network_chunks() {
        let wire = "data: {\"text\":\"halo 👋\"}\n\n".as_bytes();
        let split = wire
            .windows(4)
            .position(|window| window == "👋".as_bytes())
            .unwrap()
            + 2;
        let mut decoder = SseDecoder::default();
        assert!(decoder.push(&wire[..split]).unwrap().is_empty());
        let events = decoder.push(&wire[split..]).unwrap();
        assert_eq!(events, vec!["{\"text\":\"halo 👋\"}"]);
    }

    #[test]
    fn flushes_unterminated_final_event() {
        let mut decoder = SseDecoder::default();
        assert!(decoder.push(b"data: [DONE]").unwrap().is_empty());
        assert_eq!(decoder.finish().unwrap(), vec!["[DONE]"]);
    }

    #[test]
    fn rejects_invalid_utf8_and_oversized_event() {
        let mut invalid = SseDecoder::default();
        assert!(invalid.push(b"data: \xff\n\n").is_err());

        let mut large = SseDecoder::new(8);
        assert!(large.push(b"data: 123456789").is_err());
    }
}
