# `ccx` — Claude Code Profile/Provider Switcher — Design Spec

**Tanggal:** 2026-06-26
**Status:** Draft (menunggu review user)
**Diverifikasi terhadap:** Claude Code v2.1.173 (binary terpasang di `/usr/lib/node_modules/@anthropic-ai/claude-code`)

---

## 1. Ringkasan

`ccx` adalah satu script Bash yang membuat dan menjalankan **profil** Claude Code. Tiap profil mengarahkan CLI `claude` ke kombinasi provider + model tertentu, hanya dengan men-set environment variable lalu `exec claude`. Tidak ada patch/modifikasi terhadap Claude Code — murni env var, sehingga aman dan reversible.

Profil yang dituju:
- **`claude`** — pakai login Anthropic milik user, dengan `ANTHROPIC_MODEL=opusplan` (plan pakai Opus, execute pakai Sonnet; Haiku jadi model kecil/cepat). Tidak mengisolasi config (memakai `~/.claude` apa adanya, lengkap dengan plugin/skill/history).
- **`minimax`, `glm`, `deepseek`, `kimi`** — provider pihak ketiga ber-endpoint Anthropic-compatible. Tiap profil **terisolasi penuh**: punya `CLAUDE_CONFIG_DIR` sendiri sehingga credential, history, dan settings benar-benar terpisah dari `~/.claude` dan dari profil lain.

User dapat membuat profil tambahan kapan saja lewat wizard `ccx new`.

## 2. Keputusan desain (sudah disepakati)

| Keputusan | Pilihan |
|---|---|
| UX switching | Satu launcher + argumen: `ccx <profil> [args…]`, `ccx new`, `ccx list`, dst. |
| Provider seed | `claude` + `minimax` + `glm` + `deepseek` + `kimi` |
| Penyimpanan API key | File `profile.env` `chmod 600` di folder config |
| Isolasi config profil 3P | **Isolasi penuh** via `CLAUDE_CONFIG_DIR` (tanpa membawa plugin/skill) |
| Bahasa implementasi | **Bash** — satu file executable, tanpa dependensi runtime |

## 3. Fakta Claude Code yang menjadi dasar (terverifikasi dari binary v2.1.173)

- `CLAUDE_CONFIG_DIR` — **ada**. Merelokasi seluruh folder config (`.credentials.json`, `projects/`, `settings.json`, `history.jsonl`, `plugins/`, dst.) ke direktori yang ditunjuk. Inti mekanisme isolasi.
- `opusplan` dan `opusplan[1m]` — **ada**. Nilai untuk `ANTHROPIC_MODEL`/`--model`: Opus saat plan mode, Sonnet saat eksekusi.
- `ANTHROPIC_BASE_URL` — endpoint provider (harus expose `/v1/messages` Anthropic-compatible).
- `ANTHROPIC_AUTH_TOKEN` — dikirim sebagai header `Authorization: Bearer`. Dipakai untuk provider pihak ketiga; berlaku langsung tanpa prompt approval dan tidak memicu konflik dengan login subscription.
- `ANTHROPIC_API_KEY` — dikirim sebagai header `x-api-key`; butuh approval interaktif sekali. **Tidak dipakai** sebagai default kita (pakai `AUTH_TOKEN`).
- `ANTHROPIC_MODEL` — model untuk sesi.
- `ANTHROPIC_DEFAULT_OPUS_MODEL` / `_SONNET_MODEL` / `_HAIKU_MODEL` / `_FABLE_MODEL` — memetakan alias (`opus`/`sonnet`/`haiku`/`fable`) ke ID model konkret. Dipakai agar alias `/model` tetap berfungsi di profil 3P.
- CLI flags relevan: `--model`, `--effort`, `--settings`, `--mcp-config`, `--setting-sources`, `--bare` — semua **ada**.

> Catatan akurasi: URL endpoint dan ID model provider pihak ketiga (MiniMax/GLM/DeepSeek/Kimi) bisa berubah sewaktu-waktu. Karena itu nilai-nilai itu hanya **default template** yang di-prefill wizard dan selalu bisa diedit/dikonfirmasi user. Tool tidak bergantung pada keakuratan default tersebut.

## 4. Arsitektur

### 4.1 Layout file

```
~/.local/bin/ccx                      # satu script Bash executable (#!/usr/bin/env bash)

~/.config/ccx/
  providers/                          # template default per provider (base URL + peta model)
    claude.tmpl
    minimax.tmpl
    glm.tmpl
    deepseek.tmpl
    kimi.tmpl
  profiles/
    <nama>/
      profile.env                     # chmod 600 — env var + metadata CCX_*
      home/                           # CLAUDE_CONFIG_DIR (chmod 700) — hanya bila CCX_ISOLATE=true
```

