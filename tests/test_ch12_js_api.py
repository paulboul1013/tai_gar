"""Chapter 12 contracts through the real interpreter, runtime, and Python bridge.

Only timer expiration and network I/O are controlled; no SDL window is created.
Run from the repository root with unittest discovery (see README.md).
"""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import dukpy

import browser


WAIT_SECONDS = 3


class NullMeasure:
    def time(self, name):
        pass

    def stop(self, name):
        pass


class ControlledNetwork:
    def __init__(self):
        self.pending = []

    def submit(self, work, on_complete=None, **kwargs):
        self.pending.append(browser.NetworkTask(work, on_complete=on_complete, **kwargs))
        return True

    def run_sync(self, work, **kwargs):
        return work()

    def complete(self, index=0):
        task = self.pending.pop(index)
        try:
            result, error = task.work(), None
        except Exception as exc:
            result, error = None, exc
        task.on_complete(result, error)


class ControlledTimer:
    def __init__(self, delay, callback):
        self.delay = delay
        self.callback = callback
        self.daemon = False
        self.started = False

    def start(self):
        self.started = True

    def fire(self):
        self.callback()


class RuntimeTestCase(unittest.TestCase):
    def setUp(self):
        self.network = ControlledNetwork()
        self.frame_requested = False
        self.host = SimpleNamespace(
            measure=NullMeasure(),
            app=SimpleNamespace(network=self.network),
            set_needs_animation_frame=self.request_frame,
        )
        self.tab = browser.Tab(self.host, 800, 600, set(), [])
        self.tab.url = browser.URL("https://example.test/page")
        self.tab.allowed_origins = None
        self.tab.nodes = browser.HTMLParser('<div id="target">test</div>').parse()
        self.contexts = []
        self.interval_workers = []
        self.js = self.new_context()
        self.timers = []
        self.timer_patch = patch.object(browser.threading, "Timer", self.make_timer)
        self.timer_patch.start()
        self.addCleanup(self.timer_patch.stop)

    def request_frame(self, tab):
        self.assertIs(tab, self.tab)
        self.frame_requested = True

    def new_context(self):
        context = browser.JSContext(self.tab)
        self.contexts.append(context)
        self.tab.js = context
        return context

    def make_timer(self, delay, callback):
        timer = ControlledTimer(delay, callback)
        self.timers.append(timer)
        return timer

    def run_in_worker(self, function):
        errors = []

        def run():
            try:
                function()
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=run, name="Controlled expiration/completion")
        worker.start()
        worker.join(timeout=WAIT_SECONDS)
        self.assertFalse(worker.is_alive(), "controlled worker did not finish")
        if errors:
            raise errors[0]

    def on_main(self, function):
        """Run and query the interpreter only on the real Tab main thread."""
        done = threading.Event()
        result = []
        errors = []

        def run():
            try:
                result.append(function())
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        self.assertTrue(self.tab.task_runner.schedule_task(browser.Task(run)))
        self.assertTrue(done.wait(WAIT_SECONDS), "Tab main thread did not finish")
        if errors:
            raise errors[0]
        return result[0]

    def tearDown(self):
        workers = list(self.interval_workers)
        for context in self.contexts:
            with context.interval_lock:
                workers.extend(state["thread"] for state in context.intervals.values())
            context.discard()
        self.tab.task_runner.set_needs_quit()
        self.tab.task_runner.join_thread(timeout=WAIT_SECONDS)
        for worker in set(workers):
            worker.join(timeout=WAIT_SECONDS)
            self.assertFalse(worker.is_alive(), "interval worker leaked")
        self.assertFalse(self.tab.task_runner.main_thread.is_alive())

    def drain_tasks(self):
        runner = self.tab.task_runner
        self.assertFalse(runner.started)
        while True:
            with runner.condition:
                picked = runner._pick_next_task_locked(browser.time.perf_counter())
            if picked is None:
                return
            picked[0].run(self.host.measure)

    def track_intervals(self):
        with self.js.interval_lock:
            self.interval_workers.extend(state["thread"] for state in self.js.intervals.values())

    def queue_tick(self, handle):
        state = self.js.intervals[handle]
        deadline = state["created_at"] + state["delay"]
        self.tab.task_runner.schedule_task(browser.Task(
            self.js.dispatch_setinterval, handle, deadline, deadline, 1,
            priority=browser.TaskPriority.JS_TIMER,
        ))


