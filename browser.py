import socket
import ssl
import sys
import time 
import gzip
from urllib.parse import unquote, quote_plus, quote
from html import unescape,escape
import webbrowser
import os
import math
import dukpy
from datetime import datetime, timezone
from email.utils import format_datetime,parsedate_to_datetime
import sdl2
import skia
import ctypes
import threading
import json

# emolji cache
# key: character (e.g. "😀")
# value: SkiaImageAsset object
emoji_cache={}

# socket cache
#key:(scheme,host,port)
#value:socket object
socket_cache={}

# http cache
# key:url string
# value:(response_headers,body_bytes,expires_at_timestamp)
http_cache={}

# cookie jar
# key: host
# value: (cookie,params)
COOKIE_JAR={}

# cookie string example
# token=abc123; SameSite=Lax; HttpOnly
"""
cookie = "token=abc123"

params = {
    "samesite": "lax",
    "httponly": "true"
}
"""
def parse_cookie_string(cookie_string):
    cookie_string = str(cookie_string).strip()
    params={}

    if ";" in cookie_string:
        cookie,rest=cookie_string.split(";",1)

        for param in rest.split(";"):
            param=param.strip()

            if not param:
                continue

            if "=" in param:
                name, value=param.split("=",1)

                name = name.strip().casefold()
                value= value.strip()

                # SameSite use lower case compare
                if name=="samesite":
                    value = value.casefold()

            else:
                # HttpOnly,etc
                name = param.strip().casefold()
                value = "true"

            params[name]=value

    else:
        cookie = cookie_string
        
    return cookie.strip(),params

def cookie_expiration(params):
    expires = params.get("expires")
    
    if not expires:
        return None

    try:
        expiration = parsedate_to_datetime(expires)

    except (TypeError,ValueError,OverflowError):
        return None

    # some date format no timezone，default become UTC
    if expiration.tzinfo is None:
        expiration = expiration.replace(
            tzinfo = timezone.utc
        )

    return expiration.astimezone(timezone.utc)


def cookie_is_expired(params,now=None):
    expiration = cookie_expiration(params)

    # no expires,represent current is session cookie
    if expiration is None:
        return False

    if now is None:
        now = datetime.now(timezone.utc)

    return expiration <= now

def get_valid_cookie(host):
    entry = COOKIE_JAR.get(host)

    if entry is None:
        return None

    cookie,params=entry

    if cookie_is_expired(params):
        del COOKIE_JAR[host]
        return None

    return cookie,params

# parse server Referrer-Policy
def normalize_referrer_policy(response_headers):
    policy = response_headers.get("referrer-policy")

    if policy is None:
        return None

        
    policy = policy.strip().casefold()

    if policy in [
        "no-referrer",
        "same-origin"
    ]:
        return policy

    return None

# decide whether to send referrer
def should_send_referrer(referrer,target_url,referrer_policy):
    # first page no source
    if referrer is None:
        return False

    # only HTTP/HTTPS can request with Referer
    if referrer.scheme not in ["http","https"]:
        return False

    if target_url.scheme not in ["http","https"]:
        return False

    if referrer_policy == "no-referrer":
        return False

    if referrer_policy=="same-origin":
        return (referrer.origin()==target_url.origin())

    return True

def referer_value(referrer):
    # url fragment not put into referrer header
    return str(referrer).split("#",1)[0]

# cookie serialize example
# token=abc123; SameSite=Lax; HttpOnly
"""
serialize_cookie(
    "theme=dark",
    {
        "samesite": "lax"
    }
)
"""
def serialize_cookie(cookie,params):
    parts = [cookie]

    for name,value in params.items():
        if value=="true":
            parts.append(name)
        else:
            parts.append(
                "{}={}".format(name,value)
            )

    return "; ".join(parts)

# global Typeface cache
TYPEFACES={}


WIDTH,HEIGHT=800,600
HSTEP, VSTEP = 13, 18
SCROLL_STEP = 100

# Interest region: keep only a bounded raster cache around the viewport.
INTEREST_REGION_MULTIPLIER = 4

# Touch input uses a small contact area instead of an exact mouse point.
# Shift + left click uses the same path on desktop machines without a touch screen.
TOUCH_RADIUS_PX = 20
TOUCH_MOVE_TOLERANCE_PX = 12

# Chapter 12 scheduling: target ~30 frames per second.
REFRESH_RATE_SEC = .033

# Small JavaScript bridge snippets executed from main-thread tasks.
SETTIMEOUT_JS = "runSetTimeout(dukpy.handle)"
XHR_ONLOAD_JS = "runXHROnload(dukpy.out, dukpy.handle)"
RAF_JS = "runRAFHandlers()"


class MeasureTime:
    """Write thread-aware Chrome/Perfetto JSON trace events."""
    def __init__(self, filename="browser.trace"):
        self.file = open(filename, "w", encoding="utf-8")
        self.finished = False
        self.lock = threading.Lock()

        # Remember thread names for the entire trace lifetime. Tab main threads
        # can terminate before finish(), so relying only on threading.enumerate()
        # at shutdown would lose their human-readable names.
        self.thread_names = {}
        self.thread_names_emitted = set()

        # Performance intervals need one process-wide monotonic clock. Wall-clock
        # time can jump because of NTP, suspend/resume, VM synchronization, etc.
        self.clock = time.perf_counter_ns

        self.file.write('{"traceEvents":[')
        self.write_event({
            "name": "process_name",
            "ph": "M",
            "ts": self.timestamp_us(),
            "pid": 1,
            "cat": "__metadata",
            "args": {"name": "Browser"},
        }, first=True)

    def timestamp_us(self):
        """Return a monotonic process-wide trace timestamp in microseconds."""
        return self.clock() // 1000

    def write_event(self, event, first=False):
        """Atomically append one trace event from any browser thread."""
        with self.lock:
            if self.finished:
                return False

            if not first:
                self.file.write(",")

            self.file.write(json.dumps(event, separators=(",", ":")))
            self.file.flush()
            return True

    def thread_name(self, name=None):
        """Register and emit metadata for the calling thread exactly once."""
        tid = threading.get_ident()
        if name is None:
            name = threading.current_thread().name
        name = str(name)

        with self.lock:
            if self.finished:
                return False

            self.thread_names[tid] = name
            if tid in self.thread_names_emitted:
                return True

            event = {
                "name": "thread_name",
                "ph": "M",
                "ts": self.timestamp_us(),
                "pid": 1,
                "tid": tid,
                "cat": "__metadata",
                "args": {"name": name},
            }
            self.file.write(",")
            self.file.write(json.dumps(event, separators=(",", ":")))
            self.file.flush()
            self.thread_names_emitted.add(tid)
            return True

    def ensure_thread_name(self):
        """Automatically name a profiled thread even if its caller forgot."""
        tid = threading.get_ident()

        with self.lock:
            if self.finished:
                return False
            if tid in self.thread_names_emitted:
                return True

        return self.thread_name(threading.current_thread().name)

    def time(self, name):
        """Begin a duration event on the calling thread."""
        self.ensure_thread_name()
        self.write_event({
            "ph": "B",
            "cat": "_",
            "name": str(name),
            "ts": self.timestamp_us(),
            "pid": 1,
            "tid": threading.get_ident(),
        })

    def stop(self, name):
        """End a duration event on the calling thread."""
        self.ensure_thread_name()
        self.write_event({
            "ph": "E",
            "cat": "_",
            "name": str(name),
            "ts": self.timestamp_us(),
            "pid": 1,
            "tid": threading.get_ident(),
        })

    def finish(self):
        """Finish a valid trace while preserving names for all known threads."""
        with self.lock:
            if self.finished:
                return

            for thread in threading.enumerate():
                if thread.ident is None:
                    continue
                self.thread_names.setdefault(
                    int(thread.ident),
                    str(thread.name),
                )

            for tid, name in self.thread_names.items():
                if tid in self.thread_names_emitted:
                    continue

                event = {
                    "name": "thread_name",
                    "ph": "M",
                    "ts": self.timestamp_us(),
                    "pid": 1,
                    "tid": tid,
                    "cat": "__metadata",
                    "args": {"name": name},
                }
                self.file.write(",")
                self.file.write(json.dumps(event, separators=(",", ":")))
                self.thread_names_emitted.add(tid)

            self.file.write("]}")
            self.file.flush()
            self.file.close()
            self.finished = True


class Task:
    """A deferred function call plus its arguments."""
    def __init__(self, task_code, *args):
        self.task_code = task_code
        self.args = args

    def run(self):
        task_code = self.task_code
        args = self.args
        try:
            if task_code is not None:
                return task_code(*args)
        finally:
            # Release references after completion so completed tasks do not keep
            # pages, callbacks, or large response objects alive unnecessarily.
            self.task_code = None
            self.args = None


class TaskRunner:
    """One Tab's task queue plus the Tab's dedicated main thread."""
    def __init__(self, tab):
        self.tab = tab
        self.tasks = []
        self.condition = threading.Condition()
        self.needs_quit = False
        self.started = False
        self.main_thread = threading.Thread(
            target=self.run,
            name="Main thread",
            daemon=True,
        )

    def start_thread(self, name=None):
        with self.condition:
            if self.started:
                return
            if name:
                self.main_thread.name = str(name)
            self.started = True
        self.main_thread.start()

    def schedule_task(self, task):
        with self.condition:
            if self.needs_quit:
                return False
            self.tasks.append(task)
            self.condition.notify()
        return True

    def run(self):
        # Each Tab owns exactly one serialized DOM/JavaScript execution thread.
        # Tasks still obey run-to-completion; only raster/draw now runs in parallel.
        if hasattr(self.tab.browser.measure, "thread_name"):
            self.tab.browser.measure.thread_name(threading.current_thread().name)

        while True:
            with self.condition:
                while not self.tasks and not self.needs_quit:
                    self.condition.wait()

                if self.needs_quit:
                    return

                task = self.tasks.pop(0)

            # Never hold the queue lock while arbitrary page/JavaScript work runs.
            task.run()

    def clear_pending_tasks(self):
        with self.condition:
            self.tasks.clear()

    def clear_tasks(self):
        # Backward-compatible alias used by existing navigation/discard code.
        self.clear_pending_tasks()

    def set_needs_quit(self):
        with self.condition:
            self.needs_quit = True
            self.tasks.clear()
            self.condition.notify_all()

    def join_thread(self, timeout=1.0):
        if (
            self.started
            and self.main_thread.is_alive()
            and threading.current_thread() is not self.main_thread
        ):
            self.main_thread.join(timeout=timeout)


# CSS font-size -> Skia font-size conversion.
#
# The old Tkinter-era code multiplied CSS px by 0.75 (16px -> 12pt).
# Skia font sizes can use the CSS pixel value directly for this browser,
# so 1.0 keeps 16px as Skia size 16.
FONT_SCALE = 1.0
DEFAULT_FONT_SIZE_PX = 16.0

SCROLLBAR_WIDTH=12

INPUT_WIDTH_PX = 200
CHECKBOX_SIZE = 13
BUTTON_PADDING = 4

SECURITY_ICON_SLOT=30

# Chrome vector-icon system. Chrome controls keep their HTML/layout boxes for
# hit testing, while their visible symbols are rasterized independently by Skia.
ICON_DEFAULT_SIZE = 14
ICON_STROKE_WIDTH = 2
ICON_COLOR_ENABLED = "black"
ICON_COLOR_DISABLED = "gray"
ICON_COLOR_ACTIVE = "#d49b00"

USE_RTL=False

VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed",
    "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
}

def normalize_font_weight(weight):
    if weight in ["normal", "bold"]:
        return weight

    try:
        weight_num = int(weight)
    except (TypeError, ValueError):
        return "normal"

    return "bold" if weight_num >= 600 else "normal"


def normalize_font_slant(style):
    if style in ["italic", "oblique"]:
        return "italic"
    return "normal"


def linespace(font):
    metrics = font.getMetrics()
    return metrics.fDescent - metrics.fAscent


def css_font_size_to_skia(css_value):
    """Convert a CSS font-size value such as '16px' to a Skia font size.

    FONT_SCALE is the single global control for page font scaling.
    This toy browser currently supports pixel font sizes here.
    """
    if isinstance(css_value, (int, float)):
        css_px = float(css_value)
    else:
        value = str(css_value or "").strip().casefold()

        if value.endswith("px"):
            value = value[:-2].strip()

        try:
            css_px = float(value)
        except ValueError:
            css_px = DEFAULT_FONT_SIZE_PX

    return max(1, int(round(css_px * FONT_SCALE)))


def get_font(size, weight, style, family=None):
    family = family or "Times New Roman"
    family = str(family).split(",", 1)[0].strip().strip("'\"")

    family_aliases = {
        "serif": "Times New Roman",
        "sans-serif": "Arial",
        "monospace": "Courier New",
    }
    family = family_aliases.get(family.casefold(), family)

    weight = normalize_font_weight(weight)
    style = normalize_font_slant(style)
    size = max(1, int(size))

    # Typeface owns the reusable font-family/style data. Size belongs to Font,
    # so the cache intentionally does not include size.
    key = (family, weight, style)
    if key not in TYPEFACES:
        skia_weight = (
            skia.FontStyle.kBold_Weight
            if weight == "bold"
            else skia.FontStyle.kNormal_Weight
        )
        skia_slant = (
            skia.FontStyle.kItalic_Slant
            if style == "italic"
            else skia.FontStyle.kUpright_Slant
        )
        style_info = skia.FontStyle(
            skia_weight,
            skia.FontStyle.kNormal_Width,
            skia_slant,
        )

        try:
            typeface = skia.Typeface(family, style_info)
        except Exception:
            typeface = skia.Typeface("Arial", style_info)

        TYPEFACES[key] = typeface

    return skia.Font(TYPEFACES[key], size)


class SkiaImageAsset:
    def __init__(self, image, width, height):
        self.image = image
        self._width = int(width)
        self._height = int(height)

    def width(self):
        return self._width

    def height(self):
        return self._height


def get_emoji(char):
    if char in emoji_cache:
        return emoji_cache[char]

    code_point = "{:X}".format(ord(char))
    possible_filenames = [
        f"openmoji/{code_point}_color.png",
        f"openmoji/{code_point}.png",
    ]

    for file_path in possible_filenames:
        if not os.path.exists(file_path):
            continue

        try:
            image = skia.Image.open(file_path)
            if image is None:
                continue

            target_width = 22
            source_width = max(1, image.width())
            source_height = max(1, image.height())
            target_height = max(
                1,
                round(source_height * target_width / source_width),
            )

            asset = SkiaImageAsset(
                image,
                target_width,
                target_height,
            )
            emoji_cache[char] = asset
            return asset
        except Exception as e:
            print(f"Error loading emoji {char}: {e}")
            return None

    return None


NAMED_COLORS = {
    "black": (0, 0, 0, 255),
    "white": (255, 255, 255, 255),
    "red": (255, 0, 0, 255),
    "green": (0, 128, 0, 255),
    "blue": (0, 0, 255, 255),
    "yellow": (255, 255, 0, 255),
    "gray": (128, 128, 128, 255),
    "grey": (128, 128, 128, 255),
    "lightgray": (211, 211, 211, 255),
    "lightgrey": (211, 211, 211, 255),
    "lightblue": (173, 216, 230, 255),
    "lightgreen": (144, 238, 144, 255),
    "orange": (255, 165, 0, 255),
    "orangered": (255, 69, 0, 255),
    "transparent": (0, 0, 0, 0),
}


def parse_color(color):
    value = str(color or "black").strip().casefold()

    if value in NAMED_COLORS:
        r, g, b, a = NAMED_COLORS[value]
        return skia.ColorSetARGB(a, r, g, b)

    if value.startswith("#"):
        raw = value[1:]
        try:
            if len(raw) == 3:
                r = int(raw[0] * 2, 16)
                g = int(raw[1] * 2, 16)
                b = int(raw[2] * 2, 16)
                return skia.ColorSetARGB(255, r, g, b)
            elif len(raw) == 6:
                r = int(raw[0:2], 16)
                g = int(raw[2:4], 16)
                b = int(raw[4:6], 16)
                return skia.ColorSetARGB(255, r, g, b)

            elif len(raw) == 8:
                # CSS #RRGGBBAA: alpha is the final byte.
                r = int(raw[0:2], 16)
                g = int(raw[2:4], 16)
                b = int(raw[4:6], 16)
                a = int(raw[6:8], 16)
                return skia.ColorSetARGB(a, r, g, b)

        except ValueError:
            pass

    # Keep unsupported CSS colors visible instead of crashing the renderer.
    return skia.ColorBLACK


def parse_blend_mode(blend_mode_str):
    """Map CSS/internal compositing names to the Skia mode used by saveLayer."""
    blend_mode = str(blend_mode_str or "normal").strip().casefold()

    if blend_mode == "multiply":
        return skia.BlendMode.kMultiply
    elif blend_mode == "difference":
        return skia.BlendMode.kDifference
    elif blend_mode == "destination-in":
        # Internal mask operation used by overflow: clip.
        # This is not a CSS mix-blend-mode value.
        return skia.BlendMode.kDstIn
    elif blend_mode in ["source-over", "src-over"]:
        # Explicit source-over is used internally when clipping needs
        # isolation even though the visual blend operation is the default.
        return skia.BlendMode.kSrcOver
    else:
        # CSS normal mixing uses ordinary source-over compositing.
        return skia.BlendMode.kSrcOver


def parse_opacity(value):
    """Parse CSS opacity and clamp it to the legal [0, 1] range."""
    raw = str(value if value is not None else "1.0").strip()
    try:
        if raw.endswith("%"):
            opacity = float(raw[:-1]) / 100.0
        else:
            opacity = float(raw)
    except (TypeError, ValueError):
        opacity = 1.0

    return max(0.0, min(1.0, opacity))


def parse_css_px(value, default=0.0):
    """Parse a simple CSS pixel length used by visual effects."""
    raw = str(value if value is not None else "").strip().casefold()

    if raw.endswith("px"):
        raw = raw[:-2].strip()

    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return float(default)


def point_in_rounded_rect(x, y, rect, radius_x, radius_y=None):
    """Return True when a point lies inside a rounded rectangle.

    The rectangular bounds are the fast path. Only points that land inside one
    of the four corner boxes need the more expensive quarter-ellipse test.

    This browser currently parses the one-value border-radius form, so callers
    normally pass the same radius on both axes. Keeping radius_x/radius_y
    separate makes the geometry helper ready for elliptical radii later.
    """
    x = float(x)
    y = float(y)

    if not rect.contains(x, y):
        return False

    width = max(0.0, float(rect.width()))
    height = max(0.0, float(rect.height()))

    if width == 0.0 or height == 0.0:
        return False

    rx = max(0.0, float(radius_x))
    ry = rx if radius_y is None else max(0.0, float(radius_y))

    # CSS rounded corners cannot consume more than half of either box axis.
    rx = min(rx, width / 2.0)
    ry = min(ry, height / 2.0)

    if rx == 0.0 or ry == 0.0:
        return True

    left = float(rect.left())
    right = float(rect.right())
    top = float(rect.top())
    bottom = float(rect.bottom())

    inner_left = left + rx
    inner_right = right - rx
    inner_top = top + ry
    inner_bottom = bottom - ry

    # Most points are in the horizontal or vertical center bands and therefore
    # cannot be in a rounded-off corner. This is the fast path used before any
    # ellipse arithmetic.
    if inner_left <= x <= inner_right or inner_top <= y <= inner_bottom:
        return True

    # We are in one of the four corner boxes. Pick that quarter ellipse's
    # center and test the normalized ellipse equation.
    center_x = inner_left if x < inner_left else inner_right
    center_y = inner_top if y < inner_top else inner_bottom

    dx = (x - center_x) / rx
    dy = (y - center_y) / ry

    return dx * dx + dy * dy <= 1.0


def layout_object_rect(layout_object):
    """Return the layout object's own border box when one is available."""
    self_rect = getattr(layout_object, "self_rect", None)
    if callable(self_rect):
        try:
            return self_rect()
        except (TypeError, ValueError):
            return None

    x = getattr(layout_object, "x", None)
    y = getattr(layout_object, "y", None)
    width = getattr(layout_object, "width", None)
    height = getattr(layout_object, "height", None)

    if None in [x, y, width, height]:
        return None

    return skia.Rect.MakeLTRB(
        float(x),
        float(y),
        float(x + width),
        float(y + height),
    )


def hit_test_layout_object(layout_object, x, y):
    """Apply CSS shape-aware hit testing to one layout object.

    Ordinary rectangular elements take the cheap path. Elements with a
    border-radius are tested against their real rounded border box so clicks
    in the visually removed corner do not target the element.
    """
    node = getattr(layout_object, "node", None)

    if not isinstance(node, Element):
        return True

    radius = parse_css_px(
        node.style.get("border-radius", "0px"),
        default=0.0,
    )

    if radius <= 0.0:
        return True

    rect = layout_object_rect(layout_object)
    if rect is None:
        # If a synthetic/partial layout object has no own border box, retain the
        # old hit-testing behavior instead of accidentally making it unclickable.
        return True

    return point_in_rounded_rect(x, y, rect, radius, radius)


def parse_blur_filter(value):
    """Parse the subset of CSS filter supported by this browser: blur(<length>).

    CSS blur() uses its length as the Gaussian standard deviation. This toy
    browser accepts pixel lengths (for example blur(6px)) and unitless zero.
    Unsupported filter functions safely behave like filter: none.
    """
    raw = str(value if value is not None else "none").strip().casefold()

    if raw in ["", "none"]:
        return 0.0

    if not raw.startswith("blur(") or not raw.endswith(")"):
        return 0.0

    argument = raw[5:-1].strip()

    # blur() defaults to zero.
    if not argument:
        return 0.0

    if argument.endswith("px"):
        argument = argument[:-2].strip()
    elif argument not in ["0", "+0", "-0", "0.0", "+0.0", "-0.0"]:
        # For now, only CSS px lengths are implemented.
        return 0.0

    try:
        sigma = float(argument)
    except (TypeError, ValueError):
        return 0.0

    # Negative blur values are invalid CSS; treating them as zero keeps the
    # renderer robust while we do not yet have a full CSS value validator.
    return max(0.0, sigma)


def make_skia_surface(width, height):
    width = max(1, int(width))
    height = max(1, int(height))
    info = skia.ImageInfo.Make(
        width,
        height,
        ct=skia.kRGBA_8888_ColorType,
        at=skia.kUnpremul_AlphaType,
    )
    return skia.Surface.MakeRaster(info)


def centered_icon_rect(rect, size=ICON_DEFAULT_SIZE):
    """Return a square Skia rect centered inside a control's layout rect."""
    size = float(size)
    cx = (rect.left() + rect.right()) / 2
    cy = (rect.top() + rect.bottom()) / 2
    half = size / 2
    return skia.Rect.MakeLTRB(
        cx - half,
        cy - half,
        cx + half,
        cy + half,
    )


def build_star_path(rect):
    """Build a five-point star normalized to rect."""
    cx = (rect.left() + rect.right()) / 2
    cy = (rect.top() + rect.bottom()) / 2
    width = rect.right() - rect.left()
    height = rect.bottom() - rect.top()
    outer = min(width, height) * 0.46
    inner = outer * 0.45

    path = skia.Path()
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        radius = outer if i % 2 == 0 else inner
        x = cx + math.cos(angle) * radius
        y = cy + math.sin(angle) * radius

        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)

    path.close()
    return path


def build_lock_path(rect):
    """Build a simple outline padlock path normalized to rect."""
    left = rect.left()
    top = rect.top()
    right = rect.right()
    bottom = rect.bottom()
    width = right - left
    height = bottom - top
    cx = (left + right) / 2

    body_left = left + width * 0.18
    body_right = right - width * 0.18
    body_top = top + height * 0.46
    body_bottom = bottom - height * 0.10

    shackle_left = left + width * 0.30
    shackle_right = right - width * 0.30
    shackle_top = top + height * 0.10

    path = skia.Path()

    # Lock body.
    path.moveTo(body_left, body_top)
    path.lineTo(body_right, body_top)
    path.lineTo(body_right, body_bottom)
    path.lineTo(body_left, body_bottom)
    path.close()

    # Lock shackle. Keep this as a second sub-path so the same stroke paint
    # can render both body and shackle.
    path.moveTo(shackle_left, body_top)
    path.lineTo(shackle_left, top + height * 0.30)
    path.lineTo(cx, shackle_top)
    path.lineTo(shackle_right, top + height * 0.30)
    path.lineTo(shackle_right, body_top)

    return path


