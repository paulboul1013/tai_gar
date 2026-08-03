import socket
import urllib.parse
from html import escape
import os
import json
import secrets
import hmac
from datetime import datetime,timedelta,timezone
from email.utils import format_datetime

DATA_FILE = "message_board.json"

COOKIE_NAME = "token"

# origins allowed to read response through CORS
# this web server port is 8000
CORS_ALLOWED_ORIGINS = {
    "http://localhost:8001",
    "http://127.0.0.1:8001"
}

# every time get valid request,extend session time 
SESSION_DURATION = timedelta(seconds=30)

# token -> {
#   "data": session data,
#   "expires": expiration datetime
# }
SESSIONS = {}

def cleanup_expired_sessions(now):
    expired_tokens = [
        token
        for token,session_record in SESSIONS.items()
        if session_record["expires"] <= now
    ]

    for token in expired_tokens:
        del SESSIONS[token]

    if expired_tokens:
        print("Deleted {} expired session(s)".format(len(expired_tokens)))

    
def cors_allow_origin(headers):
    origin = headers.get("origin")

    # normal surf or same origin request may not have origin header
    if origin is None:
        return None

    # specified value '*' can allow all origins
    if "*" in CORS_ALLOWED_ORIGINS:
        return "*"

    # allow origin from list
    if origin in CORS_ALLOWED_ORIGINS:
        return origin

    # not allow origin
    return None

LOGINS = {
    "paul":"123",
    "admin":"password"
}

DEFAULT_TOPICS = {
    "cooking" :[
        {
            "text":"pavel made soup",
            "author":"paul",
        },
    ],
    "cars" :[
        {
            "text":"Toyota is reliable",
            "author":"admin",
        },
    ],
}

def default_topics_copy():
    return {
        topic: [
            message.copy()
            for message in messages
        ]
        for topic,messages in DEFAULT_TOPICS.items()
    }

def load_topics():
    if not os.path.exists(DATA_FILE):
        return default_topics_copy()

    try:
        with open(DATA_FILE,"r",encoding="utf8") as f:
            data = json.load(f)
    except Exception as e:
        print("Failed to load data file:",e)
        return default_topics_copy()

    if not isinstance(data,dict):
        return default_topics_copy()

    topics ={}

    for topic,messages in data.items():
        if not isinstance(topic,str):
            continue
    
        if not isinstance(messages,list):
            continue

        clean_messages = []

        for message in messages:
            # old format
            # "Hello"
            if isinstance(message,str):
                clean_messages.append({
                    "text":message,
                    "author":"anonymous"
                })
                continue

            # new format
            # {"text":"hello","author":"paul"}
            if isinstance(message,dict):
                text = message.get("text")
                author = message.get("author")
                
                if not isinstance(text,str):
                    continue

                if not isinstance(author,str):
                    continue

                clean_messages.append({
                    "text":text,
                    "author":author
                })

        topics[topic] = clean_messages


    return topics


TOPICS = load_topics()

def save_topics():
    temp_file = DATA_FILE + ".tmp"

    with open(temp_file,"w",encoding="utf8") as f:
        json.dump(TOPICS,f,ensure_ascii=False,indent=2)

    os.replace(temp_file,DATA_FILE)

def parse_cookies(cookie_header):
    cookies = {}

    if not cookie_header:
        return cookies

    # Cookie: token=abc; theme=dark
    for part in cookie_header.split(";"):
        part = part.strip()

        if "=" not in part:
            continue

        name, value = part.split("=",1)
        
        name = name.strip()
        value = value.strip()

        if name:
            cookies[name] = value

    return cookies


def valid_token(token):
    if not isinstance(token,str):
        return False

    # secrets.token_hex(32) -> 64 hex chars
    if len(token) != 64:
        return False

    try:
        int(token,16)
    except ValueError:
        return False

    return True

def create_nonce(session):
    nonce = secrets.token_hex(32) #create nonce value
    session["nonce"] = nonce
    return nonce

