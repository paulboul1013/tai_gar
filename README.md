# tai_gar

![alt text](tai_gar_icon.png)

## introdeuce

this is a simple browser for learn how to make a web browser

## test website

### 測試基本英文字顯示

python3 browser.py  https://browser.engineering/examples/example1-simple.html
python3 browser.py  https://browser.engineering/

### 測試重導向

python3 browser.py http://browser.engineering/redirect3

### 測試中文字顯示

python3 browser.py https://browser.engineering/examples/xiyouji.html

### 測試emoji

python3 browser.py "data:text/html,Hello 😀 World! 😃"

### 測試空白頁面

python3 browser.py about:blank

### 測試由右到左的文字顯示
python3 browser.py --rtl https://browser.engineering/examples/xiyouji.html

### 測試粗體文字和斜文字的組合顯示
python3 browser.py "data:text/html,Normal-Text. <b>Bold-Text</b>. <i>Italic-Text</i>. <b><i>Bold-And-Italic</i></b>. Back-To-Normal."

### 測試粗體文字，斜文字，圖片(emoji)的組合顯示
python3 browser.py 'data:text/html,1. Normal text here.<br>2. <b>This is Bold text!</b><br>3. <i>This is Italic text.</i><br>4. <b><i>This is Bold AND Italic!</i></b><br>5. Emoji test: <b>Bold</b> and <i>Italic 🚀</i>'

### 測試大小文字顯示
python3 browser.py 'data:text/html,Normal Text, <small>Small Text,</small> Normal, <big>Big Text,</big> Normal.'

### 測試 Gzip 壓縮
httpbin 的 /gzip 接口會回傳 gzip 壓縮後的 json 資料
python3 browser.py http://httpbin.org/gzip

### 測試快取功能
python3 browser.py http://httpbin.org/cache/10

### 測試轉址功能
python3 browser.py http://browser.engineering/redirect

### 測試連續請求同一個網站，驗證 Socket Reuse
python3 browser.py http://browser.engineering/examples/example1-simple.html
python3 browser.py http://browser.engineering/examples/example1-simple.html

### 測試View-Source 模式

python3 browser.py 'view-source:data:text/html,Hello <b>World</b>! &lt;div&gt;Test&lt;/div&gt;\n'


# 測試:Text of differnet sizes look on the same line，not ony big or small looking
python3 browser.py 'data:text/html,Normal Text, <small>Small Text,</small> Normal, <big>Big Text,</big> Normal 😀'


python3 browser.py 'data:text/html,Line 1: <big>Very Big</big><br>Line 2: <small>Very Small</small><br>Line 3: Normal 😀'

# 測試:font-cache的影響
python3 browser.py https://browser.engineering/text.html

# 測試:Text alignment
python3 browser.py 'data:text/html,normal text<h1 class="title">this is center aligned</h1>normal text'

# 測試:Superscript
python3 browser.py 'data:text/html,Normal text, <sup>superscript</sup>, normal text.'

# 測試:Abbreviation
python3 browser.py 'data:text/html,Normal text, <abbr>Abbreviation</abbr>, normal text.'

python3 browser.py 'data:text/html,<abbr>Tel: 0912-abc</abbr>'

python3 browser.py 'data:text/html,<sup><abbr>small</abbr></sup>'

python3 browser.py 'data:text/html ,<abbr>bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb</abbr>'


### 測試:Pre mode
python3 browser.py 'data:text/html,Line 1: <pre>Pre Text</pre><br>Line 2: Normal Text'

python3 browser.py 'data:text/html,<pre> Spaces are pre`served</pre>'

python3 browser.py 'data:text/html,<pre>Normal <b>Bold</b> <i>Italic</i></pre>'

python3 browser.py 'data:text/html ,<pre>bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb</pre>'

### 測試:Comment
python3 browser.py 'data:text/html,<h1>Hello</h1><!-- <div>這是一段被隱藏的文字</div> --><h2>World</h2>'

### 測試:auto-closing tags
python3 browser.py 'data:text/html,<ul><li>Item 1<p>paragraph</li><li>Item 2</li><li>Item 3</li></ul>'

python3 browser.py 'data:text/html,<ul><li>Item 1<li>Item 2</ul>'

### 測試:script tag
python3 browser.py 'data:text/html,<script>if (1 < 2) { console.log("Success"); }</script><p>Hello World</p>'

### 測試:quoted attributes
python3 browser.py 'data:text/html,<input value="1 > 2" placeholder="Space Test"><p>Done</p>'

### 測試:Syntax highlighting:show tag and bold text
python3 browser.py 'view-source:data:text/html,<html><body><h1>Title</h1><p>Hello</p></body></html>'

python3 browser.py 'view-source:data:text/html,<html>
<body>
  <h1>Title</h1>
  <p>Hello</p>
</body>
</html>'

python3 browser.py "view-source:data:text/html,<html>\n  <body>\n    <h1>Title</h1>\n  </body>\n</html>"

### 測試:layout
python3 browser.py view-source:https://browser.engineering/layout.html
python3 browser.py "data:text/html,<pre>hello world</pre><p>after</p>"
python3 browser.py "data:text/html,<pre>one</pre><pre>two</pre>"
python3 browser.py "data:text/html,<p>aaa</p><p>bbb</p>"
python3 browser.py "data:text/html,<pre>one</pre><p>gap</p><pre>two</pre>"

