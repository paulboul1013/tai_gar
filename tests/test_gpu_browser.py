"""Browser-side GPU contracts without a display; live runs are separate."""
import json
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import browser


class BrowserGpuContracts(unittest.TestCase):
    def test_trace_has_real_os_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "trace.json")
            measure = browser.MeasureTime(path)
            measure.instant("marker")
            measure.finish()
            with open(path) as stream:
                events = json.load(stream)["traceEvents"]
        marker = next(event for event in events if event["name"] == "marker")
        self.assertEqual(marker["pid"], os.getpid())
        self.assertEqual(marker["tid"], threading.get_native_id())

    def test_wrong_owner_refused_before_sdl(self):
        window = browser.BrowserWindow.__new__(browser.BrowserWindow)
        window.owner_thread_id = -1
        window.sdl_window = object()
        window.gl_context = object()
        with patch.object(browser, "RENDER_BACKEND", "gpu"), patch.object(browser.sdl2, "SDL_GL_MakeCurrent") as make:
            with self.assertRaisesRegex(RuntimeError, "owner"):
                window.make_gl_current()
        make.assert_not_called()

    def test_navigation_result_cannot_present_after_new_load(self):
        window = browser.BrowserWindow.__new__(browser.BrowserWindow)
        window.lock = threading.RLock()
        window._closed = False
        window.sdl_window = object()
        window.scene_epoch = 1
        window.window_id = 1
        window.width, window.height = 800, 600
        window.active_tab = SimpleNamespace(navigation_generation=2)
        window.measure = Mock()
        window.present_raster_result = Mock()
        result = SimpleNamespace(scene_epoch=1, width=800, height=600,
                                 navigation_generation=1, active_tab_key=id(window.active_tab), frame_id=4)
        self.assertFalse(window._accept_raster_result(result))
        window.present_raster_result.assert_not_called()

    def test_gpu_composition_never_snapshots_cpu_pixels(self):
        context = Mock()
        state = browser.GpuRasterWindowState(context)
        state.root_surface = Mock()
        state._compose_scene = Mock()
        self.assertIsNone(state._compose_pixels(object()))
        state.root_surface.makeImageSnapshot.assert_not_called()
        state.root_surface.readPixels.assert_not_called()

    def test_lost_context_is_abandoned_before_releasing_surfaces(self):
        window = browser.BrowserWindow.__new__(browser.BrowserWindow)
        window.context_lost = True
        window.owner_thread_id = threading.get_native_id()
        window.skia_context = Mock()
        window.gpu_raster_state = Mock()
        window.gl_context = object()
        window.sdl_window = object()
        window.query_ring = None
        calls = []
        window.skia_context.abandonContext.side_effect = lambda: calls.append("abandon")
        window.gpu_raster_state.release.side_effect = lambda: calls.append("release")
        with patch.object(browser.sdl2, "SDL_GL_DeleteContext"), patch.object(browser.sdl2, "SDL_GL_MakeCurrent") as make:
            window.release_gpu()
        self.assertEqual(calls, ["abandon", "release"])
        make.assert_not_called()


if __name__ == "__main__":
    unittest.main()
