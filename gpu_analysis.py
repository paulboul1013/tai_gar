"""Pure, reproducible Chapter 13 acceptance analysis.

JSON contract version 1 is illustrated by tests/test_gpu_analysis.py::valid_experiment.
An experiment freezes its identity, scenario hashes, three-configuration block order,
sample sizes, timeout, exclusion reasons, and seed before formal runs. Each independent
process exports one run, with raw frames and a renderer-batch completion denominator.
Interaction runs are separate and retain input -> effect frame -> present return IDs.
Capture/diagnostic/expected-negative runs remain in the report, never in speed samples.
Missing evidence is PENDING; an observed product failure always overrides PENDING.
CPU raster/draw share is reported as work attribution and has no eligibility minimum.

Image inputs are uint8 RGB/RGBA arrays. Regions have id, kind (geometry/blend_clip/text),
box [x0,y0,x1,y1], and pixel_tolerance. Calibration must contain positive fixtures and
all five independently identified negative fixture classes. Frozen thresholds and the
reference pixels are hash-bound; changed fixtures require a new calibration.
"""

import hashlib
import json
import math

import numpy as np


ANALYSIS_VERSION = "chapter13-v2"
CONFIGURATIONS = ("cpu_sync", "gpu_sync", "cpu_threaded")
IDENTITY_FIELDS = ("program", "platform", "driver", "dependencies", "font", "window_size",
                   "drawable_size", "dpi", "color_format", "msaa", "stencil", "cache_policy",
                   "scheduler", "present_policy")
LIFECYCLE_OPERATIONS = ("dual_window_context_switch", "resize_dpi", "minimize_restore",
                        "tab_navigation", "close_during_draw", "initialization_failure_cleanup",
                        "context_loss_stop_cleanup")
NEGATIVE_CLASSES = ("missing_text", "offset", "alpha", "clip", "stale")
REQUIRED_REGRESSIONS = ("small", "blend_clip", "scroll_raf")
DIAGNOSTIC_FLAGS = ("capture", "query", "trace", "profiler", "per_frame_finish")
CALIBRATION_TILE_SIZE = 8
COMPLETION_FLAGS = ("warmup_drained", "final_submitted", "tail_wait_included",
                    "present_excluded", "layout_excluded")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _number(value, minimum=0):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= minimum


def _integer(value, minimum=0):
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _status(statuses):
    values = list(statuses)
    return "FAIL" if "FAIL" in values else "PASS" if values and all(v == "PASS" for v in values) else "PENDING"


def _bootstrap(values, indexes, geometric=False):
    array = np.asarray(values, dtype=float)
    transformed = np.log(array) if geometric else array
    samples = np.mean(transformed[indexes], axis=1)
    estimate = float(np.mean(transformed))
    bounds = np.quantile(samples, [0.025, 0.975], method="linear")
    if geometric:
        estimate, bounds = float(np.exp(estimate)), np.exp(bounds)
    return dict(estimate=estimate, ci95=[float(bounds[0]), float(bounds[1])], paired_runs=len(values))


