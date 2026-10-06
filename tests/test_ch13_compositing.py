"""Chapter 13 Compositing / Compositing Leaves: layer split and reassembly."""
import unittest
from unittest.mock import Mock, patch

import numpy as np
import skia

import browser


def paint_html(html, width=800):
    nodes = browser.HTMLParser(html).parse()
    rules = sorted(browser.DEFAULT_STYLE_SHEET, key=browser.cascade_priority)
    browser.style(nodes, rules)
    document = browser.DocumentLayout(nodes, width)
    document.layout()
    display_list = []
    browser.paint_tree(document, display_list)
    return document, tuple(display_list)


def render_page(html, compositing, measure=None):
    document, display_list = paint_html(html)
    # Same height Tab commits: the root block plus top and bottom margins.
    page = browser.CommitData("about:test", 0, document.height + 2 * browser.VSTEP,
                              display_list, width=800, tab_height=500)
    work = browser.RasterWork(
        raster_id=1, window_id=1, scene_epoch=1, active_tab_key=1,
        page_state=page, chrome_display_list=(), width=800, height=600,
        chrome_bottom=100, chrome_raster=True, tab_raster=True,
        estimator_tab=None, title="compositing",
    )
    state = browser.RasterWindowState()
    state.measure = measure
    with patch.object(browser, "COMPOSITING_ENABLED", compositing):
        pixels = state.render(work).pixels
    return state, np.frombuffer(pixels, np.uint8).astype(int)


def draw_list_items(items):
    for item in items:
        yield item
        if isinstance(item, browser.VisualEffect):
            yield from draw_list_items(item.children)


PLAIN_PAGE = "<div>first</div><div>second</div>"
OPACITY_PAGE = (
    '<div style="background-color:lightblue">plain block</div>'
    '<div style="opacity:0.5;background-color:orange">faded <b>box</b></div>'
    '<div style="mix-blend-mode:multiply;background-color:green">multiply</div>'
)


class CompositingLeaves(unittest.TestCase):
    def test_page_without_compositing_effects_is_one_layer(self):
        state, _ = render_page(PLAIN_PAGE, compositing=True)
        self.assertEqual(len(state.composited_layers), 1)
        self.assertIsInstance(state.draw_list[0], browser.DrawCompositedLayer)

    def test_opacity_and_blend_mode_split_page_into_layers(self):
        state, _ = render_page(OPACITY_PAGE, compositing=True)
        layers = state.composited_layers
        self.assertGreater(len(layers), 1)
        for layer in layers:
            self.assertIsNotNone(layer.surface, "every visible leaf is rastered")
            self.assertEqual(layer.surface.width(), int(layer.surface_rect.width()))

        drawn = list(draw_list_items(state.draw_list))
        layer_draws = [d for d in drawn if isinstance(d, browser.DrawCompositedLayer)]
        self.assertEqual([d.layer for d in layer_draws], layers,
                         "each layer is drawn exactly once, in paint order")
        self.assertFalse(any(isinstance(d, browser.PaintCommand) for d in drawn),
                         "paint commands live in layers, not in the draw list")
        opacities = [d.opacity for d in drawn if isinstance(d, browser.Blend)]
        self.assertIn(0.5, opacities, "opacity is applied in the draw phase")

    def test_reassembled_layers_match_direct_raster(self):
        _, composited = render_page(OPACITY_PAGE, compositing=True)
        _, direct = render_page(OPACITY_PAGE, compositing=False)
        # Antialiased text edges rastered onto a transparent layer may round
        # differently from text drawn straight onto its background.
        self.assertLessEqual(np.abs(composited - direct).max(), 2)

    def test_committed_display_list_is_not_mutated(self):
        _, display_list = paint_html(OPACITY_PAGE)
        before = [(item, list(item.children)) for item in draw_list_items(display_list)
                  if isinstance(item, browser.VisualEffect)]
        browser.composite_display_list(display_list)
        for item, children in before:
            self.assertEqual(item.children, children)

    def test_sibling_leaves_share_one_clone_of_their_ancestor(self):
        a = browser.DrawRect(skia.Rect.MakeLTRB(0, 0, 10, 10), "red")
        b = browser.DrawRect(skia.Rect.MakeLTRB(0, 10, 10, 20), "blue")
        fade = browser.Blend(0.5, None, [a, b])
        layers, draw_list = browser.composite_display_list([fade])
        self.assertEqual(len(layers), 2)
        self.assertEqual(len(draw_list), 1)
        self.assertIsNot(draw_list[0], fade)
        self.assertEqual([child.layer for child in draw_list[0].children], layers)

    def test_leaves_outside_interest_region_are_culled(self):
        near = browser.DrawRect(skia.Rect.MakeLTRB(0, 0, 10, 10), "red")
        far = browser.DrawRect(skia.Rect.MakeLTRB(0, 5000, 10, 5010), "blue")
        fade = browser.Blend(0.5, None, [near, far])
        interest = skia.Rect.MakeLTRB(0, 0, 800, 1000)
        layers, _ = browser.composite_display_list([fade], interest)
        self.assertEqual([layer.items for layer in layers], [[near]])

    def test_composited_scroll_maps_interest_into_scrolled_space(self):
        inner = browser.DrawRect(skia.Rect.MakeLTRB(0, 900, 10, 910), "red")
        scroller = browser.Scroll(skia.Rect.MakeLTRB(0, 0, 100, 100), 880,
                                  [browser.Blend(0.5, None, [inner])])
        interest = skia.Rect.MakeLTRB(0, 0, 800, 100)
        layers, _ = browser.composite_display_list([scroller], interest)
        self.assertEqual(len(layers), 1, "content scrolled into view is kept")

    def test_element_effects_are_applied_once_not_per_line_box(self):
        _, display_list = paint_html('<div style="opacity:0.5">faded text</div>')
        faded = [item for item in draw_list_items(display_list)
                 if isinstance(item, browser.Blend) and item.opacity < 1.0]
        self.assertEqual(len(faded), 1, "line boxes must not repeat the div's opacity")

        _, pixels = render_page('<div style="opacity:0.5;background-color:blue">x</div>',
                                compositing=True)
        # Inside the div, away from the text: 50% blue over white.
        r, g, b, _ = pixels.reshape(600, 800, 4)[100 + 20, 700]
        self.assertAlmostEqual(r, 128, delta=2)
        self.assertAlmostEqual(b, 255, delta=2)

    def test_last_line_is_inside_committed_document_height(self):
        html = "".join("<div>line {}</div>".format(i) for i in range(5))
        document, display_list = paint_html(html)
        bottom = max(item.rect.bottom() for item in draw_list_items(display_list)
                     if isinstance(item, browser.DrawText))
        state, _ = render_page(html, compositing=True)
        last = max(layer.surface_rect.bottom() for layer in state.composited_layers)
        self.assertGreaterEqual(last, bottom, "the last line is not clipped")

    def test_trace_records_composite_raster_and_draw_phases(self):
        measure = Mock()
        render_page(OPACITY_PAGE, compositing=True, measure=measure)
        started = [call.args[0] for call in measure.time.call_args_list]
        for phase in ("composite", "raster_layers", "draw"):
            self.assertIn(phase, started)
        self.assertLess(started.index("composite"), started.index("raster_layers"))
        self.assertLess(started.index("raster_layers"), started.index("draw"))
        names = [call.args[0] for call in measure.instant.call_args_list]
        self.assertIn("composited_layers", names)


if __name__ == "__main__":
    unittest.main()
