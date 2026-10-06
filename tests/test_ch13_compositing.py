"""Chapter 13 Compositing / Compositing Leaves: layer split and reassembly."""
import time
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


def rect(left, top, right, bottom, color="red"):
    return browser.DrawRect(skia.Rect.MakeLTRB(left, top, right, bottom), color)


def raster_direct(display_list, width=100, height=60):
    surface = skia.Surface(width, height)
    canvas = surface.getCanvas()
    canvas.clear(skia.ColorWHITE)
    for item in display_list:
        item.execute(canvas)
    return surface.makeImageSnapshot().toarray().astype(int)


def raster_composited(display_list, width=100, height=60):
    layers, draw_list = browser.composite_display_list(display_list)
    for layer in layers:
        layer.raster(skia.Surface)
    return layers, raster_direct(draw_list, width, height)


def overlap_example(gap=0):
    """Plan section 2.3: two paragraphs, a faded box, a third paragraph.

    The third paragraph touches the box unless gap moves it further down.
    """
    first, second = rect(0, 0, 50, 8, "red"), rect(0, 8, 50, 16, "blue")
    box = rect(0, 16, 50, 30, "orange")
    third = rect(0, 30 + gap, 50, 40 + gap, "green")
    body = browser.Blend(0.9, None, [first, second, browser.Blend(0.5, None, [box]), third])
    return body, (first, second, box, third)


def scenario_pages(opacity):
    """The verification pages, without the animation script."""
    box = ('<div id="box" style="opacity: {}; background-color: orange">'
           'animated box</div>'.format(opacity))
    a = '<div style="background-color: lightblue">block A</div>'
    gap = '<div style="height: {}px"></div>'.format
    paras = "".join("<p>Paragraph {} filler text for the heavy layer merging page.</p>"
                    .format(i) for i in range(120))
    scrolled = "".join("<p>Scrolled paragraph {}.</p>".format(i) for i in range(10))
    green = '<div style="background-color: lightgreen">{}</div>'.format
    return {
        "small": a + box + green("third block"),
        "heavy": a + box + paras,
        "wide": a + box + green("third block") + paras,
        "touch": a + box + green("block C touches the box")
                 + '<div style="background-color: pink">block D</div>',
        "gap": a + gap(20) + box + gap(20) + green("block C has a gap"),
        "blur": a + gap(30) + '<div style="filter: blur(4px)">' + box + "</div>" + gap(6)
                + green("block C is inside the blur spread") + gap(30)
                + '<div style="background-color: pink">block D</div>',
        "blend": a + gap(20) + box + gap(20)
                 + '<div style="mix-blend-mode: multiply; background-color: yellow">'
                   "multiply block</div>" + gap(20) + green("block C"),
        "scroll": a + gap(20)
                  + '<div style="overflow: scroll; height: 100px; background-color: white">'
                  + box + scrolled + "</div>" + gap(20) + green("block C below the scroller"),
    }


# Layer counts the plan's rules predict with the box at opacity 0.5.
PREDICTED_LAYERS = {"small": 3, "heavy": 3, "wide": 3, "touch": 3, "gap": 2,
                    "blur": 3, "blend": 3, "scroll": 5}

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
        a = rect(0, 0, 10, 10, "red")
        b = rect(0, 10, 10, 20, "blue")
        fade = browser.Blend(0.5, None, [a, browser.Blend(0.5, None, [b])])
        layers, draw_list = browser.composite_display_list([fade])
        self.assertEqual(len(layers), 2)
        self.assertEqual(len(draw_list), 1, "one clone of fade")
        self.assertIsNot(draw_list[0], fade)
        self.assertEqual(len(draw_list[0].children), 2)
        self.assertIs(draw_list[0].children[0].layer, layers[0])
        self.assertIs(draw_list[0].children[1].children[0].layer, layers[1])

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