def _manifest_errors(manifest):
    errors = []
    if manifest.get("schema_version") != 1 or manifest.get("frozen") is not True:
        errors.append("manifest schema 1 must be frozen before formal runs")
    for field, minimum in [("warmup_frames", 60), ("measured_frames", 300), ("input_samples", 100)]:
        if not _integer(manifest.get(field), minimum):
            errors.append(f"{field} must be a fixed integer >= {minimum}")
    if not _integer(manifest.get("bootstrap_seed")) or manifest.get("bootstrap_resamples") != 10000:
        errors.append("predeclared integer seed and 10000 bootstrap resamples required")
    if not _number(manifest.get("timeout_seconds"), 0.001):
        errors.append("predeclared positive timeout required")
    if not manifest.get("experiment_id"):
        errors.append("experiment_id missing")
    identity = manifest.get("identity", {})
    if not isinstance(identity, dict) or any(key not in identity or identity[key] is None or identity[key] == "" or identity[key] == [] or identity[key] == {} for key in IDENTITY_FIELDS):
        errors.append("complete program/platform/rendering identity required")
    else:
        for key in ("window_size", "drawable_size"):
            size = identity[key]
            if not isinstance(size, list) or len(size) != 2 or not all(_integer(v, 1) for v in size):
                errors.append(key + " requires two positive integer pixel sizes")
        if not _number(identity["dpi"], 0.001) or not _integer(identity["msaa"]) or not _integer(identity["stencil"]):
            errors.append("DPI/MSAA/stencil values invalid")
    if not isinstance(manifest.get("invalid_run_reasons"), list):
        errors.append("predeclared measurement exclusion reasons required")
    blocks = manifest.get("blocks", [])
    if not isinstance(blocks, list) or len(blocks) < 10:
        errors.append("at least ten predeclared independent blocks required")
        blocks = blocks if isinstance(blocks, list) else []
    seen = set()
    for block in blocks:
        if not isinstance(block, dict):
            errors.append("invalid block")
            continue
        block_id = block.get("block_id")
        if not isinstance(block_id, (str, int)) or str(block_id) in seen:
            errors.append("unique block IDs required")
        seen.add(str(block_id))
        order = block.get("order")
        if not isinstance(order, list) or len(order) != 3 or not all(isinstance(v, str) for v in order) or set(order) != set(CONFIGURATIONS):
            errors.append("each frozen block order must contain the three configurations exactly once")
    scenarios = manifest.get("scenarios", [])
    if not isinstance(scenarios, list) or not scenarios or sum(s.get("role") == "primary" for s in scenarios if isinstance(s, dict)) != 1:
        errors.append("exactly one predeclared primary scenario required")
        scenarios = scenarios if isinstance(scenarios, list) else []
    seen_scenarios = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            errors.append("invalid scenario")
            continue
        scenario_id = scenario.get("id")
        if not isinstance(scenario_id, str):
            errors.append("unique scenario IDs required")
            continue
        if scenario_id in seen_scenarios:
            errors.append("unique scenario IDs required")
        seen_scenarios.add(scenario_id)
        if scenario.get("role") not in ("primary", "regression") or not isinstance(scenario.get("interaction"), bool):
            errors.append(f"{scenario_id}: role/interaction not fixed")
        if not scenario.get("workload_hash") or not scenario.get("snapshot_hash"):
            errors.append(f"{scenario_id}: workload/snapshot sequence hash missing")
        if scenario.get("role") == "primary":
            qualification = scenario.get("qualification", {})
            # Validate attribution data, without selecting scenes by drawing share.
            if not isinstance(qualification, dict) or qualification.get("visible_changes") is not True or qualification.get("rerasterized") is not True or not _number(qualification.get("cpu_raster_fraction")) or qualification.get("cpu_raster_fraction", 2) > 1 or not qualification.get("attribution"):
                errors.append(f"{scenario_id}: primary workload qualification incomplete")
    by_id = {s["id"]: s for s in scenarios if isinstance(s, dict) and isinstance(s.get("id"), str)}
    if any(name not in by_id or by_id[name].get("role") != "regression" for name in REQUIRED_REGRESSIONS):
        errors.append("small, blend_clip, and scroll_raf regression scenarios are required")
    if by_id.get("scroll_raf", {}).get("interaction") is not True:
        errors.append("scroll_raf requires the separate normal interaction gate")
    return errors


def _correctness_status(manifest):
    correctness = manifest.get("correctness", {})
    if not isinstance(correctness, dict):
        return "PENDING"
    if correctness.get("status") == "FAIL":
        return "FAIL"
    negatives = correctness.get("negative_rejections", {})
    if correctness.get("status") == "PASS" and correctness.get("frozen") is True and correctness.get("calibration_id") and isinstance(negatives, dict) and all(negatives.get(name) is True for name in NEGATIVE_CLASSES):
        return "PASS"
    return "PENDING"


