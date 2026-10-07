"""Choose the render backend when none was requested: GPU if it can draw, else CPU.

Some GL stacks (WSL D3D12 with old AMD drivers) create a context and then crash
in native code on the first draw, so the probe draws in a child process and
the browser only trusts a clean exit.
"""

from dataclasses import replace
import json
import os
import subprocess
import sys
import time

from gpu_evidence import classify_renderer


PROBE_TIMEOUT_SEC = 15
PROBE_SIZE = 32
GL_ATTRIBUTES = {
    "CONTEXT_MAJOR_VERSION": 3, "CONTEXT_MINOR_VERSION": 3,
    "CONTEXT_PROFILE_MASK": "SDL_GL_CONTEXT_PROFILE_CORE",
    "DOUBLEBUFFER": 1, "RED_SIZE": 8, "GREEN_SIZE": 8,
    "BLUE_SIZE": 8, "ALPHA_SIZE": 8, "STENCIL_SIZE": 8,
    "MULTISAMPLEBUFFERS": 0, "MULTISAMPLESAMPLES": 0,
}


def set_gl_attributes(sdl2):
    for name, value in GL_ATTRIBUTES.items():
        if isinstance(value, str):
            value = getattr(sdl2, value)
        if sdl2.SDL_GL_SetAttribute(getattr(sdl2, "SDL_GL_" + name), value) != 0:
            raise RuntimeError("SDL_GL_SetAttribute failed: " + name)


def gl_driver_attempts(env):
    """Mesa on WSL often defaults to llvmpipe even when the D3D12 driver works."""
    attempts = [{}]
    if "GALLIUM_DRIVER" not in env and os.path.exists("/dev/dxg"):
        attempts.append({"GALLIUM_DRIVER": "d3d12"})
    return attempts


class PendingProbes:
    """Probe children started early so their SDL/GL startup overlaps the browser's imports."""

    def __init__(self, env):
        self.children = [
            (overrides, subprocess.Popen(
                [sys.executable, os.path.abspath(__file__)], env={**env, **overrides},
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True))
            for overrides in gl_driver_attempts(env)]
        self.deadline = time.monotonic() + PROBE_TIMEOUT_SEC

    def results(self):
        """Yield each attempt's result in preference order; stop early to discard the rest."""
        try:
            for overrides, child in self.children:
                yield {**self._collect(child), "env": overrides}
        finally:
            for _, child in self.children:
                if child.poll() is None:
                    child.kill()
                    try:
                        child.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        pass
                child.stdout.close()

    def _collect(self, child):
        # A child that already exited is read in full even after the shared deadline.
        timeout = None if child.poll() is not None else max(0.0, self.deadline - time.monotonic())
        try:
            stdout, _ = child.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "reason": "GPU probe timed out"}
        lines = stdout.strip().splitlines()
        if child.returncode != 0 or not lines:
            return {"ok": False, "reason": "GPU probe exited with code {}".format(child.returncode)}
        try:
            report = json.loads(lines[-1])
            return {"ok": report["ok"] is True, "reason": str(report["reason"]),
                    "renderer": report.get("renderer"),
                    "classification": report.get("classification")}
        except (ValueError, KeyError, TypeError):
            return {"ok": False, "reason": "GPU probe printed an unreadable report"}


def resolve_backend(config, results, env):
    """Turn an automatic config into a concrete one and apply the winning driver env."""
    attempts = []
    for result in results:
        if config.strict and result["ok"] and result["classification"] != "hardware_candidate":
            result = {**result, "ok": False,
                      "reason": "strict mode rejected {} renderer".format(result["classification"])}
        attempts.append(result)
        if result["ok"]:
            env.update(result["env"])
            return replace(config, backend="gpu", raster_mode="sync", auto=False,
                           selection={"backend": "gpu", "attempts": attempts})
    return replace(config, auto=False, selection={"backend": "cpu", "attempts": attempts})


def describe_selection(selection):
    attempt = selection["attempts"][-1]
    if selection["backend"] == "gpu":
        return "Render backend: gpu ({})".format(attempt["renderer"])
    reasons = "; ".join(a["reason"] for a in selection["attempts"])
    return "Render backend: cpu (GPU unavailable: {})".format(reasons)


def probe_in_this_process():
    import sdl2
    import skia
    import OpenGL.GL as gl

    if sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO) != 0:
        return {"ok": False, "reason": "SDL_Init failed: {}".format(sdl2.SDL_GetError())}
    set_gl_attributes(sdl2)
    window = sdl2.SDL_CreateWindow(
        b"Tai Gar GPU probe", 0, 0, PROBE_SIZE, PROBE_SIZE,
        sdl2.SDL_WINDOW_HIDDEN | sdl2.SDL_WINDOW_OPENGL)
    if not window:
        return {"ok": False, "reason": "SDL_CreateWindow failed: {}".format(sdl2.SDL_GetError())}
    context = sdl2.SDL_GL_CreateContext(window)
    if not context:
        return {"ok": False, "reason": "no OpenGL 3.3 context: {}".format(sdl2.SDL_GetError())}
    vendor, renderer, version = [
        (gl.glGetString(enum) or b"").decode("utf8", errors="replace")
        for enum in (gl.GL_VENDOR, gl.GL_RENDERER, gl.GL_VERSION)]
    classification = classify_renderer(vendor, renderer, version)
    result = {"renderer": renderer, "classification": classification["classification"]}
    if classification["classification"] == "software":
        return {**result, "ok": False, "reason": "software renderer " + renderer}

    skia_context = skia.GrDirectContext.MakeGL()
    if skia_context is None:
        return {**result, "ok": False, "reason": "Skia could not create a GL context"}
    surface = skia.Surface.MakeRenderTarget(
        skia_context, skia.Budgeted.kNo,
        skia.ImageInfo.MakeN32Premul(PROBE_SIZE, PROBE_SIZE))
    if surface is None:
        return {**result, "ok": False, "reason": "Skia could not create a GPU render target"}
    canvas = surface.getCanvas()
    canvas.clear(skia.ColorWHITE)
    canvas.saveLayerAlpha(None, 128)
    canvas.drawRect(skia.Rect.MakeWH(PROBE_SIZE, PROBE_SIZE), skia.Paint(Color=skia.ColorRED))
    canvas.restore()
    canvas.drawString("Ag", 2, PROBE_SIZE - 4, skia.Font(None, 14), skia.Paint(Color=skia.ColorBLACK))
    skia_context.flushAndSubmit(skia.GrSyncCpu.kYes)
    pixel = bytearray(4)
    info = skia.ImageInfo.Make(1, 1, skia.kRGBA_8888_ColorType, skia.kPremul_AlphaType)
    if not surface.readPixels(info, pixel, 4, PROBE_SIZE - 1, 0):
        return {**result, "ok": False, "reason": "GPU readback failed"}
    red, green = pixel[0], pixel[1]
    if not (red > 240 and 100 < green < 160):
        return {**result, "ok": False, "reason": "GPU drew wrong pixels ({}, {})".format(red, green)}
    return {**result, "ok": True, "reason": "drew and read back a test frame"}


if __name__ == "__main__":
    try:
        outcome = probe_in_this_process()
    except Exception as error:
        outcome = {"ok": False, "reason": "{}: {}".format(type(error).__name__, error)}
    print(json.dumps(outcome))
    sys.stdout.flush()
    os._exit(0)