def valid_nonce(sesson,params):
    if "nonce" not in sesson:
        return False

    if "nonce" not in params:
        return False

    return hmac.compare_digest( # compare nonce value
        sesson["nonce"],
        params["nonce"]
    )

def handle_connection(conx):
    req = conx.makefile("b")
    reqline = req.readline().decode('utf8')

    if not reqline:
        conx.close()
        return
    
    
    method, url, version = reqline.split(" ", 2)
    assert method in ["GET", "POST"]

    headers = {}

    while True:
        line = req.readline().decode('utf8')

        if line == '\r\n': 
            break

        header, value = line.split(":", 1)

        headers[header.casefold()] = value.strip()

    if 'content-length' in headers:
        length = int(headers['content-length'])
        body = req.read(length).decode('utf8')
    else:
        body = None

    # current utc time for expiration check
    now = datetime.now(timezone.utc)

    # remove expired sessions before looking up token
    cleanup_expired_sessions(now)


    # read cookie
    cookie_header = headers.get("cookie","")
    cookies = parse_cookies(cookie_header)

    token = cookies.get(COOKIE_NAME)

    session_record = None

    # token is valid,get session record
    if valid_token(token):
        session_record = SESSIONS.get(token)


    # first visit,invalid token,unkown token
    # or previously expired token
    if session_record is None:
        token = secrets.token_hex(32)

        session_record = {
            "data":{},
            "expires" : now+SESSION_DURATION,
        }

        SESSIONS[token] = session_record

    else:
        # refresh same session with new expires time
        new_expiration = now + SESSION_DURATION
        
        if new_expiration > session_record["expires"]:
            session_record["expires"] = new_expiration
        
    # get user server-side session
    session = session_record["data"]

    # let session pass into request
    status, body = do_request(session,
        method,
        url, 
        headers, 
        body
    )

    body_bytes = body.encode("utf8")

    response = "HTTP/1.0 {}\r\n".format(status)
    response += "Content-Type: text/html; charset=utf-8\r\n"
    response += "Content-Length: {}\r\n".format(len(body_bytes))

    csp = "default-src http://localhost:8000"
    response += "Content-Security-Policy: {}\r\n".format(csp)


    cookie_expires = format_datetime(
        session_record["expires"],
        usegmt=True
    )

    response += (
        "Set-Cookie: {}={}; "
        "Expires={}; "
        "SameSite=Lax\r\n"
    ).format(
        COOKIE_NAME,
        token,
        cookie_expires
    )


    response += "\r\n"

    conx.sendall(response.encode('utf8')+body_bytes)
    conx.close()


def form_decode(body):
    params ={}
    if not body:
        return params

    
    for field in body.split("&"):
        if "=" in field:
            name, value = field.split("=",1)
        else:
            name , value  = field,""
        
        name = urllib.parse.unquote_plus(name)
        value = urllib.parse.unquote_plus(value)
        params[name] = value

    return params

def path_only(url):
    if "?" in url:
        path,query = url.split("?",1)
        return path
    
    return url

def topic_to_url(topic):
    return "/"+urllib.parse.quote(topic,safe="")

def add_topic_url(topic):
    return "/add/" +urllib.parse.quote(topic,safe="")

def normalize_topic_name(topic):
    topic = topic.strip().lower()
    
    out = []
    last_dash = False
    
    for ch in topic:
        if ch.isalnum():
            out.append(ch)
            last_dash = False
        elif ch in [" ","-","_"]:
            if not last_dash:
                out.append("-")
                last_dash = True

    topic = "".join(out).strip("-")
    return topic

