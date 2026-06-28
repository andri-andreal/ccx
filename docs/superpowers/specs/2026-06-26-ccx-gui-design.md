# `ccx-gui` — Desktop Profile Manager for ccx — Design Spec

**Tanggal:** 2026-06-26
**Status:** Draft (menunggu review user)
**Terkait:** melengkapi CLI `ccx` (lihat `2026-06-26-ccx-claude-model-switcher-design.md`)
**Toolchain terverifikasi (mesin target, CachyOS):** cargo/rustc 1.96.0, tauri-cli 2.11.2 (Tauri v2), node v26 / npm 11, webkit2gtk-4.1 2.52.4 + javascriptcoregtk-4.1 + libsoup-3.0 + gtk+-3.0. Terminal tersedia: kitty, alacritty, konsole, xterm.

---

## 1. Ringkasan

`ccx-gui` adalah aplikasi desktop **Tauri v2** (backend Rust + frontend Svelte) untuk **mengelola profil ccx** dan **meluncurkan** Claude Code dengan profil terpilih. Ia membaca/menulis file profil yang sama dengan CLI `ccx` (`~/.config/ccx/profiles/<nama>/profile.env`), sehingga profil yang dibuat di GUI berfungsi penuh di CLI dan sebaliknya.

GUI tidak menjalankan Claude di dalam jendela (tanpa terminal tertanam). "Launch" membuka jendela terminal baru yang menjalankan Claude Code; tersedia juga "Copy command" sebagai cadangan.

## 2. Keputusan desain (disepakati)

| Keputusan | Pilihan |
|---|---|
| Peran GUI | Pengelola profil (lihat/tambah/edit/hapus) + tombol Launch |
| Bentuk aplikasi | Desktop app **Tauri v2** |
| Aksi Launch | Buka jendela terminal baru **+** tombol Copy command (cadangan) |
| Arsitektur logika | **C — full Rust**: GUI mengimplementasikan ulang CRUD + launch di Rust; tidak bergantung pada biner `ccx` |
| Frontend | **Svelte** (Vite) |

## 3. Kontrak bersama dengan CLI (sumber kebenaran)

GUI dan CLI **wajib** menyepakati hal-hal berikut. Ini didokumentasikan di sini sebagai satu-satunya sumber kebenaran agar dua implementasi (Bash & Rust) tidak menyimpang.

**Lokasi config** (resolusi identik dengan `ccx`):
`CCX_HOME` bila di-set, selain itu `${XDG_CONFIG_HOME:-$HOME/.config}/ccx`.
- Profil: `<CCX_HOME>/profiles/<nama>/profile.env` (mode `600`)
- Folder isolasi: `<CCX_HOME>/profiles/<nama>/home/` (mode `700`), dipakai sebagai `CLAUDE_CONFIG_DIR`
- Template provider: `<CCX_HOME>/providers/<provider>.tmpl`

**Format `profile.env`** (baris `KEY=VALUE`, komentar diawali `#`):
- Metadata `CCX_*` (TIDAK diekspor ke `claude`): `CCX_PROVIDER`, `CCX_ISOLATE` (`true`/`false`).
- Sisanya adalah environment variable yang diekspor ke `claude`: `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL`.

**Aturan launch** (harus sama dengan `ccx <profil>`):
1. Ekspor semua key non-`CCX_*` dari `profile.env` sebagai environment.
2. Bila `CCX_ISOLATE=true`: pastikan folder `home/` ada (mode `700`), set `CLAUDE_CONFIG_DIR=<…/home>`.
3. Jalankan `claude` dengan environment tersebut (tanpa argumen tambahan dari GUI di v1).

## 4. Arsitektur

### 4.1 Layout proyek

> Refinement implementasi: seluruh logika ditempatkan di crate Rust mandiri `gui/core` (`ccx-core`) yang diuji unit secara terpisah; `gui/src-tauri` hanya shell tipis yang membungkusnya jadi command. Ini memisahkan logika teruji dari shell Tauri (tes cepat tanpa mengompilasi Tauri). Modul di bawah berada di `gui/core/src/` (bukan `src-tauri/src/`).

```
gui/
  src-tauri/
    Cargo.toml
    tauri.conf.json
    src/
      main.rs            # entrypoint Tauri, daftar command
      config.rs          # resolusi CCX_HOME / path
      profile.rs         # struct Profile + parse/serialize/CRUD profile.env
      provider.rs        # baca providers/*.tmpl untuk default form
      launcher.rs        # deteksi terminal + susun env + spawn; string command untuk Copy
      settings.rs        # baca/tulis ~/.config/ccx/gui.json (mis. override terminal)
  src/                   # frontend Svelte (Vite)
    App.svelte
    lib/
      api.js             # wrapper @tauri-apps/api invoke()
      ProfileList.svelte
      ProfileForm.svelte
      SettingsPanel.svelte
  package.json
  vite.config.js
  index.html
```

