"""Acceptance-analysis contracts; synthetic measurements never prove GPU hardware."""

import copy
import unittest

import numpy as np

from gpu_analysis import analyze_experiment, compare_images, freeze_calibration


CONFIGURATIONS = ["cpu_sync", "gpu_sync", "cpu_threaded"]
OPERATIONS = ["dual_window_context_switch", "resize_dpi", "minimize_restore",
              "tab_navigation", "close_during_draw", "initialization_failure_cleanup",
              "context_loss_stop_cleanup"]


def valid_experiment():
    identity = dict(program="source-sha-and-diff", platform="linux-test", driver="test-driver",
                    dependencies="test-versions", font="resolved-font-sha", window_size=[80, 60],
                    drawable_size=[80, 60], dpi=1.0, color_format="RGBA8888", msaa=0,
                    stencil=8, cache_policy="fixed", scheduler="normal", present_policy="swap-0")
    manifest = dict(schema_version=1, experiment_id="experiment-1", frozen=True,
                    bootstrap_seed=42, bootstrap_resamples=10000, warmup_frames=60,
                    measured_frames=300, input_samples=100, timeout_seconds=30,
                    identity=identity, invalid_run_reasons=["measurement_fault", "clock_mapping"],
                    blocks=[dict(block_id=str(i), order=CONFIGURATIONS[i % 3:] + CONFIGURATIONS[:i % 3])
                            for i in range(10)],
                    scenarios=[dict(id=name, role=role, interaction=interaction,
                                    workload_hash="work-" + name, snapshot_hash="snap-" + name,
                                    qualification=dict(visible_changes=True, rerasterized=True,
                                                       cpu_raster_fraction=0.6, attribution="raw calibration trace"))
                               for name, role, interaction in [("main", "primary", False),
                                                                ("small", "regression", False),
                                                                ("blend_clip", "regression", False),
                                                                ("scroll_raf", "regression", True)]],
                    correctness=dict(status="PASS", calibration_id="frozen-calibration",
                                     frozen=True, negative_rejections={name: True for name in
                                     ["missing_text", "offset", "alpha", "clip", "stale"]}),
                    lifecycle={op: "PASS" for op in OPERATIONS})
    runs = []
    for scenario in manifest["scenarios"]:
        for block in manifest["blocks"]:
            for configuration in block["order"]:
                kinds = ["throughput", "interaction"] if scenario["interaction"] else ["throughput"]
                for kind in kinds:
                    number = len(runs) + 1
                    renderer_ns = 10000000 if configuration != "gpu_sync" else 7000000
                    frames = [dict(frame_id=i, renderer_ns=renderer_ns) for i in range(300)]
                    inputs = []
                    if kind == "interaction":
                        for i in range(100):
                            frames[i]["effect_input_ids"] = [i]
                            event_ns = 100000000 + i * 30000000
                            latency_ns = 15000000 if configuration == "gpu_sync" else 14000000
                            frames[i]["present_return_ns"] = event_ns + latency_ns
                            inputs.append(dict(input_id=i, frame_id=i, event_ns=event_ns,
                                               present_return_ns=event_ns + latency_ns,
                                               clock_mapped=True, effect_included=True,
                                               timestamp_source="SDL event timestamp mapped with SDL_GetTicks"))
                        for i in range(100, 300):
                            frames[i]["present_return_ns"] = 100000000 + i * 30000000 + latency_ns
                    runs.append(dict(run_id="run-" + str(number), experiment_id="experiment-1",
                                     scenario_id=scenario["id"], block_id=block["block_id"],
                                     configuration=configuration, kind=kind,
                                     identity=copy.deepcopy(identity), workload_hash=scenario["workload_hash"],
                                     snapshot_hash=scenario["snapshot_hash"], independent_process_id=number,
                                     pid=number, warmup_frames=60, frames=frames, status="ok",
                                     completion=dict(boundary="renderer_batch_completed",
                                                     elapsed_ns=300 * renderer_ns, completed_frames=300,
                                                     warmup_drained=True, final_submitted=True,
                                                     tail_wait_included=True, present_excluded=True,
                                                     layout_excluded=True),
                                     diagnostics={flag: False for flag in ["capture", "query", "trace", "profiler", "per_frame_finish"]},
                                     inputs=inputs, expected_inputs=len(inputs),
                                     hardware=dict(classification="software", l3_status="PENDING"),
                                     gl_evidence=dict(l0_status="PASS", l2_status="PASS")))
    return manifest, runs


