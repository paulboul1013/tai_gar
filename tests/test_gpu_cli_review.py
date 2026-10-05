"""Independent runner review: a crashed child never becomes a successful sample."""

import json
import hashlib
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import gpu_verification


class CaptureFreezeReviewTests(unittest.TestCase):
    def capture_fixture(self, directory, same_backend=False, wrong_phase=False, wrong_pixels=False):
        import numpy as np
        from gpu_analysis import freeze_calibration
        root = Path(directory)
        identity = {key: "fixed" for key in ("program", "platform", "driver", "dependencies", "font",
                    "color_format", "cache_policy", "scheduler", "present_policy")}
        identity.update(window_size=[30, 10], drawable_size=[30, 10], dpi=1, msaa=0, stencil=8,
                        driver=dict(kernel="fixed", selection={}))
        manifest = dict(schema_version=1, experiment_id="capture-review", frozen=False,
            warmup_frames=60, measured_frames=300, input_samples=100, bootstrap_seed=1,
            bootstrap_resamples=10000, timeout_seconds=30, identity=identity,
            invalid_run_reasons=["measurement_fault"],
            blocks=[dict(block_id=str(i), order=["cpu_sync", "gpu_sync", "cpu_threaded"]) for i in range(10)],
            scenarios=[dict(id=name, role="primary" if name == "text_rect" else "regression",
                interaction=name == "scroll_raf", workload_hash="fixed",
                snapshot_hash="fixed", qualification=dict(visible_changes=True, rerasterized=True,
                cpu_raster_fraction=0.6, attribution="original qualification proof"))
                for name in ("text_rect", "small", "blend_clip", "scroll_raf")],
            correctness=dict(status="PENDING"))
        qualification = root / "qualification.json"
        gpu_verification.write_json(qualification, dict(program=identity["program"], scenarios={scene["id"]:
            dict(qualification=scene["qualification"], workload_hash="fixed", main_preparation_ns=4,
                 raw_frames=[dict(renderer_ns=3, tab_raster=True, pixels_sha256=phase) for phase in ("a", "b")])
            for scene in manifest["scenarios"]}))
        manifest["qualification_proof"] = dict(path=str(qualification),
            sha256=hashlib.sha256(qualification.read_bytes()).hexdigest())
        image = np.zeros((10, 30, 4), dtype=np.uint8)
        image[:, :, 3] = 255
        image[2:8, 2:8, :3] = 255
        missing = image.copy()
        missing[2:8, 2:8, :3] = 0
        regions = [dict(id=str(i), kind=kind, box=[10 * i, 0, 10 * (i + 1), 10], pixel_tolerance=3)
                   for i, kind in enumerate(("text", "geometry", "blend_clip"))]
        negatives = {key: missing for key in ("missing_text", "offset", "alpha", "clip", "stale")}
        calibration = freeze_calibration(image, [image], negatives, regions)
        self.assertEqual(calibration["status"], "PASS")
        calibration_path = root / "calibration.json"
        gpu_verification.write_json(calibration_path, calibration)
        index = []
        for scenario, phase in ((scene["id"], phase) for scene in manifest["scenarios"] for phase in (0, 1)):
            paths = {}
            for side, config in (("reference", "cpu_sync"), ("candidate", "cpu_sync" if same_backend else "gpu_sync")):
                image_path = root / (scenario + side + str(phase) + ".npy")
                np.save(image_path, image)
                linked_path = image_path
                if wrong_pixels and side == "candidate":
                    linked_path = root / (scenario + "actual-candidate" + str(phase) + ".npy")
                    np.save(linked_path, missing)
                run = dict(run_id=scenario + side + str(phase), experiment_id="capture-review", scenario_id=scenario,
                    configuration=config, kind="capture", status="ok", identity=identity,
                    hardware=dict(vendor="Mesa", renderer="llvmpipe review", version="4.5 review"),
                    workload_hash="fixed", snapshot_hash="fixed", captures=[dict(
                        phase=1 if wrong_phase else phase, frame_id=360,
                        path=str(linked_path), sha256=hashlib.sha256(linked_path.read_bytes()).hexdigest())])
                run_path = root / (scenario + side + str(phase) + ".json")
                gpu_verification.write_json(run_path, run)
                paths[side], paths[side + "_run"] = str(image_path), str(run_path)
            index.append(dict(scenario_id=scenario, phase=phase, calibration=str(calibration_path), **paths))
        manifest_path, index_path = root / "manifest.json", root / "index.json"
        gpu_verification.write_json(manifest_path, manifest)
        gpu_verification.write_json(index_path, index)
        return SimpleNamespace(manifest=str(manifest_path), capture_index=str(index_path), output=str(root / "frozen.json"))

    def test_two_cpu_captures_cannot_pass_gpu_correctness(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.capture_fixture(directory, same_backend=True)
            with self.assertRaises(ValueError):
                gpu_verification.freeze(args)

    def test_intact_metadata_for_every_phase_and_backend_can_freeze(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.capture_fixture(directory)
            gpu_verification.freeze(args)
            result = gpu_verification.read_json(args.output)
            self.assertTrue(result["frozen"])
            self.assertEqual(result["correctness"]["status"], "PASS")

    def test_low_raster_share_with_intact_raw_proof_can_freeze_and_start_formal_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.capture_fixture(directory)
            manifest = gpu_verification.read_json(args.manifest)
            proof_path = manifest["qualification_proof"]["path"]
            proof = gpu_verification.read_json(proof_path)
            # Two renderer samples total 6 ns; main preparation totals 14 ns.
            # The independently recomputable 30% share must not exclude this scene.
            for scene in manifest["scenarios"]:
                scene["qualification"]["cpu_raster_fraction"] = 0.3
                proof["scenarios"][scene["id"]]["main_preparation_ns"] = 14
                proof["scenarios"][scene["id"]]["qualification"] = scene["qualification"]
            gpu_verification.write_json(proof_path, proof)
            manifest["qualification_proof"]["sha256"] = hashlib.sha256(Path(proof_path).read_bytes()).hexdigest()
            gpu_verification.write_json(args.manifest, manifest)
            gpu_verification.freeze(args)
            frozen = gpu_verification.read_json(args.output)
            self.assertTrue(frozen["frozen"])
            gpu_verification.validate_formal_manifest(frozen)

    def test_index_phase_must_match_actual_capture_phase(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.capture_fixture(directory, wrong_phase=True)
            with self.assertRaises(ValueError):
                gpu_verification.freeze(args)

    def test_compared_pixels_must_be_the_file_exported_by_capture_run(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.capture_fixture(directory, wrong_pixels=True)
            with self.assertRaises(ValueError):
                gpu_verification.freeze(args)

    def test_context_driver_change_between_correctness_captures_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.capture_fixture(directory)
            entries = gpu_verification.read_json(args.capture_index)
            path = entries[-1]["candidate_run"]
            run = gpu_verification.read_json(path)
            run["hardware"]["version"] = "4.6 changed driver"
            gpu_verification.write_json(path, run)
            with self.assertRaisesRegex(ValueError, "driver baselines differ"):
                gpu_verification.freeze(args)


class ChildProcessFailureTests(unittest.TestCase):
    def run_child_fixture(self, returncode, initial_record=None, timeout=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {
                "schema_version": 1, "frozen": False, "experiment_id": "crash-review",
                "identity": {}, "warmup_frames": 60, "measured_frames": 300,
                "timeout_seconds": 1, "blocks": [{"block_id": "7", "order": ["cpu_sync"]}],
                "scenarios": [{"id": "small", "role": "primary", "interaction": False,
                               "snapshot_hash": "draft", "workload_hash": "fixed"}],
            }
            manifest_path = root / "input.json"
            manifest_path.write_text(json.dumps(manifest))
            output = root / "results"

            def child(command, **kwargs):
                path = Path(command[command.index("--output") + 1])
                if initial_record is not None:
                    path.write_text(json.dumps(initial_record))
                if timeout:
                    raise subprocess.TimeoutExpired(command, 1, output=b"partial stdout", stderr=b"partial stderr")
                return subprocess.CompletedProcess(command, returncode, stdout="original stdout", stderr="original stderr")

            args = SimpleNamespace(manifest=str(manifest_path), output=str(output), smoke=True)
            with patch("gpu_workload.build_snapshots", return_value=([], {"snapshot_hash": "smoke"})), \
                    patch.object(gpu_verification, "collect_bundle_artifacts"), \
                    patch.object(gpu_verification.subprocess, "run", side_effect=child):
                gpu_verification.run_experiment(args)
            runs = json.loads((output / "runs.json").read_text())
            summary = json.loads((output / "summary.json").read_text())
            logs = [path.read_text() for path in output.glob("*.log")]
            return runs, summary, logs

    def test_crash_before_worker_json_is_retained_with_scheduled_identity(self):
        runs, summary, logs = self.run_child_fixture(139)
        self.assertEqual(len(runs), 1, "the scheduled crash must not disappear from original runs")
        run = runs[0]
        self.assertEqual(run["status"], "product_failure")
        self.assertEqual(run["scenario_id"], "small")
        self.assertEqual(str(run["block_id"]), "7")
        self.assertEqual(run["experiment_id"], "crash-review")
        self.assertEqual(summary["performance_status"], "FAIL")
        self.assertIn("original stderr", logs[0])

    def test_nonzero_process_exit_overrides_existing_ok_record(self):
        initial = dict(run_id="persisted", experiment_id="crash-review", scenario_id="small",
                       block_id="7", configuration="cpu_sync", kind="diagnostic", status="ok")
        runs, summary, logs = self.run_child_fixture(139, initial_record=initial)
        self.assertEqual(runs[0]["status"], "product_failure")
        self.assertEqual(summary["performance_status"], "FAIL")
        self.assertEqual(runs[0]["run_id"], "persisted")

    def test_timeout_keeps_partial_stdout_and_stderr(self):
        runs, summary, logs = self.run_child_fixture(0, timeout=True)
        self.assertEqual(runs[0]["status"], "product_failure")
        self.assertEqual(summary["performance_status"], "FAIL")
        self.assertIn("partial stdout", logs[0])
        self.assertIn("partial stderr", logs[0])


class PortableEvidenceBundleTests(unittest.TestCase):
    def test_overwritten_capture_cannot_be_sealed_with_stale_correctness_pass(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            args = CaptureFreezeReviewTests().capture_fixture(directory)
            gpu_verification.freeze(args)
            manifest = gpu_verification.read_json(args.output)
            candidate = Path(manifest["correctness"]["captures"][0]["files"]["candidate"])
            image = np.load(candidate, allow_pickle=False)
            image[2:8, 2:8, :3] = 0
            np.save(candidate, image)
            bundle = Path(directory) / "bundle"
            bundle.mkdir()
            with self.assertRaises(ValueError):
                gpu_verification.collect_bundle_artifacts(bundle, manifest)

    def test_modified_calibration_payload_cannot_be_sealed_with_stale_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            args = CaptureFreezeReviewTests().capture_fixture(directory)
            gpu_verification.freeze(args)
            manifest = gpu_verification.read_json(args.output)
            calibration_path = manifest["correctness"]["captures"][0]["files"]["calibration"]
            calibration = gpu_verification.read_json(calibration_path)
            calibration["thresholds"]["0"]["max_bad_pixel_fraction"] = 1
            gpu_verification.write_json(calibration_path, calibration)
            bundle = Path(directory) / "bundle"
            bundle.mkdir()
            with self.assertRaises(ValueError):
                gpu_verification.collect_bundle_artifacts(bundle, manifest)

    def test_qualification_proof_hash_is_checked_before_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            qualification = Path(directory) / "qualification.json"
            qualification.write_text('{"fraction":0.6}')
            expected = hashlib.sha256(qualification.read_bytes()).hexdigest()
            qualification.write_text('{"fraction":0.4}')
            manifest = dict(qualification_proof=dict(path=str(qualification), sha256=expected))
            bundle = Path(directory) / "bundle"
            bundle.mkdir()
            with self.assertRaises(ValueError):
                gpu_verification.collect_bundle_artifacts(bundle, manifest)

    def test_pixel_calibration_qualification_source_and_diff_survive_original_removal(self):
        with tempfile.TemporaryDirectory() as bundle_directory:
            bundle = Path(bundle_directory) / "portable"
            bundle.mkdir()
            with tempfile.TemporaryDirectory() as originals:
                args = CaptureFreezeReviewTests().capture_fixture(originals)
                gpu_verification.freeze(args)
                manifest = gpu_verification.read_json(args.output)
                qualification = Path(originals) / "qualification.json"
                qualification.write_text('{"original_frames":[60,300]}')
                manifest["qualification_proof"] = dict(path=str(qualification),
                    sha256=hashlib.sha256(qualification.read_bytes()).hexdigest())
                gpu_verification.collect_bundle_artifacts(bundle, manifest)
                original_paths = set()
                for capture in manifest["correctness"]["captures"]:
                    original_paths.update(capture["files"][key] for key in
                        ("reference", "candidate", "reference_run", "candidate_run", "calibration"))
                original_paths.add(str(qualification))
            index = gpu_verification.read_json(bundle / "artifacts-index.json")
            self.assertEqual(set(index), original_paths)
            for original, record in index.items():
                self.assertFalse(Path(original).exists())
                artifact = bundle / record["path"]
                self.assertTrue(artifact.is_file())
                self.assertFalse(Path(record["path"]).is_absolute())
                self.assertEqual(hashlib.sha256(artifact.read_bytes()).hexdigest(), record["sha256"])
            source_identity = gpu_verification.read_json(bundle / "source-program.json")
            self.assertEqual(hashlib.sha256((bundle / "uncommitted.diff").read_bytes()).hexdigest(),
                             source_identity["dirty_diff_sha256"])
            for name, checksum in source_identity["source_sha256"].items():
                self.assertEqual(hashlib.sha256((bundle / "source" / name).read_bytes()).hexdigest(), checksum)


if __name__ == "__main__":
    unittest.main()
