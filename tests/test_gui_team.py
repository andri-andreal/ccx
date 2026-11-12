"""Desktop transport integration tests with real workflow gates and fake CLIs."""
import ast
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / 'lib' / 'gui_team.py'


class DesktopSession:
    def __init__(self, project, env):
        self.events = queue.Queue()
        self.seen = []
        self.process = subprocess.Popen([sys.executable, '-X', 'utf8', '-u', str(BRIDGE), '--session'],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, encoding='utf-8', env=env)
        self.errors = []
        def read():
            for line in self.process.stdout:
                try:
                    self.events.put(json.loads(line))
                except ValueError:
                    self.events.put({'kind': 'invalid', 'text': line})
        def read_errors():
            self.errors.extend(self.process.stderr.readlines())
        threading.Thread(target=read, daemon=True).start()
        threading.Thread(target=read_errors, daemon=True).start()
        self.send({'name': 'team', 'project': str(project)})

    def send(self, value):
        self.process.stdin.write(json.dumps(value) + '\n')
        self.process.stdin.flush()

    def wait(self, kind, predicate=lambda event: True, timeout=35):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event = self.events.get(timeout=.1)
            except queue.Empty:
                if self.process.poll() is not None:
                    raise AssertionError(f'Session exited: {self.process.returncode}; {self.errors}; {self.seen[-8:]}')
                continue
            self.seen.append(event)
            if event.get('kind') == kind and predicate(event):
                return event
        raise AssertionError(f'Timed out waiting for {kind}; errors={self.errors}; last={self.seen[-5:]}')

    def command(self, text):
        self.send({'type': 'command', 'text': text})

    def answer(self, prompt, index=None, text=''):
        self.send({'type': 'answer', 'id': prompt['id'], 'index': index, 'text': text})

    def close(self):
        if self.process.poll() is None:
            self.send({'type': 'close'})
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=8)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()


class TeamDesktopTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ccx desktop team ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / 'project space'
        self.project.mkdir()
        self.home = self.root / 'config'
        tree = ast.parse((ROOT / 'tests/test_native_workflow.py').read_text(encoding='utf-8'))
        fake = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == 'FAKE' for t in node.targets))
        fake = fake.replace("case = os.environ.get('FAKE_CASE', '')", "case = os.environ.get('FAKE_CASE', '')\nif case == 'desktop_slow': time.sleep(60)")
        self.fake = self.root / 'fake cli.py'
        self.fake.write_text(fake, encoding='utf-8')
        self.fake.chmod(0o700)
        self.env = {**os.environ, 'CCX_HOME': str(self.home), 'CCX_CODEX_BIN': str(self.fake),
                    'CCX_CLAUDE_BIN': str(self.fake), 'PYTHONUTF8': '1'}
        directory = self.home / 'profiles/codex'
        directory.mkdir(parents=True)
        (directory / 'profile.env').write_text('CCX_PROVIDER=codex\nCCX_ISOLATE=false\n', encoding='utf-8')
        self.git('init')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Fixture')
        (self.project / 'README.md').write_text('Fixture\n')
        self.git('add', '.')
        self.git('commit', '-m', 'fixture')
        self.config = self.rpc('overview')['defaults']
        for role in ('PLANNER', 'BUILDER', 'REVIEWER', 'ORCHESTRATOR'):
            self.config[f'CCX_{role}'] = 'codex'
        self.config['CCX_PROOF'] = 'python implemented.py' if os.name == 'nt' else 'python3 implemented.py'
        self.rpc('save', {'name': 'team', 'create': True, 'config': self.config})

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.project, check=True, capture_output=True)

    def rpc(self, action, data=None, ok=True):
        result = subprocess.run([sys.executable, '-X', 'utf8', str(BRIDGE), '--request', action],
                                input=json.dumps(data or {}), text=True, capture_output=True,
                                encoding='utf-8', env=self.env, timeout=20)
        response = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(response['ok'], ok, response)
        return response.get('value') if ok else response['error']

    def start(self, **extra):
        session = DesktopSession(self.project, {**self.env, **extra})
        self.addCleanup(session.close)
        session.wait('ready')
        return session

    def test_config_roundtrip_preserves_future_keys_and_cli_comments(self):
        path = self.home / 'profiles/team/workflow.env'
        path.write_text(path.read_text() + '# local comment\nCCX_FUTURE_OPTION=keep\n')
        config = self.rpc('overview')['teams'][0]['config']
        config.update(CCX_MAX_TOKENS='12345', CCX_MAX_COST_USD='2.50', CCX_KEEP_RUNS='0')
        self.rpc('save', {'name': 'team', 'config': config})
        self.assertIn('# local comment', path.read_text())
        self.assertIn('CCX_FUTURE_OPTION=keep', path.read_text())
        self.assertEqual(self.rpc('overview')['teams'][0]['config']['CCX_MAX_TOKENS'], '12345')
        self.rpc('default', {'name': 'team'})
        self.assertEqual(self.rpc('overview')['defaultTeam'], 'team')
        self.rpc('delete', {'name': 'team'})
        self.assertTrue((self.home / 'profiles/codex/profile.env').is_file())

    def test_rejects_invalid_config_names_injection_and_overwrite(self):
        self.rpc('save', {'name': 'team', 'create': True, 'config': self.config}, ok=False)
        for key, value in [('CCX_TIMEOUT', '0'), ('CCX_MAX_COST_USD', '-1'), ('CCX_PLANNER', '../bad'),
                           ('CCX_PROOF', 'echo ok\nCCX_TIMEOUT=1'), ('CCX_PLANNER_MODEL', 'invalid model')]:
            self.rpc('save', {'name': 'team', 'config': {**self.config, key: value}}, ok=False)
        self.rpc('delete', {'name': '../bad'}, ok=False)
        self.rpc('artifact', {'run': '../bad', 'file': 'x'}, ok=False)
        self.rpc('artifact', {'run': 'team-safe', 'file': '../x'}, ok=False)

    def test_project_api_and_layered_definitions_roundtrip(self):
        nested = self.project / 'src'
        nested.mkdir()
        self.assertEqual(self.rpc('project', {'project': str(nested)})['project'], str(self.project))
        api = {'server': '', 'base_url': 'http://127.0.0.1:8080', 'ready_path': '/health', 'startup_timeout': 20,
               'checks': [{'name': 'health', 'method': 'GET', 'path': '/health', 'headers': [{'name':'X-Test','value':'fixture'}],
                           'body': '', 'expect_status': 200, 'expect_contains': 'ok'}]}
        self.rpc('save_api', {'project': str(self.project), 'api': api})
        self.rpc('save_resource', {'project': str(self.project), 'layer': 'project', 'kind': 'agents',
                                  'name': 'planner', 'content': '---\nname: My planner\nskills: [testing]\n---\nPlan carefully.\n'})
        project = self.rpc('project', {'project': str(self.project)})
        self.assertEqual(project['api']['checks'][0]['headers'], [['X-Test', 'fixture']])
        self.assertEqual(project['agents']['planner']['name'], 'My planner')
        self.assertEqual(project['agents']['planner']['layer'], 'project')
        self.assertTrue(project['agents']['planner']['skills'])
        self.rpc('save_api', {'project': str(self.project), 'api': {**api, 'base_url': 'https://example.com'}}, ok=False)
        self.rpc('save_resource', {'project': str(self.project), 'layer': 'builtin', 'kind': 'agents',
                                  'name': 'planner', 'content': 'bad'}, ok=False)

    def test_interactive_build_verification_history_and_undo_redo(self):
        session = self.start()
        session.command('Implement the fixture')
        event = session.wait('event', lambda v: v['event']['type'] == 'task_finished')
        self.assertEqual(event['event']['status'], 'completed', event)
        session.wait('ready')
        self.assertTrue((self.project / 'implemented.py').exists())
        self.assertTrue(any(v.get('kind') == 'state' and v.get('panels', {}).get('build', {}).get('status') == 'running' for v in session.seen))
        runs = self.rpc('history', {'name': 'team'})
        self.assertEqual(runs[0]['result']['status'], 'completed')
        self.assertIn('consensus.json', runs[0]['files'])
        self.assertIn('Builder and checker agree', self.rpc('artifact', {'run': runs[0]['id'], 'file': 'result.json'}))
        agent_file = next(file for file in runs[0]['files'] if file.endswith('/stdout.txt'))
        self.assertTrue(self.rpc('artifact', {'run': runs[0]['id'], 'file': agent_file}))
        session.command('/undo')
        session.wait('ready')
        self.assertFalse((self.project / 'implemented.py').exists())
        session.command('/redo')
        session.wait('ready')
        self.assertTrue((self.project / 'implemented.py').exists())

    def test_plan_mode_requires_user_build_choice(self):
        session = self.start()
        session.command('/mode plan')
        session.wait('ready')
        session.command('Plan the fixture')
        prompt = session.wait('prompt', lambda v: v['title'] == 'PLAN READY')
        self.assertFalse((self.project / 'implemented.py').exists())
        session.answer(prompt, 0)
        event = session.wait('event', lambda v: v['event']['type'] == 'task_finished')
        self.assertEqual(event['event']['status'], 'completed')
        session.wait('ready')
        self.assertTrue((self.project / 'implemented.py').exists())

    def test_clarification_picker_reaches_engine(self):
        session = self.start(FAKE_QUESTIONS=json.dumps([{'question':'Which option?','options':['A','B']}]))
        session.command('Implement a choice')
        prompt = session.wait('prompt', lambda v: v.get('subtitle') == 'Which option?')
        session.answer(prompt, 1)
        event = session.wait('event', lambda v: v['event']['type'] == 'task_finished')
        self.assertEqual(event['event']['status'], 'completed')

    def test_cancel_running_process_preserves_failure(self):
        session = self.start(FAKE_CASE='desktop_slow')
        session.command('Implement slowly')
        session.wait('event', lambda v: v['event']['type'] == 'agent_started')
        session.send({'type': 'cancel'})
        event = session.wait('event', lambda v: v['event']['type'] == 'task_finished')
        self.assertEqual(event['event']['status'], 'failed')
        self.assertIn('interrupted', event['event']['error'].lower())

    def test_inspect_is_read_only_and_history_can_be_restored(self):
        session = self.start()
        session.command('/mode inspect')
        session.wait('ready')
        session.command('Inspect this checkout')
        event = session.wait('event', lambda v: v['event']['type'] == 'task_finished')
        self.assertEqual(event['event']['status'], 'completed')
        session.wait('ready')
        self.assertFalse((self.project / 'implemented.py').exists())
        session.command('/history')
        prompt = session.wait('prompt', lambda v: v['title'] == 'CCX TEAM HISTORY')
        session.answer(prompt, 0)
        detail = session.wait('prompt', lambda v: v['title'] == 'RUN DETAIL')
        session.answer(detail, 0)
        state = session.wait('state', lambda v: v.get('footer', '').startswith('Session restored'))
        self.assertIn('follow-up', state['footer'])
        session.wait('ready')

    def test_failed_build_resumes_from_approved_plan(self):
        session = self.start(FAKE_CASE='blocked_once')
        session.command('Implement and recover')
        event = session.wait('event', lambda v: v['event']['type'] == 'task_finished')
        self.assertEqual(event['event']['status'], 'failed')
        prompt = session.wait('prompt', lambda v: v['title'] == 'RUN FAILED')
        session.answer(prompt, 0)
        event = session.wait('event', lambda v: v['event']['type'] == 'task_finished')
        self.assertEqual(event['event']['status'], 'completed')
        session.wait('ready')
        self.assertTrue((self.project / 'implemented.py').exists())

    def test_live_config_saves_through_desktop_choices(self):
        session = self.start()
        session.command('/config')
        prompt = session.wait('prompt', lambda v: v['title'].startswith('CONFIG'))
        session.answer(prompt, next(i for i,o in enumerate(prompt['options']) if o['label'] == 'Loop'))
        loop = session.wait('prompt', lambda v: v['title'] == 'LOOP')
        session.answer(loop, next(i for i,o in enumerate(loop['options']) if o['label'].startswith('Timeout')))
        timeout = session.wait('prompt')
        session.answer(timeout, next(i for i,o in enumerate(timeout['options']) if o['label'] == '1800'))
        loop = session.wait('prompt', lambda v: v['title'] == 'LOOP')
        session.answer(loop, len(loop['options'])-1)
        prompt = session.wait('prompt', lambda v: v['title'].startswith('CONFIG'))
        session.answer(prompt, next(i for i,o in enumerate(prompt['options']) if o['label'].startswith('Save to workflow')))
        session.wait('ready')
        self.assertEqual(self.rpc('overview')['teams'][0]['config']['CCX_TIMEOUT'], '1800')

    def test_project_trust_and_display_settings(self):
        self.rpc('save_resource', {'project': str(self.project), 'layer': 'project', 'kind': 'agents',
                                  'name': 'planner', 'content': '---\nname: My planner\n---\nPlan carefully.\n'})
        self.git('add', '.')
        self.git('commit', '-m', 'project guidance')
        self.rpc('save_display', {'theme': 'ocean'})
        self.assertEqual(self.rpc('display')['theme'], 'ocean')
        session = self.start()
        session.command('Implement with project guidance')
        prompt = session.wait('prompt', lambda v: v['title'] == 'PROJECT AGENTS')
        session.answer(prompt, 1)
        event = session.wait('event', lambda v: v['event']['type'] == 'task_finished')
        self.assertEqual(event['event']['status'], 'completed')
        self.assertTrue((self.home / 'trusted-agents.json').exists())
        session.wait('ready')
        session.close()
        self.rpc('delete_resource', {'project': str(self.project), 'layer': 'project', 'kind': 'agents', 'name': 'planner'})
        self.assertEqual(self.rpc('project', {'project': str(self.project)})['agents']['planner']['layer'], 'builtin')

    def test_tasks_queue_while_an_agent_is_running(self):
        session = self.start(FAKE_CASE='stream')
        session.command('First fixture task')
        session.wait('event', lambda v: v['event']['type'] == 'agent_started')
        session.command('Second queued fixture task')
        session.wait('state', lambda v: bool(v.get('queued')))
        first = session.wait('event', lambda v: v['event']['type'] == 'task_finished')
        self.assertEqual(first['event']['status'], 'completed')
        second = session.wait('event', lambda v: v['event']['type'] == 'task_finished')
        self.assertEqual(second['event']['status'], 'completed')
        session.wait('ready')
        self.assertEqual(len(self.rpc('history', {'name': 'team'})), 2)

    def test_api_verification_emits_request_evidence_and_stops_server(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        self.config.update(CCX_API_SERVER='python server.py' if os.name == 'nt' else 'python3 server.py', CCX_API_BASE=base)
        self.rpc('save', {'name': 'team', 'config': self.config})
        self.rpc('save_api', {'project': str(self.project), 'api': {'server': '', 'base_url': base,
                 'ready_path': '/health', 'startup_timeout': 15, 'checks': []}})
        self.git('add', '.')
        self.git('commit', '-m', 'api defaults')
        session = self.start(FAKE_CASE='api_checks_pass')
        session.command('Build and verify the API')
        prompt = session.wait('prompt', lambda v: v['title'] == 'PROJECT AGENTS')
        session.answer(prompt, 1)
        exchange = session.wait('event', lambda v: v['event']['type'] == 'http_exchange')
        self.assertTrue(exchange['event']['exchange']['passed'])
        event = session.wait('event', lambda v: v['event']['type'] == 'task_finished')
        self.assertEqual(event['event']['status'], 'completed')
        session.wait('ready')
        with socket.socket() as sock:
            sock.settimeout(.5)
            self.assertNotEqual(sock.connect_ex(('127.0.0.1', port)), 0)

    def test_fallback_uses_and_displays_the_actual_profile(self):
        directory = self.home / 'profiles/claude'
        directory.mkdir()
        (directory / 'profile.env').write_text('CCX_PROVIDER=claude\nCCX_ISOLATE=false\n', encoding='utf-8')
        self.config.update(CCX_BUILDER='claude', CCX_BUILDER_FALLBACK='codex', CCX_RETRIES='0')
        self.rpc('save', {'name': 'team', 'config': self.config})
        session = self.start(FAKE_CASE='overloaded_claude', CCX_RETRY_DELAY='0')
        session.command('Implement using fallback')
        event = session.wait('event', lambda v: v['event']['type'] == 'task_finished')
        self.assertEqual(event['event']['status'], 'completed')
        state = session.wait('state', lambda v: v.get('resultStatus') == 'completed')
        self.assertEqual(state['panels']['build']['profile'], 'codex')


if __name__ == '__main__':
    unittest.main()
