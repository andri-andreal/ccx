"""Exercise real ccx dispatch with disposable profiles/repos and fake CLIs."""
import json
import importlib.util
import io
import os
import pty
import re
import select
from pathlib import Path
import subprocess
import tempfile
import termios
import time
import tomllib
import unittest
from unittest import mock


CCX = Path(__file__).resolve().parents[1] / 'bin' / 'ccx'
SPEC = importlib.util.spec_from_file_location('workflow', CCX.parent.parent / 'lib/workflow.py')
workflow_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workflow_module)
ANSI = re.compile(r'\x1b\[[0-9;]*m')


def visible(line):
    """Strip SGR styling so a styled frame can still be measured in columns."""
    return ANSI.sub('', line)
FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, sys, time
args = sys.argv[1:]
if os.environ.get('FAKE_ARGS_LOG'):
    with open(os.environ['FAKE_ARGS_LOG'], 'a') as log: log.write(json.dumps(args)+'\n')
if args[:3] == ['mcp', 'list', '--json']:
    print(os.environ.get('FAKE_MCP_SERVERS', '[]')); sys.exit(0)
if 'exec' not in args and '-p' not in args:
    print(json.dumps({'args':args, 'home':os.environ.get('CODEX_HOME'),
                      'claude_home':os.environ.get('CLAUDE_CONFIG_DIR')})); sys.exit(0)
kimi = '-p' in args and '--verbose' not in args
mcode = '--permission' in args and 'smart' in args
if mcode and os.environ.get('FAKE_CHECK_TMP'):
    pathlib.Path(os.environ['TMPDIR'], 'scratch-test').write_text('ok')
prompt = args[args.index('-p')+1] if kimi else sys.stdin.read()
if os.environ.get('FAKE_PROMPT_LOG'):
    with open(os.environ['FAKE_PROMPT_LOG'], 'a') as log: log.write(json.dumps(prompt) + '\n')
if mcode and '--output-schema' in args and 'Return only a JSON object matching this schema' not in prompt:
    print('missing structured output instruction', file=sys.stderr)
    sys.exit(2)
case = os.environ.get('FAKE_CASE', '')
if case == 'empty': sys.exit(0)
codex = 'exec' in args and not mcode
if case == 'stream':
    if codex:
        print(json.dumps({'type':'item.started','item':{'type':'command_execution','command':'rg security'}}),flush=True)
    else:
        print(json.dumps({'type':'assistant','message':{'content':[
            {'type':'tool_use','name':'Read','input':{'file_path':'security.py'}},
            {'type':'thinking','thinking':'PRIVATE_REASONING_DO_NOT_DISPLAY'}]}}),flush=True)
    time.sleep(0.6)
schema = None
if '--output-schema' in args:
    schema_arg = args[args.index('--output-schema')+1]
    schema = json.loads(schema_arg if mcode else pathlib.Path(schema_arg).read_text())
elif '--json-schema' in args:
    schema = json.loads(args[args.index('--json-schema')+1])
elif kimi and 'Return only a JSON object matching this schema' in prompt:
    schema = json.loads(prompt.rsplit('Return only a JSON object matching this schema, without Markdown:\n', 1)[1])
if schema and 'route' in schema['properties']:
    value = {'route':os.environ.get('FAKE_ROUTE', 'plan'), 'reason':'Fixture routing.'}
elif schema and 'plan' in schema['properties']:
    value = {'plan':'Create implemented.py; verify with the configured command.'}
elif schema and 'status' in schema['properties']:
    pathlib.Path('implemented.py').write_text('print(42)\n')
    feedback = json.loads(prompt.split('PREVIOUS ROUND / FIX REQUEST:\n',1)[1].split('\nReturn only a JSON object',1)[0])
    findings = (feedback.get('checker') or {}).get('findings',[]) if feedback else []
    if feedback and not feedback.get('checker'):
        findings = feedback.get('previous_findings',[])
    value = {'status':'READY','summary':'Created implemented.py.', 'limitations':[],
             'responses':[{'id':f['id'],'disposition':'FIXED','evidence':'Updated implementation.'} for f in findings]}
    if case == 'builder_needs_work' and feedback is None: value['status'] = 'NEEDS_WORK'
    if case == 'builder_blocked': value.update(status='BLOCKED',limitations=['Required dependency unavailable.'])
    if case == 'missing_response': value['responses'] = []
    if case == 'disputed':
        for response in value['responses']: response['disposition'] = 'DISPUTED'
    if case == 'repair_proof' and feedback:
        pathlib.Path('repaired.py').write_text('fixed')
elif schema:
    value = {'verdict':'APPROVED','summary':'Reviewed fixture.', 'findings':[],
             'coverage':['implemented.py'], 'limitations':[]}
    if case == 'revise' and 'CHANGE MANIFEST' not in prompt:
        value.update(verdict='REVISE',findings=[{'id':'R1','severity':'high','path':'plan',
            'evidence':'Missing case','fix':'Add it'}])
    if case == 'invalid_review':
        value['findings'] = [{'id':'R1','severity':'high','path':'plan','evidence':'bug','fix':'fix'}]
    if case == 'info_review':
        value['findings'] = [{'id':'R1','severity':'info','path':'plan','evidence':'Informational note','fix':'Optional'}]
    if case == 'unknown_severity':
        value['findings'] = [{'id':'R1','severity':'surprise','path':'plan','evidence':'note','fix':'Optional'}]
    if case == 'duplicate_ids':
        value['findings'] = [{'id':'R1','severity':'low','path':'plan','evidence':'note','fix':'Optional'}]*2
    if case == 'mutate_review': pathlib.Path('unexpected.py').write_text('changed')
    if case in ('inspect_revise','missing_response','disputed','always_revise') and 'CHANGE MANIFEST' in prompt:
        marker = pathlib.Path.cwd().parent / 'inspected'
        if not marker.exists() or case == 'always_revise':
            marker.write_text('once')
            value.update(verdict='REVISE',findings=[{'id':'I1','severity':'medium','path':'implemented.py',
                'evidence':'Fixture needs revision','fix':'Revise implementation'}])
    if case == 'mutate_inspection' and 'CHANGE MANIFEST' in prompt:
        pathlib.Path('implemented.py').write_text('modified during inspection')
else:
    if prompt.startswith('Inspect the repository'):
        value = 'Read-only inspection completed.'
    else:
        pathlib.Path('implemented.py').write_text('print(42)\n')
        value = 'Created implemented.py.'
session = '12345678-1234-4567-8123-123456789abc'
if kimi:
    print(json.dumps({'role':'assistant','content':json.dumps(value) if schema else value}))
elif mcode:
    print(json.dumps({'type':'exec.completed','result':{'status':'succeeded','output':value}}))
elif codex:
    pathlib.Path(args[args.index('-o')+1]).write_text(json.dumps(value) if schema else value)
    print(json.dumps({'type':'thread.started','thread_id':session}))
    print(json.dumps({'type':'turn.failed' if case == 'failed_turn' else 'turn.completed'}))
else:
    print(json.dumps({'type':'result','subtype':'success','session_id':session,
        'structured_output':value if schema else None,'result':value if not schema else '', 'is_error':False}))
