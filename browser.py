import socket
import ssl
import sys
import time 
import gzip
import tkinter
import os
import tkinter.font

# emolji cache
# key: character (e.g. "😀")
# value: tkinter.PhotoImage object
emoji_cache={}

# socket cache
#key:(scheme,host,port)
#value:socket object
socket_cache={}

# http cache
# key:url string
# value:(body_bytes,expires_at_timestamp)
http_cache={}

#global FONT cache
FONTS={}


WIDTH,HEIGHT=800,600
HSTEP, VSTEP = 13, 18
SCROLL_STEP = 100

SCROLLBAR_WIDTH=12

USE_RTL=False

def get_font(size,weight,style):
    key=(size,weight,style)
    if key not in FONTS:
        font=tkinter.font.Font(size=size,weight=weight,slant=style)
        # create a Label and associate this font can raise up metrics performance
        label=tkinter.Label(font=font)
        FONTS[key]=(font,label)

    return FONTS[key][0]

def get_emoji(char):

    if char in emoji_cache:
        return emoji_cache[char]

    # convert char to unicode hex strings (e.g. "😀" -> "U+1F600")
    code_point="{:X}".format(ord(char))

    # pic in the openmoji dir
    possible_filenames=[
        f"openmoji/{code_point}_color.png",
        f"openmoji/{code_point}.png"
    ]
    
    for file_path in possible_filenames:
        if os.path.exists(file_path):
            try:
                #loading pic
                img=tkinter.PhotoImage(file=file_path)

                target_size=35
                w=img.width()

                # opemoji pic is very big
                # need to shrink it 16x16
                # subsample(x) represent to shrink x times
                # 72x72 shrink 4 times-> 18x18 close to VSTEP(18)
                scale_factor=w//target_size

                if scale_factor<1:
                    scale_factor=1

                img=img.subsample(scale_factor,scale_factor)

                #save into cache
                emoji_cache[char]=img
                return img
            except Exception as e:
                print(f"Error loading emoji {char}: {e}")
                return None

    return None

