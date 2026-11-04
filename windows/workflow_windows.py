"""Load the existing workflow with Windows-only platform bindings.

The Linux source is unchanged. AST import substitution is deliberately restricted
to seven platform modules; all orchestration, validation and review gates remain
the original implementation instead of a divergent copy of the workflow engine.
"""
import ast
from pathlib import Path
import sys
import tempfile

from windows_platform import enable_console
import os

PLATFORM_MODULES = {'fcntl', 'termios', 'tty', 'os', 'signal', 'select', 'subprocess'}


def load_engine():
    source = Path(__file__).resolve().parent.parent / 'lib' / 'workflow.py'
    tree = ast.parse(source.read_text(encoding='utf-8'), filename=str(source))
    for index, node in enumerate(tree.body):
        if isinstance(node, ast.Import) and len(node.names) == 1 and node.names[0].name in PLATFORM_MODULES:
            replacement = ast.ImportFrom(module='windows_platform', names=node.names, level=0)
            tree.body[index] = ast.copy_location(replacement, node)
        elif isinstance(node, ast.ImportFrom) and node.module == 'pathlib':
            tree.body[index] = ast.copy_location(ast.ImportFrom(module='windows_platform', names=node.names, level=0), node)
    ast.fix_missing_locations(tree)
    namespace = {'__name__': 'ccx_windows_workflow', '__file__': str(source)}
    exec(compile(tree, str(source), 'exec'), namespace)
    original_profiles = namespace['load_role_profiles']
    def load_role_profiles(config, profiles_dir):
        profiles = original_profiles(config, profiles_dir)
        if any(profile.get('CCX_PROVIDER') in ('kimi-cli', 'mcode') for profile in profiles.values()):
            raise namespace['WorkflowError']('Native Kimi/mcode team roles require Linux Bubblewrap; on Windows use kimi/minimax provider profiles through Claude Code.')
        return profiles
    namespace['load_role_profiles'] = load_role_profiles

    def adapt_command(text):
        return text.replace('python3 ', 'python ').replace('$PORT', '%PORT%')

    for name in ('detect_proof', 'proof_candidates', 'server_candidates'):
        original = namespace[name]
        def windows_commands(*args, _original=original, **kwargs):
            value = _original(*args, **kwargs)
            if isinstance(value, str):
                return adapt_command(value)
            return [(adapt_command(item[0]), item[1]) if isinstance(item, tuple) else adapt_command(item) for item in value]
        namespace[name] = windows_commands
    ui_class = namespace['TeamUI']
    original_enter = ui_class.enter
    def enter(self):
        namespace['select'].resize_callback = self._on_resize
        return original_enter(self)
    ui_class.enter = enter

    def enable_completion(self, repo):
        try:
            files = namespace['workspace_files'](repo)
        except (namespace['WorkflowError'], OSError):
            files = []
        self.completion_files = list(files)
        self.completion_repo = repo
        self.completion_ready = True
    ui_class.enable_completion = enable_completion

    def edit_task(self):
        # Windows editors cannot reopen NamedTemporaryFile while its handle is open.
        fd, name = tempfile.mkstemp(prefix='ccx-prompt-', suffix='.md')
        os.close(fd)
        path = Path(name)
        self.exit()
        try:
            editor = os.environ.get('VISUAL') or os.environ.get('EDITOR') or 'notepad.exe'
            result = namespace['subprocess'].run([editor, str(path)], check=False)
            if result.returncode:
                raise namespace['WorkflowError'](f'Editor exited with status {result.returncode}.')
            return path.read_text(encoding='utf-8')
        finally:
            path.unlink(missing_ok=True)
            self.enter()
    ui_class.edit_task = edit_task
    return namespace


def run(argv):
    enable_console()
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))
    os.environ.setdefault('EDITOR', 'notepad.exe')
    os.environ.setdefault('CCX_MOUSE', '0')
    engine = load_engine()
    previous = sys.argv
    try:
        sys.argv = ['ccx-workflow-windows', *argv]
        return engine['main']()
    finally:
        sys.argv = previous