'''


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ccx-loop-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo with spaces'
        self.repo.mkdir()
        self.fake = self.root / 'fake cli'
        self.fake.write_text(FAKE)
        self.fake.chmod(0o755)
        self.env = {**os.environ, 'CCX_HOME':str(self.root / 'config'),
                    'CCX_CLAUDE_BIN':str(self.fake), 'CCX_CODEX_BIN':str(self.fake),
                    'CCX_KIMI_BIN':str(self.fake), 'CCX_MCODE_BIN':str(self.fake)}
        self.kimi_data = self.root / 'kimi-data'; self.kimi_data.mkdir()
        self.mcode_data = self.root / 'mcode-data'; self.mcode_data.mkdir()
        self.env.update(KIMI_CODE_HOME=str(self.kimi_data), MINIMAX_DATA_DIR=str(self.mcode_data))
        for key in ('CCX_DRY_RUN', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'FAKE_CASE'):
            self.env.pop(key, None)
        for args in (('init','-q'), ('config','user.email','test@example.invalid'),
                     ('config','user.name','Test'), ('commit','--allow-empty','-qm','initial')):
            subprocess.run(['git', *args], cwd=self.repo, check=True, capture_output=True)
        self.ccx('new','--name','claude','--provider','claude','--yes')
        self.ccx('new','--name','codex','--provider','codex','--yes')

    def ccx(self, *args, ok=True, env=None):
        result = subprocess.run([str(CCX), *args], cwd=self.repo, env=env or self.env,
                                text=True, capture_output=True, timeout=20)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def workflow(self, planner='claude', builder='claude', reviewer='codex', proof='test -f implemented.py'):
        return self.ccx('new','--name','team','--type','loop','--planner',planner,
                        '--builder',builder,'--reviewer',reviewer,'--proof',proof,'--yes')

    def result(self):
        return json.loads(next((self.root / 'config' / 'runs').glob('team-*/result.json')).read_text())

    def test_additional_native_cli_profiles(self):
        for provider in ('kimi-cli', 'mcode'):
            self.ccx('new', '--name', provider, '--provider', provider, '--model', 'chosen', '--yes')
            login = json.loads(self.ccx(provider, 'login').stdout)
            self.assertEqual(login['args'], ['login'])
            run = json.loads(self.ccx(provider, 'hello').stdout)
            self.assertEqual(run['args'], ['--model', 'chosen', 'hello'])
            override = json.loads(self.ccx(provider, '--model', 'other', 'hello').stdout)
            self.assertEqual(override['args'], ['--model', 'other', 'hello'])
            self.assertIn(provider, self.ccx('list').stdout)
            self.ccx('new', '--name', 'team-' + provider, '--type', 'loop',
                     '--planner', provider, '--builder', provider, '--reviewer', provider,
                     '--proof', 'test -f implemented.py', '--yes')
            run = self.ccx('team-' + provider, 'Implement the fixture')
            self.assertIn('finished', run.stdout)
            (self.repo / 'implemented.py').unlink()


    def test_undo_redo_restores_files_and_rejects_external_changes(self):
        source = self.repo / 'source.txt'
        source.write_text('original\n')
        (self.repo / 'binary.bin').write_bytes(b'\x00old\xff')
        (self.repo / 'link').symlink_to('source.txt')
        self.commit_fixture()
        before = self.root / 'before.zip'
        after = self.root / 'after.zip'
        workflow_module.capture_checkpoint(self.repo, before)
        source.write_text('changed\n')
        (self.repo / 'binary.bin').write_bytes(b'\x00new\xff')
        (self.repo / 'link').unlink()
        (self.repo / 'new.txt').write_text('added')
        workflow_module.capture_checkpoint(self.repo, after)
        workflow_module.restore_checkpoint(self.repo, after, before)
        self.assertEqual(source.read_text(), 'original\n')
        self.assertEqual((self.repo / 'binary.bin').read_bytes(), b'\x00old\xff')
        self.assertEqual(os.readlink(self.repo / 'link'), 'source.txt')
        self.assertFalse((self.repo / 'new.txt').exists())
        workflow_module.restore_checkpoint(self.repo, before, after)
        self.assertEqual(source.read_text(), 'changed\n')
        self.assertEqual((self.repo / 'new.txt').read_text(), 'added')
        source.write_text('external edit\n')
        with self.assertRaisesRegex(workflow_module.WorkflowError, 'project changed'):
            workflow_module.restore_checkpoint(self.repo, after, before)
        self.assertEqual(source.read_text(), 'external edit\n')

    def test_undo_refuses_staged_changes_and_ignored_collisions(self):
        before = self.root / 'before.zip'
        after = self.root / 'after.zip'
        workflow_module.capture_checkpoint(self.repo, before)
        target = self.repo / 'new.txt'
        target.write_text('created')
        workflow_module.capture_checkpoint(self.repo, after)
        subprocess.run(['git', 'add', 'new.txt'], cwd=self.repo, check=True)
        with self.assertRaisesRegex(workflow_module.WorkflowError, 'staged'):
            workflow_module.restore_checkpoint(self.repo, after, before)
        self.assertEqual(target.read_text(), 'created')

        subprocess.run(['git', 'restore', '--staged', 'new.txt'], cwd=self.repo, check=True)
        workflow_module.restore_checkpoint(self.repo, after, before)
        (self.repo / '.git' / 'info' / 'exclude').write_text('new.txt\n')
        target.write_text('ignored user file')
        with self.assertRaisesRegex(workflow_module.WorkflowError, 'untracked or ignored'):
            workflow_module.restore_checkpoint(self.repo, before, after)
        self.assertEqual(target.read_text(), 'ignored user file')

    def test_undo_rolls_back_if_restore_fails(self):
        (self.repo / 'a').write_text('old a')
        (self.repo / 'b').write_text('old b')
        self.commit_fixture()
        before, after = self.root / 'before.zip', self.root / 'after.zip'
        workflow_module.capture_checkpoint(self.repo, before)
        (self.repo / 'a').write_text('new a')
        (self.repo / 'b').write_text('new b')
        workflow_module.capture_checkpoint(self.repo, after)
        replace = os.replace
        calls = 0
        def fail_once(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('simulated failure')
            return replace(source, target)
        with mock.patch.object(workflow_module.os, 'replace', side_effect=fail_once):
            with self.assertRaisesRegex(OSError, 'simulated failure'):
                workflow_module.restore_checkpoint(self.repo, after, before)
        self.assertEqual((self.repo / 'a').read_text(), 'new a')
        self.assertEqual((self.repo / 'b').read_text(), 'new b')

    def test_theme_persists_without_overwriting_other_settings(self):
        config = {'CCX_PLANNER': 'a', 'CCX_BUILDER': 'b', 'CCX_REVIEWER': 'c'}
        settings = self.root / 'tui.json'
        settings.write_text(json.dumps({'other': 'preserve'}))
        with mock.patch.dict(os.environ, {'CCX_HOME': str(self.root)}):
            ui = workflow_module.TeamUI(config, None)
            ui.set_theme('ocean')
            self.assertEqual(json.loads(settings.read_text()), {'other': 'preserve', 'theme': 'ocean'})
            self.assertEqual(settings.stat().st_mode & 0o777, 0o600)
            self.assertEqual(workflow_module.TeamUI(config, None).theme, 'ocean')
            with self.assertRaises(workflow_module.WorkflowError):
                ui.set_theme('unknown')
            self.assertEqual(json.loads(settings.read_text())['theme'], 'ocean')

    def test_file_references_confined_to_workspace(self):
        (self.repo / 'source.txt').write_text('useful context')
        result = workflow_module.attach_file_context('Review @source.txt', self.repo)
        self.assertIn('FILE @source.txt', result)
        self.assertIn('useful context', result)
        (self.root / 'outside.txt').write_text('private')
        (self.repo / 'escape').symlink_to(self.root / 'outside.txt')
        for task in ('Review @../outside.txt', 'Review @escape'):
            with self.assertRaises(workflow_module.WorkflowError):
                workflow_module.attach_file_context(task, self.repo)
        (self.repo / 'binary').write_bytes(b'\x00data')
        with self.assertRaises(workflow_module.WorkflowError):
            workflow_module.attach_file_context('Review @binary', self.repo)

    def test_explicit_file_context_reaches_the_agent(self):
        source = self.repo / 'source.txt'
        source.write_text('specific context for review')
        self.commit_fixture()
        self.workflow()
        log = self.root / 'prompts.jsonl'
        self.ccx('team', '--review-only', 'Review @source.txt',
                 env={**self.env, 'FAKE_PROMPT_LOG': str(log)})
        prompts = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertTrue(any('specific context for review' in prompt for prompt in prompts))

    def test_checkpoint_size_limit_leaves_no_partial_archive(self):
        (self.repo / 'big').write_bytes(b'123456')
        destination = self.root / 'limited.zip'
        with mock.patch.object(workflow_module, 'CHECKPOINT_LIMIT', 4):
            with self.assertRaisesRegex(workflow_module.WorkflowError, '64 MiB'):
                workflow_module.capture_checkpoint(self.repo, destination)
        self.assertFalse(destination.exists())

    def test_workflow_records_normalized_events(self):
        self.workflow()
        self.ccx('team', '--review-only', 'Inspect fixture')
        path = next((self.root / 'config' / 'runs').glob('team-*/events.jsonl'))
        events = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual([e['sequence'] for e in events], list(range(1, len(events) + 1)))
        self.assertEqual(events[0]['type'], 'task_started')
        self.assertEqual(events[-1]['type'], 'task_finished')
        self.assertTrue(any(e['type'] == 'agent_started' for e in events))
        self.assertTrue(any(e['type'] == 'agent_finished' for e in events))
        self.assertTrue(any(e['type'] == 'activity' for e in events))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_codex_mcp_override_preserves_literal_server_names(self):
        names = ['gamersbego', 'server.with.dot', 'server "quote"']
        servers = [{'name': name} for name in names]
        override = workflow_module.disable_codex_mcp(servers)
        parsed = tomllib.loads(override[1])
        self.assertEqual(override[0], '-c')
        self.assertEqual(set(parsed['mcp_servers']), set(names))
        self.assertTrue(all(entry == {'enabled': False} for entry in parsed['mcp_servers'].values()))
        with self.assertRaises(workflow_module.WorkflowError):
            workflow_module.disable_codex_mcp([{'name': 'bad\nname'}])

    def test_codex_workflow_passes_one_complete_mcp_override(self):
        self.workflow(planner='codex', builder='codex', reviewer='codex')
        log = self.root / 'mcp-args.jsonl'
        servers = [{'name': 'gamersbego'}, {'name': 'server.with.dot'}]
        env = {**self.env, 'FAKE_MCP_SERVERS': json.dumps(servers), 'FAKE_ARGS_LOG': str(log)}
        self.ccx('team', '--review-only', 'Review fixture', env=env)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        turns = [args for args in calls if 'exec' in args]
        self.assertGreaterEqual(len(turns), 2)
        for args in turns:
            settings = [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == '-c']
            overrides = [value for value in settings if value.startswith('mcp_servers=')]
            self.assertEqual(len(overrides), 1)
            self.assertEqual(set(tomllib.loads(overrides[0])['mcp_servers']), {'gamersbego', 'server.with.dot'})

    def test_orchestrator_routes_and_manual_override(self):
        self.ccx('new', '--name', 'team', '--type', 'loop', '--orchestrator', 'codex',
                 '--planner', 'claude', '--builder', 'claude', '--reviewer', 'codex',
                 '--proof', 'test -f implemented.py', '--yes')
        self.assertIn('CCX_ORCHESTRATOR=codex', self.ccx('show', 'team').stdout)
        env = {**self.env, 'FAKE_ROUTE': 'inspect'}
        result = self.ccx('team', 'Inspect the project', env=env)
        self.assertIn('Read-only inspection completed', result.stdout)
        self.assertFalse((self.repo / 'implemented.py').exists())
        result = self.ccx('team', '--route', 'direct', 'Implement fixture', env=env)
        self.assertTrue((self.repo / 'implemented.py').exists())
        self.assertIn('route selected by user: direct', result.stdout)
        self.assertNotIn('orchestrate (', result.stdout)

    def test_four_distinct_cli_roles_complete_a_workflow(self):
        self.ccx('new', '--name', 'kimi', '--provider', 'kimi-cli', '--yes')
        self.ccx('new', '--name', 'mcode', '--provider', 'mcode', '--yes')
        self.ccx('new', '--name', 'team', '--type', 'loop', '--orchestrator', 'kimi',
                 '--planner', 'codex', '--builder', 'mcode', '--reviewer', 'claude',
                 '--proof', 'test -f implemented.py', '--yes')
        result = self.ccx('team', 'Implement fixture')
        for expected in ('orchestrate (kimi', 'plan (codex', 'build (mcode', 'review (claude'):
            self.assertIn(expected, result.stdout)
        self.assertTrue((self.repo / 'implemented.py').is_file())

    def test_orchestrator_read_only_route_requires_no_proof(self):
        self.ccx('new', '--name', 'team', '--type', 'loop', '--orchestrator', 'codex',
                 '--planner', 'claude', '--reviewer', 'codex', '--yes')
        result = self.ccx('team', 'Explain the project', env={**self.env, 'FAKE_ROUTE': 'inspect'})
        self.assertIn('Read-only inspection completed', result.stdout)
        self.assertFalse((self.repo / 'implemented.py').exists())

    def test_orchestrator_plan_route_and_invalid_route(self):
        self.ccx('new', '--name', 'team', '--type', 'loop', '--orchestrator', 'claude',
                 '--planner', 'claude', '--builder', 'claude', '--reviewer', 'codex',
                 '--proof', 'test -f implemented.py', '--yes')
        invalid = self.ccx('team', 'Implement fixture', ok=False,
                           env={**self.env, 'FAKE_ROUTE': 'invalid'})
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn('invalid route', invalid.stderr)
        self.assertFalse((self.repo / 'implemented.py').exists())
        result = self.ccx('team', 'Implement fixture')
        self.assertIn('orchestrator chose plan', result.stdout)
        self.assertTrue((self.repo / 'implemented.py').exists())

    def test_mcode_initializes_missing_data_directory(self):
        self.mcode_data.rmdir()
        self.ccx('new', '--name', 'mcode', '--provider', 'mcode', '--yes')
        self.workflow(planner='mcode', builder='mcode', reviewer='mcode')
        self.ccx('team', 'Implement fixture', env={**self.env, 'FAKE_CHECK_TMP': '1'})
        self.assertTrue(self.mcode_data.is_dir())

    def test_team_tui_layout_adapts_to_terminal_width(self):
        class Terminal(io.StringIO):
            def isatty(self): return True
        config = {'CCX_ORCHESTRATOR': 'z', 'CCX_PLANNER': 'a', 'CCX_BUILDER': 'b', 'CCX_REVIEWER': 'c'}
        with mock.patch.object(workflow_module.sys, 'stdout', new_callable=Terminal) as terminal, \
             mock.patch.dict(os.environ, {'TERM': 'xterm'}), \
             mock.patch.object(workflow_module.shutil, 'get_terminal_size', return_value=os.terminal_size((100, 28))):
            ui = workflow_module.TeamUI(config, '/tmp/run')
            ui.visible = ['orchestrate']
            ui.select_route('plan')
            lines = terminal.getvalue().splitlines()
            plan = next(i for i, line in enumerate(lines) if 'PLAN' in line)
            execute = next(i for i, line in enumerate(lines) if 'EXECUTE' in line)
            review = next(i for i, line in enumerate(lines) if 'REVIEW' in line)
            orchestrator = next(i for i, line in enumerate(lines) if 'ORCHESTRATOR' in line)
            self.assertLess(orchestrator, plan)
            self.assertLess(plan, execute)
            self.assertLess(execute, review)
            self.assertEqual(len(visible(lines[review])), 100)
            self.assertIn('CONVERSATION', terminal.getvalue())
            self.assertIn('waiting', lines[orchestrator])
        with mock.patch.object(workflow_module.sys, 'stdout', new_callable=Terminal) as terminal, \
             mock.patch.dict(os.environ, {'TERM': 'xterm'}), \
             mock.patch.object(workflow_module.shutil, 'get_terminal_size', return_value=os.terminal_size((60, 28))):
            ui = workflow_module.TeamUI(config, '/tmp/run')
            ui.visible = ['orchestrate']
            ui.select_route('plan')
            lines = terminal.getvalue().splitlines()
            positions = [next(i for i, line in enumerate(lines) if label in line)
                         for label in ('ORCHESTRATOR', 'PLAN', 'EXECUTE', 'REVIEW')]
            self.assertEqual(positions, sorted(set(positions)))

    def test_dynamic_panels_follow_route(self):
        class Terminal(io.StringIO):
            def isatty(self): return True
        config = {'CCX_ORCHESTRATOR': 'a', 'CCX_PLANNER': 'b', 'CCX_BUILDER': 'c', 'CCX_REVIEWER': 'd'}
        with mock.patch.object(workflow_module.sys, 'stdout', new_callable=Terminal), \
             mock.patch.dict(os.environ, {'TERM': 'xterm'}):
            ui = workflow_module.TeamUI(config, '/tmp/run')
            self.assertEqual(ui.visible, [])
            ui.begin('orchestrate', 'a', '', 'default')
            self.assertEqual(ui.visible, ['orchestrate'])
            ui.select_route('inspect')
            self.assertEqual(ui.visible, ['orchestrate', 'review'])
            ui.select_route('direct')
            self.assertEqual(ui.visible, ['orchestrate', 'build', 'review'])
            ui.select_route('plan')
            self.assertEqual(ui.visible, ['orchestrate', 'plan', 'build', 'review'])
            ui.select_route('plan', review_only=True)
            self.assertEqual(ui.visible, ['orchestrate', 'plan', 'review'])

    def test_orchestrator_panel_shows_activity(self):
        class Terminal(io.StringIO):
            def isatty(self): return True
        config = {'CCX_ORCHESTRATOR': 'kimi', 'CCX_PLANNER': 'a', 'CCX_BUILDER': 'b', 'CCX_REVIEWER': 'c'}
        with mock.patch.object(workflow_module.sys, 'stdout', new_callable=Terminal) as terminal, \
             mock.patch.dict(os.environ, {'TERM': 'xterm'}), \
             mock.patch.object(workflow_module.shutil, 'get_terminal_size', return_value=os.terminal_size((100, 28))):
            ui = workflow_module.TeamUI(config, '/tmp/run')
            ui.begin('orchestrate', 'kimi', '', 'default')
            ui.update('orchestrate', 'Choosing route')
            ui.done('orchestrate')
            self.assertEqual(ui.panels['orchestrate']['status'], 'done')
            self.assertIn('Choosing route', terminal.getvalue())
            self.assertIn('CLI: kimi', terminal.getvalue())

    def test_result_preview_is_rendered_in_place(self):
        class Terminal(io.StringIO):
            def isatty(self): return True
        config = {'CCX_ORCHESTRATOR': 'a', 'CCX_PLANNER': 'b', 'CCX_BUILDER': 'c', 'CCX_REVIEWER': 'd'}
        with mock.patch.object(workflow_module.sys, 'stdout', new_callable=Terminal) as terminal, \
             mock.patch.dict(os.environ, {'TERM': 'xterm'}), \
             mock.patch.object(workflow_module.shutil, 'get_terminal_size', return_value=os.terminal_size((100, 28))):
            ui = workflow_module.TeamUI(config, '/tmp/run')
            ui.visible = ['review']
            ui.result = 'Plan approved and the build verified.'
            ui.result_status = 'completed'
            ui.render()
            frame = terminal.getvalue()
            self.assertIn('RESULT', frame)
            self.assertIn('Plan approved and the build verified.', frame)
            self.assertIn('completed', frame)
            self.assertIn('\x1b[', frame)

    def test_colour_can_be_disabled_and_status_stays_readable(self):
        class Terminal(io.StringIO):
            def isatty(self): return True
        config = {'CCX_ORCHESTRATOR': 'a', 'CCX_PLANNER': 'b', 'CCX_BUILDER': 'c', 'CCX_REVIEWER': 'd'}
        with mock.patch.object(workflow_module.sys, 'stdout', new_callable=Terminal) as terminal, \
             mock.patch.dict(os.environ, {'TERM': 'xterm', 'NO_COLOR': '1'}), \
             mock.patch.object(workflow_module.shutil, 'get_terminal_size', return_value=os.terminal_size((100, 28))):
            ui = workflow_module.TeamUI(config, '/tmp/run')
            ui.visible = ['review']
            ui.begin('review', 'codex', 'gpt-6.1-sol', 'medium')
            ui.done('review')
            frame = terminal.getvalue()
            self.assertIsNone(ANSI.search(frame))
            self.assertIn('REVIEW', frame)
            self.assertIn('done', frame)

    def test_agent_drill_down_lists_agents_and_opens_one(self):
        class Terminal(io.StringIO):
            def isatty(self): return True
        config = {'CCX_ORCHESTRATOR': 'a', 'CCX_PLANNER': 'b', 'CCX_BUILDER': 'c', 'CCX_REVIEWER': 'd'}
        with mock.patch.object(workflow_module.sys, 'stdout', new_callable=Terminal) as terminal, \
             mock.patch.dict(os.environ, {'TERM': 'xterm'}), \
             mock.patch.object(workflow_module.shutil, 'get_terminal_size', return_value=os.terminal_size((100, 28))):
            ui = workflow_module.TeamUI(config, '/tmp/run')
            ui.visible = ['review']
            ui.begin('review', 'codex', 'gpt-6.1-sol', 'medium')
            ui.update('review', 'tool: glob')
            with mock.patch('builtins.input', side_effect=['1', '', 'q']):
                ui.show_agents()
            frame = terminal.getvalue()
            self.assertIn('AGENTS', frame)
            self.assertIn('REVIEW', frame)
            self.assertIn('tool: glob', frame)

    def test_team_tui_session_and_persisted_history(self):
        self.workflow()
        master, slave = pty.openpty()
        try:
            prompt_log = self.root / 'session-prompts.jsonl'
            with subprocess.Popen([str(CCX), 'team', '--review-only'], cwd=self.repo,
                                  env={**self.env, 'TERM': 'xterm', 'FAKE_PROMPT_LOG': str(prompt_log)},
                                  stdin=slave, stdout=slave, stderr=slave) as proc:
                os.close(slave)
                slave = None
                def read_until(token):
                    data = b''
                    deadline = time.monotonic() + 12
                    while token not in data:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not select.select([master], [], [], remaining)[0]:
                            proc.kill()
                            self.fail(f'Missing {token!r} in output: {data[-1000:]!r}')
                        data += os.read(master, 65536)
                    return data
                initial = read_until(b'Task (/history')
                self.assertIn(b'What would you like', initial)
                for label in (b'ORCHESTRATOR', b'PLAN |', b'EXECUTE', b'REVIEW'):
                    self.assertNotIn(label, initial)
                os.write(master, b'\x10')
                self.assertIn(b'COMMAND PALETTE', read_until(b'Search/number: '))
                os.write(master, b'files\n')
                read_until(b'Search/number: ')
                os.write(master, b'1\n')
                self.assertIn(b'WORKSPACE FILES', read_until(b'[n] next'))
                os.write(master, b'\n')
                read_until(b'Task (/history')
                os.write(master, b'Fixture task\n')
                completed = read_until(b'[n] next')
                self.assertIn(b'PLAN', completed)
                self.assertIn(b'REVIEW', completed)
                self.assertNotIn(b'EXECUTE', completed)
                self.assertIn(b'CONCLUSION', completed)
                os.write(master, b'\n')
                read_until(b'Task (/history')
                os.write(master, b'/result\n')
                detail = read_until(b'[n] next')
                self.assertIn(b'Plan approved', detail)
                os.write(master, b'\n')
                read_until(b'Task (/history')
                os.write(master, b'/history\n')
                history = read_until(b'History: ')
                self.assertIn(b'Fixture task', history)
                os.write(master, b'1\n')
                detail = read_until(b'[n] next')
                self.assertIn(b'Fixture task', detail)
                self.assertIn(b'Plan approved', detail)
                os.write(master, b'\n')
                read_until(b'History: ')
                os.write(master, b'\n')
                read_until(b'Task (/history')
                os.write(master, b'Follow-up task\n')
                second = read_until(b'[n] next')
                self.assertIn(b'CONCLUSION', second)
                os.write(master, b'\n')
                read_until(b'Task (/history')
                os.write(master, b'/quit\n')
                proc.wait(timeout=10)
                self.assertEqual(proc.returncode, 0)
            runs = list((self.root / 'config' / 'runs').glob('team-*/session.json'))
            self.assertEqual({json.loads(run.read_text())['task'] for run in runs}, {'Fixture task', 'Follow-up task'})
            prompts = [json.loads(line) for line in prompt_log.read_text().splitlines()]
            self.assertTrue(any('PREVIOUS TASK:\nFixture task' in prompt and
                                'CURRENT USER TASK (follow this):\nFollow-up task' in prompt
                                for prompt in prompts))
        finally:
            os.close(master)
            if slave is not None:
                os.close(slave)

    def test_legacy_team_orchestrates_and_displays_only_required_panels(self):
        self.ccx('new', '--name', 'team', '--type', 'loop',
                 '--planner', 'claude', '--reviewer', 'codex', '--yes')
        master, slave = pty.openpty()
        try:
            with subprocess.Popen([str(CCX), 'team'], cwd=self.repo,
                                  env={**self.env, 'TERM': 'xterm', 'FAKE_ROUTE': 'inspect'},
                                  stdin=slave, stdout=slave, stderr=slave) as proc:
                os.close(slave)
                slave = None
                def read_until(token):
                    data = b''
                    deadline = time.monotonic() + 12
                    while token not in data:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not select.select([master], [], [], remaining)[0]:
                            proc.kill()
                            self.fail(f'Missing {token!r}: {data[-1000:]!r}')
                        data += os.read(master, 65536)
                    return data
                initial = read_until(b'Task (/history')
                self.assertNotIn(b'ORCHESTRATOR |', initial)
                os.write(master, b'Inspect the project\n')
                output = read_until(b'[n] next')
                self.assertIn(b'ORCHESTRATOR', output)
                self.assertIn(b'REVIEW', output)
                self.assertNotIn(b'PLAN |', output)
                self.assertNotIn(b'EXECUTE |', output)
                self.assertIn(b'Read-only inspection completed', output)
                os.write(master, b'\n')
                read_until(b'Task (/history')
                os.write(master, b'/quit\n')
                self.assertEqual(proc.wait(timeout=10), 0)
            saved = (self.root / 'config' / 'profiles' / 'team' / 'workflow.env').read_text()
            self.assertNotIn('CCX_ORCHESTRATOR=', saved)
            effective = next((self.root / 'config' / 'runs').glob('team-*/workflow.json'))
            self.assertEqual(json.loads(effective.read_text())['CCX_ORCHESTRATOR'], 'claude')
        finally:
            os.close(master)
            if slave is not None:
                os.close(slave)

    def test_missing_verification_command_prompts_in_the_tui(self):
        self.ccx('new', '--name', 'team', '--type', 'loop',
                 '--planner', 'claude', '--builder', 'claude', '--reviewer', 'codex', '--yes')
        master, slave = pty.openpty()
        try:
            with subprocess.Popen([str(CCX), 'team'], cwd=self.repo,
                                  env={**self.env, 'TERM': 'xterm', 'FAKE_ROUTE': 'plan'},
                                  stdin=slave, stdout=slave, stderr=slave) as proc:
                os.close(slave)
                slave = None

                def read_until(token):
                    data = b''
                    deadline = time.monotonic() + 15
                    while token not in data:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not select.select([master], [], [], remaining)[0]:
                            proc.kill()
                            self.fail(f'Missing {token!r} in output: {data[-1000:]!r}')
                        data += os.read(master, 65536)
                    return data

                read_until(b'Task (/history')
                os.write(master, b'Fixture task\n')
                self.assertIn(b'Verification command', read_until(b'Verification command'))
                os.write(master, b'true\n')
                output = read_until(b'[n] next')
                self.assertIn(b'CONCLUSION', output)
                self.assertIn(b'Verification: true', output)
                os.write(master, b'\n')
                read_until(b'Task (/history')
                os.write(master, b'/quit\n')
                self.assertEqual(proc.wait(timeout=10), 0)
        finally:
            os.close(master)
            if slave is not None:
                os.close(slave)

    def test_empty_verification_prompt_points_at_the_proof_command(self):
        self.ccx('new', '--name', 'team', '--type', 'loop',
                 '--planner', 'claude', '--builder', 'claude', '--reviewer', 'codex', '--yes')
        master, slave = pty.openpty()
        try:
            with subprocess.Popen([str(CCX), 'team'], cwd=self.repo,
                                  env={**self.env, 'TERM': 'xterm', 'FAKE_ROUTE': 'plan'},
                                  stdin=slave, stdout=slave, stderr=slave) as proc:
                os.close(slave)
                slave = None

                def read_until(token):
                    data = b''
                    deadline = time.monotonic() + 15
                    while token not in data:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not select.select([master], [], [], remaining)[0]:
                            proc.kill()
                            self.fail(f'Missing {token!r} in output: {data[-1000:]!r}')
                        data += os.read(master, 65536)
                    return data

                read_until(b'Task (/history')
                os.write(master, b'Fixture task\n')
                read_until(b'Verification command')
                os.write(master, b'\n')
                output = read_until(b'[n] next')
                self.assertIn(b'/proof', output)
                self.assertIn(b'No verification command', output)
                os.write(master, b'\n')
                read_until(b'Task (/history')
                os.write(master, b'/quit\n')
                self.assertEqual(proc.wait(timeout=10), 0)
        finally:
            os.close(master)
            if slave is not None:
                os.close(slave)

    def test_provider_picker_lists_every_template(self):
        master, slave = pty.openpty()
        # No `with` block: Popen.__exit__ waits without a timeout, so a failed
        # assertion would leave the menu alive and block the whole suite.
        proc = subprocess.Popen([str(CCX), 'new', '--name', 'pick'], cwd=self.repo,
                                env={**self.env, 'TERM': 'xterm'},
                                stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        slave = None
        try:
            def read_until(token):
                data = b''
                deadline = time.monotonic() + 12
                while token not in data:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not select.select([master], [], [], remaining)[0]:
                        proc.kill()
                        self.fail(f'Missing {token!r}: {data[-1000:]!r}')
                    data += os.read(master, 65536)
                return data

            read_until(b'Choose profile type')
            os.write(master, b'\r')
            read_until(b'Choose provider')
            # The box is drawn top-down; let the remaining rows arrive.
            picker = b''
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.2)[0]:
                    chunk = os.read(master, 65536)
                    if not chunk:
                        break
                    picker += chunk
            for provider in (b'claude', b'codex', b'custom-oai', b'deepseek', b'glm',
                             b'kimi', b'lmstudio', b'minimax', b'ollama', b'openai',
                             b'openrouter', b'sakana', b'vllm', b'kimi-cli', b'mcode'):
                self.assertIn(provider, picker, f'picker must offer {provider!r}')
            self.assertIn(b'Custom / type manually', picker)
            self.assertIn(b'Anthropic-compatible', picker)
            self.assertIn(b'OpenAI-compatible', picker)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
            os.close(master)
            if slave is not None:
                os.close(slave)

    def test_queue_and_stop_while_agent_runs(self):
        self.workflow()
        def start():
            master, slave = pty.openpty()
            proc = subprocess.Popen([str(CCX), 'team', '--review-only'], cwd=self.repo,
                                    env={**self.env, 'TERM': 'xterm', 'FAKE_CASE': 'stream'},
                                    stdin=slave, stdout=slave, stderr=slave)
            os.close(slave)
            return master, proc
        def read_until(master, proc, token):
            data = b''
            deadline = time.monotonic() + 15
            while token not in data:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([master], [], [], remaining)[0]:
                    proc.kill()
                    self.fail(f'Missing {token!r}: {data[-1000:]!r}')
                data += os.read(master, 65536)
            return data
        master, proc = start()
        try:
            read_until(master, proc, b'Task (/history')
            os.write(master, b'First task\n')
            read_until(master, proc, b'Queue: ')
            os.write(master, b'Follow-up queued\n')
            read_until(master, proc, b'[n] next')
            os.write(master, b'\n')
            second = read_until(master, proc, b'[n] next')
            self.assertIn(b'CONCLUSION', second)
            os.write(master, b'\n')
            read_until(master, proc, b'Task (/history')
            os.write(master, b'/quit\n')
            self.assertEqual(proc.wait(timeout=10), 0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            os.close(master)
        runs = list((self.root / 'config' / 'runs').glob('team-*/session.json'))
        self.assertEqual({json.loads(p.read_text())['task'] for p in runs}, {'First task', 'Follow-up queued'})
        master, proc = start()
        try:
            read_until(master, proc, b'Task (/history')
            os.write(master, b'Stop test\n')
            read_until(master, proc, b'Queue: ')
            time.sleep(0.2)
            os.write(master, b'\x1b')
            output = read_until(master, proc, b'[n] next')
            self.assertIn(b'stopped by user', output)
            os.write(master, b'\n')
            read_until(master, proc, b'Task (/history')
            os.write(master, b'/quit\n')
            self.assertEqual(proc.wait(timeout=10), 0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            os.close(master)

    def test_failed_build_can_be_undone(self):
        self.workflow()
        master, slave = pty.openpty()
        try:
            with subprocess.Popen([str(CCX), 'team', '--route', 'direct'], cwd=self.repo,
                                  env={**self.env, 'TERM': 'xterm', 'FAKE_CASE': 'builder_blocked'},
                                  stdin=slave, stdout=slave, stderr=slave) as proc:
                os.close(slave)
                slave = None
                def read_until(token):
                    data = b''
                    deadline = time.monotonic() + 12
                    while token not in data:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not select.select([master], [], [], remaining)[0]:
                            proc.kill()
                            self.fail(f'Missing {token!r}: {data[-1000:]!r}')
                        data += os.read(master, 65536)
                    return data
                read_until(b'Task (/history')
                os.write(master, b'Attempt task\n')
                self.assertIn(b'FAILED', read_until(b'[n] next'))
                self.assertTrue((self.repo / 'implemented.py').exists())
                os.write(master, b'\n')
                read_until(b'Task (/history')
                os.write(master, b'/undo\n')
                self.assertIn(b'Undo complete', read_until(b'Task (/history'))
                self.assertFalse((self.repo / 'implemented.py').exists())
                os.write(master, b'/quit\n')
                self.assertEqual(proc.wait(timeout=10), 0)
        finally:
            os.close(master)
            if slave is not None:
                os.close(slave)

    def test_session_continues_unchanged_results_and_rejects_external_edits(self):
        self.workflow()
        master, slave = pty.openpty()
        try:
            with subprocess.Popen([str(CCX), 'team'], cwd=self.repo,
                                  env={**self.env, 'TERM': 'xterm'},
                                  stdin=slave, stdout=slave, stderr=slave) as proc:
                os.close(slave)
                slave = None
                def read_until(token):
                    data = b''
                    deadline = time.monotonic() + 12
                    while token not in data:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not select.select([master], [], [], remaining)[0]:
                            proc.kill()
                            self.fail(f'Missing {token!r}: {data[-1000:]!r}')
                        data += os.read(master, 65536)
                    return data
                read_until(b'Task (/history')
                os.write(master, b'/route direct\n')
                self.assertIn(b'Route set to direct', read_until(b'Task (/history'))
                os.write(master, b'/proof test -f implemented.py\n')
                self.assertIn(b'Verification command updated', read_until(b'Task (/history'))
                os.write(master, b'First task\n')
                read_until(b'[n] next')
                os.write(master, b'\n')
                read_until(b'Task (/history')
                os.write(master, b'Second task\n')
                second = read_until(b'[n] next')
                self.assertIn(b'CONCLUSION', second)
                os.write(master, b'\n')
                read_until(b'Task (/history')
                os.write(master, b'/undo\n')
                self.assertIn(b'Undo complete', read_until(b'Task (/history'))
                self.assertFalse((self.repo / 'implemented.py').exists())
                os.write(master, b'/redo\n')
                self.assertIn(b'Redo complete', read_until(b'Task (/history'))
                self.assertTrue((self.repo / 'implemented.py').exists())
                (self.repo / 'implemented.py').write_text('external edit\n')
                os.write(master, b'/undo\n')
                refused = read_until(b'[n] next')
                self.assertIn(b'project changed', refused)
                self.assertEqual((self.repo / 'implemented.py').read_text(), 'external edit\n')
                os.write(master, b'\n')
                read_until(b'Task (/history')
                os.write(master, b'Third task\n')
                failure = read_until(b'[n] next')
                self.assertIn(b'clean Git checkout', failure)
                os.write(master, b'\n')
                read_until(b'Task (/history')
                os.write(master, b'/quit\n')
                self.assertEqual(proc.wait(timeout=10), 0)
            runs = list((self.root / 'config' / 'runs').glob('team-*/session.json'))
            self.assertEqual(len(runs), 2)
        finally:
            os.close(master)
            if slave is not None:
                os.close(slave)

    def test_history_survives_restarting_the_terminal_session(self):
        self.workflow()
        self.ccx('team', '--review-only', 'Previous run')
        root = self.root / 'config' / 'runs'
        older = root / 'team-abcdefgh'
        older.mkdir()
        (older / 'workflow.json').write_text('{}')
        (older / 'result.json').write_text(json.dumps({'status': 'completed', 'summary': 'Old run'}))
        master, slave = pty.openpty()
        try:
            with subprocess.Popen([str(CCX), 'team'], cwd=self.repo,
                                  env={**self.env, 'TERM': 'xterm'},
                                  stdin=slave, stdout=slave, stderr=slave) as proc:
                os.close(slave)
                slave = None
                def read_until(token):
                    data = b''
                    deadline = time.monotonic() + 10
                    while token not in data:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not select.select([master], [], [], remaining)[0]:
                            proc.kill()
                            self.fail(f'Missing {token!r}: {data[-1000:]!r}')
                        data += os.read(master, 65536)
                    return data
                read_until(b'Task (/history')
                os.write(master, b'/history\n')
                output = read_until(b'History: ')
                self.assertIn(b'Previous run', output)
                self.assertIn(b'task not recorded in earlier run', output)
                os.write(master, b'\n')
                read_until(b'Task (/history')
                os.write(master, b'/quit\n')
                self.assertEqual(proc.wait(timeout=10), 0)
        finally:
            os.close(master)
            if slave is not None:
                os.close(slave)

    def test_resume_session_uses_context_and_rejects_changed_checkout(self):
        self.workflow()
        self.ccx('team', '--review-only', 'Previous run')
        prompt_log = self.root / 'resume-prompts.jsonl'
        master, slave = pty.openpty()
        try:
            with subprocess.Popen([str(CCX), 'team', '--review-only'], cwd=self.repo,
                                  env={**self.env, 'TERM': 'xterm', 'FAKE_PROMPT_LOG': str(prompt_log)},
                                  stdin=slave, stdout=slave, stderr=slave) as proc:
                os.close(slave)
                slave = None
                def read_until(token):
                    data = b''
                    deadline = time.monotonic() + 12
                    while token not in data:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not select.select([master], [], [], remaining)[0]:
                            proc.kill()
                            self.fail(f'Missing {token!r}: {data[-1000:]!r}')
                        data += os.read(master, 65536)
                    return data
                read_until(b'Task (/history')
                os.write(master, b'/history\n')
                read_until(b'History: ')
                os.write(master, b'1\n')
                detail = read_until(b'[r] resume')
                self.assertIn(b'Previous run', detail)
                os.write(master, b'r\n')
                self.assertIn(b'Session restored', read_until(b'Task (/history'))
                os.write(master, b'Follow-up\n')
                read_until(b'[n] next')
                os.write(master, b'\n')
                read_until(b'Task (/history')
                os.write(master, b'/quit\n')
                self.assertEqual(proc.wait(timeout=10), 0)
            prompts = [json.loads(line) for line in prompt_log.read_text().splitlines()]
            self.assertTrue(any('PREVIOUS TASK:\nPrevious run' in prompt for prompt in prompts))
        finally:
            os.close(master)
            if slave is not None:
                os.close(slave)

    def test_resume_refuses_changed_project(self):
        self.workflow()
        self.ccx('team', '--review-only', 'Previous run')
        (self.repo / 'external.txt').write_text('external change')
        master, slave = pty.openpty()
        try:
            with subprocess.Popen([str(CCX), 'team'], cwd=self.repo,
                                  env={**self.env, 'TERM': 'xterm'},
                                  stdin=slave, stdout=slave, stderr=slave) as proc:
                os.close(slave)
                slave = None
                def read_until(token):
                    data = b''
                    deadline = time.monotonic() + 12
                    while token not in data:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not select.select([master], [], [], remaining)[0]:
                            proc.kill()
                            self.fail(f'Missing {token!r}: {data[-1000:]!r}')
                        data += os.read(master, 65536)
                    return data
                read_until(b'Task (/history')
                os.write(master, b'/history\n')
                read_until(b'History: ')
                os.write(master, b'1\n')
                read_until(b'[r] resume')
                os.write(master, b'r\n')
                self.assertIn(b'project or its files changed', read_until(b'[n] next'))
                os.write(master, b'\n')
                read_until(b'Task (/history')
                os.write(master, b'/quit\n')
                self.assertEqual(proc.wait(timeout=10), 0)
            self.assertEqual((self.repo / 'external.txt').read_text(), 'external change')
        finally:
            os.close(master)
            if slave is not None:
                os.close(slave)

    def test_kimi_structured_response_with_prose(self):
        schema = workflow_module.schema({'plan': {'type': 'string'}})
        self.assertEqual(workflow_module.extract_json_response(
            'Here is the result:\n```json\n{"plan":"Add tests"}\n```', schema),
            {'plan': 'Add tests'})
        with self.assertRaises(workflow_module.WorkflowError):
            workflow_module.extract_json_response('No structured result', schema)
        with self.assertRaises(workflow_module.WorkflowError):
            workflow_module.extract_json_response('{"plan":"one"} {"plan":"two"}', schema)

    def test_native_reviewer_cannot_write_project(self):
        self.ccx('new', '--name', 'kimi-native', '--provider', 'kimi-cli', '--yes')
        self.workflow(planner='kimi-native', builder='claude', reviewer='kimi-native')
        result = self.ccx('team', 'Review fixture', ok=False,
                          env={**self.env, 'FAKE_CASE': 'mutate_review'})
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.repo / 'unexpected.py').exists())
        self.assertIn('Read-only file system', result.stdout + result.stderr)

    def test_codex_login_and_model_args(self):
        self.ccx('new','--name','native','--provider','codex','--model','chosen-model','--yes')
        auth = json.loads(self.ccx('native','login','--device-auth').stdout)
        self.assertEqual(auth['args'], ['login','--device-auth'])
        run = json.loads(self.ccx('native','--model','override','hello world').stdout)
        self.assertEqual(run['args'], ['--model','override','hello world'])
        default = json.loads(self.ccx('native','hello world').stdout)
        self.assertEqual(default['args'], ['--model','chosen-model','hello world'])
        self.assertIsNone(run['home'])
        self.assertIsNone(run['claude_home'])
        self.assertIn('CODEX_MODEL=chosen-model', self.ccx('show','native').stdout)
        self.assertNotIn('ANTHROPIC_MODEL', self.ccx('show','native').stdout)

    def test_codex_isolated_creation_login(self):
        run = self.ccx('new','--name','isolated','--provider','codex','--isolate','--device-auth','--yes')
        auth = json.loads(run.stdout.splitlines()[1])
        self.assertEqual(auth['args'], ['login','--device-auth'])
        self.assertEqual(auth['home'], str(self.root / 'config/profiles/isolated/home'))
        self.assertEqual((Path(auth['home']).stat().st_mode & 0o777), 0o700)

    def test_missing_codex_reports_install_hint(self):
        result = self.ccx('codex', ok=False, env={**self.env,'CCX_CODEX_BIN':'/missing/codex'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('codex not found', result.stderr)

    def test_workflow_both_directions(self):
        self.workflow()
        run = self.ccx('team','Implement fixture')
        self.assertIn('independently inspected', run.stdout)
        self.assertEqual(self.result()['status'], 'completed')
        self.assertTrue((self.repo / 'implemented.py').is_file())
        # Separate checkout for opposite host/builder direction.
        (self.repo / 'implemented.py').unlink()
        self.ccx('new','--name','reverse','--type','loop','--planner','codex',
                 '--builder','codex','--reviewer','claude','--proof','test -f implemented.py','--yes')
        self.ccx('reverse','Implement fixture')

    def test_review_only_and_discovery(self):
        self.workflow()
        self.assertIn('planner=claude builder=claude reviewer=codex', self.ccx('list').stdout)
        self.assertIn('CCX_TYPE=loop', self.ccx('show','team').stdout)
        self.ccx('team','--review-only','Review fixture')
        self.assertFalse((self.repo / 'implemented.py').exists())

    def test_stream_activity_visible_before_process_completion(self):
        self.workflow()
        with subprocess.Popen([str(CCX),'team','--review-only','Review fixture'], cwd=self.repo,
                              env={**self.env,'FAKE_CASE':'stream'}, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            import selectors
            selector = selectors.DefaultSelector()
            self.addCleanup(selector.close)
            selector.register(process.stdout, selectors.EVENT_READ)
            seen = ''
            deadline = time.monotonic() + 10
            while 'Read security.py' not in seen:
                self.assertGreater(deadline - time.monotonic(), 0, seen)
                if selector.select(timeout=0.1):
                    seen += os.read(process.stdout.fileno(), 65536).decode()
            self.assertIsNone(process.poll(), 'Activity must be visible while the CLI is still running')
            out, err = process.communicate(timeout=10)
            self.assertEqual(process.returncode,0,seen+out+err)
            self.assertNotIn('PRIVATE_REASONING_DO_NOT_DISPLAY',seen+out)
            self.assertIn('APPROVED:',out)
            self.assertIn('item.started: rg security',out)
        raw = next((self.root / 'config/runs').glob('team-*/01-plan/stdout.txt')).read_text()
        self.assertIn('PRIVATE_REASONING_DO_NOT_DISPLAY',raw)

    def test_info_findings_accepted_and_invalid_reviews_explained(self):
        self.workflow()
        run = self.ccx('team','--review-only','Review fixture',env={**self.env,'FAKE_CASE':'info_review'})
        self.assertIn('[info] R1 plan: Informational note',run.stdout)
        for case, message in (('unknown_severity',"Invalid severity 'surprise' for finding R1"),
                              ('duplicate_ids','Duplicate finding ID: R1')):
            with self.subTest(case=case):
                run = self.ccx('team','--review-only','Review fixture',ok=False,
                               env={**self.env,'FAKE_CASE':case})
                self.assertNotEqual(run.returncode,0)
                self.assertIn(message,run.stderr)

    def commit_fixture(self):
        subprocess.run(['git', 'add', '.'], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-qm', 'verification fixture'], cwd=self.repo, check=True, capture_output=True)

    def test_auto_verification_runs_tests_and_records_effective_command(self):
        tests = self.repo / 'tests'
        tests.mkdir()
        (tests / 'test_result.py').write_text(
            'import unittest\nfrom pathlib import Path\n'
            'class Result(unittest.TestCase):\n'
            '    def test_result(self):\n'
            '        self.assertEqual(Path("implemented.py").read_text(), "print(42)\\n")\n')
        self.commit_fixture()
        self.workflow(proof='')
        run = self.ccx('team', 'Implement fixture')
        self.assertIn('python3 -m unittest discover -s tests [auto]', run.stdout)
        self.assertEqual(self.result()['status'], 'completed')
        proof = json.loads(next((self.root / 'config/runs').glob('team-*/proof-0.json')).read_text())
        self.assertEqual(proof['exit_code'], 0)
        self.assertEqual(proof['command'], 'python3 -m unittest discover -s tests')

    def test_missing_auto_verification_fails_before_models_but_review_only_works(self):
        self.workflow(proof='')
        run = self.ccx('team', 'Implement fixture', ok=False)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('--proof', run.stderr)
        self.assertFalse((self.root / 'config/runs').exists())
        self.ccx('team', '--review-only', 'Review fixture')
        self.assertFalse((self.repo / 'implemented.py').exists())

    def test_runtime_proof_overrides_saved_command(self):
        self.workflow(proof='exit 99')
        self.ccx('team', '--proof', 'test -f implemented.py', 'Implement fixture')
        self.assertEqual(self.result()['status'], 'completed')

    def test_detect_node_scripts_package_manager_and_other_projects(self):
        package = self.repo / 'package.json'
        package.write_text(json.dumps({'scripts': {'test': 'echo "Error: no test specified" && exit 1',
                                                 'build': 'vite build'}}))
        (self.repo / 'pnpm-lock.yaml').touch()
        self.assertEqual(workflow_module.detect_proof(self.repo), 'pnpm run build')
        package.write_text(json.dumps({'packageManager': 'bun@1.3.0', 'scripts': {'test': 'vitest run'}}))
        self.assertEqual(workflow_module.detect_proof(self.repo), 'bun run test')
        package.unlink()
        (self.repo / 'Cargo.toml').touch()
        self.assertEqual(workflow_module.detect_proof(self.repo), 'cargo test')
        (self.repo / 'Cargo.toml').unlink()
        (self.repo / 'go.mod').touch()
        self.assertEqual(workflow_module.detect_proof(self.repo), 'go test ./...')
        (self.repo / 'go.mod').unlink()
        (self.repo / 'Makefile').write_text('test:\n\tpython3 check.py\n')
        self.assertEqual(workflow_module.detect_proof(self.repo), 'make test')
        (self.repo / 'Makefile').unlink()
        (self.repo / 'pytest.ini').write_text('[pytest]\n')
        self.assertEqual(workflow_module.detect_proof(self.repo), 'python3 -m pytest')

    def interactive_new(self, name, keys):
        master, slave = pty.openpty()
        try:
            with subprocess.Popen([str(CCX),'new','--name',name],cwd=self.repo,env=self.env,
                                  stdin=slave,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) as proc:
                deadline = time.monotonic() + 5
                while termios.tcgetattr(slave)[3] & termios.ICANON:
                    if time.monotonic() > deadline:
                        proc.kill()
                        self.fail('Profile-type menu did not enable arrow-key input')
                    time.sleep(0.01)
                # The first menu chooses loop; select default orchestrator,
                # model and effort before the existing per-role inputs.
                first = keys.index(b'\r') + 1
                os.write(master, keys[:first] + b'\r' * 3 + keys[first:])
                out, err = proc.communicate(timeout=10)
                return proc.returncode,out,err
        finally:
            os.close(master)
            os.close(slave)

    def test_new_interactive_workflow_wizard(self):
        rc,out,err = self.interactive_new('wizard',b'\x1b[B\x1b[A\x1b[B\r' + b'\r' * 9)
        self.assertEqual(rc,0,out+err)
        self.assertIn('Created workflow "wizard"',out)
        self.assertIn('Choose profile type',out)
        self.assertIn('↑↓ move',out)
        self.assertIn('Choose orchestrator',out)
        self.assertIn('Choose planner',out)
        self.assertIn('Choose builder',out)
        self.assertIn('Choose checker (reviewer)',out)
        self.assertNotIn('Planner profile:',out)
        self.assertNotIn('1) Single model',out)
        self.assertNotIn('Verification command (runs locally',out)
        self.assertIn('automatically detected',out)
        preset = self.ccx('show','wizard').stdout
        self.assertIn('CCX_BUILDER=claude',preset)
        self.assertIn('CCX_REVIEWER=claude',preset)
        self.assertIn('Choose planner effort', out)
        self.ccx('wizard','--review-only','fixture')

    def test_role_menu_navigation_and_builder_default(self):
        self.workflow()  # Saved workflows must not appear as model choices.
        rc,out,err = self.interactive_new('reverse-wizard',b'\x1b[B\r\x1b[B\r' + b'\r' * 8)
        self.assertEqual(rc,0,out+err)
        preset = self.ccx('show','reverse-wizard').stdout
        self.assertIn('CCX_PLANNER=codex',preset)
        self.assertIn('CCX_BUILDER=codex',preset)
        self.assertIn('CCX_REVIEWER=claude',preset)

    def test_effort_menus_arrow_navigation(self):
        rc,out,err = self.interactive_new('effort-wizard',
            b'\x1b[B\r\r\r'  # loop, planner profile and default model
            b'\x1b[B\x1b[B\x1b[B\r'  # planner high
            b'\r\r\x1b[B\x1b[B\r'  # builder profile, model, medium
            b'\r\r\x1b[B\r')  # reviewer profile, model, low
        self.assertEqual(rc,0,out+err)
        preset = self.ccx('show','effort-wizard').stdout
        for role, effort in (('PLANNER','high'),('BUILDER','medium'),('REVIEWER','low')):
            self.assertIn(f'CCX_{role}_EFFORT={effort}',preset)

    def test_invalid_effort_rejected_before_preset_created(self):
        result = self.ccx('new','--name','bad-effort','--type','loop',
                          '--planner','claude','--reviewer','claude',
                          '--planner-effort','ultra','--yes',ok=False)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.root / 'config/profiles/bad-effort').exists())

    def test_oauth_wizard_profile_model_effort_order_and_runtime_overrides(self):
        home = self.root / 'codex home'
        home.mkdir()
        (home / 'models_cache.json').write_text(json.dumps({'models': [
            {'slug': 'gpt-6.1-sol', 'visibility': 'list',
             'supported_reasoning_levels': [{'effort':'high'}, {'effort':'max'}]},
            {'slug':'hidden-model', 'visibility':'hide'}]}))
        self.env['CODEX_HOME'] = str(home)
        keys = (b'\x1b[B\r'  # loop
                b'\r\x1b[B\r\r'  # planner Claude, Sonnet, default effort
                b'\x1b[B\r\x1b[B\r\x1b[B\r'  # builder Codex, cached model, high
                b'\r\x1b[B\x1b[B\x1b[B\r\r')  # reviewer Claude, Haiku, default
        rc,out,err = self.interactive_new('model-wizard', keys)
        self.assertEqual(rc,0,out+err)
        preset = self.ccx('show','model-wizard').stdout
        self.assertIn('CCX_PLANNER_MODEL=sonnet',preset)
        self.assertIn('CCX_BUILDER_MODEL=gpt-6.1-sol',preset)
        self.assertIn('CCX_BUILDER_EFFORT=high',preset)
        self.assertIn('CCX_REVIEWER_MODEL=haiku',preset)
        self.assertNotIn('hidden-model',out)
        titles = ['Choose planner:', 'Choose planner model', 'Choose planner effort',
                  'Choose builder:', 'Choose builder model', 'Choose builder effort',
                  'Choose checker (reviewer):', 'Choose reviewer model', 'Choose reviewer effort']
        positions = [out.index(title) for title in titles]
        self.assertEqual(positions, sorted(positions))
        log = self.root / 'args.jsonl'
        self.ccx('model-wizard','--proof','test -f implemented.py','Implement fixture',
                 env={**self.env,'FAKE_ARGS_LOG':str(log)})
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        turns = [args for args in calls if '-p' in args or 'exec' in args]
        self.assertNotIn('--model', turns[0])  # Orchestrator uses the CLI default.
        self.assertEqual([args[args.index('--model')+1] for args in turns[1:]],
                         ['sonnet','haiku','gpt-6.1-sol','haiku'])
        self.assertIn('ANTHROPIC_MODEL=opusplan',self.ccx('show','claude').stdout)
        self.assertNotIn('CODEX_MODEL=gpt-6.1-sol',self.ccx('show','codex').stdout)

    def test_custom_role_models_and_default_backwards_compatibility(self):
        self.ccx('new','--name','team','--type','loop','--planner','codex',
                 '--reviewer','codex','--planner-model','custom-model',
                 '--reviewer-model','custom-reviewer','--yes')
        log = self.root / 'args.jsonl'
        self.ccx('team','--proof','test -f implemented.py','Implement fixture',
                 env={**self.env,'FAKE_ARGS_LOG':str(log)})
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        turns = [args for args in calls if 'exec' in args]
        self.assertIn('custom-model', turns[0])
        self.assertIn('custom-reviewer', turns[1])
        self.assertNotIn('--model', turns[2])  # Builder still follows its profile.

    def test_role_menu_cancellation_creates_no_preset(self):
        rc,out,err = self.interactive_new('cancelled',b'\x1b[B\r\r\r\rq')
        self.assertNotEqual(rc,0)
        self.assertIn('Choose builder',out)
        self.assertIn('profile creation cancelled',err)
        self.assertFalse((self.root / 'config/profiles/cancelled').exists())

    def test_checker_menu_allows_same_model_profile(self):
        (self.root / 'config/profiles/codex/profile.env').unlink()
        rc,out,err = self.interactive_new('same-model',b'\x1b[B\r' + b'\r' * 9)
        self.assertEqual(rc,0,out+err)
        self.assertIn('CCX_REVIEWER=claude',self.ccx('show','same-model').stdout)

    def test_checker_menu_includes_equivalent_model_alias(self):
        self.ccx('new','--name','zalias','--provider','claude','--yes')
        rc,out,err = self.interactive_new('filtered',b'\x1b[B\r' + b'\r' * 9)
        self.assertEqual(rc,0,out+err)
        checker_menu = out.split('Choose checker (reviewer)',1)[1]
        self.assertIn('zalias',checker_menu)
        self.assertIn('CCX_REVIEWER=claude',self.ccx('show','filtered').stdout)

    def test_review_budget_exhaustion_never_builds(self):
        self.workflow()
        run = self.ccx('team','Implement fixture',ok=False,env={**self.env,'FAKE_CASE':'revise'})
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('budget exhausted', run.stderr)
        self.assertFalse((self.repo / 'implemented.py').exists())
        self.assertEqual(self.result()['status'], 'failed')

    def test_empty_failed_and_invalid_reviews_fail_closed(self):
        self.workflow()
        for case in ('empty','failed_turn','invalid_review'):
            with self.subTest(case=case):
                run = self.ccx('team','Implement fixture',ok=False,env={**self.env,'FAKE_CASE':case})
                self.assertNotEqual(run.returncode, 0)
                self.assertFalse((self.repo / 'implemented.py').exists())

    def test_review_mutation_invalidates_result(self):
        self.workflow()
        run = self.ccx('team','Implement fixture',ok=False,env={**self.env,'FAKE_CASE':'mutate_review'})
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('changed during review', run.stderr)
        self.assertFalse((self.repo / 'implemented.py').exists())

    def test_failed_proof_never_inspects_or_reports_success(self):
        self.workflow(proof='exit 9')
        run = self.ccx('team','Implement fixture',ok=False)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('Verification failed', run.stderr)
        self.assertNotIn('ccx: inspect', run.stdout)
        self.assertEqual(self.result()['status'], 'failed')
        self.assertTrue((self.repo / 'implemented.py').exists())

    def test_inspection_revision_rebuilds_and_rechecks(self):
        self.workflow()
        self.ccx('team','Implement fixture',env={**self.env,'FAKE_CASE':'inspect_revise'})
        self.assertEqual(self.result()['status'], 'completed')
        run_dir = next((self.root / 'config/runs').glob('team-*'))
        self.assertEqual(len(list(run_dir.glob('*-build'))), 2)
        self.assertEqual(len(list(run_dir.glob('proof-*.json'))), 2)

    def test_builder_must_also_agree_before_completion(self):
        self.workflow()
        self.ccx('team','Implement fixture',env={**self.env,'FAKE_CASE':'builder_needs_work'})
        run_dir = next((self.root / 'config/runs').glob('team-*'))
        rounds = json.loads((run_dir / 'consensus-rounds.json').read_text())
        self.assertEqual(len(rounds),2)
        self.assertEqual(rounds[0]['builder']['status'],'NEEDS_WORK')
        self.assertEqual(rounds[0]['checker']['verdict'],'APPROVED')
        consensus = json.loads((run_dir / 'consensus.json').read_text())
        self.assertEqual(consensus['builder']['status'],'READY')
        self.assertEqual(consensus['checker']['verdict'],'APPROVED')

    def test_disputed_finding_goes_back_to_checker_with_evidence(self):
        self.workflow()
        self.ccx('team','Implement fixture',env={**self.env,'FAKE_CASE':'disputed'})
        run_dir = next((self.root / 'config/runs').glob('team-*'))
        consensus = json.loads((run_dir / 'consensus.json').read_text())
        self.assertEqual(consensus['builder']['responses'][0]['disposition'],'DISPUTED')
        self.assertEqual(consensus['checker']['verdict'],'APPROVED')
        last_inspect = sorted(run_dir.glob('*-inspect'))[-1]
        self.assertIn('DISPUTED',(last_inspect / 'prompt.txt').read_text())

    def test_builder_cannot_ignore_findings(self):
        self.workflow()
        result = self.ccx('team','fixture',ok=False,env={**self.env,'FAKE_CASE':'missing_response'})
        self.assertNotEqual(result.returncode,0)
        self.assertIn('respond to every finding',result.stderr)

    def test_failed_proof_is_returned_to_builder_for_repair(self):
        self.workflow(proof='test -f repaired.py')
        self.ccx('team','fixture',env={**self.env,'FAKE_CASE':'repair_proof'})
        run_dir = next((self.root / 'config/runs').glob('team-*'))
        rounds = json.loads((run_dir / 'consensus-rounds.json').read_text())
        self.assertNotEqual(rounds[0]['proof']['exit_code'],0)
        self.assertIsNone(rounds[0]['checker'])
        self.assertEqual(rounds[1]['proof']['exit_code'],0)
        self.assertEqual(self.result()['status'],'completed')

    def test_proof_mutation_requires_builder_to_reaffirm_code(self):
        self.workflow(proof='test -f generated.txt || echo generated > generated.txt')
        self.ccx('team','fixture')
        run_dir = next((self.root / 'config/runs').glob('team-*'))
        rounds = json.loads((run_dir / 'consensus-rounds.json').read_text())
        self.assertEqual(len(rounds),2)
        self.assertTrue(rounds[0]['proof_changed_checkout'])
        self.assertFalse(rounds[1]['proof_changed_checkout'])

    def test_consensus_budget_override_stops_disagreement(self):
        self.workflow()
        result = self.ccx('team','--fix-rounds','1','fixture',ok=False,
                          env={**self.env,'FAKE_CASE':'always_revise'})
        self.assertNotEqual(result.returncode,0)
        self.assertIn('consensus budget exhausted',result.stderr)
        run_dir = next((self.root / 'config/runs').glob('team-*'))
        self.assertEqual(len(list(run_dir.glob('*-build'))),2)
        self.assertFalse((run_dir / 'consensus.json').exists())

    def test_blocked_builder_stops_immediately(self):
        self.workflow()
        result = self.ccx('team','fixture',ok=False,env={**self.env,'FAKE_CASE':'builder_blocked'})
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Builder blocked',result.stderr)
        self.assertNotIn('running verification',result.stdout)

    def test_code_changed_during_inspection_invalidates_approval(self):
        self.workflow()
        run = self.ccx('team','Implement fixture',ok=False,env={**self.env,'FAKE_CASE':'mutate_inspection'})
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('changed during review', run.stderr)
        self.assertEqual(self.result()['status'], 'failed')

    def test_plan_changed_after_review_never_builds(self):
        self.workflow()
        path = self.root / 'config/profiles/team/workflow.env'
        config = workflow_module.read_env(path)
        run_dir = self.root / 'artifacts'
        run_dir.mkdir()
        profiles = {name:workflow_module.read_env(self.root / 'config/profiles' / name / 'profile.env')
                    for name in ('claude','codex')}
        loop = workflow_module.Loop(str(CCX),config,self.repo,run_dir,profiles)
        base = subprocess.check_output(['git','rev-parse','HEAD'],cwd=self.repo).decode().strip()
        def fake_call(role, phase, prompt, output_schema=None):
            if role == 'planner': return {'plan':'Approved plan'}
            self.fail('Builder should not run after plan mutation')
        def fake_review(phase, plan, base):
            approval = {'plan_sha256':workflow_module.sha(plan.encode()),
                        'snapshot':workflow_module.snapshot(self.repo,base)}
            workflow_module.save(run_dir / 'review-approval.json',approval)
            (run_dir / 'PLAN.md').write_text('Unreviewed plan')
            return {'verdict':'APPROVED'}
        loop.call, loop.review = fake_call, fake_review
        with self.assertRaisesRegex(workflow_module.WorkflowError,'Plan changed after approval'):
            loop.run('fixture',False)

    def test_timeout_terminates_process(self):
        with self.assertRaisesRegex(workflow_module.WorkflowError,'timed out'):
            workflow_module.execute(['python3','-c','import time; time.sleep(30)'],self.repo,'',0.1,
                                    self.root / 'out',self.root / 'err')

    def test_snapshot_tracks_staged_deleted_and_untracked_files(self):
        subprocess.run(['git','commit','--allow-empty','-qm','base'],cwd=self.repo,check=True)
        base = subprocess.check_output(['git','rev-parse','HEAD'],cwd=self.repo).decode().strip()
        (self.repo / 'staged.py').write_text('staged')
        subprocess.run(['git','add','staged.py'],cwd=self.repo,check=True)
        (self.repo / 'untracked.py').write_text('untracked')
        before = workflow_module.snapshot(self.repo,base)
        self.assertEqual({f['path'] for f in before['files']},{'staged.py','untracked.py'})
        (self.repo / 'staged.py').unlink()
        after = workflow_module.snapshot(self.repo,base)
        self.assertNotEqual(before['sha256'],after['sha256'])
        self.assertIn({'path':'staged.py','kind':'deleted','sha256':workflow_module.sha(b'')},after['files'])

    def test_dirty_checkout_preserved(self):
        self.workflow()
        (self.repo / 'mine.txt').write_text('my work')
        run = self.ccx('team','Implement fixture',ok=False)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('clean Git checkout', run.stderr)
        self.assertEqual((self.repo / 'mine.txt').read_text(), 'my work')

    def test_same_model_aliases_allowed(self):
        self.ccx('new','--name','alias','--provider','claude','--yes')
        self.workflow(reviewer='alias')
        self.ccx('team','Implement fixture')
        self.assertEqual(self.result()['status'], 'completed')

    def test_per_role_effort_reaches_cli_and_same_profile_works(self):
        self.ccx('new','--name','team','--type','loop','--planner','codex',
                 '--builder','codex','--reviewer','codex','--planner-effort','high',
                 '--builder-effort','medium','--reviewer-effort','low',
                 '--proof','test -f implemented.py','--yes')
        log = self.root / 'args.jsonl'
        self.ccx('team','Implement fixture',env={**self.env, 'FAKE_ARGS_LOG': str(log)})
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        exec_calls = [args for args in calls if 'exec' in args]
        for args, effort in zip(exec_calls, ('high', 'low', 'medium', 'low')):
            self.assertIn(f'model_reasoning_effort="{effort}"', args)
        self.assertEqual(len(exec_calls), 4)
        self.assertEqual(self.result()['status'], 'completed')

    def test_claude_effort_reaches_cli(self):
        self.ccx('new','--name','team','--type','loop','--planner','claude',
                 '--reviewer','claude','--planner-effort','high','--builder-effort','medium',
                 '--reviewer-effort','low','--proof','test -f implemented.py','--yes')
        log = self.root / 'args.jsonl'
        self.ccx('team','Implement fixture',env={**self.env, 'FAKE_ARGS_LOG': str(log)})
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual([args[args.index('--effort')+1] for args in calls], ['high','low','medium','low'])

    def test_missing_role_and_task_rejected(self):
        run = self.ccx('new','--name','bad','--type','loop','--planner','claude',
                       '--reviewer','missing','--proof','true','--yes',ok=False)
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse((self.root / 'config/profiles/bad').exists())
        self.workflow()
        self.assertNotEqual(self.ccx('team',ok=False).returncode, 0)

    def test_symlink_install_resolves_runtime(self):
        self.workflow()
        link = self.root / 'installed ccx'
        link.symlink_to(CCX)
        run = subprocess.run([str(link),'team','--review-only','fixture'],cwd=self.repo,
                             env=self.env,capture_output=True,text=True,timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_relative_config_home_from_project_subdirectory(self):
        self.workflow()
        subdir = self.repo / 'nested'
        subdir.mkdir()
        run = subprocess.run([str(CCX),'team','--review-only','fixture'],cwd=subdir,
                             env={**self.env,'CCX_HOME':'../../config'},
                             capture_output=True,text=True,timeout=20)
        self.assertEqual(run.returncode,0,run.stderr)

    def test_non_git_directory_runs_in_degraded_mode(self):
        self.workflow()
        outside = self.root / 'not a repo'
        outside.mkdir()
        args_log = self.root / 'fake-args.log'
        run = subprocess.run([str(CCX),'team','fixture'],cwd=outside,
                             env={**self.env,'FAKE_ARGS_LOG':str(args_log)},
                             capture_output=True,text=True,timeout=60)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        self.assertTrue(args_log.exists(),
                        'the agent must still run outside a git repository')
        self.assertIn('no git repository',run.stdout)
        self.assertTrue((outside / 'implemented.py').exists(),
                        'the build must land in the workspace')


if __name__ == '__main__':
    unittest.main()
