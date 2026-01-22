import socket
import ssl
import sys
import time 
import gzip
import tkinter

#key:(scheme,host,port)
#value:socket object
socket_cache={}

# http cache
# key:url string
# value:(body_bytes,expires_at_timestamp)
http_cache={}

WIDTH,HEIGHT=800,600

class Browser:
    def __init__(self):
        self.window=tkinter.Tk()
        self.canvas=tkinter.Canvas(
            self.window,
            width=WIDTH,
            height=HEIGHT
        )
        self.canvas.pack()
    
    def load(self,url):
        # 載入流程：發送請求 -> 取得內容 -> 顯示
        body = url.request()

        if url.view_source:
            print(body)
        else:
            show(body)
        self.canvas.create_rectangle(10,20,400,300)
        self.canvas.create_oval(100,100,150,150)
        self.canvas.create_text(200,150,text="Hi")


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

        # save origin url string，for cache key
        if self.scheme in ["http","https"]:
            self.url_string=f"{self.scheme}://{self.host}:{self.port}{self.path}"
        else:
            self.url_string=url


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

        
        current_url=self
        redirect_limit=10 

        while redirect_limit>0:
            
            if current_url.url_string in http_cache:
                cached_body,expires_at=http_cache[current_url.url_string]

                if time.time() < expires_at:
                    
                    print(f"Cache Hit! (Expires in {int(expires_at - time.time())}s)")
                    return cached_body.decode("utf-8",errors="replace")

                else:
                    print("Cache Expired! Re-downloading...")
                    del http_cache[current_url.url_string]



            key=(self.scheme,self.host,self.port)
            

            if key in socket_cache:
                s=socket_cache[key]
            else:
                # 建立 TCP Socket 連線
                s = socket.socket(
                    family=socket.AF_INET,
                    type=socket.SOCK_STREAM,
                    proto=socket.IPPROTO_TCP
                )

                # 連接到伺服器的Port
                s.connect((self.host, self.port))

                if self.scheme == "https":
                    ctx = ssl.create_default_context()
                    s = ctx.wrap_socket(s, server_hostname=self.host)

                socket_cache[key]=s


            # 定義要發送的headers
            headers = {
                    "Host": current_url.host, # 注意：轉址後 Host 也要變，所以用 current_url.host
                    "Connection":"keep-alive", # 關閉連線
                    "User-Agent":"MyToyBrowser/1.0", # 自定義 User-Agent
                    "Accept-Encoding":"gzip" # support gzip
            }
        
            request = "GET {} HTTP/1.1\r\n".format(current_url.path)

            for header,value in headers.items():
                request+= "{}: {}\r\n".format(header,value)
        
            request += "\r\n"  # 請求標頭結束，需多一個空行



            # 發送編碼後的請求
            s.send(request.encode("utf-8"))

            # 使用 makefile 建立檔案介面，方便逐行讀取回應
            response = s.makefile("rb")

            try:

                # 讀取狀態行 (Status Line)，例如: HTTP/1.0 200 OK
                statusline = response.readline().decode("utf-8")
                if not statusline:
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
                    
                    #更新current_url,準備下一次迴圈
                    print(f"Redirecting to: {location}") # 除錯用，讓你知道正在轉址
                    current_url=URL(location)

                    redirect_limit-=1
                    continue

            # 檢查 Cache-Control
            if status==200 and "cache-control" in response_headers:
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
                                    http_cache[current_url.url_string]=(content_bytes,expires_at)
                                    print(f"Cached! (max-age={max_age})")

                                break

                    except ValueError:
                        pass
                    
            print(f"Debug - Headers: {response_headers.keys()}")
            if "cache-control" in response_headers:
                print(f"Debug - Cache-Control value: {response_headers['cache-control']}")


            if "content-encoding" in response_headers:
                print(f"Debug - Content-Encoding: {response_headers['content-encoding']}")
            if "transfer-encoding" in response_headers:
                print(f"Debug - Transfer-Encoding: {response_headers['transfer-encoding']}")

            # 如果不是轉址 (200 OK 或其他錯誤)，直接回傳結果
            return content_bytes.decode("utf-8",errors="replace")


        raise Exception("Redirect loop detected!")

            

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

    Browser().load(URL(sys.argv[1]))
    tkinter.mainloop()


    # if len(sys.argv) > 1:
        # 從命令列參數讀取 URL 並執行
        # load(URL(sys.argv[1]))

    # else:
    #     window=tkinter.Tk()
    #     tkinter.mainloop()

        # default_file="file:///home/paulboul1013/tai_gar/test.html"

        # try:
            # 測試 Gzip 壓縮
            # httpbin 的 /gzip 接口會回傳 gzip 壓縮後的 json 資料
            # test_url = "http://httpbin.org/gzip"
            
            # print(f"--- 測試 Gzip 壓縮: {test_url} ---")
            # load(URL(test_url))


            # # 測試快取功能
            # test_url = "http://httpbin.org/cache/10"
            
            # print(f"--- 第一次請求: {test_url} ---")
            # load(URL(test_url))
            
            # print(f"\n--- 第二次請求 (應該命中快取) ---")
            # load(URL(test_url))

            # # 測試轉址功能
            # print("--- 測試轉址功能 ---")
            # # 這個網址會轉址回 /http.html
            # load(URL("http://browser.engineering/redirect"))


            # # 測試：連續請求同一個網站，驗證 Socket Reuse (你可以透過 Wireshark 或觀察延遲來驗證)
            # print("--- 第一次請求 (建立新連線) ---")
            # load(URL("http://browser.engineering/examples/example1-simple.html"))
            
            # print("\n--- 第二次請求 (應該重用 Socket) ---")
            # load(URL("http://browser.engineering/examples/example1-simple.html"))

            # load(URL(default_file))
        # except Exception as e:
        #     print(f"無法開啟檔案 ({e})")

            
        #     print("未提供 URL，使用預設 Data URL 測試...")
        
        #     backup_url = "data:text/html,Hello <b>World</b>! &lt;div&gt;Test&lt;/div&gt;\n"

        #     load(URL(backup_url))

        #      # 測試 : View-Source 模式
        #     print("\n--- 測試 3: View-Source 模式 (顯示原始碼) ---")
        #     # 注意這裡前面加了 view-source:
        #     view_source_url = "view-source:" + backup_url
        #     load(URL(view_source_url))

        