class RuntimeContractTests(RuntimeTestCase):
    def test_chapter_12_api_contract(self):
        names = [
            "setTimeout", "runSetTimeout", "setInterval", "clearInterval",
            "runSetInterval", "XMLHttpRequest", "runXHROnload",
            "requestAnimationFrame", "runRAFHandlers",
        ]
        missing = self.js.evaljs(
            'dukpy.names.filter(function (name) { return typeof window[name] !== "function"; })',
            names=names,
        )
        self.assertEqual(missing, [], "Chapter 12 JS APIs missing: " + ", ".join(missing))

    def test_async_open_is_accepted(self):
        self.js.evaljs('var xhr = new XMLHttpRequest(); xhr.open("GET", "/probe", true);')

    def test_raf_coalesces_and_defers_nested_callbacks(self):
        self.js.evaljs('''
            var frames = [];
            requestAnimationFrame(function () {
                frames.push("first");
                requestAnimationFrame(function () { frames.push("next"); });
            });
            requestAnimationFrame(function () { frames.push("second"); });
        ''')
        self.assertEqual(self.js.evaljs("frames"), [])
        self.assertTrue(self.frame_requested)
        self.js.evaljs(browser.RAF_JS)
        self.assertEqual(self.js.evaljs("frames"), ["first", "second"])
        self.js.evaljs(browser.RAF_JS)
        self.assertEqual(self.js.evaljs("frames"), ["first", "second", "next"])


class TimeoutTests(RuntimeTestCase):
    def test_expiration_only_enqueues_and_consumes_callback_once(self):
        handles = self.js.evaljs('''
            var calls = [];
            var first = setTimeout(function () { calls.push("first"); }, 0);
            var second = setTimeout(function () { calls.push("second"); }, 10);
            [first, second];
        ''')
        self.assertNotEqual(*handles)
        self.assertEqual([timer.delay for timer in self.timers], [0, 0.01])
        self.assertTrue(all(timer.started for timer in self.timers))
        self.run_in_worker(self.timers[0].fire)
        self.assertEqual(self.js.evaljs("calls"), [])
        self.drain_tasks()
        self.assertEqual(self.js.evaljs("calls"), ["first"])
        self.assertFalse(self.js.evaljs(
            "Object.prototype.hasOwnProperty.call(TIMEOUT_CALLBACKS, dukpy.handle)",
            handle=handles[0],
        ))
        self.js.evaljs("runSetTimeout(dukpy.handle); runSetTimeout(999999);", handle=handles[0])
        self.assertEqual(self.js.evaljs("calls"), ["first"])
        self.run_in_worker(self.timers[1].fire)
        self.drain_tasks()
        self.assertEqual(self.js.evaljs("calls"), ["first", "second"])

    def test_callback_can_schedule_another_timeout(self):
        self.js.evaljs('''
            var calls = [];
            setTimeout(function () {
                calls.push("outer");
                setTimeout(function () { calls.push("inner"); }, 0);
            }, 0);
        ''')
        self.timers[0].fire()
        self.drain_tasks()
        self.assertEqual(self.js.evaljs("calls"), ["outer"])
        self.timers[1].fire()
        self.drain_tasks()
        self.assertEqual(self.js.evaljs("calls"), ["outer", "inner"])

    def test_throwing_callback_is_consumed_before_execution(self):
        handle = self.js.evaljs('''
            var calls = 0;
            setTimeout(function () { calls++; throw Error("timeout probe"); }, 0);
        ''')
        with self.assertRaisesRegex(dukpy.JSRuntimeError, "timeout probe"):
            self.js.evaljs("runSetTimeout(dukpy.handle)", handle=handle)
        self.js.evaljs("runSetTimeout(dukpy.handle)", handle=handle)
        self.assertEqual(self.js.evaljs("calls"), 1)

    def test_zero_delay_runs_after_current_task_on_tab_main_thread(self):
        recorded = []
        done = threading.Event()

        def record(value):
            recorded.append((value, threading.get_ident()))
            if value == "timeout":
                done.set()

        self.js.interp.export_function("record", record)
        self.js.interp.export_function("expire", lambda: self.run_in_worker(self.timers[-1].fire))
        self.tab.task_runner.start_thread()
        self.on_main(lambda: self.js.evaljs('''
            setTimeout(function () { call_python("record", "timeout"); }, 0);
            call_python("expire");
            call_python("record", "script-end");
        '''))
        self.assertTrue(done.wait(WAIT_SECONDS))
        self.assertEqual([value for value, _ in recorded], ["script-end", "timeout"])
        self.assertEqual({ident for _, ident in recorded}, {self.tab.task_runner.main_thread.ident})


