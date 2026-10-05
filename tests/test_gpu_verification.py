"""The runner must freeze the experiment before executing measurements."""
import tempfile
import unittest
from pathlib import Path

from gpu_verification import randomized_blocks, validate_formal_manifest, seal_bundle


class RunnerContracts(unittest.TestCase):
    def test_random_order_reproducible_and_each_block_complete(self):
        first = randomized_blocks(10, 13)
        self.assertEqual(first, randomized_blocks(10, 13))
        self.assertNotEqual(first, randomized_blocks(10, 14))
        for block in first:
            self.assertEqual(set(block["order"]), {"cpu_sync", "gpu_sync", "cpu_threaded"})

    def test_formal_run_refuses_unqualified_manifest(self):
        with self.assertRaisesRegex(ValueError, "qualif|correctness|frozen"):
            validate_formal_manifest({"frozen": False})

    def test_summary_only_correctness_cannot_start_formal_measurements(self):
        from test_gpu_analysis import valid_experiment
        manifest, _ = valid_experiment()
        with self.assertRaisesRegex(ValueError, "raw"):
            validate_formal_manifest(manifest)

    def test_bundle_hashes_raw_files_and_never_hashes_itself(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "raw.json").write_text('{"status":"failure"}')
            index = seal_bundle(path)
            self.assertIn("raw.json", index["files"])
            self.assertNotIn("checksums.json", index["files"])
            self.assertEqual(index, seal_bundle(path))


if __name__ == "__main__":
    unittest.main()