### 測試nav
python3 browser.py https://browser.engineering/layout.html
python3 browser.py "data:text/html,<nav class='links'>Back Chapter Next</nav><p>body</p>"

### 測試hidden head
python3 browser.py "data:text/html,<head><title>Hidden</title></head><body><p>Visible</p></body>"

### 測試bullets
python3 browser.py "data:text/html,<ul><li>one<ul><li>two</li></ul></li><li>three</li></ul>"
python3 browser.py "data:text/html,<ul><li>one</li><li>two</li></ul>"

### 測試table of contents
python3 browser.py "data:text/html,<nav id='toc'><ul><li>one</li><li>two</li></ul></nav>"

### 測試 Anonymous block boxes
python3 browser.py 'data:text/html,<div><i>Hello, </i><b>world!</b><p>So it began...</p></div>'
python3 browser.py 'data:text/html,<div><i>A</i><b>B</b><p>C</p><i>D</i><b>E</b></div>'

### 測試 Run-ins
python3 browser.py 'data:text/html,<div><h6>Run-ins.</h6> A run-in heading is part of the paragraph.</div>'

python3 browser.py 'data:text/html,<h6>Run-ins.</h6><p>A run-in heading is part of the paragraph.</p>'

### 測試css style
python3 browser.py "data:text/html,<pre style='background-color:lightblue'>Inline should override browser.css</pre>"

### 測試font-family
python3 browser.py "data:text/html,<p>normal text <code>int main() { return 0; }</code> normal again</p>"
python3 browser.py "data:text/html,<p>normal: iiiii WWWWW <code>code: iiiii WWWWW</code> normal again</p>"

### 測試css width,height
python3 browser.py "data:text/html,<div style='width:200px;background-color:lightblue;'>one two three four five six seven eight nine ten eleven twelve thirteen fourteen</div><div style='background-color:pink;'>after block</div>"

python3 browser.py "data:text/html,<div style='width:200px;height:60px;background-color:lightblue;'>one two three four five six seven eight nine ten eleven twelve thirteen fourteen</div><div style='background-color:pink;'>after block</div>"

### 測試css class selector
python3 browser.py "data:text/html,<p class='main'>this should be red</p>"
python3 browser.py "data:text/html,<p class='main'>p is black, .main is red, final should be red</p>"
python3 browser.py "data:text/html,<p class='main warning'>red and bold text</p>"
python3 browser.py "data:text/html,<div class='box'>normal <span class='keyword'>keyword blue</span> normal</div>"

### 測試css display
python3 browser.py "data:text/html,<div>first div</div><div>second div</div>"
python3 browser.py "data:text/html,<span>first span</span><span>second span</span>"
python3 browser.py "data:text/html,<span style='display:block;background-color:lightblue;'>first span</span><span style='display:block;background-color:pink;'>second span</span>"
python3 browser.py "data:text/html,<div style='display:inline;'>first div</div><div style='display:inline;'>second div</div>"

### 測試font-family
python3 browser.py "data:text/html,<p style='font:italic bold 150% Courier;'>font shorthand works</p>"
python3 browser.py "data:text/html,<p><b><i><span class='normal-test'>normal class inside bold italic</span></i></b></p>"

### 測試inline style sheet
python3 browser.py "data:text/html,<style>p { color: red; }</style><p>this should be red</p>"
python3 browser.py "data:text/html,<style>.main { color: blue; font-weight: bold; }</style><p class='main'>blue bold text</p>"
python3 browser.py "data:text/html,<style>.box { background-color: lightblue; width: 300px; }</style><div class='box'>inline stylesheet box</div>"
python3 browser.py "data:text/html,<body><style>p { color: red; }</style><p>only this should show</p></body>"

### 測試Fast descendant selectors
python3 browser.py "data:text/html,<style>div span { color: red; }</style><div><p><span>red text</span></p></div>"
python3 browser.py "data:text/html,<style>div div div div div { color: red; }</style><div><div><div><div><div>deep red</div></div></div></div></div>"
python3 browser.py "data:text/html,<style>div span { color: blue; }</style><div><section><article><p><span>deep blue</span></p></article></section></div>"
python3 browser.py "data:text/html,<style>.outer div .target { color: purple; font-weight: bold; }</style><section class='outer'><article><div><p><span class='target'>purple bold</span></p></div></article></section>"
python3 browser.py "data:text/html,<style>div span { color: red; }</style><section><p><span>should stay black</span></p></section>"

###　測試Selector sequences
python3 browser.py "data:text/html,<style>span.announce { color: red; }</style><span class='announce'>red announce</span>"
python3 browser.py "data:text/html,<style>span.announce { color: red; }</style><div class='announce'>should stay black</div>"
python3 browser.py "data:text/html,<style>span.announce { color: red; }</style><div class='announce'>should stay black</div>"
python3 browser.py "data:text/html,<style>span.announce { color: red; }</style><span>should stay black</span>"
python3 browser.py "data:text/html,<style>div.card.highlight { color: blue; font-weight: bold; }</style><div class='card highlight'>blue bold</div>"


## go further
1. 做出跨裝置瀏覽器

## reference

https://docs.python.org/3/library/socket.html  
https://docs.python.org/3/library/ssl.html  
https://browser.engineering/http.html  