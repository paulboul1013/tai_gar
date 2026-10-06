"""Chapter 13 Overlap and Transforms: CSS translate and its compositing."""
import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import skia

import browser
from test_ch13_compositing import (
    draw_list_items, paint_html, raster_composited, raster_direct, rect, render_page,
)

# skia.Surface() pixels are BGRA.
RED, BLUE, WHITE = [0, 0, 255, 255], [255, 0, 0, 255], [255, 255, 255, 255]
# render_page() pixels are RGBA; the tab starts below the 100px browser chrome.
GREEN_RGBA = [0, 128, 0, 255]
CHROME_BOTTOM = 100


def page_pixel(pixels, x, y):
    return pixels.reshape(600, 800, 4)[CHROME_BOTTOM + y, x].tolist()


def styled_nodes(html):
    nodes = browser.HTMLParser(html).parse()
    rules = sorted(browser.DEFAULT_STYLE_SHEET, key=browser.cascade_priority)
    browser.style(nodes, rules)
    flat = []
    browser.tree_to_list(nodes, flat)
    return flat


class ParseTransformTest(unittest.TestCase):
    def test_supported_translations(self):
        cases = {
            "translate(50px, 50px)": (50.0, 50.0),
            "translate(50px,50px)": (50.0, 50.0),
            "  TRANSLATE( 10px ,  -20.5px )  ": (10.0, -20.5),
            "translate(-3px, 0.25px)": (-3.0, 0.25),
            "translate(12px)": (12.0, 0.0),
            "translate(0, 7px)": (0.0, 7.0),
            "translate(4px, 0)": (4.0, 0.0),
            "translate(0)": (0.0, 0.0),
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(browser.parse_transform(value), expected)

    def test_unsupported_values_behave_like_none(self):
        for value in [
            None,
            "",
            "none",
            "translate()",
            "translate(10px 20px)",
            "translate(1px, 2px, 3px)",
            "translate(10, 20px)",
            "translate(10%, 20px)",
            "translate(10em)",
            "translate(nanpx)",
            "translate(infpx, 0)",
            "translate(10px",
            "scale(2)",
            "rotate(45deg)",
            "translateX(10px)",
        ]:
            with self.subTest(value=value):
                self.assertIsNone(browser.parse_transform(value))

    def test_style_keeps_transform_for_parse(self):
        nodes = styled_nodes(
            '<div id="moved" style="transform: translate(30px, -8px)">a</div>'
            '<div id="still">b</div>'
        )
        by_id = {
            node.attributes.get("id"): node
            for node in nodes
            if isinstance(node, browser.Element)
        }
        self.assertEqual(
            browser.parse_transform(by_id["moved"].style["transform"]),
            (30.0, -8.0),
        )
        self.assertEqual(by_id["still"].style["transform"], "none")
        self.assertIsNone(browser.parse_transform(by_id["still"].style["transform"]))


class TransformEffectTest(unittest.TestCase):
    def test_rect_is_children_bounds_translated(self):
        transform = browser.Transform(
            (30.0, -5.0),
            [rect(0, 10, 20, 20, "red"), rect(10, 15, 40, 30, "blue")],
        )
        self.assertEqual(transform.rect, skia.Rect.MakeLTRB(30, 5, 70, 25))

    def test_parent_effect_bounds_include_translation(self):
        transform = browser.Transform((50.0, 0.0), [rect(0, 0, 10, 10, "red")])
        blend = browser.Blend(0.5, "normal", [transform])
        self.assertEqual(blend.rect, skia.Rect.MakeLTRB(50, 0, 60, 10))

    def test_rect_without_children_stays_empty(self):
        self.assertTrue(browser.Transform((30.0, 40.0), []).rect.isEmpty())

    def test_pixels_land_at_translated_position(self):
        pixels = raster_direct(
            [browser.Transform((50.0, 20.0), [rect(0, 0, 20, 20, "red")])]
        )
        self.assertEqual(pixels[30, 60].tolist(), RED)
        self.assertEqual(pixels[10, 10].tolist(), WHITE)
        self.assertEqual((pixels[:, :, 1] == 0).sum(), 20 * 20)

    def test_canvas_translation_is_restored(self):
        pixels = raster_direct([
            browser.Transform((50.0, 0.0), [rect(0, 0, 10, 10, "red")]),
            rect(0, 40, 10, 50, "blue"),
        ])
        self.assertEqual(pixels[45, 5].tolist(), BLUE)

    def test_compositing_comes_only_from_children(self):
        plain = browser.Transform((5.0, 5.0), [rect(0, 0, 10, 10, "red")])
        self.assertFalse(plain.needs_compositing)

        faded = browser.Blend(0.5, "normal", [rect(0, 0, 10, 10, "red")])
        over_faded = browser.Transform((5.0, 5.0), [faded])
        self.assertTrue(over_faded.needs_compositing)

    def test_clone_keeps_translation(self):
        transform = browser.Transform((7.0, 3.0), [rect(0, 0, 10, 10, "red")])
        twin = transform.clone([rect(0, 0, 5, 5, "blue")])
        self.assertEqual(twin.translation, (7.0, 3.0))
        self.assertEqual(len(twin.children), 1)
        self.assertEqual(len(transform.children), 1)
        self.assertIsNot(twin.children[0], transform.children[0])


def box(height, style=""):
    return '<div style="height:{}px;{}"></div>'.format(height, style)


# The book's example: green is translated up over the faded blue box. Blocks
# start at document y 18; the gap would keep an untranslated green clear.
OVERLAP_PAGE = (
    box(40, "background-color:lightblue")
    + box(40, "opacity:0.5;background-color:blue")
    + box(40)
    + box(40, "background-color:green;transform:translate(20px,-80px)")
)


class PaintTransformTest(unittest.TestCase):
    def test_transform_wraps_the_element_blend(self):
        _, display_list = paint_html(OVERLAP_PAGE)
        transforms = [item for item in draw_list_items(display_list)
                      if isinstance(item, browser.Transform)]
        self.assertEqual(len(transforms), 1)
        transform = transforms[0]
        self.assertEqual(transform.translation, (20.0, -80.0))
        self.assertEqual(len(transform.children), 1)
        self.assertIs(transform.children[0], transform.node.blend_op)

    def test_untransformed_page_has_no_transform(self):
        _, display_list = paint_html(box(40, "opacity:0.5;background-color:blue"))
        self.assertFalse(any(isinstance(item, browser.Transform)
                             for item in draw_list_items(display_list)))

    def test_direct_raster_draws_at_translated_position(self):
        _, pixels = render_page(OVERLAP_PAGE, compositing=False)
        self.assertEqual(page_pixel(pixels, 400, 70), GREEN_RGBA)
        self.assertEqual(page_pixel(pixels, 400, 150), [255, 255, 255, 255],
                         "nothing is left at green's layout position")


class MapThroughTransformTest(unittest.TestCase):
    def test_map_to_document_shifts_by_translation(self):
        transform = browser.Transform((10.0, -5.0), [])
        mapped = browser.map_to_document(skia.Rect.MakeLTRB(0, 20, 30, 40), (transform,))
        self.assertEqual(mapped, skia.Rect.MakeLTRB(10, 15, 40, 35))

    def test_round_trip_through_scroll_and_transform(self):
        scroller = browser.Scroll(skia.Rect.MakeLTRB(0, 0, 200, 200), 30, [])
        transform = browser.Transform((10.0, -5.0), [])
        local = skia.Rect.MakeLTRB(5, 60, 50, 90)
        for ancestors in [(scroller, transform), (transform, scroller)]:
            with self.subTest(ancestors=[type(a).__name__ for a in ancestors]):
                document = browser.map_to_document(local, ancestors)
                self.assertEqual(document, skia.Rect.MakeLTRB(15, 25, 60, 55))
                self.assertEqual(browser.map_from_document(document, ancestors), local)

    def test_maps_do_not_modify_their_input(self):
        transform = browser.Transform((10.0, 10.0), [])
        original = skia.Rect.MakeLTRB(0, 0, 10, 10)
        browser.map_to_document(original, (transform,))
        browser.map_from_document(original, (transform,))
        self.assertEqual(original, skia.Rect.MakeLTRB(0, 0, 10, 10))


def faded(*items):
    return browser.Blend(0.5, None, list(items))


class TransformOverlapTest(unittest.TestCase):
    def test_translated_over_composited_box_gets_own_layer(self):
        background, blue = rect(0, 0, 100, 20, "red"), rect(0, 30, 100, 50, "blue")
        green = browser.Transform((0.0, -30.0), [rect(0, 60, 100, 70, "green")])
        body = faded(background, faded(blue), green)
        layers, merged = raster_composited([body])
        self.assertEqual([layer.items for layer in layers], [[background], [blue], [green]])
        self.assertEqual(np.abs(merged - raster_direct([body])).max(), 0)

    def test_translated_away_from_composited_box_merges(self):
        background, blue = rect(0, 0, 100, 20, "red"), rect(0, 30, 100, 50, "blue")
        green = browser.Transform((0.0, 30.0), [rect(0, 35, 100, 45, "green")])
        body = faded(background, faded(blue), green)
        layers, merged = raster_composited([body])
        self.assertEqual([layer.items for layer in layers], [[background, green], [blue]])
        self.assertEqual(np.abs(merged - raster_direct([body])).max(), 0)

    def test_draw_phase_transform_moves_its_layer_footprint(self):
        background, blue = rect(0, 0, 100, 20, "red"), rect(0, 30, 100, 50, "blue")
        moved = rect(0, 0, 100, 10, "green")
        body = faded(background, browser.Transform((0.0, 35.0), [faded(moved)]), blue)
        layers, merged = raster_composited([body])
        self.assertEqual([layer.items for layer in layers], [[background], [moved], [blue]],
                         "moved is drawn at y 35-45, on top of blue's spot")
        self.assertEqual(np.abs(merged - raster_direct([body])).max(), 0)

    def test_book_example_page(self):
        state, composited = render_page(OVERLAP_PAGE, compositing=True)
        self.assertEqual(len(state.composited_layers), 3)
        self.assertEqual(page_pixel(composited, 400, 70), GREEN_RGBA,
                         "green stays on top of the faded blue box")
        _, direct = render_page(OVERLAP_PAGE, compositing=False)
        self.assertEqual(np.abs(composited - direct).max(), 0)

    def test_untranslated_book_example_merges_past_the_box(self):
        page = OVERLAP_PAGE.replace("transform:translate(20px,-80px)", "")
        state, _ = render_page(page, compositing=True)
        self.assertEqual(len(state.composited_layers), 2)


TRANSFORM_PAGES = {
    "book example": OVERLAP_PAGE,
    "with text": box(20, "background-color:lightblue")
                 + '<div style="opacity:0.5;background-color:orange">faded box</div>'
                 + '<div style="background-color:lightgreen;transform:translate(30px,-15px)">'
                   "moved text</div>",
    "overflow clip": box(40, "opacity:0.5;background-color:blue")
                     + '<div style="height:30px;overflow:clip;border-radius:8px;'
                       'background-color:green;transform:translate(10px,-20px)">'
                     + box(60, "background-color:yellow") + "</div>",
    "blur": box(40, "opacity:0.5;background-color:blue")
            + '<div style="filter:blur(3px);transform:translate(15px,-25px)">'
            + box(30, "background-color:green") + "</div>",
    "opacity on same element": box(40, "background-color:lightblue")
                               + box(40, "opacity:0.5;background-color:blue;"
                                         "transform:translate(40px,-20px)"),
    "transform above opacity": box(40, "background-color:lightblue")
                               + '<div style="transform:translate(25px,-30px)">'
                               + box(40, "opacity:0.5;background-color:blue") + "</div>"
                               + box(40, "background-color:green"),
    "inside scroller": '<div style="overflow:scroll;height:100px">'
                       + box(40, "opacity:0.5;background-color:blue")
                       + box(40, "background-color:green;transform:translate(0px,-30px)")
                       + box(200) + "</div>"
                       + box(40, "background-color:pink"),
}


class CompositedMatchesDirectTest(unittest.TestCase):
    def test_pages_with_transforms(self):
        for name, html in TRANSFORM_PAGES.items():
            with self.subTest(page=name):
                _, display_list = paint_html(html)
                self.assertTrue(any(isinstance(item, browser.Transform)
                                    for item in draw_list_items(display_list)))
                _, composited = render_page(html, compositing=True)
                _, direct = render_page(html, compositing=False)
                # Antialiased text edges rastered onto a transparent layer may
                # round differently, as in test_ch13_compositing.
                tolerance = 2 if name == "with text" else 0
                self.assertLessEqual(np.abs(composited - direct).max(), tolerance)


class InterestCullingTest(unittest.TestCase):
    INTEREST = skia.Rect.MakeLTRB(0, 0, 800, 1000)

    def kept(self, display_list):
        layers, _ = browser.composite_display_list(display_list, self.INTEREST)
        return [item for layer in layers for item in layer.items]

    def test_leaf_translated_into_view_is_kept(self):
        leaf = browser.Transform((0.0, -5000.0), [rect(0, 5000, 10, 5010)])
        self.assertEqual(self.kept([faded(leaf)]), [leaf])

    def test_leaf_translated_out_of_view_is_culled(self):
        near = rect(0, 0, 10, 10)
        leaf = browser.Transform((0.0, 5000.0), [rect(0, 0, 10, 10)])
        self.assertEqual(self.kept([faded(near, leaf)]), [near])

    def test_draw_phase_transform_into_view_is_kept(self):
        far = rect(0, 5000, 10, 5010)
        self.assertEqual(self.kept([browser.Transform((0.0, -5000.0), [faded(far)])]), [far])

    def test_draw_phase_transform_out_of_view_is_culled(self):
        near = rect(0, 0, 10, 10)
        self.assertEqual(self.kept([browser.Transform((0.0, 5000.0), [faded(near)])]), [])


class ScrollClipSurfaceTest(unittest.TestCase):
    def only_layer(self, display_list):
        layers, merged = raster_composited(display_list, height=200)
        self.assertEqual(len(layers), 1)
        self.assertEqual(np.abs(merged - raster_direct(display_list, height=200)).max(), 0)
        return layers[0]

    def test_surface_stops_at_scrolled_scroll_box(self):
        tall = rect(0, 0, 100, 1000, "blue")
        scroller = browser.Scroll(skia.Rect.MakeLTRB(0, 20, 100, 120), 50, [faded(tall)])
        layer = self.only_layer([scroller])
        self.assertEqual(layer.surface_rect, skia.Rect.MakeLTRB(0, 70, 100, 170),
                         "the scroller shows local y 70-170 at scroll_y 50")

    def test_clip_maps_through_transform_inside_scroll(self):
        tall = rect(0, 0, 100, 1000, "blue")
        scroller = browser.Scroll(
            skia.Rect.MakeLTRB(0, 20, 100, 120), 50,
            [browser.Transform((10.0, -30.0), [faded(tall)])],
        )
        layer = self.only_layer([scroller])
        self.assertEqual(layer.surface_rect, skia.Rect.MakeLTRB(-1, 100, 90, 200))

    def test_nested_scrolls_intersect(self):
        tall = rect(0, 0, 100, 1000, "blue")
        inner = browser.Scroll(skia.Rect.MakeLTRB(0, 0, 100, 300), 100, [faded(tall)])
        outer = browser.Scroll(skia.Rect.MakeLTRB(0, 0, 100, 150), 20, [inner])
        layer = self.only_layer([outer])
        self.assertEqual(layer.surface_rect, skia.Rect.MakeLTRB(0, 120, 100, 270))

    def test_fully_clipped_layer_gets_no_surface(self):
        hidden = rect(0, 500, 100, 510, "blue")
        scroller = browser.Scroll(skia.Rect.MakeLTRB(0, 0, 100, 100), 0, [faded(hidden)])
        layers, _ = raster_composited([scroller])
        self.assertIsNone(layers[0].surface)

    def test_layer_outside_scrolls_is_not_clipped(self):
        tall = rect(0, 0, 100, 1000, "blue")
        layer = self.only_layer([faded(tall)])
        self.assertEqual(layer.surface_rect, skia.Rect.MakeLTRB(-1, -1, 101, 1001))

    def test_scroller_page_surfaces_fit_the_box(self):
        html = TRANSFORM_PAGES["inside scroller"]
        state, _ = render_page(html, compositing=True)
        _, display_list = paint_html(html)
        scroller = next(item for item in draw_list_items(display_list)
                        if isinstance(item, browser.Scroll))
        scrolled = [layer for layer in state.composited_layers
                    if layer.surface is not None
                    and any(isinstance(a, browser.Scroll) for a in layer.ancestors)]
        self.assertTrue(scrolled)
        for layer in scrolled:
            self.assertLessEqual(layer.surface_rect.height(),
                                 math.ceil(scroller.clip_rect.height()) + 1)


def element_id(cmd):
    node = cmd.layout_object.node if cmd is not None else None
    while node is not None:
        if isinstance(node, browser.Element) and "id" in node.attributes:
            return node.attributes["id"]
        node = node.parent
    return None


# Moved starts at document (13, 18); translated it covers x 213-, y 118-158.
HIT_PAGE = (
    box(40, "background-color:green;transform:translate(200px,100px)").replace(
        "<div ", '<div id="moved" ')
    + '<div style="transform:translate(300px,150px)">'
      '<a id="link" href="/next">link</a></div>'
)


class HitTestTest(unittest.TestCase):
    def setUp(self):
        _, self.display_list = paint_html(HIT_PAGE)

    def test_click_hits_translated_position(self):
        hit = browser.hit_test_paint_commands(self.display_list, 400, 130)
        self.assertEqual(element_id(hit), "moved")

    def test_click_misses_layout_position(self):
        hit = browser.hit_test_paint_commands(self.display_list, 100, 30)
        self.assertNotEqual(element_id(hit), "moved")

    def link_position(self):
        _, display_list = paint_html(HIT_PAGE.replace("translate(300px,150px)", "none"))
        text = next(cmd for cmd in draw_list_items(display_list)
                    if isinstance(cmd, browser.DrawText))
        return text.rect

    def test_touch_near_translated_link_hits_it(self):
        layout = self.link_position()
        x, y = layout.right() + 300 + 10, layout.top() + 150 - 10
        self.assertIsNone(element_id(browser.hit_test_paint_commands(self.display_list, x, y)),
                          "the tap is beside the link, not on it")
        hit = browser.touch_hit_test_paint_commands(self.display_list, x, y)
        self.assertEqual(element_id(hit), "link")

    def test_touch_near_layout_position_of_link_misses_it(self):
        layout = self.link_position()
        hit = browser.touch_hit_test_paint_commands(
            self.display_list, layout.right() + 10, layout.top() - 10)
        self.assertNotEqual(element_id(hit), "link")

    def test_transform_inside_scroll_maps_both(self):
        target = rect(0, 100, 30, 130, "green")
        target.layout_object = SimpleNamespace(node=None)
        scroller = browser.Scroll(skia.Rect.MakeLTRB(0, 0, 100, 100), 50,
                                  [browser.Transform((20.0, 0.0), [target])])
        self.assertIs(browser.hit_test_paint_commands([scroller], 30, 60), target)
        self.assertIsNone(browser.hit_test_paint_commands([scroller], 10, 60))
        self.assertIsNone(browser.hit_test_paint_commands([scroller], 30, 110),
                          "outside the scroll box")
        self.assertIs(browser.touch_hit_test_paint_commands([scroller], 55, 60), target)
        self.assertIsNone(browser.touch_hit_test_paint_commands([scroller], 85, 60))


class TranslatedInputCaretTest(unittest.TestCase):
    def test_caret_lands_where_the_click_is_drawn(self):
        page = ('<div style="transform:translate(100px,0px)">'
                '<input id="field" value="abcdef"></div>')
        document, _ = paint_html(page)
        layouts = []
        browser.tree_to_list(document, layouts)
        field = next(obj for obj in layouts if isinstance(obj, browser.InputLayout))
        drawn_x = field.x + 100 + field.font.measureText("abc")
        index = browser.Tab.input_cursor_index_from_x(None, drawn_x, field, "abcdef")
        self.assertEqual(index, 3)


class TransformedOverflowTest(unittest.TestCase):
    def test_outermost_transform_bottoms(self):
        down = browser.Transform((0.0, 300.0), [rect(0, 0, 10, 20)])
        nested = browser.Transform((0.0, 10.0), [browser.Transform((0.0, 100.0),
                                                                   [rect(0, 0, 10, 20)])])
        up = browser.Transform((0.0, -50.0), [rect(0, 0, 10, 20)])
        self.assertEqual(browser.transformed_overflow_bottom([faded(down, nested, up)]), 320)
        self.assertEqual(browser.transformed_overflow_bottom([nested]), 130)
        self.assertEqual(browser.transformed_overflow_bottom([rect(0, 0, 10, 900)]), 0)

    def test_transform_inside_scroll_overflows_only_the_scroller(self):
        inner = browser.Transform((0.0, 900.0), [rect(0, 0, 10, 20)])
        scroller = browser.Scroll(skia.Rect.MakeLTRB(0, 0, 100, 100), 0, [inner])
        self.assertEqual(browser.transformed_overflow_bottom([scroller]), 0)


class TabPageHeightTest(unittest.TestCase):
    TAB_HEIGHT = 100

    def setUp(self):
        self.commits = []
        host = SimpleNamespace(
            measure=SimpleNamespace(time=lambda name: None, stop=lambda name: None),
            set_needs_animation_frame=lambda tab: None,
            commit=lambda tab, data: self.commits.append(data) or True,
            finish_animation_frame=lambda tab: None,
        )
        self.tab = browser.Tab(host, 800, self.TAB_HEIGHT, set(), [])
        self.tab.url = browser.URL("https://example.test/page")
        self.tab.rules = sorted(browser.DEFAULT_STYLE_SHEET, key=browser.cascade_priority)

    def load(self, html):
        self.tab.nodes = browser.HTMLParser(html).parse()
        self.tab.animated_nodes = set()
        self.tab.set_needs_render()
        self.tab.run_animation_frame()

    def scroll_to_bottom(self):
        for _ in range(100):
            self.tab.scrolldown()
        return self.tab.scroll

    def test_translated_box_extends_committed_height_and_scroll(self):
        self.load(box(40, "background-color:green;transform:translate(0px,300px)"))
        # The box is laid out at y 18-58 and painted at y 318-358.
        expected = 358 + browser.VSTEP
        self.assertEqual(self.tab.page_height(), expected)
        self.assertEqual(self.commits[-1].document_height, expected)
        self.assertEqual(self.scroll_to_bottom(), expected - self.TAB_HEIGHT)

    def test_untransformed_and_upward_pages_keep_layout_height(self):
        for style in ["", "transform:translate(0px,-10px)"]:
            with self.subTest(style=style):
                self.load(box(40, "background-color:green;" + style))
                self.assertEqual(self.tab.page_height(), 58 + browser.VSTEP)
                self.assertEqual(self.scroll_to_bottom(), 0)

    def test_translated_page_renders_below_layout_height(self):
        html = box(40, "background-color:green;transform:translate(0px,300px)")
        self.load(html)
        _, display_list = paint_html(html)
        page = browser.CommitData("about:test", 0, self.tab.page_height(), display_list,
                                  width=800, tab_height=500)
        work = browser.RasterWork(
            raster_id=1, window_id=1, scene_epoch=1, active_tab_key=1,
            page_state=page, chrome_display_list=(), width=800, height=600,
            chrome_bottom=100, chrome_raster=True, tab_raster=True,
            estimator_tab=None, title="overflow",
        )
        for compositing in (True, False):
            with self.subTest(compositing=compositing):
                state = browser.RasterWindowState()
                with patch.object(browser, "COMPOSITING_ENABLED", compositing):
                    pixels = np.frombuffer(state.render(work).pixels, np.uint8).astype(int)
                self.assertEqual(page_pixel(pixels, 400, 330), GREEN_RGBA)


if __name__ == "__main__":
    unittest.main()
