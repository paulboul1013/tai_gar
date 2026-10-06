"""Chapter 13 CSS Transitions: opacity interpolation and per-phase dirty flags."""
import unittest
from types import SimpleNamespace

import browser


class RecordingMeasure:
    def __init__(self):
        self.phases = []

    def time(self, name):
        self.phases.append(name)

    def stop(self, name):
        pass


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def blend_opacities(display_list):
    """Opacities below 1 on Blend nodes anywhere in a display list."""
    found = []
    for item in display_list:
        if isinstance(item, browser.Blend) and item.opacity < 1.0:
            found.append(item.opacity)
        if isinstance(item, browser.VisualEffect):
            found.extend(blend_opacities(item.children))
    return found


PAGE = '<div id="box" style="transition: opacity 2s; opacity: 1">box</div>'


class TransitionTestCase(unittest.TestCase):
    def setUp(self):
        self.frames_requested = 0
        self.measure = RecordingMeasure()
        self.host = SimpleNamespace(
            measure=self.measure,
            set_needs_animation_frame=self.request_frame,
            commit=lambda tab, data: True,
            finish_animation_frame=lambda tab: None,
        )
        self.tab = browser.Tab(self.host, 800, 600, set(), [])
        self.tab.url = browser.URL("https://example.test/page")
        self.tab.rules = sorted(
            browser.DEFAULT_STYLE_SHEET, key=browser.cascade_priority
        )
        self.clock = Clock()
        self.tab.animation_clock = self.clock
        self.load(PAGE)

    def request_frame(self, tab):
        self.assertIs(tab, self.tab)
        self.frames_requested += 1

    def load(self, html):
        self.tab.nodes = browser.HTMLParser(html).parse()
        self.tab.animated_nodes = set()
        self.tab.set_needs_render()
        self.tab.render()
        self.box = self.find("box")

    def find(self, element_id):
        return next(
            node for node in browser.tree_to_list(self.tab.nodes, [])
            if isinstance(node, browser.Element)
            and node.attributes.get("id") == element_id
        )

    def set_style(self, value):
        # Same effect as JS setAttribute("style", ...): a full restyle.
        self.box.attributes["style"] = value
        self.tab.set_needs_render()
        self.tab.render()

    def frame_at(self, now):
        self.clock.now = now
        self.tab.run_css_animations()
        self.tab.render()

    def opacity(self):
        return float(self.box.style["opacity"])

    def painted_opacities(self):
        return blend_opacities(self.tab.display_list)


class ParseTransition(unittest.TestCase):
    def test_durations_in_seconds(self):
        self.assertEqual(browser.parse_transition("opacity 2s"), {"opacity": 2.0})
        self.assertEqual(
            browser.parse_transition("opacity 250ms, width 1s"),
            {"opacity": 0.25, "width": 1.0},
        )

    def test_invalid_items_are_ignored(self):
        self.assertEqual(browser.parse_transition(None), {})
        self.assertEqual(browser.parse_transition("opacity"), {})
        self.assertEqual(browser.parse_transition("opacity fast, width 1s"), {"width": 1.0})
        self.assertEqual(browser.parse_transition("opacity -1s"), {})

    def test_css_parser_keeps_multi_word_value(self):
        pairs = browser.CSSParser("transition: opacity 2s, width 1s; opacity: 0.5").body()
        self.assertEqual(pairs["transition"], ("opacity 2s, width 1s", False))
        self.assertEqual(pairs["opacity"], ("0.5", False))


