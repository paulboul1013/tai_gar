import socket
import ssl
import sys
import time 
import gzip
import tkinter
from urllib.parse import unquote, quote_plus, quote
from html import unescape,escape
import webbrowser
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

INPUT_WIDTH_PX = 200

USE_RTL=False

# BLOCK_ELEMENTS = [
#     "html", "body", "article", "section", "nav", "aside",
#     "h1", "h2", "h3", "h4", "h5", "h6", "hgroup", "header",
#     "footer", "address", "p", "hr", "pre", "blockquote",
#     "ol", "ul", "menu", "li", "dl", "dt", "dd", "figure",
#     "figcaption", "main", "div", "table", "form", "fieldset",
#     "legend", "details", "summary"
# ]

def normalize_tk_weight(weight):
    if weight in ["normal","bold"]:
        return weight
    
    try:
        weight_num=int(weight)
    except ValueError:
        return "normal"

    return "bold" if weight_num >= 600 else "normal"

def normalize_tk_slant(style):
    if style in ["italic","oblique"]:
        return "italic"
    return "roman"

def get_font(size,weight,style,family=None):
    if not family:
        family="Times"

    weight=normalize_tk_weight(weight)
    style=normalize_tk_slant(style)

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

                target_size=16
                w=img.width()

                # opemoji pic is very big
                # need to shrink it 16x16
                # subsample(x) represent to shrink x times
                # 72x72 shrink 4 times-> 18x18 close to VSTEP(18)
                scale_factor=max(1,round(w/target_size))

                img=img.subsample(scale_factor,scale_factor)

                #save into cache
                emoji_cache[char]=img
                return img
            except Exception as e:
                print(f"Error loading emoji {char}: {e}")
                return None

    return None

def paint_tree(layout_object,display_list):
    should_paint =getattr(layout_object,"should_paint",lambda:True)

    if should_paint():

        cmds = layout_object.paint()

        for cmd in cmds:
            # DrawText / DrawRect / DrawLine / DrawOutline
            # normal object，can add attribute
            if hasattr(cmd,"execute"):
                cmd.layout_object = layout_object

            display_list.append(cmd)

    for child in layout_object.children:
        paint_tree(child,display_list)

def tree_to_list(tree,out):
    out.append(tree)
    for child in tree.children:
        tree_to_list(child,out)

    return out

def style_tag_text(node):
    out=[]
    
    for child in node.children:
        if isinstance(child,Text):
            out.append(child.text)

    return "".join(out)


class BrowserApp:
    def __init__(self):
        self.root=tkinter.Tk()
        self.windows = []
        self.visited_urls = set() 
        self.bookmarks = set()

    def new_window(self,url=None):
        if url is None:
            url = URL("https://browser.engineering/")

        if not self.windows: 
            window = self.root #create first window 
        else:
            window = tkinter.Toplevel(self.root) # create others windows

        browser_window = BrowserWindow(self,window)
        self.windows.append(browser_window)
        browser_window.new_tab(url)
        return browser_window

    def run(self):
        self.root.mainloop()

class Rect:
    def __init__(self,left,top,right,bottom):
        self.left=left
        self.top=top
        self.right=right
        self.bottom=bottom

    def contains_point(self,x,y):
        return (
            x >= self.left and x < self.right and
            y >= self.top and y < self.bottom
        )

class DrawText:
    def __init__(self,x1,y1,text,font,color):
        self.left=x1
        self.top=y1
        self.text=text
        self.font=font
        self.color=color

        self.width=font.measure(text)
        self.bottom=y1+font.metrics("linespace")

        self.rect=Rect(
            self.left,
            self.top,
            self.left+self.width,
            self.bottom
        )

    def execute(self,scroll,canvas):
        canvas.create_text(
            self.left,
            self.top-scroll,
            text=self.text,
            font=self.font,
            anchor="nw",
            fill=self.color,
        )

class DrawRect:
    def __init__(self,*args):
        if len(args)==2 and isinstance(args[0],Rect):
            rect,color=args
            self.rect=rect
            self.color=color
        else:
            x1,y1,x2,y2,color=args
            self.rect=Rect(x1,y1,x2,y2)
            self.color=color

        self.left=self.rect.left
        self.top=self.rect.top
        self.right=self.rect.right
        self.bottom=self.rect.bottom
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

class DrawLine:
    def __init__(self,x1,y1,x2,y2,color,thickness):
        self.rect=Rect(x1,y1,x2,y2)
        self.color=color
        self.thickness=thickness

        self.left=min(x1,x2)
        self.right=max(x1,x2)
        self.top=min(y1,y2)
        self.bottom=max(y1,y2)

    def execute(self,scroll,canvas):
        canvas.create_line(
            self.rect.left,
            self.rect.top-scroll,
            self.rect.right,
            self.rect.bottom-scroll,
            fill=self.color,
            width=self.thickness
        )