def _validate_run(manifest, scenario, run):
    """Return reasons and explicit product failures, without discarding either."""
    reasons, failures = [], []
    run_id = run.get("run_id", "<missing>")
    if run.get("status") == "product_failure":
        failures.append(run_id)
    if run.get("status") != "ok":
        reasons.append("formal run status is not ok: " + str(run.get("status")))
    if run.get("experiment_id") != manifest.get("experiment_id"):
        reasons.append("experiment ID differs")
    if run.get("identity") != manifest.get("identity"):
        reasons.append("program/platform/font/rendering identity differs")
    for key in ("workload_hash", "snapshot_hash"):
        if run.get(key) != scenario.get(key):
            reasons.append(key + " differs")
    if run.get("warmup_frames") != manifest.get("warmup_frames"):
        reasons.append("warmup sample count differs from frozen manifest")
    diagnostics = run.get("diagnostics", {})
    if not isinstance(diagnostics, dict) or any(diagnostics.get(flag) is not False for flag in DIAGNOSTIC_FLAGS):
        reasons.append("formal run contains diagnostics or diagnostic policy is missing")
    if run.get("readback_count", 0) != 0:
        reasons.append("formal run contains CPU readback")
    frames = run.get("frames")
    if not isinstance(frames, list):
        reasons.append("raw frame records missing")
        frames = []
    else:
        expected = manifest.get("measured_frames", 300)
        expected = expected if _integer(expected) else 300
        if len(frames) != expected:
            reasons.append("frame count differs from frozen manifest")
            if run.get("status") == "ok" and len(frames) < expected:
                failures.append(run_id)
    frame_map = {}
    for frame in frames:
        if not isinstance(frame, dict) or not isinstance(frame.get("frame_id"), (str, int)):
            reasons.append("invalid raw frame ID")
            continue
        key = str(frame["frame_id"])
        if key in frame_map:
            reasons.append("duplicate raw frame ID")
        frame_map[key] = frame
        if not _number(frame.get("renderer_ns"), 0):
            reasons.append("invalid raw renderer timing")
        if frame.get("readback_count", 0) != 0:
            reasons.append("formal frame contains CPU readback")
    if run.get("kind") == "throughput":
        completion = run.get("completion", {})
        if not isinstance(completion, dict):
            completion = {}
        if completion.get("boundary") != "renderer_batch_completed" or any(completion.get(flag) is not True for flag in COMPLETION_FLAGS):
            reasons.append("renderer completion boundary is not proven")
        if not _number(completion.get("elapsed_ns"), 1):
            reasons.append("invalid batch completion denominator")
        completed = completion.get("completed_frames")
        expected = manifest.get("measured_frames", 300)
        expected = expected if _integer(expected) else 300
        if not _integer(completed) or completed != expected or completed != len(frames):
            reasons.append("completed work count differs")
            if _integer(completed) and completed < expected and run.get("status") == "ok":
                failures.append(run_id)
    if run.get("kind") == "interaction":
        inputs = run.get("inputs", [])
        if not isinstance(inputs, list):
            inputs = []
            reasons.append("raw input records missing")
        expected = manifest.get("input_samples", 100)
        expected = expected if _integer(expected) else 100
        if run.get("expected_inputs") != expected or len(inputs) != expected:
            reasons.append("input count differs from frozen manifest")
            if run.get("expected_inputs") == expected and len(inputs) < expected and run.get("status") == "ok":
                failures.append(run_id)
        ids = set()
        for sample in inputs:
            if not isinstance(sample, dict) or not isinstance(sample.get("input_id"), (str, int)):
                reasons.append("invalid input identity")
                continue
            input_id = sample["input_id"]
            if str(input_id) in ids:
                reasons.append("duplicate input ID")
            ids.add(str(input_id))
            if sample.get("clock_mapped") is not True or not _number(sample.get("event_ns")) or not _number(sample.get("present_return_ns")) or sample.get("present_return_ns", -1) < sample.get("event_ns", 0):
                reasons.append("input/present clock mapping is invalid")
            frame = frame_map.get(str(sample.get("frame_id")))
            if sample.get("effect_included") is False:
                failures.append(run_id)
                reasons.append("required input effect was not presented")
            elif sample.get("effect_included") is not True or frame is None or input_id not in frame.get("effect_input_ids", []):
                reasons.append("input-to-effect-frame causal evidence missing")
            elif frame.get("present_return_ns") != sample.get("present_return_ns"):
                reasons.append("input latency end differs from effect frame present return")
            else:
                first_effect = next((f for f in frames if isinstance(f, dict)
                                     and input_id in f.get("effect_input_ids", [])), None)
                if first_effect is not frame:
                    reasons.append("latency sample does not reference the first effect frame")
            if not isinstance(sample.get("timestamp_source"), str) or not sample["timestamp_source"].startswith("SDL event timestamp"):
                reasons.append("input timestamp source missing or different")
    return sorted(set(reasons)), sorted(set(failures))


