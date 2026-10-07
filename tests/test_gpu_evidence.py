"""GPU evidence policies, tested without importing SDL, Skia, or OpenGL."""

import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from gpu_evidence import ConfigError, EvidenceRecorder, RenderConfig, classify_renderer


class RenderConfigTests(unittest.TestCase):
    def test_unset_configuration_is_automatic_with_cpu_threaded_until_probed(self):
        config = RenderConfig.from_env({})
        self.assertTrue(config.auto)
        self.assertEqual((config.backend, config.raster_mode), ("cpu", "threaded"))
        self.assertFalse(config.strict)
        self.assertIsNone(config.evidence_path)
        self.assertIsNone(config.requested["backend"])
        self.assertIsNone(config.requested["raster_mode"])
        self.assertTrue(config.run_id)

    def test_explicit_backend_or_raster_mode_is_not_automatic(self):
        for env in ({"BROWSER_RENDER_BACKEND": "cpu"},
                    {"BROWSER_RENDER_BACKEND": "gpu", "BROWSER_RASTER_MODE": "sync"},
                    {"BROWSER_RASTER_MODE": "threaded"},
                    {"BROWSER_RENDER_BACKEND": "auto", "BROWSER_RASTER_MODE": "sync"}):
            with self.subTest(env=env):
                self.assertFalse(RenderConfig.from_env(env).auto)

    def test_raw_requested_values_survive_normalization(self):
        config = RenderConfig.from_env({
            "BROWSER_RENDER_BACKEND": " GPU ",
            "BROWSER_RASTER_MODE": " SYNC ",
            "BROWSER_GPU_STRICT": "TRUE",
            "BROWSER_GPU_RUN_ID": "capture-17",
        })
        self.assertEqual((config.backend, config.raster_mode), ("gpu", "sync"))
        self.assertTrue(config.strict)
        self.assertEqual(config.requested["backend"], " GPU ")
        self.assertEqual(config.requested["raster_mode"], " SYNC ")
        self.assertEqual(config.run_id, "capture-17")

    def test_explicit_invalid_or_empty_modes_never_fall_back(self):
        for key, value in (
            ("BROWSER_RENDER_BACKEND", "vulkan"),
            ("BROWSER_RENDER_BACKEND", ""),
            ("BROWSER_RASTER_MODE", "async"),
            ("BROWSER_RASTER_MODE", " "),
            ("BROWSER_GPU_STRICT", "perhaps"),
            ("BROWSER_GPU_STRICT", ""),
        ):
            with self.subTest(key=key, value=value):
                with self.assertRaises(ConfigError):
                    RenderConfig.from_env({key: value})

    def test_gpu_threaded_is_rejected_even_when_raster_mode_is_defaulted(self):
        with self.assertRaisesRegex(ConfigError, "GPU.*threaded"):
            RenderConfig.from_env({"BROWSER_RENDER_BACKEND": "gpu"})

    def test_config_error_saves_original_requested_settings_before_raising(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "nested" / "error.json"
            with self.assertRaises(ConfigError):
                RenderConfig.from_env({
                    "BROWSER_RENDER_BACKEND": "typo",
                    "BROWSER_RASTER_MODE": "sync",
                    "BROWSER_GPU_EVIDENCE": str(evidence),
                    "BROWSER_GPU_RUN_ID": "bad-config",
                })
            data = json.loads(evidence.read_text())
            self.assertEqual(data["config"]["requested"]["backend"], "typo")
            self.assertIsNone(data["config"]["actual"]["backend"])
            self.assertEqual(data["run_id"], "bad-config")
            self.assertEqual(data["failures"][0]["kind"], "configuration")
            self.assertEqual(data["gl_path_status"], "FAIL")
            self.assertEqual(data["hardware_status"], "PENDING")
            self.assertEqual(data["performance_status"], "PENDING")


class RendererClassificationTests(unittest.TestCase):
    def test_software_overrides_hardware_words_and_vendor(self):
        for renderer in (
            "llvmpipe (LLVM 20.1.2, 256 bits)", "softpipe",
            "Software Rasterizer", "Google SwiftShader",
            "Microsoft Basic Render Driver", "D3D12 (Microsoft Basic Render Driver)",
            "WARP", "Intel llvmpipe", "Mesa X11",
        ):
            with self.subTest(renderer=renderer):
                result = classify_renderer("NVIDIA Corporation", renderer)
                self.assertEqual(result["classification"], "software")
                self.assertTrue(result["reason"])

    def test_known_devices_are_only_hardware_candidates(self):
        for vendor, renderer in (
            ("Microsoft Corporation", "D3D12 (Intel(R) Iris(R) Xe Graphics)"),
            ("AMD", "AMD Radeon RX 6600 (radeonsi, navi23)"),
            ("Intel", "Mesa Intel(R) UHD Graphics 620 (KBL GT2)"),
            ("NVIDIA Corporation", "NVIDIA GeForce RTX 4070/PCIe/SSE2"),
        ):
            with self.subTest(renderer=renderer):
                self.assertEqual(classify_renderer(vendor, renderer)["classification"],
                                 "hardware_candidate")

    def test_vendor_and_unrecognized_renderers_do_not_prove_hardware(self):
        for vendor, renderer in (("NVIDIA", "Mystery renderer"), ("Intel", ""),
                                 ("Microsoft", "D3D12 (Adapter)"),
                                 ("AMD", "AMD Unknown"), (None, None)):
            with self.subTest(vendor=vendor, renderer=renderer):
                self.assertEqual(classify_renderer(vendor, renderer)["classification"], "unknown")


class EvidenceRecorderTests(unittest.TestCase):
    def make_recorder(self, evidence_path=None):
        env = {"BROWSER_RENDER_BACKEND": "gpu", "BROWSER_RASTER_MODE": "sync"}
        if evidence_path:
            env["BROWSER_GPU_EVIDENCE"] = str(evidence_path)
        return EvidenceRecorder(RenderConfig.from_env(env))

    def test_identity_and_frame_ids_are_real_and_unique(self):
        recorder = self.make_recorder()
        recorder.window(42, context_id="context-42", owner_native_tid=threading.get_native_id())
        first = recorder.frame(window_id=42, context_id="context-42")
        second = recorder.frame(window_id=42, context_id="context-42")
        self.assertEqual(recorder.data["pid"], os.getpid())
        self.assertEqual(recorder.data["native_tid"], threading.get_native_id())
        self.assertNotEqual(first["frame_id"], second["frame_id"])
        self.assertEqual(first["pid"], os.getpid())
        self.assertEqual(first["native_tid"], threading.get_native_id())
        self.assertGreaterEqual(second["monotonic_ns"], first["monotonic_ns"])
        self.assertEqual(recorder.data["windows"]["42"]["context_id"], "context-42")
        self.assertTrue(recorder.data["clock_anchor"]["utc"])

    def test_first_gl_frame_and_candidate_do_not_complete_acceptance(self):
        recorder = self.make_recorder()
        recorder.window(1, gl_vendor="Intel", gl_renderer="Intel(R) UHD Graphics 620")
        recorder.frame(window_id=1, submitted=True, swapped=True)
        recorder.data["gates"].update(l0="PASS", l2="PASS")
        data = recorder.finalize()
        self.assertEqual(data["gl_path_status"], "PENDING")
        self.assertEqual(data["hardware_status"], "PENDING")
        self.assertEqual(data["performance_status"], "PENDING")

    def test_independent_gates_can_complete_gl_with_software_hardware_failure(self):
        recorder = self.make_recorder()
        recorder.window(1, gl_renderer="llvmpipe")
        recorder.data["gates"].update(l0="PASS", l2="PASS", correctness="PASS", lifecycle="PASS")
        data = recorder.finalize()
        self.assertEqual(data["gl_path_status"], "PASS")
        self.assertEqual(data["hardware_status"], "FAIL")
        self.assertEqual(data["performance_status"], "PENDING")

    def test_unknown_window_prevents_hardware_pass_for_multiwindow_run(self):
        recorder = self.make_recorder()
        recorder.window(1, gl_renderer="NVIDIA GeForce RTX 4070")
        recorder.window(2, gl_renderer="Mystery renderer")
        recorder.data["gates"].update(l2="PASS", l3="PASS")
        self.assertEqual(recorder.finalize()["hardware_status"], "PENDING")

    def test_changed_context_does_not_reuse_old_renderer_classification(self):
        recorder = self.make_recorder()
        recorder.window(1, gl_renderer="NVIDIA GeForce RTX 4070")
        recorder.data["gates"].update(l2="PASS", l3="PASS")
        self.assertEqual(recorder.finalize()["hardware_status"], "PASS")
        recorder.window(1, gl_renderer="llvmpipe", context_id="replacement")
        self.assertEqual(recorder.finalize()["hardware_status"], "FAIL")

    def test_hardware_pass_requires_l2_and_l3_for_known_candidates(self):
        recorder = self.make_recorder()
        recorder.window(1, gl_renderer="NVIDIA GeForce RTX 4070")
        recorder.data["gates"].update(l2="PASS", l3="PASS")
        self.assertEqual(recorder.finalize()["hardware_status"], "PASS")
        self.assertEqual(recorder.data["gl_path_status"], "PENDING")

    def test_product_failure_remains_fail_without_complete_timings(self):
        recorder = self.make_recorder()
        recorder.fail("context lost before required frames completed", kind="product")
        data = recorder.finalize()
        self.assertEqual(data["gl_path_status"], "FAIL")
        self.assertEqual(data["performance_status"], "FAIL")
        self.assertEqual(data["hardware_status"], "PENDING")

    def test_measurement_fault_does_not_claim_product_failure_or_pass(self):
        recorder = self.make_recorder()
        recorder.data["gates"]["performance"] = "PASS"
        recorder.fail("counter clock cannot be aligned", kind="measurement")
        self.assertEqual(recorder.finalize()["performance_status"], "PENDING")

    def test_explicit_correctness_failure_fails_gl_and_performance(self):
        recorder = self.make_recorder()
        recorder.data["gates"].update(correctness="FAIL", performance="PASS")
        data = recorder.finalize()
        self.assertEqual(data["gl_path_status"], "FAIL")
        self.assertEqual(data["performance_status"], "FAIL")

    def test_performance_pass_requires_completed_correctness_gate(self):
        recorder = self.make_recorder()
        recorder.data["gates"]["performance"] = "PASS"
        self.assertEqual(recorder.finalize()["performance_status"], "PENDING")
        recorder.data["gates"]["correctness"] = "PASS"
        self.assertEqual(recorder.finalize()["performance_status"], "PASS")

    def test_write_is_optional_and_frames_do_not_serialize(self):
        recorder = self.make_recorder()
        with patch("gpu_evidence.json.dump", side_effect=AssertionError("unexpected serialization")):
            recorder.frame(window_id=1)
            recorder.event("idle")
            recorder.write()

    def test_atomic_file_contains_version_and_source_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence.json"
            recorder = self.make_recorder(evidence)
            recorder.window(1, gl_renderer="softpipe")
            recorder.event("idle", marker="L3-control")
            recorder.frame(window_id=1, submitted=True)
            recorder.finalize()
            recorder.write()
            data = json.loads(evidence.read_text())
            self.assertEqual(data["pid"], os.getpid())
            self.assertEqual(data["hardware_status"], "FAIL")
            self.assertIn("gpu_evidence.py", data["program"]["source_sha256"])
            self.assertIn("python", data["dependencies"])
            self.assertTrue(data["program"]["commit"])
            self.assertEqual(len(data["program"]["dirty_diff_sha256"]), 64)
            self.assertEqual(list(Path(directory).iterdir()), [evidence])

    def test_serialization_failure_preserves_existing_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence.json"
            evidence.write_text('{"previous":true}')
            recorder = self.make_recorder(evidence)
            recorder.data["bad"] = object()
            with self.assertRaises(TypeError):
                recorder.write()
            self.assertEqual(json.loads(evidence.read_text()), {"previous": True})
            self.assertEqual(list(Path(directory).iterdir()), [evidence])


if __name__ == "__main__":
    unittest.main()
