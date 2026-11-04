"""Build a native Windows GUI from a staged copy, leaving gui/ unchanged."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def stage():
    directory = ROOT / 'windows' / '.build' / 'gui'
    shutil.copytree(ROOT / 'gui', directory, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('target', 'node_modules', 'dist', '.git'))
    shutil.copyfile(ROOT / 'windows' / 'gui_launcher.rs', directory / 'core' / 'src' / 'launcher.rs')
    doctor = directory / 'core' / 'src' / 'doctor.rs'
    text = doctor.read_text(encoding='utf-8')
    anchor = 'let mut process = Command::new(&bin);'
    if text.count(anchor) != 1:
        raise ValueError('GUI doctor changed; review the Windows launcher adapter.')
    text = text.replace('use std::process::Command;\n', '')
    text = text.replace(anchor, 'let mut process = crate::launcher::ccx_command(&bin)?;')
    doctor.write_text(text, encoding='utf-8', newline='\n')
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test-core', action='store_true')
    parser.add_argument('--dev', action='store_true')
    parser.add_argument('--no-bundle', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('This build entry point is for native Windows only.')
    directory = stage()
    if args.test_core:
        return subprocess.call(['cargo', 'test', '--locked', '--manifest-path', str(directory / 'Cargo.toml'), '-p', 'ccx-core'])
    # npm.cmd is a package-manager shell command with only fixed, trusted args.
    npm = shutil.which('npm.cmd')
    if not npm:
        raise ValueError('Install Node.js and npm before building the GUI.')
    subprocess.run([npm, 'ci'], cwd=directory, check=True)
    if not args.dev:
        subprocess.run([npm, 'run', 'tauri', '--', 'icon', 'src-tauri/icons/icon.png'], cwd=directory, check=True)
    command = [npm, 'run', 'tauri', '--', 'dev' if args.dev else 'build']
    if args.no_bundle and not args.dev:
        command.append('--no-bundle')
    return subprocess.call(command, cwd=directory)


if __name__ == '__main__':
    raise SystemExit(main())