def _hardware_status(gpu_runs):
    statuses = []
    for run in gpu_runs:
        hardware = run.get("hardware", {})
        if not isinstance(hardware, dict):
            statuses.append("PENDING")
            continue
        classification = hardware.get("classification")
        if classification == "software":
            statuses.append("FAIL")
        else:
            l3 = _mapping(hardware.get("l3"))
            gl = _mapping(run.get("gl_evidence"))
            phases = l3.get("phases", [])
            phases_valid = isinstance(phases, list) and all(isinstance(p, str) for p in phases)
            attributed = (isinstance(l3, dict) and l3.get("run_id") == run.get("run_id")
                          and l3.get("scope") in ("process", "context")
                          and l3.get("attribution_verified") is True
                          and l3.get("process_id") == run.get("pid")
                          and all(l3.get(key) for key in ("context_id", "adapter", "engine", "evidence_path", "evidence_sha256"))
                          and phases_valid and set(phases) >= {"idle", "rendering", "pause", "resume"})
            identified = (hardware.get("actual_context") is True and hardware.get("renderer")
                          and hardware.get("adapter") and hardware.get("host_adapter_match") is True)
            statuses.append("PASS" if classification == "hardware_candidate" and identified and attributed
                            and hardware.get("l3_status") == "PASS" and gl.get("l0_status") == "PASS"
                            and gl.get("l2_status") == "PASS" else "PENDING")
    return _status(statuses)


