"""Automatic backend selection: GPU when a probe child draws, CPU otherwise."""

import os
import stat
import tempfile
import unittest
from unittest.mock import patch

import gpu_probe
from gpu_evidence import RenderConfig


def ok(renderer, env=None, classification="hardware_candidate"):
    return {"ok": True, "renderer": renderer, "classification": classification,
            "reason": "drew", "env": env or {}}


def failed(reason, env=None):
    return {"ok": False, "reason": reason, "env": env or {}}


class ResolveBackendTests(unittest.TestCase):
    def setUp(self):
        self.config = RenderConfig.from_env({})

    def test_working_gpu_selects_gpu_sync(self):
        env = {}
        config = gpu_probe.resolve_backend(self.config, [ok("AMD Radeon")], env)
        self.assertEqual((config.backend, config.raster_mode, config.auto), ("gpu", "sync", False))
        self.assertEqual(env, {})
        self.assertEqual(gpu_probe.describe_selection(config.selection), "Render backend: gpu (AMD Radeon)")

    def test_software_default_then_working_d3d12_applies_driver_env(self):
        env = {}
        results = [failed("software renderer llvmpipe"), ok("D3D12 (AMD)", {"GALLIUM_DRIVER": "d3d12"})]
        config = gpu_probe.resolve_backend(self.config, results, env)
        self.assertEqual(config.backend, "gpu")
        self.assertEqual(env, {"GALLIUM_DRIVER": "d3d12"})

    def test_no_working_gpu_falls_back_to_cpu_threaded(self):
        env = {}
        results = [failed("software renderer llvmpipe"), failed("GPU probe exited with code -11")]
        config = gpu_probe.resolve_backend(self.config, results, env)
        self.assertEqual((config.backend, config.raster_mode, config.auto), ("cpu", "threaded", False))
        self.assertEqual(env, {})
        self.assertEqual(gpu_probe.describe_selection(config.selection),
                         "Render backend: cpu (GPU unavailable: software renderer llvmpipe; "
                         "GPU probe exited with code -11)")

    def test_strict_mode_falls_back_instead_of_accepting_an_unknown_renderer(self):
        config = RenderConfig.from_env({"BROWSER_GPU_STRICT": "1"})
        resolved = gpu_probe.resolve_backend(config, [ok("Mystery GL", classification="unknown")], {})
        self.assertEqual(resolved.backend, "cpu")
        self.assertIn("strict mode rejected unknown", gpu_probe.describe_selection(resolved.selection))

    def test_unknown_renderer_that_draws_is_used_without_strict(self):
        resolved = gpu_probe.resolve_backend(self.config, [ok("Mystery GL", classification="unknown")], {})
        self.assertEqual(resolved.backend, "gpu")

    def test_later_attempts_are_not_consumed_after_a_success(self):
        consumed = []

        def results():
            for result in (ok("AMD Radeon"), failed("must not run")):
                consumed.append(result)
                yield result

        gpu_probe.resolve_backend(self.config, results(), {})
        self.assertEqual(len(consumed), 1)


class DriverAttemptTests(unittest.TestCase):
    def test_wsl_without_driver_override_also_tries_d3d12(self):
        with patch.object(gpu_probe.os.path, "exists", return_value=True):
            self.assertEqual(gpu_probe.gl_driver_attempts({}), [{}, {"GALLIUM_DRIVER": "d3d12"}])
            self.assertEqual(gpu_probe.gl_driver_attempts({"GALLIUM_DRIVER": "llvmpipe"}), [{}])

    def test_non_wsl_tries_only_the_default_driver(self):
        with patch.object(gpu_probe.os.path, "exists", return_value=False):
            self.assertEqual(gpu_probe.gl_driver_attempts({}), [{}])


REPORT = "echo noise; echo '{\"ok\": true, \"renderer\": \"X\", \"classification\": \"hardware_candidate\", \"reason\": \"drew\"}'"


class PendingProbeTests(unittest.TestCase):
    def fake_python(self, body):
        directory = tempfile.mkdtemp()
        path = os.path.join(directory, "python")
        with open(path, "w") as stream:
            stream.write("#!/bin/sh\n" + body + "\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return path

    def probe_results(self, body):
        with patch.object(gpu_probe.sys, "executable", self.fake_python(body)), \
                patch.object(gpu_probe.os.path, "exists", return_value=False):
            return list(gpu_probe.PendingProbes({}).results())

    def test_native_crash_in_probe_child_is_a_failed_attempt(self):
        [result] = self.probe_results("kill -SEGV $$")
        self.assertEqual(result, {"ok": False, "reason": "GPU probe exited with code -11", "env": {}})

    def test_hung_probe_child_times_out(self):
        with patch.object(gpu_probe, "PROBE_TIMEOUT_SEC", 0.2):
            [result] = self.probe_results("exec sleep 30")
        self.assertEqual(result, {"ok": False, "reason": "GPU probe timed out", "env": {}})

    def test_child_report_is_the_attempt_result(self):
        [result] = self.probe_results(REPORT)
        self.assertEqual(result, {"ok": True, "renderer": "X", "classification": "hardware_candidate",
                                  "reason": "drew", "env": {}})

    def test_unreadable_child_report_is_a_failed_attempt(self):
        [result] = self.probe_results("echo not-json")
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["reason"], "GPU probe printed an unreadable report")

    def test_finished_child_is_read_after_the_shared_deadline_passes(self):
        with patch.object(gpu_probe.sys, "executable", self.fake_python(REPORT)), \
                patch.object(gpu_probe.os.path, "exists", return_value=False):
            probes = gpu_probe.PendingProbes({})
            probes.children[0][1].wait()
            probes.deadline = gpu_probe.time.monotonic() - 1
            [result] = list(probes.results())
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
