"""Exercise real ccx dispatch with disposable profiles/repos and fake CLIs."""
import json
import importlib.util
import os
import pty
from pathlib import Path
import subprocess
import tempfile
import termios
import time
import unittest


CCX = Path(__file__).resolve().parents[1] / 'bin' / 'ccx'
SPEC = importlib.util.spec_from_file_location('workflow', CCX.parent.parent / 'lib/workflow.py')
workflow_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workflow_module)
FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, sys, time
args = sys.argv[1:]
if os.environ.get('FAKE_ARGS_LOG'):
    with open(os.environ['FAKE_ARGS_LOG'], 'a') as log: log.write(json.dumps(args)+'\n')
if args[:3] == ['mcp', 'list', '--json']:
    print('[]'); sys.exit(0)
if 'exec' not in args and '-p' not in args:
    print(json.dumps({'args':args, 'home':os.environ.get('CODEX_HOME'),
                      'claude_home':os.environ.get('CLAUDE_CONFIG_DIR')})); sys.exit(0)
prompt = sys.stdin.read()
case = os.environ.get('FAKE_CASE', '')
if case == 'empty': sys.exit(0)
codex = 'exec' in args
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
    schema = json.loads(pathlib.Path(args[args.index('--output-schema')+1]).read_text())
elif '--json-schema' in args:
    schema = json.loads(args[args.index('--json-schema')+1])
if schema and 'plan' in schema['properties']:
    value = {'plan':'Create implemented.py; verify with the configured command.'}
elif schema and 'status' in schema['properties']:
    pathlib.Path('implemented.py').write_text('print(42)\n')
    feedback = json.loads(prompt.split('PREVIOUS ROUND / FIX REQUEST:\n',1)[1])
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
    pathlib.Path('implemented.py').write_text('print(42)\n')
    value = 'Created implemented.py.'
session = '12345678-1234-4567-8123-123456789abc'
if codex:
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
                    'CCX_CLAUDE_BIN':str(self.fake), 'CCX_CODEX_BIN':str(self.fake)}
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
                os.write(master,keys)
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
        self.assertEqual([args[args.index('--model')+1] for args in turns],
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


if __name__ == '__main__':
    unittest.main()
