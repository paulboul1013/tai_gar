"""Elements with display: none get no layout and paint nothing."""
import unittest

import browser


def page_texts(html):
    nodes = browser.HTMLParser(html).parse()
    rules = sorted(browser.DEFAULT_STYLE_SHEET, key=browser.cascade_priority)
    browser.style(nodes, rules)
    document = browser.DocumentLayout(nodes, browser.WIDTH)
    document.layout()
    texts = []
    for layout in browser.tree_to_list(document, []):
        for cmd in layout.paint():
            if isinstance(cmd, browser.DrawText):
                texts.append(cmd.text)
    return texts


class DisplayNoneTests(unittest.TestCase):
    def test_hidden_block_is_not_painted(self):
        texts = page_texts(
            '<div style="display: none"><p>hidden</p></div><p>shown</p>'
        )
        self.assertIn("shown", texts)
        self.assertNotIn("hidden", texts)

    def test_hidden_inline_is_not_painted(self):
        texts = page_texts('<p>a <span style="display: none">hidden</span> b</p>')
        self.assertEqual([t for t in texts if t in ["a", "hidden", "b"]], ["a", "b"])

    def test_hidden_toc_draws_no_header(self):
        texts = page_texts(
            '<nav id="toc" style="display: none"><ul><li>One</li></ul></nav><p>body</p>'
        )
        self.assertNotIn("Table of Contents", texts)
        self.assertNotIn("One", texts)


if __name__ == "__main__":
    unittest.main()
