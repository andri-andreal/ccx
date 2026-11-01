"""Exercise real ccx dispatch with disposable profiles/repos and fake CLIs."""
import json
import importlib.util
import io
import os
import pty
import re
import select
import stat
from pathlib import Path
import subprocess
import tempfile
import threading
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
def strict_problem(node):
    # OpenAI strict structured outputs: every object lists all of its properties as required.
    if isinstance(node, dict):
        if node.get('type') == 'object' and 'properties' in node:
            missing = set(node['properties']) - set(node.get('required', []))
            if missing:
                return 'Missing ' + ', '.join(sorted(missing))
        for child in node.values():
            problem = strict_problem(child)
            if problem:
                return problem
    elif isinstance(node, list):
        for child in node:
            problem = strict_problem(child)
            if problem:
                return problem
    return None
if schema and codex and strict_problem(schema):
    print(json.dumps({'type':'turn.failed','error':{'message':'invalid_json_schema: ' + strict_problem(schema)}}))
    sys.exit(1)
if schema and 'route' in schema['properties']:
    value = {'route':os.environ.get('FAKE_ROUTE', 'plan'), 'reason':'Fixture routing.'}
    if os.environ.get('FAKE_QUESTIONS'):
        value['questions'] = json.loads(os.environ['FAKE_QUESTIONS'])
elif schema and 'plan' in schema['properties']:
    value = {'plan':'Create implemented.py; verify with the configured command.'}
    if case == 'planner_questions' and 'add up to three questions' in prompt:
        value['questions'] = [{'question':'Which status values?','options':['active/inactive','active/suspended/left']}]
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
elif schema and case == 'bad_review_json' and 'YOUR PREVIOUS REPLY WAS NOT VALID JSON' not in prompt and mcode:
    print(json.dumps({'type':'turn.failed','error':{'code':'STRUCTURED_OUTPUT_INVALID','retryable':True}}))
    print(json.dumps({'type':'exec.completed','result':{'status':'failed','error':{'code':'STRUCTURED_OUTPUT_INVALID'}}}))
    sys.exit(4)
elif schema:
    value = {'verdict':'APPROVED','summary':'Reviewed fixture.', 'findings':[],
             'coverage':['implemented.py'], 'limitations':[]}
    if case == 'revise_new_medium' and 'CHANGE MANIFEST' not in prompt:
        if 'PREVIOUS FINDINGS' in prompt:
            value.update(verdict='REVISE',findings=[{'id':'N1','severity':'medium','path':'plan',
                'evidence':'A brand-new nit','fix':'Optional polish'}])
        else:
            value.update(verdict='REVISE',findings=[{'id':'R1','severity':'high','path':'plan',
                'evidence':'Missing case','fix':'Add it'}])
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
    if 'Inspect the repository' in prompt:
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
    if case == 'curl_build' and schema and 'status' in schema['properties']:
        command = "bash -lc 'curl -s -w \"\\n%{http_code}\" http://localhost:8080/health'"
        print(json.dumps({'type':'item.started','item':{'type':'command_execution','command':command}}))
        print(json.dumps({'type':'item.completed','item':{'type':'command_execution','command':command,
                                                           'aggregated_output':'{"ok":true}\n200','exit_code':0}}))
    print(json.dumps({'type':'turn.failed' if case == 'failed_turn' else 'turn.completed'}))
else:
    print(json.dumps({'type':'result','subtype':'success','session_id':session,
        'structured_output':value if schema else None,'result':value if not schema else '', 'is_error':False}))
