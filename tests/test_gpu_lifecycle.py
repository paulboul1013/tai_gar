"""Independent error-path and context-ownership tests; no display is created."""

from contextlib import ExitStack
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import browser
from gpu_evidence import EvidenceRecorder, load_config


class CurrentContextFixture:
    """Fake SDL boundary retaining the actual context and negotiated attributes."""

    def __init__(self, renderer=b"llvmpipe (LLVM test)"):
        self.window = object()
        self.context = object()
        self.current = None
        self.deleted = []
        self.destroyed = []
        self.drawable = (1200, 900)
        self.negotiated = {
            browser.sdl2.SDL_GL_STENCIL_SIZE: 16,
            browser.sdl2.SDL_GL_MULTISAMPLEBUFFERS: 1,
            browser.sdl2.SDL_GL_MULTISAMPLESAMPLES: 4,
            browser.sdl2.SDL_GL_DOUBLEBUFFER: 1,
        }
        self.gl = SimpleNamespace(GL_VENDOR=1, GL_RENDERER=2, GL_VERSION=3)
        self.gl.glGetString = Mock(side_effect=lambda token: {
            1: b"Mesa", 2: renderer, 3: b"4.5 Mesa test"}[token])
        self.skia = Mock()
        self.skia.backend.return_value = browser.skia.GrBackendApi.kOpenGL
        self.skia.abandoned.return_value = False
        self.make_skia = Mock(return_value=self.skia)

    def make_current(self, window, context):
        self.current = context
        return 0

    def attribute(self, name, value):
        value._obj.value = self.negotiated.get(name, 8)
        return 0

    def size(self, window, width, height):
        width._obj.value, height._obj.value = self.drawable

    def __enter__(self):
        self.stack = ExitStack()
        sdl = browser.sdl2
        functions = {
            "SDL_GL_SetAttribute": lambda *args: 0,
            "SDL_CreateWindow": lambda *args: self.window,
            "SDL_GetWindowID": lambda window: 11,
            "SDL_GL_CreateContext": lambda window: self.context,
            "SDL_GL_MakeCurrent": self.make_current,
            "SDL_GL_GetAttribute": self.attribute,
            "SDL_GL_GetDrawableSize": self.size,
            "SDL_GL_SetSwapInterval": lambda interval: 0,
            "SDL_GL_GetSwapInterval": lambda: 0,
            "SDL_GetCurrentVideoDriver": lambda: b"fake-offscreen",
            "SDL_GL_DeleteContext": self.deleted.append,
            "SDL_DestroyWindow": self.destroyed.append,
        }
        for name, function in functions.items():
            self.stack.enter_context(patch.object(sdl, name, side_effect=function))
        self.stack.enter_context(patch.object(browser, "get_opengl_gl", return_value=self.gl))
        self.stack.enter_context(patch.object(browser.skia, "GrDirectContext",
                                             SimpleNamespace(MakeGL=self.make_skia)))
        self.stack.enter_context(patch.object(browser, "Chrome", return_value=Mock()))
        self.stack.enter_context(patch.object(browser, "RENDER_BACKEND", "gpu"))
        self.stack.enter_context(patch.dict(os.environ, {"BROWSER_GPU_QUERY": "0"}))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)


def app_for_test(strict=False):
    config = load_config({"BROWSER_RENDER_BACKEND": "gpu", "BROWSER_RASTER_MODE": "sync",
                          "BROWSER_GPU_STRICT": "1" if strict else "0"})
    return SimpleNamespace(measure=Mock(), evidence=EvidenceRecorder(config),
                           allocate_raster_window_id=lambda: 7, current_input_id=None,
                           running=True)


