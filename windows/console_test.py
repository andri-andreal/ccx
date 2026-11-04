"""Exercise the workflow keyboard in an actual hidden Windows console."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback


class KeyEvent(ctypes.Structure):
    _fields_ = [('down', wintypes.BOOL), ('repeat', wintypes.WORD), ('key', wintypes.WORD),
                ('scan', wintypes.WORD), ('char', wintypes.WCHAR), ('control', wintypes.DWORD)]


class EventUnion(ctypes.Union):
    _fields_ = [('key', KeyEvent), ('padding', ctypes.c_byte * 16)]


class InputRecord(ctypes.Structure):
    _fields_ = [('type', wintypes.WORD), ('event', EventUnion)]


def main(result_path):
    import windows_platform as platform
    import workflow_windows
    import msvcrt
    # Explicit console devices make the test independent of its parent's pipes.
    sys.stdin = open('CONIN$', 'r', encoding='utf-8')
    sys.stdout = open('CONOUT$', 'w', encoding='utf-8')
    sys.stderr = sys.stdout
    os.environ['TERM'] = 'xterm-256color'
    os.environ['CCX_MOUSE'] = '0'
    platform.enable_console()
    trace = Path(result_path + '.trace')
    def record(message):
        with trace.open('a', encoding='utf-8') as log:
            log.write(message + '\n')
    original_read = platform.os.read
    def traced_read(fd, count):
        value = original_read(fd, count)
        record('read ' + repr(value))
        return value
    platform.os.read = traced_read
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel.WriteConsoleInputW.argtypes = [wintypes.HANDLE, ctypes.POINTER(InputRecord), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel.WriteConsoleInputW.restype = wintypes.BOOL
    handle = msvcrt.get_osfhandle(sys.stdin.fileno())

    def send(sequence):
        def inject():
            try:
                time.sleep(0.15)
                for char, virtual_key in sequence:
                    event = InputRecord(1, EventUnion(key=KeyEvent(True, 1, virtual_key, user.MapVirtualKeyW(virtual_key, 0), char, 0)))
                    written = wintypes.DWORD()
                    if not kernel.WriteConsoleInputW(handle, ctypes.byref(event), 1, ctypes.byref(written)):
                        raise ctypes.WinError(ctypes.get_last_error())
                    record('injected ' + repr(char) + ' ' + str(written.value))
                    time.sleep(0.015)
            except BaseException:
                record(traceback.format_exc())
        thread = threading.Thread(target=inject, daemon=True)
        thread.start()
        return thread

    engine = workflow_windows.load_engine()
    config = {'CCX_PLANNER': 'fixture', 'CCX_BUILDER': 'fixture', 'CCX_REVIEWER': 'fixture'}
    ui = engine['TeamUI'](config, None)
    state = platform.tcgetattr(sys.stdin.fileno())
    try:
        ui.enter()
        record('composer')
        send([(char, ord(char.upper()) if char.isascii() else 0) for char in 'hello 中文'] + [('\r', 13)])
        command = ui.read_command()
        record('command ' + repr(command))
        assert command == 'hello 中文', repr(command)
        send([('\x00', 0x28), ('\r', 13)])
        choice = ui.select_choice('Fixture picker', [('First', 1, ''), ('Second', 2, '')])
        record('choice ' + repr(choice))
        assert choice == 2, repr(choice)
        assert platform.tcgetattr(sys.stdin.fileno())[3] == state[3], 'Console mode was not restored.'
        result = {'ok': True, 'composer': command, 'picker': choice}
    finally:
        ui.exit()
    Path(result_path).write_text(json.dumps(result), encoding='utf-8')


if __name__ == '__main__':
    try:
        main(sys.argv[1])
    except BaseException:
        Path(sys.argv[1]).write_text(json.dumps({'ok': False, 'error': traceback.format_exc()}), encoding='utf-8')
        raise
