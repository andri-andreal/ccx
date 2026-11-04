"""Native Windows integration tests; no real AI calls or user configuration."""
import ast
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / 'windows' / 'ccx.py'
sys.path.insert(0, str(CLI.parent))
import windows_platform
import workflow_windows


class WindowsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ccx windows ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'project space'
        self.repo.mkdir()
        self.fake = self.root / 'fake cli.py'
        tree = ast.parse((ROOT / 'tests' / 'test_native_workflow.py').read_text(encoding='utf-8'))
        fixture = next(ast.literal_eval(node.value) for node in tree.body
                       if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'FAKE' for t in node.targets))
        self.fake.write_text(fixture, encoding='utf-8')
        self.env = {**os.environ, 'CCX_HOME': str(self.root / 'config'),
                    'CCX_CLAUDE_BIN': str(self.fake), 'CCX_CODEX_BIN': str(self.fake),
                    'CCX_KIMI_BIN': str(self.fake), 'CCX_MCODE_BIN': str(self.fake),
                    'PYTHONUTF8': '1', 'TERM': 'dumb'}

    def cli(self, *args, ok=True, **extra):
        result = subprocess.run([sys.executable, '-X', 'utf8', str(CLI), *args],
                                cwd=self.repo, env={**self.env, **extra}, capture_output=True,
                                text=True, encoding='utf-8', timeout=40)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def new(self, name='c', provider='codex', *extra):
        self.cli('new', '--name', name, '--provider', provider, '--yes', *extra)

    def team(self):
        self.new()
        self.cli('new', '--name', 'team', '--type', 'loop', '--planner', 'c', '--reviewer', 'c',
                 '--proof', 'python implemented.py', '--yes')

    def test_profiles_args_defaults_and_masking(self):
        self.new('c', 'codex', '--model', 'fixture', '--isolate')
        result = json.loads(self.cli('c', 'login').stdout)
        self.assertEqual(result['args'], ['login'])
        self.assertTrue(result['home'].endswith('home'))
        self.assertEqual(json.loads(self.cli('c', 'hello & echo UNSAFE', '{"x":"a b"}').stdout)['args'],
                         ['--model', 'fixture', 'hello & echo UNSAFE', '{"x":"a b"}'])
        self.cli('default', 'c')
        self.assertEqual(json.loads(self.cli().stdout)['args'], ['--model', 'fixture'])
        self.new('p', 'minimax', '--token', 'fixture-secret')
        self.assertNotIn('fixture-secret', self.cli('show', 'p').stdout)
        self.assertNotIn('fixture-secret', self.cli('p', CCX_DRY_RUN='1').stdout)

    def test_cmd_entry(self):
        result = subprocess.run(['cmd.exe', '/d', '/c', str(CLI.with_suffix('.cmd')), '--version'],
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Windows', result.stdout)
        self.new()
        result = subprocess.run(['cmd.exe', '/d', '/c', str(CLI.with_suffix('.cmd')), 'c', 'hello & echo UNSAFE'],
                                env=self.env, capture_output=True, text=True, encoding='utf-8', timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['args'], ['hello & echo UNSAFE'])

    def test_profile_path_and_duplicate_keys_rejected(self):
        self.assertNotEqual(self.cli('show', '..', ok=False).returncode, 0)
        self.new()
        file = Path(self.env['CCX_HOME']) / 'profiles/c/profile.env'
        with file.open('a') as stream:
            stream.write('CCX_PROVIDER=claude\n')
        self.assertNotEqual(self.cli('c', ok=False).returncode, 0)

    def test_native_workflow_build_review_and_proof(self):
        self.team()
        result = self.cli('team', '--route', 'direct', 'Implement the fixture')
        self.assertTrue((self.repo / 'implemented.py').is_file(), result.stdout)
        runs = list((Path(self.env['CCX_HOME']) / 'runs').glob('team-*'))
        self.assertEqual(len(runs), 1)
        record = json.loads((runs[0] / 'result.json').read_text())
        self.assertEqual(record['status'], 'completed', record)

    def test_failed_proof_does_not_approve(self):
        self.team()
        result = self.cli('team', '--route', 'direct', '--proof', 'exit /b 7', 'Implement the fixture', ok=False)
        self.assertNotEqual(result.returncode, 0)
        runs = list((Path(self.env['CCX_HOME']) / 'runs').glob('team-*'))
        self.assertEqual(json.loads((runs[0] / 'result.json').read_text())['status'], 'failed')

    def test_plan_and_review_loop(self):
        self.team()
        self.cli('team', '--review-only', 'Plan the fixture')
        self.assertFalse((self.repo / 'implemented.py').exists())

    def test_npm_wrapper_does_not_interpret_shell_characters(self):
        shim = self.root / 'fake npm.cmd'
        script = self.root / 'main.js'
        script.write_text('console.log(JSON.stringify(process.argv.slice(2)))')
        shim.write_text('@echo off\n"%dp0%\\node.exe" "%dp0%\\main.js" %*\n')
        arguments = ['x & echo UNSAFE', '{"x":"a b"}', '%PATH%', '中文']
        result = subprocess.run(windows_platform.command_args([str(shim), *arguments]),
                                capture_output=True, text=True, encoding='utf-8', check=True)
        self.assertEqual(json.loads(result.stdout), arguments)

    def test_lock_excludes_other_process(self):
        lock = self.root / 'checkout.lock'
        with lock.open('w') as stream:
            windows_platform.flock(stream, 3)
            script = 'import sys; sys.path.insert(0,sys.argv[1]); import windows_platform as p; f=open(sys.argv[2],"w"); p.flock(f,3)'
            result = subprocess.run([sys.executable, '-c', script, str(CLI.parent), str(lock)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'BlockingIOError', result.stderr)

    def test_actual_console_composer_and_picker(self):
        result_file = self.root / 'console-result.json'
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = 0
        process = subprocess.Popen([sys.executable, '-X', 'utf8', str(CLI.parent / 'console_test.py'), str(result_file)],
                                   env=self.env, startupinfo=info, creationflags=subprocess.CREATE_NEW_CONSOLE)
        try:
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                trace = Path(str(result_file) + '.trace')
                self.fail('Console timeout: ' + (trace.read_text(encoding='utf-8') if trace.exists() else 'no trace'))
        finally:
            if process.poll() is None:
                windows_platform.kill_tree(process.pid)
                process.wait()
        self.assertTrue(result_file.exists(), 'Console test did not produce a result.')
        result = json.loads(result_file.read_text(encoding='utf-8'))
        self.assertTrue(result['ok'], result.get('error'))

    def test_diagnostic_schema_and_consent(self):
        self.new()
        report = json.loads(self.cli('doctor', 'c', '--json', '--no-network').stdout)
        self.assertEqual(report['schema_version'], 1)
        self.assertEqual(report['status'], 'pass')
        report = json.loads(self.cli('certify', 'c', '--json', ok=False).stdout)
        self.assertEqual(report['status'], 'fail')
        self.assertEqual(set(report['capabilities'].values()), {'skip'})

    def test_api_server_commands_use_cmd_and_stop_server(self):
        engine = workflow_windows.load_engine()
        server = self.repo / 'server.py'
        server.write_text('import http.server, os\nhttp.server.HTTPServer(("127.0.0.1",int(os.environ["PORT"])),http.server.SimpleHTTPRequestHandler).serve_forever()\n')
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        check = {'name': 'root', 'method': 'GET', 'path': '/', 'headers': [], 'body': '',
                 'expect_status': 200, 'expect_contains': 'server.py'}
        results = engine['run_api_checks']([check], 'python server.py', f'http://127.0.0.1:{port}', '/',
                                          5, self.repo, self.root / 'server.log')
        self.assertTrue(results[0]['passed'], results)
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', port), timeout=0.2)

    def test_git_checkout_and_checkpoint_restore(self):
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        (self.repo / 'base.txt').write_text('base\n')
        subprocess.run(['git', 'add', '.'], cwd=self.repo, check=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '-qm', 'fixture'], cwd=self.repo, check=True)
        self.team()
        self.cli('team', '--route', 'direct', 'Implement the fixture')
        engine = workflow_windows.load_engine()
        before, after = self.root / 'before.zip', self.root / 'after.zip'
        engine['capture_checkpoint'](self.repo, before)
        (self.repo / 'base.txt').write_text('changed\n')
        engine['capture_checkpoint'](self.repo, after)
        engine['restore_checkpoint'](self.repo, after, before)
        self.assertEqual((self.repo / 'base.txt').read_text(), 'base\n')

    def test_router_launch_and_cleanup_with_local_upstream(self):
        import socket
        import ccx
        try:
            router_binary = ccx.router_bin()
        except ValueError:
            self.skipTest('Build Windows router first: python windows/build_router.py')
        observed = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                observed.append((self.path, self.headers.get('Authorization'),
                                 json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                body = json.dumps({'id': 'fixture', 'object': 'chat.completion', 'model': 'fixture',
                                   'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'OK'}, 'finish_reason': 'stop'}],
                                   'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.new('oai', 'custom-oai', '--model', 'fixture', '--upstream-url', f'http://127.0.0.1:{server.server_port}/v1',
                 '--upstream-key', 'fixture-secret')
        client = self.root / 'client.py'
        client.write_text('''import json, os, urllib.request
url = os.environ['ANTHROPIC_BASE_URL']
request = urllib.request.Request(url+'/v1/messages', data=json.dumps({'model':'fixture','max_tokens':1,'messages':[{'role':'user','content':'Hi'}]}).encode(), headers={'Authorization':'Bearer '+os.environ['ANTHROPIC_AUTH_TOKEN'],'Content-Type':'application/json'})
with urllib.request.urlopen(request) as response: body=json.load(response)
print(json.dumps({'url':url,'body':body}))
''')
        result = json.loads(self.cli('oai', CCX_CLAUDE_BIN=str(client), CCX_ROUTER_BIN=router_binary).stdout)
        self.assertEqual(result['body']['content'][0]['text'], 'OK')
        self.assertEqual(observed[0][0:2], ('/v1/chat/completions', 'Bearer fixture-secret'))
        from urllib.parse import urlsplit
        port = urlsplit(result['url']).port
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', port), timeout=0.2)
        self.assertNotIn('fixture-secret', self.cli('logs', 'oai').stdout)

    def test_process_timeout_kills_descendants(self):
        engine = workflow_windows.load_engine()
        parent = self.root / 'parent.py'
        child = self.root / 'child.py'
        child.write_text('import time, pathlib, sys\ntime.sleep(3)\npathlib.Path(sys.argv[1]).write_text("escaped")\n')
        marker = self.root / 'escaped.txt'
        parent.write_text('import subprocess, sys, time\nsubprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]])\ntime.sleep(30)\n')
        with self.assertRaises(engine['WorkflowError']):
            engine['execute']([str(parent), str(child), str(marker)], self.repo, '', 0.7,
                              self.root / 'stdout.txt', self.root / 'stderr.txt')
        import time
        time.sleep(3.2)
        self.assertFalse(marker.exists(), 'Timed-out descendant escaped cleanup.')

    def test_mixed_claude_codex_team_with_orchestrator(self):
        self.new('claude', 'claude')
        self.new('codex', 'codex')
        self.cli('new', '--name', 'mixed', '--type', 'loop', '--orchestrator', 'claude',
                 '--planner', 'codex', '--builder', 'claude', '--reviewer', 'codex',
                 '--proof', 'python implemented.py', '--yes')
        self.cli('mixed', 'Build and verify the fixture')
        runs = list((Path(self.env['CCX_HOME']) / 'runs').glob('mixed-*'))
        self.assertEqual(json.loads((runs[0] / 'result.json').read_text())['status'], 'completed')
        self.assertTrue((runs[0] / 'consensus.json').exists())

    def test_native_extra_profiles_launch_but_refuse_uncontained_team_roles(self):
        for name, provider in [('k', 'kimi-cli'), ('m', 'mcode')]:
            self.new(name, provider, '--model', 'fixture')
            self.assertEqual(json.loads(self.cli(name, 'login').stdout)['args'], ['login'])
            result = self.cli('new', '--name', 'unsafe', '--type', 'loop', '--planner', name,
                              '--reviewer', name, '--yes', ok=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((Path(self.env['CCX_HOME']) / 'profiles/unsafe').exists())

    def test_certification_uses_local_fixtures_and_does_not_probe_without_consent(self):
        import ccx
        calls = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.reply(json.dumps({'data': [{'id': 'fixture'}]}))
            def reply(self, body, content_type='application/json'):
                body = body.encode()
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def do_POST(self):
                value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append(value)
                if value.get('stream'):
                    self.reply('data: {"choices":[{"index":0,"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n', 'text/event-stream')
                elif value.get('tools'):
                    self.reply(json.dumps({'choices': [{'message': {'tool_calls': [{'function': {'name': 'ccx_probe', 'arguments': '{}'}}]}}]}))
                else:
                    self.reply(json.dumps({'choices': [{'message': {'content': 'OK'}}]}))
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.new('local', 'custom-oai', '--model', 'fixture', '--upstream-url', f'http://127.0.0.1:{server.server_port}/v1')
        router_binary = ccx.router_bin()
        self.cli('certify', 'local', '--json', ok=False, CCX_ROUTER_BIN=router_binary)
        self.assertEqual(calls, [])
        report = json.loads(self.cli('certify', 'local', '--json', '--yes', CCX_ROUTER_BIN=router_binary).stdout)
        self.assertEqual(report['capabilities'], dict(basic='pass', streaming='pass', tools='pass'))
        self.assertEqual(len(calls), 3)
        self.assertEqual(json.loads(self.cli('doctor', 'local', '--json', CCX_ROUTER_BIN=router_binary).stdout)['status'], 'pass')

    def test_cmd_installer_initializes_only_disposable_config(self):
        result = subprocess.run(['cmd.exe', '/d', '/c', str(ROOT / 'install-windows.cmd')],
                                env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((Path(self.env['CCX_HOME']) / 'providers' / 'claude.tmpl').exists())


if __name__ == '__main__':
    unittest.main()
