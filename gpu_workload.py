"""Fixed local browser pages and real DOM/layout/paint snapshot sequences."""
from contextlib import contextmanager
import functools
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace


SCENARIOS = {
    "text_rect": {"role": "primary", "interaction": False, "rows": 24},
    "small": {"role": "regression", "interaction": False, "rows": 2},
    "blend_clip": {"role": "regression", "interaction": False, "rows": 12},
    "scroll_raf": {"role": "regression", "interaction": True, "rows": 48},
}


class NullMeasure:
    def time(self, name): pass
    def stop(self, name): pass
    def instant(self, name, args=None): pass
    def thread_name(self, name=None): pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fixture_resources(scenario_id):
    scene = SCENARIOS[scenario_id]
    effects = "opacity: 0.65; overflow: hidden; height: 240px;" if scenario_id == "blend_clip" else ""
    words = "Tai Gar GPU verification rectangle text ABC 0123456789 "
    rows = "".join('<p style="background-color: #ddeeff; margin: 0px;">' + words * 2 + '</p>'
                   for _ in range(scene["rows"]))
    html = ('<html><head><title>GPU verification ' + scenario_id + '</title></head>'
            '<body><div id="change" style="background-color: #ffcc88;">Frame zero</div>'
            '<div style="' + effects + '">' + rows + '</div><script src="scene.js"></script></body></html>')
    script = '''var target = document.querySelectorAll("#change")[0];
var phase = 0;
function update() {
  phase = 1 - phase;
  target.setAttribute("style", phase ? "background-color: #88ccff;" : "background-color: #ffcc88;");
  requestAnimationFrame(update);
}
requestAnimationFrame(update);
'''
    return {"index.html": html, "scene.js": script}


def font_identity(browser, commands):
    used = set()
    def visit(command):
        font = getattr(command, "font", None)
        if font is not None:
            used.add(font.getTypeface().uniqueID())
        for child in getattr(command, "children", ()):
            visit(child)
    for command in commands:
        visit(command)
    fonts = []
    for key, face in sorted(browser.TYPEFACES.items()):
        if face.uniqueID() not in used:
            continue
        data = bytes(face.serialize(browser.skia.Typeface.kDoIncludeData))
        fonts.append({"requested": list(key), "resolved_family": face.getFamilyName(),
                      "serialized_font_sha256": hashlib.sha256(data).hexdigest()})
    return fonts


def command_identity(browser, command):
    """Hash drawing inputs without mutable DOM pointers or process-local IDs."""
    def value(item):
        if item is None or isinstance(item, (str, bool, int, float)):
            return item
        if isinstance(item, browser.skia.Rect):
            return [item.left(), item.top(), item.right(), item.bottom()]
        if isinstance(item, browser.skia.Font):
            face = item.getTypeface()
            return {"size": item.getSize(), "scale_x": item.getScaleX(), "skew_x": item.getSkewX(),
                    "typeface": hashlib.sha256(bytes(face.serialize(browser.skia.Typeface.kDoIncludeData))).hexdigest()}
        if isinstance(item, (list, tuple)):
            return [command_identity(browser, child) for child in item]
        if isinstance(item, browser.skia.BlendMode):
            return str(item)
        raise TypeError("Unhandled drawing input: " + type(item).__name__)
    return {"command": type(command).__name__, "fields": {key: value(item)
            for key, item in vars(command).items() if key not in ("layout_object", "node")}}


def build_snapshots(scenario_id, width=800, height=600, count=360, chrome_bottom=100, measure=None):
    import browser
    host = SimpleNamespace(measure=measure or NullMeasure(), set_needs_animation_frame=lambda tab: None)
    tab = browser.Tab(host, width, height - chrome_bottom, set(), [])
    resources = fixture_resources(scenario_id)
    tab.nodes = browser.HTMLParser(resources["index.html"]).parse()
    tab.url = browser.URL("http://127.0.0.1/" + scenario_id)
    with open(Path(__file__).with_name("browser.css")) as stream:
        tab.rules = browser.CSSParser(stream.read()).parse()
    change = next(node for node in browser.tree_to_list(tab.nodes, [])
                  if isinstance(node, browser.Element) and node.attributes.get("id") == "change")
    started = time.perf_counter_ns()
    states = []
    for phase in range(2):
        change.attributes["style"] = "background-color: " + ("#ffcc88;" if phase == 0 else "#88ccff;")
        tab.needs_render = True
        tab.render()
        states.append((tuple(tab.display_list), tab.document.height + 2 * browser.VSTEP))
    preparation_ns = time.perf_counter_ns() - started
    snapshots = []
    for index in range(count):
        commands, document_height = states[index % 2]
        scroll = min(100.0 if index % 2 else 0.0, max(0, document_height - tab.tab_height)) if scenario_id == "scroll_raf" else 0
        snapshots.append(browser.CommitData(str(tab.url), scroll, document_height, commands,
                         width=width, tab_height=tab.tab_height, title=scenario_id))
    metadata = dict(workload_hash=digest(resources), snapshot_hash=digest({"workload": resources,
        "size": [width, height], "chrome_bottom": chrome_bottom, "count": count,
        "states": [{"height": document_height, "commands": [command_identity(browser, cmd) for cmd in commands]}
                   for commands, document_height in states],
        "sequence": [{"phase": i % 2, "scroll": s.scroll} for i, s in enumerate(snapshots)]}),
        font=font_identity(browser, [cmd for commands, _ in states for cmd in commands]), preparation_ns=preparation_ns,
        tab_raster_flags=[True] * count, visible_change_requires_capture=True)
    return snapshots, metadata


@contextmanager
def fixture_server(scenario_id):
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, format, *args): pass
    with tempfile.TemporaryDirectory(prefix="tai-gar-fixture-") as directory:
        for name, contents in fixture_resources(scenario_id).items():
            (Path(directory) / name).write_text(contents)
        server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=directory))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield "http://127.0.0.1:{}/index.html".format(server.server_port)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
