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

BLOCK_ELEMENTS = [
    "html", "body", "article", "section", "nav", "aside",
    "h1", "h2", "h3", "h4", "h5", "h6", "hgroup", "header",
    "footer", "address", "p", "hr", "pre", "blockquote",
    "ol", "ul", "menu", "li", "dl", "dt", "dd", "figure",
    "figcaption", "main", "div", "table", "form", "fieldset",
    "legend", "details", "summary"
]

def get_font(size,weight,style,family=None):
    if not family:
        family="Times"

    key=(size,weight,style,family)

    if key not in FONTS:
        font=tkinter.font.Font(family=family,size=size,weight=weight,slant=style)
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

def paint_tree(layout_object,display_list):
    display_list.extend(layout_object.paint())

    for child in layout_object.children:
        paint_tree(child,display_list)

class DrawText:
    def __init__(self,x1,y1,text,font):
        self.left=x1
        self.top=y1
        self.text=text
        self.font=font
        self.bottom=y1+font.metrics("linespace")

    def execute(self,scroll,canvas):
        canvas.create_text(
            self.left,
            self.top-scroll,
            text=self.text,
            font=self.font,
            anchor="nw",
        )

class DrawRect:
    def __init__(self,x1,y1,x2,y2,color):
        self.left=x1
        self.top=y1
        self.right=x2
        self.bottom=y2
        self.color=color

    def execute(self,scroll,canvas):
        canvas.create_rectangle(
            self.left,
            self.top-scroll,
            self.right,
            self.bottom-scroll,
            width=0,
            fill=self.color,
        )

