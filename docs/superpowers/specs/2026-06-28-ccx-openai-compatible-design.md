# `ccx` — Provider OpenAI-Compatible via Local Router (`ccr`) — Design Spec

**Tanggal:** 2026-06-28
**Status:** Draft (menunggu review user)
**Diverifikasi terhadap:** riset deep-research 2026-06-28 (19 sumber, 25 klaim 3-vote, 0 gugur) — lihat §3.
**Prasyarat dibaca:** [`2026-06-26-ccx-claude-model-switcher-design.md`](./2026-06-26-ccx-claude-model-switcher-design.md) (arsitektur `ccx` dasar).

---

## 1. Ringkasan

Hari ini `ccx` hanya bisa memakai provider yang mengekspos endpoint **Anthropic-compatible** (`/v1/messages`), karena Claude Code berbicara Anthropic Messages API dan `ccx` cuma men-set env var lalu `exec claude`. Provider yang **hanya** punya format OpenAI Chat Completions (OpenAI, OpenRouter, Ollama, vLLM, LM Studio) tidak bisa dipakai langsung.

Spec ini menambah satu **provider kind baru: `openai-compatible`**. Untuk profil jenis ini, `ccx` menjalankan **[claude-code-router](https://github.com/musistudio/claude-code-router) (`ccr`)** sebagai router translasi Anthropic ⇄ OpenAI lokal — satu instance per peluncuran profil — lalu mengarahkan `ANTHROPIC_BASE_URL` ke router itu sebelum menjalankan `claude`.

**Profil Anthropic-compatible yang sudah ada (claude/minimax/glm/deepseek/kimi) TIDAK berubah sama sekali** — mereka tetap lewat jalur `exec claude` murni. Hanya profil ber-`CCX_ROUTER` yang masuk jalur lifecycle baru.

## 2. Keputusan desain (sudah disepakati)

| Keputusan | Pilihan | Alasan |
|---|---|---|
| Router | **claude-code-router (`ccr`)**, dikelola `ccx` | Purpose-built untuk Claude Code; tidak menambah dependensi Python (vs LiteLLM). |
| Cakupan provider | Satu kind `openai-compatible`, mencakup cloud + lokal sekaligus | Template: `openai`, `openrouter`, `ollama`, `vllm`, `lmstudio`, `custom-oai`. |
| Sikap risiko tool-call | **Default transformer `enhancetool`** (mematikan *streaming* tool-call) + dokumentasi gap jujur | Mitigasi bug streaming PR #1356 yang masih unmerged (lihat §3). |
| Perbandingan LiteLLM | **Dilewati** | Keputusan `ccr` final atas dasar kecocokan arsitektur; riset tidak menemukan bukti head-to-head (lihat §3 open question). |

### 2.1 Empat guardrail wajib (konsekuensi langsung dari riset)

1. **Pin versi `ccr` ke jalur v1.x.** npm `latest` kini v2.0.0 (rewrite Desktop/Electron) yang membuang dokumentasi CLI dan config-loader-nya **`throw` bila config tidak ada**. Kita butuh kontrak CLI v1.x (`ccr start/stop/status`, auto-behavior yang terdokumentasi).
2. **`ccx` menulis `config.json` sendiri + isolasi via `HOME` per-profil + `PORT` unik.** Tidak ada flag/env override config-path resmi (issue #977 masih open). Path config diturunkan dari `os.homedir()`, jadi isolasi dicapai dengan `HOME` per-profil.
3. **`ccx` mengelola proses `ccr` langsung** (spawn → simpan PID → `trap` teardown). Jangan andalkan `--daemon` (issue #1246 bilang bermasalah).
4. **Default `enhancetool` + bagian "Known gaps" yang eksplisit** di docs.

## 3. Fakta `ccr` yang menjadi dasar (terverifikasi riset 2026-06-28)

- **Lifecycle server-only ada:** `ccr start` (proxy saja), `ccr stop`, `ccr restart`, `ccr status`, terpisah dari `ccr code` (yang juga meluncurkan Claude Code). Endpoint `/health` + API `/v1` di `http://127.0.0.1:3456`.
- **Config:** satu file HOME-anchored `~/.claude-code-router/config.json` (Windows: `%APPDATA%\Claude Code Router\config.json`). **Tidak ada** override config-path/dir resmi (issue #977 open). Port = field `PORT` (number, default 3456) di config.json; `HOST` default `127.0.0.1`.
- **Schema config:**
  - `Providers[]`: `name`, `api_base_url` (**URL penuh**, mis. `http://localhost:11434/v1/chat/completions`), `api_key` (mendukung `${ENV}`), `models[]`, `transformer` (opsional).
  - `Router`: `default` (wajib), `background`, `think`, `longContext` (+ `longContextThreshold`, default 60000), `webSearch`. Nilai berformat `"provider_name,model_name"`.
  - Transformer: `{"use":["openai"]}` untuk Ollama/vLLM/LM Studio/generic; `{"use":["openrouter"]}` untuk OpenRouter; `enhancetool` menambah toleransi error argumen tool-call **dengan konsekuensi mematikan streaming tool-call**.
- **Risiko korektness (kritikal):**
  - Tool-calling adalah *hard requirement* Claude Code (Edit/Bash lewat situ). Model tanpa dukungan tool → error `"No endpoints found that support tool use"`.
  - **PR #1356 masih UNMERGED per 2026-06-28:** model yang meng-*interleave* text+tool_use dalam satu respons crash-kan Claude Code (`"Content block is not a text block"`). Terkonfirmasi berulang lintas proyek (sglang, cherry-studio, claude-code).
  - Transformer `reasoning` merusak argumen tool-call untuk model thinking (Qwen3) — issue #1397. Model DeepSeek-reasoning: `reasoning_content` di-strip tanpa re-inject → provider 400.
  - **Ironi target:** model lokal (Qwen3-thinking dll.) yang ROI-nya tertinggi juga yang paling rawan bug ini → ekspektasi user harus diset di docs.
- **Version drift = caveat dominan:** npm `latest` v2.0.0 Desktop rewrite membuang docs CLI dan `throw` saat config absen. → guardrail #1 & #2.
- **Open question yang TIDAK terjawab riset:** tidak ada perbandingan head-to-head `ccr` vs LiteLLM untuk reliabilitas tool-calling. Pemilihan `ccr` murni atas kecocokan arsitektur.

## 4. Arsitektur

### 4.1 Layout file (tambahan terhadap desain dasar)

```
~/.config/ccx/
  providers/
    openai.tmpl          # template baru — provider kind openai-compatible
    openrouter.tmpl
    ollama.tmpl
    vllm.tmpl
    lmstudio.tmpl
    custom-oai.tmpl
  profiles/
    <nama>/
      profile.env        # chmod 600 — kini bisa memuat field CCX_ROUTER + CCX_UPSTREAM_* + CCX_MODEL_*
      home/              # CLAUDE_CONFIG_DIR (chmod 700) — isolasi Claude Code
      router/            # BARU — HOME khusus ccr untuk isolasi config+PID (chmod 700)
        .claude-code-router/
          config.json    # digenerate ccx tiap launch (chmod 600)
```

> `router/` dipakai sebagai `HOME` saat men-spawn `ccr`, sehingga `ccr` menulis `config.json` & PID-nya di `router/.claude-code-router/` — terisolasi penuh dari `~/.claude-code-router` user dan dari profil lain.

### 4.2 Format `profile.env` — profil router

Semua key spesifik-router diberi prefix **`CCX_`** sehingga **otomatis tidak diekspor ke `claude`** (parser `cmd_run` sudah membuang semua `CCX_*`, lihat `bin/ccx:160`). `ccx` membaca key-key ini secara eksplisit untuk membangun config `ccr` dan menghitung `ANTHROPIC_BASE_URL` saat runtime.

Contoh profil **Ollama lokal** (`ccx new --provider ollama`):
```sh
# ccx profile: localqwen
CCX_PROVIDER=ollama
CCX_ROUTER=ccr
CCX_ISOLATE=true
CCX_UPSTREAM_BASE_URL=http://localhost:11434/v1/chat/completions
CCX_UPSTREAM_API_KEY=                       # kosong untuk Ollama
CCX_TRANSFORMER=enhancetool                 # default demi stabilitas tool-call
CCX_MODEL_DEFAULT=qwen2.5-coder:32b         # -> Router.default (model "execute")
CCX_MODEL_THINK=qwen2.5-coder:32b           # -> Router.think   (model "plan/reasoning")
CCX_MODEL_BACKGROUND=qwen2.5-coder:7b       # -> Router.background (model kecil/cepat)
CCX_MODEL_LONGCONTEXT=qwen2.5-coder:32b     # -> Router.longContext
```

Contoh profil **OpenRouter** (`ccx new --provider openrouter`):
```sh
# ccx profile: orouter
CCX_PROVIDER=openrouter
CCX_ROUTER=ccr
CCX_ISOLATE=true
CCX_UPSTREAM_BASE_URL=https://openrouter.ai/api/v1/chat/completions
CCX_UPSTREAM_API_KEY=sk-or-...              # diminta saat wizard (input tersembunyi)
CCX_TRANSFORMER=openrouter
CCX_MODEL_DEFAULT=qwen/qwen3-coder
CCX_MODEL_THINK=qwen/qwen3-coder
CCX_MODEL_BACKGROUND=qwen/qwen3-coder
CCX_MODEL_LONGCONTEXT=qwen/qwen3-coder
```

> **Pemetaan slot model.** Untuk kasus umum (satu model untuk semua), wizard hanya menanyakan satu model dan mengisi keempat `CCX_MODEL_*` dengan nilai yang sama (analog logika single-model pada `bin/ccx:99-109`). Pengguna lanjutan dapat membedakannya lewat `ccx edit`.

### 4.3 Template provider (default yang di-prefill wizard — semua editable)

| Provider | `CCX_UPSTREAM_BASE_URL` (default) | `CCX_TRANSFORMER` | API key | Catatan |
|---|---|---|---|---|
| `openai` | `https://api.openai.com/v1/chat/completions` | `enhancetool` | wajib | model mis. `gpt-4o` (harus tool-capable) |
| `openrouter` | `https://openrouter.ai/api/v1/chat/completions` | `openrouter` | wajib | pin provider via transformer agar tak ke endpoint non-tool/`:free` |
| `ollama` | `http://localhost:11434/v1/chat/completions` | `enhancetool` | kosong | pilih model tool-capable (mis. `qwen2.5-coder`) |
| `vllm` | `http://localhost:8000/v1/chat/completions` | `enhancetool` | kosong/opsional | server lokal |
| `lmstudio` | `http://localhost:1234/v1/chat/completions` | `enhancetool` | kosong | server lokal |
| `custom-oai` | *(kosong — diisi user)* | `enhancetool` | opsional | endpoint OpenAI-compatible apa pun |

> Semua nilai adalah **default template** yang bisa diedit; `ccx` tidak bergantung pada keakuratannya (sama prinsipnya dengan provider Anthropic-compatible yang sudah ada).

### 4.4 Config `ccr` yang digenerate `ccx`

Saat launch, `ccx` menulis `router/.claude-code-router/config.json` (chmod 600) dari `profile.env`:

```jsonc
{
  "HOST": "127.0.0.1",
  "PORT": <port-bebas-yang-dipilih-ccx>,
  "APIKEY": "<token-acak-per-launch>",      // diselaraskan dgn ANTHROPIC_AUTH_TOKEN
  "Providers": [
    {
      "name": "upstream",
      "api_base_url": "<CCX_UPSTREAM_BASE_URL>",
      "api_key": "<CCX_UPSTREAM_API_KEY atau placeholder>",
      "models": ["<CCX_MODEL_DEFAULT>", "<...THINK>", "<...BACKGROUND>", "<...LONGCONTEXT>"],
      "transformer": { "use": ["<CCX_TRANSFORMER>"] }
    }
  ],
  "Router": {
    "default":     "upstream,<CCX_MODEL_DEFAULT>",
    "think":       "upstream,<CCX_MODEL_THINK>",
    "background":  "upstream,<CCX_MODEL_BACKGROUND>",
    "longContext": "upstream,<CCX_MODEL_LONGCONTEXT>",
    "longContextThreshold": 60000
  }
}
```

- `models[]` di-dedup (sering keempatnya sama).
- `APIKEY` diisi token acak per-launch (`head -c 18 /dev/urandom | base64` saat runtime — tetap dependency-free); nilai yang sama diekspor sebagai `ANTHROPIC_AUTH_TOKEN` agar hop lokal terautentikasi.
- Untuk `openrouter`, transformer dapat diperluas menjadi `{"use":["openrouter", {"provider":{"only":[...]}}]}` (pinning) di iterasi lanjut — di luar lingkup MVP.

## 5. Alur eksekusi `ccx <profil-router> [args…]`

Cabang ini aktif **hanya bila `profile.env` memuat `CCX_ROUTER=ccr`**; selain itu jalur lama (`exec claude`) dipakai tanpa perubahan.

1. Parse `profile.env`. Deteksi `CCX_ROUTER` → masuk cabang router.
2. **Preflight:** pastikan biner `ccr` tersedia (`command -v "$CCX_CCR_BIN"`); jika tidak → error jelas + saran instalasi/pin versi. Pastikan `claude` tersedia.
3. **Pilih port bebas** pada `127.0.0.1` (probe via `/dev/tcp`, dependency-free).
4. **Generate config** `router/.claude-code-router/config.json` (chmod 600) dari profil + port + APIKEY acak.
5. **Spawn `ccr`** dengan `HOME="$pdir/router"` (mengisolasi config & PID), di background; simpan PID.
6. **Pasang `trap`** EXIT/INT/TERM → matikan router: `kill` PID yang disimpan **dan** `HOME="$pdir/router" "$CCX_CCR_BIN" stop` (belt-and-suspenders).
7. **Health-check:** poll `http://127.0.0.1:<port>/health` (curl bila ada; fallback cek port-open `/dev/tcp`) sampai siap atau timeout (default 15 dtk) → bila gagal, teardown + error.
8. **Set env untuk `claude`:** `ANTHROPIC_BASE_URL=http://127.0.0.1:<port>`, `ANTHROPIC_AUTH_TOKEN=<APIKEY>`, plus `CLAUDE_CONFIG_DIR` (isolasi Claude Code seperti biasa).
9. **Jalankan `claude "$@"` sebagai child** (BUKAN `exec`, agar `trap` teardown tetap jalan). Setelah `claude` keluar, teardown router; teruskan exit code `claude`.

> **Catatan implementasi (verifikasi saat coding terhadap versi ccr ter-pin):** apakah `ccr start` berjalan foreground (bisa di-`&`-kan dan PID-nya valid) atau men-detach sendiri. Jika men-detach, andalkan `ccr stop` + lookup PID file di `router/.claude-code-router/`.

## 6. Alur `ccx new` (tambahan wizard)

Menu provider diperluas: `claude / minimax / glm / deepseek / kimi / openai / openrouter / ollama / vllm / lmstudio / custom`. Bila provider yang dipilih memuat `CCX_ROUTER` di template:

1. **Nama profil** — validasi `^[A-Za-z0-9_-]+$`.
2. **Upstream base URL** — default dari template; user konfirmasi/edit.
3. **Model** — tanyakan satu model (kasus umum); isi keempat `CCX_MODEL_*`. (Flag `--think/--background/--longcontext` untuk membedakan, opsional.)
4. **API key upstream** — input tersembunyi; dilewati bila template menandai opsional/kosong (Ollama/vLLM/LM Studio lokal).
5. **Transformer** — default dari template (`enhancetool`/`openrouter`); dapat di-override `--transformer`.
6. **Isolasi** — selalu `true` untuk profil router (butuh `router/` & `home/`).
7. **Tulis** `profile.env` (chmod 600), buat `home/` & `router/` (chmod 700).

Flag non-interaktif baru untuk `cmd_new`: `--upstream-url`, `--upstream-key`, `--transformer`, `--think`, `--background`, `--longcontext` (selain `--model` yang sudah ada).

## 7. Dry-run (`CCX_DRY_RUN=1`)

Untuk profil router, dry-run **tidak** men-start `ccr`. Sebaliknya mencetak:
- port yang akan dipilih,
- perintah `ccr` final + `HOME` yang dipakai,
- isi `config.json` yang akan digenerate (dengan `api_key`/`APIKEY` **disamarkan**),
- `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` yang akan di-set (token disamarkan),
- perintah `claude` final.

Ini memenuhi item roadmap *"`CCX_DRY_RUN` shows the would-be router command + port without starting it"* dan menjadi basis tes otomatis.

## 8. Keamanan

- `config.json` ditulis `chmod 600`; `router/` `chmod 700`. API key upstream hidup di `profile.env` (`chmod 600`) dan disalin ke config saat launch.
- `ccr` di-*bind* ke `127.0.0.1` saja; endpoint lokal dilindungi `APIKEY` acak per-launch yang diselaraskan dengan `ANTHROPIC_AUTH_TOKEN`.
- Token/API key tak pernah dicetak utuh oleh `list`/`show`/dry-run (disamarkan).
- Tidak ada `source`/`eval` terhadap `profile.env` (tetap parsing manual).

## 9. Penanganan error

| Kondisi | Perilaku |
|---|---|
| `ccr` tidak ditemukan | Pesan + saran `npm i -g @musistudio/claude-code-router@<pinned>` atau set `CCX_CCR_BIN`; exit `1`. |
| Tidak ada port bebas | Pesan + exit `1`. |
| Health-check timeout | Teardown router; pesan (cek log `router/.claude-code-router/`) + exit `1`. |
| Router mati saat sesi | `trap` tetap membersihkan; `claude` akan error sendiri (didokumentasikan sebagai known gap). |
| Versi `ccr` v2.x terdeteksi | Peringatkan bahwa kontrak CLI mungkin berbeda; sarankan pin v1.x. |

## 10. Testing / verifikasi

1. **Dry-run** (`CCX_DRY_RUN=1`, `CCX_CCR_BIN=true`): assert config.json yang digenerate benar (Providers/Router mapping, transformer), port terpilih, env wiring (`ANTHROPIC_BASE_URL=127.0.0.1:<port>`), token disamarkan, dan **`ccr` tidak benar-benar dijalankan**.
2. **`ccx new` non-interaktif** (flag): `profile.env` memuat `CCX_ROUTER=ccr` + `CCX_UPSTREAM_*` + `CCX_MODEL_*` benar, perms `600`, `home/` & `router/` ada.
3. **Generator config terisolasi sebagai fungsi**: uji `kv → config.json` murni (tanpa proses), termasuk dedup `models[]` dan default `enhancetool`.
4. **Upstream ter-mock** (lifecycle): server HTTP kecil yang mengembalikan `/health` 200 dan satu respons Chat Completions ber-tool-call → assert `ccx` start→health→teardown bersih (tanpa proses ccr tertinggal). *Opsional/integration tier.*
5. **Smoke manual:** `ccx ollama -p "edit a file"` terhadap Ollama lokal model tool-capable → konfirmasi round-trip Edit/Bash.

## 11. Di luar lingkup (YAGNI untuk MVP)

- Pinning provider OpenRouter tingkat lanjut (`provider.only/order/ignore`) — disebut, tidak diimplementasikan di MVP.
- Reuse satu router untuk banyak sesi / router persisten antar-peluncuran (MVP: satu router per launch, mati saat sesi selesai).
- Dukungan Windows `%APPDATA%` lifecycle (fokus Linux/macOS; path dicatat sebagai catatan).
- Prompt caching & metering usage (tidak tersedia via router — didokumentasikan sebagai known gap).
- Pembungkusan/bundle `ccr` ke dalam repo (MVP mengandalkan `ccr` ter-instal/`npx` ber-versi pin).

## 12. Known gaps (wajib ada di README)

- **Streaming tool-call tidak sepenuhnya andal** (PR #1356 unmerged). Default `enhancetool` mematikan streaming tool-call demi stabilitas; tetap mungkin ada model yang bermasalah.
- **Model harus tool-capable.** Model text-only akan gagal (`No endpoints found that support tool use`).
- **Tidak ada prompt caching**; metering usage berbeda dari Anthropic native.
- **Bergantung pada kontrak CLI `ccr` v1.x** yang ter-pin; v2.x Desktop dapat berubah perilaku.
