"""Windows console, process and file-lock adapters for the shared workflow engine."""
from __future__ import annotations

import ctypes
import csv
from ctypes import wintypes
import errno
import msvcrt
import os as _os
from pathlib import Path as _Path
import re
import shutil
import signal as _signal
import subprocess as _subprocess
import sys
import time
from types import SimpleNamespace


class Path(type(_Path())):
    """Keep workflow artifacts byte-identical to their approved UTF-8 content."""

    def write_text(self, data, encoding=None, errors=None, newline=None):
        return super().write_text(data, encoding=encoding or 'utf-8', errors=errors,
                                  newline='\n' if newline is None else newline)


def private_directory(directory):
    """Protect credentials using a Windows DACL rather than Unix chmod bits."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    identity = _subprocess.run(['whoami.exe', '/user', '/fo', 'csv', '/nh'],
                               capture_output=True, text=True, check=True)
    sid = next(csv.reader(identity.stdout.splitlines()))[-1]
    if not re.fullmatch(r'S-1-[0-9-]+', sid):
        raise ValueError('Cannot determine Windows account SID.')
    result = _subprocess.run(['icacls.exe', str(directory), '/inheritance:r', '/grant:r',
                             f'*{sid}:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F'],
                            capture_output=True)
    if result.returncode:
        raise OSError('Cannot restrict CCX configuration permissions.')


def command_args(command):
    """Avoid cmd.exe for npm shims: invoke their Node entry point directly.

    JSON schemas and prompts contain shell syntax, so passing them through a
    batch interpreter would corrupt arguments or execute unintended commands.
    Unknown batch wrappers fail closed rather than silently using shell=True.
    """
    command = list(map(str, command))
    if not command:
        raise ValueError('Empty command.')
    binary = shutil.which(command[0])
    if not binary:
        raise FileNotFoundError(f'Executable not found: {command[0]}')
    path = Path(binary)
    if path.suffix.lower() == '.py':
        return [sys.executable, '-X', 'utf8', str(path), *command[1:]]
    if path.suffix.lower() in ('.cmd', '.bat'):
        if path.name.lower() == 'ccx.cmd' and path.with_name('ccx.py').is_file():
            return [sys.executable, '-X', 'utf8', str(path.with_name('ccx.py')), *command[1:]]
        content = path.read_text(encoding='utf-8-sig', errors='replace')
        scripts = re.findall(r'"(?:%dp0%|%~dp0)([^"\r\n]+\.(?:js|cjs|mjs))"', content, re.I)
        if scripts:
            script = (path.parent / scripts[-1].replace('\\', '/').lstrip('/')).resolve()
            node = path.parent / 'node.exe'
            node_bin = str(node) if node.is_file() else shutil.which('node')
            if script.is_file() and node_bin:
                return [node_bin, str(script), *command[1:]]
        raise ValueError(f'Unsupported batch wrapper: {path.name}. Set CCX_*_BIN to an .exe or .py executable.')
    return [str(path), *command[1:]]


def kill_tree(pid, _sig=None):
    # taskkill targets the entire process tree, including grandchildren.
    _subprocess.run(['taskkill.exe', '/PID', str(pid), '/T', '/F'],
                    stdout=_subprocess.DEVNULL, stderr=_subprocess.DEVNULL, check=False)


class WindowsPopen(_subprocess.Popen):
    def __init__(self, args, *positional, **kwargs):
        if isinstance(args, (list, tuple)) and len(args) >= 3 and args[:2] == ['bash', '-c']:
            # Proof and API-server commands are deliberately shell commands.
            args = [_os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/s', '/c', args[2]]
        else:
            args = command_args(args)
        if kwargs.pop('start_new_session', False):
            kwargs['creationflags'] = kwargs.get('creationflags', 0) | _subprocess.CREATE_NEW_PROCESS_GROUP
        super().__init__(args, *positional, **kwargs)


def run(*popenargs, input=None, capture_output=False, timeout=None, check=False, **kwargs):
    # subprocess.run uses its module's Popen internally. Keep adaptation scoped
    # to this module; never monkey-patch the process-wide subprocess module.
    if input is not None:
        if kwargs.get('stdin') is not None:
            raise ValueError('stdin and input cannot both be supplied.')
        kwargs['stdin'] = _subprocess.PIPE
    if capture_output:
        kwargs['stdout'] = kwargs['stderr'] = _subprocess.PIPE
    with WindowsPopen(*popenargs, **kwargs) as process:
        try:
            stdout, stderr = process.communicate(input, timeout=timeout)
        except BaseException:
            kill_tree(process.pid)
            process.wait()
            raise
        result = _subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result


class Proxy:
    def __init__(self, module, **overrides):
        self._module = module
        self.__dict__.update(overrides)

    def __getattr__(self, name):
        return getattr(self._module, name)


subprocess = Proxy(_subprocess, Popen=WindowsPopen, run=run)
signal = Proxy(_signal, SIGKILL=9)

_pending = bytearray()
_console_size = None
_special = {'H': b'\x1b[A', 'P': b'\x1b[B', 'K': b'\x1b[D', 'M': b'\x1b[C',
            'G': b'\x1b[H', 'O': b'\x1b[F', 'I': b'\x1b[5~', 'Q': b'\x1b[6~',
            'S': b'\x1b[3~', '\x0f': b'\x1b[Z'}


def _poll_console():
    if msvcrt.kbhit():
        char = msvcrt.getwch()
        if char in ('\x00', '\xe0'):
            _pending.extend(_special.get(msvcrt.getwch(), b''))
        else:
            _pending.extend(('\n' if char == '\r' else char).encode('utf-8', errors='replace'))
    return bool(_pending)


def console_select(readers, writers, errors, timeout=None):
    global _console_size
    if readers != [sys.stdin.fileno()]:
        raise OSError('Windows console adapter only supports standard input.')
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        size = shutil.get_terminal_size()
        if _console_size != size:
            _console_size = size
            callback = getattr(select, 'resize_callback', None)
            if callback:
                callback()
        if _poll_console():
            return readers, [], []
        if deadline is not None and time.monotonic() >= deadline:
            return [], [], []
        time.sleep(0.005)


def console_read(fd, count):
    if fd != sys.stdin.fileno() or not sys.stdin.isatty():
        return _os.read(fd, count)
    while not _poll_console():
        time.sleep(0.005)
    chunk = bytes(_pending[:count])
    del _pending[:count]
    return chunk


os = Proxy(_os, read=console_read, killpg=kill_tree, O_NONBLOCK=0)
select = SimpleNamespace(select=console_select)

_kernel = ctypes.WinDLL('kernel32', use_last_error=True)
_kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_kernel.GetConsoleMode.restype = wintypes.BOOL
_kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel.SetConsoleMode.restype = wintypes.BOOL


def _get_mode(fd):
    mode = wintypes.DWORD()
    if not _kernel.GetConsoleMode(msvcrt.get_osfhandle(fd), ctypes.byref(mode)):
        raise OSError(ctypes.get_last_error(), 'GetConsoleMode failed')
    return mode.value


def _set_mode(fd, mode):
    if not _kernel.SetConsoleMode(msvcrt.get_osfhandle(fd), mode):
        raise OSError(ctypes.get_last_error(), 'SetConsoleMode failed')


def tcgetattr(fd):
    # Match the termios structure fields used by the shared engine.
    return [0, 0, 0, _get_mode(fd), 0, 0, [0] * 32]


def tcsetattr(fd, when, state):
    _set_mode(fd, state[3])


def setcbreak(fd):
    _set_mode(fd, _get_mode(fd) & ~(0x0002 | 0x0004))


termios = SimpleNamespace(error=OSError, tcgetattr=tcgetattr, tcsetattr=tcsetattr,
                          ICANON=0x0002, ECHO=0x0004, VMIN=0, VTIME=1, TCSANOW=0, TCSADRAIN=1)
tty = SimpleNamespace(setcbreak=setcbreak)


def flock(file, operation):
    file.seek(0)
    try:
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EDEADLK, errno.EAGAIN):
            raise BlockingIOError('Workflow lock is held.') from error
        raise


fcntl = SimpleNamespace(flock=flock, LOCK_EX=1, LOCK_NB=2, F_GETFL=3, F_SETFL=4,
                        fcntl=lambda *args: 0)


def enable_console():
    if sys.stdout.isatty():
        _set_mode(sys.stdout.fileno(), _get_mode(sys.stdout.fileno()) | 0x0004)
    _os.environ.setdefault('TERM', 'xterm-256color')