class IntervalTests(RuntimeTestCase):
    def test_timer_callbacks_preserve_global_receiver(self):
        handle = self.js.evaljs('''
            var receivers = [];
            setTimeout(function () { receivers.push(this === window); }, 0);
            setInterval(function () { receivers.push(this === window); }, 60000);
        ''')
        self.track_intervals()
        self.timers[0].fire()
        self.queue_tick(handle)
        self.drain_tasks()
        self.assertEqual(self.js.evaljs("receivers"), [True, True])

    def test_intervals_are_independent_and_share_timer_handle_source(self):
        handles = self.js.evaljs('''
            var calls = [];
            var timeout = setTimeout(function () { calls.push("timeout"); }, 0);
            var first = setInterval(function () { calls.push("first"); }, 60000);
            var second = setInterval(function () { calls.push("second"); }, 60000);
            [timeout, first, second];
        ''')
        self.track_intervals()
        self.assertEqual(len(set(handles)), 3)
        self.queue_tick(handles[1])
        self.queue_tick(handles[2])
        self.queue_tick(handles[1])
        self.drain_tasks()
        self.assertEqual(self.js.evaljs("calls"), ["first", "second", "first"])
        self.js.evaljs("clearInterval(first)")
        self.timers[0].fire()
        self.queue_tick(handles[2])
        self.drain_tasks()
        self.assertEqual(self.js.evaljs("calls"), ["first", "second", "first", "timeout", "second"])
        newer = self.js.evaljs("setInterval(function () {}, 60000)")
        self.track_intervals()
        self.assertGreater(newer, max(handles))

    def test_cancel_makes_queued_ticks_and_unknown_handles_harmless(self):
        handle = self.js.evaljs('''
            var calls = 0;
            var interval = setInterval(function () { calls++; }, 60000);
            interval;
        ''')
        self.track_intervals()
        state = self.js.intervals[handle]
        self.queue_tick(handle)
        self.js.evaljs("clearInterval(interval); clearInterval(interval); clearInterval(999999);")
        self.assertTrue(state["stop_event"].is_set())
        self.assertNotIn(handle, self.js.intervals)
        self.drain_tasks()
        self.js.evaljs("runSetInterval(interval); runSetInterval(999999);")
        self.assertEqual(self.js.evaljs("calls"), 0)

    def test_callback_can_cancel_itself(self):
        handle = self.js.evaljs('''
            var calls = 0;
            var interval = setInterval(function () { calls++; clearInterval(interval); }, 60000);
            interval;
        ''')
        self.track_intervals()
        state = self.js.intervals[handle]
        self.queue_tick(handle)
        self.queue_tick(handle)
        self.drain_tasks()
        self.assertEqual(self.js.evaljs("calls"), 1)
        self.assertTrue(state["stop_event"].is_set())

    def test_real_worker_only_enqueues_and_stops_on_cancel(self):
        handle = self.js.evaljs('''
            var calls = 0;
            var interval = setInterval(function () { calls++; }, 1);
            interval;
        ''')
        self.track_intervals()
        runner = self.tab.task_runner
        with runner.condition:
            self.assertTrue(runner.condition.wait_for(
                lambda: bool(runner.queues[browser.TaskPriority.JS_TIMER]), WAIT_SECONDS,
            ))
        self.assertEqual(self.js.evaljs("calls"), 0)
        self.js.evaljs("clearInterval(interval)")
        self.drain_tasks()
        self.assertEqual(self.js.evaljs("calls"), 0)
        self.interval_workers[0].join(timeout=WAIT_SECONDS)
        self.assertFalse(self.interval_workers[0].is_alive())

    def test_real_worker_dispatches_on_tab_main_thread(self):
        done = threading.Event()
        identities = []

        def record():
            identities.append(threading.get_ident())
            done.set()

        self.js.interp.export_function("record", record)
        self.tab.task_runner.start_thread()

        def start_interval():
            self.js.evaljs('''
                var interval = setInterval(function () {
                    clearInterval(interval);
                    call_python("record");
                }, 1);
            ''')
            self.track_intervals()

        self.on_main(start_interval)
        self.assertTrue(done.wait(WAIT_SECONDS))
        self.assertEqual(identities, [self.tab.task_runner.main_thread.ident])


