"""Stage and build the Windows router without editing the Unix checkout.

The current router baseline has two compile defects: a missing optional
stream_options initializer and a test serializing Result instead of its value.
Apply those compatibility repairs only to the disposable Windows build tree.
All router implementation and conformance tests otherwise come from router/.
"""
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
    request = destination / 'src' / 'translate' / 'request.rs'
    content = request.read_text(encoding='utf-8')
    if 'stream_options:' not in content.split('fn validate', 1)[0]:
        anchor = 'stream: Some(req.stream.unwrap_or(false)),'
        if content.count(anchor) != 1:
            raise ValueError('Router initializer changed; review the Windows build compatibility repair.')
        content = content.replace(anchor, anchor + '\n        stream_options: req.stream.unwrap_or(false).then_some(StreamOptions { include_usage: true }),', 1)
    content = content.replace('serde_json::to_value(to_openai(&req)).unwrap()',
                              'serde_json::to_value(to_openai(&req).unwrap()).unwrap()')
    request.write_text(content, encoding='utf-8', newline='\n')
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
