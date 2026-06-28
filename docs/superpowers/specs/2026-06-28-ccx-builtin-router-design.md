# `ccx` — Built-in Anthropic→OpenAI Translator (`ccx-router`) — Design Spec

**Tanggal:** 2026-06-28
**Status:** Draft (menunggu review user)
**Menggantikan:** [`2026-06-28-ccx-openai-compatible-design.md`](./2026-06-28-ccx-openai-compatible-design.md) (pendekatan ccr) — lihat §2.
**Prasyarat dibaca:** [`2026-06-26-ccx-claude-model-switcher-design.md`](./2026-06-26-ccx-claude-model-switcher-design.md).

---

## 1. Ringkasan

Daripada menumpang `claude-code-router` (`ccr`, pihak ketiga), `ccx` menyertakan **penerjemah Anthropic⇄OpenAI sendiri**: sebuah binary Rust kecil bernama **`ccx-router`** yang `ccx` jalankan per-launch untuk profil OpenAI-compatible. Binary ini menerima Anthropic Messages API dari Claude Code, menerjemahkannya ke OpenAI Chat Completions, meneruskan ke endpoint upstream (OpenAI/OpenRouter/Ollama/vLLM/LM Studio), lalu menerjemahkan respons (termasuk streaming SSE & tool-call) kembali ke format Anthropic.

Kodenya **milik kita sepenuhnya & auditable** — tidak ada layanan pihak ketiga yang berjalan. Binary memakai *library* (crate) Rust mainstream yang di-compile masuk ke dalamnya (lihat §4.2); ini berbeda kategori dari mengandalkan aplikasi `ccr` yang terpisah dan di-maintain orang lain.

**Profil Anthropic-compatible (claude/minimax/glm/deepseek/kimi) tetap Bash murni, tidak tersentuh.**

## 2. Hubungan dengan spec ccr sebelumnya

Spec ini **menggantikan** pendekatan ccr. Yang **dipertahankan** dari implementasi branch `feat/openai-compatible-router`:
- Kerangka lifecycle Bash di `bin/ccx`: pilih port bebas, generate apikey, start router (kini `ccx-router`), health-check (port-readiness), set `ANTHROPIC_BASE_URL`, jalankan `claude` sebagai child, `trap` teardown, dukungan `CCX_DRY_RUN`.
- Struktur form GUI (provider picker + field upstream).
- Isolasi per-profil & masking secret.

Yang **diganti/dibuang**:
- `router_config_json` penghasil config ccr → tidak perlu (config via flag/env ke `ccx-router`).
- `CCX_TRANSFORMER` & `CCX_MODEL_*` → **dibuang**; model di-pass-through lewat slot `ANTHROPIC_DEFAULT_*` (lihat §5).
- Prasyarat `ccr` (npm) → diganti **binary `ccx-router`** yang di-build dari repo.

## 3. Keputusan desain (disepakati)

| Keputusan | Pilihan | Alasan |
|---|---|---|
| Penerjemah | **Milik sendiri**, bukan ccr/LiteLLM | Tidak bergantung layanan pihak ketiga; kontrol penuh atas korektness. |
| Bahasa/runtime | **Rust**, satu binary statis (`ccx-router`) | Nol runtime di mesin user; reuse ekosistem Rust repo (GUI). |
| Crate | tokio + axum + reqwest(rustls) + serde (lihat §4.2) | Library standar, di-compile ke binary kita, di-pin via `Cargo.lock`. |
| Model mapping | **Pass-through** via slot `ANTHROPIC_DEFAULT_*` | Kita kontrol dua ujung → tak perlu config "Router"/transformer ala ccr. |
| Cakupan provider | Sama: `openai/openrouter/ollama/vllm/lmstudio/custom-oai` | — |

## 4. Arsitektur `ccx-router`

### 4.1 Bentuk & lokasi crate

Crate biner baru di root repo: `router/` (`Cargo.toml` + `src/`), menghasilkan biner `ccx-router`. Berdiri sendiri (tidak menumpang workspace `gui/`) agar build CLI tidak menyeret toolchain GUI. Boleh berbagi tipe kecil nanti, tapi MVP mandiri.

```
router/
  Cargo.toml
  src/
    main.rs          # parse flag/env, start server
    server.rs        # axum app: /v1/messages, /v1/messages/count_tokens, /health
    translate/
      request.rs     # Anthropic -> OpenAI (messages, tools, tool_choice, images)
      response.rs    # OpenAI -> Anthropic (non-streaming)
      stream.rs      # OpenAI SSE -> Anthropic SSE (the hard part)
    types.rs         # serde structs untuk kedua format
```