def analyze_experiment(manifest, runs):
    """Return three independent verdicts plus reproducible paired statistics.

    No raw input is modified. Every run is catalogued, including unsuccessful and
    excluded runs. An unfair/incomplete experiment may report descriptive statistics,
    but its performance verdict remains PENDING unless a product failure makes FAIL.
    Hardware PASS additionally requires current-context identification and attributable
    L3 raw-evidence references; a counter label or summary boolean is insufficient.
    """
    if not isinstance(manifest, dict) or not isinstance(runs, list):
        return dict(analysis_version=ANALYSIS_VERSION, gl_path_status="PENDING", hardware_status="PENDING",
                    performance_status="PENDING", reasons=["manifest object and raw run list required"],
                    scenarios={}, run_review=[], excluded_runs=[], product_failures=[])
    reasons = _manifest_errors(manifest)
    scenarios = {s["id"]: s for s in manifest.get("scenarios", []) if isinstance(s, dict) and isinstance(s.get("id"), str)}
    blocks = [b for b in manifest.get("blocks", []) if isinstance(b, dict)]
    matrix, reviews, excluded, failures, gpu_runs = {}, [], [], [], []
    run_ids, processes = set(), set()
    for run in runs:
        if not isinstance(run, dict):
            reasons.append("invalid raw run object")
            continue
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or not run_id or run_id in run_ids:
            reasons.append("unique raw run IDs required")
        run_ids.add(str(run_id))
        if run.get("status") == "product_failure":
            failures.append(run_id)
        if run.get("configuration") == "gpu_sync":
            gpu_runs.append(run)
        formal = run.get("kind") in ("throughput", "interaction") and run.get("status") != "expected_negative"
        if not formal:
            excluded.append(dict(run_id=run_id, reason=run.get("reason", run.get("status", run.get("kind"))),
                                 kind=run.get("kind"), status=run.get("status")))
            reviews.append(dict(run_id=run_id, included=False, reasons=["separate diagnostic/capture/negative run"]))
            continue
        process = run.get("independent_process_id")
        if not isinstance(process, (str, int)) or not process or str(process) in processes:
            reasons.append("formal runs must originate in distinct independent processes")
        processes.add(str(process))
        scenario_id = run.get("scenario_id")
        scenario = scenarios.get(scenario_id) if isinstance(scenario_id, str) else None
        if scenario is None:
            reasons.append("undeclared scenario in raw runs")
            continue
        if run.get("configuration") not in CONFIGURATIONS:
            reasons.append("undeclared configuration in raw runs")
            reviews.append(dict(run_id=run_id, included=False, reasons=["undeclared configuration"]))
            continue
        key = (run.get("scenario_id"), str(run.get("block_id")), run.get("kind"), run.get("configuration"))
        if key in matrix:
            reasons.append("duplicate scenario/block/kind/configuration run")
        matrix[key] = run
        run_reasons, product_failures = _validate_run(manifest, scenario, run)
        failures.extend(product_failures)
        if run.get("excluded") is True or run.get("status") in ("excluded", "measurement_fault"):
            reason = run.get("reason")
            if reason not in (manifest.get("invalid_run_reasons") or []):
                run_reasons.append("exclusion reason was not predeclared")
            excluded.append(dict(run_id=run_id, reason=reason, kind=run.get("kind"), status=run.get("status")))
            run_reasons.append("frozen formal slot is excluded; do not replace or extend experiment")
        reviews.append(dict(run_id=run_id, included=not run_reasons, reasons=run_reasons))
        reasons.extend(f"{run_id}: {reason}" for reason in run_reasons)
    # Reject extra runs/blocks rather than permitting optional stopping or replacement.
    expected_keys = set()
    for scenario in scenarios.values():
        kinds = ("throughput", "interaction") if scenario.get("interaction") else ("throughput",)
        for block in blocks:
            for kind in kinds:
                for configuration in CONFIGURATIONS:
                    expected_keys.add((scenario["id"], str(block.get("block_id")), kind, configuration))
                actual_order = [r.get("configuration") for r in runs if isinstance(r, dict)
                                and r.get("scenario_id") == scenario["id"] and str(r.get("block_id")) == str(block.get("block_id"))
                                and r.get("kind") == kind and r.get("status") != "expected_negative"]
                if actual_order != block.get("order"):
                    reasons.append(f"{scenario['id']}/{block.get('block_id')}/{kind}: order differs or fixed slots missing")
    if set(matrix) != expected_keys:
        reasons.append("formal runs do not match the complete frozen experiment matrix")
    correctness = _correctness_status(manifest)
    if correctness != "PASS":
        reasons.append("correctness calibration and screenshot gate incomplete or failed")
    statistics = {}
    valid_analysis = not _manifest_errors(manifest)
    if valid_analysis:
        indexes = np.random.default_rng(manifest["bootstrap_seed"]).integers(0, len(blocks), size=(10000, len(blocks)))
        for scenario in scenarios.values():
            scenario_id = scenario["id"]
            entry = dict(cpu_work_attribution=dict(_mapping(scenario.get("qualification"))))
            ratios, input_pairs = [], []
            raw_pairs = []
            for block in blocks:
                block_id = str(block["block_id"])
                triplet = [matrix.get((scenario_id, block_id, "throughput", config)) for config in CONFIGURATIONS]
                if all(r is not None and not _validate_run(manifest, scenario, r)[0] for r in triplet):
                    cpu, gpu, threaded = triplet
                    rates = {r["configuration"]: r["completion"]["completed_frames"] * 1e9 / r["completion"]["elapsed_ns"] for r in triplet}
                    ratios.append(rates["gpu_sync"] / rates["cpu_sync"])
                    raw_pairs.append(dict(block_id=block_id, throughputs_fps=rates, backend_ratio=ratios[-1]))
                if scenario.get("interaction"):
                    triplet = [matrix.get((scenario_id, block_id, "interaction", config)) for config in CONFIGURATIONS]
                    if all(r is not None and not _validate_run(manifest, scenario, r)[0] for r in triplet):
                        cpu, gpu, threaded = triplet
                        p95 = {r["configuration"]: float(np.quantile([(s["present_return_ns"] - s["event_ns"]) / 1e6 for s in r["inputs"]], 0.95, method="linear")) for r in triplet}
                        allowance = max(2, 0.1 * p95["cpu_threaded"])
                        input_pairs.append(dict(block_id=block_id, p95_ms=p95,
                                                delta_ms=p95["gpu_sync"] - p95["cpu_threaded"],
                                                allowance_ms=allowance,
                                                excess_ms=p95["gpu_sync"] - p95["cpu_threaded"] - allowance))
            if len(ratios) == len(blocks):
                estimate = _bootstrap(ratios, indexes, geometric=True)
                threshold = 1.1 if scenario["role"] == "primary" else 0.95
                passed = estimate["ci95"][0] > threshold if scenario["role"] == "primary" else estimate["ci95"][0] >= threshold
                estimate.update(status="PASS" if passed else "FAIL", threshold=threshold,
                                comparison="strictly greater" if scenario["role"] == "primary" else "greater or equal",
                                raw_pairs=raw_pairs)
                entry["throughput"] = estimate
            else:
                entry["throughput"] = dict(status="PENDING", paired_runs=len(ratios), required_runs=len(blocks), raw_pairs=raw_pairs)
            if scenario.get("interaction"):
                if len(input_pairs) == len(blocks):
                    excess = _bootstrap([p["excess_ms"] for p in input_pairs], indexes)
                    entry["interaction"] = dict(status="PASS" if excess["ci95"][1] <= 0 else "FAIL",
                                                excess_ms=excess, raw_pairs=input_pairs,
                                                metric="SDL timestamp to first effect frame present-call return")
                else:
                    entry["interaction"] = dict(status="PENDING", paired_runs=len(input_pairs), required_runs=len(blocks), raw_pairs=input_pairs)
            statistics[scenario_id] = entry
    numeric_status = _status(scene[kind]["status"] for scene in statistics.values()
                             for kind in ("throughput", "interaction") if kind in scene)
    # Numeric FAIL applies only to fair, complete experiments. Product FAIL persists.
    performance = "FAIL" if failures or correctness == "FAIL" else "PENDING" if reasons else numeric_status
    lifecycle = manifest.get("lifecycle", {})
    lifecycle_status = _status(lifecycle.get(op, "PENDING") for op in LIFECYCLE_OPERATIONS) if isinstance(lifecycle, dict) else "PENDING"
    gl_statuses = [correctness, lifecycle_status]
    for run in gpu_runs:
        gl = _mapping(run.get("gl_evidence"))
        gl_statuses.extend([gl.get("l0_status", "PENDING"), gl.get("l2_status", "PENDING")])
        formal_readback = run.get("kind") in ("throughput", "interaction") and (run.get("readback_count", 0) != 0 or any(isinstance(f, dict) and f.get("readback_count", 0) != 0 for f in (run.get("frames") or [])))
        if run.get("status") == "product_failure" or formal_readback:
            gl_statuses.append("FAIL")
    if not gpu_runs:
        gl_statuses.append("PENDING")
    return dict(analysis_version=ANALYSIS_VERSION, experiment_id=manifest.get("experiment_id"),
                gl_path_status=_status(gl_statuses), hardware_status=_hardware_status(gpu_runs),
                performance_status=performance, correctness_status=correctness,
                platform=_mapping(manifest.get("identity")).get("platform"),
                performance_scope="software GL" if any(_mapping(r.get("hardware")).get("classification") == "software" for r in gpu_runs) else "observed renderer; hardware verdict reported separately",
                statistics_method=dict(unit="paired independent three-configuration run block", bootstrap="numpy PCG64 paired block resampling",
                                       resamples=manifest.get("bootstrap_resamples"), seed=manifest.get("bootstrap_seed"),
                                       confidence="two-sided 95%", quantile="linear", numpy_version=np.__version__),
                scenarios=statistics, reasons=sorted(set(reasons)), run_review=reviews, excluded_runs=excluded,
                product_failures=sorted(set(str(f) for f in failures)))