def build_icon_path(icon_name, rect):
    """Map a semantic Chrome icon name to Skia vector geometry."""
    left = rect.left()
    top = rect.top()
    right = rect.right()
    bottom = rect.bottom()
    width = right - left
    height = bottom - top
    cx = (left + right) / 2
    cy = (top + bottom) / 2

    if icon_name == "plus":
        path = skia.Path()
        path.moveTo(cx, top + height * 0.20)
        path.lineTo(cx, bottom - height * 0.20)
        path.moveTo(left + width * 0.20, cy)
        path.lineTo(right - width * 0.20, cy)
        return path

    if icon_name == "back":
        path = skia.Path()
        path.moveTo(right - width * 0.22, top + height * 0.20)
        path.lineTo(left + width * 0.28, cy)
        path.lineTo(right - width * 0.22, bottom - height * 0.20)
        return path

    if icon_name == "forward":
        path = skia.Path()
        path.moveTo(left + width * 0.22, top + height * 0.20)
        path.lineTo(right - width * 0.28, cy)
        path.lineTo(left + width * 0.22, bottom - height * 0.20)
        return path

    if icon_name == "star":
        return build_star_path(rect)

    if icon_name == "lock":
        return build_lock_path(rect)

    return None


class Blur:
    """Display-list effect node for CSS filter: blur().

    Blur is a pixel-moving effect, so it must rasterize the whole element
    subtree into an intermediate layer and apply the image filter *before*
    overflow clipping, opacity, and mix-blend-mode.
    """
    def __init__(self, sigma, children):
        self.sigma = max(0.0, float(sigma))
        self.children = list(children)

        self.rect = skia.Rect.MakeEmpty()
        for cmd in self.children:
            if hasattr(cmd, "rect"):
                self.rect.join(cmd.rect)

        # A Gaussian is effectively negligible beyond roughly 3 sigma.
        # Expanding the visual bounds makes parent effect bounds represent the
        # pixels that blur can move outside the original geometry.
        if self.sigma > 0.0 and not self.rect.isEmpty():
            pad = 3.0 * self.sigma
            self.rect = skia.Rect.MakeLTRB(
                self.rect.left() - pad,
                self.rect.top() - pad,
                self.rect.right() + pad,
                self.rect.bottom() + pad,
            )

    def execute(self, canvas):
        if not self.children:
            return

        if self.sigma <= 0.0:
            for cmd in self.children:
                cmd.execute(canvas)
            return

        # CSS blur(<length>) defines <length> as Gaussian standard deviation,
        # which maps directly to Skia's sigmaX/sigmaY. The filter is attached
        # to the saveLayer paint so it is applied when the layer is restored.
        image_filter = skia.ImageFilters.Blur(self.sigma, self.sigma)
        paint = skia.Paint(ImageFilter=image_filter)

        canvas.saveLayer(None, paint)
        for cmd in self.children:
            cmd.execute(canvas)
        canvas.restore()


class Blend:
    """Display-list effect node for opacity and blend/compositing.

    The important optimization is that opacity and blend mode share one
    temporary layer. If neither effect needs isolation, child commands are
    drawn directly into the current canvas without saveLayer().
    """
    def __init__(self, opacity, blend_mode, children):
        self.opacity = parse_opacity(opacity)

        raw_blend_mode = str(blend_mode or "").strip().casefold()
        # Treat CSS's ordinary compositing values as "no special blend mode".
        # paint_visual_effects may explicitly pass "source-over" when it needs
        # an isolated layer for overflow clipping.
        if raw_blend_mode in ["", "normal", "src-over"]:
            self.blend_mode = None
        else:
            self.blend_mode = raw_blend_mode

        self.should_save = bool(self.blend_mode) or self.opacity < 1.0

        self.children = list(children)
        self.rect = skia.Rect.MakeEmpty()
        for cmd in self.children:
            if hasattr(cmd, "rect"):
                self.rect.join(cmd.rect)

    def execute(self, canvas):
        if not self.children:
            return

        paint = skia.Paint(
            Alphaf=self.opacity,
            BlendMode=parse_blend_mode(self.blend_mode),
        )

        if self.should_save:
            canvas.saveLayer(None, paint)

        for cmd in self.children:
            cmd.execute(canvas)

        if self.should_save:
            canvas.restore()


class Scroll:
    """Display-list node for a scrollable element's child content.

    Child commands remain in document/layout coordinates. During rasterization
    the scroll container clips them to its fixed border box and translates the
    child subtree upward by scroll_y. Nested Scroll nodes naturally compose.
    """
    def __init__(self, rect, scroll_y, children):
        self.clip_rect = rect
        self.scroll_y = max(0.0, float(scroll_y))
        self.children = list(children)

        # The visible bounds of a scrolling subtree are the container bounds,
        # not the full (possibly very tall) layout-overflow bounds.
        self.rect = rect

    def execute(self, canvas):
        if not self.children:
            return

        canvas.save()
        canvas.clipRect(self.clip_rect)
        canvas.translate(0, -self.scroll_y)
        for cmd in self.children:
            cmd.execute(canvas)
        canvas.restore()


def paint_visual_effects(node, cmds, rect=None):
    """Apply filter, clipping, opacity, and blending in CSS rendering order.

    Rendering order:
      1. paint the element subtree;
      2. filter: blur() the complete subtree;
      3. apply overflow clipping;
      4. apply opacity and mix-blend-mode while compositing to the backdrop.

    Blur cannot be merged into the final Blend layer because it moves pixels.
    It therefore owns an inner saveLayer, while opacity and blend mode continue
    sharing the optimized outer Blend layer.
    """
    if not cmds or not isinstance(node, Element):
        return cmds

    # Synthetic button-content mirrors the button's style for text/layout.
    # Its visual effects belong to the real <button>, not this helper node.
    if node.tag == "button-content":
        return cmds

    blur_sigma = parse_blur_filter(node.style.get("filter", "none"))
    opacity = parse_opacity(node.style.get("opacity", "1.0"))

    raw_blend_mode = str(
        node.style.get("mix-blend-mode", "normal") or "normal"
    ).strip().casefold()

    # A normal CSS blend mode does not need a layer by itself.
    if raw_blend_mode in ["", "normal", "src-over", "source-over"]:
        blend_mode = None
    else:
        blend_mode = raw_blend_mode

    overflow = str(
        node.style.get("overflow", "visible") or "visible"
    ).strip().casefold()

    clip = overflow in ["clip", "scroll"] and rect is not None

    # FILTER STAGE. Blur wraps only the element's painted subtree. Because this
    # happens before adding the clip mask below, blurred pixels may expand and
    # are then cut back by overflow: clip, matching browser rendering order.
    filtered_cmds = list(cmds)
    if blur_sigma > 0.0:
        filtered_cmds = [Blur(blur_sigma, filtered_cmds)]

    # CLIP STAGE. destination-in must operate inside an isolated element layer
    # or it would also erase pixels painted by earlier siblings.
    if clip:
        if not blend_mode:
            blend_mode = "source-over"

        border_radius = parse_css_px(
            node.style.get("border-radius", "0px"),
            default=0.0,
        )

        filtered_cmds.append(
            Blend(
                1.0,
                "destination-in",
                [DrawRRect(rect, border_radius, "white")],
            )
        )

    # COMPOSITING STAGE. Opacity and mix-blend-mode still share one outer
    # layer. If neither effect nor clipping needs isolation, Blend.execute()
    # simply forwards the children without creating another surface.
    return [Blend(opacity, blend_mode, filtered_cmds)]

def paint_tree(layout_object, display_list):
    """Build the tree-shaped display list with descendant effects.

    Own paint commands and descendant commands are kept separate until the
    layout object's effect stage. A scroll container can therefore keep its
    own background fixed while translating only its descendant content.
    """
    should_paint = getattr(layout_object, "should_paint", lambda: True)
    own_cmds = []

    if should_paint():
        own_cmds = layout_object.paint()
        for cmd in own_cmds:
            if hasattr(cmd, "execute"):
                cmd.layout_object = layout_object

    child_cmds = []
    for child in layout_object.children:
        paint_tree(child, child_cmds)

    if should_paint():
        if hasattr(layout_object, "paint_effects"):
            cmds = layout_object.paint_effects(own_cmds, child_cmds)
        else:
            cmds = own_cmds + child_cmds
            node = getattr(layout_object, "node", None)
            rect = None
            self_rect = getattr(layout_object, "self_rect", None)
            if callable(self_rect):
                rect = self_rect()
            cmds = paint_visual_effects(node, cmds, rect)
    else:
        cmds = child_cmds

    display_list.extend(cmds)


def paint_commands_back_to_front(commands):
    """Yield leaf draw commands in visual hit-test order through effect nodes."""
    for cmd in reversed(commands):
        children = getattr(cmd, "children", None)
        if children is not None:
            yield from paint_commands_back_to_front(children)
        else:
            yield cmd

def hit_test_paint_commands(commands, x, y):
    """Return the topmost leaf command under a point.

    Blend/Blur nodes preserve coordinates. Scroll nodes first reject points
    outside their clip box, then convert the point into the scrolled child
    coordinate space. This recursion also handles nested scroll containers.
    """
    x = float(x)
    y = float(y)

    for cmd in reversed(commands):
        if isinstance(cmd, Scroll):
            if not cmd.clip_rect.contains(x, y):
                continue

            hit = hit_test_paint_commands(
                cmd.children,
                x,
                y + cmd.scroll_y,
            )
            if hit is not None:
                return hit
            continue

        children = getattr(cmd, "children", None)
        if children is not None:
            hit = hit_test_paint_commands(children, x, y)
            if hit is not None:
                return hit
            continue

        if not hasattr(cmd, "rect"):
            continue
        if not cmd.rect.contains(x, y):
            continue
        if not hasattr(cmd, "layout_object"):
            continue
        if not hit_test_layout_object(cmd.layout_object, x, y):
            continue

        return cmd

    return None


def rects_intersect(a, b):
    return not (
        a.right() < b.left()
        or a.left() > b.right()
        or a.bottom() < b.top()
        or a.top() > b.bottom()
    )

def rect_intersection(a, b):
    left = max(float(a.left()), float(b.left()))
    top = max(float(a.top()), float(b.top()))
    right = min(float(a.right()), float(b.right()))
    bottom = min(float(a.bottom()), float(b.bottom()))
    if right < left or bottom < top:
        return None
    return skia.Rect.MakeLTRB(left, top, right, bottom)

def translate_rect(rect, dx=0.0, dy=0.0):
    return skia.Rect.MakeLTRB(
        float(rect.left()) + dx,
        float(rect.top()) + dy,
        float(rect.right()) + dx,
        float(rect.bottom()) + dy,
    )

def clamp_point_to_rect(x, y, rect):
    return (
        min(max(float(x), float(rect.left())), float(rect.right())),
        min(max(float(y), float(rect.top())), float(rect.bottom())),
    )

def point_to_rect_distance_squared(x, y, rect):
    px, py = clamp_point_to_rect(x, y, rect)
    return (float(x) - px) ** 2 + (float(y) - py) ** 2

def interactive_layout_object(layout_object):
    """Whether a touch candidate belongs to a directly activatable control."""
    node = getattr(layout_object, "node", None)
    while node is not None:
        if isinstance(node, Element) and node.tag in ["a", "button", "input"]:
            return True
        node = getattr(node, "parent", None)
    return False

def touch_area_hits_layout_object(layout_object, touch_rect):
    """Respect rounded element geometry when a fuzzy touch area overlaps it."""
    node = getattr(layout_object, "node", None)
    if not isinstance(node, Element):
        return True

    radius = parse_css_px(node.style.get("border-radius", "0px"), default=0.0)
    if radius <= 0.0:
        return True

    layout_rect = layout_object_rect(layout_object)
    if layout_rect is None:
        return True

    overlap = rect_intersection(layout_rect, touch_rect)
    if overlap is None:
        return False

    # A few representative points are enough for this axis-aligned toy browser:
    # if the overlap only touches a rounded-off corner, all samples remain out.
    cx = (overlap.left() + overlap.right()) / 2.0
    cy = (overlap.top() + overlap.bottom()) / 2.0
    samples = [
        (cx, cy),
        (overlap.left(), overlap.top()),
        (overlap.right(), overlap.top()),
        (overlap.left(), overlap.bottom()),
        (overlap.right(), overlap.bottom()),
        (cx, overlap.top()),
        (cx, overlap.bottom()),
        (overlap.left(), cy),
        (overlap.right(), cy),
    ]
    return any(
        point_in_rounded_rect(px, py, layout_rect, radius, radius)
        for px, py in samples
    )

def collect_touch_candidates(commands, touch_rect, center_x, center_y, out):
    """Collect leaf commands overlapped by a touch area in visual front-to-back order."""
    for cmd in reversed(commands):
        if isinstance(cmd, Scroll):
            visible_touch = rect_intersection(touch_rect, cmd.clip_rect)
            if visible_touch is None:
                continue

            clamped_x, clamped_y = clamp_point_to_rect(
                center_x, center_y, visible_touch
            )
            collect_touch_candidates(
                cmd.children,
                translate_rect(visible_touch, dy=cmd.scroll_y),
                clamped_x,
                clamped_y + cmd.scroll_y,
                out,
            )
            continue

        children = getattr(cmd, "children", None)
        if children is not None:
            collect_touch_candidates(
                children, touch_rect, center_x, center_y, out
            )
            continue

        if not hasattr(cmd, "rect") or not hasattr(cmd, "layout_object"):
            continue
        if not rects_intersect(cmd.rect, touch_rect):
            continue
        if not touch_area_hits_layout_object(cmd.layout_object, touch_rect):
            continue

        distance = point_to_rect_distance_squared(center_x, center_y, cmd.rect)
        area = max(
            0.0,
            float(cmd.rect.width()) * float(cmd.rect.height()),
        )
        out.append((
            0 if interactive_layout_object(cmd.layout_object) else 1,
            distance,
            area,
            len(out),
            cmd,
        ))

def touch_hit_test_paint_commands(commands, x, y, radius=TOUCH_RADIUS_PX):
    """Area-based hit testing used for finger taps.

    Interactive controls are preferred inside the contact patch, then the nearest
    and smallest visual candidate. If no interactive element overlaps the finger,
    preserve an exact point hit (important for focusing overflow scroll containers).
    """
    x = float(x)
    y = float(y)
    radius = max(0.0, float(radius))

    exact = hit_test_paint_commands(commands, x, y)
    if exact is not None and interactive_layout_object(exact.layout_object):
        return exact

    touch_rect = skia.Rect.MakeLTRB(
        x - radius, y - radius, x + radius, y + radius
    )
    candidates = []
    collect_touch_candidates(commands, touch_rect, x, y, candidates)

    interactive = [candidate for candidate in candidates if candidate[0] == 0]
    if interactive:
        return min(interactive)[-1]

    if exact is not None:
        return exact

    if not candidates:
        return None
    return min(candidates)[-1]

def tree_to_list(tree,out):
    out.append(tree)
    for child in tree.children:
        tree_to_list(child,out)

    return out

def style_tag_text(node):
    out=[]
    
    for child in node.children:
        if isinstance(child,Text):
            out.append(child.text)

    return "".join(out)

def is_hidden_input(node):
    return (
        getattr(node,"tag",None)=="input"
        and node.attributes.get(
            "type",
            "text"
        ).casefold() == "hidden"
    )

def is_checkbox_input(node):
    return (
        getattr(node,"tag",None) == "input" 
        and node.attributes.get("type") == "checkbox"
    )

class BrowserApp:
    """Own SDL itself and route SDL events to the correct BrowserWindow."""
    def __init__(self):
        init_flags = sdl2.SDL_INIT_VIDEO | sdl2.SDL_INIT_EVENTS
        if sdl2.SDL_Init(init_flags) != 0:
            error = sdl2.SDL_GetError()
            if isinstance(error, bytes):
                error = error.decode("utf8", errors="replace")
            raise RuntimeError("SDL_Init failed: {}".format(error))

        # The process starts on the browser/compositor-style thread. SDL, browser
        # chrome, raster, draw, and native-window presentation stay on this thread.
        threading.current_thread().name = "Browser thread"

        # One process-wide trace shared by every native BrowserWindow. Keeping
        # ownership here avoids multiple windows overwriting browser.trace.
        self.measure = MeasureTime()
        self.measure.thread_name(threading.current_thread().name)

        self.windows = []
        self.windows_by_id = {}
        self.visited_urls = set()
        self.bookmarks = set()
        self.running = False

        # Active physical fingers. We wait for FINGERUP before turning a short,
        # single-finger contact into a browser tap/click. This prevents drags and
        # multi-touch gestures from accidentally activating links.
        self.active_touches = {}

        sdl2.SDL_StartTextInput()

    def new_window(self, url=None):
        if url is None:
            url = URL("https://browser.engineering/")

        browser_window = BrowserWindow(self)
        self.windows.append(browser_window)
        self.windows_by_id[browser_window.window_id] = browser_window
        browser_window.new_tab(url)
        return browser_window

    def unregister_window(self, browser_window):
        self.windows_by_id.pop(browser_window.window_id, None)
        if browser_window in self.windows:
            self.windows.remove(browser_window)

        if not self.windows:
            self.running = False

    def window_for_id(self, window_id):
        return self.windows_by_id.get(int(window_id))

    def decode_text_input(self, event):
        raw = bytes(event.text.text)
        raw = raw.split(b"\x00", 1)[0]
        return raw.decode("utf8", errors="ignore")

    def window_for_touch_event(self, event):
        """Resolve the SDL window underneath a touch event."""
        window_id = int(getattr(event.tfinger, "windowID", 0) or 0)
        if window_id:
            return self.window_for_id(window_id)

        # Some backends may not provide windowID. Falling back is unambiguous
        # when this app currently owns exactly one native window.
        if len(self.windows) == 1:
            return self.windows[0]
        return None

    def touch_pixels(self, browser_window, event):
        """Convert SDL's normalized finger coordinates into window pixels."""
        nx = max(0.0, min(1.0, float(event.tfinger.x)))
        ny = max(0.0, min(1.0, float(event.tfinger.y)))
        x = int(round(nx * max(browser_window.width - 1, 0)))
        y = int(round(ny * max(browser_window.height - 1, 0)))
        return x, y

    def touch_key(self, event):
        return (
            int(event.tfinger.touchId),
            int(event.tfinger.fingerId),
        )

    def dispatch_event(self, event):
        event_type = event.type

        if event_type == sdl2.SDL_QUIT:
            self.running = False
            return

        if event_type == sdl2.SDL_WINDOWEVENT:
            browser_window = self.window_for_id(event.window.windowID)
            if browser_window is None:
                return

            if event.window.event == sdl2.SDL_WINDOWEVENT_CLOSE:
                browser_window.close()
            elif event.window.event in [
                sdl2.SDL_WINDOWEVENT_SIZE_CHANGED,
                sdl2.SDL_WINDOWEVENT_RESIZED,
            ]:
                browser_window.resize(
                    int(event.window.data1),
                    int(event.window.data2),
                )
            return

        if event_type == sdl2.SDL_MOUSEBUTTONUP:
            browser_window = self.window_for_id(event.button.windowID)
            if browser_window is None:
                return

            # SDL can synthesize a mouse click from a real finger tap. Since the
            # browser handles SDL_FINGERUP directly below, ignore that synthetic
            # mouse event or the DOM click would fire twice.
            touch_mouse_id = getattr(sdl2, "SDL_TOUCH_MOUSEID", None)
            if (
                touch_mouse_id is not None
                and int(event.button.which) == int(touch_mouse_id)
            ):
                return

            x = int(event.button.x)
            y = int(event.button.y)

            if event.button.button == sdl2.SDL_BUTTON_LEFT:
                # Desktop test path: Shift + left click emulates a finger tap and
                # therefore uses area hit testing instead of exact point testing.
                shift_mask = getattr(
                    sdl2,
                    "KMOD_SHIFT",
                    getattr(sdl2, "KMOD_LSHIFT", 0)
                    | getattr(sdl2, "KMOD_RSHIFT", 0),
                )
                if int(sdl2.SDL_GetModState()) & int(shift_mask):
                    browser_window.handle_touch(x, y, TOUCH_RADIUS_PX)
                else:
                    browser_window.handle_click(x, y)
            elif event.button.button == sdl2.SDL_BUTTON_MIDDLE:
                browser_window.handle_middle_click(x, y)
            return

        if event_type in [
            getattr(sdl2, "SDL_FINGERDOWN", -1),
            getattr(sdl2, "SDL_FINGERMOTION", -1),
            getattr(sdl2, "SDL_FINGERUP", -1),
        ]:
            browser_window = self.window_for_touch_event(event)
            if browser_window is None:
                return

            key = self.touch_key(event)
            x, y = self.touch_pixels(browser_window, event)

            if event_type == getattr(sdl2, "SDL_FINGERDOWN", -1):
                # If another finger is already down in this window, mark both as
                # multi-touch so neither finger release becomes a click.
                multi = False
                for state in self.active_touches.values():
                    if state["window_id"] == browser_window.window_id:
                        state["multi"] = True
                        multi = True

                self.active_touches[key] = {
                    "window_id": browser_window.window_id,
                    "start_x": x,
                    "start_y": y,
                    "moved": False,
                    "multi": multi,
                }
                return

            state = self.active_touches.get(key)
            if state is None:
                return

            distance = math.hypot(
                x - state["start_x"],
                y - state["start_y"],
            )
            if distance > TOUCH_MOVE_TOLERANCE_PX:
                state["moved"] = True

            if event_type == getattr(sdl2, "SDL_FINGERMOTION", -1):
                return

            # FINGERUP: a short single-finger contact is a tap. Drags and any
            # multi-touch sequence are intentionally not converted into click.
            self.active_touches.pop(key, None)
            if state["moved"] or state["multi"]:
                return

            browser_window.handle_touch(x, y, TOUCH_RADIUS_PX)
            return

        if event_type == sdl2.SDL_MOUSEWHEEL:
            browser_window = self.window_for_id(event.wheel.windowID)
            if browser_window is None:
                return

            delta = int(event.wheel.y)
            if getattr(event.wheel, "direction", 0) == getattr(
                sdl2, "SDL_MOUSEWHEEL_FLIPPED", -1
            ):
                delta = -delta
            browser_window.handle_mousewheel(delta)
            return

        if event_type == sdl2.SDL_KEYDOWN:
            browser_window = self.window_for_id(event.key.windowID)
            if browser_window is None:
                return

            sym = event.key.keysym.sym
            mod = event.key.keysym.mod

            if (mod & sdl2.KMOD_CTRL) and sym in [
                sdl2.SDLK_n,
                getattr(sdl2, "SDLK_N", sdl2.SDLK_n),
            ]:
                browser_window.handle_new_window()
            elif sym == sdl2.SDLK_RETURN:
                browser_window.handle_enter()
            elif sym == sdl2.SDLK_DOWN:
                browser_window.handle_down()
            elif sym == sdl2.SDLK_UP:
                browser_window.handle_up()
            elif sym == sdl2.SDLK_BACKSPACE:
                browser_window.handle_backspace()
            elif sym == sdl2.SDLK_LEFT:
                browser_window.handle_left()
            elif sym == sdl2.SDLK_RIGHT:
                browser_window.handle_right()
            return

        if event_type == sdl2.SDL_TEXTINPUT:
            browser_window = self.window_for_id(event.text.windowID)
            if browser_window is None:
                return

            text = self.decode_text_input(event)
            if text:
                browser_window.handle_key(text)

    def run(self):
        self.running = True
        event = sdl2.SDL_Event()

        try:
            while self.running and self.windows:
                # Wait briefly when idle so the loop does not consume a CPU core.
                if sdl2.SDL_WaitEventTimeout(ctypes.byref(event), 16) != 0:
                    self.dispatch_event(event)

                while sdl2.SDL_PollEvent(ctypes.byref(event)) != 0:
                    self.dispatch_event(event)

                # Tab TaskRunner queues are now consumed by their own dedicated
                # main threads. The browser thread only composites/presents frames
                # and schedules future animation-frame tasks.
                for browser_window in list(self.windows):
                    browser_window.raster_and_draw()
                    browser_window.schedule_animation_frame()
        finally:
            for browser_window in list(self.windows):
                browser_window.close()

            self.measure.finish()
            sdl2.SDL_StopTextInput()
            sdl2.SDL_Quit()


