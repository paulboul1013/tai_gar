import socket


ENTRIES = [ 'Pavel was here' ]


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

def show_comments():
    out = "<!doctype html>"
    for entry in ENTRIES:
        out += "<p>" + entry + "</p>"
        out += "<form action=add method=post>"
        out +=   "<p><input name=guest></p>"
        out +=   "<p><button>Sign the book!</button></p>"
        out += "</form>"
    return out

def do_request(method, url, headers, body):
    if method == "GET" and url =="/":
        return "200 OK", show_comments()
    elif method == "POST" and url=="/add":
        params = form_decode(body)
        return "200 OK", add_entry(params)
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