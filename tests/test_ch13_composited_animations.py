"""Chapter 13 Composited Animations: opacity frames skip layout and raster."""
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

import browser
from test_ch13_transitions import TransitionTestCase


def effects(items):
    for item in items:
        if isinstance(item, browser.VisualEffect):
            yield item
            yield from effects(item.children)


def raster_work(page, tab_composite, frame_id=1, tab_key=1, width=800):
    return browser.RasterWork(
        raster_id=1, window_id=1, scene_epoch=1, active_tab_key=tab_key,
        page_state=page, chrome_display_list=(), width=width, height=600,
        chrome_bottom=100, chrome_raster=False, tab_raster=True,
        tab_composite=tab_composite, estimator_tab=None, title="anim",
        frame_id=frame_id,
    )


class CommittingTestCase(TransitionTestCase):
    def setUp(self):
        super().setUp()
        self.commits = []
        self.host.commit = lambda tab, data: self.commits.append(data) or True

    def frame(self, now):
        self.clock.now = now
        self.tab.run_animation_frame()
        return self.commits[-1]

    def box_blend(self, display_list):
        return next(e for e in effects(display_list)
                    if isinstance(e, browser.Blend) and e.node is self.box)


class BlendNodeKey(CommittingTestCase):
    def test_paint_keys_the_outer_blend_with_its_node(self):
        blend = self.box.blend_op
        self.assertIs(blend.node, self.box)
        self.assertIs(self.box_blend(self.tab.display_list), blend)
        self.assertIs(blend.clone([]).node, self.box)

    def test_overflow_clip_mask_has_no_node_key(self):
        self.load('<div id="box" style="overflow:clip">x</div>')
        masks = [e for e in effects(self.tab.display_list)
                 if isinstance(e, browser.Blend) and e.blend_mode == "destination-in"]
        self.assertEqual(len(masks), 1)
        self.assertIsNone(masks[0].node)

    def test_animated_blend_stays_in_draw_list_at_opacity_one(self):
        self.set_style("transition: opacity 2s; opacity: 0.1")
        blend = self.box_blend(self.tab.display_list)
        self.assertEqual(blend.opacity, 1.0, "first frame still shows the old value")
        self.assertTrue(blend.needs_compositing)
        _, draw_list = browser.composite_display_list(self.tab.display_list)
        keyed = [e for e in effects(draw_list) if getattr(e, "node", None) is self.box]
        self.assertEqual(len(keyed), 1)

    def test_static_opacity_one_blend_is_baked_into_a_layer(self):
        self.assertFalse(self.box_blend(self.tab.display_list).needs_compositing)


class CompositedUpdates(CommittingTestCase):
    def test_first_frame_is_structural(self):
        self.set_style("transition: opacity 2s; opacity: 0.1")
        self.assertIsNone(self.frame(100.0).composited_updates)

    def test_middle_frame_sends_the_new_blend(self):
        self.set_style("transition: opacity 2s; opacity: 0.1")
        self.frame(100.0)
        data = self.frame(101.0)
        self.assertEqual(list(data.composited_updates), [self.box])
        blend = data.composited_updates[self.box]
        self.assertAlmostEqual(blend.opacity, 0.55)
        self.assertIs(blend, self.box_blend(data.display_list))

    def test_last_frame_is_structural(self):
        self.set_style("transition: opacity 2s; opacity: 0.1")
        self.frame(100.0)
        self.frame(101.0)
        self.assertIsNone(self.frame(102.5).composited_updates)

    def test_scroll_only_frame_sends_empty_updates(self):
        self.frame(100.0)
        data = self.frame(100.1)
        self.assertIsNone(data.display_list)
        self.assertEqual(data.composited_updates, {})

    def test_direct_relayout_is_structural(self):
        self.set_style("transition: opacity 2s; opacity: 0.1")
        self.frame(100.0)
        self.clock.now = 101.0
        self.tab.run_css_animations()
        # scroll_focused_element() and others relayout without dirty flags.
        self.tab.relayout()
        self.tab.run_animation_frame()
        self.assertIsNone(self.commits[-1].composited_updates)

    def test_rejected_commit_keeps_structural_change(self):
        self.set_style("transition: opacity 2s; opacity: 0.1")
        self.host.commit = lambda tab, data: False
        self.clock.now = 100.0
        self.tab.run_animation_frame()
        self.host.commit = lambda tab, data: self.commits.append(data) or True
        self.assertIsNone(self.frame(101.0).composited_updates)