class LayerMerging(unittest.TestCase):
    def test_sibling_leaves_with_same_ancestors_merge(self):
        a = rect(0, 0, 10, 10, "red")
        b = rect(0, 10, 10, 20, "blue")
        fade = browser.Blend(0.5, None, [a, b])
        layers, draw_list = browser.composite_display_list([fade])
        self.assertEqual([layer.items for layer in layers], [[a, b]])
        self.assertEqual(len(draw_list), 1)
        self.assertEqual([child.layer for child in draw_list[0].children], layers)

    def test_can_merge_requires_identical_ancestor_objects(self):
        outer = browser.Blend(0.5, None, [])
        inner = browser.Blend(0.5, None, [])
        layer = browser.CompositedLayer([rect(0, 0, 10, 10)], ancestors=(outer, inner))
        self.assertTrue(layer.can_merge((outer, inner)))
        twin = browser.Blend(0.5, None, [])
        self.assertFalse(layer.can_merge((outer, twin)), "equal but not the same object")
        self.assertFalse(layer.can_merge((outer,)))
        self.assertFalse(layer.can_merge((outer, inner, twin)))

    def test_add_grows_shared_bounds(self):
        a = rect(0, 0, 10, 10)
        b = rect(0, 30, 20, 40)
        layer = browser.CompositedLayer([a])
        draw = browser.DrawCompositedLayer(layer)
        layer.add(b)
        self.assertEqual(layer.items, [a, b])
        self.assertEqual(draw.rect, skia.Rect.MakeLTRB(0, 0, 20, 40))
        self.assertEqual(a.rect, skia.Rect.MakeLTRB(0, 0, 10, 10), "committed rect unchanged")
        self.assertEqual(b.rect, skia.Rect.MakeLTRB(0, 30, 20, 40))

    def test_pixel_footprint_of_horizontal_line(self):
        line = browser.DrawLine(0, 10, 50, 10, "black", 1)
        self.assertEqual(line.rect.height(), 0)
        footprint = browser.pixel_footprint(line.rect)
        self.assertEqual(footprint, skia.Rect.MakeLTRB(-1, 9, 51, 11))
        self.assertEqual(browser.leaf_document_footprint(line, ()), footprint,
                         "without ancestors the document footprint is the footprint")
        self.assertEqual(line.rect.height(), 0, "the item's rect is not modified")

    def test_document_footprint_under_scroll(self):
        clip = skia.Rect.MakeLTRB(0, 0, 100, 100)
        scroller = browser.Scroll(clip, 100, [])
        inner = rect(0, 150, 50, 160)
        footprint = browser.leaf_document_footprint(inner, (scroller,))
        self.assertEqual(footprint, skia.Rect.MakeLTRB(0, 49, 51, 61),
                         "shifted up by scroll_y, clipped to the scroller's box")
        self.assertEqual(clip, skia.Rect.MakeLTRB(0, 0, 100, 100), "clip_rect unchanged")

        hidden = rect(0, 300, 50, 310)
        layer = browser.CompositedLayer([hidden], ancestors=(scroller,))
        self.assertTrue(layer.document_footprint().isEmpty())
        self.assertFalse(browser.footprints_overlap(
            layer.document_footprint(), skia.Rect.MakeLTRB(0, 0, 1000, 1000)))
        self.assertEqual(layer.bounds, skia.Rect.MakeLTRB(0, 300, 50, 310))

    def test_document_footprint_under_blur(self):
        blur = browser.Blur(2, [])
        layer = browser.CompositedLayer([rect(10, 10, 20, 20)], ancestors=(blur,))
        self.assertEqual(layer.document_footprint(), skia.Rect.MakeLTRB(3, 3, 27, 27))
        self.assertEqual(layer.bounds, skia.Rect.MakeLTRB(10, 10, 20, 20), "bounds unchanged")

    def test_footprint_cache_cleared_by_add(self):
        layer = browser.CompositedLayer([rect(0, 0, 10, 10)])
        self.assertEqual(layer.document_footprint(), skia.Rect.MakeLTRB(-1, -1, 11, 11))
        layer.add(rect(0, 50, 10, 60))
        self.assertEqual(layer.document_footprint(), skia.Rect.MakeLTRB(-1, -1, 11, 61))

    def test_items_without_rect_overlap_everything(self):
        self.assertTrue(browser.footprints_overlap(None, skia.Rect.MakeEmpty()))
        self.assertTrue(browser.footprints_overlap(skia.Rect.MakeLTRB(0, 0, 1, 1), None))
        self.assertFalse(browser.footprints_overlap(
            skia.Rect.MakeEmpty(), skia.Rect.MakeLTRB(-5, -5, 5, 5)),
            "an empty rect is not a point at the origin")

    def test_overlap_blocks_merge_past_layer(self):
        body, (first, second, box, third) = overlap_example()
        layers, _ = browser.composite_display_list([body])
        self.assertEqual([layer.items for layer in layers], [[first, second], [box], [third]])

    def test_gap_allows_merge_past_layer(self):
        body, (first, second, box, third) = overlap_example(gap=10)
        layers, draw_list = browser.composite_display_list([body])
        self.assertEqual([layer.items for layer in layers], [[first, second, third], [box]])
        self.assertEqual(len(draw_list[0].children), 2, "the merged leaf adds no draw item")

    def test_outline_counterexample_matches_direct_raster(self):
        outline = browser.DrawOutline(skia.Rect.MakeLTRB(5, 20.5, 45, 30), "black", 2)
        body = browser.Blend(0.9, None, [
            rect(0, 0, 50, 8, "red"),
            browser.Blend(0.5, None, [rect(0, 10, 50, 20, "blue")]),
            outline,
        ])
        layers, merged = raster_composited([body])
        self.assertEqual(len(layers), 3, "the outline's stroke reaches the faded box")
        self.assertEqual(np.abs(merged - raster_direct([body])).max(), 0)

    def test_scroll_offset_used_for_overlap(self):
        a, s, b = rect(0, 0, 100, 20), rect(0, 150, 100, 160), rect(0, 55, 100, 58)
        scroller = browser.Scroll(skia.Rect.MakeLTRB(0, 0, 100, 100), 100,
                                  [browser.Blend(0.5, None, [s])])
        body = browser.Blend(0.5, None, [a, scroller, b])
        layers, _ = browser.composite_display_list([body])
        self.assertEqual([layer.items for layer in layers], [[a], [s], [b]],
                         "s is drawn at y 50-60 in the document, on top of b's spot")

    def test_blur_spread_used_for_overlap(self):
        a, s, b = rect(0, 0, 100, 20), rect(0, 40, 100, 50), rect(0, 54, 100, 60)
        blur = browser.Blur(2, [browser.Blend(0.5, None, [s])])
        body = browser.Blend(0.5, None, [a, blur, b])
        layers, _ = browser.composite_display_list([body])
        self.assertEqual([layer.items for layer in layers], [[a], [s], [b]])

    def test_backward_scan_stops_after_32_layers(self):
        first = rect(0, 0, 10, 10)
        faded = [browser.Blend(0.5, None, [rect(0, 20 * i, 10, 20 * i + 10)])
                 for i in range(1, 33)]
        last = rect(0, 2000, 10, 2010)
        body = browser.Blend(0.5, None, [first] + faded + [last])
        layers, _ = browser.composite_display_list([body])
        self.assertEqual(browser.MERGE_SCAN_LIMIT, 32)
        self.assertEqual(len(layers), 34, "first is 33 layers back, past the limit")
        self.assertEqual(layers[-1].items, [last])

        body = browser.Blend(0.5, None, [first] + faded[1:] + [last])
        layers, _ = browser.composite_display_list([body])
        self.assertEqual(layers[0].items, [first, last], "32 layers back is still scanned")

    def test_merging_disabled_gives_one_layer_per_leaf(self):
        body, leaves = overlap_example(gap=10)
        with patch.object(browser, "LAYER_MERGING_ENABLED", False):
            layers, _ = browser.composite_display_list([body])
        self.assertEqual([layer.items for layer in layers], [[leaf] for leaf in leaves])

    def test_merged_layers_match_direct_raster(self):
        for gap in (0, 10):
            with self.subTest(page="plan 2.3 example", gap=gap):
                body, _ = overlap_example(gap)
                _, merged = raster_composited([body])
                self.assertLessEqual(np.abs(merged - raster_direct([body])).max(), 2)

        pages = {"OPACITY_PAGE": OPACITY_PAGE}
        for opacity in ("0.5", "0.1"):
            pages.update({"{} at {}".format(name, opacity): html
                          for name, html in scenario_pages(opacity).items()
                          if name != "wide"})
        for name, html in pages.items():
            with self.subTest(page=name):
                state, merged = render_page(html, compositing=True)
                self.assertLess(len(state.composited_layers),
                                sum(len(layer.items) for layer in state.composited_layers))
                _, direct = render_page(html, compositing=False)
                self.assertLessEqual(np.abs(merged - direct).max(), 2)

    def test_scenario_layer_counts(self):
        state, _ = render_page(OPACITY_PAGE, compositing=True)
        self.assertEqual(len(state.composited_layers), 3)
        with patch.object(browser, "LAYER_MERGING_ENABLED", False):
            state, _ = render_page(OPACITY_PAGE, compositing=True)
        self.assertEqual(len(state.composited_layers), 6)

        for name, html in scenario_pages("0.5").items():
            with self.subTest(page=name):
                state, _ = render_page(html, compositing=True)
                self.assertEqual(len(state.composited_layers), PREDICTED_LAYERS[name])

    def test_worst_case_composite_time(self):
        items = [browser.Blend(0.9, None, [rect(0, 24 * i, 100, 24 * i + 20)])
                 for i in range(800)]
        start = time.perf_counter()
        layers, _ = browser.composite_display_list(items)
        elapsed = time.perf_counter() - start
        self.assertEqual(len(layers), 800)
        self.assertLessEqual(elapsed, 0.250)


if __name__ == "__main__":
    unittest.main()