class OpacityTransition(TransitionTestCase):
    def test_first_style_does_not_animate(self):
        self.assertEqual(self.box.animations, {})
        self.assertEqual(self.opacity(), 1.0)

    def test_opacity_change_interpolates_linearly_over_duration(self):
        self.set_style("transition: opacity 2s; opacity: 0.1")
        self.assertIn("opacity", self.box.animations)
        self.assertEqual(self.opacity(), 1.0)

        self.frame_at(101.0)
        self.assertAlmostEqual(self.opacity(), 0.55)
        self.assertEqual(len(self.painted_opacities()), 1)
        self.assertAlmostEqual(self.painted_opacities()[0], 0.55)

        self.frame_at(102.5)
        self.assertEqual(self.box.style["opacity"], "0.1")
        self.assertEqual(self.box.animations, {})
        self.assertEqual(self.tab.animated_nodes, set())

    def test_milliseconds_and_percent_values(self):
        self.load('<div id="box" style="transition: opacity 500ms; opacity: 100%">box</div>')
        self.set_style("transition: opacity 500ms; opacity: 0%")
        self.frame_at(100.25)
        self.assertAlmostEqual(self.opacity(), 0.5)

    def test_animation_frames_skip_style_and_layout(self):
        self.set_style("transition: opacity 2s; opacity: 0.1")
        self.measure.phases.clear()
        self.frame_at(100.5)
        self.assertEqual(self.measure.phases, ["render", "paint"])

    def test_last_animation_frame_reruns_layout(self):
        self.set_style("transition: opacity 2s; opacity: 0.1")
        self.frame_at(101.0)
        self.measure.phases.clear()
        self.frame_at(102.5)
        self.assertEqual(self.measure.phases, ["render", "layout", "paint"])

    def test_restyle_mid_animation_does_not_restart_it(self):
        self.set_style("transition: opacity 2s; opacity: 0.1")
        animation = self.box.animations["opacity"]
        self.frame_at(101.0)

        # An unrelated change forces a full restyle while the animation runs.
        self.set_style("transition: opacity 2s; opacity: 0.1; color: red")
        self.assertIs(self.box.animations["opacity"], animation)
        self.assertAlmostEqual(self.opacity(), 0.55)
        self.assertEqual(self.box.style["color"], "red")

        self.frame_at(101.5)
        self.assertAlmostEqual(self.opacity(), 0.325)

    def test_new_target_starts_from_the_value_on_screen(self):
        self.set_style("transition: opacity 2s; opacity: 0")
        self.frame_at(101.0)
        self.assertAlmostEqual(self.opacity(), 0.5)

        self.set_style("transition: opacity 2s; opacity: 1")
        self.assertAlmostEqual(self.opacity(), 0.5)
        self.frame_at(102.0)
        self.assertAlmostEqual(self.opacity(), 0.75)

    def test_without_transition_opacity_jumps(self):
        self.set_style("opacity: 0.1")
        self.assertEqual(self.box.animations, {})
        self.assertEqual(self.opacity(), 0.1)

    def test_removing_transition_mid_animation_jumps_to_target(self):
        self.set_style("transition: opacity 2s; opacity: 0.1")
        self.frame_at(101.0)
        self.set_style("opacity: 0.1")
        self.assertEqual(self.box.animations, {})
        self.assertEqual(self.opacity(), 0.1)

    def test_other_listed_properties_still_change_instantly(self):
        self.set_style("transition: opacity 2s, color 2s; opacity: 1; color: red")
        self.assertEqual(self.box.animations, {})
        self.assertEqual(self.box.style["color"], "red")

    def test_frames_are_requested_until_the_animation_ends(self):
        self.set_style("transition: opacity 2s; opacity: 0.1")
        self.frames_requested = 0
        self.frame_at(101.0)
        self.assertEqual(self.frames_requested, 1)
        self.frame_at(102.0)
        self.assertEqual(self.frames_requested, 2)

        # Final value was painted on the previous frame; nothing is left to run.
        self.frames_requested = 0
        self.frame_at(103.0)
        self.assertEqual(self.frames_requested, 0)
        self.assertFalse(self.tab.needs_render)

    def test_run_animation_frame_advances_and_commits(self):
        commits = []
        self.host.commit = lambda tab, data: commits.append(data) or True
        self.set_style("transition: opacity 2s; opacity: 0.1")
        self.clock.now = 101.0
        self.tab.run_animation_frame()
        self.assertAlmostEqual(self.opacity(), 0.55)
        self.assertEqual(len(commits), 1)
        blends = blend_opacities(commits[0].display_list)
        self.assertAlmostEqual(blends[0], 0.55)

    def test_navigation_drops_running_animations(self):
        self.set_style("transition: opacity 2s; opacity: 0.1")
        self.assertEqual(self.tab.animated_nodes, {self.box})
        self.load(PAGE)
        self.assertEqual(self.tab.animated_nodes, set())


class DirtyFlags(TransitionTestCase):
    def test_needs_render_maps_onto_phase_flags(self):
        self.assertFalse(self.tab.needs_render)
        self.tab.needs_render = True
        self.assertTrue(self.tab.needs_style)
        self.tab.needs_render = False
        self.assertFalse(
            self.tab.needs_style or self.tab.needs_layout or self.tab.needs_paint
        )

    def test_set_needs_render_runs_every_phase(self):
        self.measure.phases.clear()
        self.tab.set_needs_render()
        self.assertTrue(self.tab.render())
        self.assertEqual(self.measure.phases, ["render", "style", "layout", "paint"])
        self.assertFalse(self.tab.needs_render)

    def test_set_needs_layout_skips_style(self):
        self.measure.phases.clear()
        self.frames_requested = 0
        self.tab.set_needs_layout()
        self.assertEqual(self.frames_requested, 1)
        self.tab.render()
        self.assertEqual(self.measure.phases, ["render", "layout", "paint"])

    def test_clean_tab_does_not_render(self):
        self.measure.phases.clear()
        self.assertFalse(self.tab.render())
        self.assertEqual(self.measure.phases, [])


if __name__ == "__main__":
    unittest.main()