class DrawText:
    def __init__(self, x1, y1, text, font, color):
        self.text = text
        self.font = font
        self.color = color

        metrics = font.getMetrics()
        width = font.measureText(str(text))
        height = metrics.fDescent - metrics.fAscent
        self.rect = skia.Rect.MakeLTRB(
            float(x1),
            float(y1),
            float(x1 + width),
            float(y1 + height),
        )

    def execute(self, canvas):
        paint = skia.Paint(
            AntiAlias=True,
            Color=parse_color(self.color),
        )
        baseline = self.rect.top() - self.font.getMetrics().fAscent
        canvas.drawString(
            str(self.text),
            float(self.rect.left()),
            float(baseline),
            self.font,
            paint,
        )


class DrawRect:
    def __init__(self, rect, color):
        self.rect = rect
        self.color = color

    def execute(self, canvas):
        paint = skia.Paint(Color=parse_color(self.color))
        canvas.drawRect(self.rect, paint)


class DrawHitTest:
    """Invisible display-list leaf used only to expose a layout hit region."""
    def __init__(self, rect):
        self.rect = rect

    def execute(self, canvas):
        pass


class DrawRRect:
    def __init__(self, rect, radius, color):
        self.rect = rect
        self.radius = max(0.0, float(radius))
        self.color = color

    def execute(self, canvas):
        paint = skia.Paint(Color=parse_color(self.color))
        rrect = skia.RRect.MakeRectXY(self.rect, self.radius, self.radius)
        canvas.drawRRect(rrect, paint)


class DrawLine:
    def __init__(self, x1, y1, x2, y2, color, thickness):
        # Keep the real endpoints because a line may slope upward. The rect is
        # only its bounding box for clipping and hit testing.
        self.x1 = float(x1)
        self.y1 = float(y1)
        self.x2 = float(x2)
        self.y2 = float(y2)
        self.color = color
        self.thickness = thickness
        self.rect = skia.Rect.MakeLTRB(
            min(self.x1, self.x2),
            min(self.y1, self.y2),
            max(self.x1, self.x2),
            max(self.y1, self.y2),
        )

    def execute(self, canvas):
        path = (
            skia.Path()
            .moveTo(self.x1, self.y1)
            .lineTo(self.x2, self.y2)
        )
        paint = skia.Paint(
            AntiAlias=True,
            Color=parse_color(self.color),
            StrokeWidth=float(self.thickness),
            Style=skia.Paint.kStroke_Style,
        )
        canvas.drawPath(path, paint)


class DrawOutline:
    def __init__(self, rect, color, thickness):
        self.rect = rect
        self.color = color
        self.thickness = thickness

    def execute(self, canvas):
        paint = skia.Paint(
            AntiAlias=True,
            Color=parse_color(self.color),
            StrokeWidth=float(self.thickness),
            Style=skia.Paint.kStroke_Style,
        )
        canvas.drawRect(self.rect, paint)


class DrawRRectOutline:
    def __init__(self, rect, radius, color, thickness):
        self.rect = rect
        self.radius = max(0.0, float(radius))
        self.color = color
        self.thickness = thickness

    def execute(self, canvas):
        paint = skia.Paint(
            AntiAlias=True,
            Color=parse_color(self.color),
            StrokeWidth=float(self.thickness),
            Style=skia.Paint.kStroke_Style,
        )
        rrect = skia.RRect.MakeRectXY(self.rect, self.radius, self.radius)
        canvas.drawRRect(rrect, paint)


class DrawVectorIcon:
    """A semantic Chrome icon rasterized from a Skia Path."""
    def __init__(
        self,
        rect,
        icon_name,
        color=ICON_COLOR_ENABLED,
        stroke_width=ICON_STROKE_WIDTH,
        fill=False,
    ):
        self.rect = rect
        self.icon_name = icon_name
        self.color = color
        self.stroke_width = float(stroke_width)
        self.fill = bool(fill)

    def execute(self, canvas):
        path = build_icon_path(self.icon_name, self.rect)
        if path is None:
            return

        paint = skia.Paint(
            AntiAlias=True,
            Color=parse_color(self.color),
            StrokeWidth=self.stroke_width,
            Style=(
                skia.Paint.kFill_Style
                if self.fill
                else skia.Paint.kStroke_Style
            ),
        )
        canvas.drawPath(path, paint)


class DrawImage:
    def __init__(self, x, y, img):
        self.img = img
        self.rect = skia.Rect.MakeXYWH(
            float(x),
            float(y),
            float(img.width()),
            float(img.height()),
        )

    def execute(self, canvas):
        canvas.drawImageRect(self.img.image, self.rect)

class DocumentLayout:
    def __init__(self,node,viewport_width=None):#build root of layout tree
        self.node=node
        self.viewport_width = viewport_width if viewport_width is not None else WIDTH
        self.parent=None
        self.previous=None
        self.children=[]

        self.x=None
        self.y=None
        self.width=None
        self.height=None

    def layout(self): # build child layout objects
        self.x=HSTEP
        self.y=VSTEP
        self.width=self.viewport_width-HSTEP*2
        
        
        child=BlockLayout([self.node],self,None)
        self.children=[child]
        child.layout()

        self.height=child.height

    def paint(self):
        return []

class LineLayout:
    def __init__(self,node,parent,previous):
        self.node=node
        self.parent=parent
        self.previous=previous
        self.children=[]

        self.x=None
        self.y=None
        self.width=None
        self.height=None

    def layout(self):
        self.width=self.parent.width
        self.x=self.parent.x

        if self.previous:
            self.y = self.previous.y + self.previous.height
        else:
            self.y=self.parent.y
        
        for child in self.children:
            child.layout()

        if not self.children:
            self.height=0
            return

        line_width=sum(child.width+child.space_after for child in self.children)

        if self.children:
            line_width-=self.children[-1].space_after

        align=self.parent.node.style.get("text-align","left")

        if align=="center":
            cursor_x=self.x+(self.width-line_width)/2
        elif align=="right" or USE_RTL: # same as RTL
            cursor_x=self.x+self.width-line_width
        else:
            cursor_x=self.x

        for child in self.children:
            child.x=cursor_x
            cursor_x+= child.width+child.space_after            

#        if USE_RTL:
#            cursor_x=self.x+self.width-line_width
#        else:
#            cursor_x=self.x

        max_ascent = max([
            child.ascent
            for child in self.children
        ])

        max_descent = max([
            child.descent
            for child in self.children
        ])

        baseline = self.y+1.25*max_ascent

        normal_text_children=[
            child for child in self.children
            if isinstance(child,TextLayout) and not getattr(child,"is_sup",False)
        ]

        if normal_text_children:
            normal_ascent=max(child.ascent for child in normal_text_children)
        else:
            normal_ascent=max_ascent
        
        for child in self.children:
            if getattr(child,"is_sup",False):
                child.y=baseline-normal_ascent
            else:
                child.y=baseline-child.ascent

        for child in self.children:
            if hasattr(child,"layout_final"):
                child.layout_final()


        self.height=1.25 *(max_ascent+max_descent)

    def paint(self):
        return []

class TextLayout:
    def __init__(self,node,word,parent,previous,
                is_sup=False,
                is_small_caps=False,
                space_after_override=None,
                font_family_override=None):
        self.node=node
        self.word=word
        self.children=[]
        self.parent=parent
        self.previous=previous


        self.is_sup=is_sup
        self.is_small_caps=is_small_caps
        self.space_after_override=space_after_override
        self.font_family_override=font_family_override

        self.x=None
        self.y=None
        self.width=None
        self.height=None

        self.font=None
        self.ascent=None
        self.descent=None
        self.space_after=None
        
    def layout(self):
        weight=self.node.style["font-weight"]
        
        style=self.node.style["font-style"]
        if style=="normal":
            style="roman"

        size = css_font_size_to_skia(self.node.style.get("font-size", "16px"))
        
        if self.is_sup:
            size=max(1,int(size/2))

        if self.is_small_caps:
            size=max(1,int(size*0.8))
            weight="bold"

        if self.font_family_override:
            family = self.font_family_override
        else:
            family = self.node.style["font-family"]

        self.font=get_font(size,weight,style,family=family)

        self.width=self.font.measureText(self.word)
        self.height=linespace(self.font)

        self.ascent=-self.font.getMetrics().fAscent
        self.descent=self.font.getMetrics().fDescent
        
        if self.space_after_override is None:
            self.space_after=self.font.measureText(" ")
        else:
            self.space_after=self.space_after_override
            

        self.x=None
#        if self.previous:
#            self.x=(
#                self.previous.x
#                +self.previous.width
#                +self.previous.space_after
#            )
#       else:
#            self.x=self.parent.x


    def paint(self):
        color=self.node.style["color"]
        return [DrawText(self.x,self.y,self.word,self.font,color)]

class InputLayout:
    def __init__(self,node,parent,previous):
        self.node = node
        self.children = []
        self.parent = parent
        self.previous = previous

        self.x = None
        self.y = None
        self.width = None
        self.height = None
        
        self.font= None
        self.ascent = None
        self.descent = None
        self.space_after = None

    def parse_px(self,value):
        if value=="auto":
            return None

        if isinstance(value,str) and value.endswith("px"):
            try:
                return int(value[:-2])

            except ValueError:
                return None

        return None

    def layout(self):
        weight = self.node.style["font-weight"]
        
        style = self.node.style["font-style"]
        if style == "normal":
            style = "roman"

        size = css_font_size_to_skia(self.node.style.get("font-size", "16px"))
        family = self.node.style["font-family"]

        self.font=get_font(size,weight,style,family=family)

        if is_checkbox_input(self.node):
            self.width = CHECKBOX_SIZE
            self.height = CHECKBOX_SIZE
            self.ascent = CHECKBOX_SIZE
            self.descent = 0
        else:
            css_width = self.parse_px(self.node.style.get("width","auto"))
            
            if css_width:
                self.width = css_width
            else:
                self.width = INPUT_WIDTH_PX

            self.height = linespace(self.font)
            self.ascent=-self.font.getMetrics().fAscent
            self.descent=self.font.getMetrics().fDescent


        self.space_after=self.font.measureText(" ")
        self.x=None

    def self_rect(self):
        return skia.Rect.MakeLTRB(
            self.x,
            self.y,
            self.x+self.width,
            self.y+self.height
        )

    def paint(self):
        cmds=[]

        bgcolor=self.node.style.get("background-color","transparent")
        if bgcolor!="transparent" and not is_checkbox_input(self.node):
            radius = self.parse_px(self.node.style.get("border-radius", "0px")) or 0
            rect = self.self_rect()
            if radius > 0:
                cmds.append(DrawRRect(rect, radius, bgcolor))
            else:
                cmds.append(DrawRect(rect, bgcolor))

        if is_checkbox_input(self.node):
            rect = self.self_rect()

            cmds.append(DrawRect(
                rect,
                "white"
            ))

            cmds.append(DrawOutline(
                rect,
                "black",
                1
            ))

            if getattr(self.node,"is_checked",False):
                cmds.append(DrawLine(
                    rect.left()+3,
                    rect.top()+CHECKBOX_SIZE//2,
                    rect.left()+CHECKBOX_SIZE//2,
                    rect.bottom()-3,
                    "black",
                    2
                ))

                cmds.append(DrawLine(
                    rect.left()+CHECKBOX_SIZE//2,
                    rect.bottom()-3,
                    rect.right()-3,
                    rect.top()+3,
                    "black",
                    2
                ))

            return cmds

        if self.node.tag=="input":
            text=self.node.attributes.get("value","")
            
            # if type=password, the value will show all '*'
            if self.node.attributes.get("type","text").casefold()=="password":
                text="*" * len(text)

        else:
            text = ""

        color=self.node.style["color"]
        cmds.append(DrawText(
            self.x,
            self.y,
            text,
            self.font,
            color
        ))

        if self.node.is_focused:
            cursor_index = getattr(self.node,"cursor_index",len(text))
            cursor_index = max(0,min(cursor_index,len(text)))

            cx = self.x + self.font.measureText(text[:cursor_index])

            cmds.append(DrawLine(
                cx,
                self.y,
                cx,
                self.y+self.height,
                "black",
                1
            ))

        return cmds
        
class ButtonContentParent:
    def __init__(self,x,y,width):
        self.x=x
        self.y=y
        self.width=width

class ButtonLayout:
    def __init__(self,node,parent,previous):
        self.node=node
        self.children=[]
        self.parent=parent
        self.previous=previous

        self.x=None
        self.y=None
        self.width=None
        self.height=None

        self.font=None
        self.ascent=None
        self.descent=None
        self.space_after=None
        
    def parse_px(self,value):
        if value=="auto":
            return None

        if isinstance(value,str) and value.endswith("px"):
            try:
                return int(value[:-2])

            except ValueError:
                return None

        return None

    def make_content_node(self):
        content_node = Element("button-content",{},self.node)

        # don't change really DOM tree, just make a layout wrapper
        content_node.children = self.node.children

        # wrapper must have style，otherwise BlockLayout read style will wrong
        content_node.style=dict(self.node.style)
        content_node.style["background-color"] = "transparent"
        content_node.style["display"] = "block"
        content_node.style["overflow"] = "visible"
        content_node.style["opacity"] = "1.0"
        content_node.style["mix-blend-mode"] = "normal"
        content_node.style["filter"] = "none"

        return content_node

    def layout(self):
        weight = self.node.style["font-weight"]

        style = self.node.style["font-style"]
        if style=="normal":
            style="roman"

        size = css_font_size_to_skia(self.node.style.get("font-size", "16px"))
        family = self.node.style["font-family"]
        
        self.font = get_font(size,weight,style,family=family)

        css_width = self.parse_px(self.node.style.get("width","auto"))
        if css_width:
            self.width = css_width
        else:
            self.width = INPUT_WIDTH_PX

        content_width = max(1,self.width-2*BUTTON_PADDING)
        
        if self.node.children:
            content_node = self.make_content_node()
            content_parent = ButtonContentParent(
                0,
                0,
                content_width
            )

            child = BlockLayout([content_node],content_parent,None)
            self.children = [child]
            child.layout()

            content_height = child.height

        else:
            content_height = linespace(self.font)
            self.children=[]

        self.height = max(
            content_height + 2 * BUTTON_PADDING,
            linespace(self.font) + 2*BUTTON_PADDING
        )

        # make full of the button to a very high inline object
        self.ascent = self.height
        self.descent = 0

        self.space_after = self.font.measureText(" ")
        self.x = None

    def layout_final(self):
        if not self.node.children:
            return
        
        content_width = max(1,self.width-2*BUTTON_PADDING)

        content_node = self.make_content_node()
        content_parent = ButtonContentParent(
            self.x + BUTTON_PADDING,
            self.y + BUTTON_PADDING,
            content_width
        )

        child = BlockLayout([content_node],content_parent,None)
        self.children = [child]
        child.layout()
        
    def self_rect(self):
        return skia.Rect.MakeLTRB(
            self.x,
            self.y,
            self.x+self.width,
            self.y+self.height
        )

    def paint(self):
        rect = self.self_rect()

        bgcolor = self.node.style.get("background-color","lightgray")
        if bgcolor=="transparent":
            bgcolor="lightgray"

        radius = self.parse_px(self.node.style.get("border-radius", "0px")) or 0
        if radius > 0:
            return [
                DrawRRect(rect, radius, bgcolor),
                DrawRRectOutline(rect, radius, "black", 1),
            ]

        return [
            DrawRect(rect, bgcolor),
            DrawOutline(rect, "black", 1),
        ]

class EmojiLayout:
    def __init__(self,node,img,parent, previous, space_after):
        self.node=node
        self.img=img
        self.parent=parent
        self.previous=previous
        self.children=[]

        self.x=None
        self.y=None
        self.width=None
        self.height=None

        self.ascent=None
        self.descent=None
        self.space_after=space_after

    def layout(self):
        self.width=self.img.width()
        self.height=self.img.height()

        # let emoji bottom close to baseline
        self.ascent=self.height
        self.descent=0

        self.x=None
#        if self.previous:
#            self.x=(
#                self.previous.x
#                +self.previous.width
#                +self.previous.space_after
#            )
#        else:
#            self.x=self.parent.x


    def paint(self):
        return [DrawImage(self.x, self.y, self.img)]
    

class BlockLayout: # layout for block level elements
    def __init__(self,nodes,parent,previous):
        self.nodes=nodes
        self.node=nodes[0]
        self.parent=parent
        self.previous=previous
        self.children=[]

        self.x=None
        self.y=None
        self.width=None
        self.height=None

        # Natural laid-out content height is kept separately from a fixed CSS
        # height so overflow: scroll can compute its scroll range.
        self.content_height = 0
        self.scroll = 0

        #self.display_list=[]


    def should_paint(self):
        if isinstance(self.node,Text):
            return True

        if not isinstance(self.node,Element):
            return True

        return self.node.tag not in ["input","button"]

    def self_rect(self):
        return skia.Rect.MakeLTRB(
            self.x,
            self.y,
            self.x + self.width,
            self.y + self.height,
        )

    def paint_effects(self, own_cmds, child_cmds):
        # Background/border paint belongs to the fixed scroll-container box.
        # Only descendants move when the element scrolls.
        if self.is_scrollable():
            content_cmds = []
            if child_cmds:
                content_cmds.append(
                    Scroll(self.self_rect(), self.scroll, child_cmds)
                )
            cmds = own_cmds + content_cmds
        else:
            cmds = own_cmds + child_cmds

        return paint_visual_effects(self.node, cmds, self.self_rect())

    def paint(self):
        cmds=[]

        bgcolor=self.node.style.get("background-color","transparent")

        if bgcolor!="transparent":
            rect = skia.Rect.MakeLTRB(
                self.x,
                self.y,
                self.x + self.width,
                self.y + self.height,
            )
            radius = self.parse_px(self.node.style.get("border-radius", "0px")) or 0
            if radius > 0:
                cmds.append(DrawRRect(rect, radius, bgcolor))
            else:
                cmds.append(DrawRect(rect, bgcolor))

        if isinstance(self.node,Element):

            if self.node.tag=="nav" and self.node.attributes.get("class") =="links":
                x2=self.x+self.width
                y2=self.y+self.height
                cmds.append(DrawRect(skia.Rect.MakeLTRB(self.x, self.y, x2, y2), "lightgray"))

            elif self.node.tag=="nav" and self.node.attributes.get("id")=="toc":
                header_h=VSTEP+4
                x2=self.x+self.width
                y2=self.y+header_h

                #gray background behind the heading
                cmds.append(DrawRect(skia.Rect.MakeLTRB(self.x, self.y, x2, y2), "gray"))

                #heading text
                font=get_font(12,"bold","roman")
                cmds.append(DrawText(self.x+4,self.y+2,"Table of Contents",font,"black"))

            # bullet of list items
            elif  self.node.tag=="li":
                bullet_size=5
                bullet_x=self.x-15
                bullet_y=self.y+8
                cmds.append(
                    DrawRect(
                        skia.Rect.MakeLTRB(
                            bullet_x,
                            bullet_y,
                            bullet_x + bullet_size,
                            bullet_y + bullet_size,
                        ),
                        "black",
                    )
                )

        #inline mode turn text/picture into Draw command
        #if self.layout_mode() == "inline":
         #   for item in self.display_list:
          #      if isinstance(item,tuple) and len(item)==5:
           #         x,y,word,font,color=item
            #        cmds.append(DrawText(x,y,word,font,color))

             #   else:
                    # keep origin emoji/image tuple
          #          cmds.append(item)

        # A transparent scroll container must still be clickable/focusable even
        # when no painted child covers the exact click location.
        if self.is_scrollable():
            cmds.append(DrawHitTest(self.self_rect()))

        return cmds

    def is_block_node(self,node):
        if not isinstance(node,Element):
            return False

        return node.style.get("display","inline") == "block"

    def child_groups(self):
        groups = []
        buffer = []

        def is_whitespace_text(node):
            return isinstance(node,Text) and node.text.isspace()

        all_children = []
        for node in self.nodes:
            if isinstance(node, Element):
                for child in node.children:
                    if isinstance(child, Element) and child.tag == "head":
                        continue
                    all_children.append(child)

        i = 0
        while i < len(all_children):
            child = all_children[i]

            # special case: <h6> followed by <p> should run in
            # Add whitespace text node between h6 and p
            if isinstance(child, Element) and child.tag == "h6":
                j=i+1

                # skip pure whitespace text node between <h6> and <p>
                while j < len(all_children) and is_whitespace_text(all_children[j]):
                    j+=1

                if j < len(all_children):
                    next_child = all_children[j]

                    if isinstance(next_child, Element) and next_child.tag == "p":
                        if buffer:
                            groups.append(buffer)
                            buffer = []

                        # merge h6 + p into one inline/layout group
                        merged = [child] + next_child.children
                        groups.append(merged)

                        # skip h6,whitespace,and p
                        i=j+1
                        continue

                    # also allow h6 + normal text node
                    if not self.is_block_node(next_child):
                        buffer.append(child)
                        i += 1
                        continue

                if buffer:
                    groups.append(buffer)
                    buffer = []
                groups.append([child])
                i += 1
                continue

            if self.is_block_node(child):
                if buffer:
                    groups.append(buffer)
                    buffer = []
                groups.append([child])
            else:
                buffer.append(child)

            i += 1

        if buffer:
            groups.append(buffer)

        return groups


    def layout_mode(self):
        if isinstance(self.node,Element) and self.node.tag in ["input","button"]:
            return "inline"

        if any(self.is_block_node(child)
                for node in self.nodes if isinstance(node,Element)
                for child in node.children):
            return "block"

        else:
            return "inline"

    def layout(self):
        self.x=self.parent.x
        available_width=self.parent.width

        # ident list items ，the text sits to the right of the bullet
        if isinstance(self.node,Element) and self.node.tag=="li":
            self.x+=20
            available_width-=20


        css_width=self.css_width()
        if css_width:
            self.width=css_width
        else:
            self.width=available_width

        if self.previous:
            self.y=self.previous.y+self.previous.height
        else:
            self.y=self.parent.y

        mode=self.layout_mode()

        toc_header_h = 0
        old_y = self.y

        # before layout clear chlidren,void resize
        self.children=[]

        if mode=="block":
            # reserve one extra line above <nav id="toc">
            if isinstance(self.node,Element) and \
                self.node.tag=="nav" and \
                self.node.attributes.get("id")=="toc":
                    toc_header_h=VSTEP+4
                    self.y=self.y+toc_header_h

            previous=None
            for group in self.child_groups():
                next=BlockLayout(group,self,previous)
                self.children.append(next)
                previous=next

        else:
            self.cursor_x=0
            self.is_sup=False
            self.is_abbr=False
            self.is_pre=False

            self.new_line()

            for node in self.nodes:
                self.recurse(node)

            # if last line is empty,remove it
            if self.children and not self.children[-1].children:
                self.children.pop()

        # block/inline layout children together
        for child in self.children:
            child.layout()

        # block:children are BlockLayout; inline:children are LineLayout.
        # Keep the natural content height even when CSS fixes the visible box
        # height. That difference is the vertical layout overflow.
        self.content_height = (
            sum(child.height for child in self.children)
            + toc_header_h
        )
        self.height = self.content_height

        # if have toc_header_h,reset y
        self.y=old_y

        css_height=self.css_height()
        if css_height is not None:
            self.height=css_height

        if self.is_scrollable():
            stored_scroll = getattr(self.node, "scroll_y", 0)
            self.scroll = max(
                0,
                min(float(stored_scroll), self.max_scroll()),
            )
            self.node.scroll_y = self.scroll
        else:
            self.scroll = 0

    def flush(self):
        pass
        # self.flush_line()
        # self.cursor_x=0
        
        # for rel_x,word,font in self.line:
        #     x=self.x+rel_x
        #     y=self.y+baseline-font.metrics("ascent")
        #     self.display_list.append((x,y,word,font))

    def parse_px(self, value):
        if value=="auto":
            return None
        
        if isinstance(value, str) and value.endswith("px"):
            try:
                return int(value[:-2])
            except ValueError:
                return None
        
        return None
        
    def css_width(self):
        if not isinstance(self.node, Element):
            return None
        
        return self.parse_px(self.node.style.get("width","auto"))

    def css_height(self):
        if not isinstance(self.node, Element):
            return None

        return self.parse_px(self.node.style.get("height","auto"))

    def is_scrollable(self):
        return (
            isinstance(self.node, Element)
            and self.node.style.get("overflow", "visible") == "scroll"
            and self.css_height() is not None
        )

    def max_scroll(self):
        if not self.is_scrollable():
            return 0
        return max(0, self.content_height - self.height)

    def scroll_by(self, delta):
        if not self.is_scrollable():
            return False

        old_scroll = self.scroll
        self.scroll = max(
            0,
            min(self.scroll + delta, self.max_scroll()),
        )
        self.node.scroll_y = self.scroll
        return self.scroll != old_scroll

    # Convert CSS style into a Skia font.
    # CSS font-size conversion is centralized in css_font_size_to_skia().
    # font-style: normal    -> roman
    # font-style: italic    -> italic
    # font-weight: bold     -> bold
    def font_helper(self,node,family=None):
        weight = node.style["font-weight"]

        style=node.style["font-style"]
        if style=="normal":
            style="roman"

        size=css_font_size_to_skia(node.style.get("font-size", "16px"))

        if self.is_sup:
            size=max(1,int(size/2))

        if family is None:
            family=node.style["font-family"]

        return get_font(size,weight,style,family=family)

    def open_tag(self, tag):
        # already handled in HTMLParser
        # if tag == 'h1 class="title"':
        #     self.flush_line()
        #     self.alignment = "center"
        if tag == "sup":
            self.is_sup = True
        if tag == "pre":
            self.is_pre = True
            if self.children and self.children[-1].children:
                self.new_line()
        elif tag == "abbr":
            self.is_abbr = True
        elif tag == "p":
            if self.children and self.children[-1].children:
                self.new_line()


    def close_tag(self, tag):
        if tag == "sup":
            self.is_sup = False
        if tag == "pre":
            self.is_pre = False
            if self.children and self.children[-1].children:
                self.new_line()
        elif tag == "abbr":
            self.is_abbr = False
        elif tag == "p":
            if self.children and self.children[-1].children:
                self.new_line()

    def recurse(self,tree):
        if isinstance(tree,Text):
            if self.is_pre:
                self.pre_word(tree, tree.text)
            else:
                # normal mode
                for word in tree.text.split():
                    self.word(tree, word)
        
        else:
            # if is script tag,just skip not render that child nodes(it's js code)
            if tree.tag in ["script","style"]:
                return

            if tree.tag == "br":
                self.new_line()
                return

            # hidden inpu exists in DOM,but not render this node
            if is_hidden_input(tree):
                return

            if tree.tag == "input" or tree.tag == "button":
                self.input(tree)
                return

            self.open_tag(tree.tag)

            for child in tree.children:
                self.recurse(child)
            
            self.close_tag(tree.tag)
        
    def append_pre_text(self,node,text):
        line=self.children[-1]
        previous_word = line.children[-1] if line.children else None

        text_layout=TextLayout(
            node,
            text,
            line,
            previous_word,
            self.is_sup,
            False,
            0,
            "Courier New",
        )

        line.children.append(text_layout)

        font=self.font_helper(node,family="Courier New")
        self.cursor_x+=font.measureText(text)

    def pre_word(self,node,text):
        lines=text.split("\n")

        for i, line in enumerate(lines):
            # keep this line all content，include front whitespace，multi whitespace，tab
            if line:
                self.append_pre_text(node,line)

            else:
                # whitespace line must have height info，so append empty TextLayout
                if i!=len(lines)-1:
                    self.append_pre_text(node,"")
            
            # origin text have '\n' character，force to new line
            if i!=len(lines)-1:
                self.new_line()


    def new_line(self):
        self.cursor_x=0
        last_line=self.children[-1] if self.children else None
        new_line=LineLayout(self.node,self,last_line)
        self.children.append(new_line)
    
    def abbr_word(self,node,word):
        clean_word=word.replace("\xad","")
        
        if not clean_word:
            return

        normal_font=self.font_helper(node)
        space_w=normal_font.measureText(" ")

        pieces=[]
        total_width=0

        for char in clean_word:
            if char.islower():
                display_char=char.upper()
                is_small_caps=True

                weight="bold"

                style=node.style["font-style"]
                if style=="normal":
                    style="roman"

                size=css_font_size_to_skia(node.style.get("font-size", "16px"))

                if self.is_sup:
                    size=max(1,int(size/2))

                size=max(1,int(size*0.8))

                family =node.style["font-family"]
                font=get_font(size,weight,style,family=family)

            else:
                display_char=char
                is_small_caps=False
                font=self.font_helper(node)

            w=font.measureText(display_char)
            pieces.append((display_char,is_small_caps,w))
            total_width+=w

        if self.cursor_x+total_width > self.width and self.children[-1].children:
            self.new_line()

        for i,(display_char,is_small_caps,w) in enumerate(pieces):
            line=self.children[-1]
            previous_word=line.children[-1] if line.children else None

            if i==len(pieces)-1:
                space_after=space_w
            else:
                space_after=0

            text = TextLayout(
                node,
                display_char,
                line,
                previous_word,
                self.is_sup,
                is_small_caps,
                space_after,
            )

            line.children.append(text)

        self.cursor_x+=total_width+space_w
            

    def word(self,node,word):
        if self.is_abbr:
            self.abbr_word(node,word)
            return

        font=self.font_helper(node)
        clean_word=word.replace("\xad","")

        w=font.measureText(clean_word)
        space_w=font.measureText(" ")

        img=None
        if len(word)==1:
            img=get_emoji(word)
        
        if img:
            w=img.width()
            
            if self.cursor_x+w>self.width and self.children[-1].children:
                self.new_line()

            line=self.children[-1]
            previous=line.children[-1] if line.children else None

            emoji=EmojiLayout(node,img,line,previous,space_w)
            line.children.append(emoji)

            self.cursor_x+=w+space_w
            return
        
        if self.cursor_x+w > self.width and self.children[-1].children:
            self.new_line()

        line=self.children[-1]
        previous_word=line.children[-1] if line.children else None


        text=TextLayout(node,clean_word,line,previous_word,self.is_sup)
        line.children.append(text)

        self.cursor_x+=w+space_w

    def input(self,node):
        if node.tag=="button":
            css_width = self.parse_px(node.style.get("width","auto"))
            if css_width:
                w=css_width
            else:
                w = INPUT_WIDTH_PX
            
        elif is_checkbox_input(node):
            w = CHECKBOX_SIZE
        else:
            css_width = self.parse_px(node.style.get("width","auto"))
            if css_width:
                w = css_width
            else:
                w = INPUT_WIDTH_PX
        
        if self.cursor_x+w > self.width and self.children[-1].children:
            self.new_line()

        line = self.children[-1]
        previous_word = line.children[-1] if line.children else None

        if node.tag=="button":
            input_layout = ButtonLayout(node,line,previous_word)
        else:
            input_layout = InputLayout(node,line,previous_word)

        line.children.append(input_layout)

        weight = node.style["font-weight"]
        
        style = node.style["font-style"]
        if style=="normal":
            style="roman"

        size=css_font_size_to_skia(node.style.get("font-size", "16px"))
        family =node.style["font-family"]

        font = get_font(size,weight,style,family=family)

        self.cursor_x+=w+font.measureText(" ")
        

