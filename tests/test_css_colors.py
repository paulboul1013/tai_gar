"""Named CSS colors draw as their real color, not the black fallback."""
import unittest

import skia

import browser


def rgb(name):
    color = browser.parse_color(name)
    return skia.ColorGetR(color), skia.ColorGetG(color), skia.ColorGetB(color)


class NamedColorTests(unittest.TestCase):
    def test_visited_link_color_is_purple(self):
        self.assertEqual(rgb("purple"), (128, 0, 128))


if __name__ == "__main__":
    unittest.main()
