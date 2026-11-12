"""Stage and build the shared router unchanged in the Windows build tree."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def stage():
    destination = ROOT / 'windows' / '.build' / 'router'
    destination.mkdir(parents=True, exist_ok=True)
    source = ROOT / 'router'
    shutil.copytree(source, destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('target', '.git'))
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test', action='store_true')
    parser.add_argument('--release', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('This build entry point is for native Windows only.')
    directory = stage()
    command = ['cargo', 'test' if args.test else 'build', '--locked', '--manifest-path', str(directory / 'Cargo.toml')]
    if args.release:
        command.append('--release')
    return subprocess.call(command)


if __name__ == '__main__':
    raise SystemExit(main())
