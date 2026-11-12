# Desktop Team implementation

The Team workspace must use the CLI engine and its existing gates. A separate
Python JSON transport adapts the existing interactive UI; the Rust desktop host
owns the process, routes events and input, and stops it when the window closes.

Required surfaces:

- Teams: list, create, edit, duplicate, delete, set default; compatible workflow.env.
- Roles: all four profiles, model overrides, effort and fallback profiles; existing
  model catalogs; Windows native Kimi/mcode restrictions.
- Budgets: planning rounds, fix rounds, per-agent timeout, token budget, USD budget,
  retained runs, verification command and detection.
- Project: directory picker, API server/base/readiness/startup settings and editable
  standing API checks; project overrides clearly identified.
- Agents/skills: effective layered definitions and Markdown editing for project
  and user copies, preserving the engine's project trust prompts.
- Session: embedded conversation, live four-role activity/flow and usage, modes,
  tasks, clarification/choice/text dialogs, plan approval, cancellation, queue,
  resume, undo/redo, diff/files, requests, result and history.
- History: persisted runs, status, result, artifacts and resumable sessions.
- App shell: Profiles, Teams, Settings navigation; responsive desktop layout,
  empty/loading/error/unsaved states, keyboard-accessible controls.

Verification must cover configuration round trips and existing CLI compatibility,
validation and filesystem boundaries, interactive desktop transport with fake
CLIs (including prompts, plan/build, failures and cancellation), frontend build,
Rust host tests/build, and a rendered Windows GUI smoke test. No paid model calls
are required. Preserve previously authorized router fixes.

## Verification evidence

- `tests/test_gui_team.py`: configuration preservation and validation, API and
  definition persistence, trust, plan approval, clarification, build/verify/review,
  cancellation, failure resume, inspect, history restore, live configuration,
  queued tasks, local API evidence/server cleanup, fallback identity and undo/redo.
- `gui/scripts/test-team-desktop.mjs`: drives the real Windows Tauri/WebView2 app
  with isolated configuration and browser data. Covers creating/saving a team,
  four role forms, project selection, embedded workflow and usage, undo/redo,
  history, close/reopen, default, a routed builder with the bundled router, dirty
  checkout choices, API and skill editors, duplicate/delete and narrow layout.
  Screenshots are generated in `gui/test-results/`.
- `windows/test_windows.py`: existing 18 Windows integration checks still pass.
- GUI core: 39 Rust tests; staged desktop build and clippy with denied warnings;
  frontend production build and native window smoke check.
- Runtime uses `lib/workflow.py` unchanged except an opt-out for raw terminal
  input in desktop sessions. CLI terminal behavior retains its default.