def _image(array):
    image = np.asarray(array)
    if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] not in (3, 4) or min(image.shape[:2]) < 1:
        raise ValueError("images must be nonempty uint8 RGB/RGBA arrays")
    return image


def _image_hash(array):
    return hashlib.sha256(str(array.shape).encode() + array.tobytes()).hexdigest()


def _regions(regions, shape):
    if not isinstance(regions, list) or not regions:
        raise ValueError("explicit comparison regions required")
    seen, kinds = set(), set()
    coverage = np.zeros(shape[:2], dtype=bool)
    for region in regions:
        if not isinstance(region, dict) or not isinstance(region.get("id"), str) or region["id"] in seen:
            raise ValueError("region IDs must be unique strings")
        seen.add(region["id"])
        kinds.add(region.get("kind"))
        box = region.get("box")
        if not isinstance(box, list) or len(box) != 4 or not all(_integer(v) for v in box):
            raise ValueError("region boxes require four integer pixel coordinates")
        x0, y0, x1, y1 = box
        if not (x0 < x1 <= shape[1] and y0 < y1 <= shape[0]) or not _integer(region.get("pixel_tolerance"), 0) or region["pixel_tolerance"] > 255:
            raise ValueError("region bounds/tolerance invalid")
        coverage[y0:y1, x0:x1] = True
    if not {"geometry", "blend_clip", "text"} <= kinds:
        raise ValueError("geometry, blend_clip, and text regions required")
    if not np.all(coverage):
        raise ValueError("comparison regions must cover every image pixel")