class GpuInitializationTests(unittest.TestCase):
    def test_first_tab_failure_is_recorded_as_product_failure(self):
        app = browser.BrowserApp.__new__(browser.BrowserApp)
        app.evidence = app_for_test().evidence
        app.windows, app.windows_by_id = [], {}
        window = SimpleNamespace(window_id=11, new_tab=Mock(side_effect=RuntimeError("first tab failed")))
        with patch.object(browser, "BrowserWindow", return_value=window):
            with self.assertRaisesRegex(RuntimeError, "first tab failed"):
                app.new_window(browser.URL("about:blank"))
        self.assertEqual(app.evidence.finalize()["gl_path_status"], "FAIL")
        self.assertEqual(app.evidence.data["failures"][0]["kind"], "product")

    def test_driver_probe_exception_deletes_context_and_window(self):
        with CurrentContextFixture() as fixture, patch.object(browser, "RENDER_CONFIG", SimpleNamespace(strict=False)):
            fixture.gl.glGetString.side_effect = RuntimeError("driver probe failed")
            with self.assertRaisesRegex(RuntimeError, "driver probe failed"):
                browser.BrowserWindow(app_for_test())
            self.assertEqual(fixture.deleted, [fixture.context])
            self.assertEqual(fixture.destroyed, [fixture.window])

    def test_skia_initialization_exception_deletes_context_and_window(self):
        with CurrentContextFixture() as fixture, patch.object(browser, "RENDER_CONFIG", SimpleNamespace(strict=False)):
            fixture.make_skia.side_effect = RuntimeError("Skia initialization failed")
            with self.assertRaisesRegex(RuntimeError, "Skia initialization failed"):
                browser.BrowserWindow(app_for_test())
            self.assertEqual(fixture.deleted, [fixture.context])
            self.assertEqual(fixture.destroyed, [fixture.window])

    def test_chrome_initialization_exception_also_deletes_context_and_window(self):
        with CurrentContextFixture() as fixture, patch.object(browser, "RENDER_CONFIG", SimpleNamespace(strict=False)):
            with patch.object(browser, "Chrome", side_effect=RuntimeError("chrome initialization failed")):
                with self.assertRaisesRegex(RuntimeError, "chrome initialization failed"):
                    browser.BrowserWindow(app_for_test())
            self.assertEqual(fixture.deleted, [fixture.context])
            self.assertEqual(fixture.destroyed, [fixture.window])

    def test_strict_software_rejection_preserves_actual_renderer_and_cleans_resources(self):
        app = app_for_test(strict=True)
        with CurrentContextFixture() as fixture, patch.object(browser, "RENDER_CONFIG", SimpleNamespace(strict=True)):
            with self.assertRaisesRegex(RuntimeError, "Strict GPU mode rejected software"):
                browser.BrowserWindow(app)
            fixture.make_skia.assert_not_called()
            self.assertEqual(fixture.deleted, [fixture.context])
            self.assertEqual(fixture.destroyed, [fixture.window])
        self.assertEqual(app.evidence.finalize()["hardware_status"], "FAIL")
        self.assertEqual(app.evidence.data["windows"]["11"]["gl_renderer"], "llvmpipe (LLVM test)")
        self.assertEqual(app.evidence.data["failures"][0]["kind"], "policy")

    def test_actual_attributes_and_drawable_are_recorded_instead_of_requested_values(self):
        app = app_for_test()
        with CurrentContextFixture() as fixture, patch.object(browser, "RENDER_CONFIG", SimpleNamespace(strict=False)):
            window = browser.BrowserWindow(app, width=800, height=600)
            actual = app.evidence.data["windows"]["11"]
            self.assertEqual(actual["drawable_size"], [1200, 900])
            self.assertEqual(actual["logical_size"], [800, 600])
            self.assertEqual(actual["attributes"]["STENCIL_SIZE"], 16)
            self.assertEqual(window.gpu_raster_state.stencil, 16)
            self.assertEqual(window.gpu_raster_state.samples, 4)
            self.assertEqual(actual["swap_interval_actual"], 0)
            window.release_gpu()