def login_form(session):
    out = "<!doctype html>"
    out += "<html>"
    out += "<body>"

    out += "<h1>Log in</h1>"

    out += "<form action=/ method=post>"

    out += "<p>"
    out += "Username: "
    out += "<input name=username>"
    out += "</p>"

    out += "<p>"
    out += "Password: "
    out += "<input name=password type=password>"
    out += "</p>"

    out += "<p>"
    out += "<button>Log in</button>"
    out += "</p>"

    out += "</form>"

    out += "<p><a href=/>Back to topics</a></p>"

    out += "</body>"
    out += "</html>"

    return out
    
def do_login(session,params):
    username = params.get("username","")
    password = params.get("password","")

    expected_password = LOGINS.get(username)

    valid_login =(
        expected_password is not None
        and hmac.compare_digest(
            expected_password,
            password
        )
    )

    if valid_login:
        session["user"] = username

        return "200 OK",show_home(session)

    out = "<!doctype html>"
    out += "<html>"
    out += "<body>"

    out += "<h1>Invalid username or password</h1>"

    if username:
        out+="<p>Username:"
        out+=escape(username)
        out+="</p>"

    out += "<p><a href=/login>Try again</a></p>"

    out += "</body>"
    out += "</html>"

    return "401 Unauthorized",out

def show_home(session):
    out = "<!doctype html>"
    out += "<html>"
    out += "<body>"

    out += "<h1>Message Board</h1>"

    if "user" in session:
        out += "<p>Hello, "
        out += escape(session["user"])
        out += "</p>"
    else:
        out += "<p>"
        out += "<a href=/login>"
        out += "sign in to post messages"
        out += "</a>"
        out += "</p>"
    
    out += "<h2>Topics</h2>"

    if not TOPICS:
        out += "<p>No topics yet.</p>"
    else:
        out+="<ul>"

        for topic in sorted(TOPICS.keys()):
            topic_url = topic_to_url(topic)

            out += "<li>"
            out += "<a href={}>".format(escape(topic_url, quote=True))
            out += escape(topic)
            out += "</a>"
            out += "</li>"

        out+="</ul>"

    # only login user can add new topic
    if "user" in session:

        nonce = create_nonce(session)

        out += "<h2>Add new topic</h2>"
        out += "<form action=/add-topic method=post>"
        # add nonce to form
        out += "<input "
        out += "name=nonce " # nonce field
        out += "type=hidden " # hidden field
        out += "value={}>".format(nonce) # nonce value

        out += "<p><input name=topic></p>"
        out += "<p><button>Add topic</button></p>"

        out += "</form>"

    out += "</body>"
    out += "</html>"

    return out

def show_topic(session,topic):
    messages = TOPICS[topic]

    out = "<!doctype html>"
    out += "<html>"
    out += "<body>"

    out += "<p><a href=/>Back to topics</a></p>"

    out += "<h1>Topic: "
    out += escape(topic)
    out += "</h1>"

    if "user" in session:
        nonce = create_nonce(session)

        out += "<p>Hello,"
        out += escape(session["user"])
        out += "</p>"

        out += "<form action={} method=post>".format(
            escape(add_topic_url(topic), quote=True)
        )

        out += "<input "
        out += "name=nonce "
        out += "type=hidden "
        out += "value={}>".format(nonce)

        out +=   "<p><input name=message></p>"
        out +=   "<p><button>Post message</button></p>"
        out += "</form>"

    else:
        out += "<p>"
        out += "<a href=/login>"
        out += "Sign in to post a message"
        out += "</a>"
        out += "</p>"


    out += "<h2>Messages</h2>"

    if not messages:
        out += "<p>No messages yet.</p>"
    else:
        for message in messages:
            text = message["text"]
            author = message["author"]

            out += "<p>"
            out += escape(text)
            out += "<br>"
            out += "<i>by "
            out += escape(author)
            out += "</i>"
            out += "</p>"

    out += "</body>"
    out += "</html>"

    return out

def add_topic(session,params):
    if "user" not in session:
        return show_home(session)

    if "topic" not in params:
        return show_home(session)

    topic = normalize_topic_name(params["topic"])
    

    if topic=="":
        return show_home(session)

    if topic not in TOPICS:
        TOPICS[topic] = []
        save_topics()

    return show_home(session)

