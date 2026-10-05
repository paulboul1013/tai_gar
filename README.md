# tai_gar

![alt text](tai_gar_icon.png)

## introdeuce

simple browser by python 

for the learning how to make a web browser

## usage
### basic usage
```bash
python3 browser.py <url>
```

#### using example 
```bash
python3 browser.py https://browser.engineering/
```

---

### multi-windows

`python3 browser.py` and then press `Ctrl+N` to open a new window

---

### bookmarks

open webpage and click the star icon to add bookmark，and then input `about:bookmarks` in the address bar to open the boomarks page

### Chapter 12 JavaScript API tests

Run from the repository root with the browser dependencies (`dukpy`, `PySDL2`,
and `skia-python`) installed:

```bash
BROWSER_RENDER_BACKEND=cpu python3 -B -m unittest discover -s tests -p 'test_ch12_js_api.py' -v
```

The tests load the real JavaScript runtime and Python handlers without opening
an SDL window or accessing the internet. They cover timeout/interval callbacks,
interval cancellation, synchronous/asynchronous XHR, CSP/CORS, navigation and
window cleanup, and RAF/render ordering. Timer expiration and network I/O are
controlled; integration cases also use the real Tab and networking threads.

This preserves the teaching API's synchronous default when XHR `open` omits
its third argument; pass `true` to enable asynchronous requests. `clearTimeout`,
`cancelAnimationFrame`, `fetch`, and additional XHR state/error APIs are outside
this restoration.

### Chapter 13 GPU verification

```bash
python3 -B -m unittest discover -s tests -v
python3 -B gpu_verification.py prepare --output /tmp/gpu-manifest.json
python3 -B gpu_verification.py qualify --manifest /tmp/gpu-manifest.json --output /tmp/qualification.json
python3 -B gpu_verification.py run --manifest /tmp/gpu-manifest.json --output /tmp/gpu-smoke --smoke
```

The smoke command opens SDL windows. `BROWSER_RENDER_BACKEND=gpu` requires
`BROWSER_RASTER_MODE=sync`; CPU remains the default. Export actual-context data
with `BROWSER_GPU_EVIDENCE=/tmp/gpu-evidence.json`. Set `BROWSER_GPU_STRICT=1`
to reject software or unknown GL renderers. Invalid configurations raise an error.

Formal runs require proof of visible reraster work and frozen regional image calibration;
smoke, capture, query and expected-negative runs do not satisfy performance gates.
CPU raster/draw share is diagnostic data and has no minimum for measurement eligibility.
See [CLI and calibration workflow](docs/chapter-13-gpu-verification-usage.md)
and [implementation results](docs/chapter-13-gpu-verification-results.md).