### 4.2 Backend Rust — modul & tanggung jawab

- **`config.rs`** — `ccx_home() -> PathBuf`, `profiles_dir()`, `profile_dir(name)`, `profile_env_path(name)`, `providers_dir()`. Resolusi mengikuti §3.
- **`profile.rs`** — `struct Profile { name, provider, isolate, base_url, token, model, opus, sonnet, haiku }`.
  - `parse(path) -> Profile` (baca KEY=VALUE, abaikan komentar/baris kosong).
  - `serialize(&Profile) -> String` (urutan field deterministik sesuai §3).
  - `list() -> Vec<Profile>`, `get(name)`, `create(&Profile)` (tulis file `600`, buat `home/` `700` bila isolate), `update(&Profile)`, `delete(name)`.
  - `masked_token(&str) -> String` (3 char awal + `…` + 4 char akhir; `****` bila ≤ 8).
- **`provider.rs`** — `list_providers() -> Vec<ProviderTemplate>` membaca `providers/*.tmpl`; tiap template memberi default `base_url`, `model`, `opus/sonnet/haiku`, `isolate` untuk mengisi form. Provider seed: `claude`, `minimax`, `glm`, `deepseek`, `kimi`.
- **`launcher.rs`**
  - `detect_terminal() -> Option<TerminalSpec>` — pilih pertama yang tersedia: `$TERMINAL`, lalu `kitty`, `alacritty`, `konsole`, `foot`, `wezterm`, `ghostty`, `gnome-terminal`, `xterm`. `TerminalSpec` memetakan flag exec per-terminal (lihat §6).
  - `build_env(&Profile) -> Vec<(String,String)>` — sesuai aturan §3.
  - `launch(&Profile) -> Result<()>` — `Command::new(term).args(exec_args).envs(build_env).spawn()` (detached); proses anak mewarisi env yang di-set.
  - `copy_command(&Profile) -> String` — kembalikan `ccx <nama>` (paste-able; token tidak masuk clipboard). Catatan: string ini mengasumsikan CLI `ccx` terpasang di PATH; GUI sendiri (Launch native) tidak bergantung padanya — Copy murni teks bantu.
- **`settings.rs`** — `GuiSettings { terminal_override: Option<Vec<String>> }` di `~/.config/ccx/gui.json`; baca/tulis.

### 4.3 Tauri commands (jembatan ke frontend)

`list_profiles`, `get_profile(name)`, `create_profile(payload)`, `update_profile(payload)`, `delete_profile(name)`, `list_providers`, `launch_profile(name)`, `copy_command(name) -> String`, `get_settings`, `set_settings(payload)`, `detect_terminal -> String|null`.

Semua command mengembalikan `Result<T, String>`; error dipetakan ke pesan yang ditampilkan frontend.

## 5. Frontend (Svelte)

Satu jendela, tiga view:
1. **ProfileList** — daftar kartu profil (default). Tiap kartu: nama, badge provider, base URL (atau "anthropic login"), model, badge "isolated"/"shared", token tersamarkan. Aksi: **Launch**, **Copy**, **Edit**, **Delete** (Delete minta konfirmasi).
2. **ProfileForm** (modal/route) untuk New & Edit: field nama (read-only saat edit), dropdown provider (memicu prefill base URL + model dari template), token (password + tombol reveal), toggle "isolasi", bagian "Lanjutan" untuk override `opus/sonnet/haiku`.
3. **SettingsPanel** — menampilkan terminal yang terdeteksi; field opsional untuk override perintah terminal.

`lib/api.js` membungkus `invoke()` dari `@tauri-apps/api`. State sederhana via Svelte store (daftar profil + view aktif).

### 5.1 Mockup tampilan

```
┌─ ccx ───────────────────────────────────────[+ New profile]┐
│  ┌───────────────────────────────────────────────────────┐ │
│  │ work            [claude]            opusplan           │ │
│  │ (anthropic login) · shared config                     │ │
│  │             [▶ Launch] [⧉ Copy] [✎ Edit] [🗑 Delete]   │ │
│  ├───────────────────────────────────────────────────────┤ │
│  │ mm              [minimax]  ⊘ isolated   MiniMax-M2     │ │
│  │ https://api.minimax.io/anthropic · token sk-…5678     │ │
│  │             [▶ Launch] [⧉ Copy] [✎ Edit] [🗑 Delete]   │ │
│  └───────────────────────────────────────────────────────┘ │
└──────────────────── terminal: kitty ────────────[⚙ Settings]┘
```

