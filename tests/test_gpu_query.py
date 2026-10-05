"""Timer-query contracts without a display; the fake models query completion."""

import unittest

from gpu_query import GLTimerQueryRing, probe_timer_query


class FakeGL:
    GL_VERSION = 1
    GL_EXTENSIONS = 2
    GL_NUM_EXTENSIONS = 3
    GL_TIME_ELAPSED = 4
    GL_QUERY_COUNTER_BITS = 5
    GL_QUERY_RESULT_AVAILABLE = 6
    GL_QUERY_RESULT = 7

    def __init__(self, version=b"3.3 Mesa", bits=64, extensions=()):
        self.version = version
        self.bits = bits
        self.extensions = extensions
        self.queries = {}
        self.active = None
        self.calls = []
        self.next_id = 1

    def glGetString(self, name):
        return self.version if name == self.GL_VERSION else b" ".join(self.extensions)

    def glGetIntegerv(self, name):
        return len(self.extensions)

    def glGetStringi(self, name, index):
        return self.extensions[index]

    def glGetQueryiv(self, target, name):
        return self.bits

    def glGenQueries(self, count):
        ids = list(range(self.next_id, self.next_id + count))
        self.next_id += count
        self.queries.update({query: None for query in ids})
        return ids

    def glBeginQuery(self, target, query):
        assert self.active is None
        self.active = query
        self.queries[query] = None
        self.calls.append("begin")

    def glEndQuery(self, target):
        assert self.active is not None
        self.active = None
        self.calls.append("end")

    def glGetQueryObjectiv(self, query, name):
        assert query != self.active
        self.calls.append("availability")
        return self.queries[query] is not None

    def glGetQueryObjectui64v(self, query, name, output):
        assert self.queries[query] is not None, "result read would block"
        self.calls.append("result64")
        output._obj.value = self.queries[query]

    def glDeleteQueries(self, count, queries):
        self.calls.append("delete")
        for query in queries:
            del self.queries[query]


class QueryCapabilityTests(unittest.TestCase):
    def test_old_version_needs_timer_extension(self):
        self.assertEqual(probe_timer_query(FakeGL(version=b"3.2 Mesa"))["status"], "UNAVAILABLE")
        gl = FakeGL(version=b"3.2 Mesa", extensions=(b"GL_ARB_timer_query",))
        self.assertEqual(probe_timer_query(gl)["status"], "AVAILABLE")

    def test_zero_counter_bits_and_missing_entry_point_are_unavailable(self):
        self.assertEqual(probe_timer_query(FakeGL(bits=0))["status"], "UNAVAILABLE")
        gl = FakeGL()
        gl.glGetQueryObjectui64v = None
        self.assertEqual(probe_timer_query(gl)["status"], "UNAVAILABLE")

    def test_es_is_not_assumed_to_support_desktop_timer_queries(self):
        self.assertEqual(probe_timer_query(FakeGL(version=b"OpenGL ES 3.3"))["status"], "UNAVAILABLE")


class QueryRingTests(unittest.TestCase):
    def test_binding_with_missing_automatic_uint64_dtype_uses_explicit_buffer(self):
        class PointerGL(FakeGL):
            def glGetQueryObjectui64v(self, query, name, output=None):
                if output is None:
                    raise KeyError("GL_UNSIGNED_INT64_AMD")
                output._obj.value = self.queries[query]
        gl = PointerGL()
        ring = GLTimerQueryRing(gl, capacity=1)
        ring.begin("actual-binding-regression")
        ring.end(lambda: None)
        gl.queries[1] = 2 ** 54 + 17
        self.assertEqual(ring.poll(), [{"frame_id": "actual-binding-regression", "elapsed_ns": 2 ** 54 + 17}])
        ring.close()

    def test_deferred_submit_is_inside_query_and_result_is_read_only_when_ready(self):
        gl = FakeGL()
        ring = GLTimerQueryRing(gl, capacity=2)
        self.assertTrue(ring.begin("frame-1"))
        self.assertTrue(ring.end(lambda: gl.calls.append("submit")))
        self.assertEqual(gl.calls[:3], ["begin", "submit", "end"])
        self.assertEqual(ring.poll(), [])
        self.assertNotIn("result64", gl.calls)
        gl.queries[1] = 2 ** 40
        self.assertEqual(ring.poll(), [{"frame_id": "frame-1", "elapsed_ns": 2 ** 40}])
        ring.close()
        self.assertFalse(gl.queries)

    def test_pending_ring_is_bounded_and_does_not_overwrite_unread_result(self):
        gl = FakeGL()
        ring = GLTimerQueryRing(gl, capacity=1)
        ring.begin("first")
        ring.end(lambda: None)
        self.assertFalse(ring.begin("second"))
        self.assertEqual(ring.skipped, 1)
        self.assertEqual(len(gl.queries), 1)
        gl.queries[1] = 99
        self.assertTrue(ring.begin("third"))
        self.assertEqual(ring.samples, [{"frame_id": "first", "elapsed_ns": 99}])
        ring.end(lambda: None)
        summary = ring.close(timeout_s=0)
        self.assertEqual(summary["unresolved"], 1)
        self.assertFalse(gl.queries)

    def test_lost_context_cleanup_performs_no_gl_calls(self):
        gl = FakeGL()
        ring = GLTimerQueryRing(gl)
        ring.begin("lost")
        before = list(gl.calls)
        summary = ring.close(context_lost=True)
        self.assertEqual(gl.calls, before)
        self.assertEqual(summary["unresolved"], 1)
        self.assertTrue(summary["context_lost"])
        self.assertFalse(ring.begin("after-close"))

    def test_unavailable_probe_does_not_allocate_or_create_zero_samples(self):
        gl = FakeGL(bits=0)
        ring = GLTimerQueryRing(gl)
        self.assertFalse(gl.queries)
        self.assertFalse(ring.begin("frame"))
        self.assertFalse(ring.end(lambda: None))
        self.assertEqual(ring.poll(), [])
        self.assertEqual(ring.samples, [])

    def test_query_must_end_even_when_submit_fails_without_accepting_a_sample(self):
        gl = FakeGL()
        ring = GLTimerQueryRing(gl)
        ring.begin("broken")
        def broken_submit():
            raise RuntimeError("submit failed")
        with self.assertRaisesRegex(RuntimeError, "submit failed"):
            ring.end(broken_submit)
        self.assertIsNone(gl.active)
        self.assertEqual(ring.samples, [])
        ring.close(timeout_s=0)

    def test_close_is_idempotent(self):
        gl = FakeGL()
        ring = GLTimerQueryRing(gl)
        ring.close()
        before = list(gl.calls)
        ring.close()
        self.assertEqual(gl.calls, before)


if __name__ == "__main__":
    unittest.main()