class DocumentLayout:
    def __init__(self,node):#build root of layout tree
        self.node=node
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
        self.width=WIDTH-HSTEP*2
        
        
        child=BlockLayout([self.node],self,None)
        self.children=[child]
        child.layout()

        self.height=child.height

    def paint(self):
        return []

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

        self.display_list=[]

    def paint(self):
        cmds=[]


        if isinstance(self.node,Element):
            # first draw background，the text will cover up background
            if self.node.tag=="pre":
                x2=self.x+self.width
                y2=self.y+self.height
                cmds.append(DrawRect(self.x,self.y,x2,y2,"gray"))

            elif self.node.tag=="nav" and self.node.attributes.get("class") =="links":
                x2=self.x+self.width
                y2=self.y+self.height
                cmds.append(DrawRect(self.x,self.y,x2,y2,"lightgray"))

            elif self.node.tag=="nav" and self.node.attributes.get("id")=="toc":
                header_h=VSTEP+4
                x2=self.x+self.width
                y2=self.y+header_h

                #gray background behind the heading
                cmds.append(DrawRect(self.x,self.y,x2,y2,"gray"))

                #heading text
                font=get_font(12,"bold","roman")
                cmds.append(DrawText(self.x+4,self.y+2,"Table of Contents",font))

            # bullet of list items
            elif  self.node.tag=="li":
                bullet_size=5
                bullet_x=self.x-15
                bullet_y=self.y+8
                cmds.append(
                    DrawRect(
                        bullet_x,
                        bullet_y,
                        bullet_x+bullet_size,
                        bullet_y+bullet_size,
                        "black"
                    )
                )

        #inline mode turn text/picture into Draw command
        if self.layout_mode() == "inline":
            for item in self.display_list:
                if len(item)==4:
                    x,y,word,font=item
                    cmds.append(DrawText(x,y,word,font))

                else:
                    # keep origin emoji/image tuple
                    cmds.append(item)

        return cmds

    def is_block_node(self,node):
        if not isinstance(node,Element):
            return False

        # h6 is special: it can run into the next paragraph
        if node.tag=="h6":
            return False
        
        return node.tag in BLOCK_ELEMENTS

    def child_groups(self):
        groups = []
        buffer = []

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
            if isinstance(child, Element) and child.tag == "h6":
                if i + 1 < len(all_children):
                    next_child = all_children[i + 1]

                    if isinstance(next_child, Element) and next_child.tag == "p":
                        if buffer:
                            groups.append(buffer)
                            buffer = []

                        # merge h6 + p into one inline/layout group
                        merged = [child] + next_child.children
                        groups.append(merged)
                        i += 2
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
        if any(self.is_block_node(child)
                for node in self.nodes if isinstance(node,Element)
                for child in node.children):
            return "block"

        else:
            return "inline"

    def layout(self):
        self.x=self.parent.x
        self.width=self.parent.width

        # ident list items ，the text sits to the right of the bullet
        if isinstance(self.node,Element) and self.node.tag=="li":
            self.x+=20
            self.width-=20

        if self.previous:
            self.y=self.previous.y+self.previous.height
        else:
            self.y=self.parent.y

        mode=self.layout_mode()

        if mode=="block":
            toc_header_h=0
            old_y=self.y

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

            for child in self.children:
                child.layout()

            self.height=sum(child.height for child in self.children)+toc_header_h
            self.y=old_y
            
        else:
            self.cursor_x=0
            self.cursor_y=0
            self.weight="normal"
            self.style="roman"
            self.size=12
            self.alignment="left"
            self.is_sup=False
            self.is_abbr=False
            self.is_pre=False

            self.line_buffer=[]
            self.display_list=[]


            for node in self.nodes:
                self.recurse(node)

            self.flush_line()

            self.height=self.cursor_y


    def flush(self):
        self.flush_line()
        # self.cursor_x=0
        
        # for rel_x,word,font in self.line:
        #     x=self.x+rel_x
        #     y=self.y+baseline-font.metrics("ascent")
        #     self.display_list.append((x,y,word,font))
            

    def open_tag(self, tag):
        # already handled in HTMLParser
        # if tag == 'h1 class="title"':
        #     self.flush_line()
        #     self.alignment = "center"
        if tag == "sup":
            self.is_sup = True
            self.size = int(self.size / 2)
        elif tag == "pre":
            self.is_pre = True
            self.flush_line()
        elif tag == "abbr":
            self.is_abbr = True
        elif tag == "b":
            self.weight = "bold"
        elif tag == "i":
            self.style = "italic"
        elif tag == "br":
            self.flush_line()
        elif tag == "p":
            self.flush_line()
        elif tag == "small":
            self.size -= 2
        elif tag == "big":
            self.size += 4
        elif tag=="h6":
            self.weight="bold"

    def close_tag(self, tag):
        if tag == 'h1 class="title"':
            self.flush_line()
            self.alignment = "left"
        elif tag == "sup":
            self.is_sup = False
            self.size = int(self.size * 2)
        elif tag == "pre":
            self.is_pre = False
            self.flush_line()
        elif tag == "abbr":
            self.is_abbr = False
        elif tag == "b":
            self.weight = "normal"
        elif tag == "i":
            self.style = "roman"
        elif tag == "p":
            self.flush_line()
            self.cursor_y += VSTEP
        elif tag == "small":
            self.size += 2
        elif tag == "big":
            self.size -= 4
        elif tag=="h6":
            self.weight="normal"


    def recurse(self,tree):
        if isinstance(tree,Text):
            if self.is_pre:
                self.pre_word(tree.text)
            else:
                # normal mode
                for word in tree.text.split():
                    self.word(word)
        
        else:
            # if is script tag,just skip not render that child nodes(it's js code)
            if tree.tag == "script":
                return

            self.open_tag(tree.tag)
            for child in tree.children:
                self.recurse(child)
            
            self.close_tag(tree.tag)
        
                
    def pre_word(self,text):
        font=get_font(self.size,self.weight,self.style,family="Courier New")
        
        normalized_text=text.replace("\\n","\n")

        # make sure can catch end of line \n
        lines = normalized_text.splitlines(keepends=True)

        if not lines and normalized_text=='\n':
            lines=['\n']

        for line in lines:
            # remove \n and \r calculate width
            clean_line=line.replace('\n','').replace('\r','')
            w=font.measure(clean_line)

            # add content to buffer
            content=(clean_line,font,self.is_sup)
            self.line_buffer.append((w,content))

            # ecounter \n or end of line，force change next line
            # if not last line，execute flush_line()
            if '\n' in line:
                self.flush_line()

    def word(self,word):

        if self.is_abbr:
            for i,char in enumerate(word):
                if char.islower():
                    # lowercase : change to uppercase ，resize 0.8 ，bold
                    c=char.upper()
                    f=get_font(int(self.size*0.8),"bold",self.style)
                else:
                    # other characters: normal
                    c=char
                    f=get_font(self.size,self.weight,self.style)

                w=f.measure(c)
                # only on the last word's character add space width
                space_w=f.measure(" ") if i==len(word)-1 else 0
                
                # change line check
                current_line_w=sum(item[0] for item in self.line_buffer)
                if current_line_w+w >= self.width:
                    self.flush_line()

                self.line_buffer.append((w+space_w,(c,f,self.is_sup)))
            
            return

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
            if current_line_w +w >= self.width:
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
        available_space=self.width - current_line_w

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
            # pre mode
            if self.is_pre:#force cursor_y down
                font = get_font(self.size, self.weight, self.style, family="Courier New")
                self.cursor_y += font.metrics("linespace") * 1.25

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
            remaining_space=self.width-line_width
            cursor_x=max(0,remaining_space//2)

        # base on baseline to put every object
        elif USE_RTL:
            cursor_x=max(0,self.width-line_width)
        else:
            cursor_x=0

        for item_w,item_content in self.line_buffer:
            if isinstance(item_content,tuple):
                word,font,is_sup=item_content

                if is_sup:
                    y=baseline-max_ascent
                else:
                    y=baseline-font.metrics("ascent")

                #every single word's y =baseline - this word ascent
                self.display_list.append((self.x+cursor_x,self.y+y,word,font))
                
            else:
                # pic bottom is on the baseline
                img_offset=12
                y=baseline-item_content.height()
                self.display_list.append((self.x+cursor_x,self.y+y+img_offset,item_content))

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
    def __init__(self,text,parent):
        self.text=text
        self.children=[]
        self.parent=parent
    def __repr__(self):
        return repr(self.text)

class Element:
    def __init__(self,tag,attributes,parent):
        self.tag=tag
        self.attributes=attributes
        self.children=[]
        self.parent=parent
    def __repr__(self):
        return "<"+self.tag+">"
        # return "<"+self.tag+" "+str(self.attributes)+">"

class Browser:
    def __init__(self):
        self.window = tkinter.Tk()
        self.width=WIDTH
        self.height=HEIGHT

        self.tokens=[]
        self.display_list = []

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

        if url.view_source:
            # execute syntax highlight: make raw html turn into highlighted html
            highlighted_body=ViewSourceParser(body).handle_view_source()
            # after highlight html feed standard Parser make DOM tree
            self.nodes=HTMLParser(highlighted_body).parse()
        else:
            self.nodes=HTMLParser(body).parse()

        self.document=DocumentLayout(self.nodes)
        self.document.layout()

        self.display_list=[]
        paint_tree(self.document,self.display_list)
        self.draw()

    def draw(self):
        self.canvas.delete("all")
        for item in self.display_list:

            # DrawText/DrawRect architecture
            if hasattr(item,"execute"):
                if item.top > self.scroll + self.height:
                    continue

                if item.bottom < self.scroll:
                    continue

                item.execute(self.scroll,self.canvas)


            elif isinstance(item,tuple) and len(item)==3:

                x,y,img=item

                if y> self.scroll +self.height :continue
                if y+img.height() < self.scroll: continue

                self.canvas.create_image(
                    x,
                    y-self.scroll-2,
                    image=img,
                    anchor="nw"
                )

  
        #scrollbar section: put the loop outside, only draw once
            
        #calculate web total height
        document_height=self.document.height+2*VSTEP

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
        
        max_y=max(self.document.height+2*VSTEP-self.height,0)
        self.scroll=min(self.scroll+SCROLL_STEP,max_y)
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
        if hasattr(self,"nodes") and self.nodes:
            global WIDTH,HEIGHT
            WIDTH=self.width
            HEIGHT=self.height

            self.document=DocumentLayout(self.nodes)
            self.document.layout()

            self.display_list=[]
            paint_tree(self.document,self.display_list)
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

        decode_text=text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")

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

    target_url = URL(url_arg)
    body = target_url.request()

    if target_url.view_source:
        # if view-source mode，run syntax highlight parser
        highlighted_html = ViewSourceParser(body).handle_view_source()
        nodes = HTMLParser(highlighted_html).parse()
    else:
        # normal mode
        nodes = HTMLParser(body).parse()
    
    
    print("--- DOM Tree ---")
    print_tree(nodes)
    print("----------------")

    browser = Browser()
    browser.load(target_url)
    print("display items:", len(browser.display_list))
    print("document height:", browser.document.height)
    tkinter.mainloop()