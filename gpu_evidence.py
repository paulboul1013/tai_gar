"""Rendering configuration and conservative GPU evidence, without GL imports.

GL execution, hardware execution, and performance are independent claims. The
recorder never promotes a renderer name or a successful swap to full acceptance.
The browser owns live resources; this module stores diagnostic values only.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import threading
import time
import uuid


ENV_FIELDS = {
    "backend": "BROWSER_RENDER_BACKEND",
    "raster_mode": "BROWSER_RASTER_MODE",
    "strict": "BROWSER_GPU_STRICT",
    "evidence_path": "BROWSER_GPU_EVIDENCE",
    "run_id": "BROWSER_GPU_RUN_ID",
}
GATE_NAMES = ("l0", "l2", "correctness", "lifecycle", "l3", "performance")


class ConfigError(ValueError):
    """An explicitly requested rendering configuration is invalid."""

    def __init__(self, message, requested):
        super().__init__(message)
        self.requested = requested


@dataclass(frozen=True)
class RenderConfig:
    backend: str
    raster_mode: str
    strict: bool
    evidence_path: str | None
    run_id: str
    requested: dict

    @classmethod
    def from_env(cls, env=None):
        """Validate settings and save a configuration-error run before raising.

        Only missing backend/raster variables select defaults. An explicitly
        empty setting is an error. Raw requested strings remain in the evidence.
        """
        env = os.environ if env is None else env
        requested = {field: env.get(name) for field, name in ENV_FIELDS.items()}
        evidence_path = requested["evidence_path"] or None
        run_id = requested["run_id"] or str(uuid.uuid4())
        try:
            backend = cls._mode(requested, "backend", "cpu", ("cpu", "gpu"))
            raster_mode = cls._mode(requested, "raster_mode", "threaded", ("sync", "threaded"))
            raw_strict = requested["strict"]
            if raw_strict is None:
                strict = False
            elif raw_strict.strip().lower() in ("1", "true", "yes", "on"):
                strict = True
            elif raw_strict.strip().lower() in ("0", "false", "no", "off"):
                strict = False
            else:
                raise ConfigError("BROWSER_GPU_STRICT must be a boolean (true/false or 1/0)", requested)
            if backend == "gpu" and raster_mode == "threaded":
                raise ConfigError("GPU backend does not support threaded raster mode; request sync", requested)
        except ConfigError as error:
            if evidence_path:
                # Actual backend remains unknown: no context or fallback exists.
                invalid = cls(None, None, False, evidence_path, run_id, requested)
                recorder = EvidenceRecorder(invalid)
                recorder.fail(str(error), kind="configuration")
                try:
                    recorder.write()
                except OSError as write_error:
                    error.add_note(f"Unable to save configuration evidence: {write_error}")
            raise
        return cls(backend, raster_mode, strict, evidence_path, run_id, requested)

    @staticmethod
    def _mode(requested, field, default, allowed):
        raw = requested[field]
        actual = default if raw is None else raw.strip().lower()
        if actual not in allowed:
            raise ConfigError(f"{ENV_FIELDS[field]} must be one of {', '.join(allowed)}; received {raw!r}", requested)
        return actual


def load_config(env=None):
    return RenderConfig.from_env(env)


_SOFTWARE_RENDERERS = (
    "llvmpipe", "softpipe", "software rasterizer", "software renderer",
    "swiftshader", "microsoft basic render driver", "microsoft basic display adapter",
    "swrast", "mesa x11",
)
_HARDWARE_NAME = re.compile(
    r"(?:nvidia\s+(?:geforce|quadro|tesla|rtx|titan|[ahl]\d{2,3}\b))"
    r"|(?:(?:amd|ati)\s+radeon)"
    r"|(?:intel(?:\(r\))?\s+(?:iris|uhd|hd\s+graphics|arc))"
    r"|(?:apple\s+m\d\b)|(?:mali[-\s][gt]\d)|(?:adreno(?:\s*\(tm\))?\s*\d)",
    re.IGNORECASE,
)


def classify_renderer(vendor, renderer, version=""):
    """Recognize known software and device names; candidates still require L3."""
    vendor, renderer, version = str(vendor or ""), str(renderer or ""), str(version or "")
    text = " ".join((vendor, renderer, version)).lower()
    software = next((name for name in _SOFTWARE_RENDERERS if name in text), None)
    if software is None and re.search(r"\bwarp\b", text):
        software = "WARP"
    if software:
        classification, reason = "software", f"Known software implementation: {software}"
    elif _HARDWARE_NAME.search(renderer):
        classification = "hardware_candidate"
        reason = "Recognized device name; actual GL execution and attributable L3 activity still required"
    else:
        classification = "unknown"
        reason = "Renderer has no recognized device identity; vendor or an absent software match is insufficient"
    return {"classification": classification, "reason": reason,
            "vendor": vendor, "renderer": renderer, "version": version}


def _clock_anchor():
    monotonic_ns = time.monotonic_ns()
    perf_counter_ns = time.perf_counter_ns()
    utc_ns = time.time_ns()
    return {"utc": datetime.fromtimestamp(utc_ns / 1e9, timezone.utc).isoformat(),
            "utc_ns": utc_ns, "monotonic_ns": monotonic_ns, "perf_counter_ns": perf_counter_ns,
            "clock": "time.monotonic_ns", "utc_clock": "time.time_ns"}


def _program_identity():
    root = Path(__file__).resolve().parent
    result = {"commit": None, "dirty_diff_sha256": None, "source_sha256": {}, "errors": []}

    def git(*args):
        return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, timeout=5).stdout

    try:
        result["commit"] = git("rev-parse", "HEAD").decode().strip()
        result["dirty_diff_sha256"] = hashlib.sha256(git("diff", "--binary", "HEAD", "--")).hexdigest()
        filenames = git("ls-files", "-z", "--cached", "--others", "--exclude-standard").decode().split("\0")
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        result["errors"].append(f"Git identity unavailable: {type(error).__name__}")
        filenames = ["browser.py", "gpu_evidence.py"]
    for filename in sorted(set(filenames)):
        if not filename:
            continue
        path = root / filename
        if Path(filename).parts[0] in ("tests", "docs"):
            continue
        if path.suffix not in (".py", ".js", ".css", ".html") or not path.is_file():
            continue
        try:
            result["source_sha256"][filename] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            result["errors"].append(f"Source identity unavailable for {filename}: {type(error).__name__}")
    return result


def _dependencies():
    result = {"python": platform.python_version()}
    for package in ("skia-python", "PySDL2", "pysdl2-dll", "PyOpenGL", "dukpy", "Pillow", "requests", "numpy"):
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = None
    return result


def program_identity():
    """Return commit, uncommitted diff hash, and current application source hashes."""
    return _program_identity()


def dependency_versions():
    """Return installed rendering dependencies without importing native libraries."""
    return _dependencies()


class EvidenceRecorder:
    """Accumulate cheap records; serialize only on an explicit write.

    Gate values are PASS, FAIL, or PENDING. Callers must only set PASS after
    inspecting the required raw evidence. Full correctness and lifecycle gates
    are deliberately separate from a single successfully submitted frame.
    """

    def __init__(self, config):
        self.config = config
        self.enabled = bool(config.evidence_path)
        self._lock = threading.RLock()
        self._frame_sequence = 0
        self.data = {
            "schema_version": 1, "run_id": config.run_id,
            "pid": os.getpid(), "native_tid": threading.get_native_id(),
            "thread_name": threading.current_thread().name,
            "clock_anchor": _clock_anchor(),
            "platform": {"system": platform.system(), "release": platform.release(),
                         "machine": platform.machine()},
            "config": {"requested": dict(config.requested),
                       "actual": {"backend": config.backend, "raster_mode": config.raster_mode},
                       "strict": config.strict},
            "windows": {}, "frames": [], "events": [], "failures": [],
            "gates": {gate: "PENDING" for gate in GATE_NAMES},
            "gl_path_status": "PENDING", "hardware_status": "PENDING", "performance_status": "PENDING",
        }
        if self.enabled:
            self.data["program"] = _program_identity()
            self.data["dependencies"] = _dependencies()

    @staticmethod
    def _record(fields):
        return {**fields, "pid": os.getpid(), "native_tid": threading.get_native_id(),
                "monotonic_ns": time.monotonic_ns()}

    def window(self, window_id, **fields):
        with self._lock:
            window = self.data["windows"].setdefault(str(window_id), self._record({"window_id": window_id}))
            window.update(fields)
            renderer_fields = {"gl_vendor", "gl_renderer", "gl_version", "vendor", "renderer", "version"}
            if renderer_fields.intersection(fields) and "renderer_classification" not in fields:
                window["renderer_classification"] = classify_renderer(
                    window.get("gl_vendor", window.get("vendor", "")),
                    window.get("gl_renderer", window.get("renderer", "")),
                    window.get("gl_version", window.get("version", "")))
            return window

    def frame(self, **fields):
        with self._lock:
            self._frame_sequence += 1
            record = self._record({"frame_id": f"{self.config.run_id}:{self._frame_sequence}",
                                   "sequence": self._frame_sequence, **fields})
            self.data["frames"].append(record)
            self.data["frame_count"] = self._frame_sequence
            # Ordinary runs without an export path retain bounded diagnostics.
            if not self.enabled and len(self.data["frames"]) > 256:
                del self.data["frames"][0]
            return record

    def event(self, name, **fields):
        with self._lock:
            record = self._record({"name": name, **fields})
            self.data["events"].append(record)
            if not self.enabled and len(self.data["events"]) > 256:
                del self.data["events"][0]
            return record

    def fail(self, reason, kind="product", **fields):
        with self._lock:
            failure = self._record({"reason": str(reason), "kind": kind, **fields})
            self.data["failures"].append(failure)
            return failure

    def finalize(self):
        with self._lock:
            gates = self.data["gates"]
            gl_gates = [gates.get(name, "PENDING") for name in ("l0", "l2", "correctness", "lifecycle")]
            failures = self.data["failures"]
            product_failure = any(failure["kind"] == "product" for failure in failures)
            config_failure = any(failure["kind"] == "configuration" for failure in failures)
            measurement_failure = any(failure["kind"] == "measurement" for failure in failures)
            if product_failure or config_failure or "FAIL" in gl_gates:
                gl_status = "FAIL"
            elif all(status == "PASS" for status in gl_gates):
                gl_status = "PASS"
            else:
                gl_status = "PENDING"

            classifications = []
            for window in self.data["windows"].values():
                classified = window.get("renderer_classification")
                if not isinstance(classified, dict):
                    classified = classify_renderer(
                        window.get("gl_vendor", window.get("vendor", "")),
                        window.get("gl_renderer", window.get("renderer", "")),
                        window.get("gl_version", window.get("version", "")))
                    window["renderer_classification"] = classified
                classifications.append(classified.get("classification", "unknown"))
            if "software" in classifications or gates.get("l3") == "FAIL":
                hardware_status = "FAIL"
            elif (classifications and all(value == "hardware_candidate" for value in classifications)
                  and gates.get("l2") == "PASS" and gates.get("l3") == "PASS"):
                hardware_status = "PASS"
            else:
                hardware_status = "PENDING"

            if product_failure or "FAIL" in gl_gates or gates.get("performance") == "FAIL":
                performance_status = "FAIL"
            elif measurement_failure or config_failure:
                performance_status = "PENDING"
            else:
                performance_status = gates.get("performance", "PENDING")
                if performance_status not in ("PASS", "FAIL", "PENDING"):
                    performance_status = "PENDING"
                if performance_status == "PASS" and gates.get("correctness") != "PASS":
                    performance_status = "PENDING"
            self.data.update(gl_path_status=gl_status, hardware_status=hardware_status,
                             performance_status=performance_status, final_clock_anchor=_clock_anchor())
            return self.data

    def write(self):
        """Replace JSON atomically; a failed write preserves the previous file."""
        if not self.enabled:
            return
        with self._lock:
            self.finalize()
            path = Path(self.config.evidence_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                                 prefix=f".{path.name}.", delete=False) as stream:
                    temporary = Path(stream.name)
                    json.dump(self.data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()