def add_message(session,topic,params):
    if "user" not in session:
        return show_topic(session,topic)

    if topic not in TOPICS:
        return not_found("/add/"+topic,"POST")

    if "message" in params:
        message = params["message"].strip()

        if message and len(message) <= 100:
            TOPICS[topic].append({
                "text":message,
                "author":session["user"],
            })

            save_topics()

    return show_topic(session,topic)

def query_decode(url):
    if "?" not in url:
        return {}

    path,query=url.split("?",1)
    return form_decode(query)
 
def show_submit_result(url):
    params = query_decode(url)

    out = "<!doctype html>"
    out += "<html>"
    out += "<body>"

    out += "<h1>Submitted GET Form</h1>"

    out += "<p>Raw URL:</p>"
    out += "<p>" + escape(url) + "</p>"

    out += "<h2>Decoded fields</h2>"

    if not params:
        out += "<p>No fields submitted.</p>"
    else:
        for name,value in params.items():
            out += "<p>"
            out += escape(name)
            out += " = "
            out += escape(value)
            out += "</p>"

    out += "<p><a href=/>Back</a></p>"

    out += "</body>"
    out += "</html>"

    return out

def csrf_rejected():# when nonce is incorrect return error page
    out = "<!doctype html>"
    out += "<html>"
    out += "<body>"

    out += "<h1>Invalid form submission</h1>"
    out += "<p>The CSRF nonce is missing or invalid.</p>"
    out += "<p><a href=/>Back to topics</a></p>"

    out += "</body>"
    out += "</html>"

    return out

def login_required():
    out = "<!doctype html>"
    out += "<html>"
    out += "<body>"

    out += "<h1>Login required</h1>"
    out += "<p>You must log in before posting.</p>"
    out += "<p><a href=/login>Log in</a></p>"

    out += "</body>"
    out += "</html>"

    return out

def not_found(url,method):
    out = "<!doctype html>"
    out += "<html>"
    out += "<body>"
    out += "<h1>{} {} not found!</h1>".format(
        escape(method),
        escape(url)
    )
    out += "<p><a href=/>Back to topics</a></p>"
    out += "</body>"
    out += "</html>"
    return out

def do_request(session,method, url, headers, body):
    path = path_only(url)

    # home page
    if method == "GET" and path =="/":
        return "200 OK", show_home(session)

    # login page
    elif method == "GET" and path =="/login":
        return "200 OK" ,login_form(session)

    # deal with login
    elif method == "POST" and path == "/":
        params = form_decode(body)

        return do_login(
            session,
            params
        )
    
    # add topic
    elif method=="POST" and path=="/add-topic":
        if "user" not in session:
            return "403 Forbidden",login_required()

        params = form_decode(body)

        if not valid_nonce(session,params):
            return "403 Forbidden",csrf_rejected()

        return "200 OK" ,add_topic(session,params)

    # show topic
    elif method=="GET" and path.startswith("/") and len(path) > 1:
        topic = urllib.parse.unquote(path[1:])

        if topic in TOPICS:
            return "200 OK", show_topic(session,topic)
        else:
            return "404 Not Found",not_found(url,method)

    # add message
    elif method == "POST" and path.startswith("/add/"):
        if "user" not in session:
            return "403 Forbidden",login_required()

        params = form_decode(body)

        if not valid_nonce(session,params):
            return "403 Forbidden", csrf_rejected()

        topic = urllib.parse.unquote(path[len("/add/"):])

        return "200 OK", add_message(session,topic,params)
    
    # other
    else:
        return "404 Not Found", not_found(url,method)


if __name__ == "__main__":
    s = socket.socket(
        family=socket.AF_INET,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    s.bind(('', 8000))
    s.listen()

    while True:
        conx, addr = s.accept()
        handle_connection(conx)