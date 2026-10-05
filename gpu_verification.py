"""Chapter 13 CLI: separate capture, replay, interaction, and analysis runs.

Run `python3 -B gpu_verification.py --help`. Formal experiments require a frozen,
qualified manifest and frozen image calibration. Smoke runs cannot satisfy gates.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import shutil
import subprocess
import sys
import time
import uuid

from gpu_evidence import dependency_versions, program_identity


CONFIGURATIONS = ("cpu_sync", "gpu_sync", "cpu_threaded")


def host_identity():
    return dict(program=program_identity(), platform=platform.platform(),
        driver={"kernel": platform.release(), "selection": {key: os.environ.get(key) for key in
            ("SDL_VIDEODRIVER", "LIBGL_ALWAYS_SOFTWARE", "GALLIUM_DRIVER", "MESA_D3D12_DEFAULT_ADAPTER_NAME")}},
        dependencies=dependency_versions())


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)


def randomized_blocks(count, seed):
    rng = random.Random(seed)
    blocks = []
    for index in range(count):
        order = list(CONFIGURATIONS)
        rng.shuffle(order)
        blocks.append(dict(block_id=str(index), order=order))
    return blocks


def seal_bundle(directory):
    directory = Path(directory)
    files = {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(directory.rglob("*")) if path.is_file() and path.name != "checksums.json"}
    value = dict(algorithm="sha256", files=files)
    write_json(directory / "checksums.json", value)
    return value


def collect_bundle_artifacts(directory, manifest):
    """Make source, calibration, capture and qualification proofs portable."""
    directory = Path(directory)
    root = Path(__file__).resolve().parent
    identity = program_identity()
    diff = subprocess.run(["git", "diff", "--binary", "HEAD", "--"], cwd=root,
                          capture_output=True, check=True).stdout
    (directory / "uncommitted.diff").write_bytes(diff)
    write_json(directory / "source-program.json", identity)
    for name in identity["source_sha256"]:
        target = directory / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, target)
    paths = set()
    for capture in manifest.get("correctness", {}).get("captures", []):
        import numpy as np
        from gpu_analysis import compare_images
        files = capture["files"]
        calibration = read_json(files["calibration"])
        actual = compare_images(np.load(files["reference"], allow_pickle=False),
            np.load(files["candidate"], allow_pickle=False), calibration["regions"], calibration)
        if actual != capture["result"] or actual["status"] != "PASS":
            raise ValueError("Frozen capture/calibration proof changed")
        for key, expected in capture.get("file_sha256", {}).items():
            if hashlib.sha256(Path(files[key]).read_bytes()).hexdigest() != expected:
                raise ValueError("Frozen proof file hash changed")
        paths.update(capture["files"][key] for key in
            ("reference", "candidate", "calibration", "reference_run", "candidate_run"))
    if manifest.get("qualification_proof"):
        proof = manifest["qualification_proof"]
        if hashlib.sha256(Path(proof["path"]).read_bytes()).hexdigest() != proof["sha256"]:
            raise ValueError("Frozen qualification proof changed")
        paths.add(manifest["qualification_proof"]["path"])
    index = {}
    for name in sorted(paths):
        source = Path(name)
        checksum = hashlib.sha256(source.read_bytes()).hexdigest()
        target = directory / "proof" / (checksum + "-" + source.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        index[str(source.resolve())] = dict(path=str(target.relative_to(directory)), sha256=checksum)
    write_json(directory / "artifacts-index.json", index)


def validate_formal_manifest(manifest):
    from gpu_analysis import _manifest_errors, _correctness_status
    errors = _manifest_errors(manifest)
    if _correctness_status(manifest) != "PASS":
        errors.append("frozen correctness calibration and screenshot gate required")
    captures = manifest.get("correctness", {}).get("captures", [])
    expected = {(scene["id"], phase) for scene in manifest.get("scenarios", []) for phase in (0, 1)}
    if (not captures or {(capture.get("scenario_id"), capture.get("phase")) for capture in captures} != expected
        or len(captures) != len(expected)):
        errors.append("raw calibrated capture pairs for every scenario and phase required")
    proof_ref = manifest.get("qualification_proof")
    if not proof_ref:
        errors.append("raw CPU qualification proof required")
    else:
        proof_path = Path(proof_ref["path"])
        if hashlib.sha256(proof_path.read_bytes()).hexdigest() != proof_ref["sha256"]:
            errors.append("qualification proof hash differs")
        proof = read_json(proof_path)
        if proof.get("program") != manifest.get("identity", {}).get("program"):
            errors.append("qualification program differs")
        for scene in manifest.get("scenarios", []):
            recorded = proof.get("scenarios", {}).get(scene["id"], {})
            if recorded.get("qualification") != scene.get("qualification"):
                errors.append("qualification summary differs from raw proof: " + scene["id"])
            raw_frames = recorded.get("raw_frames", [])
            main_ns = recorded.get("main_preparation_ns", 0)
            raster_ns = sum(frame.get("renderer_ns", 0) for frame in raw_frames)
            if (len(raw_frames) < 2 or main_ns <= 0 or raster_ns <= 0
                or not all(frame.get("tab_raster") for frame in raw_frames)
                or len({frame.get("pixels_sha256") for frame in raw_frames}) < 2
                or recorded.get("workload_hash") != scene["workload_hash"]
                or raster_ns / (raster_ns + main_ns) != scene["qualification"]["cpu_raster_fraction"]):
                errors.append("qualification cannot be recomputed from raw workload timings: " + scene["id"])
    if errors:
        raise ValueError("; ".join(errors))


def qualify(args):
    """Save visible reraster proof and CPU work attribution, without a share gate."""
    import browser
    from gpu_workload import build_snapshots
    manifest = read_json(args.manifest)
    if manifest["frozen"]:
        raise ValueError("A frozen experiment requires a new manifest for qualification")
    proof = {}
    for scene in manifest["scenarios"]:
        snapshots, metadata = build_snapshots(scene["id"], count=2)
        state = browser.RasterWindowState()
        window = type("QualificationWindow", (), {"raster_id": 0, "window_id": 0,
            "scene_epoch": 0, "width": 800, "height": 600})()
        raw = []
        for index, snapshot in enumerate(snapshots):
            started = time.perf_counter_ns()
            result = state.render(render_work(browser, window, snapshot, index + 1))
            raw.append(dict(renderer_ns=time.perf_counter_ns() - started,
                            pixels_sha256=hashlib.sha256(result.pixels).hexdigest(), tab_raster=True))
        renderer_ns = sum(record["renderer_ns"] for record in raw)
        fraction = renderer_ns / (renderer_ns + metadata["preparation_ns"])
        visible = raw[0]["pixels_sha256"] != raw[1]["pixels_sha256"]
        qualification = dict(visible_changes=visible, rerasterized=True,
            cpu_raster_fraction=fraction, attribution="qualification.json: real Tab style/layout/paint plus RasterWindowState raster/draw/CPU pixels; no idle or present")
        scene["qualification"] = qualification
        proof[scene["id"]] = dict(qualification=qualification, raw_frames=raw,
            main_preparation_ns=metadata["preparation_ns"], workload_hash=metadata["workload_hash"],
            font=metadata["font"])
    write_json(args.output, dict(program=program_identity(), scenarios=proof))
    manifest["qualification_proof"] = dict(path=str(Path(args.output).resolve()),
        sha256=hashlib.sha256(Path(args.output).read_bytes()).hexdigest())
    write_json(args.manifest, manifest)


def calibrate(args):
    import numpy as np
    from gpu_analysis import freeze_calibration
    negatives = {key: np.load(path, allow_pickle=False) for key, path in
                 (value.split("=", 1) for value in args.negative)}
    result = freeze_calibration(np.load(args.reference, allow_pickle=False),
        [np.load(path, allow_pickle=False) for path in args.positive], negatives, read_json(args.regions))
    write_json(args.output, result)


def compare(args):
    import numpy as np
    from gpu_analysis import compare_images
    calibration = read_json(args.calibration)
    result = compare_images(np.load(args.reference, allow_pickle=False),
        np.load(args.candidate, allow_pickle=False), calibration["regions"], calibration)
    result["files"] = {name: dict(path=str(Path(path).resolve()), sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())
                       for name, path in (("reference", args.reference), ("candidate", args.candidate), ("calibration", args.calibration))}
    write_json(args.output, result)


def freeze(args):
    """Verify capture pairs against frozen calibration before starting formal runs."""
    import numpy as np
    from gpu_analysis import compare_images
    manifest = read_json(args.manifest)
    if manifest["frozen"]:
        raise ValueError("A frozen experiment is immutable; create a new experiment")
    index = read_json(args.capture_index)
    expected = {(s["id"], phase) for s in manifest["scenarios"] for phase in (0, 1)}
    found, results, calibrations, driver_baselines = set(), [], [], []
    for entry in index:
        key = (entry["scenario_id"], entry["phase"])
        if key in found:
            raise ValueError("duplicate correctness capture pair")
        found.add(key)
        calibration = read_json(entry["calibration"])
        result = compare_images(np.load(entry["reference"], allow_pickle=False),
            np.load(entry["candidate"], allow_pickle=False), calibration["regions"], calibration)
        capture_runs = []
        for name, configuration, pixel_field in (("reference_run", "cpu_sync", "reference"),
                                                  ("candidate_run", "gpu_sync", "candidate")):
            run = read_json(entry[name])
            scene = next(s for s in manifest["scenarios"] if s["id"] == entry["scenario_id"])
            if (run.get("kind") != "capture" or run.get("status") != "ok" or run.get("configuration") != configuration
                or run.get("identity") != manifest["identity"] or run.get("workload_hash") != scene["workload_hash"]
                or run.get("snapshot_hash") != scene["snapshot_hash"]):
                raise ValueError("capture identity/sequence differs from experiment")
            pixel_path = Path(entry[pixel_field]).resolve()
            pixel_hash = hashlib.sha256(pixel_path.read_bytes()).hexdigest()
            if not any(record.get("phase") == entry["phase"] and record.get("sha256") == pixel_hash
                and Path(record.get("path", "")).resolve() == pixel_path for record in run.get("captures", [])):
                raise ValueError("capture pixels/phase are not bound to the actual exported frame")
            capture_runs.append(run["run_id"])
            if configuration == "gpu_sync":
                hardware = run.get("hardware", {})
                baseline = {field: hardware.get(field) for field in ("vendor", "renderer", "version")}
                if not all(baseline.values()):
                    raise ValueError("Actual GPU context driver baseline is missing")
                driver_baselines.append(baseline)
        if capture_runs[0] == capture_runs[1]:
            raise ValueError("CPU/GPU captures require distinct runs")
        results.append(dict(scenario_id=key[0], phase=key[1], result=result, files=entry,
            file_sha256={name: hashlib.sha256(Path(entry[name]).read_bytes()).hexdigest() for name in
                ("reference", "candidate", "calibration", "reference_run", "candidate_run")}))
        calibrations.append(calibration)
    if found != expected or any(result["result"]["status"] != "PASS" for result in results):
        raise ValueError("Every scenario and both alternating states require a passing calibrated capture")
    manifest["correctness"] = dict(status="PASS", frozen=True,
        calibration_id=hashlib.sha256(json.dumps(calibrations, sort_keys=True).encode()).hexdigest(),
        negative_rejections={name: all(c["negative_rejections"].get(name) is True for c in calibrations)
                             for name in ("missing_text", "offset", "alpha", "clip", "stale")},
        captures=results)
    manifest["frozen"] = True
    if any(baseline != driver_baselines[0] for baseline in driver_baselines):
        raise ValueError("GPU capture driver baselines differ")
    manifest["identity"]["driver"]["gpu_context_baseline"] = driver_baselines[0]
    validate_formal_manifest(manifest)
    write_json(args.output, manifest)


def observation_template(args):
    write_json(args.output, dict(run_id=None, process_id=None, context_id=None, scope="PENDING",
        adapter=None, engine=None, attribution_verified=False, evidence_path=None, evidence_sha256=None,
        phases=[dict(name=name, utc=None, monotonic_ns=None, marker=None) for name in
                ("idle", "rendering", "pause", "resume")],
        limitations="System/WSL aggregate GPU counters do not establish Tai Gar context attribution"))


def prepare(args):
    from gpu_analysis import LIFECYCLE_OPERATIONS
    from gpu_workload import SCENARIOS, build_snapshots
    if args.blocks < 10 or args.warmup < 60 or args.frames < 300:
        raise ValueError("Prepare requires >=10 blocks, >=60 warmup, >=300 measured frames")
    scenes, font = [], []
    for name, settings in SCENARIOS.items():
        _, metadata = build_snapshots(name, count=args.warmup + args.frames)
        font = metadata["font"]
        scenes.append(dict(id=name, role=settings["role"], interaction=settings["interaction"],
            workload_hash=metadata["workload_hash"], snapshot_hash=metadata["snapshot_hash"],
            qualification=dict(visible_changes=False, rerasterized=False, cpu_raster_fraction=None,
                               attribution=None)))
    identity = dict(**host_identity(), font=font, window_size=[800, 600], drawable_size=[800, 600],
        dpi=1.0, color_format="RGBA8888", msaa=0, stencil=8,
        cache_policy="bounded tab interest region; reraster alternating DOM styles",
        scheduler={"frame": "adaptive", "poll": "normal"}, present_policy="backend default; GPU swap interval 0")
    manifest = dict(schema_version=1, experiment_id=str(uuid.uuid4()), frozen=False,
        bootstrap_seed=args.seed, bootstrap_resamples=10000, warmup_frames=args.warmup,
        measured_frames=args.frames, input_samples=100, timeout_seconds=args.timeout,
        identity=identity, invalid_run_reasons=["measurement_fault", "clock_mapping"],
        blocks=randomized_blocks(args.blocks, args.seed), scenarios=scenes,
        correctness={"status": "PENDING"}, lifecycle={name: "PENDING" for name in LIFECYCLE_OPERATIONS})
    write_json(args.output, manifest)


def render_work(browser, window, snapshot, frame_id):
    return browser.RasterWork(raster_id=window.raster_id, window_id=window.window_id,
        scene_epoch=window.scene_epoch, frame_id=frame_id, active_tab_key=1,
        page_state=snapshot, chrome_display_list=(), width=window.width, height=window.height,
        chrome_bottom=100, chrome_raster=True, tab_raster=True, estimator_tab=None, title=snapshot.title)


def replay(browser, app, window, snapshots, warmup, count, capture_path=None, query=False):
    from gpu_workload import NullMeasure
    gpu = browser.RENDER_BACKEND == "gpu"
    if not query:
        app.raster.measure = NullMeasure()
    def execute(index):
        work = render_work(browser, window, snapshots[index], index + 1)
        if gpu:
            window.make_gl_current()
            window.check_gpu_context()
            if window.query_ring is not None:
                window.query_ring.begin(work.frame_id)
            result = app.raster.render_sync(work, state=window.gpu_raster_state)
            if window.query_ring is not None:
                window.query_ring.end(window.skia_context.flushAndSubmit)
        elif browser.RASTER_EXECUTION_MODE == "sync":
            result = app.raster.render_sync(work)
        else:
            if not app.raster.submit(work):
                raise RuntimeError("raster worker rejected required work")
            deadline = time.monotonic() + 10
            result = None
            while result is None and time.monotonic() < deadline:
                result = app.raster.take_result(window.raster_id)
                if result is None:
                    time.sleep(0.0001)
            if result is None or isinstance(result, browser.RasterFailure):
                raise RuntimeError("raster worker failed to complete required work")
        return result
    for index in range(warmup):
        execute(index)
    if gpu:
        window.skia_context.flushAndSubmit()
        browser.get_opengl_gl().glFinish()
    process_started = time.process_time_ns()
    started = time.perf_counter_ns()
    frames, captures = [], []
    for index in range(warmup, warmup + count):
        result = execute(index)
        frames.append(dict(frame_id=result.frame_id, renderer_ns=int(result.elapsed_sec * 1e9),
                           effect_input_ids=[], tab_raster=True, visible_phase=index % 2))
        if capture_path and index < warmup + 2:
            import numpy as np
            state = window.gpu_raster_state if gpu else app.raster.states[window.raster_id]
            path = Path(capture_path).with_suffix(".phase-{}.npy".format(index % 2))
            np.save(path, state.root_surface.makeImageSnapshot().toarray(colorType=browser.skia.kRGBA_8888_ColorType))
            captures.append(dict(phase=index % 2, frame_id=result.frame_id, path=str(path.resolve()),
                                 sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            app.evidence.event("capture_readback", frame_id=result.frame_id, readback_count=1)
    if gpu:
        window.skia_context.flushAndSubmit()
        browser.get_opengl_gl().glFinish()
    elapsed_ns = time.perf_counter_ns() - started
    cpu_ns = time.process_time_ns() - process_started
    resources = window.gpu_raster_state.resource_evidence() if gpu else {}
    if capture_path:
        import numpy as np
        state = window.gpu_raster_state if gpu else app.raster.states[window.raster_id]
        np.save(capture_path, state.root_surface.makeImageSnapshot().toarray(colorType=browser.skia.kRGBA_8888_ColorType))
        app.evidence.event("capture_readback", frame_id=frames[-1]["frame_id"], readback_count=1)
    return dict(frames=frames, process_cpu_ns=cpu_ns, resources=resources, captures=captures,
        completion=dict(boundary="renderer_batch_completed", elapsed_ns=elapsed_ns,
            completed_frames=len(frames), warmup_drained=True, final_submitted=True,
            tail_wait_included=True, present_excluded=True, layout_excluded=True))


def interaction(browser, app, scenario_id, warmup, count, expected_inputs, timeout):
    import ctypes
    from gpu_workload import fixture_server
    with fixture_server(scenario_id) as url:
        window = app.new_window(browser.URL(url))
        deadline = time.monotonic() + timeout
        injected = 0
        after_frame = 0
        origin = None
        event = browser.sdl2.SDL_Event()
        while origin is None or len(app.evidence.data["frames"]) - origin < warmup + count:
            if time.monotonic() >= deadline:
                raise RuntimeError("normal pipeline did not finish required frames/inputs")
            while browser.sdl2.SDL_PollEvent(ctypes.byref(event)):
                app.dispatch_event(event)
            if origin is None and window.active_committed_state() is not None:
                origin = len(app.evidence.data["frames"])
            completed = len(app.evidence.data["frames"]) - origin if origin is not None else 0
            # One causal input in flight. SDL supplies the event timestamp.
            if completed >= warmup and completed >= after_frame and injected < expected_inputs:
                wheel = browser.sdl2.SDL_Event()
                wheel.type = browser.sdl2.SDL_MOUSEWHEEL
                wheel.wheel.windowID = window.window_id
                wheel.wheel.y = -1 if injected % 2 == 0 else 1
                if browser.sdl2.SDL_PushEvent(ctypes.byref(wheel)) != 1:
                    raise RuntimeError("SDL failed to enqueue required input")
                injected += 1
                after_frame = completed + 3
            app._service_browser_work()
            browser.sdl2.SDL_Delay(app._browser_wait_timeout_ms())
        frames = app.evidence.data["frames"][origin + warmup:origin + warmup + count]
        samples = list(app.inputs.values())
        if len(samples) != expected_inputs or any(not sample.get("effect_included") for sample in samples):
            raise RuntimeError("required input effect was not presented")
        return dict(frames=frames, inputs=samples, expected_inputs=expected_inputs,
                    cadence=window.active_tab.frame_time_estimator.snapshot())


def worker(args):
    manifest = read_json(args.manifest)
    output = Path(args.output)
    backend, mode = args.configuration.split("_")
    os.environ.update(BROWSER_RENDER_BACKEND=backend, BROWSER_RASTER_MODE=mode,
        BROWSER_GPU_EVIDENCE=str(output.with_suffix(".evidence.json")),
        BROWSER_TRACE_FILE=str(output.with_suffix(".trace.json")), BROWSER_GPU_SWAP_INTERVAL="0")
    if getattr(args, "strict", False):
        os.environ["BROWSER_GPU_STRICT"] = "1"
    if args.kind == "query":
        os.environ["BROWSER_GPU_QUERY"] = "1"
    else:
        os.environ.pop("BROWSER_GPU_QUERY", None)
    import browser
    from gpu_workload import build_snapshots
    scene = next(s for s in manifest["scenarios"] if s["id"] == args.scenario)
    run_id = browser.RENDER_CONFIG.run_id
    run = dict(run_id=run_id, experiment_id=manifest["experiment_id"], scenario_id=args.scenario,
        block_id=args.block, configuration=args.configuration, kind=args.kind, pid=os.getpid(),
        independent_process_id=run_id, identity=manifest["identity"],
        workload_hash=scene["workload_hash"], snapshot_hash=scene["snapshot_hash"],
        warmup_frames=manifest["warmup_frames"], frames=[], status="product_failure",
        diagnostics={name: args.kind == name for name in ("capture", "query", "trace", "profiler", "per_frame_finish")},
        readback_count=1 if args.kind == "capture" else 0)
    write_json(output, run)
    app = None
    try:
        observed = host_identity()
        observed["driver"] = dict(manifest["identity"]["driver"], **observed["driver"])
        run["identity"] = dict(manifest["identity"], **observed)
        if any(observed[key] != manifest["identity"][key] for key in observed):
            run.update(status="measurement_fault", reason="observed program/platform/driver/dependencies changed")
            return
        app = browser.BrowserApp()
        app.measure.disabled = args.kind not in ("query", "capture")
        snapshots, metadata = build_snapshots(args.scenario,
            count=manifest["warmup_frames"] + manifest["measured_frames"])
        if metadata["snapshot_hash"] != scene["snapshot_hash"] or metadata["font"] != manifest["identity"]["font"]:
            run.update(status="measurement_fault", reason="workload sequence or resolved font differs")
            return
        if args.kind == "interaction":
            run.update(interaction(browser, app, args.scenario, manifest["warmup_frames"],
                manifest["measured_frames"], manifest["input_samples"], manifest["timeout_seconds"]))
        else:
            window = browser.BrowserWindow(app)
            app.windows.append(window)
            app.windows_by_id[window.window_id] = window
            run.update(replay(browser, app, window, snapshots, manifest["warmup_frames"],
                manifest["measured_frames"], str(output.with_suffix(".npy")) if args.kind == "capture" else None,
                query=args.kind == "query"))
            if args.kind == "capture":
                run["readback_count"] = len(run["captures"]) + 1
            if backend == "gpu" and (list(window.drawable_size) != manifest["identity"]["drawable_size"]
                or window.gl_attributes["MULTISAMPLESAMPLES"] != manifest["identity"]["msaa"]
                or window.gl_attributes["STENCIL_SIZE"] != manifest["identity"]["stencil"]):
                run.update(status="measurement_fault", reason="actual drawable/attributes differ")
                return
        current_window = app.windows[0]
        if backend == "gpu":
            run["identity"]["drawable_size"] = list(current_window.drawable_size)
            run["identity"]["msaa"] = current_window.gl_attributes["MULTISAMPLESAMPLES"]
            run["identity"]["stencil"] = current_window.gl_attributes["STENCIL_SIZE"]
            window_evidence = app.evidence.data["windows"][str(current_window.window_id)]
            if "gpu_context_baseline" in manifest["identity"]["driver"]:
                run["identity"]["driver"]["gpu_context_baseline"] = {
                    key: window_evidence["gl_" + key] for key in ("vendor", "renderer", "version")}
        run["identity"]["window_size"] = [current_window.width, current_window.height]
        if run["identity"] != manifest["identity"]:
            run.update(status="measurement_fault", reason="observed rendering identity differs")
            return
        run["status"] = "ok"
        gpu_windows = list(app.evidence.data["windows"].values())
        if gpu_windows:
            window = gpu_windows[0]
            run["hardware"] = dict(window["renderer_classification"], actual_context=True,
                                   l3_status="PENDING")
        else:
            run["hardware"] = dict(classification="unknown", l3_status="PENDING")
        run["gl_evidence"] = dict(l0_status=app.evidence.data["gates"]["l0"], l2_status="PENDING")
    except Exception as exc:
        run.update(status="product_failure", reason=repr(exc))
        policy_rejection = app is not None and any(failure["kind"] == "policy" for failure in app.evidence.data["failures"])
        if policy_rejection and args.kind == "diagnostic":
            run["status"] = "expected_negative"
        elif app is not None:
            app.evidence.fail(str(exc))
        raise
    finally:
        if app is not None:
            gpu_windows = list(app.evidence.data["windows"].values())
            if gpu_windows and "renderer_classification" in gpu_windows[0]:
                run["hardware"] = dict(gpu_windows[0]["renderer_classification"], actual_context=True,
                                       l3_status="PENDING")
            app.shutdown()
        write_json(output, run)


def run_experiment(args):
    manifest = read_json(args.manifest)
    output = Path(args.output)
    if not args.smoke:
        validate_formal_manifest(manifest)
    else:
        manifest["warmup_frames"], manifest["measured_frames"] = 2, 4
        from gpu_workload import build_snapshots
        for scene in manifest["scenarios"]:
            _, metadata = build_snapshots(scene["id"], count=6)
            scene["snapshot_hash"] = metadata["snapshot_hash"]
    output.mkdir(parents=True, exist_ok=False)
    saved_manifest = output / "manifest.json"
    write_json(saved_manifest, manifest)
    collect_bundle_artifacts(output, manifest)
    runs = []
    for scene in manifest["scenarios"]:
        for block in manifest["blocks"][:1] if args.smoke else manifest["blocks"]:
            for configuration in block["order"]:
                kinds = ["diagnostic"] if args.smoke else (["throughput", "interaction"] if scene["interaction"] else ["throughput"])
                for kind in kinds:
                    path = output / "{}-{}-{}-{}.json".format(scene["id"], block["block_id"], configuration, kind)
                    command = [sys.executable, "-B", str(Path(__file__).resolve()), "worker", "--manifest", str(saved_manifest),
                        "--scenario", scene["id"], "--block", str(block["block_id"]),
                        "--configuration", configuration, "--kind", kind, "--output", str(path)]
                    try:
                        result = subprocess.run(command, capture_output=True, text=True, timeout=manifest["timeout_seconds"])
                        path.with_suffix(".log").write_text(result.stdout + result.stderr)
                        if result.returncode != 0 or not path.exists():
                            record = read_json(path) if path.exists() else dict(run_id=str(uuid.uuid4()),
                                experiment_id=manifest["experiment_id"], scenario_id=scene["id"],
                                block_id=block["block_id"], kind=kind, configuration=configuration)
                            record.update(status="product_failure", reason="worker exited with status {}".format(result.returncode))
                            write_json(path, record)
                    except subprocess.TimeoutExpired as exc:
                        record = read_json(path) if path.exists() else dict(run_id=str(uuid.uuid4()),
                            experiment_id=manifest["experiment_id"], scenario_id=scene["id"],
                            block_id=block["block_id"], kind=kind, configuration=configuration)
                        record.update(status="product_failure", reason="predeclared run timeout")
                        write_json(path, record)
                        def decoded(value):
                            return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")
                        path.with_suffix(".log").write_text(decoded(exc.stdout) + decoded(exc.stderr) + str(exc))
                    if path.exists():
                        runs.append(read_json(path))
    write_json(output / "runs.json", runs)
    from gpu_analysis import analyze_experiment
    write_json(output / "summary.json", analyze_experiment(manifest, runs))
    seal_bundle(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare", help="Create a draft manifest before calibration")
    prepare_parser.add_argument("--output", required=True)
    prepare_parser.add_argument("--blocks", type=int, default=10)
    prepare_parser.add_argument("--warmup", type=int, default=60)
    prepare_parser.add_argument("--frames", type=int, default=300)
    prepare_parser.add_argument("--seed", type=int, default=20261005)
    prepare_parser.add_argument("--timeout", type=float, default=120)
    run_parser = commands.add_parser("run", help="Execute fixed independent-process blocks")
    run_parser.add_argument("--manifest", required=True)
    run_parser.add_argument("--output", required=True)
    run_parser.add_argument("--smoke", action="store_true")
    worker_parser = commands.add_parser("worker", help="One independent capture/replay/interaction run")
    worker_parser.add_argument("--manifest", required=True)
    worker_parser.add_argument("--output", required=True)
    worker_parser.add_argument("--scenario", required=True)
    worker_parser.add_argument("--block", default="0")
    worker_parser.add_argument("--configuration", choices=CONFIGURATIONS, required=True)
    worker_parser.add_argument("--kind", choices=["throughput", "interaction", "capture", "diagnostic", "query"], required=True)
    worker_parser.add_argument("--strict", action="store_true", help="Reject actual software/unknown GL renderer")
    analyze_parser = commands.add_parser("analyze", help="Recompute summary from original manifest/raw runs")
    analyze_parser.add_argument("--manifest", required=True)
    analyze_parser.add_argument("--runs", required=True)
    analyze_parser.add_argument("--output", required=True)
    qualify_parser = commands.add_parser("qualify", help="Record visible reraster proof and CPU work attribution; no share minimum")
    qualify_parser.add_argument("--manifest", required=True)
    qualify_parser.add_argument("--output", required=True)
    calibrate_parser = commands.add_parser("calibrate", help="Freeze regions using positive and five negative fixtures")
    calibrate_parser.add_argument("--reference", required=True)
    calibrate_parser.add_argument("--positive", nargs="+", required=True)
    calibrate_parser.add_argument("--negative", nargs="+", required=True, help="class=fixture.npy pairs")
    calibrate_parser.add_argument("--regions", required=True)
    calibrate_parser.add_argument("--output", required=True)
    compare_parser = commands.add_parser("compare", help="Compare a capture using frozen calibration")
    compare_parser.add_argument("--reference", required=True)
    compare_parser.add_argument("--candidate", required=True)
    compare_parser.add_argument("--calibration", required=True)
    compare_parser.add_argument("--output", required=True)
    freeze_parser = commands.add_parser("freeze", help="Freeze qualified experiment and verified capture pairs")
    freeze_parser.add_argument("--manifest", required=True)
    freeze_parser.add_argument("--capture-index", required=True)
    freeze_parser.add_argument("--output", required=True)
    observe_parser = commands.add_parser("observe-template", help="Export L3 attribution worksheet")
    observe_parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "prepare": prepare(args)
    elif args.command == "run": run_experiment(args)
    elif args.command == "worker": worker(args)
    elif args.command == "qualify": qualify(args)
    elif args.command == "calibrate": calibrate(args)
    elif args.command == "compare": compare(args)
    elif args.command == "freeze": freeze(args)
    elif args.command == "observe-template": observation_template(args)
    else:
        from gpu_analysis import analyze_experiment
        write_json(args.output, analyze_experiment(read_json(args.manifest), read_json(args.runs)))


if __name__ == "__main__":
    main()