def lex(body):
    out=[]
    buffer=""
    in_tag = False

    for c in body:
        if c == "<":
            in_tag = True
            if buffer:
                decode_text=buffer.replace("&lt;","<").replace("&gt;",">")
                out.append(Text(decode_text))
                buffer=""
        elif c == ">":
            in_tag = False
            out.append(Tag(buffer))
            buffer=""
        else:
            buffer+=c

    if not in_tag and buffer:
        decode_text=buffer.replace("&lt;","<").replace("&gt;",">")
        out.append(Text(decode_text))
        
    return out

class Text:
    def __init__(self,text,parent):
        self.text=text
        self.children=[]
        self.parent=parent
        self.is_focused=False
    def __repr__(self):
        return repr(self.text)

class Element:
    def __init__(self,tag,attributes,parent):
        self.tag=tag
        self.attributes=attributes
        self.children=[]
        self.parent=parent
        self.is_focused=False

        # checkbox status
        # checkbox attribute only decide page just loading init status
        self.is_checked = "checked" in attributes
    def __repr__(self):
        # return "<"+self.tag+">"
        return "<"+self.tag+" "+str(self.attributes)+">"

class TagSelector:
    def __init__(self,tag):
        self.tag=tag
        self.priority=1

    def matches(self,node):
        return isinstance(node,Element) and node.tag==self.tag

class ClassSelector:
    def __init__(self, class_name):
        self.class_name=class_name

        # class selector must be have more priority than tag selector
        # tag selector priority is 1
        # class selector priority is 10
        self.priority=10

    def matches(self, node):
        if not isinstance(node, Element) :
            return False

        class_attr = node.attributes.get("class", "")
        classes = class_attr.split()

        return self.class_name in classes

class IdSelector:
    def __init__(self,id_name):
        self.id_name = id_name
        # id selector priority is higher than class selector
        # tag selector priority is 1
        # class selector priority is 10
        # id selector priority is 100
        self.priority = 100

    def matches(self,node):
        return (
            isinstance(node,Element)
            and node.attributes.get("id","") == self.id_name
        )

class SelectorSequence:
    def __init__(self,selectors):
        self.selectors = selectors
        self.priority = sum(selector.priority for selector in selectors)

    def matches(self,node):
        for selector in self.selectors:
            if not selector.matches(node):
                return False
        return True

class HasSelector:
    def __init__(self,selector):
        self.selector=selector
        
        # :has(...) become pseudo-class, give 10 priority
        # add internal selector priority
        self.priority=10+selector.priority

    def matches(self,node):
        if not isinstance(node,Element):
            return False

        return self.has_matching_descendant(node)

    def has_matching_descendant(self,node):
        for child in node.children:
            if self.selector.matches(child):
                return True
            
            if self.has_matching_descendant(child):
                return True

        return False

class VisitedSelector:
    def __init__(self):
        # pseduo-class selector has same priority as class selector 
        self.priority=10

    def matches(self,node):
        return (
            isinstance(node,Element) 
            and node.tag=="a"
            and getattr(node,"is_visited",False)
        )

class DescendantSelector:
    def __init__(self,selectors):
        self.selectors=selectors
        self.priority=sum(selector.priority for selector in selectors)

    def matches(self,node):
        # rightmost selector must match current node
        selector_index=len(self.selectors)-1

        if not self.selectors[selector_index].matches(node):
            return False

        # then leftward find ancestor selectors
        selector_index-=1
        current=node.parent

        while selector_index >= 0 and current:
            if self.selectors[selector_index].matches(current):
                selector_index-=1
                
            current=current.parent
        
        return selector_index < 0

def cascade_priority(rule):
    selector, body=rule
    return selector.priority

RUNTIME_JS = open("runtime.js").read()

# Chapter 12 APIs are layered on top of the existing runtime.js. Keeping this
# small bridge here lets the scheduling implementation travel with browser.py
# without replacing the rest of the project's DOM/event runtime.
SCHEDULING_RUNTIME_JS = r"""
SET_TIMEOUT_REQUESTS = {};

function setTimeout(callback, time_delta) {
    var handle = Object.keys(SET_TIMEOUT_REQUESTS).length;
    SET_TIMEOUT_REQUESTS[handle] = callback;
    call_python("setTimeout", handle, time_delta);
}

function runSetTimeout(handle) {
    var callback = SET_TIMEOUT_REQUESTS[handle];
    if (callback) callback();
}

RAF_LISTENERS = [];

function requestAnimationFrame(callback) {
    RAF_LISTENERS.push(callback);
    call_python("requestAnimationFrame");
}

function runRAFHandlers() {
    // Move this frame's callbacks out before running them. A callback that
    // requests another animation frame therefore lands in the fresh list and
    // cannot run recursively in the current frame.
    var handlers_copy = RAF_LISTENERS;
    RAF_LISTENERS = [];

    for (var i = 0; i < handlers_copy.length; i++) {
        handlers_copy[i]();
    }
}

XHR_REQUESTS = {};

XMLHttpRequest = function() {
    this.handle = Object.keys(XHR_REQUESTS).length;
    XHR_REQUESTS[this.handle] = this;
    this.is_async = false;
    this.method = "GET";
    this.url = "";
    this.responseText = "";
    this.onload = null;
};

XMLHttpRequest.prototype.open = function(method, url, is_async) {
    this.is_async = !!is_async;
    this.method = method;
    this.url = url;
};

XMLHttpRequest.prototype.send = function(body) {
    if (body === undefined) body = null;
    var out = call_python(
        "XMLHttpRequest_send",
        this.method, this.url, body, this.is_async, this.handle
    );
    if (!this.is_async) this.responseText = out;
    return out;
};

function runXHROnload(body, handle) {
    var obj = XHR_REQUESTS[handle];
    if (!obj) return;

    var evt = new Event("load");
    obj.responseText = body;
    if (obj.onload) obj.onload(evt);
}
"""

EVENT_DISPATCH_JS = \
    "dispatch_event_path(dukpy.type, dukpy.handles)"

class JSContext:
    def __init__(self,tab):
        self.tab = tab
        self.discarded = False

        self.node_to_handle = {}
        self.handle_to_node = {}

        self.interp=dukpy.JSInterpreter()
        self.interp.export_function("log",print)
        self.interp.export_function("querySelectorAll",self.querySelectorAll)
        self.interp.export_function("getAttribute",self.getAttribute)
        self.interp.export_function("setAttribute",self.setAttribute)
        self.interp.export_function("children",self.children)

        self.interp.export_function("createElement",self.createElement)
        self.interp.export_function("appendChild",self.appendChild)
        self.interp.export_function("insertBefore",self.insertBefore)
        self.interp.export_function("removeChild",self.removeChild)

        self.interp.export_function("innerHTML_get",self.innerHTML_get)
        self.interp.export_function("innerHTML_set",self.innerHTML_set)
        self.interp.export_function("outerHTML_get",self.outerHTML_get)

        self.interp.export_function("document_cookie_get",self.document_cookie_get)
        self.interp.export_function("document_cookie_set",self.document_cookie_set)

        self.interp.export_function("XMLHttpRequest_send",self.XMLHttpRequest_send)
        self.interp.export_function("setTimeout",self.setTimeout)
        self.interp.export_function(
            "requestAnimationFrame", self.requestAnimationFrame
        )


        self.evaljs(RUNTIME_JS)
        self.evaljs(SCHEDULING_RUNTIME_JS)

        self.update_id_globals()

    def evaljs(self, code, **kwargs):
        """Run JavaScript while recording its main-thread execution time."""
        self.tab.browser.measure.time("javascript")
        try:
            return self.interp.evaljs(code, **kwargs)
        finally:
            self.tab.browser.measure.stop("javascript")

    def get_handle(self,node):
        if node not in self.node_to_handle:
            handle = len(self.node_to_handle)
            self.node_to_handle[node]=handle
            self.handle_to_node[handle]=node

        else:
            handle = self.node_to_handle[node]

        return handle

    def update_id_globals(self):
        entries = []
        seen_ids = set()

        # only scan document root can reach nodes
        for node in tree_to_list(self.tab.nodes,[]):
            if not isinstance(node,Element):
                continue

            id_name = node.attributes.get("id","")
            
            if not id_name:
                continue

            # simplfity deal with duplicate id
            if id_name in seen_ids:
                continue

            seen_ids.add(id_name)
            
            entries.append([
                id_name,
                self.get_handle(node)
            ])

        self.evaljs(
            "sync_id_globals(dukpy.entries)",
            entries=entries
        )

    def document_cookie_get(self):
        host = getattr(self.tab.url,"host",None)

        if not host:
            return ""

        cookie_entry = get_valid_cookie(host)

        if cookie_entry is None:
            return ""

        cookie,params = cookie_entry

        # HttpOnly cookie is invisible to JS
        if "httponly" in params:
            return ""

        return serialize_cookie(cookie,params)

    def document_cookie_set(self,cookie_string):
        host = getattr(self.tab.url,"host",None)

        if not host:
            return

        old_cookie_entry = get_valid_cookie(host)

        if old_cookie_entry is not None:
            old_cookie,old_params=old_cookie_entry
        
            if "httponly" in old_params:
                return None

        cookie,params = parse_cookie_string(cookie_string)


        #ignore malformed cookie strings
        if not cookie or "=" not in cookie:
            return None

        # JS cannot create an HttpOnly cookie
        if "httponly" in params:
            return None

        if cookie_is_expired(params):
            COOKIE_JAR.pop(host,None)
            return None

        COOKIE_JAR[host]=(
            cookie,
            params
        )

        return None

    def dispatch_settimeout(self, handle):
        if self.discarded:
            return

        try:
            self.evaljs(SETTIMEOUT_JS, handle=handle)
        except dukpy.JSRuntimeError as e:
            print("setTimeout callback crashed", e)

    def setTimeout(self, handle, time_delta):
        def run_callback():
            if self.discarded:
                return
            task = Task(self.dispatch_settimeout, handle)
            self.tab.task_runner.schedule_task(task)

        timer = threading.Timer(max(0.0, float(time_delta)) / 1000.0, run_callback)
        timer.daemon = True
        timer.start()

    def requestAnimationFrame(self):
        if self.discarded:
            return

        # RAF does not execute JavaScript immediately. It only asks the active
        # browser window for one rendering frame; all callbacks requested before
        # that frame are coalesced and run together at the frame boundary.
        self.tab.browser.set_needs_animation_frame(self.tab)

    def dispatch_xhr_onload(self, out, handle):
        if self.discarded:
            return

        try:
            self.evaljs(XHR_ONLOAD_JS, out=out, handle=handle)
        except dukpy.JSRuntimeError as e:
            print("XMLHttpRequest onload crashed", e)

    def XMLHttpRequest_send(self, method, url, body, isasync=False, handle=None):
        # Capture the source-document state before a worker thread starts. A later
        # navigation may replace tab.url/referrer_policy while the request is live.
        source_url = self.tab.url
        referrer_policy = self.tab.referrer_policy
        full_url = source_url.resolve(url)

        if not self.tab.allowed_request(full_url):
            raise Exception("Cross-origin XHR blocked by CSP")

        page_origin = source_url.origin()
        is_cross_origin = full_url.origin() != page_origin
        request_origin = page_origin if is_cross_origin else None

        def perform_request():
            response_headers, out = full_url.request(
                source_url,
                body,
                origin=request_origin,
                referrer_policy=referrer_policy,
            )

            if is_cross_origin:
                allowed_origin = response_headers.get(
                    "access-control-allow-origin"
                )
                if allowed_origin not in [page_origin, "*"]:
                    raise Exception("Cross-origin XHR blocked by CORS")

            return out

        if not isasync:
            return perform_request()

        def run_load():
            try:
                out = perform_request()
            except Exception as e:
                print("Async XMLHttpRequest failed", e)
                return

            if self.discarded:
                return
            task = Task(self.dispatch_xhr_onload, out, handle)
            self.tab.task_runner.schedule_task(task)

        thread = threading.Thread(target=run_load)
        thread.daemon = True
        thread.start()
        return None

    def querySelectorAll(self,selector_text):
        selector = CSSParser(selector_text).selector()

        nodes = [
            node for node in tree_to_list(self.tab.nodes,[])
            if selector.matches(node)
        ]

        return [self.get_handle(node) for node in nodes]

    def getAttribute(self,handle,name):
        elt = self.handle_to_node[handle]
        value = elt.attributes.get(name,None)
        return value if value else ""

    def setAttribute(self,handle,name,value):
        elt = self.handle_to_node[handle]
        
        if not isinstance(elt,Element):
            raise Exception(
                "setAttribute can only be used on an Element"
            )

        name=str(name).casefold()
        value=str(value)

        elt.attributes[name]=value

        # after changed id，js global id variable must sync
        if name=="id":
            self.update_id_globals()

        self.tab.set_needs_render()

    def serialize_attributes(self,element):
        output = []
        
        for name,value in element.attributes.items():
            if value is None:
                value = ""

            escaped_value = escape(
                str(value),
                quote=True
            )

            output.append(
                ' {}="{}"'.format(
                    name,
                    escaped_value
                )
            )

        return "".join(output)

    def serialize_node(self,node):
        # text node only output text,but must escape HTML speical characters
        if isinstance(node,Text):
            return escape(
                node.text,
                quote=False
            )

        if not isinstance(node,Element):
            return ""

        attributes = self.serialize_attributes(node)

        opening_tag = "<{}{}>".format(
            node.tag,
            attributes
        )

        # void element don't have closing tag
        if node.tag in VOID_ELEMENTS:
            return opening_tag

        children_html = "".join(
            self.serialize_node(child)
            for child in node.children
        )

        closing_tag = "</{}>".format(node.tag)
        
        return (
            opening_tag
            + children_html
            + closing_tag
        )

    def innerHTML_get(self,handle):
        node = self.handle_to_node[handle]
        
        # innerHTML only serialize children
        return "".join(
            self.serialize_node(child)
            for child in node.children
        )

    def outerHTML_get(self,handle):
        node = self.handle_to_node[handle]

        # outerHTML serialize children node and itself
        return self.serialize_node(node)

    def children(self,handle):
        node = self.handle_to_node[handle]
        
        element_children = [
            child for child in node.children
            if isinstance(child,Element)
        ]

        return [self.get_handle(child) for child in element_children]

    def createElement(self,tag_name):
        tag_name = str(tag_name).casefold()
        
        # build not connected DOM tree's element yet
        node = Element(tag_name,{},None)

        return self.get_handle(node)

    def detach_node(self,node):
        old_parent = node.parent
        
        if old_parent is not None and node in old_parent.children:
            old_parent.children.remove(node)

        node.parent = None

    def check_insert_cycle(self,parent,child):
        current = parent 

        while current is not None:
            if current is child:
                raise Exception("Cannot insert a node into itself or its descendant")

            current = current.parent

    def appendChild(self,parent_handle,child_handle):
        parent = self.handle_to_node[parent_handle]
        child = self.handle_to_node[child_handle]
        
        self.check_insert_cycle(parent,child)
        
        # if child already in other place，remove from old parent
        self.detach_node(child)

        child.parent = parent # reconnect new parent
        parent.children.append(child) # append new child
        
        self.update_id_globals() # after subtree connect into document, maybe have add id globals
        self.tab.set_needs_render()

        return child_handle

    def insertBefore(self,parent_handle,new_child_handle,reference_child_handle):
        parent = self.handle_to_node[parent_handle]
        new_child = self.handle_to_node[new_child_handle]

        # insertBefore(parent,new_child,null) equal to appendChild(new_child)
        if reference_child_handle is None:
            return self.appendChild(parent_handle,new_child_handle)

        reference_child = self.handle_to_node[reference_child_handle]

        if reference_child.parent is not parent:
            raise Exception("Reference child is not a child of parent")

        # if new_child is reference_child, do nothing
        if new_child is reference_child:
            return new_child_handle

        self.check_insert_cycle(parent,new_child)

        # remove from origin postition
        self.detach_node(new_child)

        # after remove，restart find reference_child postition
        index = parent.children.index(reference_child)

        new_child.parent = parent
        parent.children.insert(index,new_child)

        self.update_id_globals() # after subtree connect into document, maybe have add id globals
        self.tab.set_needs_render()

        return new_child_handle

    def removeChild(self,parent_handle,child_handle):
        parent = self.handle_to_node[parent_handle]
        child = self.handle_to_node[child_handle]

        # only remove direct child node itself
        if child.parent is not parent or child not in parent.children:
            raise Exception("Node is not a child of this parent")

        # from python DOM tree unlock connect
        self.detach_node(child)

        self.update_id_globals() # after subtree remove from document, maybe have remove id globals

        # after DOM changed，render it
        self.tab.set_needs_render()

        # return removed child
        return child_handle
        

    def innerHTML_set(self,handle,s):
        doc = HTMLParser("<html><body>"+s+"</body></html>").parse()

        body = self.find_body(doc)
        if body:
            new_nodes = body.children
        else:
            new_nodes = []

        elt = self.handle_to_node[handle]

        # old children become detaced subtree roots
        for old_child in elt.children:
            old_child.parent = None

        
        elt.children = new_nodes

        for child in elt.children:
            child.parent = elt

        # print("--- after innerHTML_set full DOM Tree ---")
        # print_tree(self.tab.nodes)
        # print("---------------------------------------")

        # delete old id globals，add new id globals
        self.update_id_globals()
        self.tab.set_needs_render()

    def dispatch_event(self,type,elt):
        handles =[] 
        
        current = elt

        # from target start，up parent to document root
        while current is not None:
            if isinstance(current,Element):
                handles.append(self.get_handle(current))

            current = current.parent

        if not handles:
            return False

        try:
            do_default = self.evaljs(
                EVENT_DISPATCH_JS,
                type=type,
                handles=handles
            )

            return not do_default

        except dukpy.JSRuntimeError as e:
            print("Event",type,"crashed",e)
            return False

    def run(self,script,code):
        if self.discarded:
            return None

        try:
            return self.evaljs(code)
        except dukpy.JSRuntimeError as e:
            print("Script",script,"crashed",e)

    def find_body(self,node):
        body = None
        
        for n in tree_to_list(node,[]):
            if isinstance(n,Element) and n.tag=="body":
                body = n

        return body
            
    
