# tai_gar

![alt text](tai_gar_icon.png)

## introdeuce

this is a simple browser for learn how to make a web browser

## test website

### 測試基本英文字顯示

python3 browser.py  https://browser.engineering/examples/example1-simple.html

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

## reference

https://docs.python.org/3/library/socket.html  
https://docs.python.org/3/library/ssl.html  
https://browser.engineering/http.html  