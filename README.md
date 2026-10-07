# tai_gar

![tai_gar icon](tai_gar_icon.png)

[English](#english) | [中文](#中文)

---

## English

A simple web browser written in Python, built for learning how web browsers work.

### Install

```bash
pip install dukpy PySDL2 skia-python PyOpenGL
```

### Quick Start

```bash
python3 browser.py https://browser.engineering/
```

Or open any URL:

```bash
python3 browser.py <url>
```

### Tips

- **New window**: press `Ctrl+N`
- **Bookmarks**: click the star icon to bookmark a page, then type `about:bookmarks` in the address bar to view them

### Rendering

The browser renders with the GPU by default. At startup it draws a test frame on the GPU in a separate process. It uses the CPU if that process crashes, finds only a software renderer such as llvmpipe, or cannot load PyOpenGL. On WSL it also tries `GALLIUM_DRIVER=d3d12`. The first line of output names the chosen backend.

To choose a backend yourself:

```bash
BROWSER_RENDER_BACKEND=cpu python3 browser.py <url>
BROWSER_RENDER_BACKEND=gpu BROWSER_RASTER_MODE=sync python3 browser.py <url>
```

---

## 中文

一個用 Python 寫的簡易瀏覽器，用來學習瀏覽器的運作原理。

### 安裝

```bash
pip install dukpy PySDL2 skia-python PyOpenGL
```

### 快速開始

```bash
python3 browser.py https://browser.engineering/
```

或開啟任意網址：

```bash
python3 browser.py <url>
```

### 小技巧

- **開新視窗**：按 `Ctrl+N`
- **書籤**：點擊星號圖示加入書籤，在網址列輸入 `about:bookmarks` 查看所有書籤

### 繪製

瀏覽器預設用 GPU 繪製。啟動時會在另一個程序裡用 GPU 畫一張測試畫面。如果該程序崩潰、只找到 llvmpipe 之類的軟體繪製器，或無法載入 PyOpenGL，就改用 CPU。在 WSL 上也會嘗試 `GALLIUM_DRIVER=d3d12`。輸出的第一行會顯示選到的 backend。

手動指定 backend：

```bash
BROWSER_RENDER_BACKEND=cpu python3 browser.py <url>
BROWSER_RENDER_BACKEND=gpu BROWSER_RASTER_MODE=sync python3 browser.py <url>
```