class ChromeLayoutParent:
    def __init__(self,width):
        self.x=0
        self.y=0
        self.width = width

class Chrome:
    def __init__(self,browser):
        self.browser=browser

        self.focus=None
        self.address_bar = ""
        self.address_bar_cursor=0
        self.address_bar_dirty = False

        self.nodes = None
        self.document = None
        self.display_list = []

        # When HTTPS is active, render() reserves a slot immediately before
        # the address bar and stores the lock icon geometry here.
        self.security_icon_rect = None

        # init height，new_tab build a Tab will use it
        self.bottom = 80

        self.render()


    def clamp_address_bar_cursor(self):
        self.address_bar_cursor=max(
            0,
            min(self.address_bar_cursor,len(self.address_bar))
        )

    def blur_address_bar(self):
        self.focus=None

    def discard_address_bar_edit(self):
        self.focus = None
        self.address_bar = ""
        self.address_bar_cursor = 0
        self.address_bar_dirty = False

    def address_bar_display_text(self):
        if self.focus == "address bar" or self.address_bar_dirty:
            return self.address_bar
        
        url = self.browser.active_url_string()
        if url:
            return url
        
        return ""

    # convert mouse x coordinate to string index
    def cursor_index_from_x(self,x,input_layout):
        text = self.address_bar

        local_x = x-input_layout.x

        if local_x <= 0:
            return 0

        font = input_layout.font

        for i in range(len(self.address_bar)):
            left=font.measureText(self.address_bar[:i])
            right=font.measureText(self.address_bar[:i+1])
            mid=(left+right)/2

            if local_x < mid:
                return i

        return len(self.address_bar)

    def paint(self):
        return self.display_list

    
    def layout_object_at(self,x,y):
        # Chrome does not normally contain scroll containers, but sharing the
        # transform-aware walker keeps hit testing consistent with page content.
        cmd = hit_test_paint_commands(self.display_list, x, y)
        if cmd is None:
            return None

        print("hit display command:", type(cmd).__name__)
        print("generated by layout object:", type(cmd.layout_object).__name__)
        return cmd.layout_object

    def touch_layout_object_at(self, x, y, radius=TOUCH_RADIUS_PX):
        cmd = touch_hit_test_paint_commands(
            self.display_list, x, y, radius
        )
        if cmd is None:
            return None
        return cmd.layout_object

    def ancestor(self,elt,tag):
        while elt:
            if isinstance(elt,Element) and elt.tag==tag:
                return elt
            elt=elt.parent
        
        return None


    def click(self,x,y,touch_radius=None):

        was_address_bar_focused = self.focus=="address bar"

        if touch_radius is None:
            obj = self.layout_object_at(x,y)
        else:
            obj = self.touch_layout_object_at(x,y,touch_radius)

        #click any chrome section，default is clear first
        self.focus=None

        if obj is None:
            self.discard_address_bar_edit()
            return

        elt = obj.node

        button = self.ancestor(elt,"button")

        if button:
            button_id = button.attributes.get("id")

            if button_id == "new-tab":
                self.discard_address_bar_edit()
                self.browser.new_tab(URL("https://browser.engineering/"))
                return

            elif button_id == "back":
                self.discard_address_bar_edit()
                self.browser.schedule_go_back()
                return

            elif button_id == "forward":
                self.discard_address_bar_edit()
                self.browser.schedule_go_forward()
                return

            elif button_id == "bookmark":
                self.discard_address_bar_edit()
                self.browser.toggle_bookmark()
                return

        if isinstance(elt,Element) and elt.tag == "input" and elt.attributes.get("id") == "address":
            self.focus = "address bar"
            
            if not was_address_bar_focused and not self.address_bar_dirty:
                url = self.browser.active_url_string()
                self.address_bar = url or ""

            self.address_bar_cursor = self.cursor_index_from_x(x,obj)
            self.clamp_address_bar_cursor()
            return

        link =self.ancestor(elt,"a")
        
        if link:
            href = link.attributes.get("href","")

            if href.startswith("tab-"):
                try:
                    index = int(href[len("tab-"):])
                except ValueError:
                    return

                tabs = self.browser.tabs_snapshot()
                if 0 <= index < len(tabs):
                    self.discard_address_bar_edit()
                    self.browser.set_active_tab(tabs[index])
                    return

        self.discard_address_bar_edit()


    def is_url_like(self,text):
        return(
            "://" in text
            or text.startswith("about:")
            or text.startswith("data:")
            or text.startswith("file:")
            or text.startswith("view-source:")
            or text.startswith("mailto:")
        )

    def address_bar_to_url(self,text):
        text=text.strip()

        if self.is_url_like(text):
            return URL(text)

        query=quote_plus(text)
        return URL("https://html.duckduckgo.com/html/?q="+query)

    def chrome_html(self):
        tabs_html = ""

        active_tab = self.browser.active_tab_snapshot()
        for i,tab in enumerate(self.browser.tabs_snapshot()):
            if tab == active_tab:
                style="font-weight:bold;color:black"
                label ="[Tab {}]".format(i)

            else:
                style = "color:blue"
                label = "Tab {}".format(i)

            tabs_html += (
                "<a href='tab-{}' style='{}'>{}</a> "
                .format(i,style,label)
            )

        if self.browser.is_current_page_bookmarked():
            bookmark_bg="yellow"
        else:
            bookmark_bg="white"

        secure = self.browser.active_is_secure()
        
        icon_space = (SECURITY_ICON_SLOT if secure else 0)

        address_width = max(100,self.browser.width-150-icon_space)

        out = "<!doctype html>"
        out += "<html>"
        out += "<body>"
        out += "<div style='background-color:lightgray;width:{}px'>".format(self.browser.width)

        # first layer: new tab button + tab links 
        out += "<button id=new-tab style='width:30px'></button> "
        out += tabs_html

        # second layer: back/ forward / bookmark / url address input
        out += "<br>"
        out += "<button id=back style='width:45px'></button> "
        out += "<button id=forward style='width:45px'></button> "
        out += "<button id=bookmark style='width:30px;background-color:{}'></button> ".format(bookmark_bg)
        out += "<input id=address style='width:{}px;background-color:white'>".format(address_width)

        out += "</div>"
        out += "</body>"
        out += "</html>"

        return out

    def find_button_layout(self, button_id):
        if self.document is None:
            return None

        for obj in tree_to_list(self.document, []):
            if not isinstance(obj, ButtonLayout):
                continue

            node = getattr(obj, "node", None)
            if (
                isinstance(node, Element)
                and node.tag == "button"
                and node.attributes.get("id") == button_id
            ):
                return obj

        return None

    def build_chrome_icons(self):
        """Create one unified Skia vector-icon display list for Chrome UI."""
        icons = []

        new_tab_layout = self.find_button_layout("new-tab")
        if new_tab_layout:
            icons.append(DrawVectorIcon(
                centered_icon_rect(new_tab_layout.self_rect(), 12),
                "plus",
                color=ICON_COLOR_ENABLED,
            ))

        back_layout = self.find_button_layout("back")
        if back_layout:
            back_enabled = self.browser.active_can_go_back()
            icons.append(DrawVectorIcon(
                centered_icon_rect(back_layout.self_rect(), 14),
                "back",
                color=(
                    ICON_COLOR_ENABLED
                    if back_enabled
                    else ICON_COLOR_DISABLED
                ),
            ))

        forward_layout = self.find_button_layout("forward")
        if forward_layout:
            forward_enabled = self.browser.active_can_go_forward()
            icons.append(DrawVectorIcon(
                centered_icon_rect(forward_layout.self_rect(), 14),
                "forward",
                color=(
                    ICON_COLOR_ENABLED
                    if forward_enabled
                    else ICON_COLOR_DISABLED
                ),
            ))

        bookmark_layout = self.find_button_layout("bookmark")
        if bookmark_layout:
            bookmarked = self.browser.is_current_page_bookmarked()
            icons.append(DrawVectorIcon(
                centered_icon_rect(bookmark_layout.self_rect(), 14),
                "star",
                color=(
                    ICON_COLOR_ACTIVE
                    if bookmarked
                    else ICON_COLOR_ENABLED
                ),
                fill=bookmarked,
            ))

        if self.security_icon_rect is not None:
            icons.append(DrawVectorIcon(
                self.security_icon_rect,
                "lock",
                color=ICON_COLOR_ENABLED,
                stroke_width=1.8,
            ))

        return icons

    def find_address_layout(self,address_node):
        if self.document is None:
            return None

        for obj in tree_to_list(self.document,[]):
            if (isinstance(obj,InputLayout) and obj.node is address_node):
                return obj

        return None

    def find_address_node(self):
        if not self.nodes:
            return None

        for node in tree_to_list(self.nodes,[]):
            if (
                isinstance(node,Element)
                and node.tag =="input"
                and node.attributes.get("id")=="address"
            ):
                return node

        return None

    def render(self):
        html = self.chrome_html()
        
        self.nodes = HTMLParser(html).parse()

        rules = DEFAULT_STYLE_SHEET.copy()
        style(self.nodes,rules)

        address_node = self.find_address_node()

        if address_node:
            address_node.attributes["value"] = self.address_bar_display_text()

            if self.focus == "address bar":
                address_node.is_focused = True
                address_node.cursor_index = self.address_bar_cursor
            else:
                address_node.is_focused = False

        parent = ChromeLayoutParent(self.browser.width)

        self.document = BlockLayout([self.nodes],parent,None)
        self.document.layout()

        # Reserve a lock-icon slot before the address field. The lock itself
        # is rendered later by the same DrawVectorIcon system as every other
        # Chrome icon.
        self.security_icon_rect = None

        if (
            self.browser.active_is_secure()
            and address_node
        ):
            address_layout = self.find_address_layout(address_node)

            if address_layout:
                slot_x = address_layout.x
                slot_y = address_layout.y
                slot_height = address_layout.height

                address_layout.x += SECURITY_ICON_SLOT

                icon_size = 14
                icon_cx = slot_x + SECURITY_ICON_SLOT / 2
                icon_cy = slot_y + slot_height / 2
                half = icon_size / 2
                self.security_icon_rect = skia.Rect.MakeLTRB(
                    icon_cx - half,
                    icon_cy - half,
                    icon_cx + half,
                    icon_cy + half,
                )

        # Paint the HTML/layout-defined Chrome controls first. Their empty
        # button boxes remain responsible for layout and hit testing.
        self.display_list = []
        paint_tree(self.document,self.display_list)

        # Paint semantic vector icons as a visual overlay. These commands do
        # not receive layout_object, so hit testing falls through to the
        # underlying ButtonLayout instead of treating the icon as a widget.
        self.display_list.extend(self.build_chrome_icons())

        self.bottom = self.document.height + 2
        
    
    def keypress(self,char):
        if self.focus=="address bar":
            self.clamp_address_bar_cursor()

            i=self.address_bar_cursor
            self.address_bar =(
                self.address_bar[:i]
                + char
                + self.address_bar[i:]
            )

            self.address_bar_cursor += len(char)
            self.address_bar_dirty = True
            return True

        return False

    def enter(self):
        if self.focus=="address bar":
            url=self.address_bar_to_url(self.address_bar)

            if url.is_external():
                url.open_external()
                self.discard_address_bar_edit()
                return True

            if self.browser.active_tab_snapshot() is not None:
                self.browser.schedule_load(url)
            else:
                self.browser.new_tab(url)

            self.discard_address_bar_edit()
            return True

        return False

    def backspace(self):
        if self.focus=="address bar":
            self.clamp_address_bar_cursor()

            if self.address_bar_cursor==0:
                return
                
            i = self.address_bar_cursor
            self.address_bar = (
                self.address_bar[:i-1]
                +self.address_bar[i:]
            )

            self.address_bar_cursor -= 1
            self.address_bar_dirty = True

    def left(self):
        if self.focus=="address bar":
            self.address_bar_cursor = max(0,self.address_bar_cursor-1)

    def right(self):
        if self.focus=="address bar":
            self.address_bar_cursor = min(
                len(self.address_bar),
                self.address_bar_cursor+1
            )
        


class CommitData:
    """Rendering snapshot transferred from a Tab Main Thread to BrowserWindow."""
    __slots__ = (
        "url",
        "scroll",
        "height",
        "display_list",
        "title",
        "secure",
        "can_go_back",
        "can_go_forward",
        "width",
        "tab_height",
    )

    def __init__(
        self,
        url,
        scroll,
        height,
        display_list,
        title="Tai Gar",
        secure=False,
        can_go_back=False,
        can_go_forward=False,
        width=0,
        tab_height=0,
    ):
        # Everything needed by the Browser Thread is captured before commit().
        # display_list is intentionally not copied: ownership moves across the
        # commit boundary, and the Tab drops its reference after a successful send.
        self.url = str(url) if url is not None else None
        self.scroll = float(scroll)
        self.height = max(0.0, float(height))
        self.display_list = display_list
        self.title = str(title)
        self.secure = bool(secure)
        self.can_go_back = bool(can_go_back)
        self.can_go_forward = bool(can_go_forward)
        self.width = int(width)
        self.tab_height = int(tab_height)

    @property
    def url_string(self):
        return self.url

    @property
    def document_height(self):
        return self.height