def _region_metrics(reference, candidate, regions):
    metrics = {}
    for region in regions:
        x0, y0, x1, y1 = region["box"]
        delta = np.abs(reference[y0:y1, x0:x1].astype(np.int16) - candidate[y0:y1, x0:x1].astype(np.int16))
        tiles = {}
        for y in range(0, delta.shape[0], CALIBRATION_TILE_SIZE):
            for x in range(0, delta.shape[1], CALIBRATION_TILE_SIZE):
                tile = delta[y:y + CALIBRATION_TILE_SIZE, x:x + CALIBRATION_TILE_SIZE]
                tiles[f"{x},{y}"] = dict(mean_abs_error=float(np.mean(tile)),
                                          bad_pixel_fraction=float(np.mean(np.max(tile, axis=2) > region["pixel_tolerance"])))
        metrics[region["id"]] = dict(mean_abs_error=float(np.mean(delta)),
                                     bad_pixel_fraction=float(np.mean(np.max(delta, axis=2) > region["pixel_tolerance"])),
                                     max_channel_error=int(np.max(delta)), tiles=tiles)
    return metrics


def _threshold_verdict(metrics, thresholds):
    return all(value["mean_abs_error"] <= thresholds[name]["max_mean_abs_error"]
               and value["bad_pixel_fraction"] <= thresholds[name]["max_bad_pixel_fraction"]
               and all(tile["mean_abs_error"] <= thresholds[name]["tiles"][key]["max_mean_abs_error"]
                       and tile["bad_pixel_fraction"] <= thresholds[name]["tiles"][key]["max_bad_pixel_fraction"]
                       for key, tile in value["tiles"].items())
               for name, value in metrics.items())


