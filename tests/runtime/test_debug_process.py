"""重放 macOS 先上报资源跟踪器的 DAP 事件，确保断点落在业务进程。"""
from pathlib import Path
import queue
import sys
from types import SimpleNamespace

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from check_debug import Debug, application_subprocess


def test_resource_tracker_notification_does_not_capture_application_breakpoint(monkeypatch):
    commands = {
        101: ['python', '-c', 'from multiprocessing.resource_tracker import main; main(8)'],
        102: ['python', '-c', 'from multiprocessing.spawn import spawn_main; spawn_main()'],
    }
    monkeypatch.setattr(psutil, 'Process', lambda pid: SimpleNamespace(cmdline=lambda: commands[pid]))
    tracker = {'event': 'debugpyAttach', 'body': {'subProcessId': 101}}
    worker = {'event': 'debugpyAttach', 'body': {'subProcessId': 102}}
    debug = Debug.__new__(Debug)
    debug.pending = []
    debug.incoming = queue.Queue()
    debug.incoming.put(tracker)
    debug.incoming.put(worker)

    assert debug.wait(application_subprocess) == worker
    assert debug.pending == [tracker]


def test_exited_subprocess_is_not_selected(monkeypatch):
    def disappeared(pid):
        raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(psutil, 'Process', disappeared)
    assert not application_subprocess({'event': 'debugpyAttach', 'body': {'subProcessId': 103}})