class Tab:
    def __init__(self,browser,width,tab_height,visited_urls,bookmarks):
        self.browser = browser
        self.width=width
        self.height=tab_height
        self.tab_height=tab_height

        self.display_list = []
        self.display_list_needs_commit = False
        self.scroll = 0
        self.url=None
        self.nodes=None
        self.document=None
        self.secure = False

        #current page Referrer-Policy
        self.referrer_policy = None

        self.focus=None

        # DOM element that currently owns keyboard overflow scrolling. Keeping
        # the DOM node (not a layout object) survives relayout/repaint rebuilds.
        self.scroll_focus = None

        self.visited_urls = visited_urls
        self.bookmarks = bookmarks
        self.rules=[]
        
        self.history=[]
        self.history_index=-1

        self.task_runner = TaskRunner(self)
        self.needs_render = False
        self.js = None
        self.pending_fragment = None

    def set_needs_render(self):
        self.needs_render = True
        self.browser.set_needs_animation_frame(self)

    def discard(self):
        if self.js is not None:
            self.js.discarded = True
        self.task_runner.clear_tasks()

    def is_internal_page(self,url):
        return url.scheme=="about" and url.path=="bookmarks"

    def request_internal_page(self,url):
        if url.path=="bookmarks":
            return self.bookmarks_page()
        
        return ""

    def certificate_error_page(self,url,error):
        out = []
        
        out.append("<!doctype html>")
        out.append("<html>")
        out.append("<head><title>Certificate Error</title></head>")
        out.append("<body>")

        out.append("<h1>Certificate Error</h1>")
        out.append(
            "<p>Warning: this HTTPS page has an invalid certificate.</p>"
        )

        out.append(
            "<p>URL: {}</p>".format(
                escape(str(url))
            )
        )

        out.append(
            "<pre>{}</pre>".format(
                escape(str(error))
            )
        )

        out.append("</body>")
        out.append("</html>")

        return "\n".join(out)
        
    def bookmarks_page(self):
        out=[]
        out.append("<html>")
        out.append("<head><title>Bookmarks</title></head>")
        out.append("<body>")
        out.append("<h1>Bookmarks</h1>")

        if not self.bookmarks:
            out.append("<p>No bookmarks yet.</p>")
        else:
            out.append("<ul>")
            
            for url in sorted(self.bookmarks):
                safe_url=escape(url,quote=True)
                out.append(f'<li><a href="{safe_url}">{safe_url}</a></li>')

            out.append("</ul>")

        out.append("</body>")
        out.append("</html>")

        return "\n".join(out)

    def navigate(self, url, payload=None, add_to_history=True):
        """Navigate from inside the Tab main thread and invalidate older queued work."""
        self.task_runner.clear_pending_tasks()
        return self.load(url, payload, add_to_history)

    def load(self, url,payload=None,add_to_history=True):
        if self.js is not None:
            self.js.discarded = True
        self.pending_fragment = None

        referrer=self.url #old url
        referrer_policy=self.referrer_policy #old url referrer policy
        
        self.url=url # new url to come
        self.scroll=0

        # every navigation starts as unverified
        self.secure = False

        self.visited_urls.add(str(url))

        if add_to_history:
            # if current page in the history，not last page
            #　represent user click back button
            # if click new link or enter new URL
            # forward history will be clear
            if self.history_index < len(self.history)-1:
                self.history=self.history[:self.history_index+1]

            self.history.append(url)
            self.history_index+=1

        

        if self.is_internal_page(url): # bookmarks page
            headers = {}
            body = self.request_internal_page(url)
            self.nodes=HTMLParser(body).parse()
        
        else: # normal web page
            try:
                # request with referrer and referrer policy
                headers,body = url.request(referrer,payload,referrer_policy=referrer_policy)

                # request finished without certificate error
                self.secure=(url.scheme=="https")

            except ssl.SSLCertVerificationError as e:
                headers = {}

                body = self.certificate_error_page(
                    url,
                    e
                )

                self.secure = False

            if url.view_source:
                # execute syntax highlight: make raw html turn into highlighted html
                highlighted_body=ViewSourceParser(body).handle_view_source()
                # after highlight html feed standard Parser make DOM tree
                self.nodes=HTMLParser(highlighted_body).parse()
            
            else:
                self.nodes=HTMLParser(body).parse()


        # read new page referrer-policy
        self.referrer_policy = normalize_referrer_policy(headers)

        #CSP(content-security-policy)
        self.allowed_origins = None

        if "content-security-policy" in headers:
            csp = headers[
                "content-security-policy"
            ].split()

            if len(csp) > 0 and csp[0]=="default-src":
                self.allowed_origins = []

                for origin in csp[1:]:
                    self.allowed_origins.append(
                        URL(origin).origin()
                    )
            
        scripts = [
            node.attributes["src"]
            for node in tree_to_list(self.nodes,[])
            if isinstance(node,Element)
            and node.tag=="script"
            and "src" in node.attributes
        ]

        self.js = JSContext(self)
        
        for script in scripts:
            script_url = url.resolve(script)

            if script_url is None:
                continue

            if not self.allowed_request(script_url):
                print(
                    "Blocked script",
                    script,
                    "du to CSP"
                )
                continue

            try:
                headers, body = script_url.request(url,referrer_policy=self.referrer_policy)
            except Exception:
                continue

            task = Task(self.js.run, script, body)
            self.task_runner.schedule_task(task)


        rules=DEFAULT_STYLE_SHEET.copy()

        links = [node.attributes["href"]
                for node in tree_to_list(self.nodes,[])
                if isinstance(node,Element)
                and node.tag=="link"
                and node.attributes.get("rel")=="stylesheet"
                and "href" in node.attributes]

        for link in links:
            style_url=url.resolve(link)

            if style_url is None:
                continue

            if not self.allowed_request(style_url):
                print(
                    "Blocked style",
                    link,
                    "due to SCP"
                )
                continue

            try:
                headers, body = style_url.request(url,referrer_policy=self.referrer_policy)
            except Exception:
                continue
                
            rules.extend(CSSParser(body).parse())

        # deal with <style>..</style> inline stylesheet
        style_nodes = [node
                      for node in tree_to_list(self.nodes,[])
                      if isinstance(node,Element)
                      and node.tag=="style"]

        for style_node in style_nodes:
            css_text=style_tag_text(style_node)
            rules.extend(CSSParser(css_text).parse())


        self.rules=sorted(rules,key=cascade_priority)

        self.focus=None
        self.pending_fragment = self.url.fragment or None
        self.set_needs_render()

        # URL/history/security become Browser-Thread-visible at the next commit.

        # self.document=DocumentLayout(self.nodes)
        # self.document.layout()

        # self.display_list=[]
        # paint_tree(self.document,self.display_list)
        # self.draw()

    def allowed_request(self,url):
        return (
            self.allowed_origins is None
            or url.origin() in self.allowed_origins
        )

    def can_go_back(self):
        return self.history_index > 0

    def can_go_forward(self):
        return self.history_index < len(self.history)-1

    def go_back(self):
        if self.can_go_back():
            self.history_index-=1
            self.navigate(self.history[self.history_index],add_to_history=False)

        
    def go_forward(self):
        if self.can_go_forward():
            self.history_index+=1
            self.navigate(self.history[self.history_index],add_to_history=False)

    def get_title(self):
        if not self.nodes:
            return "Tai Gar"

        for node in tree_to_list(self.nodes,[]):
            if isinstance(node,Element) and node.tag=="title":
                title=style_tag_text(node).strip()

                if title:
                    return title

        return "Tai Gar"

    def mark_visited_links(self):
        if not self.nodes or not self.url:
            return

        for node in tree_to_list(self.nodes,[]):
            if not isinstance(node,Element):
                continue

            node.is_visited=False

            if node.tag != "a":
                continue

            if "href" not in node.attributes:
                continue

            try:
                link_url=self.url.resolve(node.attributes["href"])
            except Exception:
                continue

            if link_url is None:
                continue

            if str(link_url) in self.visited_urls:
                node.is_visited=True

    def restyle(self):
        if not self.nodes:
            return

        self.mark_visited_links()
        style(self.nodes,self.rules)

    def blur(self):
        # Keyboard scrolling focus is independent of text-input focus.
        self.scroll_focus = None

        if not self.focus:
            return

        self.focus.is_focused = False # Text or Element disable focused
        self.focus= None
        self.set_needs_render()

    def run_animation_frame(self):
        """Run one frame on the Tab Main Thread and commit its rendering snapshot."""
        # RAF belongs to the frame boundary. render() is intentionally reusable by
        # hit testing and other synchronous Main-Thread paths and therefore does
        # not commit by itself.
        if self.js is not None and not self.js.discarded:
            try:
                self.js.evaljs(RAF_JS)
            except dukpy.JSRuntimeError as e:
                print("requestAnimationFrame callback crashed", e)

        self.render()

        document_height = (
            max(1.0, float(self.document.height + 2 * VSTEP))
            if self.document is not None
            else 0.0
        )

        # Only a display list produced by render/relayout crosses the ownership
        # boundary. A scroll-only frame sends None so BrowserWindow can reuse the
        # previously committed display list.
        committed_display_list = (
            self.display_list
            if self.display_list_needs_commit
            else None
        )

        data = CommitData(
            self.url,
            self.scroll,
            document_height,
            committed_display_list,
            title=self.get_title(),
            secure=self.secure,
            can_go_back=self.can_go_back(),
            can_go_forward=self.can_go_forward(),
            width=self.width,
            tab_height=self.tab_height,
        )

        accepted = self.browser.commit(self, data)

        if accepted and committed_display_list is not None:
            # Ownership moved only after BrowserWindow accepted this snapshot.
            # If the window rejects a commit (for example during shutdown), keep
            # the local list so it is not silently lost.
            self.display_list = None
            self.display_list_needs_commit = False

        return accepted

    def render(self):
        """Update style, layout, and paint when the Tab is dirty; never commit."""
        if not self.needs_render:
            return False

        self.browser.measure.time("render")
        try:
            self.restyle()
            self.relayout()
            self.needs_render = False

            if self.pending_fragment:
                fragment = self.pending_fragment
                self.pending_fragment = None
                self.scroll_to_fragment(fragment)

            return True
        finally:
            self.browser.measure.stop("render")

    def relayout(self):
        self.document=DocumentLayout(self.nodes,self.width)
        self.document.layout()

        self.display_list=[]
        paint_tree(self.document,self.display_list)
        self.display_list_needs_commit = True

    def scroll_to_fragment(self,fragment):
        if not fragment or not self.document or not self.nodes:
            return

        target = None
        for node in tree_to_list(self.nodes, []):
            if (
                isinstance(node, Element)
                and node.attributes.get("id") == fragment
            ):
                target = node
                break

        if target is None:
            return

        candidate_y = []

        for obj in tree_to_list(self.document, []):
            if getattr(obj, "y", None) is None:
                continue

            node = getattr(obj, "node", None)

            if node is target:
                candidate_y.append(obj.y)
                continue

            nodes = getattr(obj, "nodes", None)
            if nodes and target in nodes:
                candidate_y.append(obj.y)
                continue

            ancestor = node
            while ancestor is not None:
                if ancestor is target:
                    candidate_y.append(obj.y)
                    break
                ancestor = getattr(ancestor, "parent", None)

        if not candidate_y:
            return

        target_y = min(candidate_y)
        max_y = max(
            self.document.height + 2 * VSTEP - self.tab_height,
            0,
        )
        self.scroll = max(0, min(target_y, max_y))

    def navigate_to_fragment(self,fragment,add_to_history=True):
        url=self.url.with_fragment(fragment)
        self.url=url

        self.visited_urls.add(str(url))

        if add_to_history:
            if self.history_index < len(self.history)-1:
                self.history=self.history[:self.history_index+1]

            self.history.append(url)
            self.history_index+=1

        self.pending_fragment = fragment
        self.set_needs_render()


    def raster(self, canvas, interest_rect=None):
        """Raster page commands in document coordinates.

        BrowserWindow owns the document->surface transform. When an interest
        rectangle is supplied, skip top-level display-list items whose visual
        bounds do not overlap that cached document region. Skia clipRect remains
        the final pixel-level guard against drawing outside the surface.
        """
        for item in self.display_list:
            if interest_rect is not None and hasattr(item, "rect"):
                if (
                    item.rect.bottom() <= interest_rect.top()
                    or item.rect.top() >= interest_rect.bottom()
                ):
                    continue

            item.execute(canvas)

    def scrolldown(self):
        if self.document is None:
            return
        max_y=max(self.document.height+2*VSTEP-self.tab_height,0)
        self.scroll=min(self.scroll+SCROLL_STEP,max_y)

    def scrollup(self):
        self.scroll-=SCROLL_STEP
        if self.scroll<0:
            self.scroll=0

    def mousewheel(self,delta):
        if delta > 0:
            self.scrollup()
        elif delta < 0:
            self.scrolldown()

    def scroll_by(self, delta):
        """Main-thread scroll handler used by browser-thread input tasks."""
        if self.document is None:
            return False

        handled, changed = self.scroll_focused_element(delta)
        if handled:
            return changed

        old_scroll = self.scroll
        max_y = max(self.document.height + 2 * VSTEP - self.tab_height, 0)
        self.scroll = max(0, min(self.scroll + delta, max_y))

        if self.scroll != old_scroll:
            # Scroll state crosses threads only at a frame boundary. No new display
            # list is needed for ordinary page scrolling.
            self.browser.set_needs_animation_frame(self)
            return True

        return False


    def ensure_display_list_for_hit_test(self):
        """Return a Main-Thread-local paint list without crossing the commit boundary."""
        self.render()

        if self.display_list is None and self.document is not None:
            self.display_list = []
            paint_tree(self.document, self.display_list)
            # This list was rebuilt only for hit testing. It does not represent new
            # page state and therefore should not force a Browser-Thread reraster.
            self.display_list_needs_commit = False

        return self.display_list or []

    def layout_object_at(self,x,y):
        display_list = self.ensure_display_list_for_hit_test()

        # Viewport -> document coordinates. Nested Scroll nodes then convert the
        # point into each scrolled child coordinate space as the tree is walked.
        document_y = y + self.scroll
        cmd = hit_test_paint_commands(display_list, x, document_y)

        if cmd is None:
            return None

        print("hit display command:", type(cmd).__name__)
        print("generated by layout object:", type(cmd.layout_object).__name__)
        return cmd.layout_object

    def touch_layout_object_at(self, x, y, radius=TOUCH_RADIUS_PX):
        display_list = self.ensure_display_list_for_hit_test()

        # The finger area starts in the page viewport coordinate system. Convert
        # only its center by page scroll; nested Scroll nodes perform the remaining
        # element-scroll transforms recursively. Radius is unchanged by translation.
        document_y = y + self.scroll
        cmd = touch_hit_test_paint_commands(
            display_list, x, document_y, radius
        )
        if cmd is None:
            return None
        return cmd.layout_object

    def scrollable_ancestor(self, layout_object):
        """Return the nearest (innermost) scrollable BlockLayout ancestor."""
        current = layout_object
        while current is not None:
            if isinstance(current, BlockLayout) and current.is_scrollable():
                return current
            current = getattr(current, "parent", None)
        return None

    def scrollable_layout_for_node(self, node):
        if node is None or self.document is None:
            return None

        for obj in tree_to_list(self.document, []):
            if (
                isinstance(obj, BlockLayout)
                and obj.node is node
                and obj.is_scrollable()
            ):
                return obj
        return None

    def scroll_focused_element(self, delta):
        """Scroll the focused overflow container; return (handled, changed)."""
        self.render()

        if self.scroll_focus is None:
            return False, False

        layout = self.scrollable_layout_for_node(self.scroll_focus)
        if layout is None:
            self.scroll_focus = None
            return False, False

        changed = layout.scroll_by(delta)
        if changed:
            # Geometry is unchanged, but rebuilding the display list captures the
            # new Scroll node offset and preserves nested-scroll composition.
            self.relayout()
            self.browser.set_needs_animation_frame(self)

        return True, changed

    def href_at(self,x,y):
        obj = self.layout_object_at(x,y)

        if obj is None:
            return None

        # last matched layout object
        elt=obj.node

        while elt:
            if isinstance(elt,Element) and elt.tag=="a" and "href" in elt.attributes:
                return elt.attributes["href"]

            elt=elt.parent

        return None

    def link_at(self,x,y):
        href=self.href_at(x,y)
        
        if href is None:
            return None

        return self.url.resolve(href)

    def button_ancestor(self,elt):
        while elt:
            if isinstance(elt,Element) and elt.tag=="button":
                return elt
            elt=elt.parent

        return None

    def submit_button(self,button):
        elt = button
        
        while elt:
            if isinstance(elt,Element) and elt.tag=="form" and "action" in elt.attributes:
                self.submit_form(elt)
                return True

            elt = elt.parent

        return False

    def encode_form_data(self,elt):
        inputs = [
            node for node in tree_to_list(elt,[])
            if isinstance(node,Element)
            and node.tag == "input"
            and "name" in node.attributes
        ]

        body_parts = []

        for input in inputs:
            name = input.attributes["name"]

            if is_checkbox_input(input):
                if not getattr(input,"is_checked",False):
                    continue

                value = input.attributes.get("value","on")
            else:
                value = input.attributes.get("value","")

            name = quote_plus(name)
            value = quote_plus(value)

            body_parts.append(name+"="+value)

        return "&".join(body_parts)


    def submit_form(self,elt):
        if self.js.dispatch_event("submit",elt):
            return

        body = self.encode_form_data(elt)

        action = elt.attributes.get("action","")
        url = self.url.resolve(action)

        if url is None:
            return

        method = elt.attributes.get("method","get").lower()

        if method == "post":
            self.navigate(url,body)
        else:
            separator = "&" if "?" in url.path else "?"
            get_url = URL(str(url)+separator+body)
            self.navigate(get_url)

    def input_cursor_index_from_x(self, x, input_layout, display_text):
        """Map a click x-coordinate to a text-input caret index."""
        local_x = x - input_layout.x

        if local_x <= 0:
            return 0

        font = input_layout.font

        for i in range(len(display_text)):
            left = font.measureText(display_text[:i])
            right = font.measureText(display_text[:i + 1])
            midpoint = (left + right) / 2

            if local_x < midpoint:
                return i

        return len(display_text)

    def click(self,x,y,touch_radius=None):
        self.blur()

        if touch_radius is None:
            obj = self.layout_object_at(x,y)
        else:
            obj = self.touch_layout_object_at(x,y,touch_radius)

        if obj is None:
            return

        target = obj.node

        # when click text,obj.node maybe just a text
        # click event should be from target's parent element start
        while target is not None and not isinstance(target,Element):
            target = target.parent

        if target is None:
            return

        scroll_candidate = self.scrollable_ancestor(obj)

        # from real target start dispatch event and bubble up ancestor
        if self.js.dispatch_event("click",target):
            # preventDefault() is called
            return

        # Click focus chooses the nearest scroll container, which naturally makes
        # nested overflow scrolling focus the innermost container first.
        self.scroll_focus = (
            scroll_candidate.node
            if scroll_candidate is not None
            else None
        )

        button = self.button_ancestor(target) # find button object
        if button:

            if self.submit_button(button): # find form and action attribution
                return

            return

        elt=target
        
        while elt:
            if isinstance(elt,Element) and elt.tag == "input":

                if is_checkbox_input(elt):
                    elt.is_checked = not elt.is_checked
                    self.set_needs_render()
                    return

                # Match the address bar editing model: clicking focuses the
                # input without clearing it and places the caret at the click.
                self.focus = elt
                elt.is_focused = True

                value = elt.attributes.get("value", "")
                display_text = value
                if elt.attributes.get("type", "text").casefold() == "password":
                    display_text = "*" * len(value)

                if isinstance(obj, InputLayout) and obj.node is elt:
                    elt.cursor_index = self.input_cursor_index_from_x(
                        x,
                        obj,
                        display_text,
                    )
                else:
                    elt.cursor_index = len(value)

                self.set_needs_render()
                return

            if isinstance(elt,Element) and elt.tag == "a" and "href" in elt.attributes:


                href = elt.attributes["href"]

                if href.startswith("#"):
                    self.navigate_to_fragment(href[1:])
                    return

                url=self.url.resolve(href)
                if url is None:
                    return

                if url.is_external():
                    url.open_external()
                    return

                self.navigate(url)
                return

            elt = elt.parent

        return

    def keypress(self,char):
        if self.focus:
            if self.js.dispatch_event("keydown",self.focus):
                return

            value = self.focus.attributes.get("value", "")
            cursor = getattr(self.focus, "cursor_index", len(value))
            cursor = max(0, min(cursor, len(value)))

            self.focus.attributes["value"] = (
                value[:cursor]
                + char
                + value[cursor:]
            )
            self.focus.cursor_index = cursor + len(char)
            self.set_needs_render()

    def backspace(self):
        if not self.focus:
            return

        value = self.focus.attributes.get("value", "")
        cursor = getattr(self.focus, "cursor_index", len(value))
        cursor = max(0, min(cursor, len(value)))

        if cursor == 0:
            return

        self.focus.attributes["value"] = (
            value[:cursor - 1]
            + value[cursor:]
        )
        self.focus.cursor_index = cursor - 1
        self.set_needs_render()

    def left(self):
        if not self.focus:
            return

        value = self.focus.attributes.get("value", "")
        cursor = getattr(self.focus, "cursor_index", len(value))
        self.focus.cursor_index = max(0, cursor - 1)
        self.set_needs_render()

    def right(self):
        if not self.focus:
            return

        value = self.focus.attributes.get("value", "")
        cursor = getattr(self.focus, "cursor_index", len(value))
        self.focus.cursor_index = min(len(value), cursor + 1)
        self.set_needs_render()

    def enter(self):
        if not self.focus:
            return

        elt = self.focus

        if not isinstance(elt,Element):
            return

        if elt.tag != "input":
            return

        while elt:
            if isinstance(elt,Element) and elt.tag == "form" and "action" in elt.attributes:
                self.submit_form(elt)
                return

            elt=elt.parent

    def resize(self,width,tab_height):
        if width <= 10 or tab_height <= 10:
            return

        if self.width == width and self.tab_height == tab_height:
            return

        self.width = width
        self.height = tab_height
        self.tab_height = tab_height

        if self.nodes:
            self.set_needs_render()