class XHRTests(RuntimeTestCase):
    def test_real_network_completion_runs_on_tab_main_thread(self):
        network = browser.NetworkTaskRunner(self.host.measure)
        self.host.app.network = network
        started = threading.Event()
        release = threading.Event()
        done = threading.Event()
        io_threads = []
        routing_threads = []
        callback_threads = []

        def request(url, source, body, **kwargs):
            io_threads.append(threading.current_thread())
            started.set()
            if not release.wait(WAIT_SECONDS):
                raise RuntimeError("network release timed out")
            return {}, "threaded body"

        def record(body, event_type, correct_receiver):
            callback_threads.append((threading.get_ident(), body, event_type, correct_receiver))
            done.set()

        finish_task = network._finish_task

        def finish(*args):
            routing_threads.append(threading.get_ident())
            finish_task(*args)

        self.js.interp.export_function("record", record)
        network.start_thread()
        self.tab.task_runner.start_thread()
        try:
            with patch.object(browser.URL, "request", request), patch.object(network, "_finish_task", finish):
                self.on_main(lambda: self.js.evaljs('''
                    var xhr = new XMLHttpRequest();
                    xhr.open("GET", "/threaded", true);
                    xhr.onload = function (event) {
                        call_python("record", this.responseText, event.type, this === xhr);
                    };
                    xhr.send();
                '''))
                self.assertTrue(started.wait(WAIT_SECONDS))
                self.assertEqual(self.on_main(lambda: self.js.evaljs("xhr.responseText")), None)
                self.assertEqual(callback_threads, [])
                release.set()
                self.assertTrue(done.wait(WAIT_SECONDS))
                self.on_main(lambda: None)  # Wait until the onload task has returned.
            self.assertEqual(callback_threads, [(
                self.tab.task_runner.main_thread.ident, "threaded body", "load", True,
            )])
            self.assertEqual(routing_threads, [network.network_thread.ident])
            self.assertNotIn(io_threads[0].ident, [network.network_thread.ident, self.tab.task_runner.main_thread.ident])
        finally:
            release.set()
            network.set_needs_quit()
            network.join_thread(timeout=WAIT_SECONDS)
            for worker in io_threads:
                worker.join(timeout=WAIT_SECONDS)
                self.assertFalse(worker.is_alive(), "network I/O worker leaked")
            self.assertFalse(network.network_thread.is_alive(), "network coordinator leaked")

    def test_sync_send_returns_response_and_normalizes_omitted_body(self):
        send = unittest.mock.Mock(wraps=self.js.XMLHttpRequest_send)
        self.js.interp.export_function("XMLHttpRequest_send", send)
        with patch.object(browser.URL, "request", return_value=({}, "sync body")) as request:
            result = self.js.evaljs('''
                var loads = 0;
                var xhr = new XMLHttpRequest();
                xhr.open("GET", "/sync");
                xhr.onload = function () { loads++; };
                var result = xhr.send();
                [result, xhr.responseText, loads];
            ''')
        self.assertEqual(result, ["sync body", "sync body", 0])
        self.assertEqual(send.call_args.args, ("GET", "/sync", None, False, 0))
        self.assertEqual(request.call_args.args, (self.tab.url, None))
        self.assertEqual(self.network.pending, [])
        with patch.object(browser.URL, "request", return_value=({}, "post body")):
            self.js.evaljs('xhr.open("POST", "/sync", false); xhr.send("payload");')
        self.assertEqual(send.call_args.args, ("POST", "/sync", "payload", False, 0))
        self.assertEqual(self.js.evaljs("xhr.responseText"), "post body")

    def test_async_reverse_completion_routes_to_correct_object_and_load_event(self):
        recorded = []
        self.js.interp.export_function("record", lambda value: recorded.append((value, threading.get_ident())))
        self.js.evaljs('''
            var first = new XMLHttpRequest();
            var second = new XMLHttpRequest();
            first.open("GET", "/first", true);
            second.open("POST", "/second", true);
            first.onload = function (event) {
                call_python("record", ["first", this.responseText, event.type, this === first]);
            };
            second.onload = function (event) {
                call_python("record", ["second", this.responseText, event.type, this === second]);
            };
            var firstReturn = first.send();
            var secondReturn = second.send("payload");
        ''')
        self.assertEqual(self.js.evaljs("[first.responseText, second.responseText]"), [None, None])
        self.assertEqual(len(self.network.pending), 2)
        self.assertEqual(recorded, [])
        request_calls = []

        def request(url, source, body, **kwargs):
            request_calls.append((url.path, body))
            return {}, url.path + " body"

        with patch.object(browser.URL, "request", request):
            self.run_in_worker(lambda: self.network.complete(1))
            self.assertEqual(recorded, [])
            self.assertEqual(self.js.evaljs("second.responseText"), None)
            self.drain_tasks()
            self.assertEqual([value for value, _ in recorded], [["second", "/second body", "load", True]])
            self.run_in_worker(self.network.complete)
            self.drain_tasks()
        self.assertEqual([value for value, _ in recorded], [
            ["second", "/second body", "load", True],
            ["first", "/first body", "load", True],
        ])
        self.assertEqual(request_calls, [("/second", "payload"), ("/first", None)])
        self.assertEqual({ident for _, ident in recorded}, {threading.get_ident()})

    def test_completion_without_onload_and_unknown_handle(self):
        self.js.evaljs('''
            var xhr = new XMLHttpRequest();
            xhr.open("GET", "/body", true);
            xhr.send();
        ''')
        with patch.object(browser.URL, "request", return_value=({}, "body")):
            self.network.complete()
        self.drain_tasks()
        self.assertEqual(self.js.evaljs("xhr.responseText"), "body")
        self.js.evaljs('runXHROnload("unknown", 999999)')

    def test_csp_rejects_before_network_submission(self):
        self.tab.allowed_origins = [self.tab.url.origin()]
        with patch.object(browser.URL, "request") as request:
            for is_async in [False, True]:
                with self.subTest(is_async=is_async):
                    with self.assertRaisesRegex(dukpy.JSRuntimeError, "blocked by CSP"):
                        self.js.evaljs('''
                            var xhr = new XMLHttpRequest();
                            xhr.open("GET", "https://other.test/data", dukpy.is_async);
                            xhr.send();
                        ''', is_async=is_async)
            request.assert_not_called()
        self.assertEqual(self.network.pending, [])

    def test_cors_accepts_matching_origin_or_wildcard(self):
        for allowed in [self.tab.url.origin(), "*"]:
            with self.subTest(allowed=allowed):
                self.js.evaljs('''
                    var loads = 0;
                    var xhr = new XMLHttpRequest();
                    xhr.open("GET", "https://other.test/data", true);
                    xhr.onload = function () { loads++; };
                    xhr.send();
                ''')
                with patch.object(browser.URL, "request", return_value=(
                    {"access-control-allow-origin": allowed}, "cors body",
                )) as request:
                    self.network.complete()
                self.drain_tasks()
                self.assertEqual(self.js.evaljs("[xhr.responseText, loads]"), ["cors body", 1])
                self.assertEqual(request.call_args.kwargs["origin"], self.tab.url.origin())

    def test_cors_and_network_failures_do_not_dispatch_success(self):
        for outcome in [({}, "denied"), ({"access-control-allow-origin": "https://wrong.test:443"}, "denied"), OSError("network probe")]:
            with self.subTest(outcome=outcome):
                self.js.evaljs('''
                    var loads = 0;
                    var xhr = new XMLHttpRequest();
                    xhr.open("GET", "https://other.test/data", true);
                    xhr.onload = function () { loads++; };
                    xhr.send();
                ''')
                kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
                with patch.object(browser.URL, "request", **kwargs), patch("builtins.print"):
                    self.network.complete()
                self.drain_tasks()
                self.assertEqual(self.js.evaljs("[xhr.responseText, loads]"), [None, 0])

    def test_request_captures_source_document_before_completion(self):
        source = self.tab.url
        self.tab.referrer_policy = "same-origin"
        self.js.evaljs('''
            var xhr = new XMLHttpRequest();
            xhr.open("GET", "/relative", true);
            xhr.send();
        ''')
        self.tab.url = browser.URL("https://new.test/page")
        self.tab.referrer_policy = "no-referrer"
        calls = []

        def request(url, referrer, body, **kwargs):
            calls.append((str(url), referrer, body, kwargs))
            return {}, "captured"

        with patch.object(browser.URL, "request", request):
            self.network.complete()
        self.drain_tasks()
        self.assertEqual(calls, [("https://example.test/relative", source, None, {
            "origin": None, "referrer_policy": "same-origin",
        })])


