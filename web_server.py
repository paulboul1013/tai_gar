import socket
import urllib.parse
from html import escape
import os
import json
import secrets

DATA_FILE = "message_board.json"

COOKIE_NAME = "token"

# token -> for user session dictionary
SESSIONS = {}

DEFAULT_TOPICS = {
    "cooking" :[
        "Pavel made soup",
    ],
    "cars" :[
        "Toyota is reliable",
    ],
}

def load_topics():
    if not os.path.exists(DATA_FILE):
        return {
            topic:message[:]
            for topic,message in DEFAULT_TOPICS.items()
        }

    try:
        with open(DATA_FILE,"r",encoding="utf8") as f:
            data = json.load(f)
    except Exception as e:
        print("Failed to load data file:",e)
        return {
            topic:message[:]
            for topic,message in DEFAULT_TOPICS.items()
        }

    if not isinstance(data,dict):
        return{
            topic:message[:]
            for topic,message in DEFAULT_TOPICS.items()
        }

    topics ={}

    for topic,messages in data.items():
        if not isinstance(topic,str):
            continue
    
        if not isinstance(messages,list):
            continue

        clean_messages = []

        for message in messages:
            if isinstance(message,str):
                clean_messages.append(message)

        topics[topic] = clean_messages


    return topics


TOPICS = load_topics()

def save_topics():
    temp_file = DATA_FILE + ".tmp"

    with open(temp_file,"w",encoding="utf8") as f:
        json.dump(TOPICS,f,ensure_ascii=False,indent=2)

    os.replace(temp_file,DATA_FILE)


def handle_connection(conx):
    req = conx.makefile("b")
    reqline = req.readline().decode('utf8')
    method, url, version = reqline.split(" ", 2)
    assert method in ["GET", "POST"]

    headers = {}
    while True:
        line = req.readline().decode('utf8')
        if line == '\r\n': break
        header, value = line.split(":", 1)
        headers[header.casefold()] = value.strip()

    if 'content-length' in headers:
        length = int(headers['content-length'])
        body = req.read(length).decode('utf8')
    else:
        body = None

    status, body = do_request(method, url, headers, body)

    response = "HTTP/1.0 {}\r\n".format(status)
    response += "Content-Length: {}\r\n".format(
    len(body.encode("utf8")))
    response += "\r\n" + body

    conx.send(response.encode('utf8'))
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

def show_home():
    out = "<!doctype html>"
    out += "<html>"
    out += "<body>"

    out += "<h1>Message Board</h1>"

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

    out += "<h2>Add new topic</h2>"
    out += "<form action=/add-topic method=post>"
    out +=   "<p><input name=topic></p>"
    out +=   "<p><button>Add topic</button></p>"
    out += "</form>"

    out += "</body>"
    out += "</html>"

    return out

def show_topic(topic):
    messages = TOPICS[topic]

    out = "<!doctype html>"
    out += "<html>"
    out += "<body>"

    out += "<p><a href=/>Back to topics</a></p>"

    out += "<h1>Topic: "
    out += escape(topic)
    out += "</h1>"

    out += "<form action={} method=post>".format(
        escape(add_topic_url(topic), quote=True)
    )
    out +=   "<p><input name=message></p>"
    out +=   "<p><button>Post message</button></p>"
    out += "</form>"

    out += "<h2>Messages</h2>"

    if not messages:
        out += "<p>No messages yet.</p>"
    else:
        for message in messages:
            out += "<p>"
            out += escape(message)
            out += "</p>"

    out += "</body>"
    out += "</html>"

    return out

def add_topic(params):
    if "topic" not in params:
        return show_home()

    topic = normalize_topic_name(params["topic"])
    

    if topic=="":
        return show_home()

    if topic not in TOPICS:
        TOPICS[topic] = []
        save_topics()

    return show_home()

def add_message(topic,params):
    if topic not in TOPICS:
        return not_found("/add/"+topic,"POST")

    if "message" in params:
        message = params["message"].strip()

        if message:
            TOPICS[topic].append(message)
            save_topics()

    return show_topic(topic)

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

def do_request(method, url, headers, body):
    path = path_only(url)

    if method == "GET" and path =="/":
        return "200 OK", show_home()

    elif method=="POST" and path=="/add-topic":
        params = form_decode(body)
        return "200 OK" ,add_topic(params)

    elif method=="GET" and path.startswith("/") and len(path) > 1:
        topic = urllib.parse.unquote(path[1:])

        if topic in TOPICS:
            return "200 OK", show_topic(topic)
        else:
            return "404 Not Found",not_found(url,method)

    elif method == "POST" and path.startswith("/add/"):
        topic = urllib.parse.unquote(path[len("/add/"):])
        params = form_decode(body)
        return "200 OK", add_message(topic,params)
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