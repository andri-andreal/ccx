"""Create a disposable desktop fixture; the caller owns its directory/lifetime."""
import ast
import json
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1]).resolve()
source = Path(__file__).resolve().parents[1]
tree = ast.parse((source / 'tests/test_native_workflow.py').read_text(encoding='utf-8'))
fake = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == 'FAKE' for t in node.targets))
(root / 'fake cli.py').write_text(fake, encoding='utf-8')
directory = root / 'config/profiles/codex-work'
directory.mkdir(parents=True)
(directory / 'profile.env').write_text('CCX_PROVIDER=codex\nCCX_ISOLATE=false\n', encoding='utf-8')
routed = root / 'config/profiles/routed-work'
routed.mkdir(parents=True)
(routed / 'profile.env').write_text('CCX_PROVIDER=openai\nCCX_ISOLATE=true\nCCX_ROUTER=builtin\n'
                                  'CCX_UPSTREAM_BASE_URL=http://127.0.0.1:9/v1\n'
                                  'CCX_UPSTREAM_API_KEY=fixture-key\nANTHROPIC_MODEL=fixture-model\n', encoding='utf-8')
repo = root / 'project space'
repo.mkdir()
(repo / 'README.md').write_text('Fixture\n')
for args in [('init',), ('config', 'user.name', 'Fixture'), ('config', 'user.email', 'fixture@example.invalid'),
             ('add', '.'), ('commit', '-m', 'fixture')]:
    subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True)
print(json.dumps({'project': str(repo), 'home': str(root / 'config'), 'fake': str(root / 'fake cli.py')}))