### 4.2 Dependensi (crate)

- `tokio` — async runtime.
- `axum` — server HTTP (di atas hyper); ergonomis untuk handler + SSE.
- `reqwest` dengan fitur `rustls-tls` + `stream` — klien ke upstream (HTTPS untuk cloud, HTTP untuk lokal), streaming respons.
- `serde` + `serde_json` — (de)serialisasi kedua format.
- `futures` / `tokio-stream` — proses stream SSE.
- (opsional) `bytes`, `eventsource-stream` untuk parse SSE upstream; boleh parse manual bila ingin minim crate.

Semua di-pin lewat `Cargo.lock` yang di-commit. Tidak ada koneksi keluar selain ke upstream yang dikonfigurasi user.

### 4.3 Konfigurasi (dari `ccx`, tanpa file)

`ccx` menjalankan `ccx-router` dengan **flag non-rahasia + env untuk rahasia**:
- `--port <p>` — port listen di `127.0.0.1`.
- `--upstream-url <url>` — base OpenAI upstream (mis. `https://api.openai.com/v1` atau `http://localhost:11434/v1`); router menambah `/chat/completions`.
- `CCX_ROUTER_UPSTREAM_KEY` (env) — API key upstream (lewat env, bukan argv, agar tak terlihat di `ps`).
- `CCX_ROUTER_API_KEY` (env) — token acak yang dibuat `ccx`; router **mewajibkan** header `Authorization: Bearer <token>` ini cocok, sehingga hanya Claude Code milik user yang boleh memakai router.

### 4.4 Endpoint

- `POST /v1/messages` — terjemahkan & teruskan; dukung `stream: true` (SSE) **dan** non-streaming.
- `POST /v1/messages/count_tokens` — Claude Code memanggilnya; MVP balas estimasi heuristik (≈ panjang teks / 4) bila upstream tak menyediakan; cukup agar Claude Code jalan.
- `GET /health` — `200 OK` untuk kesiapan.

### 4.5 Translasi (inti)

**Request (Anthropic → OpenAI):**
- `model` → diteruskan apa adanya.
- `system` (string atau blok) → pesan `role: system`.
- `messages[]` blok → pesan OpenAI:
  - `text` → `content` string.
  - `tool_use` (assistant) → `assistant` + `tool_calls:[{id,type:function,function:{name,arguments(JSON string)}}]`.
  - `tool_result` (user) → pesan `role: tool` (`tool_call_id`, `content`).
  - `image` (base64) → `image_url` data-URL.
- `tools[]` `{name,description,input_schema}` → `{type:function,function:{name,description,parameters}}`.
- `tool_choice` `auto|any|tool` → `auto|required|{type:function,function:{name}}`.
- `max_tokens`, `temperature`, `stop_sequences`→`stop`, `stream`.

**Response non-streaming (OpenAI → Anthropic):**
- `choices[0].message` → blok konten: `text` + `tool_use` (dari `tool_calls`, `arguments` di-parse ke `input`).
- `finish_reason` → `stop_reason`: `stop`→`end_turn`, `length`→`max_tokens`, `tool_calls`→`tool_use`.
- `usage` → `input_tokens`/`output_tokens`.

