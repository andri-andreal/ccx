// Wrapper around the Tauri CLI.
//
// On Linux/Wayland, WebKitGTK's DMABUF renderer triggers
// "Gdk Error 71 (Protocol error) dispatching to Wayland display" on some
// compositor/GPU combos, which prevents the window from opening. Disabling the
// DMABUF renderer is the standard, harmless workaround. It only affects
// WebKitGTK (Linux), so we set it only there and never override an explicit
// value the user already provided.
import { spawn } from "node:child_process";

const env = { ...process.env };
if (process.platform === "linux" && env.WEBKIT_DISABLE_DMABUF_RENDERER == null) {
  env.WEBKIT_DISABLE_DMABUF_RENDERER = "1";
}

const child = spawn("tauri", process.argv.slice(2), {
  stdio: "inherit",
  env,
  shell: process.platform === "win32",
});

child.on("exit", (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  else process.exit(code ?? 0);
});