class LifecycleTests(RuntimeTestCase):
    def make_window(self):
        window = browser.BrowserWindow.__new__(browser.BrowserWindow)
        window.lock = threading.RLock()
        window._closed = False
        window.tabs = [self.tab]
        window.animation_timer = None
        window.frame_clock = browser.AdaptiveFrameClock()
        window.raster_id = 1
        window.sdl_window = None
        window.gl_context = None
        window.app = SimpleNamespace(
            raster=SimpleNamespace(discard_window=lambda raster_id: None),
            unregister_window=lambda closed_window: None,
        )
        return window

    def test_close_blocks_interval_registration_from_in_flight_javascript(self):
        window = self.make_window()
        entered = threading.Event()
        resume = threading.Event()
        discarded = threading.Event()
        finished = threading.Event()
        errors = []

        def pause():
            entered.set()
            if not resume.wait(WAIT_SECONDS):
                raise RuntimeError("close race resume timed out")

        def work():
            try:
                self.js.evaljs('''
                    call_python("pause_for_close");
                    setInterval(function () {}, 60000);
                ''')
                self.track_intervals()
            except BaseException as exc:
                errors.append(exc)
            finally:
                finished.set()

        original_discard = self.js.discard

        def signal_discard():
            original_discard()
            discarded.set()

        def close():
            try:
                window.close()
            except BaseException as exc:
                errors.append(exc)

        closer = threading.Thread(target=close, name="UI close test")
        self.js.interp.export_function("pause_for_close", pause)
        self.tab.task_runner.start_thread()
        try:
            self.tab.task_runner.schedule_task(browser.Task(work))
            self.assertTrue(entered.wait(WAIT_SECONDS))
            with patch.object(self.js, "discard", signal_discard):
                closer.start()
                self.assertTrue(discarded.wait(WAIT_SECONDS))
                resume.set()
                self.assertTrue(finished.wait(WAIT_SECONDS))
                closer.join(timeout=WAIT_SECONDS)
            self.assertFalse(closer.is_alive())
            self.assertEqual(errors, [])
            self.assertFalse(self.tab.task_runner.main_thread.is_alive())
            self.assertTrue(self.js.discarded)
            self.assertEqual(self.js.intervals, {}, "in-flight JS registered an interval after close")
            self.assertEqual(self.interval_workers, [])
        finally:
            resume.set()
            if closer.ident is not None:
                closer.join(timeout=WAIT_SECONDS)

    def start_old_document_work(self):
        self.js.evaljs('''
            var calls = [];
            function changeOldPage() { calls.push("old"); target.style = "opacity: 0"; }
            var timeout = setTimeout(changeOldPage, 0);
            setTimeout(changeOldPage, 0);
            var interval = setInterval(changeOldPage, 60000);
            var early = new XMLHttpRequest();
            var late = new XMLHttpRequest();
            early.open("GET", "/early", true);
            late.open("GET", "/late", true);
            early.onload = changeOldPage;
            late.onload = changeOldPage;
            early.send();
            late.send();
        ''')
        self.track_intervals()
        self.timers[0].fire()
        self.queue_tick(self.js.evaljs("interval"))
        with patch.object(browser.URL, "request", return_value=({}, "early body")):
            self.network.complete()

    def test_navigation_drops_queued_and_late_callbacks_and_new_context_works(self):
        self.start_old_document_work()
        old = self.js
        old_workers = list(self.interval_workers)
        self.tab.load(browser.URL("https://example.test/new-page"))
        self.assertTrue(old.discarded)
        for worker in old_workers:
            worker.join(timeout=WAIT_SECONDS)
            self.assertFalse(worker.is_alive(), "navigation must stop old interval workers")
        with patch.object(browser.URL, "request", return_value=({}, '<div id="target">new</div>')):
            self.network.complete(1)  # Document completes before the late old XHR.
        with patch.object(old, "evaljs", side_effect=AssertionError("discarded context executed")):
            self.drain_tasks()
            self.timers[1].fire()
            with patch.object(browser.URL, "request", return_value=({}, "late body")):
                self.run_in_worker(self.network.complete)
            self.drain_tasks()
        self.js = self.tab.js
        self.contexts.append(self.js)
        self.assertIsNot(self.js, old)
        self.assertEqual(self.js.evaljs("target.style"), "")
        self.js.evaljs('''
            var calls = [];
            setTimeout(function () { calls.push("timeout"); }, 0);
            var interval = setInterval(function () { calls.push("interval"); clearInterval(interval); }, 60000);
            var xhr = new XMLHttpRequest();
            xhr.open("GET", "/new-xhr", true);
            xhr.onload = function () { calls.push(this.responseText); };
            xhr.send();
        ''')
        self.track_intervals()
        self.timers[2].fire()
        self.queue_tick(self.js.evaljs("interval"))
        with patch.object(browser.URL, "request", return_value=({}, "new-xhr")):
            self.network.complete()
        self.drain_tasks()
        self.assertEqual(sorted(self.js.evaljs("calls")), ["interval", "new-xhr", "timeout"])

    def test_tab_discard_blocks_queued_and_late_callbacks(self):
        self.start_old_document_work()
        old = self.js
        self.tab.discard()
        self.assertTrue(old.discarded)
        with patch.object(old, "evaljs", side_effect=AssertionError("discarded context executed")):
            old.dispatch_settimeout(0)
            old.dispatch_xhr_onload("queued body", 0)
            self.timers[1].fire()
            with patch.object(browser.URL, "request", return_value=({}, "late body")):
                self.network.complete()
            self.drain_tasks()

    def test_window_close_discards_context_and_stops_long_interval_immediately(self):
        self.start_old_document_work()
        state = next(iter(self.js.intervals.values()))
        window = self.make_window()
        window.close()
        self.assertTrue(self.js.discarded, "closing a window must invalidate its JS context")
        self.assertTrue(state["stop_event"].is_set(), "close must stop long intervals without waiting for a tick")
        state["thread"].join(timeout=WAIT_SECONDS)
        self.assertFalse(state["thread"].is_alive())
        with patch.object(self.js, "evaljs", side_effect=AssertionError("closed context executed")):
            self.timers[1].fire()
            with patch.object(browser.URL, "request", return_value=({}, "late body")):
                self.network.complete()
            self.js.dispatch_settimeout(0)
            self.js.dispatch_xhr_onload("queued body", 0)
        window.close()  # Repeated close is safe.