class BrowserWindow:
    """One native SDL window containing many browser tabs."""
    def __init__(self, app, width=WIDTH, height=HEIGHT):
        self.app = app
        self.measure = app.measure
        self.width = int(width)
        self.height = int(height)

        self.tabs = []
        self.active_tab = None
        self.focus = None
        self._closed = False

        # One BrowserWindow lock protects every piece of state shared by the
        # Browser Thread, Timer callbacks, and Tab Main Threads. RLock keeps helper
        # methods composable without introducing self-deadlocks.
        self.lock = threading.RLock()
        self.committed_states = {}
        self.frame_state = None

        self.animation_timer = None
        self.needs_animation_frame = True
        self.needs_raster_and_draw = False
        self.needs_chrome_raster = False
        self.needs_tab_raster = False
        self.discard_address_bar_edit_on_commit = False

        flags = sdl2.SDL_WINDOW_SHOWN | sdl2.SDL_WINDOW_RESIZABLE
        self.sdl_window = sdl2.SDL_CreateWindow(
            b"Tai Gar",
            sdl2.SDL_WINDOWPOS_CENTERED,
            sdl2.SDL_WINDOWPOS_CENTERED,
            self.width,
            self.height,
            flags,
        )
        if not self.sdl_window:
            error = sdl2.SDL_GetError()
            if isinstance(error, bytes):
                error = error.decode("utf8", errors="replace")
            raise RuntimeError("SDL_CreateWindow failed: {}".format(error))

        self.window_id = int(sdl2.SDL_GetWindowID(self.sdl_window))
        self.root_surface = make_skia_surface(self.width, self.height)

        # Browser compositing surfaces. Chrome has a small fixed-height surface.
        # tab_surface is now a bounded interest-region cache instead of a
        # full-document bitmap, so very long pages do not allocate huge surfaces.
        self.chrome_surface = None
        self.tab_surface = None

        # Document coordinate represented by tab_surface y=0.
        self.interest_start = 0

        # Maximum cached height. The actual surface is smaller for short pages.
        self.interest_height = max(
            1,
            INTEREST_REGION_MULTIPLIER * self.height,
        )

        if sdl2.SDL_BYTEORDER == sdl2.SDL_BIG_ENDIAN:
            self.RED_MASK = 0xff000000
            self.GREEN_MASK = 0x00ff0000
            self.BLUE_MASK = 0x0000ff00
            self.ALPHA_MASK = 0x000000ff
        else:
            self.RED_MASK = 0x000000ff
            self.GREEN_MASK = 0x0000ff00
            self.BLUE_MASK = 0x00ff0000
            self.ALPHA_MASK = 0xff000000

        self.chrome = Chrome(self)
        self.raster_chrome()

    def close(self):
        with self.lock:
            if self._closed:
                return
            self._closed = True
            tabs = list(self.tabs)

        with self.lock:
            if self.animation_timer is not None:
                self.animation_timer.cancel()
                self.animation_timer = None

        # Wake every sleeping Tab main thread and ask it to terminate.
        for tab in tabs:
            tab.task_runner.set_needs_quit()

        for tab in tabs:
            tab.task_runner.join_thread(timeout=1.0)

        self.app.unregister_window(self)

        if self.sdl_window:
            sdl2.SDL_DestroyWindow(self.sdl_window)
            self.sdl_window = None

    def handle_quit(self):
        self.close()

    def tabs_snapshot(self):
        with self.lock:
            return list(self.tabs)

    def active_tab_snapshot(self):
        with self.lock:
            return self.active_tab

    def active_committed_state(self):
        with self.lock:
            tab = self.active_tab
            return self.committed_states.get(tab)

    def page_state(self):
        # During raster/draw, latch one commit so a concurrent Main Thread commit
        # cannot mix two different page snapshots inside a single presented frame.
        if self.frame_state is not None:
            return self.frame_state
        return self.active_committed_state()

    def set_active_tab(self, tab):
        with self.lock:
            if tab not in self.tabs:
                return

            if tab is self.active_tab:
                self.needs_animation_frame = True
                return

            self.active_tab = tab
            self.needs_animation_frame = True

            # A timer may belong to the previously-active tab. Release that frame
            # gate immediately so the new active tab can schedule its own frame.
            if self.animation_timer is not None:
                self.animation_timer.cancel()
                self.animation_timer = None

            # The existing surface belongs to the previous active tab.
            self.tab_surface = None
            self.interest_start = 0
            self.needs_raster_and_draw = True
            self.needs_chrome_raster = True
            self.needs_tab_raster = True

    def schedule_tab_task(self, tab, task_code, *args, clear_pending=False):
        if tab is None:
            return False
        if clear_pending:
            tab.task_runner.clear_pending_tasks()
        return tab.task_runner.schedule_task(Task(task_code, *args))

    def schedule_load(self, url, body=None, tab=None, add_to_history=True):
        if tab is None:
            with self.lock:
                tab = self.active_tab
        if tab is None:
            return False

        # A navigation invalidates queued work from the previous document.
        tab.task_runner.clear_pending_tasks()
        return tab.task_runner.schedule_task(
            Task(tab.load, url, body, add_to_history)
        )

    def schedule_go_back(self):
        with self.lock:
            tab = self.active_tab
        if tab is None:
            return
        self.schedule_tab_task(tab, tab.go_back, clear_pending=True)

    def schedule_go_forward(self):
        with self.lock:
            tab = self.active_tab
        if tab is None:
            return
        self.schedule_tab_task(tab, tab.go_forward, clear_pending=True)

    def new_tab(self, url):
        new_tab = Tab(
            self,
            self.width,
            max(1, self.height - self.chrome.bottom),
            self.app.visited_urls,
            self.app.bookmarks,
        )

        with self.lock:
            self.tabs.append(new_tab)
            tab_index = len(self.tabs) - 1

        new_tab.task_runner.start_thread(
            "Main thread - window {} tab {}".format(self.window_id, tab_index)
        )
        self.set_active_tab(new_tab)
        self.schedule_load(url, tab=new_tab)
        return new_tab

    def commit(self, tab, data):
        """Accept one Main-Thread CommitData snapshot as quickly as possible."""
        self.measure.time("commit")
        try:
            with self.lock:
                if self._closed or tab not in self.tabs:
                    return False

                old_state = self.committed_states.get(tab)
                has_new_display_list = data.display_list is not None

                # None means "reuse this tab's previously committed display list".
                # This keeps scroll-only frames cheap without sharing live Tab state.
                if data.display_list is None:
                    if old_state is None:
                        data.display_list = []
                    else:
                        data.display_list = old_state.display_list

                self.committed_states[tab] = data
                is_active = tab is self.active_tab

                # Inactive tabs are allowed to finish one last frame. Cache their
                # snapshot for a future tab switch, but never disturb the visible
                # tab's timer, dirty flags, chrome, or raster state.
                if not is_active:
                    return True

                chrome_changed = (
                    old_state is None
                    or old_state.title != data.title
                    or old_state.url_string != data.url_string
                    or old_state.secure != data.secure
                    or old_state.can_go_back != data.can_go_back
                    or old_state.can_go_forward != data.can_go_forward
                )

                if (
                    old_state is not None
                    and old_state.url_string != data.url_string
                ):
                    self.discard_address_bar_edit_on_commit = True

                # The animation timer remains non-None from scheduling until the
                # active tab commits. Clearing it here is the frame gate that
                # prevents multiple rendering tasks from piling up.
                self.animation_timer = None

                self.needs_raster_and_draw = True
                if chrome_changed:
                    self.needs_chrome_raster = True
                if has_new_display_list:
                    self.needs_tab_raster = True

                return True
        finally:
            self.measure.stop("commit")

    def set_needs_raster_and_draw(self, chrome=False, tab=False):
        with self.lock:
            self.needs_raster_and_draw = True
            if chrome:
                self.needs_chrome_raster = True
            if tab:
                self.needs_tab_raster = True


    def set_needs_animation_frame(self, tab):
        # Main Thread calls this when DOM/scroll/RAF work needs another frame.
        with self.lock:
            if self._closed or tab is not self.active_tab:
                return
            self.needs_animation_frame = True

    def schedule_animation_frame(self):
        """Schedule at most one in-flight animation frame for this window."""
        with self.lock:
            if (
                self._closed
                or self.active_tab is None
                or not self.needs_animation_frame
                or self.animation_timer is not None
                or self.needs_raster_and_draw
            ):
                return

            def callback():
                with self.lock:
                    if self._closed:
                        self.animation_timer = None
                        return

                    active_tab = self.active_tab
                    # Consume this request, but deliberately keep animation_timer
                    # non-None. commit() clears it only after Main Thread finishes
                    # the frame, providing back pressure.
                    self.needs_animation_frame = False

                if active_tab is None:
                    with self.lock:
                        self.animation_timer = None
                    return

                scheduled = active_tab.task_runner.schedule_task(
                    Task(active_tab.run_animation_frame)
                )
                if not scheduled:
                    with self.lock:
                        self.animation_timer = None

            timer = threading.Timer(REFRESH_RATE_SEC, callback)
            timer.daemon = True
            self.animation_timer = timer

        # Starting a Timer can create a thread, so do it after releasing Browser lock.
        timer.start()

    def raster_and_draw(self):
        # Consume the current dirty batch before doing expensive work. If a Main
        # Thread commits again while raster is running, its new dirty bits survive
        # for the next browser-loop iteration instead of being cleared accidentally.
        with self.lock:
            if not self.needs_raster_and_draw or self._closed:
                return False

            chrome_raster = self.needs_chrome_raster
            tab_raster = self.needs_tab_raster
            self.needs_raster_and_draw = False
            self.needs_chrome_raster = False
            self.needs_tab_raster = False

            # Dirty bits and committed data must belong to the same version.
            # A commit arriving after this lock is released becomes next frame's
            # dirty work and cannot be mixed into the frame being presented now.
            self.frame_state = self.committed_states.get(self.active_tab)

        self.measure.time("raster_and_draw")
        try:
            # A scroll-only commit normally reuses the cached interest region.
            # Crossing its boundary upgrades this frame to a tab raster.
            if (
                not tab_raster
                and self.frame_state is not None
                and (
                    self.tab_surface is None
                    or not self.viewport_inside_interest_region()
                )
            ):
                tab_raster = True

            if chrome_raster:
                self.raster_chrome()
            if tab_raster:
                self.raster_tab()

            self.draw()
            return True
        finally:
            with self.lock:
                self.frame_state = None
            self.measure.stop("raster_and_draw")


    def present_surface(self):
        skia_image = self.root_surface.makeImageSnapshot()
        skia_bytes = skia_image.tobytes()

        depth = 32
        pitch = 4 * self.width
        sdl_surface = sdl2.SDL_CreateRGBSurfaceFrom(
            skia_bytes,
            self.width,
            self.height,
            depth,
            pitch,
            self.RED_MASK,
            self.GREEN_MASK,
            self.BLUE_MASK,
            self.ALPHA_MASK,
        )
        if not sdl_surface:
            raise RuntimeError("SDL_CreateRGBSurfaceFrom failed")

        try:
            window_surface = sdl2.SDL_GetWindowSurface(self.sdl_window)
            rect = sdl2.SDL_Rect(0, 0, self.width, self.height)
            sdl2.SDL_BlitSurface(
                sdl_surface,
                ctypes.byref(rect),
                window_surface,
                ctypes.byref(rect),
            )
            sdl2.SDL_UpdateWindowSurface(self.sdl_window)
        finally:
            sdl2.SDL_FreeSurface(sdl_surface)

    def raster_chrome(self):
        """Raster browser chrome only when Chrome state/layout changes."""
        with self.lock:
            discard_edit = self.discard_address_bar_edit_on_commit
            self.discard_address_bar_edit_on_commit = False

        if discard_edit:
            self.chrome.discard_address_bar_edit()

        self.chrome.render()
        chrome_height = max(1, math.ceil(self.chrome.bottom))

        if (
            self.chrome_surface is None
            or self.chrome_surface.width() != self.width
            or self.chrome_surface.height() != chrome_height
        ):
            self.chrome_surface = make_skia_surface(self.width, chrome_height)

        canvas = self.chrome_surface.getCanvas()
        canvas.clear(skia.ColorWHITE)
        for cmd in self.chrome.paint():
            cmd.execute(canvas)


    def tab_view_height(self):
        """Height of the visible page viewport below browser chrome."""
        return max(1, int(math.ceil(self.height - self.chrome.bottom)))

    def document_height(self):
        """Full committed page height in document coordinates."""
        state = self.page_state()
        if state is None:
            return 0
        return max(0, int(math.ceil(state.document_height)))

    def interest_surface_height(self):
        """Actual bounded raster-cache height for the current page."""
        document_height = self.document_height()
        if document_height <= 0:
            return 0

        return max(
            1,
            min(document_height, int(math.ceil(self.interest_height))),
        )

    def interest_end(self):
        """Document-space end coordinate represented by tab_surface."""
        return min(
            self.document_height(),
            self.interest_start + self.interest_surface_height(),
        )

    # --- Coordinate-system helpers ---------------------------------------
    # Document: positions produced by layout/display-list commands.
    # Surface:  pixels stored in the bounded interest-region tab_surface.
    # Viewport: positions visible inside the page area after scrolling.

    def document_to_surface_y(self, document_y):
        """Document coordinate -> interest-surface coordinate."""
        return float(document_y) - float(self.interest_start)

    def surface_to_document_y(self, surface_y):
        """Interest-surface coordinate -> document coordinate."""
        return float(surface_y) + float(self.interest_start)

    def active_scroll(self):
        state = self.page_state()
        return float(state.scroll) if state is not None else 0.0

    def document_to_viewport_y(self, document_y):
        """Document coordinate -> page viewport coordinate."""
        return float(document_y) - self.active_scroll()

    def viewport_to_document_y(self, viewport_y):
        """Page viewport coordinate -> document coordinate."""
        return float(viewport_y) + self.active_scroll()

    def surface_to_viewport_y(self, surface_y):
        """Interest-surface coordinate -> page viewport coordinate."""
        return self.document_to_viewport_y(
            self.surface_to_document_y(surface_y)
        )

    def tab_surface_root_offset(self):
        """Root-canvas y offset used when compositing tab_surface."""
        # surface y=0 represents document y=interest_start. Convert that point
        # into viewport coordinates, then place the viewport below Chrome.
        return (
            float(self.chrome.bottom)
            + float(self.interest_start)
            - self.active_scroll()
        )

    # --- Interest-region management -------------------------------------

    def viewport_inside_interest_region(self):
        """Return True when the complete committed viewport is cached."""
        state = self.page_state()
        if state is None or state.document_height <= 0:
            return False

        region_height = self.interest_surface_height()
        if region_height <= 0:
            return False

        viewport_top = float(state.scroll)
        viewport_bottom = min(
            float(self.document_height()),
            viewport_top + float(self.tab_view_height()),
        )

        region_top = float(self.interest_start)
        region_bottom = region_top + float(region_height)

        return (
            viewport_top >= region_top
            and viewport_bottom <= region_bottom
        )

    def choose_interest_start(self):
        """Center the committed viewport inside a new bounded cache."""
        state = self.page_state()
        if state is None or state.document_height <= 0:
            return 0

        document_height = self.document_height()
        viewport_height = self.tab_view_height()
        region_height = self.interest_surface_height()

        if region_height >= document_height:
            return 0

        # Put half of the extra cached pixels before the viewport and half
        # after it. This avoids rerastering again immediately after crossing
        # an old interest-region boundary.
        spare_height = max(0, region_height - viewport_height)
        desired_start = float(state.scroll) - spare_height / 2.0

        max_start = max(0, document_height - region_height)
        return int(max(0, min(desired_start, max_start)))

    def reposition_interest_region(self):
        """Move the raster-cache window without changing page layout."""
        self.interest_start = self.choose_interest_start()

    def ensure_interest_region(self):
        """Reraster only when committed scrolling leaves the cached region."""
        state = self.page_state()
        if state is None or state.document_height <= 0:
            self.tab_surface = None
            self.interest_start = 0
            return False

        if self.tab_surface is None or not self.viewport_inside_interest_region():
            self.reposition_interest_region()
            self.set_needs_raster_and_draw(tab=True)
            return True

        return False

    def raster_tab(self):
        """Raster the active Tab's committed display-list snapshot.

        Browser Thread never calls Tab.render()/Tab.raster() here. The Main Thread
        already produced this display list and handed it across commit().
        """
        state = self.page_state()
        if state is None or state.document_height <= 0:
            self.tab_surface = None
            self.interest_start = 0
            return

        # Keep the viewport fully covered. Page edits/navigations can change
        # document height or scroll position even when this is not a scroll event.
        if not self.viewport_inside_interest_region():
            self.reposition_interest_region()

        region_height = self.interest_surface_height()
        region_end = self.interest_start + region_height

        if (
            self.tab_surface is None
            or self.tab_surface.width() != self.width
            or self.tab_surface.height() != region_height
        ):
            self.tab_surface = make_skia_surface(self.width, region_height)

        canvas = self.tab_surface.getCanvas()
        canvas.clear(skia.ColorWHITE)

        surface_clip = skia.Rect.MakeLTRB(
            0,
            0,
            self.width,
            region_height,
        )
        document_interest_rect = skia.Rect.MakeLTRB(
            0,
            self.interest_start,
            self.width,
            region_end,
        )

        canvas.save()
        canvas.clipRect(surface_clip)
        canvas.translate(0, -self.interest_start)

        for item in state.display_list:
            if hasattr(item, "rect"):
                if (
                    item.rect.bottom() <= document_interest_rect.top()
                    or item.rect.top() >= document_interest_rect.bottom()
                ):
                    continue
            item.execute(canvas)

        canvas.restore()


    def draw_scrollbar(self, canvas):
        """Draw the viewport scrollbar from committed state."""
        state = self.page_state()
        if state is None or state.document_height <= 0:
            return

        viewport_height = max(1, self.height - self.chrome.bottom)
        document_height = max(1.0, float(state.document_height))
        if document_height <= viewport_height:
            return

        bar_height = viewport_height * viewport_height / document_height
        bar_height = max(20.0, min(float(viewport_height), bar_height))

        max_scroll = max(document_height - viewport_height, 1)
        max_bar_y = max(viewport_height - bar_height, 0.0)
        bar_y = max_bar_y * float(state.scroll) / max_scroll

        scrollbar = skia.Rect.MakeLTRB(
            self.width - SCROLLBAR_WIDTH,
            self.chrome.bottom + bar_y,
            self.width,
            self.chrome.bottom + bar_y + bar_height,
        )
        canvas.drawRect(
            scrollbar,
            skia.Paint(Color=parse_color("blue")),
        )

    def draw(self):
        """Composite cached tab/chrome surfaces and present them through SDL."""
        if self._closed or not self.sdl_window:
            return

        self.update_title()

        canvas = self.root_surface.getCanvas()
        canvas.clear(skia.ColorWHITE)

        # Page surface: clip to the content viewport and translate by the
        # committed scroll offset.
        if self.page_state() is not None and self.tab_surface is not None:
            tab_rect = skia.Rect.MakeLTRB(
                0,
                self.chrome.bottom,
                self.width,
                self.height,
            )
            # tab_surface y=0 is no longer document y=0. It represents
            # document y=interest_start, so composite with the third coordinate
            # conversion: surface -> viewport/root.
            tab_offset = self.tab_surface_root_offset()

            canvas.save()
            canvas.clipRect(tab_rect)
            canvas.translate(0, tab_offset)
            self.tab_surface.draw(canvas, 0, 0)
            canvas.restore()

            # The scrollbar is viewport-relative, so it is cheap to redraw here.
            self.draw_scrollbar(canvas)

        # Chrome surface is composited last so page pixels never cover it.
        if self.chrome_surface is not None:
            chrome_rect = skia.Rect.MakeLTRB(
                0,
                0,
                self.width,
                self.chrome.bottom,
            )
            canvas.save()
            canvas.clipRect(chrome_rect)
            self.chrome_surface.draw(canvas, 0, 0)
            canvas.restore()

        self.present_surface()

    def update_title(self):
        state = self.page_state()
        title = state.title if state is not None else "Tai Gar"

        sdl2.SDL_SetWindowTitle(
            self.sdl_window,
            title.encode("utf8", errors="replace"),
        )

    def active_url_string(self):
        state = self.active_committed_state()
        return state.url_string if state is not None else None

    def current_url_string(self):
        url = self.active_url_string()
        if not url or url in ["about:blank", "about:bookmarks"]:
            return None
        return url

    def active_is_secure(self):
        state = self.active_committed_state()
        return bool(state.secure) if state is not None else False

    def active_can_go_back(self):
        state = self.active_committed_state()
        return bool(state.can_go_back) if state is not None else False

    def active_can_go_forward(self):
        state = self.active_committed_state()
        return bool(state.can_go_forward) if state is not None else False

    def is_current_page_bookmarked(self):
        url = self.current_url_string()
        return url is not None and url in self.app.bookmarks

    def toggle_bookmark(self):
        url = self.current_url_string()
        if url is None:
            return

        if url in self.app.bookmarks:
            self.app.bookmarks.remove(url)
        else:
            self.app.bookmarks.add(url)

        self.set_needs_raster_and_draw(chrome=True)

    def handle_down(self):
        with self.lock:
            tab = self.active_tab
        if tab is not None:
            self.schedule_tab_task(tab, tab.scroll_by, SCROLL_STEP)

    def handle_up(self):
        with self.lock:
            tab = self.active_tab
        if tab is not None:
            self.schedule_tab_task(tab, tab.scroll_by, -SCROLL_STEP)

    def handle_mousewheel(self, delta):
        if delta == 0:
            return

        with self.lock:
            tab = self.active_tab
        if tab is None:
            return

        scroll_delta = -SCROLL_STEP if delta > 0 else SCROLL_STEP
        self.schedule_tab_task(tab, tab.scroll_by, scroll_delta)


    def handle_primary_activation(self, x, y, touch_radius=None):
        """Route browser-chrome work locally and page work to the Tab main thread."""
        if y < self.chrome.bottom:
            self.focus = None

            with self.lock:
                old_tab = self.active_tab

            if old_tab is not None:
                self.schedule_tab_task(old_tab, old_tab.blur)

            # Chrome hit testing and controls belong to the Browser Thread.
            self.chrome.click(x, y, touch_radius=touch_radius)
            self.set_needs_raster_and_draw(chrome=True)
            return

        self.focus = "content"
        chrome_was_focused = self.chrome.focus == "address bar"
        self.chrome.blur_address_bar()

        with self.lock:
            tab = self.active_tab
        if tab is None:
            return

        tab_y = y - self.chrome.bottom
        self.schedule_tab_task(tab, tab.click, x, tab_y, touch_radius)

        if chrome_was_focused:
            self.set_needs_raster_and_draw(chrome=True)


    def handle_click(self, x, y):
        self.handle_primary_activation(x, y, touch_radius=None)

    def handle_touch(self, x, y, radius=TOUCH_RADIUS_PX):
        """Handle a one-finger tap using area-based hit testing."""
        print("[touch] tap at ({}, {}) radius={}".format(x, y, radius))
        self.handle_primary_activation(x, y, touch_radius=radius)

    def committed_link_at(self, x, y):
        """Hit-test the active committed display list without calling into Tab."""
        state = self.active_committed_state()
        if state is None or not state.url_string:
            return None

        document_y = float(y) + float(state.scroll)
        cmd = hit_test_paint_commands(state.display_list, x, document_y)
        if cmd is None or not hasattr(cmd, "layout_object"):
            return None

        elt = getattr(cmd.layout_object, "node", None)
        while elt is not None:
            if (
                isinstance(elt, Element)
                and elt.tag == "a"
                and "href" in elt.attributes
            ):
                base_url = URL(state.url_string)
                return base_url.resolve(elt.attributes["href"])
            elt = getattr(elt, "parent", None)

        return None

    def handle_middle_click(self, x, y):
        with self.lock:
            tab = self.active_tab
        if tab is None:
            return

        if y < self.chrome.bottom:
            self.schedule_tab_task(tab, tab.blur)
            self.set_needs_raster_and_draw(chrome=True)
            return

        chrome_was_focused = self.chrome.focus == "address bar"
        self.chrome.blur_address_bar()
        tab_y = y - self.chrome.bottom
        url = self.committed_link_at(x, tab_y)

        if chrome_was_focused:
            self.set_needs_raster_and_draw(chrome=True)

        if url is not None:
            if url.is_external():
                url.open_external()
            else:
                # Middle-click tab creation stays on the Browser Thread.
                self.new_tab(url)


    def handle_key(self, text):
        if not text:
            return

        text = "".join(ch for ch in text if ord(ch) >= 0x20)
        if not text:
            return

        if self.chrome.keypress(text):
            self.set_needs_raster_and_draw(chrome=True)
            return

        if self.focus == "content":
            with self.lock:
                tab = self.active_tab
            if tab is not None:
                self.schedule_tab_task(tab, tab.keypress, text)

    def handle_enter(self):
        if self.chrome.focus == "address bar":
            if self.chrome.enter():
                self.set_needs_raster_and_draw(chrome=True)
            return

        if self.focus == "content":
            with self.lock:
                tab = self.active_tab
            if tab is not None:
                self.schedule_tab_task(tab, tab.enter)

    def handle_backspace(self):
        if self.chrome.focus == "address bar":
            self.chrome.backspace()
            self.set_needs_raster_and_draw(chrome=True)
            return

        if self.focus == "content":
            with self.lock:
                tab = self.active_tab
            if tab is not None:
                self.schedule_tab_task(tab, tab.backspace)

    def handle_left(self):
        if self.chrome.focus == "address bar":
            self.chrome.left()
            self.set_needs_raster_and_draw(chrome=True)
            return

        if self.focus == "content":
            with self.lock:
                tab = self.active_tab
            if tab is not None:
                self.schedule_tab_task(tab, tab.left)

    def handle_right(self):
        if self.chrome.focus == "address bar":
            self.chrome.right()
            self.set_needs_raster_and_draw(chrome=True)
            return

        if self.focus == "content":
            with self.lock:
                tab = self.active_tab
            if tab is not None:
                self.schedule_tab_task(tab, tab.right)


    def handle_new_window(self):
        self.app.new_window(
            URL("https://browser.engineering/")
        )

    def resize(self, width, height):
        width = int(width)
        height = int(height)

        if width <= 10 or height <= 10:
            return
        if self.width == width and self.height == height:
            return

        self.width = width
        self.height = height
        self.root_surface = make_skia_surface(width, height)
        self.chrome_surface = None
        self.tab_surface = None

        self.interest_height = max(
            1,
            INTEREST_REGION_MULTIPLIER * self.height,
        )
        self.interest_start = 0

        # Chrome is Browser-Thread-owned and can be rebuilt immediately.
        self.raster_chrome()
        tab_height = max(1, height - self.chrome.bottom)

        # Layout belongs to each Tab Main Thread. A resize task marks that Tab
        # dirty; the next animation frame renders and commits a new display list.
        for tab in self.tabs_snapshot():
            self.schedule_tab_task(tab, tab.resize, width, tab_height)

        # Present the resized chrome now; the page is filled in by the next commit.
        self.set_needs_raster_and_draw()




class URL:
    def __init__(self, url):
        self.view_source=False
        self.scheme=""
        self.host=""
        self.path=""
        self.port=0
        self.fragment=""
        self.url_string=url
        
        try:

            # parse view-source        
            if url.startswith("view-source:"):
                # 例如 "view-source:http://google.com" 變成 "http://google.com"
                self.view_source=True
                _,url=url.split(":",1)

            # parse fragment: page.html#section
            if "#" in url:
                url,self.fragment=url.split("#",1)


            if url.startswith("about:"):
                self.scheme="about"
                self.path=url.split(":",1)[1]
                self.url_string = "about:"+self.path
                return

            if url.startswith("mailto:"):
                # self.scheme="mailto"
                self.scheme,self.path=url.split(":",1)
                self.url_string="mailto:"+self.path
                return

            
            if url.startswith("data:"):
                self.scheme="data"
                self.scheme,self.path = url.split(":", 1)
            else:
                if "://"  not in url:
                    raise ValueError("Malformed URL: missing ://")
                
                self.scheme, url = url.split("://", 1)

            # 支援的 URL Scheme
            if self.scheme not in ["http", "https","file","data","about","mailto"]:
                raise ValueError(f"Unsupported scheme: {self.scheme}")
            

            if self.scheme=="http":
                self.port=80
            elif self.scheme=="https":
                self.port=443
                
            if self.scheme=="http" or self.scheme=="https":
                # 原本http/https的處理邏輯
                # 確保 URL 包含路徑，若無則補上 "/"
                if "/" not in url:
                    # 如果網址像 "http://google.com"，沒有斜線
                    url = url + "/"

                # 分離主機名稱 (Host) 與路徑 (Path)
                self.host, url = url.split("/", 1)
                self.path = "/" + url

                if ":" in self.host:
                    self.host,port=self.host.split(":",1)
                    self.port=int(port)

            if self.scheme == "file":
                # 檔案協議沒Host，剩下的url就是路徑
                # file:///Users/test.txt -> url 變為 /Users/test.txt
                self.path=url
                self.host=""

            # save origin url string，for cache key
            if self.scheme in ["http","https"]:
                self.url_string=f"{self.scheme}://{self.host}:{self.port}{self.path}"
            else:
                self.url_string=url

        except Exception as e:
            # 只要解析失敗，自動降級為 about:blank
            print(f"URL Parse Error: {e}. Falling back to about:blank")
            self.scheme="about"
            self.path="blank"
            self.url_string="about:blank"

    def origin(self):
        return (
            self.scheme
            +"://"
            +self.host
            +":"
            +str(self.port)
        )

    # referrer: reference browser lauch request current page
    def request(self,referrer,payload=None,origin=None,referrer_policy=None):

        if self.scheme=="about":
            return {},""

        if self.scheme=="data":   
            #example: text/html,Hello World!
            if "," in self.path:
                media_type,body=self.path.split(",",1)
                return {},unquote(body)
            else:
                return {},""

        if self.scheme=="mailto":
            return {},"""
            <html>
            <body>
                <h1>External mail link</h1>
                <p>This link should be opened by your mail application.</p>
            </body>
            </html>
            """


        if self.scheme=="file":
            try:
                with open(self.path,"r",encoding="utf-8") as f:
                    return {},f.read()
            except Exception as e:
                print(f"File read error: {e}")
                return {},f"""
                <html>
                <body>
                    <h1>File not found</h1>
                    <p>{self.path}</p>
                    <pre>{e}</pre>
                </body>
                </html>
                """
        
        current_url=self
        redirect_limit=10 

        while redirect_limit>0:
            
            if payload is None and origin is None and  current_url.url_string in http_cache:
                cached_headers,cached_body,expires_at=http_cache[current_url.url_string]

                if time.time() < expires_at:
                    
                    print(f"Cache Hit! (Expires in {int(expires_at - time.time())}s)")
                    return cached_headers,cached_body.decode("utf-8",errors="replace")

                else:
                    print("Cache Expired! Re-downloading...")
                    del http_cache[current_url.url_string]



            key=(current_url.scheme, current_url.host, current_url.port)
            
            # POST don't reuse socket
            use_socket_cache = payload is None

            if use_socket_cache and key in socket_cache:
                s=socket_cache[key]
            else:
                # 建立 TCP Socket 連線
                s = socket.socket(
                    family=socket.AF_INET,
                    type=socket.SOCK_STREAM,
                    proto=socket.IPPROTO_TCP
                )

                # 連接到伺服器的Port
                s.connect((current_url.host,current_url.port))

                if current_url.scheme == "https":
                    ctx = ssl.create_default_context()

                    try:
                        s = ctx.wrap_socket(s, server_hostname=current_url.host)
                    except ssl.SSLCertVerificationError as e:
                        s.close()
                        raise

            # 定義要發送的headers
            headers = {
                    "Host": current_url.host, # 注意：轉址後 Host 也要變，所以用 current_url.host
                    "Connection":"close" if payload is not None else "keep-alive", # 關閉連線
                    "User-Agent":"Tai_Gar/1.0", # 自定義 User-Agent
                    "Accept-Encoding":"gzip" # support gzip
            }


            # CORS Origin
            if origin is not None:
                headers["Origin"] = origin

            # Referrer
            if should_send_referrer(referrer,current_url,referrer_policy):
                headers["Referer"] = referer_value(referrer)

            if payload is not None:
                headers["Content-Length"] = str(len(payload.encode("utf-8")))

            method = "POST" if payload is not None else "GET"

            cookie_entry=get_valid_cookie(current_url.host)

            if cookie_entry is not None:
                cookie,params = cookie_entry

                allow_cookie=True

                if referrer and params.get("samesite","none")=="lax":
                    if method != "GET":
                        allow_cookie=(current_url.host==referrer.host)

                if allow_cookie:
                    headers["Cookie"] = cookie
        
            request = "{} {} HTTP/1.1\r\n".format(method,current_url.path)

            for header,value in headers.items():
                request+= "{}: {}\r\n".format(header,value)
        
            request += "\r\n"  # 請求標頭結束，需多一個空行

            if payload is not None:
                request += payload

            # print("\n===== REQUEST =====")
            # print(request)

            # 發送編碼後的請求
            s.send(request.encode("utf-8"))

            # 使用 makefile 建立檔案介面，方便逐行讀取回應
            response = s.makefile("rb")

            try:

                # 讀取狀態行 (Status Line)，例如: HTTP/1.0 200 OK
                statusline = response.readline().decode("utf-8")
                if not statusline:
                    s.close()
                    if key in socket_cache:
                        del socket_cache[key]
                    break

                version, status, explanation = statusline.split(" ", 2)
                status=int(status)

            except Exception:
                s.close()
                if key in socket_cache:
                    del socket_cache[key]
                    
                continue
        
            # 讀取並解析回應標頭 (Headers)
            response_headers = {}
            while True:
                line = response.readline().decode("utf-8")
                if line == "\r\n": break  # 遇到空行表示標頭結束
                header, value = line.split(":", 1)
                response_headers[header.casefold()] = value.strip()

            # print("\n===== RESPONSE =====")

            # for header, value in \
            #         response_headers.items():
            #     print("{}: {}".format(
            #         header,
            #         value
            #     ))

            # if server send back like set-cookie: token=abc123; SameSite=Lax
            if "set-cookie" in response_headers:
                cookie,params=parse_cookie_string(
                    response_headers["set-cookie"]
                )

                # delete expire cookie
                if cookie_is_expired(params):
                    COOKIE_JAR.pop(current_url.host,None)

                else:
                    # update new expire cookie info
                    COOKIE_JAR[current_url.host]=(cookie,params)


            content_bytes=b""

            if response_headers.get("transfer-encoding") == "chunked":
                # chucked transfer read mode
                while True:
                    #1. read line (16 bits) b"1F\r\n"
                    line=response.readline().strip() # remove \r\n
                    if not line:
                        break

                    # let 16 bits string into int
                    chunk_len=int(line,16)

                    # check 0 len
                    if chunk_len==0:
                        break

                    # 3. read data blocks
                    chuck_data=response.read(chunk_len)
                    content_bytes+=chuck_data 

                    # 4. read and throw away data blocks after \r\n
                    response.read(2)


            # 讀取 Body (無論是 200 還是 301，都要把 Body 讀乾淨，才能 reuse socket)
            elif "content-length" in response_headers:
                content_length = int(response_headers["content-length"])
                content_bytes = response.read(content_length)
            else:
                # 對於 3xx 轉址，如果沒有 Content-Length，有些伺服器可能直接不傳 Body
                # 但為了安全起見，這裡還是保留 read()，但在 Keep-Alive 下沒 Length 其實很危險
                content_bytes = response.read()

            #gzip decompression
            if response_headers.get("content-encoding") == "gzip":
                # if sever say it's gzip ，then decompression
                content_bytes=gzip.decompress(content_bytes)

            # --- 轉址處理 ---
            if 300<=status<400:
                
                if "location" in response_headers:
                    location=response_headers["location"]
                    
                     # 處理相對路徑 (例如 "/redirect2")
                    if location.startswith("/"):
                        location=current_url.scheme+"://"+current_url.host+location
                    
                    print(f"Redirect location: {location}")
                    
                    #更新current_url,準備下一次迴圈
                    print(f"Redirecting to: {location}") # 除錯用，讓你知道正在轉址
                    current_url=URL(location)

                    redirect_limit-=1
                    continue

            # 檢查 Cache-Control
            if payload is None and  origin is None and status==200 and "cache-control" in response_headers:
                cache_control=response_headers["cache-control"]

                cache_control = cache_control.lower()
                
                if "no-store" in cache_control:
                    pass

                elif "max-age" in cache_control:
                    try:
                        directives=cache_control.split(",")
                        for directive in directives:
                            directive=directive.strip()

                            if directive.startswith("max-age="):
                                _,seconds=directive.split("=",1)
                                

                                if seconds.isdigit():
                                    max_age=int(seconds)
                                    expires_at=time.time()+max_age

                                    http_cache[current_url.url_string]=(
                                        response_headers.copy(),
                                        content_bytes,
                                        expires_at
                                    )
                                    
                                    print(f"Cached! (max-age={max_age})")

                                break

                    except ValueError:
                        pass
                    
            # print(f"Debug - Headers: {response_headers.keys()}")
            if "cache-control" in response_headers:
                print(f"Debug - Cache-Control value: {response_headers['cache-control']}")


            if "content-encoding" in response_headers:
                print(f"Debug - Content-Encoding: {response_headers['content-encoding']}")
            if "transfer-encoding" in response_headers:
                print(f"Debug - Transfer-Encoding: {response_headers['transfer-encoding']}")

            if payload is not None:
                s.close()

            # 如果不是轉址 (200 OK 或其他錯誤)，直接回傳結果
            return response_headers, content_bytes.decode("utf-8",errors="replace")


        raise Exception("Redirect loop detected!")

    def is_external(self):
        return self.scheme in ["mailto"]

    def open_external(self):
        if self.scheme=="mailto":
            print("open external URL:",str(self))
            ok = webbrowser.open(str(self))
            print("webbrowser.open returned:",ok)
            return True

        return False

    def __str__(self):
        fragment_part=""
        if self.fragment:
            fragment_part="#"+self.fragment

        if self.scheme=="mailto":
            return "mailto:"+self.path

        if self.view_source:
            return "view-source:" + self.url_string +fragment_part

        if self.scheme=="about":
            return "about:"+self.path +fragment_part

        if self.scheme=="data":
            return "data:"+self.path +fragment_part

        if self.scheme=="file":
            return "file://"+self.path +fragment_part

        port_part=":"+str(self.port)

        if self.scheme=="https" and self.port==443:
            port_part=""

        if self.scheme=="http" and self.port==80:
            port_part=""

        return self.scheme+"://"+self.host+port_part+self.path+fragment_part

    def with_fragment(self,fragment):
        base=str(self).split("#",1)[0]

        if fragment:
            return URL(base+"#"+fragment)

        else:
            return URL(base)

    def resolve(self,url):
        if url is None:
            return None

        url=url.strip()
        
        if not url:
            return self

        # fragment-only relative URL: #section
        if url.startswith("#"):
            return self.with_fragment(url[1:])


        if url.startswith("//"): # scheme-relative URL: //example.com/path
            return URL(self.scheme+":"+url)

        # explicit URL with scheme
        # examples: http:, https:, data:, file:, view-source:, mailto
        if ":" in url.split("/",1)[0]: 
            scheme = url.split(":",1)[0].casefold()

            if scheme in ["http", "https", "file", "data", "about", "view-source", "mailto"]:
                return URL(url)

            # unsupported scheme: javascript:,tel:,sms:,ftp:,..
            return None

        # host-relative URL
        if url.startswith("/"):
            if self.scheme in ["http","https"]:
                port_part=""

                if self.scheme == "http" and self.port !=80:
                    port_part=":"+str(self.port)
                    
                elif self.scheme == "https" and self.port!= 443:
                    port_part = ":"+str(self.port)

                return URL(self.scheme+"://"+self.host+port_part+url)

            if self.scheme=="file":
                return URL("file://"+url)

            return None

        # path-relative URL: page.html, test.js ../x.js
        dir,_ = self.path.rsplit("/",1) 
        while url.startswith("../"): # deal with relative URL parent directory `..`
            _,url= url.split("/",1)

            if "/" in dir:
                dir, _ = dir.rsplit("/",1)
    
        # file:// URL has no host and no port
        if self.scheme == "file":
            return URL("file://"+dir+"/"+url)

        # normal http /https relative URL
        port_part = ""

        if self.scheme == "http" and self.port !=80:
            port_part=":"+str(self.port)
            
        elif self.scheme == "https" and self.port!= 443:
            port_part = ":"+str(self.port)
    

        return URL(self.scheme+ "://" +self.host+port_part+dir+"/"+url)
        