**Response streaming (OpenAI SSE → Anthropic SSE) — bagian tersulit:**
Hasilkan urutan event Anthropic yang benar: `message_start` → (`content_block_start`/`content_block_delta`/`content_block_stop`) per blok → `message_delta` (stop_reason, usage) → `message_stop`. **Lacak tipe blok yang sedang terbuka** dan tutup blok sebelum membuka yang baru — ini persis bug yang menjatuhkan ccr (PR #1356) saat text & tool_use berselang-seling; kita lakukan dengan benar:
- delta `content` → blok `text` (`text_delta`).
- delta `tool_calls` → blok `tool_use`, argumen via `input_json_delta` (partial JSON).
- saat beralih jenis, kirim `content_block_stop` blok lama dulu.

## 5. Skema profil (disederhanakan)

Profil router kini nyaris sama dengan profil Anthropic, plus dua field upstream:

```sh
# ccx profile: local
CCX_PROVIDER=ollama
CCX_ISOLATE=true
CCX_ROUTER=builtin                         # penanda: pakai ccx-router (vs jalur exec biasa)
CCX_UPSTREAM_BASE_URL=http://localhost:11434/v1
CCX_UPSTREAM_API_KEY=                       # kosong untuk server lokal
ANTHROPIC_MODEL=qwen2.5-coder:32b           # ID model UPSTREAM, diteruskan apa adanya
ANTHROPIC_DEFAULT_OPUS_MODEL=qwen2.5-coder:32b
ANTHROPIC_DEFAULT_SONNET_MODEL=qwen2.5-coder:32b
ANTHROPIC_DEFAULT_HAIKU_MODEL=qwen2.5-coder:32b
```

- `CCX_TRANSFORMER` & `CCX_MODEL_*` **dibuang**.
- Model dipetakan lewat slot `ANTHROPIC_DEFAULT_*` (mekanisme yang sudah dipahami `ccx`/Claude Code), berisi ID model upstream. `ccx-router` meneruskan `model` apa adanya.
- `CCX_UPSTREAM_BASE_URL` kini **base** (`…/v1`), bukan endpoint penuh `…/chat/completions`.

## 6. Lifecycle eksekusi `ccx <profil-router>`

Hampir identik dengan implementasi sekarang, hanya target & config berubah:
1. Parse `profile.env`; lihat `CCX_ROUTER=builtin` → cabang router.
2. Preflight: cari biner `ccx-router` (`CCX_ROUTER_BIN`, default cari di PATH lalu `target/release/ccx-router` repo). Bila tak ada → error + saran build.
3. Pilih port bebas + apikey acak.
4. Start: `CCX_ROUTER_UPSTREAM_KEY=… CCX_ROUTER_API_KEY=… ccx-router --port <p> --upstream-url <url> &`; simpan PID.
5. `trap` teardown (kill PID) di EXIT/INT/TERM.
6. Health-check `GET /health` / port-open sampai siap atau timeout.
7. Export `ANTHROPIC_BASE_URL=http://127.0.0.1:<p>`, `ANTHROPIC_AUTH_TOKEN=<apikey>`, `CLAUDE_CONFIG_DIR`.
8. Jalankan `claude "$@"` sebagai child; teardown saat keluar; teruskan exit code.

`CCX_DRY_RUN=1` mencetak perintah `ccx-router` + port + env wiring (rahasia di-mask) tanpa start.

> Teardown `ccx-router` lebih sederhana dari ccr: ia foreground-able (kita `&`-kan), jadi `kill $PID` cukup — tak perlu `stop` terpisah maupun PID-file.

## 7. Build & distribusi

- **Prasyarat baru:** toolchain Rust (cargo) untuk mem-build `ccx-router`. (Menggantikan prasyarat `ccr` via npm.)
- `install.sh`: `cargo build --release --manifest-path router/Cargo.toml`, lalu symlink/copy `ccx-router` ke `~/.local/bin` (atau biarkan `ccx` menemukannya di `target/release`).
- Di luar lingkup MVP: distribusi binary prebuilt (release GitHub) agar user tanpa Rust tetap bisa pakai.

## 8. Cakupan MVP & di luar lingkup

**MVP (harus ada):** `/v1/messages` non-streaming **dan** streaming, tool-calling round-trip (text+tool_use, termasuk berselang-seling), tool_result, gambar (base64), `count_tokens` heuristik, stop_reason mapping, auth token lokal.

**Di luar lingkup MVP:** prompt caching (tak relevan), routing per-kategori (think/background beda model), pinning provider OpenRouter, binary prebuilt, Windows.

## 9. Risiko & mitigasi

- **Korektness streaming+tool-call** = risiko utama (sama seperti yang dihadapi semua proxy). Mitigasi: pelacakan tipe blok yang benar (§4.5) + test translasi berbasis fixture (lihat §10).
- **Kuirk per-provider** (mis. Ollama vs OpenAI beda kecil pada `tool_calls` streaming). Mitigasi: test fixture per-provider; mulai dari Ollama (lokal, mudah diuji) lalu OpenRouter.
- **Beban maintenance** pindah ke kita. Diterima sebagai konsekuensi keputusan.

## 10. Testing

- **Unit/fixture (Rust, `cargo test`):** beri JSON request Anthropic → assert JSON OpenAI yang dihasilkan; beri respons OpenAI (non-stream & potongan SSE) → assert event/JSON Anthropic. Termasuk kasus interleaved text+tool_use, multi tool_call, tool_result, gambar, stop_reason.
- **Integrasi (opsional):** upstream OpenAI-compatible tiruan (server kecil) → jalankan `ccx-router` → kirim request Anthropic → cek hasil; plus uji streaming.
- **CLI bash:** dry-run merakit wiring `ccx-router`; profil router tanpa `CCX_TRANSFORMER`/`CCX_MODEL_*`.
- **Smoke manual:** `ccx local` vs Ollama nyata; konfirmasi Edit/Bash (tool-call) jalan.