class FakeWindow:
    """Just the BrowserWindow state commit() and _take_raster_work() touch."""

    def __init__(self, tab):
        self.lock = threading.RLock()
        self._closed = False
        self.tabs = [tab]
        self.active_tab = tab
        self.committed_states = {}
        self.measure = Mock()
        self.discard_address_bar_edit_on_commit = False
        self.armed_frame_deadline = None
        self.needs_raster_and_draw = False
        self.needs_chrome_raster = False
        self.needs_tab_raster = False
        self.needs_tab_composite = False
        self.pending_frame_raster_tab = None
        self.raster_in_flight = False


class FakeTab:
    navigation_generation = 0


class CommitMerging(unittest.TestCase):
    def setUp(self):
        self.tab = FakeTab()
        self.window = FakeWindow(self.tab)

    def commit(self, updates, display_list=()):
        data = browser.CommitData("about:x", 0, 100, list(display_list),
                                  composited_updates=updates)
        browser.BrowserWindow.commit(self.window, self.tab, data)

    def test_draw_only_commit_does_not_need_composite(self):
        self.commit(None)
        self.window.needs_tab_composite = False
        self.commit({self.tab: None})
        self.assertTrue(self.window.needs_tab_raster)
        self.assertFalse(self.window.needs_tab_composite)

    def test_structural_commit_in_a_batch_wins(self):
        self.commit({self.tab: None})
        self.commit(None)
        self.commit({self.tab: None})
        self.assertTrue(self.window.needs_tab_composite)

    def test_failed_work_restores_composite_flag(self):
        work = raster_work(None, tab_composite=True)
        browser.BrowserWindow._restore_failed_raster_work(self.window, work)
        self.assertTrue(self.window.needs_tab_composite)