def freeze_calibration(reference, positives, negatives, regions):
    """Derive regional limits only from benign fixtures, then challenge every negative.

    No post-result tolerance increase is accepted. This routine establishes comparator
    calibration; the caller must freeze its ID in the experiment before formal runs.
    """
    try:
        reference = _image(reference)
        _regions(regions, reference.shape)
        if not isinstance(positives, list) or not positives or not isinstance(negatives, dict) or any(name not in negatives for name in NEGATIVE_CLASSES):
            return dict(status="PENDING", frozen=False, reasons=["positive fixtures and all five negative classes required"])
        positive_arrays = [_image(p) for p in positives]
        negative_arrays = {name: _image(negatives[name]) for name in NEGATIVE_CLASSES}
        if any(p.shape != reference.shape for p in positive_arrays + list(negative_arrays.values())):
            return dict(status="PENDING", frozen=False, reasons=["calibration fixtures must have the reference dimensions"])
        positive_metrics = [_region_metrics(reference, candidate, regions) for candidate in positive_arrays]
        thresholds = {region["id"]: dict(max_mean_abs_error=max(metrics[region["id"]]["mean_abs_error"] for metrics in positive_metrics),
                                          max_bad_pixel_fraction=max(metrics[region["id"]]["bad_pixel_fraction"] for metrics in positive_metrics),
                                          tiles={key: dict(max_mean_abs_error=max(metrics[region["id"]]["tiles"][key]["mean_abs_error"] for metrics in positive_metrics),
                                                           max_bad_pixel_fraction=max(metrics[region["id"]]["tiles"][key]["bad_pixel_fraction"] for metrics in positive_metrics))
                                                 for key in positive_metrics[0][region["id"]]["tiles"]})
                      for region in regions}
        negative_metrics = {name: _region_metrics(reference, candidate, regions) for name, candidate in negative_arrays.items()}
        rejections = {name: not _threshold_verdict(metrics, thresholds) for name, metrics in negative_metrics.items()}
        payload = dict(analysis_version=ANALYSIS_VERSION, tile_size=CALIBRATION_TILE_SIZE, reference_hash=_image_hash(reference), shape=list(reference.shape),
                       regions=regions, thresholds=thresholds, positive_hashes=[_image_hash(p) for p in positive_arrays],
                       negative_hashes={name: _image_hash(p) for name, p in negative_arrays.items()},
                       negative_rejections=rejections, negative_metrics=negative_metrics)
        passed = all(rejections.values())
        # Copy nested configuration to prevent mutation through the caller's region list.
        payload = json.loads(json.dumps(payload))
        return dict(payload, calibration_id=_digest(payload), status="PASS" if passed else "FAIL", frozen=passed,
                    reasons=[] if passed else ["negative fixture accepted: " + name for name, rejected in rejections.items() if not rejected])
    except (ValueError, TypeError, KeyError) as error:
        return dict(status="PENDING", frozen=False, reasons=[str(error)])


def compare_images(reference, candidate, regions, calibration):
    """Compare every region independently using an intact, frozen calibration."""
    try:
        reference, candidate = _image(reference), _image(candidate)
        _regions(regions, reference.shape)
        if not isinstance(calibration, dict) or calibration.get("status") != "PASS" or calibration.get("frozen") is not True:
            return dict(status="PENDING", reasons=["successful frozen calibration required"], regions={})
        payload = {key: value for key, value in calibration.items() if key not in ("calibration_id", "status", "frozen", "reasons")}
        if _digest(payload) != calibration.get("calibration_id") or calibration.get("regions") != regions or calibration.get("reference_hash") != _image_hash(reference):
            return dict(status="PENDING", reasons=["calibration hash, reference, or regions changed"], regions={})
        if candidate.shape != reference.shape:
            return dict(status="FAIL", reasons=["candidate dimensions differ"], regions={})
        metrics = _region_metrics(reference, candidate, regions)
        thresholds = calibration["thresholds"]
        results = {name: dict(value, status="PASS" if _threshold_verdict({name: value}, thresholds) else "FAIL",
                             thresholds=thresholds[name]) for name, value in metrics.items()}
        return dict(status=_status(r["status"] for r in results.values()), regions=results,
                    calibration_id=calibration["calibration_id"], reference_hash=_image_hash(reference),
                    candidate_hash=_image_hash(candidate), reasons=[])
    except (ValueError, TypeError, KeyError) as error:
        return dict(status="PENDING", reasons=[str(error)], regions={})
