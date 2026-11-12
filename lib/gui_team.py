"""Desktop Team transport. Uses the same engine, gates and files as the CLI.

Requests and interactive sessions use newline-delimited JSON on stdin/stdout.
No terminal emulation, shell interpolation or independent workflow implementation.
"""
from __future__ import annotations

import argparse
from collections import deque
import importlib.util
import json
import os
from pathlib import Path
import queue
import re
import sys
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
ROLES = ('orchestrator', 'planner', 'builder', 'reviewer')
DEFAULTS = {'CCX_TYPE': 'loop', 'CCX_ROUNDS': '3', 'CCX_FIX_ROUNDS': '2', 'CCX_TIMEOUT': '900',
            'CCX_PROOF': '', 'CCX_MAX_TOKENS': '', 'CCX_MAX_COST_USD': '', 'CCX_KEEP_RUNS': '100',
            'CCX_RETRIES': '2', 'CCX_API_BASE': '', 'CCX_API_SKIP': ''}
for _role in ROLES:
    DEFAULTS.update({f'CCX_{_role.upper()}': '', f'CCX_{_role.upper()}_MODEL': '',
                     f'CCX_{_role.upper()}_EFFORT': 'default', f'CCX_{_role.upper()}_FALLBACK': ''})


def engine():
    sys.path.insert(0, str(ROOT / 'lib'))
    if os.name == 'nt':
        sys.path.insert(0, str(ROOT / 'windows'))
        from workflow_windows import load_engine
        return load_engine()
    spec = importlib.util.spec_from_file_location('ccx_gui_workflow', ROOT / 'lib' / 'workflow.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return vars(module)


def home():
    base = os.environ.get('XDG_CONFIG_HOME') or os.environ.get('APPDATA') or str(Path.home() / '.config')
    return Path(os.environ.get('CCX_HOME') or str(Path(base) / 'ccx')).resolve()


def named(root, name):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', name):
        raise ValueError('Use letters, digits, underscores or hyphens for names.')
    target = root / name
    if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('Symbolic links are not supported here.')
    return target


def read_env(path):
    result = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key) or key in result:
            raise ValueError(f'Malformed or duplicate key in {path.name}.')
        result[key] = value
    return result


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == 'nt' and path.is_relative_to(home()):
        from windows_platform import private_directory
        private_directory(home())
    fd, temporary = tempfile.mkstemp(prefix='.ccx-gui-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def project_path(value):
    if not value or not Path(value).is_dir():
        raise ValueError('Choose an existing project directory.')
    return Path(value).resolve()


def workspace_path(value, e):
    path = project_path(value)
    try:
        return e['repo_root'](path)
    except e['NotARepositoryError']:
        return path


def display_settings():
    path = home() / 'tui.json'
    if not path.is_file():
        return {'theme': 'default'}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Cannot update invalid display settings.')
    return data


def teams():
    result = []
    for path in sorted((home() / 'profiles').glob('*/workflow.env')):
        try:
            named(home() / 'profiles', path.parent.name)
            result.append({'name': path.parent.name, 'config': {**DEFAULTS, **read_env(path)}})
        except (OSError, ValueError) as error:
            result.append({'name': path.parent.name, 'error': str(error)})
    return result


def history(team):
    named(home() / 'profiles', team)
    result = []
    for path in (home() / 'runs').glob('*/session.json'):
        try:
            item = json.loads(path.read_text(encoding='utf-8'))
            if item.get('team') != team:
                continue
            outcome = path.parent / 'result.json'
            result.append({**item, 'id': path.parent.name,
                           'result': json.loads(outcome.read_text(encoding='utf-8')) if outcome.exists() else {},
                           'files': sorted(p.relative_to(path.parent).as_posix() for p in path.parent.rglob('*') if p.is_file() and not p.is_symlink())})
        except (OSError, ValueError, AttributeError):
            continue
    return sorted(result, key=lambda x: x.get('started', ''), reverse=True)[:100]


def request(action, data, e):
    if action == 'overview':
        return {'teams': teams(), 'defaults': DEFAULTS, 'home': str(home()),
                'platform': sys.platform, 'defaultTeam': (home() / 'default').read_text().strip() if (home() / 'default').is_file() else ''}
    if action == 'display':
        return display_settings()
    if action == 'save_display':
        if data.get('theme') not in ('default', 'ocean', 'contrast'):
            raise ValueError('Choose default, ocean or contrast.')
        atomic_write(home() / 'tui.json', json.dumps({**display_settings(), 'theme': data['theme']}, indent=2) + '\n')
        return True
    if action == 'save':
        directory = named(home() / 'profiles', data['name'])
        path = directory / 'workflow.env'
        if data.get('create') and directory.exists():
            raise ValueError('That name already exists. Choose a different name.')
        if not data.get('create') and not path.is_file():
            raise ValueError('Team no longer exists. Refresh the list.')
        config = {**DEFAULTS, **data['config']}
        if config.get('CCX_TYPE') != 'loop':
            raise ValueError('Team type must be loop.')
        for key, value in config.items():
            if not re.fullmatch(r'[A-Z][A-Z0-9_]*', key) or not isinstance(value, str) or any(c in value for c in '\r\n\0'):
                raise ValueError('Configuration keys and values must be single-line environment data.')
        e['load_role_profiles'](config, home() / 'profiles')
        if not re.fullmatch(r'[0-9]+', config['CCX_RETRIES']):
            raise ValueError('Retry budget must be a whole number (0 disables retries).')
        if config.get('CCX_API_BASE'):
            e['local_base_url'](config['CCX_API_BASE'])
        if config.get('CCX_API_SKIP') not in ('', '0', '1'):
            raise ValueError('API skip must be empty, 0 or 1.')
        # Keep comments and future keys when editing an existing CLI preset.
        output, seen = [], set()
        for line in path.read_text(encoding='utf-8').splitlines() if path.exists() else []:
            key = line.partition('=')[0]
            if key in config:
                output.append(f'{key}={config[key]}')
                seen.add(key)
            else:
                output.append(line)
        output.extend(f'{key}={value}' for key, value in config.items() if key not in seen)
        atomic_write(path, '\n'.join(output) + '\n')
        return {'name': data['name'], 'config': config}
    if action == 'delete':
        path = named(home() / 'profiles', data['name']) / 'workflow.env'
        if not path.is_file():
            raise ValueError('Unknown team.')
        path.unlink()
        default = home() / 'default'
        if default.is_file() and default.read_text().strip() == data['name']:
            default.unlink()
        # Preserve any other files in the profile directory.
        if not any(path.parent.iterdir()):
            path.parent.rmdir()
        return True
    if action == 'default':
        path = named(home() / 'profiles', data['name']) / 'workflow.env'
        if not path.is_file():
            raise ValueError('Unknown team.')
        atomic_write(home() / 'default', data['name'] + '\n')
        return True
    if action == 'catalog':
        path = named(home() / 'profiles', data['name']) / 'profile.env'
        profile = read_env(path)
        provider = profile.get('CCX_PROVIDER', '')
        return {'models': e['model_choices'](path, provider), 'efforts': e['role_efforts'](provider)}
    if action == 'project':
        repo = workspace_path(data['project'], e)
        agents, warnings = e['load_agents'](repo)
        resources = []
        for layer, root in e['definition_layers'](repo, True):
            for kind in ('agents', 'skills'):
                for path in sorted((root / kind).glob('*.md')):
                    resources.append({'layer': layer, 'kind': kind, 'name': path.stem,
                                      'content': path.read_text(encoding='utf-8')})
        return {'project': str(repo), 'proof': e['detect_proof'](repo),
                'proofCandidates': e['proof_candidates'](repo), 'serverCandidates': e['server_candidates'](repo),
                'api': e['load_api_config'](repo), 'agents': agents, 'warnings': warnings,
                'resources': resources, 'files': (e['workspace_files'](repo) if (repo / '.git').exists() else e['fs_workspace_files'](repo))[:3000]}
    if action in ('save_resource', 'delete_resource'):
        repo = workspace_path(data['project'], e)
        if data['kind'] not in ('agents', 'skills') or data['layer'] not in ('project', 'user'):
            raise ValueError('Choose a project or user agent/skill.')
        root = repo / '.ccx' if data['layer'] == 'project' else home()
        path = named(root / data['kind'], data['name']).with_suffix('.md')
        if action == 'delete_resource':
            path.unlink()
            return True
        if len(data['content'].encode()) > e['DEFINITION_LIMIT']:
            raise ValueError('Definition is too large (maximum 64 KB).')
        atomic_write(path, data['content'])
        return True
    if action == 'save_api':
        repo = workspace_path(data['project'], e)
        api = data['api']
        e['local_base_url'](api['base_url'])
        if not isinstance(api.get('startup_timeout'), int) or not 1 <= api['startup_timeout'] <= 600:
            raise ValueError('Server startup timeout must be 1–600 seconds.')
        for key in ('server', 'ready_path'):
            if not isinstance(api.get(key), str):
                raise ValueError(f'{key} must be text.')
        checks = e['validate_api_checks'](api.get('checks', []))
        for check in checks:
            check['headers'] = [{'name': h[0], 'value': h[1]} for h in check['headers']]
        atomic_write(repo / '.ccx' / 'api.json', json.dumps({k: api[k] for k in ('server', 'base_url', 'ready_path', 'startup_timeout')}, indent=2) + '\n')
        atomic_write(repo / '.ccx' / 'api-checks.json', json.dumps(checks, indent=2) + '\n')
        return True
    if action == 'history':
        return history(data['name'])
    if action == 'artifact':
        run = named(home() / 'runs', data['run'])
        file = data['file']
        parts = file.replace('\\', '/').split('/') if isinstance(file, str) else []
        if not parts or any(part in ('', '.', '..') or ':' in part for part in parts):
            raise ValueError('Invalid artifact name.')
        path = run.joinpath(*parts)
        if not path.resolve().is_relative_to(run.resolve()) or path.is_symlink() or path.suffix == '.zip':
            raise ValueError('Choose a text artifact.')
        return path.read_text(encoding='utf-8', errors='replace')[:1048576]
    raise ValueError('Unknown desktop Team action.')


def session(data, e):
    """Adapt the engine's existing interactive UI protocol to desktop events."""
    incoming, output = sys.stdin, sys.stdout
    messages = queue.Queue()
    cancelled, closing = threading.Event(), threading.Event()
    lock = threading.Lock()

    class DesktopProcesses:
        # Child tools must never inherit the desktop's JSON control pipe.
        # Some Windows Git builds otherwise wait for input on that pipe.
        def __init__(self, original):
            self.original = original
        def Popen(self, *args, **kwargs):
            kwargs.setdefault('stdin', self.original.DEVNULL)
            return self.original.Popen(*args, **kwargs)
        def run(self, *args, **kwargs):
            if kwargs.get('input') is None:
                kwargs.setdefault('stdin', self.original.DEVNULL)
            return self.original.run(*args, **kwargs)
        def __getattr__(self, key):
            return getattr(self.original, key)

    e['subprocess'] = DesktopProcesses(e['subprocess'])

    def send(kind, **payload):
        with lock:
            output.write(json.dumps({'kind': kind, **payload}, ensure_ascii=True, default=lambda v: list(v) if isinstance(v, (deque, set, frozenset)) else str(v)) + '\n')
            output.flush()

    def reader():
        for line in incoming:
            try:
                item = json.loads(line)
                if item.get('type') in ('cancel', 'close'):
                    cancelled.set()
                    if item['type'] == 'close':
                        closing.set()
                else:
                    messages.put(item)
            except (ValueError, AttributeError):
                continue
        closing.set()
        cancelled.set()

    class Stream:
        def __init__(self, original):
            self.original = original
        def isatty(self):
            return True
        def write(self, text):
            if text.strip():
                send('log', text=text)
            return len(text)
        def flush(self):
            pass
        def __getattr__(self, key):
            return getattr(self.original, key)

    base = e['TeamUI']

    class DesktopUI(base):
        terminal_input = False
        def __init__(self, *args):
            self.last_render = 0
            self.waiting = False
            self.desktop_config = args[0]
            super().__init__(*args)
            self.enabled = True
            self.color = False

        def enter(self):
            self.entered = True
            self.render(force=True)

        def exit(self):
            self.entered = False

        def exit_banner(self, *args):
            pass

        def _can_pick(self):
            return True

        def begin(self, phase, name, model, effort):
            key = self.panel_for(phase)
            if key:
                self.panels[key]['profile'] = name
            super().begin(phase, name, model, effort)

        def render(self, force=False):
            now = time.monotonic()
            if not force and now - self.last_render < .12:
                return
            self.last_render = now
            send('state', panels={key: {**panel, 'totalUsage': self.panel_usage(panel)} for key, panel in self.panels.items()}, timeline=list(self.timeline)[-300:],
                 footer=self.footer, active=self.active, mode=self.mode, route=self.route,
                 result=self.result, resultStatus=self.result_status, usage=self.task_usage(),
                 requests=self.requests[-200:], run=str(self.run_dir) if self.run_dir else None,
                 approved=bool(self.approved), queued=list(self.queued_tasks),
                 agents=self.agent_details, waiting=self.waiting, flow=self.flow_edge, config=self.desktop_config, theme=self.theme)

        def tick(self):
            if cancelled.is_set():
                cancelled.clear()
                raise KeyboardInterrupt
            if not self.waiting:
                while True:
                    try:
                        item = messages.get_nowait()
                    except queue.Empty:
                        break
                    if item.get('type') == 'command':
                        self.queued_tasks.append(str(item.get('text', '')))
            self.render()

        def wait(self, prompt):
            prompt_id = str(uuid.uuid4())
            self.waiting = True
            self.render(force=True)
            send('prompt', id=prompt_id, **prompt)
            try:
                while True:
                    if closing.is_set():
                        raise EOFError
                    self.tick()
                    try:
                        item = messages.get(timeout=.1)
                    except queue.Empty:
                        continue
                    if item.get('type') == 'answer' and item.get('id') == prompt_id:
                        return item
                    if item.get('type') == 'command':
                        self.queued_tasks.append(str(item.get('text', '')))
            finally:
                self.waiting = False
                send('prompt_closed', id=prompt_id)

        def read_command(self, prompt=None):
            if prompt:
                return str(self.wait({'title': prompt, 'input': True}).get('text', ''))
            self.render(force=True)
            send('ready')
            while True:
                if closing.is_set():
                    raise EOFError
                if self.queued_tasks:
                    return self.queued_tasks.popleft()
                cancelled.clear()
                try:
                    item = messages.get(timeout=.1)
                except queue.Empty:
                    continue
                if item.get('type') == 'command':
                    return str(item.get('text', ''))

        def select_choice(self, title, options, subtitle=''):
            if not options:
                return None
            answer = self.wait({'title': title, 'subtitle': subtitle,
                                'options': [{'label': x[0], 'note': x[2]} for x in options]})
            index = answer.get('index')
            return options[index][1] if isinstance(index, int) and 0 <= index < len(options) else None

        def show_text(self, title, body, allow_resume=False, actions=None, **kwargs):
            if actions:
                return self.select_choice(title, [(label, value, '') for label, value in actions], str(body))
            if allow_resume:
                return self.select_choice(title, [('Resume', 'resume', ''), ('Back', None, '')], str(body))
            send('document', title=title, body=body, markdown=kwargs.get('markdown', False))
            return None

        def edit_task(self):
            return self.read_command('Write your task')

        def edit_file(self, path):
            send('document', title=str(path), body=Path(path).read_text(encoding='utf-8'), editable=True)

        def show_requests(self):
            send('requests', requests=self.requests)

        def on_event(self, event):
            super().on_event(event)
            send('event', event=event)
            self.render(force=event.get('type') == 'task_finished')

    repo = project_path(data['project'])
    path = named(home() / 'profiles', data['name']) / 'workflow.env'
    if not path.is_file():
        raise ValueError('Unknown team.')
    os.chdir(repo)
    os.environ['TERM'] = 'ccx-desktop'
    os.environ['CCX_HOME'] = str(home())
    os.environ['CCX_MOUSE'] = '0'
    e['TeamUI'] = DesktopUI
    sys.stdout = Stream(output)
    sys.stdin = Stream(incoming)
    threading.Thread(target=reader, daemon=True).start()
    cli = os.environ.get('CCX_BIN') or ROOT / ('windows/ccx.py' if os.name == 'nt' else 'bin/ccx')
    sys.argv = ['ccx-desktop-team', '--ccx', str(cli), '--config', str(path)]
    if data.get('route'):
        sys.argv += ['--route', data['route']]
    try:
        code = e['main']()
        send('exited', code=code or 0)
    finally:
        sys.stdout, sys.stdin = output, incoming


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request')
    parser.add_argument('--session', action='store_true')
    args = parser.parse_args()
    try:
        data = json.loads(sys.stdin.readline())
        e = engine()
        if args.session:
            session(data, e)
        else:
            result = request(args.request, data, e)
            print(json.dumps({'ok': True, 'value': result}, ensure_ascii=True, default=str))
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=True))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