- **Tool**: satu file `~/.local/bin/ccx`. Dipilih `~/.local/bin` karena lazim ada di PATH dan tidak butuh sudo.
- **Config root**: `~/.config/ccx/` (mengikuti XDG; hormati `$XDG_CONFIG_HOME` bila di-set).
- Karena `ccx` adalah executable mandiri (bukan fungsi shell), ia berjalan sama di **zsh** maupun **fish**.

### 4.2 Format `profile.env`

File `KEY=VALUE` sederhana. Baris `CCX_*` adalah metadata untuk `ccx` sendiri (tidak diekspor ke `claude` kecuali yang relevan). Sisanya diekspor sebagai environment variable.

Contoh profil **claude** (tanpa isolasi, pakai login Anthropic):
```sh
# ccx profile: claude
CCX_PROVIDER=claude
CCX_ISOLATE=false
ANTHROPIC_MODEL=opusplan
```

Contoh profil **glm** (isolasi penuh, provider pihak ketiga):
```sh
# ccx profile: glm
CCX_PROVIDER=glm
CCX_ISOLATE=true
ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic
ANTHROPIC_AUTH_TOKEN=<token-user>
ANTHROPIC_MODEL=glm-4.6
ANTHROPIC_DEFAULT_OPUS_MODEL=glm-4.6
ANTHROPIC_DEFAULT_SONNET_MODEL=glm-4.6
ANTHROPIC_DEFAULT_HAIKU_MODEL=glm-4.5-air
```

### 4.3 Template provider (default yang di-prefill wizard — semua editable)

| Provider | `ANTHROPIC_BASE_URL` (default) | opus / sonnet | haiku | Isolasi default |
|---|---|---|---|---|
| `claude` | *(tidak di-set — pakai login)* | `ANTHROPIC_MODEL=opusplan` | *(default Haiku)* | `false` |
| `minimax` | `https://api.minimax.io/anthropic` | `MiniMax-M2` | `MiniMax-M2` | `true` |
| `glm` | `https://api.z.ai/api/anthropic` | `glm-4.6` | `glm-4.5-air` | `true` |
| `deepseek` | `https://api.deepseek.com/anthropic` | `deepseek-reasoner` / `deepseek-chat` | `deepseek-chat` | `true` |
| `kimi` | `https://api.moonshot.ai/anthropic` | `kimi-k2-0905-preview` | `kimi-k2-turbo-preview` | `true` |

Untuk provider 3P, `ANTHROPIC_MODEL` default diisi ID model utama provider, dan ketiga `ANTHROPIC_DEFAULT_*_MODEL` di-set agar alias `/model opus|sonnet|haiku` di dalam sesi tetap berfungsi.

## 5. Perintah (interface)

| Perintah | Perilaku |
|---|---|
| `ccx <profil> [args…]` | Muat `profile.env`, set env (+ `CLAUDE_CONFIG_DIR` bila isolasi), lalu `exec claude "$@"`. **Semua argumen setelah nama profil diteruskan apa adanya** ke `claude` (mis. `ccx claude --model haiku`, `ccx glm -p "halo"`). |
| `ccx new` | Wizard interaktif (lihat §6). |
| `ccx list` | Daftar profil: nama, provider, base URL, model. Token disamarkan. |
| `ccx show <profil>` | Tampilkan isi `profile.env` dengan token disamarkan. |
| `ccx edit <profil>` | Buka `profile.env` di `$EDITOR` (fallback `vi`); set ulang `chmod 600` setelah keluar. |
| `ccx rm <profil>` | Hapus folder profil setelah konfirmasi `y/N`. |
| `ccx help` / `ccx` (tanpa arg) / `-h` | Tampilkan ringkasan penggunaan. |

Resolusi argumen pertama: jika cocok dengan salah satu subcommand di atas → jalankan subcommand; selain itu diperlakukan sebagai nama profil.

## 6. Alur `ccx new` (wizard)

