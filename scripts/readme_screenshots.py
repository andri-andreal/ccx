#!/usr/bin/env python3
"""Render README screenshots of the ccx team TUI from its real drawing code.

Each scene drives TeamUI with the same events a run emits (agent start/finish,
activity, usage, HTTP exchanges, conclusion), captures the ANSI frame, replays
it on a small terminal grid, and prints it to PNG through headless Chromium.
The data is a neutral demo project, so no local paths or accounts appear.

    python3 scripts/readme_screenshots.py            # writes images/team-*.png
"""
import html
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'images'
spec = importlib.util.spec_from_file_location('workflow', ROOT / 'lib' / 'workflow.py')
wf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wf)

FG, BG = '#d7dae0', '#1b1e24'
COLOURS = {30: '#3f4451', 31: '#e06c75', 32: '#98c379', 33: '#e5c07b', 34: '#61afef', 35: '#c678dd',
           36: '#56b6c2', 37: '#d7dae0', 90: '#7a818e', 91: '#ff7b86', 92: '#b5e890', 93: '#f0d58a',
           94: '#7cc4ff', 95: '#d79cf2', 96: '#6fd1dc', 97: '#ffffff'}
BG_236 = '#2c313a'


class Terminal(io.StringIO):
    def isatty(self):
        return True


def replay(text, width, height):
    """Apply cursor moves, clears and SGR colours to a width x height grid."""
    blank = (' ', (None, None, False, False))
    grid = [[blank] * width for _ in range(height)]
    row = col = 0
    fg = bg = None
    bold = italic = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == '\x1b':
            match = re.match(r'\x1b\[([0-9;?]*)([A-Za-z])', text[index:])
            if not match:
                index += 1
                continue
            params, command = match.groups()
            index += len(match.group(0))
            if command == 'H':
                parts = [int(p) if p else 1 for p in params.split(';')] if params else [1, 1]
                row, col = parts[0] - 1, (parts[1] - 1 if len(parts) > 1 else 0)
            elif command == 'J' and params == '2':
                grid = [[blank] * width for _ in range(height)]
            elif command == 'K':
                if 0 <= row < height:
                    grid[row] = [blank] * width
            elif command == 'm':
                codes = [int(p) if p else 0 for p in params.split(';')] if params else [0]
                position = 0
                while position < len(codes):
                    code = codes[position]
                    if code == 0:
                        fg = bg = None
                        bold = italic = False
                    elif code == 1:
                        bold = True
                    elif code == 3:
                        italic = True
                    elif code in COLOURS:
                        fg = COLOURS[code]
                    elif code == 48 and codes[position + 1:position + 3] == [5, 236]:
                        bg = BG_236
                        position += 2
                    position += 1
            continue
        index += 1
        if char == '\n':
            row, col = row + 1, 0
        elif char == '\r':
            col = 0
        elif 0 <= row < height and 0 <= col < width:
            grid[row][col] = (char, (fg, bg, bold, italic))
            col += 1
    return grid


def to_html(grid, title):
    lines = []
    for cells in grid:
        spans, run, style = [], '', None
        for char, cell_style in cells + [(None, None)]:
            if cell_style != style or char is None:
                if run:
                    fg, bg, bold, italic = style
                    css = f'color:{fg or FG};' + (f'background:{bg};' if bg else '') + \
                          ('font-weight:700;' if bold else '') + ('font-style:italic;' if italic else '')
                    spans.append(f'<span style="{css}">{html.escape(run)}</span>')
                run, style = '', cell_style
            if char is not None:
                run += char
        lines.append(''.join(spans))
    return f'''<!doctype html><meta charset="utf-8"><style>
body{{margin:0;background:#0d1117;display:inline-block;padding:28px}}
.window{{background:{BG};border-radius:12px;box-shadow:0 18px 50px rgba(0,0,0,.55);overflow:hidden;
border:1px solid #2b303b}}
.bar{{height:34px;background:#21252b;display:flex;align-items:center;padding:0 14px;gap:8px;
font:600 13px system-ui,sans-serif;color:#9aa1ad}}
.dot{{width:12px;height:12px;border-radius:50%}}
.title{{margin-left:12px}}
pre{{margin:0;padding:14px 18px 16px;font:14px/19px "DejaVu Sans Mono","Hack","Noto Sans Mono",monospace;
color:{FG};white-space:pre}}
</style><div class="window"><div class="bar"><span class="dot" style="background:#ff5f57"></span>
<span class="dot" style="background:#febc2e"></span><span class="dot" style="background:#28c840"></span>
<span class="title">{html.escape(title)}</span></div><pre>{chr(10).join(lines)}</pre></div>'''


