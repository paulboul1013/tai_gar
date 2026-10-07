"""@media rules apply only when their query matches the viewport width."""
import unittest
from types import SimpleNamespace

import browser


def font_size(css, html, tag, width):
    nodes = browser.HTMLParser(html).parse()
    rules = browser.DEFAULT_STYLE_SHEET + browser.CSSParser(css).parse()
    browser.style(nodes, sorted(rules, key=browser.cascade_priority), SimpleNamespace(width=width))
    for node in browser.tree_to_list(nodes, []):
        if isinstance(node, browser.Element) and node.tag == tag:
            return node.style["font-size"]
    raise AssertionError(f"no <{tag}> in page")


# Shape of browser.engineering's book.css: a base size, a narrow-window
# override, and a print-only one.
BOOK_CSS = """
html { font-size: 24px; }
@media (max-width: 800px) { html { font-size: 18px; } }
@media print { html { font-size: 12px; } }
p { color: red; }
"""


class MediaQueryTests(unittest.TestCase):
    def test_max_width_applies_at_or_below_the_limit(self):
        self.assertEqual(font_size(BOOK_CSS, "<p>x</p>", "html", 800), "18px")

    def test_max_width_skipped_above_the_limit(self):
        self.assertEqual(font_size(BOOK_CSS, "<p>x</p>", "html", 1200), "24px")

    def test_min_width_with_media_type(self):
        css = "@media only screen and (min-width: 1150px) { p { font-size: 30px } }"
        self.assertEqual(font_size(css, "<p>x</p>", "p", 1150), "30px")
        self.assertEqual(font_size(css, "<p>x</p>", "p", 1149), "16px")

    def test_comma_means_any_query(self):
        css = "@media print, (max-width: 500px) { p { font-size: 30px } }"
        self.assertEqual(font_size(css, "<p>x</p>", "p", 400), "30px")
        self.assertEqual(font_size(css, "<p>x</p>", "p", 800), "16px")

    def test_unsupported_queries_never_match(self):
        css = (
            "@media print { p { font-size: 30px } }"
            "@media (prefers-color-scheme: dark) { p { font-size: 31px } }"
            "@media (max-width: 50em) { p { font-size: 32px } }"
        )
        self.assertEqual(font_size(css, "<p>x</p>", "p", 400), "16px")

    def test_rules_after_media_block_still_parse(self):
        rules = browser.CSSParser(BOOK_CSS).parse()
        self.assertEqual(rules[-1][1], {"color": ("red", False)})
        self.assertIsNone(rules[-1][2])

    def test_unknown_at_rule_with_nested_blocks_is_skipped(self):
        css = (
            "@keyframes spin { from { color: red } to { color: blue } }"
            "@import url(x.css);"
            "p { font-size: 30px }"
        )
        self.assertEqual(font_size(css, "<p>x</p>", "p", 800), "30px")

    def test_resize_reapplies_media_rules(self):
        nodes = browser.HTMLParser("<p>x</p>").parse()
        rules = sorted(browser.CSSParser(BOOK_CSS).parse(), key=browser.cascade_priority)
        browser.style(nodes, rules, SimpleNamespace(width=1200))
        self.assertEqual(nodes.style["font-size"], "24px")
        browser.style(nodes, rules, SimpleNamespace(width=700))
        self.assertEqual(nodes.style["font-size"], "18px")


if __name__ == "__main__":
    unittest.main()