1. **Nama profil** — validasi `^[a-zA-Z0-9_-]+$`, tolak jika sudah ada.
2. **Pilih provider** — menu: `claude / minimax / glm / deepseek / kimi / custom`. Memuat default dari `providers/<x>.tmpl` (atau kosong untuk `custom`).
3. **Konfirmasi/edit nilai** — tiap field (`ANTHROPIC_BASE_URL`, peta model) ditampilkan dengan default; user tekan Enter untuk terima atau ketik nilai baru.
4. **Token** — untuk provider 3P, minta `ANTHROPIC_AUTH_TOKEN` via input tersembunyi (`read -rs`). Untuk profil `claude`, dilewati (pakai login).
5. **Isolasi** — default sesuai template (`true` untuk 3P, `false` untuk `claude`); dapat di-override.
6. **Tulis** — buat `profiles/<nama>/`, tulis `profile.env`, `chmod 600`. Jika isolasi, buat `home/` `chmod 700`.
7. **Selesai** — cetak cara menjalankan (`ccx <nama>`), dan jika perlu, instruksi menambahkan `~/.local/bin` ke PATH untuk fish & zsh.

## 7. Alur eksekusi `ccx <profil> [args…]`

1. Pastikan `claude` ada di PATH → jika tidak, error jelas + keluar `1`.
2. Pastikan `profiles/<profil>/profile.env` ada → jika tidak, cetak daftar profil + keluar `1`.
3. Cek permission `profile.env`: jika lebih longgar dari `600`, peringatkan (dan tawarkan perbaikan otomatis).
4. Parse `profile.env` baris demi baris (bukan `source`, demi keamanan): untuk tiap `KEY=VALUE` non-komentar, `export KEY=VALUE`. Variabel `CCX_*` tidak diekspor ke `claude` (hanya dibaca `ccx`).
5. Jika `CCX_ISOLATE=true`: `mkdir -p` + `chmod 700` folder `home/`, lalu `export CLAUDE_CONFIG_DIR=<…/home>`.
6. `shift` argumen profil; `exec claude "$@"`.

## 8. Keamanan

- `profile.env` selalu `chmod 600`; folder `home/` `chmod 700`. `ccx` memverifikasi dan memperingatkan bila longgar.
- Token diminta via input tersembunyi dan tidak pernah dicetak utuh oleh `list`/`show` (disamarkan, mis. `sk-…abcd`).
- Parsing `profile.env` dilakukan manual (tanpa `source`/`eval`) untuk menghindari eksekusi kode tak sengaja.
- Profil 3P pakai `ANTHROPIC_AUTH_TOKEN` (bukan `API_KEY`) agar langsung aktif tanpa approval dan tanpa mengganggu login subscription Anthropic.

## 9. Penanganan error

| Kondisi | Perilaku |
|---|---|
| `claude` tidak di PATH | Pesan + petunjuk instalasi; keluar `1`. |
| Profil tak dikenal | Cetak daftar profil yang ada; keluar `1`. |
| `profile.env` permission longgar | Peringatkan; tawarkan `chmod 600`. |
| `~/.local/bin` belum di PATH | Cetak baris yang perlu ditambahkan untuk fish (`fish_add_path`) dan zsh (`export PATH`). |
| `ccx new` nama sudah ada / invalid | Tolak dengan pesan; minta ulang. |
| `ccx rm` tanpa konfirmasi | Default `N` (batal). |

## 10. Testing / verifikasi

Karena tool meng-`exec claude`, pengujian fokus pada penyiapan env yang benar tanpa benar-benar memanggil API:

1. **Dry-run hook** — env var `CCX_DRY_RUN=1` membuat `ccx <profil>` mencetak env var yang akan di-set + perintah `claude` final, lalu keluar tanpa exec. Dipakai untuk uji otomatis.
2. **Uji unit (bats atau script shell)**:
   - `ccx new` non-interaktif (via env/stdin terskrip) menghasilkan `profile.env` benar + permission `600`.
   - `ccx <profil>` (dry-run) mengekspor `ANTHROPIC_BASE_URL`/`AUTH_TOKEN`/peta model yang sesuai; men-set `CLAUDE_CONFIG_DIR` hanya saat `CCX_ISOLATE=true`.
   - Argumen pass-through: `ccx claude --model haiku` (dry-run) meneruskan `--model haiku`.
   - `list`/`show` menyamarkan token.
   - Profil tak dikenal → exit code `1` + daftar.
3. **Smoke test manual** — `ccx claude -p "say hi"` dan `ccx <3p> -p "say hi"` (butuh token valid) untuk konfirmasi end-to-end.

## 11. Di luar lingkup (YAGNI untuk v1)

- Tidak ada sinkronisasi/symlink plugin ke profil 3P (sesuai keputusan isolasi penuh).
- Tidak ada integrasi OS keyring (pakai file `chmod 600`).
- Tidak ada GUI/TUI menu interaktif (cukup launcher + argumen).
- Tidak ada manajemen MCP per profil di v1 (bisa ditambah lewat `home/` masing-masing nanti).
