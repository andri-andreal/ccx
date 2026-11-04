"""Native Windows CCX entry point. Does not invoke or modify the Unix CLI."""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from windows_platform import command_args, kill_tree, private_directory


def home():
    root = os.environ.get('XDG_CONFIG_HOME') or os.environ.get('APPDATA') or str(Path.home() / '.config')
    return Path(os.environ.get('CCX_HOME') or str(Path(root) / 'ccx')).resolve()


def profile_dir(name):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
        raise ValueError('Invalid profile name; use letters, digits, underscores or hyphens.')
    path = home() / 'profiles' / name
    if path.is_symlink():
        raise ValueError('Profile directory must not be a symbolic link.')
    return path


def read_env(path):
    data = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if not separator or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key) or key in data:
            raise ValueError(f'Malformed or duplicate key in {path.name}.')
        data[key] = value
    return data


def write_env(path, data):
    for key, value in data.items():
        if any(c in str(value) for c in '\r\n\0'):
            raise ValueError(f'{key} must be a single line.')
    private_directory(home())
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(''.join(f'{key}={value}\n' for key, value in data.items()))
    temporary.replace(path)


def endpoint(value):
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Endpoint must be an HTTP(S) URL without embedded credentials or fragment.')
    if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('Remote endpoints require HTTPS.')
    return value


def load_profile(name):
    data = read_env(profile_dir(name) / 'profile.env')
    if data.get('CCX_ISOLATE', 'false') not in ('true', 'false'):
        raise ValueError('CCX_ISOLATE must be true or false.')
    for key in data:
        if not key.startswith(('CCX_', 'ANTHROPIC_')) and key != 'CODEX_MODEL':
            raise ValueError(f'Unsupported profile key: {key}')
    for key in ('ANTHROPIC_BASE_URL', 'CCX_UPSTREAM_BASE_URL'):
        if data.get(key):
            endpoint(data[key])
    for url in data.get('CCX_ROUTER_FALLBACK_URLS', '').split(','):
        if url:
            endpoint(url)
    return data


def templates():
    result = {p.stem: p for p in (ROOT / 'providers').glob('*.tmpl')}
    result.update({p.stem: p for p in (home() / 'providers').glob('*.tmpl')})
    for provider in ('kimi-cli', 'mcode'):
        result.setdefault(provider, None)
    return result