class Layout:
    def __init__(self,tokens,width):
        self.display_list=[]
        #restore current line all objects
        # format [(width,object),(width,object),(..)]
        self.line_buffer=[]
        self.width =width

        self.cursor_x=HSTEP
        self.cursor_y=VSTEP

        # word type status management
        self.weight="normal"
        self.style="roman"
        self.size=12
        self.alignment="left"
        self.is_sup=False # is it superscript

        # traversal tokesn and deal with
        for tok in tokens:
            self.token(tok)

        self.flush_line()

    def token(self,tok):
        if isinstance(tok,Tag):
            if tok.tag=='h1 class="title"':
                self.flush_line()
                self.alignment="center"
            elif tok.tag=="/h1":
                self.flush_line()
                self.alignment="left"
            elif tok.tag=="sup":
                self.is_sup=True
                self.size=int(self.size/2)
            elif tok.tag=="/sup":
                self.is_sup=False
                self.size=int(self.size*2)
            elif tok.tag=="b":
                self.weight="bold"
            elif tok.tag=="/b":
                self.weight="normal"
            elif tok.tag=="i":
                self.style="italic"
            elif tok.tag=="/i":
                self.style="roman"
            elif tok.tag=="br":
                self.flush_line()
            elif tok.tag=="p":
                self.flush_line()
            elif tok.tag=="/p":
                self.flush_line()
                self.cursor_y+=VSTEP
            elif tok.tag=="small":
                self.size-=2
            elif tok.tag=="/small":
                self.size+=2
            elif tok.tag == "big":
                self.size += 4
            elif tok.tag == "/big":
                self.size -= 4

        # deal with text
        elif isinstance(tok,Text):
            # from html rules，let text split into words with space
            words=tok.text.split()

            # if this line is empty (example double \n)，words will be empty list
            if not words:
                # have empty line，auto add height
                self.flush_line()
                return
            
            for word in words:
                self.word(word)

    def word(self,word):
        # check is emoji or text
        # because now use word for basic unit，if word is emoji (and len is 1)，loading picture
        img=None

        if len(word)==1:
            img=get_emoji(word)

        if img:
            # picture doesn't have ascent/descent，make default height is ascent
            w=img.width()
            content=img
            space_w=0

            current_line_w=sum(item[0] for item in self.line_buffer)
            if current_line_w +w >= self.width - HSTEP*2:
                self.flush_line()
            self.line_buffer.append((w+space_w,content))
            return


        # use font cache
        font = get_font(self.size,self.weight, self.style)

        #normal showing，don't want to show \xad，first remove it to calucalute real status width
        clean_word=word.replace("\xad","")

        #use font measure to get width of text
        w=font.measure(clean_word)

        space_w=font.measure(" ")

        # calculate current line available space
        current_line_w=sum(item[0] for item in self.line_buffer)
        available_space=(self.width - HSTEP*2) - current_line_w

        # status a: word can place current line and add '-' directly
        if w+space_w <=available_space:
            content=(clean_word,font,self.is_sup)
            self.line_buffer.append((w+space_w,content))
            return

        # status b: word can't place and add '-' 。 try split word
        if "\xad" in word:
            
            parts=word.split("\xad")
            best_prefix=None
            best_width=0
            remainder=None

            # looking for can place avaiable space longest prefix word
            # try parts[0], parts[i]，add '-'
            for i in range(len(parts)-1):
                # assemble text (remove \xad)
                prefix_text="".join(parts[:i+1])
                # add '-'
                candidate_text=prefix_text+"-"
                candidate_width=font.measure(candidate_text)

                if candidate_width+space_w <=available_space:
                    # if can place，this is a candidate plan
                    best_prefix=candidate_text
                    best_width=candidate_width
                    
                    # left parts (need add \xad back ，because maybe next line need to split)
                    remainder="\xad".join(parts[i+1:])

                else:
                    # if can't place，longer must can't also ，stop finding
                    break

            # if find best_prefix，use it
            if best_prefix:
                # add front parts (include '-' ) add current line
                content=(best_prefix,font,self.is_sup)
                self.line_buffer.append((best_width+space_w,content))

                # force change line
                self.flush_line()

                # deal with remainder
                if remainder:
                    self.word(remainder)
                
                return

        # status c : word can't place and no soft break or (split first parts still too long)
        # execute standard change to next line
        self.flush_line()
        # content save because draw need to know font object to draw text
        content=(clean_word,font,self.is_sup)
        # add buffer(not decide coord yet)
        self.line_buffer.append((w+space_w,content))


    def flush_line(self):

        if not self.line_buffer:
            return

        # find heightest ascent and descent
        max_ascent=0
        max_descent=0

        # buffer object into the display_list
        for item_w,item_content in self.line_buffer:
            if isinstance(item_content,tuple):
                word,font,is_sup=item_content
                ascent=font.metrics("ascent")
                descent=font.metrics("descent")
            else:
                # pic (emoji)
                ascent=item_content.height()
                descent=0

            if ascent > max_ascent: max_ascent=ascent
            if descent > max_descent: max_descent=descent


        # calculate baseline position
        baseline=self.cursor_y+max_ascent*1.25

        # calculate current line total width
        line_width=sum(item[0] for item in self.line_buffer)

        # decide starting cursor_x
        if self.alignment=="center":
            # total usefull widht is self.width-HSTEP*2 (minus left and right margin)
            remaining_space=(self.width-HSTEP*2)-line_width
            cursor_x=HSTEP+(remaining_space//2)

        # base on baseline to put every object
        elif USE_RTL:
            cursor_x=self.width-line_width-HSTEP
        else:
            cursor_x=HSTEP

        for item_w,item_content in self.line_buffer:
            if isinstance(item_content,tuple):
                word,font,is_sup=item_content

                if is_sup:
                    y=baseline-max_ascent
                else:
                    y=baseline-font.metrics("ascent")

                #every single word's y =baseline - this word ascent
                self.display_list.append((cursor_x,y,word,font))
                
            else:
                # pic bottom is on the baseline
                img_offset=12
                y=baseline-item_content.height()
                self.display_list.append((cursor_x,y+img_offset,item_content))

            cursor_x+=item_w

        # update next starting y coord  
        self.cursor_y=baseline+max_descent*1.25
        
        # clear buffer
        self.line_buffer.clear()

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
    def __init__(self,text):
        self.text=text
class Tag:
    def __init__(self,tag):
        self.tag=tag

class Browser:
    def __init__(self):
        self.window = tkinter.Tk()
        self.width=WIDTH
        self.height=HEIGHT

        self.tokens=[]

        self.canvas = tkinter.Canvas(
            self.window,
            width=WIDTH,
            height=HEIGHT
        )
        # let canvas fill the window
        self.canvas.pack(fill=tkinter.BOTH,expand=True)
        self.canvas.bind("<Configure>",self.resize)
        self.scroll = 0

        # bind keyboard events
        self.window.bind("<Up>",self.scrollup)
        self.window.bind("<Down>", self.scrolldown)

        #bind mouse events
        self.window.bind("<MouseWheel>",self.mousewheel)

        #use buttion4 and button5 to scroll
        self.window.bind("<Button-4>",self.scrollup)
        self.window.bind("<Button-5>",self.scrolldown)

        

    def load(self, url):
        body = url.request()
        self.tokens=lex(body)

        self.display_list = Layout(self.tokens,self.width).display_list
        self.draw()

    def draw(self):
        self.canvas.delete("all")
        for item in self.display_list:

            item_y=item[1]
            if item_y>self.scroll +self.height :continue
            if item_y+VSTEP < self.scroll: continue

            if len(item)==4:
                #this is all text information
                x,y,word,font=item
                self.canvas.create_text(x,y-self.scroll,text=word,font=font,anchor="nw")

            elif len(item)==3:
                #this is image information
                x,y,img=item

                self.canvas.create_image(x,y-self.scroll-2,image=img,anchor="nw")


            #scrollbar section
            
            #calculate web total height
            if self.display_list:
                # last word y coord  + height
                document_height=self.display_list[-1][1]+VSTEP
            else:
                document_height=0

            # only web longer than window height need scrollbar
            if document_height>self.height:
                # calculate ratio
                ratio_visible=self.height/document_height
                ratio_scroll=self.scroll/document_height

                # calculate scrollbar size and position
                bar_h=self.height*ratio_visible
                bar_y=self.height*ratio_scroll

                #draw blue rectangle
                #pos:(right edge - width bound, top edge,right edge,down edge)
                self.canvas.create_rectangle(
                    self.width-SCROLLBAR_WIDTH,
                    bar_y,
                    self.width,
                    bar_y+bar_h,
                    fill="blue",outline=""
                )

            

    def scrolldown(self, e):
        
        # calculate web total height
        if self.display_list:
            document_height=self.display_list[-1][1]+VSTEP
        else:
            document_height=0

        # calculate max scroll distance(web total height - window height)
        max_scroll=document_height-self.height


        # execute scroll and limit edge
        self.scroll+=SCROLL_STEP

        # if scroll is greater than max scroll, limit it
        if self.scroll>max_scroll:
            self.scroll=max_scroll

        # if web less than window (max_scroll < 0)
        if self.scroll<0:
            self.scroll=0

        self.draw()

    def scrollup(self,e):
        self.scroll-=SCROLL_STEP
        if self.scroll<0:
            self.scroll=0
        self.draw()

    def mousewheel(self,e):
        if e.delta>0:
            self.scrollup(e)
        else:
            self.scrolldown(e)

    def resize(self,e):
        if e.width <=10 or e.height <=10:
            return

        if self.width == e.width and self.height == e.height:
            return

        
        # read new window size
        self.width=e.width
        self.height=e.height
        
        #recalculate layout
        if self.tokens:
            self.display_list=Layout(self.tokens,self.width).display_list
            self.draw()


class URL:
    def __init__(self, url):
        self.view_source=False
        self.scheme=""
        self.host=""
        self.path=""
        self.port=0
        self.url_string=url
        
        try:

            # parse view-source        
            if url.startswith("view-source:"):
                # 例如 "view-source:http://google.com" 變成 "http://google.com"
                self.view_source=True
                _,url=url.split(":",1)

            if url=="about:blank":
                self.scheme="about"
                self.path="blank"
                return

            
            if url.startswith("data:"):
                self.scheme="data"
                self.scheme,self.path = url.split(":", 1)
            else:
                if "://"  not in url:
                    raise ValueError("Malformed URL: missing ://")
                
                self.scheme, url = url.split("://", 1)

            # 支援的 URL Scheme
            if self.scheme not in ["http", "https","file","data","about"]:
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



    def request(self):

        if self.scheme=="about":
            return ""

        if self.scheme=="data":   
            #example: text/html,Hello World!
            if "," in self.path:
                media_type,body=self.path.split(",",1)
                return body
            else:
                return ""


        if self.scheme=="file":
            try:
                with open(self.path,"r",encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                print(f"File read error: {e}")
        
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
                    
                    print(f"Redirect location: {location}")
                    
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

    if "--rtl" in sys.argv:
        USE_RTL=True
        sys.argv.remove("--rtl")
        print("RTL mode enabled")

    
    if len(sys.argv) > 1:
        url_arg=sys.argv[1]
    else:
        # url_arg = "data:text/html,This is default text showing 😀   "  
         
        # 使用 \xad 插入軟連字符
        long_word = "super\xadcali\xadfragi\xadlistic\xadexpi\xadali\xaddocious"
        # 重複多次以確保觸發換行
        text = f"This is a test of soft hyphens. {long_word} " * 5
        url_arg = f"data:text/html,{text}"     

    Browser().load(URL(url_arg))
    tkinter.mainloop()