import socket
import ssl

class URL:
    def __init__(self, url):
        # 解析 URL Scheme，目前僅支援 http
        self.scheme, url = url.split("://", 1)

        # 確保支援的 URL Scheme
        assert self.scheme in ["http", "https"]
            

        if self.scheme == "http":
            self.port=80
        elif self.scheme == "https":
            self.port=443

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





    def request(self):
        # 建立 TCP Socket 連線
        s = socket.socket(
            family=socket.AF_INET,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP
        )

        # 連接到伺服器的 80 Port (HTTP 標準埠號)
        s.connect((self.host, self.port))
        if self.scheme == "https":
            ctx = ssl.create_default_context()
            s = ctx.wrap_socket(s, server_hostname=self.host)

        # 建構 HTTP GET 請求
        # HTTP/1.0 需要 Host header 以支援虛擬主機
        request = "GET {} HTTP/1.0\r\n".format(self.path)
        request += "Host: {}\r\n".format(self.host)
        request += "\r\n"  # 請求標頭結束，需多一個空行
        
        # 發送編碼後的請求
        s.send(request.encode("utf-8"))

        # 使用 makefile 建立檔案介面，方便逐行讀取回應
        response = s.makefile("r", encoding="utf8", newline="\r\n")

        # 讀取狀態行 (Status Line)，例如: HTTP/1.0 200 OK
        statusline = response.readline()
        version, status, explanation = statusline.split(" ", 2)
        
        # 讀取並解析回應標頭 (Headers)
        response_headers = {}
        while True:
            line = response.readline()
            if line == "\r\n": break  # 遇到空行表示標頭結束
            header, value = line.split(":", 1)
            response_headers[header.casefold()] = value.strip()

        # 讀取回應內容 (Body)
        # 根據 Content-Length 讀取指定長度，或直接讀到連線關閉
        if "content-length" in response_headers:
            content_length = int(response_headers["content-length"])
            content = response.read(content_length)
        else:
            content = response.read()

        # 關閉 Socket
        s.close()

        return content

def show(body):
    # 簡單的 HTML 解析器：移除角括號 <> 包夾的標籤內容，只顯示文字
    in_tag = False
    for c in body:
        if c == "<":
            in_tag = True
        elif c == ">":
            in_tag = False
        elif not in_tag:
            print(c, end="")

def load(url):
    # 載入流程：發送請求 -> 取得內容 -> 顯示
    body = url.request()
    show(body)


if __name__ == "__main__":
    import sys
    # 從命令列參數讀取 URL 並執行
    load(URL(sys.argv[1]))

        