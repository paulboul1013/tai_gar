LISTENERS = {};

Node.prototype.addEventListener = function (type, listener) {
    if (!LISTENERS[this.handle]) LISTENERS[this.handle] = {};

    var dict = LISTENERS[this.handle];

    if (!dict[type]) dict[type] = [];

    var list = dict[type];
    list.push(listener);
}

function Event(type) {
    this.type = type;

    //default execute browser default action
    this.do_default = true;


    //default make for ancestor bubbling
    this.propagation_stopped = false;

    //first have event's element
    this.target = null;

    //current executing listener's element
    this.currentTarget = null;
}

Event.prototype.preventDefault = function () {
    this.do_default = false;
}

Event.prototype.stopPropagation = function () {
    this.propagation_stopped = true;
};

Node.prototype.dispatchEvent = function (event) {
    var type = event.type;
    var handle = this.handle;
    var list = (LISTENERS[handle] && LISTENERS[handle][type]) || [];

    //dealing with event node right now
    event.currentTarget = this;

    for (var i = 0; i < list.length; i++) {
        list[i].call(this, event);
    }

    return event.do_default;
}

function dispatch_event_path(type, handles) {
    var event = new Event(type);

    if (handles.length === 0) {
        return true;
    }

    // handles's order 
    /*
        [
            target,
            parent,
            grandparent
        ]
    */

    event.target = new Node(handles[0]);

    for (var i = 0; i < handles.length; i++) {
        var current = new Node(handles[i]);

        current.dispatchEvent(event);

        //when current node listeners all done
        //and then check need to bubble up
        if (event.propagation_stopped) {
            break;
        }
    }

    return event.do_default;
}

function log(x) {
    call_python("log", x);
}

//global window object
var window = this;

//record from browser auto build ID global variable
//like :
/*
<div id="foo"></div> will build 
window.foo = new Node(handle);
*/
var ID_GLOBALS = {};

function sync_id_globals(entries) {
    //first remove last time build ID global variable from browser

    for (var name in ID_GLOBALS) {
        if (window[name] === ID_GLOBALS[name]) {
            delete window[name];
        }
    }

    ID_GLOBALS = {};

    /* entries format:
        [
            ["foo",3],
            ["message",9]
        ]
    */

    for (var i = 0; i < entries.length; i++) {
        var name = entries[i][0];
        var handle = entries[i][1];

        /*
            don't overwrite document,window,Node,Event,
            or already builed global variable
        */

        if (name in window) {
            continue;
        }

        var node = new Node(handle);

        window[name] = node;
        ID_GLOBALS[name] = node;
    }

}

function Node(handle) {
    this.handle = handle;
}

Node.prototype.getAttribute = function (attr) {
    return call_python("getAttribute", this.handle, attr)
}

Node.prototype.setAttribute = function (name, value) {
    call_python(
        "setAttribute",
        this.handle,
        name.toString(),
        value.toString()
    );
};

Object.defineProperty(Node.prototype, "id", {
    get: function () {
        return this.getAttribute("id");
    },

    set: function (value) {
        this.setAttribute("id", value.toString());
    }
});

Node.prototype.appendChild = function (child) {
    call_python(
        "appendChild",
        this.handle,
        child.handle
    );

    return child;
};

Node.prototype.insertBefore = function (new_child, reference_child) {
    var reference_handle = null;

    if (reference_child !== null) {
        reference_handle = reference_child.handle;
    }

    call_python(
        "insertBefore",
        this.handle,
        new_child.handle,
        reference_handle
    );

    return new_child;
};

Node.prototype.removeChild = function (child) {
    call_python(
        "removeChild",
        this.handle,
        child.handle
    );

    return child;
}

Object.defineProperty(Node.prototype, "children", {
    get: function () {
        var handles = call_python("children", this.handle);
        return handles.map(function (h) {
            return new Node(h);
        });
    }
});

Object.defineProperty(Node.prototype, "innerHTML", {
    get: function () {
        return call_python(
            "innerHTML_get",
            this.handle
        );
    },

    set: function (s) {
        call_python(
            "innerHTML_set",
            this.handle,
            s.toString()
        );
    }
});

Object.defineProperty(Node.prototype, "outerHTML", {
    get: function () {
        return call_python(
            "outerHTML_get",
            this.handle
        );
    }
});

document = {
    querySelectorAll: function (s) {
        var handles = call_python("querySelectorAll", s);
        return handles.map(function (h) {
            return new Node(h);
        });
    },

    createElement: function (tag_name) {
        var handle = call_python(
            "createElement",
            tag_name
        );

        return new Node(handle);
    }
};