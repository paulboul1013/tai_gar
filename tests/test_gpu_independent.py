"""Independent GPU review checks. These tests create no SDL windows."""

import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import browser
from gpu_query import GLTimerQueryRing
from gpu_workload import NullMeasure, build_snapshots, fixture_resources


class WorkloadIdentityReview(unittest.TestCase):
    def test_external_animation_executes_on_the_real_tai_gar_js_bridge(self):
        scheduled = []
        host = SimpleNamespace(measure=NullMeasure(),
                               set_needs_animation_frame=lambda tab: scheduled.append(tab))
        tab = browser.Tab(host, 800, 500, set(), [])
        resources = fixture_resources("scroll_raf")
        tab.nodes = browser.HTMLParser(resources["index.html"]).parse()
        tab.url = browser.URL("http://127.0.0.1/fixture")
        tab.js = browser.JSContext(tab)
        self.addCleanup(tab.js.discard)
        target = next(node for node in browser.tree_to_list(tab.nodes, [])
                      if isinstance(node, browser.Element) and node.attributes.get("id") == "change")
        original_style = target.attributes["style"]
        tab.js.evaljs(resources["scene.js"])
        self.assertEqual(target.attributes["style"], original_style)
        tab.js.evaljs(browser.RAF_JS)
        self.assertNotEqual(target.attributes["style"], original_style)
        self.assertTrue(tab.needs_render)
        tab.js.evaljs(browser.RAF_JS)
        self.assertEqual(target.attributes["style"], original_style)
        self.assertGreaterEqual(len(scheduled), 3)

    def test_snapshot_hash_changes_when_actual_layout_geometry_changes(self):
        regular, regular_metadata = build_snapshots("small", count=2)
        actual_get_font = browser.get_font

        def larger_font(size, weight, style, family=None):
            return actual_get_font(size * 2, weight, style, family=family)

        with patch.object(browser, "get_font", larger_font):
            larger, larger_metadata = build_snapshots("small", count=2)
        self.assertNotEqual(regular[0].document_height, larger[0].document_height,
                            "the independent perturbation must change actual layout")
        self.assertNotEqual(regular_metadata["snapshot_hash"], larger_metadata["snapshot_hash"],
                            "a snapshot hash must identify rendered commands, including geometry")

    def test_unrelated_font_cache_entries_do_not_change_workload_font_identity(self):
        original_typefaces = dict(browser.TYPEFACES)
        try:
            _, before = build_snapshots("small", count=2)
            browser.get_font(19, "bold", "roman", family="DejaVu Sans Mono")
            _, after = build_snapshots("small", count=2)
            self.assertEqual(before["font"], after["font"],
                             "workload identity must describe fonts used by its display commands")
        finally:
            browser.TYPEFACES.clear()
            browser.TYPEFACES.update(original_typefaces)

    def test_real_browser_raster_proves_alternating_fixture_is_visibly_changed(self):
        snapshots, metadata = build_snapshots("text_rect", count=2)
        state = browser.RasterWindowState()
        pixels = []
        for index, snapshot in enumerate(snapshots):
            work = browser.RasterWork(
                raster_id=index + 1, window_id=1, scene_epoch=1, frame_id=index + 1,
                active_tab_key=1, page_state=snapshot, chrome_display_list=(),
                width=800, height=600, chrome_bottom=100, chrome_raster=index == 0,
                tab_raster=metadata["tab_raster_flags"][index], estimator_tab=None,
                title="independent review",
            )
            pixels.append(state.render(work).pixels)
        self.assertNotEqual(hashlib.sha256(pixels[0]).hexdigest(),
                            hashlib.sha256(pixels[1]).hexdigest())
        self.assertTrue(all(isinstance(snapshot.display_list, tuple) for snapshot in snapshots))


class QueryDevice:
    """A minimal device whose completion and query IDs are independently controlled."""
    GL_VERSION = 1
    GL_TIME_ELAPSED = 2
    GL_QUERY_COUNTER_BITS = 3
    GL_QUERY_RESULT_AVAILABLE = 4
    GL_QUERY_RESULT = 5

    def __init__(self):
        self.active = None
        self.calls = []
        self.complete = False

    def glGetString(self, name):
        return b"4.6 independent review device"

    def glGetQueryiv(self, target, name):
        return 64

    def glGenQueries(self, count):
        self.calls.append("allocate")
        return 11 if count == 1 else list(range(11, 11 + count))

    def glBeginQuery(self, target, query):
        self.active = query
        self.calls.append("begin")

    def glEndQuery(self, target):
        self.active = None
        self.calls.append("end")

    def glGetQueryObjectiv(self, query, name):
        self.calls.append("availability")
        return int(self.complete)

    def glGetQueryObjectui64v(self, query, name, output):
        if not self.complete:
            raise AssertionError("a pending result would block the renderer")
        self.calls.append("read64")
        output._obj.value = 2 ** 54 + 17

    def glDeleteQueries(self, count, queries):
        self.calls.append("delete")


class QueryContractReview(unittest.TestCase):
    def test_scalar_allocation_and_64_bit_result_preserve_frame_identity(self):
        device = QueryDevice()
        ring = GLTimerQueryRing(device, capacity=1)
        identity = "run:window:context:frame"
        self.assertTrue(ring.begin(identity))
        ring.end(lambda: device.calls.append("submit"))
        self.assertEqual(device.calls[1:4], ["begin", "submit", "end"])
        self.assertEqual(ring.poll(), [])
        self.assertNotIn("read64", device.calls)
        device.complete = True
        self.assertEqual(ring.poll(), [{"frame_id": identity, "elapsed_ns": 2 ** 54 + 17}])
        ring.close(timeout_s=0)

    def test_unresolved_query_is_not_fabricated_and_lost_close_makes_no_gl_calls(self):
        device = QueryDevice()
        ring = GLTimerQueryRing(device, capacity=1)
        ring.begin("rendered-before-loss")
        ring.end(lambda: None)
        before = list(device.calls)
        summary = ring.close(context_lost=True)
        self.assertEqual(device.calls, before)
        self.assertEqual(summary["completed"], 0)
        self.assertEqual(summary["unresolved"], 1)
        self.assertEqual(ring.samples, [])


if __name__ == "__main__":
    unittest.main()