## 6. Deteksi terminal & pembentukan perintah

`TerminalSpec` = `{ bin, exec_args }`, perintah final = `bin` + `exec_args` + `["claude"]`. Default per-terminal:

| Terminal | Perintah |
|---|---|
| kitty | `kitty claude` |
| alacritty | `alacritty -e claude` |
| konsole | `konsole -e claude` |
| foot | `foot claude` |
| wezterm | `wezterm start -- claude` |
| ghostty | `ghostty -e claude` |
| gnome-terminal | `gnome-terminal -- claude` |
| xterm | `xterm -e claude` |

Env profil di-set pada proses yang men-spawn terminal; jendela baru mewarisinya.
**Caveat terdokumentasi:** gnome-terminal memakai daemon dan mungkin tidak mewarisi env dari proses spawn — bila terdeteksi gnome-terminal, GUI menampilkan peringatan & menyarankan tombol **Copy** (`ccx <nama>`). Terminal user saat ini (kitty/alacritty/konsole/xterm) mewarisi env dengan benar. User dapat meng-override perintah terminal di Settings.

## 7. Keamanan

- `profile.env` selalu ditulis mode `600`; folder `home/` mode `700` (sama dgn CLI).
- Token ditampilkan tersamarkan secara default; reveal hanya saat diminta user.
- Token tidak pernah masuk clipboard (Copy memakai `ccx <nama>`, bukan env mentah) dan tidak pernah masuk argv proses (env di-set via API proses, bukan baris perintah).
- Tidak ada koneksi jaringan dari GUI; semua operasi lokal (file + spawn proses).

## 8. Penanganan error

| Kondisi | Perilaku |
|---|---|
| `CCX_HOME`/folder profil belum ada | Buat saat dibutuhkan; daftar kosong → ajak buat profil baru |
| Nama profil tidak valid / sudah ada (create) | Form menolak dgn pesan inline |
| Tidak ada terminal terdeteksi (launch) | Tampilkan pesan + arahkan ke Copy / Settings |
| `profile.env` permission longgar | Saat load, tampilkan badge peringatan; tombol "perbaiki ke 600" |
| Command Rust gagal | `Result::Err(String)` → toast/error di UI |

## 9. Testing

- **Unit test Rust** (`cargo test`) pada fungsi murni: `profile::parse`/`serialize` (round-trip), `masked_token`, `config::ccx_home` (hormati `CCX_HOME`/`XDG_CONFIG_HOME`), `provider` template parse, `launcher::build_env` (isolasi → ada `CLAUDE_CONFIG_DIR`; non-isolasi → tidak), `launcher::detect_terminal` (urutan & `$TERMINAL`), `launcher::copy_command` (= `ccx <nama>`). CRUD diuji terhadap `CCX_HOME` direktori temporer; verifikasi mode `600`/`700`.
- **Build:** `cargo build` (dan `cargo tauri build`/`dev` untuk integrasi) harus sukses.
- **Verifikasi visual:** dilakukan user via `cargo tauri dev` — lingkungan agen ini headless sehingga tidak bisa melihat/menguji interaksi UI. Spec menandai ini eksplisit.

## 10. Di luar lingkup (YAGNI untuk v1)

- Tanpa terminal tertanam / monitor sesi Claude.
- Tanpa editor template provider di GUI (edit file `providers/*.tmpl` langsung).
- Tanpa packaging/installer/penandatanganan (cukup `cargo tauri dev`/`build` lokal).
- Tanpa argumen Claude tambahan dari GUI (mis. `--model`) di v1 — Launch memakai default profil.
- Tanpa multi-window / tema selain default yang rapi.
- Badge "permission longgar + perbaiki" (§8) ditunda ke v2: GUI selalu menulis `profile.env` mode `600` dan CLI sudah memperingatkan saat launch, jadi tidak dibangun di v1.

## 11. Catatan interoperabilitas (risiko C)

Karena logika launch kini ada di dua tempat (Bash `ccx` dan Rust `ccx-gui`), §3 adalah kontrak yang mengikat keduanya. Perubahan pada format `profile.env` atau aturan launch harus diterapkan di kedua sisi. Test `launcher::build_env` mengunci aturan §3 di sisi Rust.
