import os
import sys
import threading


class _ThreadLocalTee:
    def __init__(self, stream):
        self._stream = stream
        self._local = threading.local()
        self._lock = threading.RLock()

    def set_file(self, file_obj):
        stack = getattr(self._local, "file_stack", None)
        if stack is None:
            stack = []
            self._local.file_stack = stack
        stack.append(file_obj)

    def clear_file(self):
        stack = getattr(self._local, "file_stack", None)
        if stack:
            stack.pop()

    def _current_file(self):
        stack = getattr(self._local, "file_stack", None)
        if not stack:
            return None
        return stack[-1]

    def write(self, data):
        with self._lock:
            self._stream.write(data)
            file_obj = self._current_file()
            if file_obj is not None:
                file_obj.write(data)
        return len(data)

    def flush(self):
        with self._lock:
            self._stream.flush()
            file_obj = self._current_file()
            if file_obj is not None:
                file_obj.flush()

    def isatty(self):
        return self._stream.isatty()

    def fileno(self):
        return self._stream.fileno()

    def __getattr__(self, name):
        return getattr(self._stream, name)


_stdout_tee = None
_install_lock = threading.Lock()


def _install_stdout_tee():
    global _stdout_tee
    with _install_lock:
        if _stdout_tee is None:
            _stdout_tee = _ThreadLocalTee(sys.stdout)
            sys.stdout = _stdout_tee
    return _stdout_tee


class ThreadTeeLogger:
    def __init__(self, log_file, mode="a"):
        self.log_file = log_file
        self.mode = mode
        self._tee = None
        self._file = None

    def __enter__(self):
        log_dir = os.path.dirname(self.log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        self._tee = _install_stdout_tee()
        self._file = open(self.log_file, self.mode, buffering=1)
        self._tee.set_file(self._file)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self._tee is not None:
            self._tee.clear_file()
        if self._file is not None:
            self._file.close()
