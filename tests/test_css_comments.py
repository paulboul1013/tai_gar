"""CSS comments are ignored wherever they appear."""
import unittest

import browser


def computed_color(html, tag):
    nodes = browser.HTMLParser(html).parse()
    rules = sorted(browser.DEFAULT_STYLE_SHEET, key=browser.cascade_priority)
    browser.style(nodes, rules)
    for node in browser.tree_to_list(nodes, []):
        if isinstance(node, browser.Element) and node.tag == tag:
            return node.style["color"]
    raise AssertionError(f"no <{tag}> in page")


class CSSCommentTests(unittest.TestCase):
    def test_default_links_are_blue(self):
        self.assertEqual(computed_color('<a href="x">link</a>', "a"), "blue")

    def test_default_sheet_keeps_text_black(self):
        html = '<div class="main"><p>text</p></div>'
        self.assertEqual(computed_color(html, "p"), "black")

    def test_rule_after_comment_is_kept(self):
        rules = browser.CSSParser("/* note */ p { color: red; }").parse()
        self.assertEqual([body for _, body, _ in rules], [{"color": ("red", False)}])

    def test_comments_inside_rules_are_ignored(self):
        rules = browser.CSSParser(
            "p { /* a */ color: red/* b */; /* c */ width: 10px }"
        ).parse()
        self.assertEqual(
            rules[0][1],
            {"color": ("red", False), "width": ("10px", False)},
        )

    def test_unterminated_comment_runs_to_end(self):
        rules = browser.CSSParser("p { color: red } /* open q { color: blue }").parse()
        self.assertEqual([body for _, body, _ in rules], [{"color": ("red", False)}])


if __name__ == "__main__":
    unittest.main()
