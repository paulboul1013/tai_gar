import socket
import ssl
import sys

class URL:
    def __init__(self, url):
        self.view_source=False

        # 解析 URL Scheme        
        if url.startswith("view-source:"):
            # 例如 "view-source:http://google.com" 變成 "http://google.com"
            self.view_source=True
            _,url=url.split(":",1)

        # 解析 URL Scheme        
        if url.startswith("data:"):
            self.scheme="data"
            self.scheme,self.path = url.split(":", 1)
        else:
            self.scheme, url = url.split("://", 1)

        # 確保支援的 URL Scheme
        assert self.scheme in ["http", "https","file","data"]

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


            

    def request(self):

        if self.scheme=="data":   
            #example: text/html,Hello World!
            if "," in self.path:
                media_type,body=self.path.split(",",1)
                return body
            else:
                return ""


        if self.scheme=="file":
            with open(self.path,"r",encoding="utf-8") as f:
                return f.read()


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


        # 定義要發送的headers
        headers = {
            "Host": self.host,
            "Connection":"close", # 關閉連線
            "User-Agent":"MyToyBrowser/1.0"  # 自定義 User-Agent
        }
        
        request = "GET {} HTTP/1.1\r\n".format(self.path)

        for header,value in headers.items():
            request+= "{}: {}\r\n".format(header,value)
        
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

def load(url):
    # 載入流程：發送請求 -> 取得內容 -> 顯示
    body = url.request()

    if url.view_source:
        print(body)
    else:
        show(body)


if __name__ == "__main__":

    if len(sys.argv) > 1:
        # 從命令列參數讀取 URL 並執行
        load(URL(sys.argv[1]))

    else:

        default_file="file:///home/paulboul1013/tai_gar/test.html"
        

        try:
            load(URL(default_file))
        except Exception as e:
            print(f"無法開啟檔案 ({e})")

            
            print("未提供 URL，使用預設 Data URL 測試...")
        
            backup_url = "data:text/html,Hello <b>World</b>! &lt;div&gt;Test&lt;/div&gt;\n"

            load(URL(backup_url))

             # 測試 : View-Source 模式
            print("\n--- 測試 3: View-Source 模式 (顯示原始碼) ---")
            # 注意這裡前面加了 view-source:
            view_source_url = "view-source:" + backup_url
            load(URL(view_source_url))

        