"""Opt-in real SDL lifecycle checks. This file is not unittest discovery input.

python3 -B tests/live_gpu_checks.py --output /tmp/tai-gar-live-lifecycle.json
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    os.environ.update(BROWSER_RENDER_BACKEND="gpu", BROWSER_RASTER_MODE="sync",
        BROWSER_GPU_EVIDENCE=args.output + ".evidence.json", BROWSER_TRACE_FILE=args.output + ".trace.json")
    import browser
    from gpu_workload import fixture_server
    app = browser.BrowserApp()
    results = {}
    event = browser.sdl2.SDL_Event()
    def pump_until(predicate):
        deadline = time.monotonic() + 15
        while not predicate():
            if time.monotonic() >= deadline:
                raise AssertionError("required lifecycle work timed out")
            while browser.sdl2.SDL_PollEvent(ctypes.byref(event)):
                app.dispatch_event(event)
            app._service_browser_work()
            browser.sdl2.SDL_Delay(1)
    def presented(window, generation=None, scene_epoch=None):
        return any(record["window_id"] == window.window_id and
            (generation is None or record["navigation_generation"] == generation) and
            (scene_epoch is None or record["scene_epoch"] == scene_epoch)
            for record in app.evidence.data["frames"])
    try:
        with fixture_server("small") as url:
            first = app.new_window(browser.URL(url))
            second = app.new_window(browser.URL(url))
            pump_until(lambda: presented(first) and presented(second))
            first.make_gl_current()
            second.make_gl_current()
            results["dual_window_context_switch"] = "PASS"
            old_size = first.drawable_size
            browser.sdl2.SDL_SetWindowSize(first.sdl_window, 900, 650)
            pump_until(lambda: first.width == 900 and first.height == 650 and first.drawable_size != old_size)
            before = len(app.evidence.data["frames"])
            pump_until(lambda: any(f["window_id"] == first.window_id for f in app.evidence.data["frames"][before:]))
            results["resize"] = "PASS"
            results["resize_dpi"] = "PENDING"  # Physical DPI transition was not induced.
            browser.sdl2.SDL_MinimizeWindow(first.sdl_window)
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                while browser.sdl2.SDL_PollEvent(ctypes.byref(event)):
                    app.dispatch_event(event)
                browser.sdl2.SDL_Delay(1)
            minimized = first.minimized
            browser.sdl2.SDL_RestoreWindow(first.sdl_window)
            if minimized:
                pump_until(lambda: not first.minimized)
            results["minimize_restore"] = "PASS" if minimized else "PENDING"
            original = first.active_tab
            alternate = first.new_tab(browser.URL(url + "?alternate"))
            pump_until(lambda: first.active_tab is alternate and presented(first, alternate.navigation_generation, first.scene_epoch))
            first.set_active_tab(original)
            first.schedule_load(browser.URL(url + "?navigation"), tab=original)
            pump_until(lambda: original.navigation_generation >= 2 and presented(first, original.navigation_generation))
            results["tab_navigation"] = "PASS"
            first.set_needs_raster_and_draw(chrome=True, tab=True)
            first.maybe_start_raster_and_draw()
            first.close()  # Close after commands were submitted, without a tail glFinish.
            assert first.gl_context is None and first.sdl_window is None
            results["close_during_draw"] = "PASS"
            native_destroy = browser.sdl2.SDL_DestroyWindow
            destroyed = []
            def destroy(window):
                destroyed.append(window)
                native_destroy(window)
            with patch.object(browser.sdl2, "SDL_GL_CreateContext", return_value=None), \
                 patch.object(browser.sdl2, "SDL_DestroyWindow", side_effect=destroy):
                try:
                    browser.BrowserWindow(app)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("injected GL initialization failure was accepted")
            assert len(destroyed) == 1
            results["initialization_failure_cleanup"] = "PASS"
            second.skia_context.abandonContext()
            try:
                second.check_gpu_context()
            except RuntimeError:
                assert second.context_lost and not app.running
            else:
                raise AssertionError("injected context loss was accepted")
            with patch.object(browser.sdl2, "SDL_GL_MakeCurrent", wraps=browser.sdl2.SDL_GL_MakeCurrent) as make:
                second.close()
                assert make.call_count == 0
            results["context_loss_stop_cleanup"] = "PASS"
            results["fault_assertion_status"] = "PASS"
    finally:
        app.shutdown()
        Path(args.output).write_text(json.dumps(dict(operations=results,
            expected_negative="context loss deliberately injected; raw run records the stop",
            gl_path_status="PENDING", hardware_status=app.evidence.data["hardware_status"]), indent=2) + "\n")


if __name__ == "__main__":
    main()
