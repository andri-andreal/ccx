"""Native CMD installer; does not require changing PowerShell execution policy."""
import argparse
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import winreg

import ccx


def add_to_path(directory):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
        try:
            current, kind = winreg.QueryValueEx(key, 'Path')
        except FileNotFoundError:
            current, kind = '', winreg.REG_EXPAND_SZ
        if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
            raise ValueError('User PATH has an unsupported registry type.')
        normalize = lambda value: os.path.normcase(os.path.normpath(os.path.expandvars(value)))
        if normalize(directory) not in [normalize(p) for p in current.split(';') if p]:
            updated = current.rstrip(';') + (';' if current else '') + directory
            winreg.SetValueEx(key, 'Path', 0, kind, updated)
    # Notify Explorer so newly opened terminals inherit the updated user PATH.
    send = ctypes.WinDLL('user32').SendMessageTimeoutW
    send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR,
                     wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    result = ctypes.c_size_t()
    send(0xffff, 0x001a, 0, 'Environment', 0x0002, 5000, ctypes.byref(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--add-to-path', action='store_true')
    parser.add_argument('--build-router', action='store_true')
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        parser.error('Python 3.10 or newer is required.')
    directory = Path(__file__).resolve().parent
    ccx.main(['--init-windows'])
    if args.build_router:
        subprocess.run([sys.executable, '-X', 'utf8', str(directory / 'build_router.py'), '--release'], check=True)
    if args.add_to_path:
        add_to_path(str(directory))
        print('User PATH updated. Open a new terminal to run ccx.')
    print('Windows launcher: ' + str(directory / 'ccx.cmd'))
    print('Keep this checkout in place. Install/authenticate your AI CLIs separately.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print('ccx installer: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