'''



class KillOnErrorPopen(subprocess.Popen):
    """A failed assertion inside `with` kills the child instead of waiting on it forever."""
    def __exit__(self, exc_type, value, traceback):
        if exc_type is not None and self.poll() is None:
            self.kill()
        return super().__exit__(exc_type, value, traceback)

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

    def orchestrator_team(self, name='team-orch'):
        """A loop team whose orchestrator runs first and can ask the user."""
        return self.ccx('new','--name',name,'--type','loop','--orchestrator','claude',
                        '--planner','claude','--builder','claude','--reviewer','codex',
                        '--proof','test -f implemented.py','--yes')

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

    def test_role_definitions_layer_project_over_user_over_builtin(self):
        home = self.root / 'config'
        (home / 'agents').mkdir(parents=True, exist_ok=True)
        (home / 'agents' / 'reviewer.md').write_text('---\nname: my-reviewer\nskills: [testing, nope]\neffort: turbo\n---\nMine.\n')
        project = self.repo / '.ccx' / 'agents'
        project.mkdir(parents=True)
        (project / 'planner.md').write_text('---\nname: go-architect  # comment\nskills: [go-api]\nmodel: gpt-x\neffort: high\n---\nPlan Go.\n')
        (self.repo / '.ccx' / 'skills').mkdir()
        (self.repo / '.ccx' / 'skills' / 'go-api.md').write_text('---\nname: go-api\n---\nUse net/http.\n')
        with mock.patch.dict(os.environ, {'CCX_HOME': str(home)}):
            agents, warnings = workflow_module.load_agents(self.repo)
            plain, _ = workflow_module.load_agents(self.repo, use_project=False)
        self.assertEqual((agents['planner']['name'], agents['planner']['layer']), ('go-architect', 'project'))
        self.assertEqual((agents['planner']['model'], agents['planner']['effort']), ('gpt-x', 'high'))
        self.assertEqual([skill['name'] for skill in agents['planner']['skills']], ['go-api'])
        self.assertEqual((agents['reviewer']['name'], agents['reviewer']['layer']), ('my-reviewer', 'user'))
        self.assertEqual([skill['layer'] for skill in agents['reviewer']['skills']], ['builtin'])
        self.assertEqual(agents['builder']['layer'], 'builtin')
        self.assertIn("reviewer: skill 'nope' not found", warnings)
        self.assertIn("reviewer: ignored invalid effort 'turbo'", warnings)
        self.assertEqual(plain['planner']['layer'], 'builtin')
        guidance = workflow_module.role_guidance(agents['planner'])
        self.assertIn('ROLE GUIDANCE (go-architect, project definition)', guidance)
        self.assertIn('take precedence', guidance)
        self.assertIn('SKILL go-api:\nUse net/http.', guidance)

    def test_role_guidance_reaches_prompts_and_project_files_need_trust(self):
        self.workflow()
        log = self.root / 'role-prompts.jsonl'
        args_log = self.root / 'role-args.jsonl'
        env = {**self.env, 'FAKE_PROMPT_LOG': str(log), 'FAKE_ARGS_LOG': str(args_log)}
        self.ccx('team', 'Implement fixture', env=env)
        prompts = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertTrue(any('ROLE GUIDANCE (planner, builtin definition)' in p for p in prompts))
        self.assertTrue(any('ROLE GUIDANCE (builder, builtin definition)' in p and 'SKILL testing:' in p for p in prompts))
        self.assertTrue(all(p.startswith('PROJECT DIRECTORY: ') for p in prompts))
        run_dir = sorted((self.root / 'config' / 'runs').glob('team-*/agents.json'))[-1]
        recorded = json.loads(run_dir.read_text())
        self.assertEqual(recorded['reviewer']['layer'], 'builtin')
        self.assertTrue(recorded['reviewer']['sha256'])
        self.assertNotIn('body', recorded['reviewer'])
        # A project definition is ignored until trusted.
        (self.repo / '.ccx' / 'agents').mkdir(parents=True)
        (self.repo / '.ccx' / 'agents' / 'planner.md').write_text(
            '---\nname: go-architect\neffort: high\n---\nPROJECT PLANNER RULE\n')
        git_env = {**env}
        subprocess.run(['git', 'add', '-A'], cwd=self.repo, check=True)
        subprocess.run(['git', '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'agents'], cwd=self.repo, check=True)
        log.unlink()
        run = self.ccx('team', 'Implement fixture', env=git_env)
        self.assertIn('project agents in .ccx/ are not trusted yet', run.stdout)
        self.assertFalse(any('PROJECT PLANNER RULE' in p for p in map(json.loads, log.read_text().splitlines())))
        # ccx agents init trusts the project files as they are now.
        subprocess.run(['git', 'checkout', '-q', '--', '.'], cwd=self.repo, check=True)
        (self.repo / 'implemented.py').unlink(missing_ok=True)
        listing = self.ccx('agents', 'list', env=git_env)
        self.assertIn('go-architect  [project]', listing.stdout)
        self.assertIn('not trusted yet', listing.stdout)
        self.ccx('agents', 'init', env=git_env)
        subprocess.run(['git', 'add', '-A'], cwd=self.repo, check=True)
        subprocess.run(['git', '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'init'], cwd=self.repo, check=True)
        log.unlink()
        args_log.unlink()
        self.ccx('team', 'Implement fixture', env=git_env)
        prompts = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertTrue(any('PROJECT PLANNER RULE' in p and 'go-architect, project definition' in p for p in prompts))
        # The agent's effort applies because the workflow left it at default.
        self.assertTrue(any('--effort' in a and a[a.index('--effort') + 1] == 'high'
                            for a in map(json.loads, args_log.read_text().splitlines())))
        self.assertIn('trusted', self.ccx('agents', 'list', env=git_env).stdout)
        self.assertIn('PROJECT PLANNER RULE', self.ccx('agents', 'show', 'planner', env=git_env).stdout)

    def test_roles_view_lists_each_role_and_opens_its_definition(self):
        self.workflow()
        master, slave = pty.openpty()
        try:
            with KillOnErrorPopen([str(CCX), 'team'], cwd=self.repo, env={**self.env, 'TERM': 'xterm'},
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
                try:
                    read_until(b'Describe a task')
                    os.write(master, b'/roles\n')
                    picker = read_until(b'q cancel')
                    for label in (b'ORCH', b'PLAN', b'EXEC', b'REVIEW', b'builtin'):
                        self.assertIn(label, picker)
                    os.write(master, b'4')
                    read_until(b'q cancel')
                    os.write(master, b'\r')
                    detail = read_until(b'Enter select')
                    self.assertIn(b'ROLE', detail)
                    self.assertIn(b'reviewer', detail)
                    self.assertIn(b'Edit (project copy', detail)
                    os.write(master, b'\x1b')
                    read_until(b'q cancel')
                    os.write(master, b'q')
                    read_until(b'Describe a task')
                    os.write(master, b'/quit\n')
                except BaseException:
                    proc.kill()
                    raise
                self.assertEqual(proc.wait(timeout=10), 0)
        finally:
            os.close(master)
            if slave is not None:
                os.close(slave)

    def test_agent_names_label_the_rail_and_flow_boxes(self):
        config = {'CCX_ORCHESTRATOR': 'a', 'CCX_PLANNER': 'b', 'CCX_BUILDER': 'c', 'CCX_REVIEWER': 'd'}
        ui = workflow_module.TeamUI(config, '/tmp/run')
        ui.color = False
        ui.on_event({'type': 'agents_loaded', 'agents': {'orchestrate': 'orchestrator', 'plan': 'go-architect',
                                                         'build': 'builder', 'review': 'reviewer'}})
        self.assertEqual(ui.custom_agent('plan'), 'go-architect')
        self.assertEqual(ui.custom_agent('build'), '')
        self.assertIn('PLAN (go-architect)', ui._agent_block(ui.panels['plan'], 60)[0])
        rows = ui.flow_rows(36)
        self.assertIn('┌ go-archit', rows[0])
        self.assertIn('┌──────', rows[6])

    def test_http_exchanges_parse_curl_httpie_and_mask_secrets(self):
        command = ("bash -lc 'curl -si -X POST http://localhost:8080/employees?token=abc "
                   "-H \"Content-Type: application/json\" -H \"Authorization: Bearer abc\" "
                   "-d \"{\\\"name\\\":\\\"Ana\\\",\\\"password\\\":\\\"hunter2\\\"}\"'")
        output = ('HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 201 Created\r\nContent-Type: application/json\r\n'
                  'Set-Cookie: session=xyz\r\n\r\n{"id":42,"name":"Ana"}')
        [exchange] = workflow_module.http_exchanges(command, output, 14, 0)
        self.assertEqual((exchange['method'], exchange['status'], exchange['reason']), ('POST', 201, 'Created'))
        self.assertEqual(exchange['url'], 'http://localhost:8080/employees?token=••••••')
        self.assertIn(['Authorization', '••••••'], exchange['request_headers'])
        self.assertIn(['Set-Cookie', '••••••'], exchange['response_headers'])
        self.assertEqual(json.loads(exchange['request_body']), {'name': 'Ana', 'password': '••••••'})
        self.assertEqual(json.loads(exchange['response_body']), {'id': 42, 'name': 'Ana'})
        self.assertNotIn('hunter2', json.dumps(exchange))
        # -w "%{http_code}" puts the status on the last line.
        [written] = workflow_module.http_exchanges("curl -s -w '\\n%{http_code}' localhost:8080/e/9",
                                                   '{"error":"not found"}\n404')
        self.assertEqual((written['method'], written['status'], written['response_body']),
                         ('GET', 404, '{"error": "not found"}'))
        # -u passwords, HTTPie items and pipelines.
        [basic] = workflow_module.http_exchanges('curl -u ana:secret -d x=1 http://h/x | jq .', None)
        self.assertEqual(basic['method'], 'POST')
        self.assertIn(['Authorization', 'Basic ana:••••••'], basic['request_headers'])
        [httpie] = workflow_module.http_exchanges('http PUT :8080/e/1 name=Ana age:=30 X-Api-Key:k', None)
        self.assertEqual((httpie['method'], httpie['url']), ('PUT', 'http://localhost:8080/e/1'))
        self.assertEqual(json.loads(httpie['request_body']), {'name': 'Ana', 'age': 30})
        self.assertIn(['X-Api-Key', '••••••'], httpie['request_headers'])
        self.assertEqual(workflow_module.http_exchanges('go test ./... && echo http is fine', 'ok'), [])

    def test_request_cards_and_detail_pane(self):
        config = {'CCX_ORCHESTRATOR': 'a', 'CCX_PLANNER': 'b', 'CCX_BUILDER': 'c', 'CCX_REVIEWER': 'd'}
        ui = workflow_module.TeamUI(config, '/tmp/run')
        ui.color = False
        [exchange] = workflow_module.http_exchanges(
            "curl -si -X POST http://localhost:8080/employees -d '{\"name\":\"Ana\"}'",
            'HTTP/1.1 201 Created\r\nContent-Type: application/json\r\n\r\n{"id":42}', 14)
        ui.on_event({'type': 'http_exchange', 'phase': 'build', 'exchange': exchange})
        self.assertEqual(len(ui.requests), 1)
        rows = [ui._plain(row) for row in ui._timeline_rows(90, 81, 6)]
        self.assertTrue(rows[0].startswith('▌ exec   ⇄ POST /employees'))
        self.assertTrue(rows[0].rstrip().endswith('201 Created · 14 ms · 9 B'), rows[0])
        self.assertIn('└ {"id": 42}', rows[1])
        detail = [ui._plain(row) for row in ui.http_detail_rows(ui.requests[0], 60)]
        self.assertEqual(detail[0], 'POST http://localhost:8080/employees')
        self.assertTrue(any(row.startswith('── Request') for row in detail))
        self.assertTrue(any(row.startswith('── Response · 201 Created · 14 ms · 9 B') for row in detail))
        self.assertIn('  "id": 42', detail)
        ui.reset_task()
        self.assertEqual(ui.requests, [])

    def test_requests_view_shows_list_and_selected_exchange(self):
        class Terminal(io.StringIO):
            def isatty(self): return True
        config = {'CCX_ORCHESTRATOR': 'a', 'CCX_PLANNER': 'b', 'CCX_BUILDER': 'c', 'CCX_REVIEWER': 'd'}
        master, slave = pty.openpty()
        stdin = os.fdopen(slave, 'r')
        try:
            with mock.patch.object(workflow_module.sys, 'stdout', new_callable=Terminal) as terminal, \
                 mock.patch.object(workflow_module.sys, 'stdin', stdin), \
                 mock.patch.dict(os.environ, {'TERM': 'xterm'}), \
                 mock.patch.object(workflow_module.shutil, 'get_terminal_size', return_value=os.terminal_size((120, 30))):
                ui = workflow_module.TeamUI(config, '/tmp/run')
                ui.color = False
                for command, output in (("curl -si http://h/employees", 'HTTP/1.1 200 OK\r\n\r\n[{"id":1}]'),
                                        ("curl -si http://h/employees/9", 'HTTP/1.1 404 Not Found\r\n\r\n{"error":"nope"}')):
                    [exchange] = workflow_module.http_exchanges(command, output)
                    ui.on_event({'type': 'http_exchange', 'phase': 'review', 'exchange': exchange})
                def press():
                    for keys in (b'\x1b[A', b'\x1b'):
                        time.sleep(0.15)
                        os.write(master, keys)
                presser = threading.Thread(target=press, daemon=True)
                presser.start()
                terminal.seek(0); terminal.truncate()
                ui.show_requests()
                presser.join(timeout=5)
                frames = terminal.getvalue().split('\x1b[H\x1b[2J')
            first, moved = frames[1], frames[-1]
            # Opens on the newest request; ↑ selects the one before it.
            self.assertIn('REQUESTS (2)', first)
            self.assertIn('GET http://h/employees/9', first)
            self.assertIn('Response · 404 Not Found', first)
            self.assertIn('GET http://h/employees', moved)
            self.assertIn('Response · 200 OK', moved)
            self.assertIn('│', moved)
        finally:
            stdin.close()
            os.close(master)

    def test_codex_curl_reaches_requests_log_and_the_tui(self):
        self.workflow(builder='codex')
        run = self.ccx('team', 'Implement fixture', env={**self.env, 'FAKE_CASE': 'curl_build'})
        self.assertIn('⇄ GET http://localhost:8080/health 200', run.stdout)
        log = sorted((self.root / 'config' / 'runs').glob('team-*/requests.jsonl'))[-1]
        [record] = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual((record['phase'], record['status'], record['response_body']),
                         ('build', 200, '{"ok": true}'))

    def test_invalid_reviewer_json_is_asked_once_more(self):
        self.ccx('new', '--name', 'mcode', '--provider', 'mcode', '--yes')
        self.workflow(reviewer='mcode')
        log = self.mcode_data / 'retry-prompts.jsonl'
        run = self.ccx('team', 'Implement fixture',
                       env={**self.env, 'FAKE_CASE': 'bad_review_json', 'FAKE_PROMPT_LOG': str(log)})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('asking once more', run.stdout)
        prompts = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertTrue(any('YOUR PREVIOUS REPLY WAS NOT VALID JSON' in p for p in prompts))
        # Every agent is told which directory is the project.
        self.assertTrue(all(p.startswith('PROJECT DIRECTORY: ') for p in prompts), prompts[0][:200])

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

    def test_follow_up_review_does_not_block_on_new_medium_findings(self):
        self.workflow()
        run = self.ccx('team', 'Implement fixture', env={**self.env, 'FAKE_CASE': 'revise_new_medium'})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        runs = sorted((self.root / 'config' / 'runs').glob('team-*/review-approval.json'))
        review = json.loads(runs[-1].read_text())['review']
        self.assertEqual(review['verdict'], 'APPROVED')
        self.assertEqual(review['findings'][0]['severity'], 'low')
        self.assertIn('follow-up round', review['findings'][0]['evidence'])

    def test_every_output_schema_is_valid_for_codex_strict_mode(self):
        def check(node, where):
            if isinstance(node, dict):
                if node.get('type') == 'object' and 'properties' in node:
                    self.assertEqual(set(node['properties']), set(node.get('required', [])), where)
                    self.assertIs(node.get('additionalProperties'), False, where)
                for key, child in node.items():
                    check(child, f'{where}.{key}')
            elif isinstance(node, list):
                for child in node:
                    check(child, where)
        for name in ('PLAN_SCHEMA', 'ROUTE_SCHEMA', 'REVIEW_SCHEMA', 'BUILD_SCHEMA'):
            check(getattr(workflow_module, name), name)

    def test_settle_follow_up_keeps_unresolved_and_new_high_findings(self):
        previous = [{'id': 'R1', 'severity': 'high', 'path': 'plan', 'evidence': 'x', 'fix': 'y'}]
        base = {'verdict': 'REVISE', 'summary': 's', 'coverage': [], 'limitations': []}
        still = workflow_module.settle_follow_up({**base, 'findings': [dict(previous[0], severity='medium')]}, previous)
        self.assertEqual(still['verdict'], 'REVISE')
        new_high = workflow_module.settle_follow_up(
            {**base, 'findings': [{'id': 'N9', 'severity': 'high', 'path': 'p', 'evidence': 'e', 'fix': 'f'}]}, previous)
        self.assertEqual(new_high['verdict'], 'REVISE')
        nit = workflow_module.settle_follow_up(
            {**base, 'findings': [{'id': 'N2', 'severity': 'medium', 'path': 'p', 'evidence': 'e', 'fix': 'f'}]}, previous)
        self.assertEqual(nit['verdict'], 'APPROVED')
        self.assertEqual(nit['findings'][0]['severity'], 'low')

    def test_exhausted_plan_budget_without_a_terminal_keeps_the_plan(self):
        self.workflow()
        run = self.ccx('team', 'Implement fixture', ok=False, env={**self.env, 'FAKE_CASE': 'revise'})
        self.assertIn('round budget exhausted (1 high open)', run.stderr)
        self.assertIn('PLAN.md', run.stderr)

    def test_exhausted_plan_budget_offers_to_build_in_the_tui(self):
        self.workflow()
        master, slave = pty.openpty()
        try:
            with KillOnErrorPopen([str(CCX), 'team'], cwd=self.repo,
                                  env={**self.env, 'TERM': 'xterm', 'FAKE_CASE': 'revise'},
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
                            self.fail(f'Missing {token!r}: {data[-1000:]!r}')
                        data += os.read(master, 65536)
                    return data
                read_until(b'Describe a task')
                os.write(master, b'/proof test -f implemented.py\n')
                read_until(b'Describe a task')
                os.write(master, b'Implement fixture\n')
                picker = read_until(b'q cancel')
                self.assertIn(b'PLAN REVIEW BUDGET USED UP', picker)
                self.assertIn(b'Build with this plan (1 high open)', picker)
                os.write(master, b'\r')
                done = read_until(b'Describe a task')
                self.assertIn(b'Completed in', done)
                os.write(master, b'/quit\n')
                self.assertEqual(proc.wait(timeout=10), 0)
            self.assertTrue((self.repo / 'implemented.py').exists())
            approval = sorted((self.root / 'config' / 'runs').glob('team-*/review-approval.json'))[-1]
            self.assertTrue(json.loads(approval.read_text())['accepted_by_user'])
        finally:
            os.close(master)
            if slave is not None:
                os.close(slave)

    def test_planner_questions_are_asked_once_without_using_a_round(self):
        self.workflow()
        master, slave = pty.openpty()
        prompt_log = self.root / 'planner-prompts.jsonl'
        try:
            with KillOnErrorPopen([str(CCX), 'team', '--review-only'], cwd=self.repo,
                                  env={**self.env, 'TERM': 'xterm', 'FAKE_CASE': 'planner_questions',
                                       'FAKE_PROMPT_LOG': str(prompt_log)},
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
                            self.fail(f'Missing {token!r}: {data[-1000:]!r}')
                        data += os.read(master, 65536)
                    return data
                read_until(b'Describe a task')
                os.write(master, b'Build employees\n')
                picker = read_until(b'q cancel')
                self.assertIn(b'Which status values?', picker)
                os.write(master, b'2')
                read_until(b'q cancel')
                os.write(master, b'\r')
                self.assertIn(b'Completed in', read_until(b'Describe a task'))
                os.write(master, b'/quit\n')
                self.assertEqual(proc.wait(timeout=10), 0)
            prompts = [json.loads(line) for line in prompt_log.read_text().splitlines()]
            planner = [p for p in prompts if 'Produce an implementation plan' in p]
            self.assertEqual(len(planner), 2)
            self.assertIn('active/suspended/left', planner[1])
            self.assertIn('Record those as unresolved questions', planner[1])
        finally:
            os.close(master)
            if slave is not None:
                os.close(slave)

    def test_flow_panel_draws_hand_offs_and_animates_the_live_one(self):
        class Terminal(io.StringIO):
            def isatty(self): return True
        config = {'CCX_ORCHESTRATOR': 'a', 'CCX_PLANNER': 'b', 'CCX_BUILDER': 'c', 'CCX_REVIEWER': 'd'}
        with mock.patch.object(workflow_module.sys, 'stdout', new_callable=Terminal) as terminal, \
             mock.patch.dict(os.environ, {'TERM': 'xterm'}), \
             mock.patch.object(workflow_module.shutil, 'get_terminal_size', return_value=os.terminal_size((120, 36))):
            ui = workflow_module.TeamUI(config, '/tmp/run')
            ui.color = False
            for phase in ('orchestrate', 'plan'):
                ui.begin(phase, 'cli', 'm', 'medium')
                ui.done(phase)
            ui.begin('review', 'cli', 'm', 'medium')
            self.assertEqual(ui.flow_edge, ('plan', 'review'))
            self.assertEqual(ui.flow_used, {frozenset({'orchestrate', 'plan'}), frozenset({'plan', 'review'})})
            first = ui.flow_rows(34, ui.flow_started)
            later = ui.flow_rows(34, ui.flow_started + 0.25)
            text = '\n'.join(first)
            for label in ('✓ ORCH', '✓ PLAN', '○ EXEC', '● REVIEW'):
                self.assertIn(label, text)
            self.assertIn('──────', first[1])
            self.assertIn('▼', text)
            # The dot moves along the live hand-off between frames.
            self.assertNotEqual(first, later)
            self.assertEqual(sum(row.count('●') for row in first), 2)
            # The panel sits at the bottom of the agent rail when there is room.
            ui._previous_rows = None
            ui.render()
            frame = terminal.getvalue()
            self.assertIn('FLOW', frame)
            self.assertIn('┌────────────┐', frame)
            # Once the reviewer finishes, the hand-off stops animating.
            ui.done('review')
            self.assertEqual(sum(row.count('●') for row in ui.flow_rows(34)), 0)
            ui.reset_task()
            self.assertIsNone(ui.flow_edge)
            self.assertEqual(ui.flow_used, set())

    def test_answer_lands_in_the_conversation_from_its_first_line(self):
        class Terminal(io.StringIO):
            def isatty(self): return True
        config = {'CCX_ORCHESTRATOR': 'a', 'CCX_PLANNER': 'b', 'CCX_BUILDER': 'c', 'CCX_REVIEWER': 'd'}
        with mock.patch.object(workflow_module.sys, 'stdout', new_callable=Terminal) as terminal, \
             mock.patch.dict(os.environ, {'TERM': 'xterm'}), \
             mock.patch.object(workflow_module.shutil, 'get_terminal_size', return_value=os.terminal_size((100, 28))):
            ui = workflow_module.TeamUI(config, '/tmp/run')
            ui.visible = ['review']
            ui.timeline.extend(('review', f'tool: read', 'tool') for _ in range(5))
            answer = 'Answer starts here.\n\n' + '\n'.join(f'- point {n}' for n in range(60))
            ui.on_event({'type': 'task_finished', 'status': 'completed', 'summary': answer})
            ui.result = answer
            ui._previous_rows = None
            ui.render()
            frame = terminal.getvalue()
            # The answer is read from its first line; no separate RESULT panel.
            self.assertIn('Answer starts here.', frame)
            self.assertNotIn('point 59', frame)
            self.assertIn('newer lines', frame)
            self.assertNotIn('── RESULT', frame)
            terminal.seek(0); terminal.truncate()
            ui.on_event({'type': 'task_finished', 'status': 'failed', 'error': 'checker rejected the build'})
            ui._previous_rows = None
            ui.render()
            self.assertIn('Failed: checker rejected the build', terminal.getvalue())

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
            with mock.patch('builtins.input', side_effect=['1', '', 'q']), \
                 mock.patch.object(workflow_module.sys, 'stdin', io.StringIO()):
                ui.show_agents()
            frame = terminal.getvalue()
            self.assertIn('AGENTS', frame)
            self.assertIn('REVIEW', frame)
            self.assertIn('tool: glob', frame)

    def test_composer_suggests_commands_and_files_and_edits_like_a_shell(self):
        config = {'CCX_ORCHESTRATOR': 'a', 'CCX_PLANNER': 'b', 'CCX_BUILDER': 'c', 'CCX_REVIEWER': 'd'}
        ui = workflow_module.TeamUI(config, '/tmp/run')
        ui.completion_files = ['src/main.rs', 'src/lib.rs', 'README.md']
        keys = ui._compose_keys
        # A leading slash lists commands; the exact or first prefix match is highlighted.
        self.assertIsNone(keys('/r'))
        self.assertEqual([name for name, _ in ui.suggestions], ['/result', '/redo', '/roles', '/requests', '/route'])
        self.assertEqual(keys('\r'), '/result')
        # Arrow keys choose, Tab completes and leaves room for an argument.
        keys('/r')
        keys('\x1b[B\x1b[B\x1b[B\x1b[B')
        self.assertEqual(ui.suggestions[ui.suggestion_index][0], '/route')
        keys('\t')
        self.assertEqual(ui.input_buffer, '/route ')
        self.assertEqual([name for name, _ in ui.suggestions],
                         ['/route auto', '/route plan', '/route direct', '/route inspect'])
        self.assertEqual(keys('direct\r'), '/route direct')
        # Completing a command with choices lists them; no typing needed.
        keys('/the\t')
        self.assertEqual(ui.input_buffer, '/theme ')
        self.assertEqual([name for name, _ in ui.suggestions], ['/theme default', '/theme ocean', '/theme contrast'])
        self.assertEqual(keys('\x1b[B\r'), '/theme ocean')
        keys('/queue c')
        self.assertEqual([name for name, _ in ui.suggestions], ['/queue clear'])
        keys('\x1b\x1b')
        # @ lists workspace files and Enter inserts the highlighted one.
        keys('fix @lib')
        self.assertEqual(ui.suggestions[0][0], '@src/lib.rs')
        self.assertIsNone(keys('\r'))
        self.assertEqual(ui.input_buffer, 'fix @src/lib.rs ')
        self.assertEqual(keys('now\r'), 'fix @src/lib.rs now')
        # Alt+Enter, a trailing backslash and pasted text keep their newlines.
        keys('one\x1b\rtwo\\\rthree')
        self.assertEqual(ui.input_buffer, 'one\ntwo\nthree')
        keys('\x15')
        self.assertEqual(ui.input_buffer, 'one\ntwo\n')
        keys('\x1b')
        self.assertEqual(ui.input_buffer, '')
        keys('\x1b[200~first line\r\nsecond line\x1b[201~')
        self.assertEqual(keys('\r'), 'first line\nsecond line')
        # Up recalls history; Esc clears; Ctrl+P opens the palette.
        keys('\x1b[A')
        self.assertEqual(ui.input_buffer, 'first line\nsecond line')
        keys('\x1b')
        self.assertEqual(ui.input_buffer, '')
        self.assertEqual(keys('\x10'), '/commands')
        # Editing keys: left, insert, Ctrl+W deletes the previous word.
        keys('hello world')
        keys('\x1b[D\x1b[D\x1b[D\x1b[D\x1b[DX')
        self.assertEqual(ui.input_buffer, 'hello Xworld')
        keys('\x05\x17')
        self.assertEqual(ui.input_buffer, 'hello ')

    def test_transcript_groups_tool_calls_and_lays_out_agent_markdown(self):
        config = {'CCX_ORCHESTRATOR': 'a', 'CCX_PLANNER': 'b', 'CCX_BUILDER': 'c', 'CCX_REVIEWER': 'd'}
        ui = workflow_module.TeamUI(config, '/tmp/run')
        ui.color = False
        ui.on_event({'type': 'agent_started', 'phase': 'review', 'profile': 'minimax', 'model': ''})
        for message in ('tool: read', 'tool: read', 'still running (10s elapsed)', 'tool: grep', 'tool: bash'):
            ui.on_event({'type': 'activity', 'phase': 'review', 'message': message})
        ui.on_event({'type': 'activity', 'phase': 'review', 'category': 'text',
                     'message': '## Findings\n\n- **CI** missing\n- `lockfile` duplicated'})
        rows = [ui._plain(row).rstrip() for row in ui._timeline_rows(80, 71, 6)]
        self.assertIn('▌ review ⏺ 4 tool calls · read 2 · grep 1 · bash 1', rows)
        self.assertTrue(any('└ bash' in row and 'ctrl+o expand' in row for row in rows))
        self.assertFalse(any('still running' in row for row in rows))
        self.assertIn('▌ review ▍ Findings', rows)
        self.assertTrue(any(row.endswith('• CI missing') for row in rows))
        self.assertTrue(any(row.endswith('• lockfile duplicated') for row in rows))
        ui.expand_tools = True
        rows = [ui._plain(row).rstrip() for row in ui._timeline_rows(80, 71, 6)]
        self.assertEqual(sum('⏺ read' in row for row in rows), 2)
        self.assertFalse(any('still running' in row for row in rows))
        # The rail stays one line per entry even for multi-line agent text.
        self.assertTrue(all('\n' not in line for line in ui.panels['review']['lines']))

    def test_diff_view_colours_changes_and_summarises_files(self):
        config = {'CCX_ORCHESTRATOR': 'a', 'CCX_PLANNER': 'b', 'CCX_BUILDER': 'c', 'CCX_REVIEWER': 'd'}
        ui = workflow_module.TeamUI(config, '/tmp/run')
        ui.color = True
        diff = ('diff --git a/app.py b/app.py\nindex 1..2 100644\n--- a/app.py\n+++ b/app.py\n'
                '@@ -1,2 +1,2 @@\n-old = 1\n+new = 2\n context\n')
        rows = ui.diff_rows(diff, 60)
        plain = [ui._plain(row) for row in rows]
        self.assertEqual(plain[0], '1 file changed  +1 -1')
        self.assertIn('▍ app.py', plain)
        self.assertIn(ui._paint('+new = 2', 'ok'), rows)
        self.assertIn(ui._paint('-old = 1', 'err'), rows)
        self.assertIn(ui._paint('@@ -1,2 +1,2 @@', 'accent'), rows)

    def test_composer_history_persists_and_ctrl_r_searches_it(self):
        config = {'CCX_ORCHESTRATOR': 'a', 'CCX_PLANNER': 'b', 'CCX_BUILDER': 'c', 'CCX_REVIEWER': 'd'}
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {'CCX_HOME': home}):
            ui = workflow_module.TeamUI(config, '/tmp/run')
            self.assertEqual(ui.input_history, [])
            for task in ('fix the login bug', 'add tests for sync', 'fix the logout bug'):
                ui._compose_keys(task)
                sent = ui._compose_keys('\r')
                ui._save_history(sent)
            history = Path(home) / 'tui-history.jsonl'
            self.assertEqual(stat.S_IMODE(history.stat().st_mode), 0o600)
            again = workflow_module.TeamUI(config, '/tmp/run')
            self.assertEqual(again.input_history[-1], 'fix the logout bug')
            # Ctrl+R lists matching entries, newest first; Enter puts one back to edit.
            again._compose_keys('\x12fix')
            self.assertEqual([item[2] for item in again.suggestions], ['fix the logout bug', 'fix the login bug'])
            again._compose_keys('\x1b[B\r')
            self.assertFalse(again.history_search)
            self.assertEqual(again.input_buffer, 'fix the login bug')
            # Fuzzy @file matching finds letters in order.
            again.completion_files = ['lib/workflow.py', 'README.md', 'bin/ccx']
            again._compose_keys('\x1b')
            again._compose_keys('see @wfpy')
            self.assertEqual(again.suggestions[0][0], '@lib/workflow.py')

    def test_resize_flags_a_full_repaint(self):
        config = {'CCX_ORCHESTRATOR': 'a', 'CCX_PLANNER': 'b', 'CCX_BUILDER': 'c', 'CCX_REVIEWER': 'd'}
        ui = workflow_module.TeamUI(config, '/tmp/run')
        ui._previous_rows = ['old frame']
        ui._on_resize()
        self.assertTrue(ui._resized)
        self.assertIsNone(ui._previous_rows)

    def test_result_markdown_is_laid_out_for_the_terminal(self):
        config = {'CCX_ORCHESTRATOR': 'a', 'CCX_PLANNER': 'b', 'CCX_BUILDER': 'c', 'CCX_REVIEWER': 'd'}
        ui = workflow_module.TeamUI(config, '/tmp/run')
        ui.color = True
        text = ("Intro line.\n\n## Structure\n\n| Piece | Path |\n|---|---|\n"
                "| Frontend | `src/` |\n| Backend | `src-tauri/src/` with a much longer description |\n\n"
                "1. **CI covers only Laravel.** Nothing else runs.\n- *note* bullet\n")
        rows = ui.markdown_rows(text, 40)
        plain = [ui._plain(row) for row in rows]
        self.assertTrue(all(len(row) <= 40 for row in plain), plain)
        self.assertIn('Intro line.', plain[0])
        self.assertIn('▍ Structure', plain)
        header = next(row for row in plain if 'Piece' in row)
        self.assertIn('│', header)
        self.assertNotIn('|', ''.join(plain))
        self.assertTrue(any('─┼─' in row for row in plain))
        self.assertTrue(any(row.startswith(' 1. CI covers only Laravel.') for row in plain))
        self.assertTrue(any(row.startswith(' • note bullet') for row in plain))
        self.assertNotIn('**', ''.join(plain))
        self.assertNotIn('`', ''.join(plain))
        self.assertNotIn('##', ''.join(plain))
        # Bold and code keep their own styles in colour terminals.
        self.assertIn('\x1b[1mLaravel.\x1b[0m', ''.join(rows))

    def test_result_view_wheel_scrolls_text_and_arrows_choose(self):
        class Terminal(io.StringIO):
            def isatty(self): return True
        config = {'CCX_ORCHESTRATOR': 'a', 'CCX_PLANNER': 'b', 'CCX_BUILDER': 'c', 'CCX_REVIEWER': 'd'}
        master, slave = pty.openpty()
        stdin = os.fdopen(slave, 'r')
        try:
            with mock.patch.object(workflow_module.sys, 'stdout', new_callable=Terminal) as terminal, \
                 mock.patch.object(workflow_module.sys, 'stdin', stdin), \
                 mock.patch.dict(os.environ, {'TERM': 'xterm'}), \
                 mock.patch.object(workflow_module.shutil, 'get_terminal_size', return_value=os.terminal_size((100, 20))):
                ui = workflow_module.TeamUI(config, '/tmp/run')
                body = '\n'.join(f'line {n:03d}' for n in range(60))
                def press():
                    # Keys sent after the view switched the terminal to cbreak:
                    # two wheel notches down, then the down arrow and Enter.
                    for keys in (b'\x1b[<65;10;5M', b'\x1b[<65;10;5M', b'\x1b[B', b'\r'):
                        time.sleep(0.15)
                        os.write(master, keys)
                presser = threading.Thread(target=press, daemon=True)
                presser.start()
                choice = ui.show_text('CONCLUSION', body, actions=[('New task', None), ('Quit', '/quit')])
                frames = terminal.getvalue()
                presser.join(timeout=5)
            self.assertEqual(choice, '/quit')
            self.assertIn('(7-', frames)
            self.assertIn('\x1b[?1000h', frames)
            self.assertTrue(frames.rstrip().endswith('\x1b[?25l') or '\x1b[?1000l' in frames)
        finally:
            stdin.close()
            os.close(master)

    def test_idle_composer_scrolls_the_conversation_with_wheel_and_page_keys(self):
        class Terminal(io.StringIO):
            def isatty(self): return True
        config = {'CCX_ORCHESTRATOR': 'a', 'CCX_PLANNER': 'b', 'CCX_BUILDER': 'c', 'CCX_REVIEWER': 'd'}
        with mock.patch.object(workflow_module.sys, 'stdout', new_callable=Terminal), \
             mock.patch.dict(os.environ, {'TERM': 'xterm'}), \
             mock.patch.object(workflow_module.shutil, 'get_terminal_size', return_value=os.terminal_size((100, 20))):
            ui = workflow_module.TeamUI(config, '/tmp/run')
            ui.visible = ['review']
            ui.timeline = [('review', f'line {n:03d}', 'text') for n in range(80)]
            ui.input_history = ['earlier task']
            ui.composing = True
            ui.render()
            self.assertIsNone(ui.transcript_top)
            # Wheel up (SGR button 64) scrolls; the input line is untouched.
            ui._compose_keys('\x1b[<64;10;5M')
            self.assertIsNotNone(ui.transcript_top)
            self.assertEqual(ui.input_buffer, '')
            ui.render()
            ui._compose_keys('\x1b[5~')
            first = ui.transcript_top
            ui.render()
            ui._compose_keys('\x1b[5~')
            self.assertLess(ui.transcript_top, first)
            # Home and End scroll while the line is empty; ↑ still recalls input.
            ui._compose_keys('\x1b[H')
            self.assertEqual(ui.transcript_top, 0)
            ui.render()
            ui._compose_keys('\x1b[F')
            self.assertIsNone(ui.transcript_top)
            ui._compose_keys('\x1b[A')
            self.assertEqual(ui.input_buffer, 'earlier task')
            ui._compose_keys('\x1b[H')
            self.assertEqual(ui.input_cursor, 0)
            self.assertIsNone(ui.transcript_top)

    def test_running_view_scrolls_back_through_the_conversation(self):
        class Terminal(io.StringIO):
            def isatty(self): return True
        config = {'CCX_ORCHESTRATOR': 'a', 'CCX_PLANNER': 'b', 'CCX_BUILDER': 'c', 'CCX_REVIEWER': 'd'}
        with mock.patch.object(workflow_module.sys, 'stdout', new_callable=Terminal) as terminal, \
             mock.patch.dict(os.environ, {'TERM': 'xterm'}), \
             mock.patch.object(workflow_module.shutil, 'get_terminal_size', return_value=os.terminal_size((100, 20))):
            ui = workflow_module.TeamUI(config, '/tmp/run')
            ui.visible = ['review']
            ui.timeline = [('review', f'line {n:03d}') for n in range(80)]
            ui.render()
            self.assertIn('earlier lines', terminal.getvalue())
            self.assertIn('line 079', terminal.getvalue())
            master, slave = pty.openpty()
            try:
                decoder = workflow_module.codecs.getincrementaldecoder('utf-8')('replace')
                terminal.seek(0); terminal.truncate()
                os.write(slave, b'\x1b[H')
                self.assertFalse(workflow_module.read_running_input(ui, master, decoder))
                frame = terminal.getvalue()
                self.assertIn('line 000', frame)
                self.assertIn('newer lines', frame)
                self.assertNotIn('line 079', frame)
                # New output while scrolled back keeps the view in place.
                ui.timeline.append(('review', 'line 080'))
                terminal.seek(0); terminal.truncate()
                ui._previous_rows = None
                ui.render()
                self.assertIn('line 000', terminal.getvalue())
                terminal.seek(0); terminal.truncate()
                os.write(slave, b'\x1b[F')
                workflow_module.read_running_input(ui, master, decoder)
                self.assertIsNone(ui.transcript_top)
                self.assertIn('line 080', terminal.getvalue())
                # Arrow keys move one line at a time and never reach the task input.
                os.write(slave, b'\x1b[A\x1b[A')
                workflow_module.read_running_input(ui, master, decoder)
                self.assertIsNotNone(ui.transcript_top)
                self.assertEqual(ui.queue_input, '')
            finally:
                os.close(master)
                os.close(slave)

    def test_team_tui_session_and_persisted_history(self):
        self.workflow()
        master, slave = pty.openpty()
        try:
            prompt_log = self.root / 'session-prompts.jsonl'
            with KillOnErrorPopen([str(CCX), 'team', '--review-only'], cwd=self.repo,
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
                initial = read_until(b'Describe a task')
                self.assertIn(b'What would you like', initial)
                for label in (b'ORCHESTRATOR', b'PLAN |', b'EXECUTE', b'REVIEW'):
                    self.assertNotIn(label, initial)
                os.write(master, b'\x10')
                self.assertIn(b'COMMAND PALETTE', read_until(b'q cancel'))
                os.write(master, b'9')
                self.assertIn(b'/files', read_until(b'q cancel'))
                os.write(master, b'\r')
                self.assertIn(b'WORKSPACE FILES', read_until(b'Enter select'))
                os.write(master, b'\n')
                read_until(b'Describe a task')
                os.write(master, b'/route\n')
                picker = read_until(b'q cancel')
                for label in (b'auto', b'plan', b'direct', b'inspect'):
                    self.assertIn(label, picker)
                os.write(master, b'3')
                read_until(b'q cancel')
                os.write(master, b'\r')
                self.assertIn(b'Route set to direct', read_until(b'Describe a task'))
                os.write(master, b'/route\n')
                read_until(b'q cancel')
                os.write(master, b'1')
                read_until(b'q cancel')
                os.write(master, b'\r')
                self.assertIn(b'Route set to auto', read_until(b'Describe a task'))
                os.write(master, b'Fixture task\n')
                # The run ends back at the composer: no screen to dismiss.
                completed = read_until(b'Describe a task')
                self.assertIn(b'PLAN', completed)
                self.assertIn(b'REVIEW', completed)
                self.assertNotIn(b'EXECUTE', completed)
                self.assertIn(b'Completed in', completed)
                self.assertNotIn(b'CONCLUSION', completed)
                os.write(master, b'/result\n')
                detail = read_until(b'Enter select')
                self.assertIn(b'Plan approved', detail)
                os.write(master, b'\n')
                read_until(b'Describe a task')
                os.write(master, b'/history\n')
                history = read_until(b'q cancel')
                self.assertIn(b'Fixture task', history)
                os.write(master, b'1\n')
                detail = read_until(b'Enter select')
                self.assertIn(b'Fixture task', detail)
                self.assertIn(b'Plan approved', detail)
                os.write(master, b'\n')
                read_until(b'q cancel')
                os.write(master, b'q')
                read_until(b'Describe a task')
                os.write(master, b'Follow-up task\n')
                second = read_until(b'Describe a task')
                self.assertIn(b'Completed in', second)
                self.assertIn(b'/undo', second)
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
            with KillOnErrorPopen([str(CCX), 'team'], cwd=self.repo,
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
                initial = read_until(b'Describe a task')
                self.assertNotIn(b'ORCHESTRATOR |', initial)
                os.write(master, b'Inspect the project\n')
                output = read_until(b'Describe a task')
                self.assertIn(b'ORCHESTRATOR', output)
                self.assertIn(b'REVIEW', output)
                self.assertNotIn(b'PLAN |', output)
                self.assertNotIn(b'EXECUTE |', output)
                self.assertIn(b'Read-only inspection completed', output)
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
            with KillOnErrorPopen([str(CCX), 'team'], cwd=self.repo,
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

                read_until(b'Describe a task')
                os.write(master, b'Fixture task\n')
                picker = read_until(b'Type another command')
                self.assertIn(b'Verification command', picker)
                # Jump to the escape hatch and type a command of our own.
                os.write(master, b'5')
                os.write(master, b'\r')
                read_until(b'Command: ')
                os.write(master, b'true\n')
                output = read_until(b'Describe a task')
                self.assertIn(b'Completed in', output)
                self.assertIn(b'Verification: true', output)
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
            with KillOnErrorPopen([str(CCX), 'team'], cwd=self.repo,
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

                read_until(b'Describe a task')
                os.write(master, b'Fixture task\n')
                read_until(b'Verification command')
                # Cancelling the picker is the same as leaving the answer empty.
                os.write(master, b'q')
                output = read_until(b'Describe a task')
                self.assertIn(b'/proof', output)
                self.assertIn(b'No verification command', output)
                self.assertIn(b'Failed', output)
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
            read_until(master, proc, b'Describe a task')
            os.write(master, b'First task\n')
            read_until(master, proc, b'Queue: ')
            os.write(master, b'Follow-up queued\n')
            # The queued task starts as soon as the first one completes.
            second = read_until(master, proc, b'Describe a task')
            self.assertIn(b'Completed in', second)
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
            read_until(master, proc, b'Describe a task')
            os.write(master, b'Stop test\n')
            read_until(master, proc, b'Queue: ')
            time.sleep(0.2)
            os.write(master, b'\x1b')
            output = read_until(master, proc, b'Describe a task')
            self.assertIn(b'stopped by user', output)
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
            with KillOnErrorPopen([str(CCX), 'team', '--route', 'direct'], cwd=self.repo,
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
                read_until(b'Describe a task')
                os.write(master, b'Attempt task\n')
                self.assertIn(b'Failed', read_until(b'Describe a task'))
                self.assertTrue((self.repo / 'implemented.py').exists())
                os.write(master, b'/undo\n')
                self.assertIn(b'Undo complete', read_until(b'Describe a task'))
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
            with KillOnErrorPopen([str(CCX), 'team'], cwd=self.repo,
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
                read_until(b'Describe a task')
                os.write(master, b'/route direct\n')
                self.assertIn(b'Route set to direct', read_until(b'Describe a task'))
                os.write(master, b'/proof test -f implemented.py\n')
                self.assertIn(b'Verification command updated', read_until(b'Describe a task'))
                os.write(master, b'First task\n')
                read_until(b'Describe a task')
                os.write(master, b'Second task\n')
                second = read_until(b'Describe a task')
                self.assertIn(b'Completed in', second)
                os.write(master, b'/undo\n')
                self.assertIn(b'Undo complete', read_until(b'Describe a task'))
                self.assertFalse((self.repo / 'implemented.py').exists())
                os.write(master, b'/redo\n')
                self.assertIn(b'Redo complete', read_until(b'Describe a task'))
                self.assertTrue((self.repo / 'implemented.py').exists())
                (self.repo / 'implemented.py').write_text('external edit\n')
                os.write(master, b'/undo\n')
                refused = read_until(b'Enter select')
                self.assertIn(b'project changed', refused)
                self.assertEqual((self.repo / 'implemented.py').read_text(), 'external edit\n')
                os.write(master, b'\n')
                read_until(b'Describe a task')
                os.write(master, b'Third task\n')
                failure = read_until(b'Describe a task')
                self.assertIn(b'clean Git checkout', failure)
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
            with KillOnErrorPopen([str(CCX), 'team'], cwd=self.repo,
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
                read_until(b'Describe a task')
                os.write(master, b'/history\n')
                output = read_until(b'q cancel')
                self.assertIn(b'Previous run', output)
                self.assertIn(b'task not recorded in earlier run', output)
                os.write(master, b'q')
                read_until(b'Describe a task')
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
            with KillOnErrorPopen([str(CCX), 'team', '--review-only'], cwd=self.repo,
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
                read_until(b'Describe a task')
                os.write(master, b'/history\n')
                read_until(b'q cancel')
                os.write(master, b'1\n')
                detail = read_until(b'Resume this session')
                self.assertIn(b'Previous run', detail)
                os.write(master, b'\x1b[B')
                time.sleep(0.2)
                os.write(master, b'\r')
                self.assertIn(b'Session restored', read_until(b'Describe a task'))
                os.write(master, b'Follow-up\n')
                self.assertIn(b'Completed in', read_until(b'Describe a task'))
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
            with KillOnErrorPopen([str(CCX), 'team'], cwd=self.repo,
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
                read_until(b'Describe a task')
                os.write(master, b'/history\n')
                read_until(b'q cancel')
                os.write(master, b'1\n')
                read_until(b'Resume this session')
                os.write(master, b'\x1b[B')
                time.sleep(0.2)
                os.write(master, b'\r')
                refused = read_until(b'CANNOT RESUME')
                if b'Enter select' not in refused.split(b'CANNOT RESUME')[-1]:
                    refused += read_until(b'Enter select')
                self.assertIn(b'project or its files changed', refused)
                os.write(master, b'\n')
                read_until(b'Describe a task')
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
        with KillOnErrorPopen([str(CCX),'team','--review-only','Review fixture'], cwd=self.repo,
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
            self.assertIn('review: bash rg security',out)
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
            with KillOnErrorPopen([str(CCX),'new','--name',name],cwd=self.repo,env=self.env,
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

    def test_proof_candidates_lead_with_the_detected_command(self):
        go = self.root / 'go-project'
        go.mkdir()
        (go / 'go.mod').write_text('module example.com/demo\n')
        candidates = workflow_module.proof_candidates(go)
        self.assertEqual(candidates[0][0], 'go test ./...')
        self.assertIn('detected', candidates[0][1])
        self.assertIn('go build ./...', [command for command, _ in candidates])
        python = self.root / 'python-project'
        python.mkdir()
        (python / 'pyproject.toml').write_text('[tool.pytest.ini_options]\n')
        commands = [command for command, _ in workflow_module.proof_candidates(python)]
        self.assertEqual(commands[0], 'python3 -m pytest')
        empty = self.root / 'empty-project'
        empty.mkdir()
        commands = [command for command, _ in workflow_module.proof_candidates(empty)]
        self.assertIn('go test ./...', commands)
        self.assertIn('python3 -m pytest', commands)

    def test_require_proof_uses_the_picked_command(self):
        class FakeUI:
            enabled = True

            def __init__(self):
                self.picked = None

            def select_choice(self, title, options, subtitle=''):
                self.title = title
                self.options = options
                return self.picked

            def read_command(self, prompt='Task: '):
                return ''

        ui = FakeUI()
        with mock.patch.object(workflow_module, 'team_ui', ui):
            config = {'CCX_PROOF': ''}
            ui.picked = 'go test ./...'
            self.assertEqual(workflow_module.require_proof(config, self.repo), 'go test ./...')
            self.assertEqual(config['CCX_PROOF'], 'go test ./...')
            self.assertEqual(config['CCX_PROOF_SOURCE'], 'prompt')
            self.assertEqual(ui.title, 'Verification command')
            labels = [label for label, _value, _note in ui.options]
            self.assertIn('Type another command', labels)
            ui.picked = None
            with self.assertRaisesRegex(workflow_module.WorkflowError, '/proof COMMAND'):
                workflow_module.require_proof({'CCX_PROOF': ''}, self.repo)

    def test_validate_questions_rejects_unusable_questions(self):
        self.assertEqual(workflow_module.validate_questions(None), [])
        self.assertEqual(workflow_module.validate_questions([]), [])
        kept = workflow_module.validate_questions([
            {'question': 'Which framework?', 'options': ['net/http', 'gin']}])
        self.assertEqual(kept[0]['options'], ['net/http', 'gin'])
        for broken in ([{'question': 'Which?', 'options': []}],
                       [{'question': 'Which?', 'options': ['']}],
                       [{'question': '', 'options': ['a']}],
                       [{'question': 'Which?'}]):
            with self.assertRaises(workflow_module.WorkflowError):
                workflow_module.validate_questions(broken)

    def test_clarification_answers_reach_the_planner(self):
        self.orchestrator_team()
        questions = json.dumps([{'question': 'Which framework?', 'options': ['net/http', 'gin']}])
        prompt_log = self.root / 'prompts.log'
        run = subprocess.run([str(CCX),'team-orch','Build an API'],cwd=self.repo,
                             env={**self.env,'FAKE_QUESTIONS':questions,
                                  'FAKE_PROMPT_LOG':str(prompt_log)},
                             capture_output=True,text=True,timeout=60)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        run_dir = next((self.root / 'config' / 'runs').glob('team-orch-*'))
        saved = json.loads((run_dir / 'clarifications.json').read_text())
        self.assertEqual(saved['questions'][0]['question'], 'Which framework?')
        self.assertEqual(saved['answers'][0]['answer'], '')
        prompts = [json.loads(line) for line in prompt_log.read_text().splitlines() if line.strip()]
        plan_prompt = next(prompt for prompt in prompts if 'USER ANSWERS' in prompt)
        self.assertIn('Which framework?', plan_prompt)
        self.assertIn('(no answer given)', plan_prompt)

    def test_verification_picker_offers_commands_before_the_task_runs(self):
        self.ccx('new','--name','team-noproof','--type','loop','--planner','claude',
                 '--builder','claude','--reviewer','codex','--yes')
        master, slave = pty.openpty()
        proc = subprocess.Popen([str(CCX), 'team-noproof'], cwd=self.repo,
                                env={**self.env, 'TERM': 'xterm'},
                                stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        slave = None
        try:
            def read_until(token):
                data = b''
                deadline = time.monotonic() + 15
                while token not in data:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not select.select([master], [], [], remaining)[0]:
                        proc.kill()
                        self.fail(f'Missing {token!r}: {data[-1200:]!r}')
                    data += os.read(master, 65536)
                return data

            read_until(b'Describe a task')
            os.write(master, b'Build an API\n')
            picker = read_until(b'move')
            self.assertIn(b'Verification command', picker)
            self.assertIn(b'go test ./...', picker)
            self.assertIn(b'python3 -m pytest', picker)
            self.assertIn(b'Type another command', picker)
            os.write(master, b'\r')
            read_until(b'verification command set for this session')
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                runs = list((self.root / 'config' / 'runs').glob('team-noproof-*'))
                if runs and json.loads((runs[0] / 'workflow.json').read_text()).get('CCX_PROOF'):
                    break
                time.sleep(0.2)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
            os.close(master)
            if slave is not None:
                os.close(slave)

        run_dir = next((self.root / 'config' / 'runs').glob('team-noproof-*'))
        saved = json.loads((run_dir / 'workflow.json').read_text())
        self.assertEqual(saved['CCX_PROOF'], 'go test ./...')
        self.assertEqual(saved['CCX_PROOF_SOURCE'], 'prompt')
        events = (run_dir / 'events.jsonl').read_text()
        self.assertIn('verification command set for this session: go test ./...', events)

    def test_clarification_picker_shows_the_question_and_options(self):
        self.orchestrator_team()
        questions = json.dumps([{'question': 'Which framework?', 'options': ['net/http', 'gin']}])
        master, slave = pty.openpty()
        proc = subprocess.Popen([str(CCX), 'team-orch'], cwd=self.repo,
                                env={**self.env, 'TERM': 'xterm', 'FAKE_QUESTIONS':questions},
                                stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        slave = None
        try:
            def read_until(token):
                data = b''
                deadline = time.monotonic() + 15
                while token not in data:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not select.select([master], [], [], remaining)[0]:
                        proc.kill()
                        self.fail(f'Missing {token!r}: {data[-1200:]!r}')
                    data += os.read(master, 65536)
                return data

            read_until(b'Describe a task')
            os.write(master, b'Build an API\n')
            picker = read_until(b'net/http')
            self.assertIn(b'Which framework?', picker)
            self.assertIn(b'gin', picker)
            os.write(master, b'\r')
            read_until(b'Describe a task')
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
            os.close(master)
            if slave is not None:
                os.close(slave)

        run_dir = next((self.root / 'config' / 'runs').glob('team-orch-*'))
        saved = json.loads((run_dir / 'clarifications.json').read_text())
        self.assertEqual(saved['answers'][0]['answer'], 'net/http')


if __name__ == '__main__':
    unittest.main()
