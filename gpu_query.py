"""Optional, asynchronous GL elapsed-time diagnostics for a current context.

Create and use a ring on the GL context owner thread. These samples describe GL
execution and never classify a renderer as hardware. Enable the ring only in a
diagnostic run, outside the acceptance benchmark. ``end`` must submit deferred
Skia drawing before ending the query. A lost context is closed without GL calls.
"""

from collections import deque
import ctypes
import re
import time


def _text(value):
    return value.decode("ascii", errors="replace") if isinstance(value, bytes) else str(value or "")


def _integer(value):
    if hasattr(value, "item"):
        return int(value.item())
    if isinstance(value, (tuple, list)):
        return int(value[0])
    return int(value)


def probe_timer_query(gl):
    """Check desktop version/extensions, loaded entry points, and counter bits."""
    evidence = {"status": "UNAVAILABLE", "reason": None, "counter_bits": None,
                "version": None, "timer_extension": False}
    try:
        version = _text(gl.glGetString(gl.GL_VERSION))
        evidence["version"] = version
        match = re.match(r"^(\d+)\.(\d+)", version)
        if not match:
            evidence["reason"] = "desktop OpenGL version unavailable"
            return evidence
        major, minor = map(int, match.groups())
        extensions = set()
        if (major, minor) < (3, 3):
            if major >= 3:
                count = _integer(gl.glGetIntegerv(gl.GL_NUM_EXTENSIONS))
                extensions = {_text(gl.glGetStringi(gl.GL_EXTENSIONS, i)) for i in range(count)}
            else:
                extensions = set(_text(gl.glGetString(gl.GL_EXTENSIONS)).split())
        evidence["timer_extension"] = "GL_ARB_timer_query" in extensions
        if (major, minor) < (3, 3) and not evidence["timer_extension"]:
            evidence["reason"] = "OpenGL 3.3 or GL_ARB_timer_query required"
            return evidence
        functions = ("glGetQueryiv", "glGenQueries", "glBeginQuery", "glEndQuery",
                     "glGetQueryObjectiv", "glGetQueryObjectui64v", "glDeleteQueries")
        for name in functions:
            entry = getattr(gl, name, None)
            if not callable(entry) or not bool(entry):
                evidence["reason"] = "missing entry point: " + name
                return evidence
        bits = _integer(gl.glGetQueryiv(gl.GL_TIME_ELAPSED, gl.GL_QUERY_COUNTER_BITS))
        evidence["counter_bits"] = bits
        if bits <= 0:
            evidence["reason"] = "GL_TIME_ELAPSED counter has no useful bits"
            return evidence
        evidence.update(status="AVAILABLE", reason=None)
    except Exception as exc:
        evidence["reason"] = "timer query capability probe failed: " + str(exc)
    return evidence


class GLTimerQueryRing:
    """A fixed-size ring; full rings skip measurement instead of stalling frames.

    ``emit(name, payload)`` receives capability, sample, skipped-frame, and cleanup
    records. Frame identity comes from the caller's run/window/context/frame IDs.
    ``samples`` preserves completed results; unresolved queries never become zero.
    """

    def __init__(self, gl, capacity=4, emit=None):
        if not 1 <= int(capacity) <= 64:
            raise ValueError("query capacity must be between 1 and 64")
        self.gl = gl
        self.emit = emit
        self.capability = probe_timer_query(gl)
        self.samples = []
        self.skipped = 0
        self.closed = False
        self._ids = []
        self._free = deque()
        self._pending = deque()
        self._active = None
        self._summary = None
        if self.capability["status"] == "AVAILABLE":
            try:
                ids = gl.glGenQueries(int(capacity))
                try:
                    self._ids = [int(value) for value in ids]
                except TypeError:
                    self._ids = [_integer(ids)]
                if len(self._ids) != int(capacity) or any(query <= 0 for query in self._ids):
                    raise RuntimeError("query allocation returned invalid IDs")
                self._free.extend(self._ids)
            except Exception as exc:
                self.capability.update(status="UNAVAILABLE", reason="query allocation failed: " + str(exc))
        self._emit("gpu_query_capability", self.capability)

    def _emit(self, name, payload):
        if self.emit is not None:
            self.emit(name, dict(payload))

    def begin(self, frame_id):
        if self.closed or self.capability["status"] != "AVAILABLE":
            return False
        if self._active is not None:
            raise RuntimeError("GL_TIME_ELAPSED query already active")
        self.poll()
        if not self._free:
            self.skipped += 1
            self._emit("gpu_query_skipped", {"frame_id": frame_id, "reason": "ring full"})
            return False
        query = self._free.popleft()
        try:
            self.gl.glBeginQuery(self.gl.GL_TIME_ELAPSED, query)
        except Exception:
            self._free.appendleft(query)
            raise
        self._active = (query, frame_id)
        return True

    def end(self, submit):
        """Submit Skia commands within the active query, then end it."""
        if self._active is None or self.closed:
            return False
        if not callable(submit):
            raise TypeError("submit must be a callable that submits deferred drawing")
        query, frame_id = self._active
        try:
            submit()
        except BaseException:
            self.gl.glEndQuery(self.gl.GL_TIME_ELAPSED)
            self._active = None
            self._free.append(query)
            raise
        self.gl.glEndQuery(self.gl.GL_TIME_ELAPSED)
        self._active = None
        self._pending.append((query, frame_id))
        return True

    def poll(self):
        """Retrieve available 64-bit results only; never wait on a pending query."""
        if self.closed:
            return []
        completed = []
        while self._pending:
            query, frame_id = self._pending[0]
            available = self.gl.glGetQueryObjectiv(query, self.gl.GL_QUERY_RESULT_AVAILABLE)
            if not _integer(available):
                break
            # PyOpenGL's automatic uint64 numpy allocation is unavailable on
            # some bindings. Supply a native 64-bit output buffer explicitly.
            output = ctypes.c_uint64()
            self.gl.glGetQueryObjectui64v(query, self.gl.GL_QUERY_RESULT, ctypes.byref(output))
            elapsed = output.value
            sample = {"frame_id": frame_id, "elapsed_ns": elapsed}
            self._pending.popleft()
            self._free.append(query)
            self.samples.append(sample)
            completed.append(sample)
            self._emit("gpu_query_sample", sample)
        return completed

    def close(self, context_lost=False, timeout_s=0.05):
        """Bounded pending-result drain and deletion; lost contexts need no calls."""
        if self.closed:
            return dict(self._summary)
        timeout_s = max(0.0, min(float(timeout_s), 1.0))
        active_unresolved = int(self._active is not None)
        error = None
        if not context_lost:
            try:
                if self._active is not None:
                    self.gl.glEndQuery(self.gl.GL_TIME_ELAPSED)
                    self._active = None
                deadline = time.monotonic() + timeout_s
                for _ in range(1024):
                    self.poll()
                    remaining = deadline - time.monotonic()
                    if not self._pending or remaining <= 0:
                        break
                    time.sleep(min(0.001, remaining))
            except Exception as exc:
                error = str(exc)
            finally:
                if self._ids:
                    try:
                        self.gl.glDeleteQueries(len(self._ids), self._ids)
                    except Exception as exc:
                        error = str(exc)
        self._summary = {"status": self.capability["status"], "completed": len(self.samples),
                         "skipped": self.skipped, "unresolved": len(self._pending) + active_unresolved,
                         "context_lost": bool(context_lost), "cleanup_error": error}
        self._pending.clear()
        self._free.clear()
        self._ids.clear()
        self._active = None
        self.closed = True
        self._emit("gpu_query_cleanup", self._summary)
        return dict(self._summary)