def shoot(html_text, target):
    """Print the page with headless Chromium, then crop to the window."""
    with tempfile.TemporaryDirectory() as folder:
        page = Path(folder) / 'shot.html'
        page.write_text(html_text, encoding='utf-8')
        raw = Path(folder) / 'raw.png'
        subprocess.run(['chromium', '--headless', '--disable-gpu', '--hide-scrollbars',
                        '--force-device-scale-factor=2', '--window-size=1600,1200',
                        f'--screenshot={raw}', page.as_uri()], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        from PIL import Image, ImageChops
        image = Image.open(raw).convert('RGB')
        background = Image.new('RGB', image.size, (13, 17, 23))
        box = ImageChops.difference(image, background).getbbox()
        pad = 56
        box = (max(0, box[0] - pad), max(0, box[1] - pad), min(image.width, box[2] + pad), min(image.height, box[3] + pad))
        image.crop(box).save(target, optimize=True)
    print('wrote', target.relative_to(ROOT))


CONFIG = {'CCX_ORCHESTRATOR': 'claude', 'CCX_PLANNER': 'codex', 'CCX_BUILDER': 'kimi-native',
          'CCX_REVIEWER': 'minimax-native'}


def new_ui():
    ui = wf.TeamUI(CONFIG, Path('~/.config/ccx/runs/team-k3v9q2ma'))
    ui.color = True
    ui.enabled = False  # build the scene quietly; capture() turns drawing on
    ui.branch = 'main'
    ui.mouse = False
    ui.on_event({'type': 'agents_loaded',
                 'agents': {'orchestrate': 'orchestrator', 'plan': 'go-architect', 'build': 'builder', 'review': 'reviewer'},
                 'details': {'orchestrate': {'name': 'orchestrator', 'layer': 'builtin', 'skills': []},
                             'plan': {'name': 'go-architect', 'layer': 'project', 'skills': ['go-api', 'testing']},
                             'build': {'name': 'builder', 'layer': 'builtin', 'skills': ['testing']},
                             'review': {'name': 'reviewer', 'layer': 'builtin', 'skills': ['testing', 'security']}}})
    return ui


def exchange(command, output, duration, phase, ui, **extra):
    for item in wf.http_exchanges(command, output, duration, 0):
        item.update(extra)
        ui.on_event({'type': 'http_exchange', 'phase': phase, 'exchange': item})


def agent(ui, phase, profile, model, tools=(), text=None, usage=None, finish=True):
    ui.begin(phase, profile, model, 'medium')
    for tool in tools:
        ui.update(phase, tool, 'tool')
    if text:
        ui.update(phase, text, 'text')
    if usage:
        ui.on_event({'type': 'usage', 'phase': phase, 'final': finish, 'usage': usage})
    if finish:
        ui.done(phase)


TASK = 'Add GET /employees/:id/status with tests'


def running_scene(ui):
    ui.on_event({'type': 'task_started', 'task': TASK})
    agent(ui, 'orchestrate', 'claude', 'sonnet', text='route: plan — new endpoint plus tests across handler, store and router',
          usage={'input': 3, 'cache_read': 5120, 'cache_write': 780, 'output': 141, 'cost_usd': 0.0051})
    ui.select_route('plan')
    agent(ui, 'plan', 'codex', 'gpt-6.1-terra',
          tools=['Read internal/http/router.go', 'Read internal/store/employee.go', 'Grep "status"', 'Read go.mod'],
          text='## Plan\n\n- `internal/store`: add `Status(id)` returning active, on leave or left\n'
               '- `internal/http`: `GET /employees/:id/status`, 404 for unknown ids\n- Table-driven tests for both layers',
          usage={'input': 9800, 'cache_read': 18200, 'cache_write': 0, 'output': 1240})
    agent(ui, 'review', 'minimax-native', '', tools=['Read internal/http/router.go'],
          text='APPROVED: scope, 404 behaviour and tests are explicit; assumptions are stated.',
          usage={'input': 15100, 'cache_read': 30700, 'cache_write': 0, 'output': 512})
    agent(ui, 'build', 'kimi-native', '', tools=['Edit internal/store/employee.go', 'Edit internal/http/router.go',
                                                   'Write internal/http/status_test.go', 'bash go test ./...'],
          usage={'input': 22400, 'cache_read': 41000, 'cache_write': 0, 'output': 2380}, finish=False)
    exchange("curl -si -X POST http://localhost:8080/employees -H 'Content-Type: application/json' "
             "-H 'Authorization: Bearer secret' -d '{\"name\":\"Ana\",\"status\":\"active\"}'",
             'HTTP/1.1 201 Created\r\nContent-Type: application/json\r\n\r\n{"id":42,"name":"Ana","status":"active"}',
             14, 'build', ui)
    exchange('curl -si http://localhost:8080/employees/42/status',
             'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{"id":42,"status":"active"}', 3, 'build', ui)
    exchange('curl -si http://localhost:8080/employees/999/status',
             'HTTP/1.1 404 Not Found\r\nContent-Type: application/json\r\n\r\n{"error":"employee not found"}', 2, 'build', ui)
    ui.flow_started -= 0.3
    ui.started_at = __import__('time').monotonic() - 71.4


def finished_scene(ui):
    running_scene(ui)
    ui.done('build')
    for name, path, body in (('Status of a known employee', '/employees/42/status', '{"id":42,"status":"active"}'),
                             ('Unknown employee is 404', '/employees/999/status', '{"error":"employee not found"}')):
        code = 200 if '42' in path else 404
        ui.on_event({'type': 'http_exchange', 'phase': 'api', 'exchange': {
            'client': 'ccx', 'method': 'GET', 'url': 'http://127.0.0.1:8080' + path, 'request_headers': [],
            'request_body': '', 'status': code, 'reason': 'OK' if code == 200 else 'Not Found',
            'response_headers': [['Content-Type', 'application/json']], 'response_body': body,
            'response_size': len(body), 'truncated': False, 'duration_ms': 3, 'exit_code': None,
            'check': name, 'passed': True, 'failure': ''}})
    agent(ui, 'review', 'minimax-native', '', tools=['Read internal/http/status_test.go'],
          usage={'input': 6200, 'cache_read': 12900, 'cache_write': 0, 'output': 344})
    summary = ('Builder and checker agree. Build verified and independently inspected.\n\n'
               '## Changes\n\n| File | What changed |\n|---|---|\n'
               '| `internal/store/employee.go` | `Status(id)` with active, on_leave, left |\n'
               '| `internal/http/router.go` | `GET /employees/:id/status`, 404 for unknown ids |\n'
               '| `internal/http/status_test.go` | table-driven tests, error paths included |\n\n'
               '**Verification:** `go test ./...` passed · **API checks:** 2/2 passed')
    ui.on_event({'type': 'task_finished', 'status': 'completed', 'summary': summary})
    ui.result = summary
    ui.footer = '✓ Completed in 94.2s · ' + wf.usage_text(ui.task_usage()) + ' · /diff · /undo · /result'
    ui.composing = True
    ui.mode = 'plan'


def capture(ui, width, height, draw):
    terminal = Terminal()
    with mock.patch.object(wf.sys, 'stdout', terminal), \
         mock.patch.object(wf.sys, 'stdin', Terminal()), \
         mock.patch.object(wf.shutil, 'get_terminal_size', return_value=os.terminal_size((width, height))), \
         mock.patch.object(wf.Path, 'cwd', return_value=Path('~/code/employee-api')):
        ui.enabled = True
        ui._previous_rows = None
        draw()
    return replay(terminal.getvalue(), width, height)


def main():
    OUT.mkdir(exist_ok=True)
    with mock.patch.dict(os.environ, {'TERM': 'xterm-256color', 'NO_COLOR': ''}):
        ui = new_ui()
        running_scene(ui)
        ui.running = True
        ui.queue_input = 'then add a PATCH endpoint for status'
        grid = capture(ui, 150, 40, ui.render)
        shoot(to_html(grid, 'ccx team — plan, build and review in one window'), OUT / 'team-running.png')

        ui = new_ui()
        finished_scene(ui)
        grid = capture(ui, 150, 40, ui.render)
        shoot(to_html(grid, 'ccx team — the answer lands in the conversation'), OUT / 'team-finished.png')

        ui = new_ui()
        running_scene(ui)
        grid = capture(ui, 150, 30, lambda: requests_frame(ui))
        shoot(to_html(grid, 'ccx team — /requests'), OUT / 'team-requests.png')

        ui = new_ui()
        config = {**CONFIG, 'CCX_ORCHESTRATOR_MODEL': 'sonnet', 'CCX_ORCHESTRATOR_EFFORT': 'medium',
                  'CCX_PLANNER_MODEL': 'gpt-6.1-terra', 'CCX_PLANNER_EFFORT': 'high', 'CCX_BUILDER_EFFORT': 'default',
                  'CCX_REVIEWER_EFFORT': 'medium', 'CCX_ROUNDS': '3', 'CCX_FIX_ROUNDS': '2', 'CCX_TIMEOUT': '600',
                  'CCX_PROOF': 'go test ./...'}
        with mock.patch.object(wf.TeamUI, '_read_choice_key', return_value=None):
            grid = capture(ui, 110, 18, lambda: ui.show_config(config, ROOT, ROOT, 'team'))
        shoot(to_html(grid, 'ccx team — /config'), OUT / 'team-config.png')


def requests_frame(ui):
    """/requests with the POST selected: masked auth header, JSON in and out."""
    with mock.patch.object(ui, '_can_pick', return_value=True):
        keys = iter(['up', 'up', None])
        with mock.patch.object(wf.TeamUI, '_read_choice_key', lambda self, fd=None: next(keys)):
            ui.show_requests()


if __name__ == '__main__':
    sys.exit(main())
