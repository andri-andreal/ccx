#!/usr/bin/env bash
# Minimal ccx-router stand-in for tests: binds the given --port on loopback and
# answers 200, so ccx's port-readiness health-check passes. Runs in the
# foreground (execs python), so the PID ccx backgrounds is the server itself and
# `kill` tears it down cleanly.
set -u

port=""
while [ $# -gt 0 ]; do
  case "$1" in
    --port) port="$2"; shift 2 ;;
    --upstream-url) shift 2 ;;
    *) shift ;;
  esac
done
[ -n "$port" ] || { echo "fake_ccx_router: --port required" >&2; exit 2; }

exec python3 - "$port" <<'PY'
import sys, http.server
port = int(sys.argv[1])
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def do_POST(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
    def log_message(self, *a):
        pass
http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
PY