class DrawOnlyRaster(CommittingTestCase):
    PAGE = ('<div style="background-color:lightblue">static</div>'
            '<div id="box" style="transition: opacity 2s; opacity: 1;'
            'background-color:orange">box <b>text</b></div>')

    # Two blocks before the box share the body's ancestors and merge.
    MERGING_PAGE = '<div style="background-color:pink">second</div>' + PAGE

    def setUp(self):
        super().setUp()
        self.load(self.PAGE)
        self.measure_raster = Mock()
        self.state = browser.RasterWindowState()
        self.state.measure = self.measure_raster

    def page(self, data, scroll=None):
        scroll = data.scroll if scroll is None else scroll
        return browser.CommitData("about:x", scroll, data.height,
                                  data.display_list, width=800, tab_height=500)

    def raster(self, data, tab_composite, state=None, scroll=None):
        state = state or self.state
        result = state.render(raster_work(self.page(data, scroll), tab_composite))
        return np.frombuffer(result.pixels, np.uint8).astype(int)

    def modes(self):
        return [c.args[1]["mode"] for c in self.measure_raster.instant.call_args_list
                if c.args[0] == "composited_layers"]

    def start(self):
        self.set_style("transition: opacity 2s; opacity: 0.1; background-color:orange")
        first = self.frame(100.0)
        self.raster(first, tab_composite=True)

    def test_middle_frame_reuses_layer_pixels(self):
        self.start()
        surfaces = [layer.surface for layer in self.state.composited_layers]
        middle = self.frame(101.0)
        self.raster(middle, tab_composite=False)
        self.assertEqual(self.modes(), ["full", "draw_only"])
        self.assertEqual([layer.surface for layer in self.state.composited_layers],
                         surfaces, "no layer was rastered again")

    def test_draw_only_pixels_match_full_path(self):
        self.start()
        middle = self.frame(101.0)
        draw_only = self.raster(middle, tab_composite=False)
        full = self.raster(middle, tab_composite=True, state=browser.RasterWindowState())
        self.assertEqual(np.abs(draw_only - full).max(), 0)
        # 55% orange over white really is on screen, not the first frame's 100%.
        r, g, b, _ = draw_only.reshape(600, 800, 4)[100 + 40, 700]
        self.assertAlmostEqual(g, round(255 - 0.55 * (255 - 165)), delta=2)

    def test_trace_has_no_composite_or_raster_on_middle_frames(self):
        self.start()
        self.measure_raster.reset_mock()
        self.raster(self.frame(101.0), tab_composite=False)
        started = [c.args[0] for c in self.measure_raster.time.call_args_list]
        self.assertEqual(started, ["draw_list_refresh", "draw"])

    def test_moved_interest_region_takes_full_path(self):
        self.load(self.PAGE + "<div>line</div>" * 400)
        self.start()
        start = self.state.interest_start
        # Scroll far below the region the cached layers were rastered for.
        self.raster(self.frame(101.0), tab_composite=False, scroll=5000)
        self.assertNotEqual(self.state.interest_start, start)
        self.assertEqual(self.modes(), ["full", "full"])

    def test_tab_switch_drops_cached_layers(self):
        self.start()
        middle = self.frame(101.0)
        self.state.render(raster_work(self.page(middle), False, tab_key=2))
        self.assertEqual(self.modes(), ["full", "full"])

    def test_missing_blend_falls_back_to_full_path(self):
        self.start()
        self.load('<div id="box">other page</div>')
        self.raster(self.frame(101.0), tab_composite=False)
        self.assertEqual(self.modes(), ["full", "full"])

    def test_flag_off_rasters_every_frame(self):
        self.start()
        with patch.object(browser, "COMPOSITED_ANIMATIONS_ENABLED", False):
            self.raster(self.frame(101.0), tab_composite=False)
        self.assertEqual(self.modes(), ["full", "full"])

    def layer_events(self):
        return [c.args[1] for c in self.measure_raster.instant.call_args_list
                if c.args[0] == "composited_layers"]

    def test_draw_only_path_reuses_merged_layers(self):
        self.load(self.MERGING_PAGE)
        self.start()
        layers = list(self.state.composited_layers)
        self.assertTrue(any(len(layer.items) > 1 for layer in layers), "a layer was merged")
        surfaces = [layer.surface for layer in layers]
        self.raster(self.frame(101.0), tab_composite=False)
        self.assertEqual(self.modes(), ["full", "draw_only"])
        self.assertEqual(self.layer_events()[-1]["rastered"], 0)
        self.assertEqual(self.state.composited_layers, layers)
        self.assertEqual([layer.surface for layer in layers], surfaces)

    def test_trace_reports_merged_and_layer_pixels(self):
        self.load(self.MERGING_PAGE)
        self.start()
        self.raster(self.frame(101.0), tab_composite=False)
        full, draw_only = self.layer_events()
        layers = self.state.composited_layers
        self.assertEqual(full["merged"],
                         sum(len(layer.items) for layer in layers) - len(layers))
        self.assertGreater(full["merged"], 0)
        self.assertEqual(full["layer_pixels"], sum(
            int(layer.surface_rect.width()) * int(layer.surface_rect.height())
            for layer in layers if layer.surface_rect is not None))
        self.assertEqual(draw_only["merged"], full["merged"])
        self.assertEqual(draw_only["layer_pixels"], full["layer_pixels"])


if __name__ == "__main__":
    unittest.main()
