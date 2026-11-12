"""Start the compiled Windows desktop in an isolated config and verify its window."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from windows_platform import kill_tree


def main():
    directory = Path(__file__).resolve().parent
    executable = directory / '.build' / 'gui' / 'target' / 'release' / 'ccx-gui.exe'
    if not executable.is_file():
        raise FileNotFoundError('Build the GUI first: python windows/build_gui.py --no-bundle')
    user = ctypes.WinDLL('user32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    with tempfile.TemporaryDirectory(prefix='ccx-gui-smoke-') as temporary:
        environment = {**os.environ, 'CCX_HOME': temporary,
                       'WEBVIEW2_USER_DATA_FOLDER': str(Path(temporary) / 'webview'),
                       'PATH': str(directory) + os.pathsep + os.environ.get('PATH', '')}
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        process = subprocess.Popen([str(executable)], env=environment, startupinfo=startup,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 30
            found = []
            @callback_type
            def visit(window, _):
                pid = wintypes.DWORD()
                user.GetWindowThreadProcessId(window, ctypes.byref(pid))
                if pid.value == process.pid:
                    title = ctypes.create_unicode_buffer(512)
                    user.GetWindowTextW(window, title, len(title))
                    if title.value.lower() == 'ccx':
                        found.append(title.value)
                return True
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f'GUI exited during startup: {process.returncode}')
                user.EnumWindows(visit, 0)
                if found:
                    time.sleep(1)
                    if process.poll() is not None:
                        raise RuntimeError('GUI exited after creating its window.')
                    print(json.dumps({'ok': True, 'window': found[0], 'executable': str(executable)}))
                    return 0
                time.sleep(0.1)
            raise RuntimeError('GUI did not create its main window.')
        finally:
            if process.poll() is None:
                kill_tree(process.pid)
                process.wait(timeout=10)


if __name__ == '__main__':
    raise SystemExit(main())