class RAFCoexistenceTests(RuntimeTestCase):
    def test_raf_runs_before_render_and_nested_raf_waits_for_next_frame(self):
        order = []
        render_styles = []
        self.js.interp.export_function("record", order.append)

        def render():
            order.append("render")
            render_styles.append(self.js.evaljs("target.style"))
            self.tab.needs_render = False

        self.tab.render = render
        self.host.commit = lambda tab, data: order.append("commit") or True
        self.host.finish_animation_frame = lambda tab: order.append("finish")
        self.js.evaljs('''
            var interval = setInterval(function () { call_python("record", "interval"); clearInterval(interval); }, 60000);
            setTimeout(function () { call_python("record", "timeout"); }, 0);
            var xhr = new XMLHttpRequest();
            xhr.open("GET", "/frame", true);
            xhr.onload = function () { call_python("record", "xhr"); };
            xhr.send();
            requestAnimationFrame(function () {
                call_python("record", "raf-first");
                target.style = "opacity: 0.5";
                requestAnimationFrame(function () { call_python("record", "raf-next"); });
            });
            requestAnimationFrame(function () { call_python("record", "raf-second"); });
        ''')
        self.track_intervals()
        self.timers[0].fire()
        self.queue_tick(self.js.evaljs("interval"))
        with patch.object(browser.URL, "request", return_value=({}, "frame body")):
            self.network.complete()
        self.tab.task_runner.schedule_task(browser.Task(
            self.tab.run_animation_frame, priority=browser.TaskPriority.RENDER,
        ))
        self.drain_tasks()
        self.assertEqual(order[:5], ["raf-first", "raf-second", "render", "commit", "finish"])
        self.assertEqual(set(order[5:]), {"interval", "timeout", "xhr"})
        self.assertEqual(render_styles, ["opacity: 0.5"])
        self.assertEqual(self.js.handle_to_node[self.js.evaljs("target.handle")].attributes["style"], "opacity: 0.5")
        order.clear()
        self.tab.run_animation_frame()
        self.assertEqual(order, ["raf-next", "render", "commit", "finish"])
        self.frame_requested = False
        self.js.evaljs('target.style = "opacity: 1"')
        self.assertTrue(self.tab.needs_render)
        self.assertTrue(self.frame_requested)


if __name__ == "__main__":
    unittest.main()
