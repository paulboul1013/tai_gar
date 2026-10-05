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