class PerformanceAnalysisTests(unittest.TestCase):
    def test_reproducible_complete_experiment_separates_software_hardware(self):
        manifest, runs = valid_experiment()
        result = analyze_experiment(manifest, runs)
        self.assertEqual(result["performance_status"], "PASS")
        self.assertEqual(result["hardware_status"], "FAIL")
        self.assertEqual(result["gl_path_status"], "PASS")
        self.assertEqual(result, analyze_experiment(manifest, runs))
        self.assertAlmostEqual(result["scenarios"]["main"]["throughput"]["estimate"], 10 / 7)

    def test_raster_share_has_no_minimum_for_performance_comparison(self):
        for fraction in (0.0, 0.01, 0.312, 0.49, 0.5, 1.0):
            with self.subTest(fraction=fraction):
                manifest, runs = valid_experiment()
                manifest["scenarios"][0]["qualification"]["cpu_raster_fraction"] = fraction
                result = analyze_experiment(manifest, runs)
                self.assertEqual(result["performance_status"], "PASS")
                self.assertEqual(result["scenarios"]["main"]["cpu_work_attribution"]["cpu_raster_fraction"], fraction)
                self.assertAlmostEqual(result["scenarios"]["main"]["throughput"]["estimate"], 10 / 7)

    def test_low_raster_share_preserves_a_measured_slowdown_as_failure(self):
        manifest, runs = valid_experiment()
        manifest["scenarios"][0]["qualification"]["cpu_raster_fraction"] = 0.312
        for run in runs:
            if run["configuration"] == "gpu_sync" and run["scenario_id"] == "main":
                run["completion"]["elapsed_ns"] = 3000000000 / 0.8
        result = analyze_experiment(manifest, runs)
        self.assertEqual(result["performance_status"], "FAIL")
        self.assertAlmostEqual(result["scenarios"]["main"]["throughput"]["estimate"], 0.8)

    def test_raster_share_remains_valid_raw_attribution_data(self):
        for fraction in (-0.01, 1.01, float("nan"), float("inf"), True, None):
            with self.subTest(fraction=fraction):
                manifest, runs = valid_experiment()
                manifest["scenarios"][0]["qualification"]["cpu_raster_fraction"] = fraction
                self.assertEqual(analyze_experiment(manifest, runs)["performance_status"], "PENDING")

    def test_paired_run_uncertainty_does_not_treat_frames_as_independent(self):
        manifest, runs = valid_experiment()
        for run in runs:
            if run["configuration"] == "gpu_sync" and run["scenario_id"] == "main":
                ratio = 0.8 if int(run["block_id"]) < 5 else 2.0
                run["completion"]["elapsed_ns"] = int(3000000000 / ratio)
        result = analyze_experiment(manifest, runs)
        self.assertEqual(result["performance_status"], "FAIL")
        stats = result["scenarios"]["main"]["throughput"]
        self.assertEqual(stats["paired_runs"], 10)
        self.assertLess(stats["ci95"][0], 1.1)
        self.assertAlmostEqual(stats["estimate"], (0.8 * 2) ** 0.5, places=7)

    def test_primary_strict_threshold_and_regression_inclusive_threshold(self):
        manifest, runs = valid_experiment()
        for run in runs:
            if run["configuration"] == "gpu_sync" and run["scenario_id"] == "small":
                run["completion"]["elapsed_ns"] = 3000000000 / 0.95
        result = analyze_experiment(manifest, runs)
        self.assertEqual(result["scenarios"]["small"]["throughput"]["status"], "PASS")
        for run in runs:
            if run["configuration"] == "gpu_sync" and run["scenario_id"] == "main":
                run["completion"]["elapsed_ns"] = 3000000000 / 1.1
        self.assertEqual(analyze_experiment(manifest, runs)["performance_status"], "FAIL")

    def test_product_latency_uses_cpu_threaded_paired_excess(self):
        manifest, runs = valid_experiment()
        for run in runs:
            if run["kind"] == "interaction" and run["configuration"] == "gpu_sync":
                for sample in run["inputs"]:
                    sample["present_return_ns"] = sample["event_ns"] + 17000000
                    run["frames"][sample["frame_id"]]["present_return_ns"] = sample["present_return_ns"]
        result = analyze_experiment(manifest, runs)
        self.assertEqual(result["performance_status"], "FAIL")
        latency = result["scenarios"]["scroll_raf"]["interaction"]
        self.assertEqual(latency["excess_ms"]["estimate"], 1)

    def test_incomplete_fixed_experiment_is_pending(self):
        manifest, runs = valid_experiment()
        self.assertEqual(analyze_experiment(manifest, runs[:-1])["performance_status"], "PENDING")

    def test_product_failure_cannot_be_excluded_or_erased_by_missing_timings(self):
        manifest, runs = valid_experiment()
        runs[0].update(status="product_failure", excluded=True, reason="crash")
        runs[0].pop("completion")
        result = analyze_experiment(manifest, runs)
        self.assertEqual(result["performance_status"], "FAIL")
        self.assertIn("run-1", result["product_failures"])

    def test_required_effect_missing_is_product_failure(self):
        manifest, runs = valid_experiment()
        run = next(r for r in runs if r["kind"] == "interaction")
        run["inputs"][0]["effect_included"] = False
        self.assertEqual(analyze_experiment(manifest, runs)["performance_status"], "FAIL")

    def test_clock_failure_is_pending(self):
        manifest, runs = valid_experiment()
        run = next(r for r in runs if r["kind"] == "interaction")
        run["inputs"][0]["clock_mapped"] = False
        self.assertEqual(analyze_experiment(manifest, runs)["performance_status"], "PENDING")

    def test_latency_cannot_use_a_timestamp_unrelated_to_effect_frame(self):
        manifest, runs = valid_experiment()
        run = next(r for r in runs if r["kind"] == "interaction")
        run["inputs"][0]["present_return_ns"] += 1
        self.assertEqual(analyze_experiment(manifest, runs)["performance_status"], "PENDING")

    def test_latency_must_reference_the_first_effect_frame(self):
        manifest, runs = valid_experiment()
        run = next(r for r in runs if r["kind"] == "interaction")
        run["frames"][0]["effect_input_ids"] = [0, 1]
        self.assertEqual(analyze_experiment(manifest, runs)["performance_status"], "PENDING")

    def test_per_frame_readback_cannot_hide_behind_run_counter(self):
        manifest, runs = valid_experiment()
        run = next(r for r in runs if r["configuration"] == "gpu_sync")
        run["frames"][0]["readback_count"] = 1
        result = analyze_experiment(manifest, runs)
        self.assertEqual(result["performance_status"], "PENDING")
        self.assertEqual(result["gl_path_status"], "FAIL")

    def test_known_software_diagnostic_remains_hardware_failure(self):
        manifest, runs = valid_experiment()
        runs = [dict(run, kind="diagnostic") for run in runs]
        self.assertEqual(analyze_experiment(manifest, runs)["hardware_status"], "FAIL")

    def test_hardware_summary_without_attributable_raw_l3_is_pending(self):
        manifest, runs = valid_experiment()
        for run in runs:
            if run["configuration"] == "gpu_sync":
                run["hardware"] = dict(classification="hardware_candidate", l3_status="PASS")
        self.assertEqual(analyze_experiment(manifest, runs)["hardware_status"], "PENDING")

    def test_missing_identity_empty_identity_or_unknown_run_kind_fail_closed(self):
        for mutate in [lambda m, r: m["identity"].update(program=""),
                       lambda m, r: r[0].update(kind="mystery")]:
            manifest, runs = valid_experiment()
            mutate(manifest, runs)
            self.assertEqual(analyze_experiment(manifest, runs)["performance_status"], "PENDING")

    def test_empty_program_identity_shared_by_manifest_and_runs_is_pending(self):
        manifest, runs = valid_experiment()
        manifest["identity"]["program"] = ""
        for run in runs:
            run["identity"]["program"] = ""
        self.assertEqual(analyze_experiment(manifest, runs)["performance_status"], "PENDING")

    def test_required_regression_cannot_be_removed_from_manifest_and_runs(self):
        manifest, runs = valid_experiment()
        manifest["scenarios"] = [s for s in manifest["scenarios"] if s["id"] != "blend_clip"]
        runs = [r for r in runs if r["scenario_id"] != "blend_clip"]
        self.assertEqual(analyze_experiment(manifest, runs)["performance_status"], "PENDING")

    def test_malformed_evidence_is_pending_and_preserves_product_failures(self):
        for mutate in [lambda m, r: m.update(identity=None),
                       lambda m, r: m["blocks"][0].update(order=[None, None, None]),
                       lambda m, r: r[0].update(run_id=[]),
                       lambda m, r: r[0].update(hardware=None),
                       lambda m, r: r[0].update(configuration={})]:
            with self.subTest(mutation=mutate):
                manifest, runs = valid_experiment()
                mutate(manifest, runs)
                runs[-1].update(status="product_failure", reason="crash")
                result = analyze_experiment(manifest, runs)
                self.assertEqual(result["performance_status"], "FAIL")
                self.assertIn(runs[-1]["run_id"], result["product_failures"])

    def test_denominator_completion_boundary_must_be_proven(self):
        for key in ["tail_wait_included", "warmup_drained", "present_excluded", "layout_excluded", "final_submitted"]:
            with self.subTest(key=key):
                manifest, runs = valid_experiment()
                runs[0]["completion"][key] = False
                self.assertEqual(analyze_experiment(manifest, runs)["performance_status"], "PENDING")

    def test_missing_completed_work_is_failure(self):
        manifest, runs = valid_experiment()
        runs[0]["completion"]["completed_frames"] = 299
        self.assertEqual(analyze_experiment(manifest, runs)["performance_status"], "FAIL")

    def test_formal_query_capture_or_per_frame_finish_contamination_is_pending(self):
        for flag in ["capture", "query", "trace", "profiler", "per_frame_finish"]:
            with self.subTest(flag=flag):
                manifest, runs = valid_experiment()
                runs[0]["diagnostics"][flag] = True
                self.assertEqual(analyze_experiment(manifest, runs)["performance_status"], "PENDING")

    def test_inconsistent_identity_or_snapshot_is_pending(self):
        manifest, runs = valid_experiment()
        runs[0]["identity"]["font"] = "different-font"
        self.assertEqual(analyze_experiment(manifest, runs)["performance_status"], "PENDING")
        runs[0]["identity"] = manifest["identity"].copy()
        runs[0]["snapshot_hash"] = "different-snapshot"
        self.assertEqual(analyze_experiment(manifest, runs)["performance_status"], "PENDING")

    def test_duplicate_process_not_independent_sample(self):
        manifest, runs = valid_experiment()
        runs[1]["independent_process_id"] = runs[0]["independent_process_id"]
        self.assertEqual(analyze_experiment(manifest, runs)["performance_status"], "PENDING")

    def test_manifest_and_run_minima_and_invalid_values_fail_closed(self):
        mutations = [lambda m, r: m.update(frozen=False),
                     lambda m, r: m.update(warmup_frames=59),
                     lambda m, r: m.update(measured_frames=299),
                     lambda m, r: m.update(input_samples=99),
                     lambda m, r: m.update(bootstrap_resamples=500),
                     lambda m, r: r[0]["completion"].update(elapsed_ns=float("nan")),
                     lambda m, r: r[0]["frames"][1].update(frame_id=0)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                manifest, runs = valid_experiment()
                mutate(manifest, runs)
                self.assertEqual(analyze_experiment(manifest, runs)["performance_status"], "PENDING")

    def test_correctness_and_all_lifecycle_operations_gate_gl_path(self):
        manifest, runs = valid_experiment()
        manifest["lifecycle"].pop("minimize_restore")
        self.assertEqual(analyze_experiment(manifest, runs)["gl_path_status"], "PENDING")
        manifest["correctness"]["status"] = "FAIL"
        result = analyze_experiment(manifest, runs)
        self.assertEqual(result["gl_path_status"], "FAIL")
        self.assertEqual(result["performance_status"], "FAIL")

    def test_diagnostics_and_expected_negatives_preserved_but_not_speed_samples(self):
        manifest, runs = valid_experiment()
        negative = copy.deepcopy(runs[0])
        negative.update(run_id="negative", status="expected_negative", kind="diagnostic")
        runs.append(negative)
        result = analyze_experiment(manifest, runs)
        self.assertEqual(result["performance_status"], "PASS")
        self.assertEqual(result["excluded_runs"][0]["run_id"], "negative")


class RegionalComparatorTests(unittest.TestCase):
    def setUp(self):
        self.reference = np.zeros((10, 30, 4), dtype=np.uint8)
        self.reference[:, :, 3] = 255
        self.reference[2:8, 2:8, :3] = 255
        self.regions = [dict(id="text", kind="text", box=[0, 0, 10, 10], pixel_tolerance=3),
                        dict(id="geometry", kind="geometry", box=[10, 0, 20, 10], pixel_tolerance=3),
                        dict(id="blend", kind="blend_clip", box=[20, 0, 30, 10], pixel_tolerance=3)]
        self.negatives = {}
        for name in ["missing_text", "offset", "alpha", "clip", "stale"]:
            candidate = self.reference.copy()
            candidate[2:8, 2:8, :3] = 0
            self.negatives[name] = candidate

    def test_calibration_freezes_regional_thresholds_and_rejects_all_negatives(self):
        calibration = freeze_calibration(self.reference, [self.reference.copy()], self.negatives, self.regions)
        self.assertEqual(calibration["status"], "PASS")
        self.assertEqual(compare_images(self.reference, self.reference, self.regions, calibration)["status"], "PASS")
        for candidate in self.negatives.values():
            self.assertEqual(compare_images(self.reference, candidate, self.regions, calibration)["status"], "FAIL")

    def test_missing_negative_or_accepting_negative_cannot_freeze(self):
        self.negatives.pop("clip")
        calibration = freeze_calibration(self.reference, [self.reference], self.negatives, self.regions)
        self.assertEqual(calibration["status"], "PENDING")
        self.negatives["clip"] = self.reference.copy()
        self.assertEqual(freeze_calibration(self.reference, [self.reference], self.negatives, self.regions)["status"], "FAIL")

    def test_threshold_tampering_and_different_regions_are_pending(self):
        calibration = freeze_calibration(self.reference, [self.reference], self.negatives, self.regions)
        calibration["thresholds"]["text"]["max_bad_pixel_fraction"] = 1.0
        self.assertEqual(compare_images(self.reference, self.reference, self.regions, calibration)["status"], "PENDING")

    def test_small_region_failure_cannot_be_averaged_across_whole_image(self):
        calibration = freeze_calibration(self.reference, [self.reference], self.negatives, self.regions)
        candidate = self.reference.copy()
        candidate[2:8, 2:8, :3] = 0
        comparison = compare_images(self.reference, candidate, self.regions, calibration)
        self.assertEqual(comparison["status"], "FAIL")
        self.assertEqual(comparison["regions"]["text"]["status"], "FAIL")
        self.assertEqual(comparison["regions"]["geometry"]["status"], "PASS")

    def test_shape_difference_is_real_correctness_failure(self):
        calibration = freeze_calibration(self.reference, [self.reference], self.negatives, self.regions)
        self.assertEqual(compare_images(self.reference, self.reference[:5], self.regions, calibration)["status"], "FAIL")

    def test_fixed_local_tiles_reject_concentrated_omission_with_equal_region_error(self):
        reference = np.zeros((16, 48, 4), dtype=np.uint8)
        reference[:, :, 3] = 255
        reference[2:4, 2:4, :3] = 255
        regions = [dict(id=str(i), kind=kind, box=[i * 16, 0, (i + 1) * 16, 16], pixel_tolerance=3)
                   for i, kind in enumerate(["text", "geometry", "blend_clip"])]
        benign = reference.copy()
        for y in [0, 8]:
            for x in [0, 8, 16, 24, 32, 40]:
                benign[y, x, :3] = 255
        omission = reference.copy()
        omission[2:4, 2:4, :3] = 0
        negatives = {name: omission for name in ["missing_text", "offset", "alpha", "clip", "stale"]}
        calibration = freeze_calibration(reference, [benign], negatives, regions)
        self.assertEqual(calibration["status"], "PASS")
        self.assertEqual(compare_images(reference, benign, regions, calibration)["status"], "PASS")
        result = compare_images(reference, omission, regions, calibration)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["regions"]["0"]["bad_pixel_fraction"],
                         calibration["thresholds"]["0"]["max_bad_pixel_fraction"])

    def test_uncovered_pixels_cannot_be_ignored(self):
        regions = copy.deepcopy(self.regions)
        regions[-1]["box"][2] = 29
        calibration = freeze_calibration(self.reference, [self.reference], self.negatives, regions)
        self.assertEqual(calibration["status"], "PENDING")


if __name__ == "__main__":
    unittest.main()
