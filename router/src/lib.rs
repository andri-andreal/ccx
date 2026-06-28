//! ccx-router: a local translator from the Anthropic Messages API (which Claude
//! Code speaks) to the OpenAI Chat Completions API (which OpenAI / OpenRouter /
//! Ollama / vLLM / LM Studio speak). Started per-launch by `ccx` for
//! OpenAI-compatible profiles.

pub mod server;
pub mod translate;
pub mod types;