def create(argv):
    parser = argparse.ArgumentParser(prog='ccx new')
    for key in ('name', 'provider', 'model', 'base-url', 'token', 'upstream-url', 'upstream-key', 'proof', 'orchestrator', 'planner', 'builder', 'reviewer'):
        parser.add_argument('--' + key)
    for key in ('opus', 'sonnet', 'haiku', 'provider-only', 'provider-order'):
        parser.add_argument('--' + key)
    for key in ('fallback-url', 'fallback-key', 'fallback-provider-only', 'fallback-provider-order', 'fallback-require-parameters'):
        parser.add_argument('--' + key, action='append', default=[])
    parser.add_argument('--router-attempts', type=int)
    parser.add_argument('--require-parameters', action='store_true')
    parser.add_argument('--token-stdin', action='store_true')
    parser.add_argument('--upstream-key-stdin', action='store_true')
    parser.add_argument('--login', action='store_true')
    parser.add_argument('--device-auth', action='store_true')
    parser.add_argument('--type', choices=('profile', 'loop'), default='profile')
    parser.add_argument('--isolate', action='store_true', default=None)
    parser.add_argument('--no-isolate', action='store_false', dest='isolate')
    parser.add_argument('--yes', '-y', action='store_true')
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--fix-rounds', type=int, default=2)
    parser.add_argument('--timeout', type=int, default=900)
    for role in ('orchestrator', 'planner', 'builder', 'reviewer'):
        parser.add_argument(f'--{role}-model', default='')
        parser.add_argument(f'--{role}-effort', default='default')
    args = parser.parse_args(argv)
    if args.type == 'loop' and (args.login or args.device_auth):
        parser.error('Login applies to model profiles, not workflows.')
    if args.yes and not args.name:
        parser.error('--name is required with --yes')
    if args.yes and args.type != 'loop' and not args.provider:
        parser.error('--provider is required with --yes')
    name = args.name or input('Profile name: ').strip()
    directory = profile_dir(name)
    if directory.exists():
        raise ValueError('Profile already exists; use ccx edit or choose another name.')
    if args.type == 'loop':
        if min(args.rounds, args.fix_rounds, args.timeout) <= 0:
            raise ValueError('Round budgets and timeout must be positive.')
        data = {'CCX_TYPE': 'loop', 'CCX_PROOF': args.proof or '', 'CCX_ROUNDS': args.rounds,
                'CCX_FIX_ROUNDS': args.fix_rounds, 'CCX_TIMEOUT': args.timeout}
        for role in ('orchestrator', 'planner', 'builder', 'reviewer'):
            selected = getattr(args, role)
            if role == 'builder':
                selected = selected or data.get('CCX_PLANNER')
            if not selected and not args.yes:
                selected = input(f'{role} profile (empty skips orchestrator): ').strip()
            if not selected and role == 'orchestrator':
                continue
            if not selected:
                raise ValueError(f'--{role} is required.')
            role_profile = load_profile(selected)
            provider = role_profile.get('CCX_PROVIDER')
            if provider in ('kimi-cli', 'mcode'):
                raise ValueError('Native Kimi/mcode team roles require Linux bubblewrap. On Windows use kimi/minimax provider profiles through Claude Code, or Codex roles.')
            effort = getattr(args, role + '_effort')
            valid_efforts = ('default', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra') if provider == 'codex' else ('default', 'low', 'medium', 'high', 'xhigh', 'max')
            if effort not in valid_efforts:
                raise ValueError(f'Invalid {role} effort for this provider.')
            data['CCX_' + role.upper()] = selected
            data['CCX_' + role.upper() + '_MODEL'] = getattr(args, role + '_model')
            data['CCX_' + role.upper() + '_EFFORT'] = getattr(args, role + '_effort')
        write_env(directory / 'workflow.env', data)
    else:
        available = templates()
        provider = args.provider
        if not provider:
            print('Providers: ' + ', '.join(sorted(available)))
            provider = input('Provider: ').strip()
        if provider not in available:
            raise ValueError('Unknown provider template.')
        data = read_env(available[provider]) if available[provider] else {}
        data['CCX_PROVIDER'] = provider
        if args.isolate is not None:
            data['CCX_ISOLATE'] = str(args.isolate).lower()
        data.setdefault('CCX_ISOLATE', 'false' if provider in ('claude', 'codex', 'kimi-cli', 'mcode') else 'true')
        if provider in ('kimi-cli', 'mcode') and data['CCX_ISOLATE'] == 'true':
            raise ValueError('Native Kimi/mcode profiles do not support isolation.')
        model_key = 'CODEX_MODEL' if provider == 'codex' else 'CCX_NATIVE_MODEL' if provider in ('kimi-cli', 'mcode') else 'ANTHROPIC_MODEL'
        if args.model:
            data[model_key] = args.model
        if args.base_url:
            data['CCX_UPSTREAM_BASE_URL' if data.get('CCX_ROUTER') else 'ANTHROPIC_BASE_URL'] = endpoint(args.base_url)
        if args.upstream_url:
            data['CCX_UPSTREAM_BASE_URL'] = endpoint(args.upstream_url)
        for tier in ('opus', 'sonnet', 'haiku'):
            if getattr(args, tier):
                data['ANTHROPIC_DEFAULT_' + tier.upper() + '_MODEL'] = getattr(args, tier)
        key = 'CCX_UPSTREAM_API_KEY' if data.get('CCX_ROUTER') else 'ANTHROPIC_AUTH_TOKEN'
        token = args.upstream_key or args.token
        if args.token_stdin or args.upstream_key_stdin:
            token = sys.stdin.readline().rstrip('\r\n')
            if not token:
                raise ValueError('No API key received on stdin.')
        if token is None and not args.yes and provider not in ('claude', 'codex', 'kimi-cli', 'mcode', 'ollama', 'vllm', 'lmstudio'):
            token = getpass.getpass('Provider API key: ')
        if token:
            data[key] = token
        if args.fallback_url:
            for url in args.fallback_url:
                endpoint(url)
                if ',' in url:
                    raise ValueError('Fallback URLs cannot contain commas.')
            data['CCX_ROUTER_FALLBACK_URLS'] = ','.join(args.fallback_url)
        if args.fallback_key:
            if len(args.fallback_key) > len(args.fallback_url) or any(',' in k for k in args.fallback_key):
                raise ValueError('Fallback keys must align with fallback URLs and cannot contain commas.')
            data['CCX_ROUTER_FALLBACK_KEYS'] = ','.join(args.fallback_key)
        if args.router_attempts is not None:
            if not 1 <= args.router_attempts <= 10:
                raise ValueError('--router-attempts must be 1..10.')
            data['CCX_ROUTER_ATTEMPTS_PER_UPSTREAM'] = str(args.router_attempts)
        for flag, variable in [('provider_only', 'CCX_ROUTER_PROVIDER_ONLY'), ('provider_order', 'CCX_ROUTER_PROVIDER_ORDER'), ('require_parameters', 'CCX_ROUTER_REQUIRE_PARAMETERS')]:
            primary = getattr(args, flag)
            fallback = getattr(args, 'fallback_' + flag)
            if flag == 'require_parameters':
                primary = '1' if primary else ''
                if any(v not in ('1', '0', 'true', 'false', '') for v in fallback):
                    raise ValueError('Fallback require-parameters must be 1 or 0.')
                fallback = ['1' if v in ('1', 'true') else '' for v in fallback]
            if len(fallback) > len(args.fallback_url) or any(';' in v for v in [primary or '', *fallback]):
                raise ValueError('Provider pinning positions must align with the upstreams.')
            if primary or fallback:
                data[variable] = ';'.join([primary or '', *fallback])
        if provider not in ('codex', 'kimi-cli', 'mcode'):
            if data.get('CCX_ROUTER'):
                endpoint(data.get('CCX_UPSTREAM_BASE_URL', ''))
            elif provider != 'claude' or data.get('ANTHROPIC_BASE_URL'):
                endpoint(data.get('ANTHROPIC_BASE_URL', ''))
            if not data.get('ANTHROPIC_MODEL') and provider != 'claude':
                if args.yes:
                    raise ValueError('--model is required for this provider.')
                data['ANTHROPIC_MODEL'] = input('Model ID: ').strip()
                if not data['ANTHROPIC_MODEL']:
                    raise ValueError('Model must not be empty.')
        write_env(directory / 'profile.env', data)
    print(f'Created {name}. Run: ccx {name}')
    if args.login or args.device_auth:
        login_args = ['login'] if provider in ('codex', 'kimi-cli', 'mcode') else ['auth', 'login']
        if args.device_auth:
            login_args.append('--device-auth')
        return run_profile(name, login_args)
    return 0


def router_bin():
    override = os.environ.get('CCX_ROUTER_BIN')
    if override:
        return override
    found = shutil.which('ccx-router')
    if found:
        return found
    for candidate in (ROOT / 'windows' / 'bin' / 'ccx-router.exe',
                      ROOT / 'windows' / '.build' / 'router' / 'target' / 'release' / 'ccx-router.exe',
                      ROOT / 'windows' / '.build' / 'router' / 'target' / 'debug' / 'ccx-router.exe',
                      ROOT / 'router' / 'target' / 'release' / 'ccx-router.exe'):
        if candidate.is_file():
            return str(candidate)
    raise ValueError('Router missing. Run install.ps1 -BuildRouter or set CCX_ROUTER_BIN.')


def build_command(name, data, argv):
    env = os.environ.copy()
    provider = data.get('CCX_PROVIDER', '')
    native = {'codex': ('CCX_CODEX_BIN', 'codex', 'CODEX_MODEL'),
              'kimi-cli': ('CCX_KIMI_BIN', 'kimi', 'CCX_NATIVE_MODEL'),
              'mcode': ('CCX_MCODE_BIN', 'mcode', 'CCX_NATIVE_MODEL')}
    if provider in native:
        variable, default, model_key = native[provider]
        binary = env.get(variable, default)
        model = data.get(model_key)
        excluded = ('login', 'logout', 'help', '--help', '-h', '--version', '-V', 'mcp', 'doctor', 'completion', 'features', 'update', 'provider', 'info')
        options = argv[:argv.index('--')] if '--' in argv else argv
        explicit = any(a == '--model' or a.startswith('--model=') or a.startswith('-m') for a in options)
        if model and not explicit and (not argv or argv[0] not in excluded):
            if argv and argv[0] in ('exec', 'e', 'review', 'resume', 'fork'):
                argv = [argv[0], '--model', model, *argv[1:]]
            else:
                argv = ['--model', model, *argv]
        if provider == 'codex' and data.get('CCX_ISOLATE') == 'true':
            isolated = profile_dir(name) / 'home'
            isolated.mkdir(parents=True, exist_ok=True)
            env['CODEX_HOME'] = str(isolated)
        if provider == 'codex' and argv and argv[0] in ('exec', 'e') and '--skip-git-repo-check' not in argv:
            try:
                tracked = subprocess.run(['git', 'rev-parse', '--git-dir'], stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, timeout=10).returncode == 0
            except OSError:
                tracked = False
            if not tracked:
                argv = [argv[0], '--skip-git-repo-check', *argv[1:]]
    else:
        binary = env.get('CCX_CLAUDE_BIN', 'claude')
        env.update({k: v for k, v in data.items() if k.startswith('ANTHROPIC_')})
        if data.get('CCX_ISOLATE') == 'true':
            isolated = profile_dir(name) / 'home'
            isolated.mkdir(parents=True, exist_ok=True)
            env['CLAUDE_CONFIG_DIR'] = str(isolated)
    return [binary, *argv], env


def run_profile(name, argv):
    directory = profile_dir(name)
    if (directory / 'workflow.env').is_file():
        from workflow_windows import run
        return run(['--ccx', str(Path(__file__).resolve()), '--config', str(directory / 'workflow.env'), *argv])
    data = load_profile(name)
    command, env = build_command(name, data, list(argv))
    dry = os.environ.get('CCX_DRY_RUN') == '1'
    if dry:
        print(json.dumps({'profile': name, 'provider': data.get('CCX_PROVIDER'), 'command': command,
                          'environment': {k: ('***' if any(s in k for s in ('KEY', 'TOKEN')) else v)
                                          for k, v in env.items() if k.startswith(('ANTHROPIC_', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR'))},
                          'router': bool(data.get('CCX_ROUTER'))}, ensure_ascii=False))
        return 0
    router = None
    log = None
    try:
        if data.get('CCX_ROUTER'):
            if data['CCX_ROUTER'] != 'builtin':
                raise ValueError('Only the builtin router is supported.')
            upstream = endpoint(data.get('CCX_UPSTREAM_BASE_URL', ''))
            with socket.socket() as port_socket:
                port_socket.bind(('127.0.0.1', 0))
                port = port_socket.getsockname()[1]
            token = secrets.token_urlsafe(32)
            router_env = env.copy()
            router_env.update({k: v for k, v in data.items() if k.startswith('CCX_ROUTER_')})
            router_env.update(CCX_ROUTER_API_KEY=token, CCX_ROUTER_UPSTREAM_KEY=data.get('CCX_UPSTREAM_API_KEY', ''))
            log = (directory / 'router.log').open('ab')
            router = subprocess.Popen(command_args([router_bin(), '--port', str(port), '--upstream-url', upstream]),
                                      env=router_env, stdout=log, stderr=log, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            local_url = f'http://127.0.0.1:{port}'
            for _ in range(100):
                if router.poll() is not None:
                    raise ValueError('Router exited during startup; inspect ccx logs ' + name)
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                raise ValueError('Router startup timed out.')
            env.update(ANTHROPIC_BASE_URL=local_url, ANTHROPIC_AUTH_TOKEN=token)
            env.pop('ANTHROPIC_API_KEY', None)
        process = subprocess.Popen(command_args(command), env=env)
        try:
            return process.wait()
        except KeyboardInterrupt:
            if process.poll() is None:
                kill_tree(process.pid)
            process.wait()
            return 130
    finally:
        if router is not None and router.poll() is None:
            kill_tree(router.pid)
            router.wait()
        if log:
            log.close()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    default = home() / 'default'
    if not argv:
        argv = [default.read_text(encoding='utf-8').strip()] if default.is_file() else ['help']
    command, *args = argv
    if command in ('help', '--help', '-h'):
        print('CCX for Windows\nUsage: ccx <profile> [CLI arguments...]\n       ccx new [--provider NAME] [--name NAME]\n       ccx new --type loop --planner NAME --builder NAME --reviewer NAME\n       ccx list | show NAME | edit NAME | rm NAME\n       ccx default [NAME|--clear]\n       ccx doctor [NAME] [--json] [--no-network]\n       ccx logs NAME | agents [arguments...]')
        return 0
    if command in ('version', '--version', '-V'):
        print('ccx 0.1.0 (Windows)')
        return 0
    if command == '--init-windows':
        private_directory(home())
        destination = home() / 'providers'
        destination.mkdir(exist_ok=True)
        for template in (ROOT / 'providers').glob('*.tmpl'):
            shutil.copyfile(template, destination / template.name)
        print('Windows configuration initialized: ' + str(home()))
        return 0
    if command == 'new':
        return create(args)
    if command == 'list':
        for folder in sorted((home() / 'profiles').glob('*')):
            file = folder / ('workflow.env' if (folder / 'workflow.env').is_file() else 'profile.env')
            if file.is_file():
                data = read_env(file)
                print(f"{folder.name:20} {data.get('CCX_PROVIDER', data.get('CCX_TYPE', '?')):12} {data.get('CODEX_MODEL', data.get('CCX_NATIVE_MODEL', data.get('ANTHROPIC_MODEL', '')))}")
        return 0
    if command == 'default':
        if not args:
            print(default.read_text(encoding='utf-8').strip() if default.exists() else 'No default profile.')
        elif args == ['--clear']:
            default.unlink(missing_ok=True)
        else:
            if len(args) != 1:
                raise ValueError('Usage: ccx default [NAME|--clear]')
            folder = profile_dir(args[0])
            if not any((folder / f).is_file() for f in ('profile.env', 'workflow.env')):
                raise ValueError('Unknown profile.')
            home().mkdir(parents=True, exist_ok=True)
            default.write_text(args[0] + '\n', encoding='utf-8')
        return 0
    if command == 'agents':
        os.environ['CCX_HOME'] = str(home())
        from workflow_windows import run
        return run(['--agents', *args])
    if command in ('doctor', 'certify'):
        import diagnostics
        return diagnostics.run(command, args, sys.modules[__name__])
    if command in ('show', 'edit', 'rm', 'logs'):
        if len(args) != 1:
            raise ValueError(f'Usage: ccx {command} NAME')
        directory = profile_dir(args[0])
        if not directory.is_dir():
            raise ValueError('Unknown profile.')
        file = directory / ('workflow.env' if (directory / 'workflow.env').exists() else 'profile.env')
        if command == 'show':
            for key, value in read_env(file).items():
                print(f"{key}={'***' if any(word in key for word in ('TOKEN', 'KEY')) else value}")
        elif command == 'logs':
            print((directory / 'router.log').read_text(encoding='utf-8', errors='replace'))
        elif command == 'edit':
            subprocess.run(command_args([os.environ.get('EDITOR', 'notepad.exe'), str(file)]), check=True)
            if file.name == 'profile.env':
                load_profile(args[0])
        elif input(f'Delete profile {args[0]} and its isolated home? [y/N] ').lower() == 'y':
            shutil.rmtree(directory)
        return 0
    return run_profile(command, args)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f'ccx: {error}', file=sys.stderr)
        raise SystemExit(1)
