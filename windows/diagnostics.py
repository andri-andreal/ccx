"""Bounded, secret-safe diagnostics with the same report schema as the GUI."""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url, key, anthropic=False, payload=None, stream=False):
    headers = {'Accept': 'text/event-stream' if stream else 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    if anthropic:
        headers['anthropic-version'] = '2023-06-01'
        if key:
            headers['x-api-key'] = key
    body = None
    if payload is not None:
        body = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.build_opener(NoRedirect).open(req, timeout=20) as response:
        content = bytearray()
        deadline = time.monotonic() + 20
        while True:
            if time.monotonic() >= deadline:
                raise ValueError('Response exceeded diagnostic time limit.')
            chunk = response.read1(min(65536, 1048577 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > 1048576:
                raise ValueError('Response exceeded diagnostic size limit.')
        return content.decode('utf-8')


def valid_probe(text, protocol, capability):
    if capability == 'streaming':
        events = []
        for line in text.splitlines():
            if line.startswith('data:'):
                raw = line[5:].strip()
                if raw and raw != '[DONE]':
                    events.append(json.loads(raw))
        if protocol == 'anthropic':
            return any(e.get('type') in ('message_start', 'content_block_delta') for e in events) and any(e.get('type') == 'message_stop' for e in events)
        return any(isinstance(e.get('choices'), list) and e['choices'] for e in events) and 'data: [DONE]' in text
    data = json.loads(text)
    if protocol == 'anthropic':
        content = data.get('content', [])
        if capability == 'tools':
            return any(c.get('type') == 'tool_use' and c.get('name') == 'ccx_probe' and isinstance(c.get('input'), dict) for c in content)
        return data.get('type') == 'message' and isinstance(content, list) and any(c.get('type') == 'text' for c in content)
    choices = data.get('choices', [])
    if not choices or not isinstance(choices[0].get('message'), dict):
        return False
    message = choices[0]['message']
    if capability == 'tools':
        for call in message.get('tool_calls', []):
            function = call.get('function', {})
            if function.get('name') == 'ccx_probe' and isinstance(json.loads(function.get('arguments', 'null')), dict):
                return True
        return False
    return isinstance(message.get('content'), str)


def run(command, argv, cli):
    parser = argparse.ArgumentParser(prog='ccx ' + command)
    parser.add_argument('profile', nargs='?' if command == 'doctor' else None)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--no-network', action='store_true')
    parser.add_argument('--yes', '-y', action='store_true')
    parser.add_argument('--checks', default='basic,streaming,tools')
    args = parser.parse_args(argv)
    checks, capabilities = [], {c: 'unknown' for c in ('basic', 'streaming', 'tools')}

    def add(id, label, status, message, required=True):
        checks.append(dict(id=id, label=label, status=status, message=message, required=required))

    def finish():
        summary = {key: sum(c['status'] == status for c in checks)
                   for key, status in [('passed', 'pass'), ('warnings', 'warn'), ('failed', 'fail'), ('skipped', 'skip')]}
        status = 'fail' if summary['failed'] else 'warn' if summary['warnings'] else 'pass'
        report = dict(schema_version=1, command=command, profile=args.profile, status=status,
                      summary=summary, checks=checks, capabilities=capabilities)
        if args.json:
            print(json.dumps(report))
        else:
            for check in checks:
                print(f"{check['status'].upper():5} {check['label']}: {check['message']}")
        return int(bool(summary['failed']))

    add('system.python', 'Python', 'pass', sys.version.split()[0])
    if not args.profile:
        add('profiles.available', 'Profiles', 'pass', str(sum(1 for p in (cli.home() / 'profiles').glob('*/profile.env'))), False)
        return finish()
    try:
        data = cli.load_profile(args.profile)
        add('profile.syntax', 'Profile syntax', 'pass', 'Valid profile.')
        invocation, _ = cli.build_command(args.profile, data, [])
        try:
            cli.command_args(invocation)
            add('system.cli', 'AI CLI', 'pass', 'Executable available.')
        except (ValueError, OSError):
            add('system.cli', 'AI CLI', 'fail', 'Executable missing or unsupported; check CCX_*_BIN.')
        if data.get('CCX_ROUTER'):
            try:
                cli.command_args([cli.router_bin()])
                add('system.router', 'Router', 'pass', 'Executable available.')
            except (ValueError, OSError):
                add('system.router', 'Router', 'fail', 'Build or install ccx-router.exe.')
    except (ValueError, OSError):
        add('profile.syntax', 'Profile syntax', 'fail', 'Profile missing or invalid; use ccx edit.')
        return finish()
    anthropic = not data.get('CCX_ROUTER')
    url = data.get('ANTHROPIC_BASE_URL' if anthropic else 'CCX_UPSTREAM_BASE_URL', '').rstrip('/')
    key = data.get('ANTHROPIC_AUTH_TOKEN' if anthropic else 'CCX_UPSTREAM_API_KEY', '')
    model = data.get('ANTHROPIC_MODEL', '')
    native = data.get('CCX_PROVIDER') in ('codex', 'kimi-cli', 'mcode')
    if command == 'doctor':
        if native or not url:
            add('network.models', 'Model discovery', 'skip', 'Authentication is managed by the native CLI.', False)
        elif args.no_network:
            add('network.models', 'Model discovery', 'skip', 'Network checks disabled.', False)
        else:
            try:
                model_url = data.get('CCX_MODELS_URL') or url + ('/v1/models' if anthropic else '/models')
                cli.endpoint(model_url)
                response = json.loads(request(model_url, key, anthropic and data.get('CCX_MODELS_AUTH') != 'bearer'))
                if not isinstance(response.get('data'), list):
                    raise ValueError('Invalid model response.')
                add('network.models', 'Model discovery', 'pass', 'Endpoint returned a model list.', False)
            except (ValueError, OSError, TypeError, AttributeError, urllib.error.URLError):
                add('network.models', 'Model discovery', 'warn', 'Model list unavailable; no prompt was sent.', False)
        return finish()
    selected = set(args.checks.split(','))
    if selected == {'all'}:
        selected = set(capabilities)
    if not selected or selected - capabilities.keys():
        parser.error('--checks must contain basic,streaming,tools or all')
    consent = args.yes
    if not consent and sys.stdin.isatty() and not args.json:
        consent = input('Certification sends up to three minimal requests and may incur charges. Continue? [y/N] ').lower() == 'y'
    if not consent:
        add('certify.consent', 'Probe consent', 'fail', 'Pass --yes only after authorizing active probes.')
    if native or not url or not model:
        add('certify.scope', 'Certification scope', 'fail', 'Requires an explicit provider endpoint and model; native CLI logins are not read.')
    blocked = args.no_network or any(c['status'] == 'fail' for c in checks)
    for capability in capabilities:
        if blocked or capability not in selected:
            capabilities[capability] = 'skip'
            add('capability.' + capability, capability.title(), 'skip', 'Not selected or blocked by preflight checks.', capability in selected)
            continue
        payload = {'model': model, 'max_tokens': 32 if capability == 'tools' else 1,
                   'messages': [{'role': 'user', 'content': 'Call ccx_probe exactly once.' if capability == 'tools' else 'Reply with OK.'}]}
        if capability == 'streaming':
            payload['stream'] = True
        if capability == 'tools':
            parameters = {'type': 'object', 'properties': {}, 'additionalProperties': False}
            if anthropic:
                payload.update(tools=[{'name': 'ccx_probe', 'description': 'Inert compatibility probe.', 'input_schema': parameters}],
                               tool_choice={'type': 'tool', 'name': 'ccx_probe'})
            else:
                payload.update(tools=[{'type': 'function', 'function': {'name': 'ccx_probe', 'description': 'Inert compatibility probe.', 'parameters': parameters}}],
                               tool_choice={'type': 'function', 'function': {'name': 'ccx_probe'}})
        try:
            text = request(url + ('/v1/messages' if anthropic else '/chat/completions'), key, anthropic, payload, capability == 'streaming')
            passed = valid_probe(text, 'anthropic' if anthropic else 'openai', capability)
        except (ValueError, OSError, TypeError, KeyError, AttributeError, urllib.error.URLError):
            passed = False
        capabilities[capability] = 'pass' if passed else 'fail'
        add('capability.' + capability, capability.title(), capabilities[capability],
            'Recognized response.' if passed else 'Probe failed or response shape was invalid.')
    return finish()