class DrawOutline:
    def __init__(self,rect,color,thickness):
        self.rect=rect
        self.color=color
        self.thickness=thickness

        self.left=rect.left
        self.top=rect.top
        self.right=rect.right
        self.bottom=rect.bottom

    def execute(self,scroll,canvas):
        canvas.create_rectangle(
            self.rect.left,
            self.rect.top-scroll,
            self.rect.right,
            self.rect.bottom-scroll,
            width=self.thickness,
            outline=self.color
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

class LineLayout:
    def __init__(self,node,parent,previous):
        self.node=node
        self.parent=parent
        self.previous=previous
        self.children=[]

        self.x=None
        self.y=None
        self.width=None
        self.height=None

    def layout(self):
        self.width=self.parent.width
        self.x=self.parent.x

        if self.previous:
            self.y = self.previous.y + self.previous.height
        else:
            self.y=self.parent.y
        
        for child in self.children:
            child.layout()

        if not self.children:
            self.height=0
            return

        line_width=sum(child.width+child.space_after for child in self.children)

        if self.children:
            line_width-=self.children[-1].space_after

        align=self.parent.node.style.get("text-align","left")

        if align=="center":
            cursor_x=self.x+(self.width-line_width)/2
        elif align=="right" or USE_RTL: # same as RTL
            cursor_x=self.x+self.width-line_width
        else:
            cursor_x=self.x

        for child in self.children:
            child.x=cursor_x
            cursor_x+= child.width+child.space_after            

#        if USE_RTL:
#            cursor_x=self.x+self.width-line_width
#        else:
#            cursor_x=self.x

        max_ascent = max([
            child.ascent
            for child in self.children
        ])

        max_descent = max([
            child.descent
            for child in self.children
        ])

        baseline = self.y+1.25*max_ascent

        normal_text_children=[
            child for child in self.children
            if isinstance(child,TextLayout) and not getattr(child,"is_sup",False)
        ]

        if normal_text_children:
            normal_ascent=max(child.ascent for child in normal_text_children)
        else:
            normal_ascent=max_ascent
        
        for child in self.children:
            if getattr(child,"is_sup",False):
                child.y=baseline-normal_ascent
            else:
                child.y=baseline-child.ascent


        self.height=1.25 *(max_ascent+max_descent)

    def paint(self):
        return []

class TextLayout:
    def __init__(self,node,word,parent,previous,
                is_sup=False,
                is_small_caps=False,
                space_after_override=None,
                font_family_override=None):
        self.node=node
        self.word=word
        self.children=[]
        self.parent=parent
        self.previous=previous


        self.is_sup=is_sup
        self.is_small_caps=is_small_caps
        self.space_after_override=space_after_override
        self.font_family_override=font_family_override

        self.x=None
        self.y=None
        self.width=None
        self.height=None

        self.font=None
        self.ascent=None
        self.descent=None
        self.space_after=None
        
    def layout(self):
        weight=self.node.style["font-weight"]
        
        style=self.node.style["font-style"]
        if style=="normal":
            style="roman"

        size = int(float(self.node.style["font-size"][:-2])*0.75)
        
        if self.is_sup:
            size=max(1,int(size/2))

        if self.is_small_caps:
            size=max(1,int(size*0.8))
            weight="bold"

        if self.font_family_override:
            family = self.font_family_override
        else:
            family = self.node.style["font-family"]

        self.font=get_font(size,weight,style,family=family)

        self.width=self.font.measure(self.word)
        self.height=self.font.metrics("linespace")

        self.ascent=self.font.metrics("ascent")
        self.descent=self.font.metrics("descent")
        
        if self.space_after_override is None:
            self.space_after=self.font.measure(" ")
        else:
            self.space_after=self.space_after_override
            

        self.x=None
#        if self.previous:
#            self.x=(
#                self.previous.x
#                +self.previous.width
#                +self.previous.space_after
#            )
#       else:
#            self.x=self.parent.x


    def paint(self):
        color=self.node.style["color"]
        return [DrawText(self.x,self.y,self.word,self.font,color)]

class InputLayout:
    def __init__(self,node,parent,previous):
        self.node = node
        self.children = []
        self.parent = parent
        self.previous = previous

        self.x = None
        self.y = None
        self.width = None
        self.height = None
        
        self.font= None
        self.ascent = None
        self.descent = None
        self.space_after = None

    def layout(self):
        weight = self.node.style["font-weight"]
        
        style = self.node.style["font-style"]
        if style == "normal":
            style = "roman"

        size = int(float(self.node.style["font-size"][:-2])*0.75)
        family = self.node.style["font-family"]

        self.font=get_font(size,weight,style,family=family)

        self.width = INPUT_WIDTH_PX
        self.height = self.font.metrics("linespace")

        self.ascent=self.font.metrics("ascent")
        self.descent=self.font.metrics("descent")
        self.space_after=self.font.measure(" ")

        self.x=None

    def self_rect(self):
        return Rect(
            self.x,
            self.y,
            self.x+self.width,
            self.y+self.height
        )

    def paint(self):
        cmds=[]

        bgcolor=self.node.style.get("background-color","transparent")
        if bgcolor!="transparent":
            cmds.append(DrawRect(
                self.self_rect(),
                bgcolor
            ))

        if self.node.tag=="input":
            text=self.node.attributes.get("value","")

        elif self.node.tag=="button":
            if len(self.node.children) ==1 and isinstance(self.node.children[0],Text):
                text= self.node.children[0].text

            else:
                print("Ignoring HTML contents insdie button")
                text = ""

        else:
            text = ""

        color=self.node.style["color"]
        cmds.append(DrawText(
            self.x,
            self.y,
            text,
            self.font,
            color
        ))

        if self.node.is_focused:
            cx = self.x + self.font.measure(text)
            cmds.append(DrawLine(
                cx,
                self.y,
                cx,
                self.y+self.height,
                "black",
                1
            ))

        return cmds
        

class EmojiLayout:
    def __init__(self,node,img,parent, previous, space_after):
        self.node=node
        self.img=img
        self.parent=parent
        self.previous=previous
        self.children=[]

        self.x=None
        self.y=None
        self.width=None
        self.height=None

        self.ascent=None
        self.descent=None
        self.space_after=space_after

    def layout(self):
        self.width=self.img.width()
        self.height=self.img.height()

        # let emoji bottom close to baseline
        self.ascent=self.height
        self.descent=0

        self.x=None
#        if self.previous:
#            self.x=(
#                self.previous.x
#                +self.previous.width
#                +self.previous.space_after
#            )
#        else:
#            self.x=self.parent.x


    def paint(self):
        return [(self.x,self.y,self.img)]
    

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

        #self.display_list=[]


    def should_paint(self):
        if isinstance(self.node,Text):
            return True

        if not isinstance(self.node,Element):
            return True

        return self.node.tag not in ["input","button"]

    def paint(self):
        cmds=[]

        bgcolor=self.node.style.get("background-color","transparent")

        if bgcolor!="transparent":
            x2,y2=self.x+self.width,self.y+self.height
            rect=DrawRect(self.x,self.y,x2,y2,bgcolor)
            cmds.append(rect)

        if isinstance(self.node,Element):

            if self.node.tag=="nav" and self.node.attributes.get("class") =="links":
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
                cmds.append(DrawText(self.x+4,self.y+2,"Table of Contents",font,"black"))

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
        #if self.layout_mode() == "inline":
         #   for item in self.display_list:
          #      if isinstance(item,tuple) and len(item)==5:
           #         x,y,word,font,color=item
            #        cmds.append(DrawText(x,y,word,font,color))

             #   else:
                    # keep origin emoji/image tuple
          #          cmds.append(item)

        return cmds

    def is_block_node(self,node):
        if not isinstance(node,Element):
            return False

        return node.style.get("display","inline") == "block"

    def child_groups(self):
        groups = []
        buffer = []

        def is_whitespace_text(node):
            return isinstance(node,Text) and node.text.isspace()

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
            # Add whitespace text node between h6 and p
            if isinstance(child, Element) and child.tag == "h6":
                j=i+1

                # skip pure whitespace text node between <h6> and <p>
                while j < len(all_children) and is_whitespace_text(all_children[j]):
                    j+=1

                if j < len(all_children):
                    next_child = all_children[j]

                    if isinstance(next_child, Element) and next_child.tag == "p":
                        if buffer:
                            groups.append(buffer)
                            buffer = []

                        # merge h6 + p into one inline/layout group
                        merged = [child] + next_child.children
                        groups.append(merged)

                        # skip h6,whitespace,and p
                        i=j+1
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
        if isinstance(self.node,Element) and self.node.tag in ["input","button"]:
            return "inline"

        if any(self.is_block_node(child)
                for node in self.nodes if isinstance(node,Element)
                for child in node.children):
            return "block"

        else:
            return "inline"

    def layout(self):
        self.x=self.parent.x
        available_width=self.parent.width

        # ident list items ，the text sits to the right of the bullet
        if isinstance(self.node,Element) and self.node.tag=="li":
            self.x+=20
            available_width-=20


        css_width=self.css_width()
        if css_width:
            self.width=css_width
        else:
            self.width=available_width

        if self.previous:
            self.y=self.previous.y+self.previous.height
        else:
            self.y=self.parent.y

        mode=self.layout_mode()

        toc_header_h = 0
        old_y = self.y

        # before layout clear chlidren,void resize
        self.children=[]

        if mode=="block":
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

        else:
            self.cursor_x=0
            self.is_sup=False
            self.is_abbr=False
            self.is_pre=False

            self.new_line()

            for node in self.nodes:
                self.recurse(node)

            # if last line is empty,remove it
            if self.children and not self.children[-1].children:
                self.children.pop()

        # block/inline layout children together
        for child in self.children:
            child.layout()

        # block:chlidren are BlockLayout
        # inline:children are LineLayout
        self.height=sum(child.height for child in self.children)+ toc_header_h

        # if have toc_header_h,reset y
        self.y=old_y

        css_height=self.css_height()
        if css_height:
            self.height=css_height

    def flush(self):
        pass
        # self.flush_line()
        # self.cursor_x=0
        
        # for rel_x,word,font in self.line:
        #     x=self.x+rel_x
        #     y=self.y+baseline-font.metrics("ascent")
        #     self.display_list.append((x,y,word,font))

    def parse_px(self, value):
        if value=="auto":
            return None
        
        if isinstance(value, str) and value.endswith("px"):
            try:
                return int(value[:-2])
            except ValueError:
                return None
        
        return None
        
    def css_width(self):
        if not isinstance(self.node, Element):
            return None
        
        return self.parse_px(self.node.style.get("width","auto"))

    def css_height(self):
        if not isinstance(self.node, Element):
            return None

        return self.parse_px(self.node.style.get("height","auto"))

    # convert CSS style into Tkinter font
    # font-size: 16px       -> 12pt
    # font-style: normal    -> roman
    # font-style: italic    -> italic
    # font-weight: bold     -> bold
    def font_helper(self,node,family=None):
        weight = node.style["font-weight"]

        style=node.style["font-style"]
        if style=="normal":
            style="roman"

        size=int(float(node.style["font-size"][:-2])*0.75)

        if self.is_sup:
            size=max(1,int(size/2))

        if family is None:
            family=node.style["font-family"]

        return get_font(size,weight,style,family=family)

    def open_tag(self, tag):
        # already handled in HTMLParser
        # if tag == 'h1 class="title"':
        #     self.flush_line()
        #     self.alignment = "center"
        if tag == "sup":
            self.is_sup = True
        if tag == "pre":
            self.is_pre = True
            if self.children and self.children[-1].children:
                self.new_line()
        elif tag == "abbr":
            self.is_abbr = True
        elif tag == "p":
            if self.children and self.children[-1].children:
                self.new_line()


    def close_tag(self, tag):
        if tag == "sup":
            self.is_sup = False
        if tag == "pre":
            self.is_pre = False
            if self.children and self.children[-1].children:
                self.new_line()
        elif tag == "abbr":
            self.is_abbr = False
        elif tag == "p":
            if self.children and self.children[-1].children:
                self.new_line()

    def recurse(self,tree):
        if isinstance(tree,Text):
            if self.is_pre:
                self.pre_word(tree, tree.text)
            else:
                # normal mode
                for word in tree.text.split():
                    self.word(tree, word)
        
        else:
            # if is script tag,just skip not render that child nodes(it's js code)
            if tree.tag in ["script","style"]:
                return

            if tree.tag == "br":
                self.new_line()
                return

            if tree.tag == "input" or tree.tag == "button":
                self.input(tree)
                return

            self.open_tag(tree.tag)

            for child in tree.children:
                self.recurse(child)
            
            self.close_tag(tree.tag)
        
    def append_pre_text(self,node,text):
        line=self.children[-1]
        previous_word = line.children[-1] if line.children else None

        text_layout=TextLayout(
            node,
            text,
            line,
            previous_word,
            self.is_sup,
            False,
            0,
            "Courier New",
        )

        line.children.append(text_layout)

        font=self.font_helper(node,family="Courier New")
        self.cursor_x+=font.measure(text)

    def pre_word(self,node,text):
        lines=text.split("\n")

        for i, line in enumerate(lines):
            # keep this line all content，include front whitespace，multi whitespace，tab
            if line:
                self.append_pre_text(node,line)

            else:
                # whitespace line must have height info，so append empty TextLayout
                if i!=len(lines)-1:
                    self.append_pre_text(node,"")
            
            # origin text have '\n' character，force to new line
            if i!=len(lines)-1:
                self.new_line()


    def new_line(self):
        self.cursor_x=0
        last_line=self.children[-1] if self.children else None
        new_line=LineLayout(self.node,self,last_line)
        self.children.append(new_line)
    
    def abbr_word(self,node,word):
        clean_word=word.replace("\xad","")
        
        if not clean_word:
            return

        normal_font=self.font_helper(node)
        space_w=normal_font.measure(" ")

        pieces=[]
        total_width=0

        for char in clean_word:
            if char.islower():
                display_char=char.upper()
                is_small_caps=True

                weight="bold"

                style=node.style["font-style"]
                if style=="normal":
                    style="roman"

                size=int(float(node.style["font-size"][:-2])*0.75)

                if self.is_sup:
                    size=max(1,int(size/2))

                size=max(1,int(size*0.8))

                family =node.style["font-family"]
                font=get_font(size,weight,style,family=family)

            else:
                display_char=char
                is_small_caps=False
                font=self.font_helper(node)

            w=font.measure(display_char)
            pieces.append((display_char,is_small_caps,w))
            total_width+=w

        if self.cursor_x+total_width > self.width and self.children[-1].children:
            self.new_line()

        for i,(display_char,is_small_caps,w) in enumerate(pieces):
            line=self.children[-1]
            previous_word=line.children[-1] if line.children else None

            if i==len(pieces)-1:
                space_after=space_w
            else:
                space_after=0

            text = TextLayout(
                node,
                display_char,
                line,
                previous_word,
                self.is_sup,
                is_small_caps,
                space_after,
            )

            line.children.append(text)

        self.cursor_x+=total_width+space_w
            

    def word(self,node,word):
        if self.is_abbr:
            self.abbr_word(node,word)
            return

        font=self.font_helper(node)
        clean_word=word.replace("\xad","")

        w=font.measure(clean_word)
        space_w=font.measure(" ")

        img=None
        if len(word)==1:
            img=get_emoji(word)
        
        if img:
            w=img.width()
            
            if self.cursor_x+w>self.width and self.children[-1].children:
                self.new_line()

            line=self.children[-1]
            previous=line.children[-1] if line.children else None

            emoji=EmojiLayout(node,img,line,previous,space_w)
            line.children.append(emoji)

            self.cursor_x+=w+space_w
            return
        
        if self.cursor_x+w > self.width and self.children[-1].children:
            self.new_line()

        line=self.children[-1]
        previous_word=line.children[-1] if line.children else None


        text=TextLayout(node,clean_word,line,previous_word,self.is_sup)
        line.children.append(text)

        self.cursor_x+=w+space_w

    def input(self,node):
        w = INPUT_WIDTH_PX
        
        if self.cursor_x+w > self.width and self.children[-1].children:
            self.new_line()

        line = self.children[-1]
        previous_word = line.children[-1] if line.children else None

        input_layout = InputLayout(node,line,previous_word)
        line.children.append(input_layout)

        weight = node.style["font-weight"]
        
        style = node.style["font-style"]
        if style=="normal":
            style="roman"

        size=int(float(node.style["font-size"][:-2])*0.75)
        family =node.style["font-family"]

        font = get_font(size,weight,style,family=family)

        self.cursor_x+=w+font.measure(" ")
        

    def flush_line(self):
        pass
        # if not self.line_buffer:
        #     # pre mode
        #     if self.is_pre:#force cursor_y down
        #         font = get_font(self.size, self.weight, self.style, family="Courier New")
        #         self.cursor_y += font.metrics("linespace") * 1.25

        #     return

        # # find heightest ascent and descent
        # max_ascent=0
        # max_descent=0

        # # buffer object into the display_list
        # for item_w,item_content in self.line_buffer:
        #     if isinstance(item_content,tuple):
        #         word,font,is_sup,color=item_content
        #         ascent=font.metrics("ascent")
        #         descent=font.metrics("descent")
        #     else:
        #         # pic (emoji)
        #         ascent=item_content.height()
        #         descent=0

        #     if ascent > max_ascent: max_ascent=ascent
        #     if descent > max_descent: max_descent=descent


        # # calculate baseline position
        # baseline=self.cursor_y+max_ascent*1.25

        # # calculate current line total width
        # line_width=sum(item[0] for item in self.line_buffer)

        # # decide starting cursor_x
        # if self.alignment=="center":
        #     # total usefull widht is self.width-HSTEP*2 (minus left and right margin)
        #     remaining_space=self.width-line_width
        #     cursor_x=max(0,remaining_space//2)

        # # base on baseline to put every object
        # elif USE_RTL:
        #     cursor_x=max(0,self.width-line_width)
        # else:
        #     cursor_x=0

        # for item_w,item_content in self.line_buffer:
        #     if isinstance(item_content,tuple):
        #         word,font,is_sup,color=item_content

        #         if is_sup:
        #             y=baseline-max_ascent
        #         else:
        #             y=baseline-font.metrics("ascent")

        #         #every single word's y =baseline - this word ascent
        #         self.display_list.append((self.x+cursor_x,self.y+y,word,font,color))
                
        #     else:
        #         # pic bottom is on the baseline
        #         img_offset=12
        #         y=baseline-item_content.height()
        #         self.display_list.append((self.x+cursor_x,self.y+y+img_offset,item_content))

        #     cursor_x+=item_w

        # # update next starting y coord  
        # self.cursor_y=baseline+max_descent*1.25
        
        # # clear buffer
        # self.line_buffer.clear()

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
        self.is_focused=False
    def __repr__(self):
        return repr(self.text)

class Element:
    def __init__(self,tag,attributes,parent):
        self.tag=tag
        self.attributes=attributes
        self.children=[]
        self.parent=parent
        self.is_focused=False
    def __repr__(self):
        # return "<"+self.tag+">"
        return "<"+self.tag+" "+str(self.attributes)+">"

class TagSelector:
    def __init__(self,tag):
        self.tag=tag
        self.priority=1

    def matches(self,node):
        return isinstance(node,Element) and node.tag==self.tag

class ClassSelector:
    def __init__(self, class_name):
        self.class_name=class_name

        # class selector must be have more priority than tag selector
        # tag selector priority is 1
        # class selector priority is 10
        self.priority=10

    def matches(self, node):
        if not isinstance(node, Element) :
            return False

        class_attr = node.attributes.get("class", "")
        classes = class_attr.split()

        return self.class_name in classes

class SelectorSequence:
    def __init__(self,selectors):
        self.selectors = selectors
        self.priority = sum(selector.priority for selector in selectors)

    def matches(self,node):
        for selector in self.selectors:
            if not selector.matches(node):
                return False
        return True

class HasSelector:
    def __init__(self,selector):
        self.selector=selector
        
        # :has(...) become pseudo-class, give 10 priority
        # add internal selector priority
        self.priority=10+selector.priority

    def matches(self,node):
        if not isinstance(node,Element):
            return False

        return self.has_matching_descendant(node)

    def has_matching_descendant(self,node):
        for child in node.children:
            if self.selector.matches(child):
                return True
            
            if self.has_matching_descendant(child):
                return True

        return False

class VisitedSelector:
    def __init__(self):
        # pseduo-class selector has same priority as class selector 
        self.priority=10

    def matches(self,node):
        return (
            isinstance(node,Element) 
            and node.tag=="a"
            and getattr(node,"is_visited",False)
        )

class DescendantSelector:
    def __init__(self,selectors):
        self.selectors=selectors
        self.priority=sum(selector.priority for selector in selectors)

    def matches(self,node):
        # rightmost selector must match current node
        selector_index=len(self.selectors)-1

        if not self.selectors[selector_index].matches(node):
            return False

        # then leftward find ancestor selectors
        selector_index-=1
        current=node.parent

        while selector_index >= 0 and current:
            if self.selectors[selector_index].matches(current):
                selector_index-=1
                
            current=current.parent
        
        return selector_index < 0

def cascade_priority(rule):
    selector, body=rule
    return selector.priority

class Chrome:
    def __init__(self,browser):
        self.browser=browser

        self.font=get_font(20,"normal","roman")
        self.font_height=self.font.metrics("linespace")

        self.padding=5

        # first row: tab bar
        self.tabbar_top=0
        self.tabbar_bottom=self.font_height+2*self.padding

        plus_width=self.font.measure("+") + 2*self.padding

        self.newtab_rect = Rect(
            self.padding,
            self.padding,
            self.padding+plus_width,
            self.padding+self.font_height
        )

        # second row: URL bar
        self.urlbar_top=self.tabbar_bottom
        self.urlbar_bottom=self.urlbar_top+self.font_height+2*self.padding

        back_width=self.font.measure("<")+2*self.padding
        forward_width=self.font.measure(">")+2*self.padding

        self.back_rect=Rect(
            self.padding,
            self.urlbar_top+self.padding,
            self.padding+back_width,
            self.urlbar_bottom-self.padding
        )

        self.forward_rect = Rect(
            self.back_rect.right+self.padding,
            self.urlbar_top+self.padding,
            self.back_rect.right+self.padding+forward_width,
            self.urlbar_bottom-self.padding
        )

        bookmark_width = self.font.measure("★")+2*self.padding

        self.bookmark_rect=Rect(
            self.forward_rect.right+self.padding,
            self.urlbar_top+self.padding,
            self.forward_rect.right+self.padding+bookmark_width,
            self.urlbar_bottom-self.padding
        )

        self.address_rect=Rect(
            self.bookmark_rect.right+self.padding,
            self.urlbar_top+self.padding,
            WIDTH-self.padding,
            self.urlbar_bottom-self.padding
        )

        self.bottom = self.urlbar_bottom

        self.focus=None
        self.address_bar = ""
        self.address_bar_cursor=0
        self.address_bar_dirty = False

    def tab_rect(self,i):
        tabs_start=self.newtab_rect.right+self.padding
        tab_width=self.font.measure("Tab X")+2*self.padding
        return Rect(
            tabs_start+tab_width*i,
            self.tabbar_top,
            tabs_start+tab_width*(i+1),
            self.tabbar_bottom
        )

    def clamp_address_bar_cursor(self):
        self.address_bar_cursor=max(
            0,
            min(self.address_bar_cursor,len(self.address_bar))
        )

    def blur_address_bar(self):
        self.focus=None

    def discard_address_bar_edit(self):
        self.focus = None
        self.address_bar = ""
        self.address_bar_cursor = 0
        self.address_bar_dirty = False

    def address_bar_display_text(self):
        if self.focus == "address bar" or self.address_bar_dirty:
            return self.address_bar
        
        if self.browser.active_tab and self.browser.active_tab.url:
            return str(self.browser.active_tab.url)
        
        return ""

    # convert mouse x coordinate to string index
    def cursor_index_from_x(self,x):
        local_x = x-self.address_rect.left-self.padding

        if local_x <= 0:
            return 0

        for i in range(len(self.address_bar)):
            left=self.font.measure(self.address_bar[:i])
            right=self.font.measure(self.address_bar[:i+1])
            mid=(left+right)/2

            if local_x < mid:
                return i

        return len(self.address_bar)

    def paint(self):
        cmds=[]

        # chrome background，must be first draw
        cmds.append(DrawRect(Rect(0,0,WIDTH,self.bottom),"white"))

        # new tab button
        cmds.append(DrawOutline(self.newtab_rect,"black",1))
        cmds.append(DrawText(
            self.newtab_rect.left+self.padding,
            self.newtab_rect.top,
            "+",
            self.font,
            "black"
        ))

        active_bounds=None

        for i,tab in enumerate(self.browser.tabs):
            bounds=self.tab_rect(i)

            cmds.append(DrawLine(
                bounds.left,0,
                bounds.left,bounds.bottom,
                "black",1
            ))

            cmds.append(DrawLine(
                bounds.right,0,
                bounds.right,bounds.bottom,
                "black",1
            ))

            cmds.append(DrawText(
                bounds.left+self.padding,
                bounds.top+self.padding,
                "Tab {}".format(i),
                self.font,
                "black"
            ))

            if tab==self.browser.active_tab:
                active_bounds=bounds

        # bottom line:active tab under line
        if active_bounds:
            cmds.append(DrawLine(
                0,active_bounds.bottom,
                active_bounds.left,active_bounds.bottom,
                "black",1
            ))

            cmds.append(DrawLine(
                active_bounds.right,active_bounds.bottom,
                WIDTH,active_bounds.bottom,
                "black",1
            ))

        else:
            cmds.append(DrawLine(
                0,self.bottom,
                WIDTH,self.bottom,
                "black",1
            ))

        # back button
        if self.browser.active_tab and self.browser.active_tab.can_go_back():
            back_color="black"
        else:
            back_color="gray"


        cmds.append(DrawOutline(self.back_rect,back_color,1))
        cmds.append(DrawText(
            self.back_rect.left+self.padding,
            self.back_rect.top,
            "<",
            self.font,
            back_color
        ))

        # forward button
        if self.browser.active_tab and self.browser.active_tab.can_go_forward():
            forward_color="black"
        else:
            forward_color="gray"

        cmds.append(DrawOutline(self.forward_rect,forward_color,1))
        cmds.append(DrawText(
            self.forward_rect.left+self.padding,
            self.forward_rect.top,
            ">",
            self.font,
            forward_color
        ))

        # bookmark button
        if self.browser.is_current_page_bookmarked():
            bookmark_bg="yellow"
            bookmark_fg="black"
        else:
            bookmark_bg="white"
            bookmark_fg = "black" if self.browser.current_url_string() else "gray"


        cmds.append(DrawRect(self.bookmark_rect,bookmark_bg))
        cmds.append(DrawOutline(self.bookmark_rect,bookmark_fg,1))
        cmds.append(DrawText(
            self.bookmark_rect.left+self.padding,
            self.bookmark_rect.top,
            "★",
            self.font,
            bookmark_fg
        ))

        # addres bar
        cmds.append(DrawOutline(self.address_rect,"black",1))

        display_text = self.address_bar_display_text()

        cmds.append(DrawText(
            self.address_rect.left+self.padding,
            self.address_rect.top,
            display_text,
            self.font,
            "black"
        ))

        # only draw cursor when address bar is focused
        # if dirty but not focus,show draft text but no cursor showing
        if self.focus=="address bar":

            self.clamp_address_bar_cursor()

            cursor_text = self.address_bar[:self.address_bar_cursor]
            w=self.font.measure(cursor_text)

            cursor_x = self.address_rect.left+self.padding+w

            cmds.append(DrawLine(
                cursor_x,
                self.address_rect.top,
                cursor_x,
                self.address_rect.bottom,
                "red",
                1
            ))
        # else:
        #     # no focus, display url
        #     if self.browser.active_tab and self.browser.active_tab.url:
        #         url=str(self.browser.active_tab.url)
        #     else:
        #         url=""

        #     cmds.append(DrawText(
        #         self.address_rect.left+self.padding,
        #         self.address_rect.top,
        #         url,
        #         self.font,
        #         "black"
        #     ))

        # chrome and website content split line
        cmds.append(DrawLine(
            0,
            self.bottom,
            WIDTH,
            self.bottom,
            "black",
            1
        ))

        return cmds

    def click(self,x,y):
        was_address_bar_focused = self.focus=="address bar"

        #click any chrome section，default is clear first
        self.focus=None
        
        if self.newtab_rect.contains_point(x,y):
            self.discard_address_bar_edit()
            self.browser.new_tab(URL("https://browser.engineering/"))
            return
        
        if self.back_rect.contains_point(x,y):
            self.discard_address_bar_edit()
            if self.browser.active_tab:
                self.browser.active_tab.go_back()
            return

        if self.forward_rect.contains_point(x,y):
            self.discard_address_bar_edit()
            if self.browser.active_tab:
                self.browser.active_tab.go_forward()
            return

        if self.bookmark_rect.contains_point(x,y):
            self.blur_address_bar()
            self.browser.toggle_bookmark()
            return

        if self.address_rect.contains_point(x,y):
            self.focus="address bar"
        
            # first click address bar: copy current page URL
            # If it was already focused, keep user's current editing text
            # If have dirty flag, keep it
            if not was_address_bar_focused and not self.address_bar_dirty:
                if self.browser.active_tab and self.browser.active_tab.url:
                    self.address_bar = str(self.browser.active_tab.url)
                else:
                    self.address_bar = ""

            self.address_bar_cursor = self.cursor_index_from_x(x)

            return
        
        for i, tab in enumerate(self.browser.tabs):
            if self.tab_rect(i).contains_point(x,y):
                tab.restyle()
                tab.relayout()
                self.browser.active_tab=tab
                return

    def is_url_like(self,text):
        return(
            "://" in text
            or text.startswith("about:")
            or text.startswith("data:")
            or text.startswith("file:")
            or text.startswith("view-source:")
            or text.startswith("mailto:")
        )

    def address_bar_to_url(self,text):
        text=text.strip()

        if self.is_url_like(text):
            return URL(text)

        query=quote_plus(text)
        return URL("https://html.duckduckgo.com/html/?q="+query)

    def keypress(self,char):
        if self.focus=="address bar":
            self.clamp_address_bar_cursor()

            i=self.address_bar_cursor
            self.address_bar =(
                self.address_bar[:i]
                + char
                + self.address_bar[i:]
            )

            self.address_bar_cursor += len(char)
            self.address_bar_dirty = True
            return True

        return False

    def enter(self):
        if self.focus=="address bar":
            url=self.address_bar_to_url(self.address_bar)

            if url.is_external():
                url.open_external()
                self.discard_address_bar_edit()
                return

            if self.browser.active_tab:
                self.browser.active_tab.load(url)
            else:
                self.browser.new_tab(url)

            self.discard_address_bar_edit()

    def backspace(self):
        if self.focus=="address bar":
            self.clamp_address_bar_cursor()

            if self.address_bar_cursor==0:
                return
                
            i = self.address_bar_cursor
            self.address_bar = (
                self.address_bar[:i-1]
                +self.address_bar[i:]
            )

            self.address_bar_cursor -= 1
            self.address_bar_dirty = True

    def left(self):
        if self.focus=="address bar":
            self.address_bar_cursor = max(0,self.address_bar_cursor-1)

    def right(self):
        if self.focus=="address bar":
            self.address_bar_cursor = min(
                len(self.address_bar),
                self.address_bar_cursor+1
            )
        

class Tab:
    def __init__(self,tab_height,visited_urls,bookmarks):
        self.width=WIDTH
        self.height=HEIGHT
        self.tab_height=tab_height

        self.display_list = []
        self.scroll = 0
        self.url=None
        self.nodes=None
        self.document=None

        self.focus=None

        self.visited_urls = visited_urls
        self.bookmarks = bookmarks
        self.rules=[]
        
        self.history=[]
        self.history_index=-1

    def is_internal_page(self,url):
        return url.scheme=="about" and url.path=="bookmarks"

    def request_internal_page(self,url):
        if url.path=="bookmarks":
            return self.bookmarks_page()
        
        return ""
        
    def bookmarks_page(self):
        out=[]
        out.append("<html>")
        out.append("<head><title>Bookmarks</title></head>")
        out.append("<body>")
        out.append("<h1>Bookmarks</h1>")

        if not self.bookmarks:
            out.append("<p>No bookmarks yet.</p>")
        else:
            out.append("<ul>")
            
            for url in sorted(self.bookmarks):
                safe_url=escape(url,quote=True)
                out.append(f'<li><a href="{safe_url}">{safe_url}</a></li>')

            out.append("</ul>")

        out.append("</body>")
        out.append("</html>")

        return "\n".join(out)

    def load(self, url,payload=None,add_to_history=True):
        self.url=url
        self.scroll=0

        self.visited_urls.add(str(url))

        if add_to_history:
            # if current page in the history，not last page
            #　represent user click back button
            # if click new link or enter new URL
            # forward history will be clear
            if self.history_index < len(self.history)-1:
                self.history=self.history[:self.history_index+1]

            self.history.append(url)
            self.history_index+=1

        

        if self.is_internal_page(url):
            body = self.request_internal_page(url)
            self.nodes=HTMLParser(body).parse()
        
        else:
            body = url.request(payload)

            if url.view_source:
                # execute syntax highlight: make raw html turn into highlighted html
                highlighted_body=ViewSourceParser(body).handle_view_source()
                # after highlight html feed standard Parser make DOM tree
                self.nodes=HTMLParser(highlighted_body).parse()
            else:
                self.nodes=HTMLParser(body).parse()

        rules=DEFAULT_STYLE_SHEET.copy()

        links = [node.attributes["href"]
                for node in tree_to_list(self.nodes,[])
                if isinstance(node,Element)
                and node.tag=="link"
                and node.attributes.get("rel")=="stylesheet"
                and "href" in node.attributes]

        for link in links:
            style_url=url.resolve(link)

            if style_url is None:
                continue

            try:
                body=style_url.request()
            except Exception:
                continue
                
            rules.extend(CSSParser(body).parse())

        # deal with <style>..</style> inline stylesheet
        style_nodes = [node
                      for node in tree_to_list(self.nodes,[])
                      if isinstance(node,Element)
                      and node.tag=="style"]

        for style_node in style_nodes:
            css_text=style_tag_text(style_node)
            rules.extend(CSSParser(css_text).parse())


        self.rules=sorted(rules,key=cascade_priority)

        self.focus=None

        self.render()

        if self.url.fragment:
            self.scroll_to_fragment(self.url.fragment)

        # self.document=DocumentLayout(self.nodes)
        # self.document.layout()

        # self.display_list=[]
        # paint_tree(self.document,self.display_list)
        # self.draw()

    def can_go_back(self):
        return self.history_index > 0

    def can_go_forward(self):
        return self.history_index < len(self.history)-1

    def go_back(self):
        if self.can_go_back():
            self.history_index-=1
            self.load(self.history[self.history_index],add_to_history=False)

        
    def go_forward(self):
        if self.can_go_forward():
            self.history_index+=1
            self.load(self.history[self.history_index],add_to_history=False)

    def get_title(self):
        if not self.nodes:
            return "Tai Gar"

        for node in tree_to_list(self.nodes,[]):
            if isinstance(node,Element) and node.tag=="title":
                title=style_tag_text(node).strip()

                if title:
                    return title

        return "Tai Gar"

    def mark_visited_links(self):
        if not self.nodes or not self.url:
            return

        for node in tree_to_list(self.nodes,[]):
            if not isinstance(node,Element):
                continue

            node.is_visited=False

            if node.tag != "a":
                continue

            if "href" not in node.attributes:
                continue

            try:
                link_url=self.url.resolve(node.attributes["href"])
            except Exception:
                continue

            if link_url is None:
                continue

            if str(link_url) in self.visited_urls:
                node.is_visited=True

    def restyle(self):
        if not self.nodes:
            return

        self.mark_visited_links()
        style(self.nodes,self.rules)

    def render(self):
        self.restyle()
        self.relayout()

    def relayout(self):
        self.document=DocumentLayout(self.nodes)
        self.document.layout()

        self.display_list=[]
        paint_tree(self.document,self.display_list)

    def scroll_to_fragment(self,fragment):
        if not fragment:
            return
        
        if not self.document:
            return

        for obj in tree_to_list(self.document,[]):
            node=getattr(obj,"node",None)

            if not isinstance(node,Element):
                continue

            if node.attributes.get("id") != fragment:
                continue

            if obj.y is None:
                continue

            max_y=max(self.document.height+2*VSTEP-self.tab_height,0)
            self.scroll=min(obj.y,max_y)

            return

    def navigate_to_fragment(self,fragment,add_to_history=True):
        url=self.url.with_fragment(fragment)
        self.url=url

        self.visited_urls.add(str(url))

        if add_to_history:
            if self.history_index < len(self.history)-1:
                self.history=self.history[:self.history_index+1]

            self.history.append(url)
            self.history_index+=1

        self.restyle()

        self.relayout()

        self.scroll_to_fragment(fragment)


    def draw(self,canvas,offset):
        # self.canvas.delete("all")
        for item in self.display_list:

            # DrawText/DrawRect architecture
            if hasattr(item,"execute"):
                if item.top > self.scroll + self.tab_height:
                    continue

                if item.bottom < self.scroll:
                    continue

                item.execute(self.scroll-offset,canvas)


            elif isinstance(item,tuple) and len(item)==3:

                x,y,img=item

                if y> self.scroll +self.tab_height :continue
                if y+img.height() < self.scroll: continue

                canvas.create_image(
                    x,
                    y-self.scroll+offset,
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
            canvas.create_rectangle(
                self.width-SCROLLBAR_WIDTH,
                bar_y,
                self.width,
                bar_y+bar_h,
                fill="blue",outline=""
            )

            

    def scrolldown(self):
        max_y=max(self.document.height+2*VSTEP-self.tab_height,0)
        self.scroll=min(self.scroll+SCROLL_STEP,max_y)

    def scrollup(self):
        self.scroll-=SCROLL_STEP
        if self.scroll<0:
            self.scroll=0

    def mousewheel(self,e):
        if e.delta>0:
            self.scrollup(e)
        else:
            self.scrolldown(e)

    def layout_object_at(self,x,y):
        # tab coordinate -> page coordinate
        y += self.scroll

        # reverse scan display list
        # first get top level draw command
        for cmd in reversed(self.display_list):
            # only deal with DrawText / DrawRect / DrawLine / DrawOutline
            # skip emoji tuple
            if not hasattr(cmd,"rect"):
                continue

            if not cmd.rect.contains_point(x,y):
                continue

            if not hasattr(cmd,"layout_object"):
                continue

            print("hit display command:",type(cmd).__name__)
            print("generated by layout object:",type(cmd.layout_object).__name__)

            return cmd.layout_object

        return None

    def href_at(self,x,y):
        obj = self.layout_object_at(x,y)

        if obj is None:
            return None

        # last matched layout object
        elt=obj.node

        while elt:
            if isinstance(elt,Element) and elt.tag=="a" and "href" in elt.attributes:
                return elt.attributes["href"]

            elt=elt.parent

        return None

    def link_at(self,x,y):
        href=self.href_at(x,y)
        
        if href is None:
            return None

        return self.url.resolve(href)

    def submit_form(self,elt):
        inputs = [
            node for node in tree_to_list(elt,[])
            if isinstance(node,Element)
            and node.tag == "input"
            and "name" in node.attributes
        ]

        body_parts = []

        for input in inputs:
            name = input.attributes["name"]
            value = input.attributes.get("value","")

            name = quote(name,safe="")
            value = quote(value,safe="")

            body_parts.append(name+"="+value)

        body = "&".join(body_parts)

        url = self.url.resolve(elt.attributes["action"])

        if url is None:
            return

        self.load(url,body)

    def click(self,x,y):
        if self.focus:
            self.focus.is_focused = False
            
        self.focus = None

        obj = self.layout_object_at(x,y)

        if obj is None:
            self.render()
            return

        elt = obj.node
        
        while elt:
            if isinstance(elt,Element) and elt.tag == "input":
                elt.attributes["value"] = ""
                self.focus = elt
                elt.is_focused = True
                self.render()
                return

            if isinstance(elt,Element) and elt.tag == "button":
                while elt:
                    if isinstance(elt,Element) and elt.tag == "form" and "action" in elt.attributes:
                        self.submit_form(elt)
                        return

                    elt = elt.parent

                self.render()
                return

            if isinstance(elt,Element) and elt.tag == "a" and "href" in elt.attributes:
                href = elt.attributes["href"]

                if href.startswith("#"):
                    self.navigate_to_fragment(href[1:])
                    return

                url=self.url.resolve(href)
                if url is None:
                    return

                if url.is_external():
                    url.open_external()
                    return

                self.load(url)
                return

            elt = elt.parent

        self.render()

    def keypress(self,char):
        if self.focus:
            self.focus.attributes["value"] += char
            self.render()

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


class BrowserWindow:
    def __init__(self,app,window):
        self.app=app
        self.window=window

        self.tabs=[]
        self.active_tab=None
        self.focus = None

        self.window.title("Tai Gar")

        self.canvas = tkinter.Canvas(
            self.window,
            width=WIDTH,
            height=HEIGHT,
            bg="white"
        )

        self.canvas.pack(fill=tkinter.BOTH,expand=True)

        self.chrome=Chrome(self)

        self.canvas.bind("<Configure>",self.resize)

        self.window.bind("<Down>",self.handle_down)
        self.window.bind("<Up>",self.handle_up)

        self.window.bind("<MouseWheel>",self.handle_mousewheel)
        self.window.bind("<Button-4>",self.handle_up)
        self.window.bind("<Button-5>",self.handle_down)

        self.window.bind("<Button-1>",self.handle_click)
        self.window.bind("<Button-2>",self.handle_middle_click)

        self.window.bind("<Key>",self.handle_key)
        self.window.bind("<Return>",self.handle_enter)
        self.window.bind("<BackSpace>",self.handle_backspace)
        self.window.bind("<Left>",self.handle_left)
        self.window.bind("<Right>",self.handle_right)

        self.window.bind("<Control-n>",self.handle_new_window)
        self.window.bind("<Control-N>",self.handle_new_window)

        self.window.protocol("WM_DELETE_WINDOW",self.close)

    def close(self):
        is_root_window  = self.window == self.app.root

        if self in self.app.windows:
            self.app.windows.remove(self) # remove closed window

        # still have other browser windows
        # if this window is root,not destroy it，withdraw window
        if self.app.windows:
            if is_root_window:
                self.window.withdraw()
            else:
                self.window.destroy()

            return

        # last window is root，can destroy
        self.app.root.destroy()

    def new_tab(self,url):
        new_tab=Tab(HEIGHT-self.chrome.bottom,self.app.visited_urls,self.app.bookmarks)
        
        new_tab.load(url)

        self.tabs.append(new_tab)
        self.active_tab=new_tab

        self.draw()

    def draw(self):
        self.canvas.delete("all")

        self.update_title()

        if self.active_tab:
            self.active_tab.draw(self.canvas,self.chrome.bottom)

        for cmd in self.chrome.paint():
            cmd.execute(0,self.canvas)

    def update_title(self):
        if self.active_tab:
            self.window.title(self.active_tab.get_title())
        else:
            self.window.title("Tai Gar")

    def current_url_string(self):
        if not self.active_tab:
            return None

        if not self.active_tab.url:
            return None

        url = str(self.active_tab.url)

        # first not allow bookmark internal page,avoid about:bookmarks marks self
        if url in ["about:blank","about:bookmarks"]:
            return None
        
        return url

    def is_current_page_bookmarked(self):
        url = self.current_url_string()
        return url is not None and url in self.app.bookmarks

    def toggle_bookmark(self):
        url = self.current_url_string()

        if url is None:
            return 

        if url in self.app.bookmarks:
            self.app.bookmarks.remove(url)
        else:
            self.app.bookmarks.add(url)

    def handle_down(self,e):
        if not self.active_tab:
            return

        self.active_tab.scrolldown()
        self.draw()

    def handle_up(self,e):
        if not self.active_tab:
            return

        self.active_tab.scrollup()
        self.draw()

    def handle_mousewheel(self,e):
        if not self.active_tab:
            return

        if e.delta > 0:
            self.active_tab.scrollup()
        else:
            self.active_tab.scrolldown()

        self.draw()

    def handle_click(self,e):
        if e.y < self.chrome.bottom:
            self.focus = None
            self.chrome.click(e.x,e.y)
        else:
            self.focus = "content"

            # click web page content:
            # blur address bar, keep url draft
            self.chrome.blur_address_bar()

            if not self.active_tab:
                return

            old_url = str(self.active_tab.url) if self.active_tab and self.active_tab.url else None

            tab_y=e.y-self.chrome.bottom
            self.active_tab.click(e.x,tab_y)

            new_url = str(self.active_tab.url) if self.active_tab and self.active_tab.url else None

            # If the page click actually navigated somewhere, discard the old draft
            if old_url != new_url:
                self.chrome.discard_address_bar_edit()

        self.draw()

    def handle_middle_click(self,e):
        if not self.active_tab:
            return

        # middle click on the chrome，do nothing
        if e.y < self.chrome.bottom:
            return


        # click web page content，clear address bar focus
        self.chrome.focus=None
        
        # window coordinate -> tab coordinate
        tab_y=e.y-self.chrome.bottom

        url = self.active_tab.link_at(e.x,tab_y)

        if url:
            if url.is_external():
                url.open_external()
                self.draw()
            else:
                self.new_tab(url)
        else:
            self.draw()

    def handle_key(self,e):
        if len(e.char)==0:
            return

        if not (0x20 <= ord(e.char) < 0x7f): #skip non ASCII characters
            return

        if self.chrome.keypress(e.char):
            self.draw()

        elif self.focus == "content" and self.active_tab:
            self.active_tab.keypress(e.char)
            self.draw()

    def handle_enter(self,e):
        self.chrome.enter()
        self.draw()

    def handle_backspace(self,e):
        self.chrome.backspace()
        self.draw()

    def handle_left(self,e):
        self.chrome.left()
        self.draw()

    def handle_right(self,e):
        self.chrome.right()
        self.draw()

    def handle_new_window(self,e):
        self.app.new_window(URL("https://browser.engineering/"))

    def resize(self,e):
        if e.width <=10 or e.height<=10:
            return

        global WIDTH,HEIGHT

        if WIDTH==e.width and HEIGHT == e.height:
            return

        WIDTH=e.width
        HEIGHT=e.height

        #rebuild chrome，let address bar with also change
        self.chrome=Chrome(self)

        for tab in self.tabs:
            tab.tab_height=HEIGHT-self.chrome.bottom

            if tab.nodes:
                tab.restyle()
                tab.relayout()

        self.draw()


class URL:
    def __init__(self, url):
        self.view_source=False
        self.scheme=""
        self.host=""
        self.path=""
        self.port=0
        self.fragment=""
        self.url_string=url
        
        try:

            # parse view-source        
            if url.startswith("view-source:"):
                # 例如 "view-source:http://google.com" 變成 "http://google.com"
                self.view_source=True
                _,url=url.split(":",1)

            # parse fragment: page.html#section
            if "#" in url:
                url,self.fragment=url.split("#",1)


            if url.startswith("about:"):
                self.scheme="about"
                self.path=url.split(":",1)[1]
                self.url_string = "about:"+self.path
                return

            if url.startswith("mailto:"):
                # self.scheme="mailto"
                self.scheme,self.path=url.split(":",1)
                self.url_string="mailto:"+self.path
                return

            
            if url.startswith("data:"):
                self.scheme="data"
                self.scheme,self.path = url.split(":", 1)
            else:
                if "://"  not in url:
                    raise ValueError("Malformed URL: missing ://")
                
                self.scheme, url = url.split("://", 1)

            # 支援的 URL Scheme
            if self.scheme not in ["http", "https","file","data","about","mailto"]:
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

    def request(self,payload=None):

        if self.scheme=="about":
            return ""

        if self.scheme=="data":   
            #example: text/html,Hello World!
            if "," in self.path:
                media_type,body=self.path.split(",",1)
                return unquote(body)
            else:
                return ""

        if self.scheme=="mailto":
            return """
            <html>
            <body>
                <h1>External mail link</h1>
                <p>This link should be opened by your mail application.</p>
            </body>
            </html>
            """


        if self.scheme=="file":
            try:
                with open(self.path,"r",encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                print(f"File read error: {e}")
                return f"""
                <html>
                <body>
                    <h1>File not found</h1>
                    <p>{self.path}</p>
                    <pre>{e}</pre>
                </body>
                </html>
                """
        
        current_url=self
        redirect_limit=10 

        while redirect_limit>0:
            
            if payload is None and current_url.url_string in http_cache:
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

            if payload is not None:
                headers["Content-Length"] = str(len(payload.encode("utf-8")))

            method = "POST" if payload is not None else "GET"
        
            request = "{} {} HTTP/1.1\r\n".format(method,current_url.path)

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

    def is_external(self):
        return self.scheme in ["mailto"]

    def open_external(self):
        if self.scheme=="mailto":
            print("open external URL:",str(self))
            ok = webbrowser.open(str(self))
            print("webbrowser.open returned:",ok)
            return True

        return False

    def __str__(self):
        fragment_part=""
        if self.fragment:
            fragment_part="#"+self.fragment

        if self.scheme=="mailto":
            return "mailto:"+self.path

        if self.view_source:
            return "view-source:" + self.url_string +fragment_part

        if self.scheme=="about":
            return "about:"+self.path +fragment_part

        if self.scheme=="data":
            return "data:"+self.path +fragment_part

        if self.scheme=="file":
            return "file://"+self.path +fragment_part

        port_part=":"+str(self.port)

        if self.scheme=="https" and self.port==443:
            port_part=""

        if self.scheme=="http" and self.port==80:
            port_part=""

        return self.scheme+"://"+self.host+port_part+self.path+fragment_part

    def with_fragment(self,fragment):
        base=str(self).split("#",1)[0]

        if fragment:
            return URL(base+"#"+fragment)

        else:
            return URL(base)

    def resolve(self,url):
        if url is None:
            return None

        url=url.strip()
        
        if not url:
            return self

        # fragment-only relative URL: #section
        if url.startswith("#"):
            return self.with_fragment(url[1:])


        if url.startswith("//"): # scheme-relative URL: //example.com/path
            return URL(self.scheme+":"+url)

        # explicit URL with scheme
        # examples: http:, https:, data:, file:, view-source:, mailto
        if ":" in url.split("/",1)[0]: 
            scheme = url.split(":",1)[0].casefold()

            if scheme in ["http", "https", "file", "data", "about", "view-source", "mailto"]:
                return URL(url)

            # unsupported scheme: javascript:,tel:,sms:,ftp:,..
            return None

        # path-relative URL: page.html
        dir,_ = self.path.rsplit("/",1) 
        while url.startswith("../"): # deal with relative URL parent directory `..`
            _,url= url.split("/",1)
            if "/" in dir:
                dir, _ = dir.rsplit("/",1)

        # host-relative URL
        if url.startswith("/"):
            if self.scheme in ["http","https"]:
                return URL(self.scheme+"://"+self.host+":"+str(self.port)+dir+"/"+url)

            if self.scheme=="file":
                return URL("file://"+dir+"/"+url)

            return None

        return URL(self.scheme+ "://" +self.host+":"+str(self.port)+dir+"/"+url)
        

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

        decode_text=unescape(text)

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

class CSSParser:
    def __init__(self,s):
        self.s=s
        self.i=0

    def whitespace(self):
        while self.i < len(self.s) and self.s[self.i].isspace():
            self.i+=1
    
    def literal(self,literal):
        if not (self.i < len(self.s) and self.s[self.i]==literal):
            raise Exception("Parsing error")

        self.i+=1

    def word(self):
        start=self.i
        while self.i < len(self.s):
            c=self.s[self.i]
            if c.isalnum() or c in "#-.%":
                self.i+=1
            else:
                break

        if self.i <= start:
            raise Exception("Parsing error")
        return self.s[start:self.i]

    # read tag name and class name
    # span.announce
    # read only span tag and then encounter "." stop
    def identifier(self):
        start = self.i
        
        while self.i < len(self.s):
            c = self.s[self.i]

            if c.isalnum() or c in "-_":
                self.i+=1
            else:
                break

        if self.i <= start:
            raise Exception("Parsing error")
        
        return self.s[start:self.i]

    def value(self):
        values=[]
        important = False

        while self.i < len(self.s) and self.s[self.i] not in ";}":
            self.whitespace()

            if self.i >= len(self.s) or self.s[self.i] in ";}":
                break

            if self.s[self.i] == "!":
                self.literal("!")
                self.whitespace()

                word =self.identifier().casefold()
                if word!="important":
                    raise Exception("Parsing error")

                important = True
                self.whitespace()
            else:
                values.append(self.word())
                self.whitespace()

        return values,important

    def pair(self):
        prop=self.word()
        self.whitespace()
        self.literal(":")
        self.whitespace()

        vals, important =self.value()

        return prop.casefold(), vals, important 

    def font_shorthand(self,values):
        out={}
        family=[]
        saw_size=False

        
        for value in values:
            lowered= value.casefold()

            if lowered=="italic":
                out["font-style"]="italic"

            elif lowered=="bold":
                out["font-weight"] = "bold"

            elif lowered=="normal":
                out["font-style"] = "normal"
                out["font-weight"] = "normal"
                # out.setdefault("font-style","normal")
                # out.setdefault("font-weight","normal")

            elif lowered.endswith("px") or lowered.endswith("%"):
                out["font-size"] = lowered
                saw_size = True

            else:
                if saw_size:
                    family.append(lowered)

        if family:
            out["font-family"]=" ".join(family)

        return out

    def ignore_until(self,chars):
        while self.i < len(self.s):
            if self.s[self.i] in chars:
                return self.s[self.i]
            self.i+=1

        return None

    def body(self):
        pairs={}
        while self.i < len(self.s) and self.s[self.i]!="}":
            try:
                prop, vals, important=self.pair()

                if prop=="font":
                    expanded = self.font_shorthand(vals)

                    for subprop, subvalue in expanded.items():
                        pairs[subprop]=(subvalue,important)

                else:
                    if len(vals)==1:
                        value=vals[0]
                    else:
                        value=" ".join(vals)
                
                    pairs[prop]=(value,important)

                self.whitespace()

                if self.i < len(self.s) and self.s[self.i]==";":
                    self.literal(";")
                    self.whitespace()

            except Exception:
                why=self.ignore_until([";","}"])
                if why==";":
                    self.literal(";")
                    self.whitespace()
                else:
                    break

        return pairs

    def parenthesized_selector(self):
        self.literal("(")
        
        start=self.i
        depth=1

        while self.i < len(self.s) and depth > 0:
            if self.s[self.i]=="(":
                depth+=1
            
            elif self.s[self.i]==")":
                depth-=1

                if depth==0:
                    break

            self.i+=1

        if depth!=0:
            raise Exception("Parsing error")

        inner=self.s[start:self.i]
        self.literal(")")
        
        parser=CSSParser(inner)
        return parser.selector()

    def simple_selector(self):
        selectors = []

        # optional tag selector
        # ex:
        # span.announce
        # div.card.highlight
        # div.card:has(span)

        # if current scan "." or ":"，it's no tag selector
        if self.i < len(self.s) and self.s[self.i] not in ".:":
            tag = self.identifier().casefold()
            selectors.append(TagSelector(tag))

        #  class selectors or has selectors
        while self.i < len(self.s):
            if self.s[self.i]==".":
                self.literal(".")
                class_name=self.identifier()
                selectors.append(ClassSelector(class_name))
            
            elif self.s[self.i]==":":
                self.literal(":")
                pseudo = self.identifier().casefold()

                if pseudo=="has":
                    inner_selector = self.parenthesized_selector()
                    selectors.append(HasSelector(inner_selector))
                
                elif pseudo=="visited":
                    selectors.append(VisitedSelector())

                else:
                    raise Exception("Parsing error")

            else:
                break

        if len(selectors)==0:
            raise Exception("Parsing error")
            
        if len(selectors)==1:
            return selectors[0]


        return SelectorSequence(selectors)

    def selector(self):
        selectors=[self.simple_selector()]
        self.whitespace()

        while self.i < len(self.s) and self.s[self.i] !="{":
            selectors.append(self.simple_selector())
            self.whitespace()

        if len(selectors)==1:
            return selectors[0]
        else:
            return DescendantSelector(selectors)

    def parse(self):
        rules=[]
        while self.i < len(self.s):
            try:
                self.whitespace()
                selector=self.selector()
                self.literal("{")
                self.whitespace()
                body=self.body()
                self.literal("}")
                rules.append((selector,body))
            except Exception:
                why=self.ignore_until(["}"])
                if why=="}":
                    self.literal("}")
                    self.whitespace()
                else:
                    break
        return rules


INHERITED_PROPERTIES = {
    "font-size": "16px",
    "font-style": "normal",
    "font-weight" : "normal",
    "color" : "black",
    "font-family": "Times",
    "text-align":"left",
}

NON_INHERITED_PROPERTIES = {
    "width" : "auto",
    "height" : "auto",
    "display" : "inline",
}

DEFAULT_STYLE_SHEET=CSSParser(open("browser.css").read()).parse()

IMPORTANT_OFFSET = 10000
INLINE_STYLE_PRIORITY = 1000

def apply_style(node,prop,value,priority):
    old_priority=node.style_priority.get(prop,-1)

    # when same priority, last rule cover previous rule
    if priority >= old_priority:
        node.style[prop]=value
        node.style_priority[prop]=priority

def style(node,rules):
    node.style={}
    node.style_priority={}
    
    # first deal with inherited properties
    # if no node specify property, inherit from parent
    for property, default_value in INHERITED_PROPERTIES.items():
        if node.parent:
            node.style[property]=node.parent.style[property]
        else:
            node.style[property]=default_value

        # inheritance value can't inherit important
        node.style_priority[property]=0

    # deal css format width and height default auto
    for property, default_value in NON_INHERITED_PROPERTIES.items():
        node.style[property]=default_value
        node.style_priority[property]=0

    # If is element, picked by CSS selector
    if isinstance(node,Element):
        # first deal with stylesheet rules
        for selector, body in rules:
            if not selector.matches(node):
                continue

            # debug rules
            # print("MATCH",node,selector,body)

            for prop, pair in body.items():
                value, important = pair
                
                priority = selector.priority
                if important:
                    priority += IMPORTANT_OFFSET

                apply_style(node,prop,value,priority)

        # embedded inline style, let inline sytle cover stylesheet
        if "style" in node.attributes:
            pairs=CSSParser(node.attributes["style"]).body()

            for prop, pair in pairs.items():
                value, important = pair
                
                priority = INLINE_STYLE_PRIORITY
                if important:
                    priority += IMPORTANT_OFFSET

                apply_style(node,prop,value,priority)

    #handle CSS inherit keyword
    for property in INHERITED_PROPERTIES:
        if node.style.get(property) == "inherit":
            if node.parent:
                node.style[property]=node.parent.style[property]
            else:
                node.style[property]=INHERITED_PROPERTIES[property]

    # handle unsupported font-size keyword
    font_size=node.style.get("font-size","16px")

    if (not font_size.endswith("px") and not font_size.endswith("%")):
        if node.parent:
            node.style["font-size"]=node.parent.style["font-size"]
        else:
            node.style["font-size"]=INHERITED_PROPERTIES["font-size"]

    # convert the percentage of font-size to px
    # example: 150% -> parent_font_size * 1.5
    if node.style["font-size"].endswith("%"):
        if node.parent:
            parent_font_size=node.parent.style["font-size"]
        else:
            parent_font_size=INHERITED_PROPERTIES["font-size"]

        node_pct=float(node.style["font-size"][:-1]) / 100
        parent_px = float(parent_font_size[:-2])
        node.style["font-size"] = str(node_pct * parent_px) + "px"

    # finally recursively DOM tree
    # because child need inhertied parent already computed style
    for child in node.children:
        style(child,rules)

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

    
    if len(sys.argv) >= 2:
        url = URL(sys.argv[1])
    else:
        url = URL("https://browser.engineering/")


    app = BrowserApp()
    main_window=app.new_window(url)
    

    print("--- DOM Tree ---")
    print_tree(main_window.active_tab.nodes)
    print("----------------")
    print("display items:", len(main_window.active_tab.display_list))
    print("document height:", main_window.active_tab.document.height)

    app.run()