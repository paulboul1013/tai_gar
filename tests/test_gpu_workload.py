import unittest
from gpu_workload import build_snapshots, fixture_resources


class WorkloadContracts(unittest.TestCase):
    def test_real_browser_snapshot_sequence_is_reproducible_and_changed(self):
        first, a = build_snapshots("small", count=4)
        second, b = build_snapshots("small", count=4)
        self.assertEqual(a["snapshot_hash"], b["snapshot_hash"])
        self.assertIsNot(first[0].display_list, first[1].display_list)
        self.assertTrue(first[0].display_list)
        self.assertTrue(all(a["tab_raster_flags"]))
        self.assertTrue(all(font["serialized_font_sha256"] for font in a["font"]))

    def test_fixture_uses_supported_external_raf_and_whole_style(self):
        resources = fixture_resources("scroll_raf")
        self.assertIn('src="scene.js"', resources["index.html"])
        self.assertIn('setAttribute("style"', resources["scene.js"])
        self.assertIn("requestAnimationFrame(update)", resources["scene.js"])


if __name__ == "__main__":
    unittest.main()