class GpuLifecycleTests(unittest.TestCase):
    def test_normal_session_without_evidence_does_not_accumulate_causal_input_history(self):
        app = browser.BrowserApp.__new__(browser.BrowserApp)
        app.evidence = SimpleNamespace(enabled=False, event=Mock())
        app.inputs = {}
        app.input_counter = 0
        observed_ids = []
        app._dispatch_event = lambda event: observed_ids.append(app.current_input_id)
        event = browser.sdl2.SDL_Event()
        event.type = browser.sdl2.SDL_MOUSEWHEEL
        with patch.object(browser.sdl2, "SDL_GetTicks") as ticks:
            for _ in range(20):
                app.dispatch_event(event)
        self.assertEqual(app.inputs, {})
        self.assertEqual(app.input_counter, 0)
        self.assertEqual(observed_ids, [None] * 20)
        app.evidence.event.assert_not_called()
        ticks.assert_not_called()

    def test_drawable_change_releases_surfaces_with_own_context_current(self):
        with CurrentContextFixture() as fixture, patch.object(browser, "RENDER_CONFIG", SimpleNamespace(strict=False)):
            window = browser.BrowserWindow(app_for_test())
            fixture.current = object()  # A second native window most recently rendered.
            fixture.drawable = (1600, 1200)
            owner_contexts = []
            window.gpu_raster_state.release = lambda: owner_contexts.append(fixture.current)
            window.refresh_drawable()
            self.assertEqual(owner_contexts, [fixture.context])
            self.assertEqual(window.gpu_raster_state.drawable_size, (1600, 1200))
            self.assertTrue(window.needs_tab_raster)
            window.release_gpu()

    def test_minimize_blocks_raster_and_restore_marks_frame_dirty(self):
        with CurrentContextFixture() as fixture, patch.object(browser, "RENDER_CONFIG", SimpleNamespace(strict=False)):
            window = browser.BrowserWindow(app_for_test())
            app = browser.BrowserApp.__new__(browser.BrowserApp)
            app.window_for_id = lambda window_id: window
            app.trace_input_dispatch_latency = lambda event: None
            event = browser.sdl2.SDL_Event()
            event.type = browser.sdl2.SDL_WINDOWEVENT
            event.window.windowID = 11
            event.window.event = browser.sdl2.SDL_WINDOWEVENT_MINIMIZED
            app._dispatch_event(event)
            self.assertTrue(window.minimized)
            self.assertIsNone(window._take_raster_work())
            event.window.event = browser.sdl2.SDL_WINDOWEVENT_RESTORED
            app._dispatch_event(event)
            self.assertFalse(window.minimized)
            self.assertTrue(window.needs_raster_and_draw)
            window.release_gpu()

    def test_context_loss_stops_app_and_lost_cleanup_avoids_make_current(self):
        app = app_for_test()
        with CurrentContextFixture() as fixture, patch.object(browser, "RENDER_CONFIG", SimpleNamespace(strict=False)):
            window = browser.BrowserWindow(app)
            fixture.skia.abandoned.return_value = True
            with self.assertRaisesRegex(RuntimeError, "stopping GPU process"):
                window.check_gpu_context()
            self.assertFalse(app.running)
            self.assertTrue(window.context_lost)
            with patch.object(browser.sdl2, "SDL_GL_MakeCurrent") as make:
                window.release_gpu()
                make.assert_not_called()
            fixture.skia.abandonContext.assert_called_once()
            self.assertEqual(fixture.deleted, [fixture.context])
            self.assertEqual(app.evidence.finalize()["gl_path_status"], "FAIL")

    def test_repeated_dirty_input_mutations_keep_both_causal_ids(self):
        window = browser.BrowserWindow.__new__(browser.BrowserWindow)
        window.app = SimpleNamespace(current_input_id=1)
        tasks = []
        tab = SimpleNamespace(scroll=0, needs_render=True, visual_effect_revision=0,
                              applied_input_ids=[], browser=SimpleNamespace(set_needs_animation_frame=Mock()),
                              task_runner=SimpleNamespace(schedule_task=lambda task: tasks.append(task) or True))
        for input_id in (1, 2):
            window.app.current_input_id = input_id
            window.schedule_tab_task(tab, browser.Tab.set_needs_render, tab)
            tasks.pop(0).run()
        self.assertEqual(tab.applied_input_ids, [1, 2])


if __name__ == "__main__":
    unittest.main()