class HTMLParser:
    def __init__(self,body):
        self.body=body
        self.unfinished=[] # stack
        self.formatting_stack=[]
        self.FORMATTING_TAGS=["b","i","u","small","big"]
        self.SELF_CLOSING_TAGS = [
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr",
        ]
        self.HEAD_TAGS=[
        "base", "basefont", "bgsound", "noscript",
        "link", "meta", "title", "style", "script",
        ]


    def parse(self):
        text=""
        in_tag=False
        quote=None # record current which quote (None, '"', "'")
        i=0
        while i<len(self.body):
            # first check comment
            
            # if now not in the tag，and detect "<!--"
            if not in_tag  and self.body.startswith("<!--",i):
                # if have appended text before comment, remove texts
                if text: self.add_text(text)
                text=""
                
                # from i+4 to find "-->"，make sure that not misleading from <!--
                end_idx=self.body.find("-->",i+4)
                if end_idx==-1: # not found，just skip to the end
                    i=len(self.body)
                else:# find it，jump to --> behind position 
                    i=end_idx+3
                
                continue


            c=self.body[i]

            if not in_tag:
                if c =="<":
                    in_tag=True
                    if text: self.add_text(text)
                    text=""
                else:
                    text+=c

            else: # in_tag
                if quote: # in quotet protect status
                    if c==quote:
                        quote=None

                    text+=c
                else:
                    if c in ["'",'"']:
                        quote=c #into quote protect status 
                        text+=c

                    elif c==">": # when non quote status > represent tag end
                        in_tag=False
                        # get tag name check is it script 
                        tag_name=text.split()[0].casefold() if text else ""
                        self.add_tag(text)
                        text=""

                        if tag_name=="script":
                            # from i position start to find next </script> 
                            content_start=i+1
                            lower_body=self.body.lower()
                            end_script_idx=lower_body.find("</script>",content_start)

                            if end_script_idx==-1:
                                # if not finding </script>，lefting word make text
                                script_content=self.body[content_start:]
                                if script_content:
                                    self.add_text(script_content)
                                i=len(self.body)

                            else:
                                # get middle js code make pure text
                                script_content=self.body[content_start:end_script_idx]
                                if script_content:
                                    self.add_text(script_content)

                                # move i position to </script> before word
                                # next loop i+=1 ，metting </script>'s "<"
                                i=end_script_idx-1
                    else:
                        text+=c


            i+=1

        if not in_tag and text:
            self.add_text(text)

        return self.finish()
    
    def implicit_tags(self,tag):
        while True:
            open_tags=[node.tag for node in self.unfinished]

            if open_tags == [] and tag !="html":
                self.add_tag("html")

            elif open_tags==["html"] and tag not in ["head","body","/html"]:
                if tag in self.HEAD_TAGS:
                    self.add_tag("head")
                else:
                    self.add_tag("body")

            elif open_tags==["html","head"] and tag not in ["/head"] + self.HEAD_TAGS:
                self.add_tag("/head")

            else:
                break
                

    def add_text(self,text):
        # if text.isspace(): return # ignore space node
        self.implicit_tags(None)
        if not self.unfinished: return
        parent=self.unfinished[-1]

        decode_text=unescape(text)

        node=Text(decode_text,parent)
        parent.children.append(node)

    def add_tag(self,tag):
        tag,attributes=self.get_attribute(tag)
        # ignore Doctype and comment
        if tag.startswith("!"): return 
        self.implicit_tags(tag)


        # auto-closing tags
        if tag=="p":
            # if stack have p，pop it，until close that p
            if any(node.tag=="p" for node in self.unfinished):
                self.add_tag("/p")

        # deal with li
        if tag=="li":
            # check nearest list tag
            for node in reversed(self.unfinished):
                if node.tag=="li":
                    # found last li，and middle have no new ul/ol，auto close it
                    self.add_tag("/li")
                    break

                if node.tag in ["ul","ol"]:
                    # found list container tag，can't close li
                    break

        if tag.startswith("/"): #end tag label , like </hmtl>
            
            tag_name=tag[1:]
            
            # simple Adoption Agency Algorithm
            if tag_name in self.FORMATTING_TAGS:
                # check this tag is in the formatting stack
                if tag_name not in [node.tag  for node in self.formatting_stack]:
                    return # not opened yet tag，ignore 

                # find what tags need to tempeory close and restart
                # pop it out from formatting_stack，until encounter target label
                reopen_list=[]
                while self.formatting_stack:
                    node=self.formatting_stack.pop()
                    if node.tag==tag_name:
                        break

                    reopen_list.append(node)

                # in the unfinished stack do it same thing
                # need to force pop it out until find target tag close it
                while self.unfinished:
                    node=self.unfinished.pop()
                    # let node mount to parent node
                    if self.unfinished:
                        parent=self.unfinished[-1]
                        parent.children.append(node)

                    if node.tag==tag_name:
                        break

                # reopen these forcing tags (reopen_list)
                # these tags will make target tags silbiing nodes
                for f_node in reversed(reopen_list):
                    # build one same attribute new node
                    new_node=Element(f_node.tag,f_node.attributes,self.unfinished[-1])
                    self.unfinished.append(new_node)
                    self.formatting_stack.append(new_node)

                return

            # origin non-formatting tags(p,li,div..)
            if len(self.unfinished)==1: return
            node=self.unfinished.pop()
            parent=self.unfinished[-1]
            parent.children.append(node)

        elif tag in self.SELF_CLOSING_TAGS:
            parent=self.unfinished[-1]
            node=Element(tag,attributes,parent)
            parent.children.append(node)
        else: # start tag label, like <html>
            parent=self.unfinished[-1] if self.unfinished else None
            node=Element(tag,attributes,parent)
            self.unfinished.append(node)

            if tag in self.FORMATTING_TAGS:
                self.formatting_stack.append(node)

    def finish(self):
        if not self.unfinished:
            self.implicit_tags(None)
        # deal with not close yet tags
        while len(self.unfinished) > 1:
            node=self.unfinished.pop()
            parent=self.unfinished[-1]
            parent.children.append(node)

        return self.unfinished.pop()         

    def get_attribute(self,text):
        if not text: return "",{}

        # get tag name (first space before)
        i=0
        while i<len(text) and not text[i].isspace():
            i+=1
        tag=text[:i].casefold()

        # get attributes key pair
        attributes={}
        while i< len(text):
            # skip space
            while i<len(text) and text[i].isspace():
                i+=1
            if i>=len(text):break

            # starting scan one key-value pair(key=value)
            start=i
            quote=None
            while i<len(text):
                if text[i] in ["'",'"']:
                    if quote==text[i]: quote=None
                    elif not quote: quote=text[i]

                # only in non quote encounter space,represent attribute tag ending
                if not quote and text[i].isspace():
                    break

                i+=1

            attrpair=text[start:i]
            if "=" in attrpair:
                key,value=attrpair.split("=",1)
                # remove two side quote
                if len(value)>=2 and value[0] in ["'",'"']  and value[0] ==value[-1]:
                    value=value[1:-1]

                attributes[key.casefold()]=value

            else:
                attributes[attrpair.casefold()]=""
        
        return tag,attributes  


class CSSParser:
    def __init__(self,s):
        self.s=s
        self.i=0

    def whitespace(self):
        while self.i < len(self.s) and self.s[self.i].isspace():
            self.i+=1
    
    def literal(self,literal):
        if not (self.i < len(self.s) and self.s[self.i]==literal):
            raise Exception("Parsing error")

        self.i+=1

    def word(self):
        start=self.i
        while self.i < len(self.s):
            c=self.s[self.i]
            if c.isalnum() or c in "#-.%":
                self.i+=1
            else:
                break

        if self.i <= start:
            raise Exception("Parsing error")
        return self.s[start:self.i]

    # read tag name and class name
    # span.announce
    # read only span tag and then encounter "." stop
    def identifier(self):
        start = self.i
        
        while self.i < len(self.s):
            c = self.s[self.i]

            if c.isalnum() or c in "-_":
                self.i+=1
            else:
                break

        if self.i <= start:
            raise Exception("Parsing error")
        
        return self.s[start:self.i]

    def value_token(self):
        """Read one CSS declaration value token.

        Unlike word(), declaration values may contain functional notation such
        as blur(6px), grayscale(1), rgb(...), or calc(...). Keep a complete
        parenthesized function together so later property-specific parsers can
        interpret it.
        """
        start = self.i
        depth = 0

        while self.i < len(self.s):
            c = self.s[self.i]

            if depth == 0 and (c.isspace() or c in ";}!" ):
                break

            if c == "(":
                depth += 1
            elif c == ")":
                if depth == 0:
                    raise Exception("Parsing error")
                depth -= 1

            self.i += 1

        if self.i <= start or depth != 0:
            raise Exception("Parsing error")

        return self.s[start:self.i]

    def value(self):
        values=[]
        important = False

        while self.i < len(self.s) and self.s[self.i] not in ";}":
            self.whitespace()

            if self.i >= len(self.s) or self.s[self.i] in ";}":
                break

            if self.s[self.i] == "!":
                self.literal("!")
                self.whitespace()

                word =self.identifier().casefold()
                if word!="important":
                    raise Exception("Parsing error")

                important = True
                self.whitespace()
            else:
                values.append(self.value_token())
                self.whitespace()

        return values,important

    def pair(self):
        prop=self.word()
        self.whitespace()
        self.literal(":")
        self.whitespace()

        vals, important =self.value()

        return prop.casefold(), vals, important 

    def font_shorthand(self,values):
        out={}
        family=[]
        saw_size=False

        
        for value in values:
            lowered= value.casefold()

            if lowered=="italic":
                out["font-style"]="italic"

            elif lowered=="bold":
                out["font-weight"] = "bold"

            elif lowered=="normal":
                out["font-style"] = "normal"
                out["font-weight"] = "normal"
                # out.setdefault("font-style","normal")
                # out.setdefault("font-weight","normal")

            elif lowered.endswith("px") or lowered.endswith("%"):
                out["font-size"] = lowered
                saw_size = True

            else:
                if saw_size:
                    family.append(lowered)

        if family:
            out["font-family"]=" ".join(family)

        return out

    def ignore_until(self,chars):
        while self.i < len(self.s):
            if self.s[self.i] in chars:
                return self.s[self.i]
            self.i+=1

        return None

    def body(self):
        pairs={}

        # Inline style strings may begin with spaces/newlines:
        # style="\n    width: 360px; ..."
        # Unlike stylesheet parsing, body() can be called directly, so it must
        # consume leading whitespace itself or the first declaration is skipped.
        self.whitespace()

        while self.i < len(self.s) and self.s[self.i]!="}":
            try:
                self.whitespace()
                if self.i >= len(self.s) or self.s[self.i] == "}":
                    break

                prop, vals, important=self.pair()

                if prop=="font":
                    expanded = self.font_shorthand(vals)

                    for subprop, subvalue in expanded.items():
                        pairs[subprop]=(subvalue,important)

                else:
                    if len(vals)==1:
                        value=vals[0]
                    else:
                        value=" ".join(vals)
                
                    pairs[prop]=(value,important)

                self.whitespace()

                if self.i < len(self.s) and self.s[self.i]==";":
                    self.literal(";")
                    self.whitespace()

            except Exception:
                why=self.ignore_until([";","}"])
                if why==";":
                    self.literal(";")
                    self.whitespace()
                else:
                    break

        return pairs

    def parenthesized_selector(self):
        self.literal("(")
        
        start=self.i
        depth=1

        while self.i < len(self.s) and depth > 0:
            if self.s[self.i]=="(":
                depth+=1
            
            elif self.s[self.i]==")":
                depth-=1

                if depth==0:
                    break

            self.i+=1

        if depth!=0:
            raise Exception("Parsing error")

        inner=self.s[start:self.i]
        self.literal(")")
        
        parser=CSSParser(inner)
        return parser.selector()

    def simple_selector(self):
        selectors = []

        # optional tag selector
        # ex:
        # span.announce
        # div.card.highlight
        # div.card:has(span)

        # if current scan "." or ":" or "#",it's no tag selector
        if self.i < len(self.s) and self.s[self.i] not in ".:#":
            tag = self.identifier().casefold()
            selectors.append(TagSelector(tag))

        #  class selectors or has selectors
        while self.i < len(self.s):
            if self.s[self.i]==".":
                self.literal(".")
                class_name=self.identifier()
                selectors.append(ClassSelector(class_name))
            
            elif self.s[self.i]==":":
                self.literal(":")
                pseudo = self.identifier().casefold()

                if pseudo=="has":
                    inner_selector = self.parenthesized_selector()
                    selectors.append(HasSelector(inner_selector))
                
                elif pseudo=="visited":
                    selectors.append(VisitedSelector())

                else:
                    raise Exception("Parsing error")


            elif self.s[self.i]=="#":
                self.literal("#")
                id_name = self.identifier()
                selectors.append(IdSelector(id_name))

            else:
                break

        if len(selectors)==0:
            raise Exception("Parsing error")
            
        if len(selectors)==1:
            return selectors[0]


        return SelectorSequence(selectors)

    def selector(self):
        selectors=[self.simple_selector()]
        self.whitespace()

        while self.i < len(self.s) and self.s[self.i] !="{":
            selectors.append(self.simple_selector())
            self.whitespace()

        if len(selectors)==1:
            return selectors[0]
        else:
            return DescendantSelector(selectors)

    def parse(self):
        rules=[]
        while self.i < len(self.s):
            try:
                self.whitespace()
                selector=self.selector()
                self.literal("{")
                self.whitespace()
                body=self.body()
                self.literal("}")
                rules.append((selector,body))
            except Exception:
                why=self.ignore_until(["}"])
                if why=="}":
                    self.literal("}")
                    self.whitespace()
                else:
                    break
        return rules


INHERITED_PROPERTIES = {
    "font-size": "16px",
    "font-style": "normal",
    "font-weight" : "normal",
    "color" : "black",
    "font-family": "Times",
    "text-align":"left",
}

NON_INHERITED_PROPERTIES = {
    "width" : "auto",
    "height" : "auto",
    "display" : "inline",
    "border-radius": "0px",
    "overflow": "visible",
    "opacity": "1.0",
    "mix-blend-mode": "normal",
    "filter": "none",
}

DEFAULT_STYLE_SHEET=CSSParser(open("browser.css").read()).parse()

IMPORTANT_OFFSET = 10000
INLINE_STYLE_PRIORITY = 1000

def apply_style(node,prop,value,priority):
    old_priority=node.style_priority.get(prop,-1)

    # when same priority, last rule cover previous rule
    if priority >= old_priority:
        node.style[prop]=value
        node.style_priority[prop]=priority

def style(node,rules):
    node.style={}
    node.style_priority={}
    
    # first deal with inherited properties
    # if no node specify property, inherit from parent
    for property, default_value in INHERITED_PROPERTIES.items():
        if node.parent:
            node.style[property]=node.parent.style[property]
        else:
            node.style[property]=default_value

        # inheritance value can't inherit important
        node.style_priority[property]=0

    # deal css format width and height default auto
    for property, default_value in NON_INHERITED_PROPERTIES.items():
        node.style[property]=default_value
        node.style_priority[property]=0

    # If is element, picked by CSS selector
    if isinstance(node,Element):
        # first deal with stylesheet rules
        for selector, body in rules:
            if not selector.matches(node):
                continue

            # debug rules
            # print("MATCH",node,selector,body)

            for prop, pair in body.items():
                value, important = pair
                
                priority = selector.priority
                if important:
                    priority += IMPORTANT_OFFSET

                apply_style(node,prop,value,priority)

        # embedded inline style, let inline sytle cover stylesheet
        if "style" in node.attributes:
            pairs=CSSParser(node.attributes["style"]).body()

            for prop, pair in pairs.items():
                value, important = pair
                
                priority = INLINE_STYLE_PRIORITY
                if important:
                    priority += IMPORTANT_OFFSET

                apply_style(node,prop,value,priority)

    #handle CSS inherit keyword
    for property in INHERITED_PROPERTIES:
        if node.style.get(property) == "inherit":
            if node.parent:
                node.style[property]=node.parent.style[property]
            else:
                node.style[property]=INHERITED_PROPERTIES[property]

    # handle unsupported font-size keyword
    font_size=node.style.get("font-size","16px")

    if (not font_size.endswith("px") and not font_size.endswith("%")):
        if node.parent:
            node.style["font-size"]=node.parent.style["font-size"]
        else:
            node.style["font-size"]=INHERITED_PROPERTIES["font-size"]

    # convert the percentage of font-size to px
    # example: 150% -> parent_font_size * 1.5
    if node.style["font-size"].endswith("%"):
        if node.parent:
            parent_font_size=node.parent.style["font-size"]
        else:
            parent_font_size=INHERITED_PROPERTIES["font-size"]

        node_pct=float(node.style["font-size"][:-1]) / 100
        parent_px = float(parent_font_size[:-2])
        node.style["font-size"] = str(node_pct * parent_px) + "px"

    # finally recursively DOM tree
    # because child need inhertied parent already computed style
    for child in node.children:
        style(child,rules)

def print_tree(node,indent=0):
    print(" "*indent,node)
    for child in node.children:
        print_tree(child,indent+2)

class ViewSourceParser(HTMLParser):
    def __init__(self,body):
        super().__init__(body)
        self.output_html=""

    def handle_view_source(self):
        # call father class parse，rewrite add_text and add_tag
        self.parse()
        # final result wrap <pre> and </pre>
        return "<pre>" + self.output_html + "</pre>"

    def add_text(self,text):
        # origin code text content，transferred meaning and bold text
        escaped_text=text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        # escaped_text=text.replace("<","&lt;").replace(">","&gt;")
        self.output_html+="<b>"+escaped_text+"</b>"

    def add_tag(self,tag):
        # origin code text content，transferred meaning and place
        escaped_tag=tag.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        # escaped_tag=tag.replace("<","&lt;").replace(">","&gt;")
        self.output_html+="&lt;" + escaped_tag + "&gt;"

    def implicit_tags(self,tag):
        # in the view-source mode，not need auto fill html/body labels
        # otherwise output source code get more unexist labels
        pass

    def finish(self):
        # rewrite finishd ，because no need DOM trees，just need ending signal
        return None


def show(body):
    
    in_tag = False
    text_buffer ="" # 用來暫存過濾掉標籤後的文字
    for c in body:
        if c == "<":
            in_tag = True
        elif c == ">":
            in_tag = False
        elif not in_tag:
            #非標籤文字
            text_buffer+=c

    #避免 &lt，&gt； 被轉成 <，> 後又被誤認為標籤
    text_buffer=text_buffer.replace("&lt;","<")
    text_buffer=text_buffer.replace("&gt;",">")
    
    print(text_buffer)


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--rtl" in args:
        USE_RTL = True
        args.remove("--rtl")
        print("RTL mode enabled")

    if args:
        url = URL(args[0])
    else:
        url = URL("https://browser.engineering/")

    app = BrowserApp()
    main_window = app.new_window(url)

    print(
        "Initial page load scheduled on",
        main_window.active_tab.task_runner.main_thread.name,
    )

    app.run()
