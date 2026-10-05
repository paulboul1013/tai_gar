---
title: tai_gar 瀏覽器實作進度
subtitle: 對照 Web Browser Engineering 官方線上版
template: sheet
theme: shadcn
cols: 3
date: 2026-10-04
commit: 7d536e0
---

## A 目前做到哪裡 {span=2}

**已提交版本：第 13 章 JavaScript Animations。**

**GPU Acceleration 原型已在 AMD 硬體實測（2026-10-05 更新）。**

下一個實作階段是 Compositing Leaves 的合成圖層。

依[第 13 章正文順序](https://browser.engineering/animations.html)對照：

| 官方小節 | repo 狀態 | 判斷證據 |
| --- | --- | --- |
| [JavaScript Animations](https://browser.engineering/animations.html#javascript-animations) | ok 已有 | RAF 回呼修改 Node.style；opacity 進入 Blend。 |
| [GPU Acceleration](https://browser.engineering/animations.html#gpu-acceleration) | ok 已有，已實測硬體加速 | GL context、GPU surfaces 與 SwapWindow；實際 renderer 為 `D3D12 (AMD Radeon(TM) Graphics)`。 |
| [Compositing](https://browser.engineering/animations.html#compositing) | warn 尚缺本節的圖層模型 | 目前快取整頁區域與 chrome surface。 |
| [Compositing Leaves](https://browser.engineering/animations.html#compositing-leaves) | no 未實作 | 無 CompositedLayer 與 DrawCompositedLayer。 |
| [CSS Transitions](https://browser.engineering/animations.html#css-transitions) | no 未實作 | opacity 直接跳至終值，沒有逐幀插值。 |
| [Composited Animations](https://browser.engineering/animations.html#composited-animations) | no 未實作 | 無 composited_updates；opacity 仍重建 layout。 |
| [Optimizing Compositing](https://browser.engineering/animations.html#optimizing-compositing) | no 未實作 | 無 display-list 圖層分組與合併。 |
| [Overlap and Transforms](https://browser.engineering/animations.html#overlap-and-transforms) | no 未實作 | 無 CSS translate 與圖層 overlap 處理。 |

正文只實作 opacity transition。
transform transition 屬 Exercise 13-3。

## B 第 12 章有 API 回退

HEAD `7d536e0` 刪除了 `SCHEDULING_RUNTIME_JS`。
`runtime.js` 僅補入 RAF 與 Node.style。

實際建立 JSContext 後得到：

| JavaScript API | 執行結果 |
| --- | --- |
| requestAnimationFrame | ok function |
| setTimeout | no undefined |
| setInterval | no undefined |
| clearInterval | no undefined |
| runXHROnload | no undefined |
| XHR.open(..., true) | no 拋出不支援錯誤 |

Python handlers 仍存在，JS 橋接缺失。

`setTimeout`、async XHR 是[第 12 章正文](https://browser.engineering/scheduling.html)要求。
`setInterval`、`clearInterval` 屬 Exercise 12-1。

程式證據：`runtime.js:292`、`browser.py:4437`。
Commit 刪除內容可用 `git show 7d536e0` 查看。

## C 其他章節涵蓋程度 {span=2}

章號以[官方目錄](https://browser.engineering/)為準。
「主要功能已有」不代表所有習題都完成。

| 章節 | 判斷 | repo 證據與缺口 |
| --- | --- | --- |
| 1–3 下載與文字 | ok 主要功能已有 | URL、HTTP/TLS、文字排版；另有 gzip、快取、emoji。 |
| 4–6 HTML／layout／CSS | ok 主要功能已有 | HTMLParser、Block/Line/TextLayout、CSSParser、cascade。 |
| 7–9 導覽／表單／JS | ok 主要功能已有 | Chrome、tabs、history、POST、DOM 修改、事件冒泡。 |
| 10 隱私與安全 | ok 主要功能已有 | cookies、SameSite/HttpOnly、CSP、CORS；測試伺服器有 CSRF nonce。 |
| 11 視覺效果 | ok 正文主要功能已有 | SDL/Skia、Blend、rounded clipping、surface 快取。另有 blur、overflow scrolling、touch。 |
| 12 排程與執行緒 | warn 架構已有，API 有回退 | task queue、RAF、profiling、commit、threaded scrolling；另有 priority、network、raster threads 與 adaptive cadence。 |
| 14 無障礙 | no 未見章節核心實作 | 有基本 input focus；缺 zoom、dark mode、Tab 導覽、accessibility tree、screen reader。 |
| 15 嵌入內容 | no 未見章節核心實作 | DrawImage 用於 emoji；未見一般 img 載入／layout、iframe、Frame、postMessage。 |
| 16 增量計算 | no 未見章節核心實作 | 使用單一 needs_render；無 protected fields、依賴追蹤或 contenteditable。 |

主要程式位置：

- DOM／CSS：`browser.py:9072`、`9347`、`9697`。
- JS／表單：`browser.py:4437`、`6617`。
- 第 11 章：`browser.py:2052`、`2101`、`2147`、`2175`。
- 第 12 章：`browser.py:900`、`1160`、`6219`、`7278`、`7802`。
- 第 13 章 GPU 原型：`browser.py:1881`、`7238`、`7435`、`8173`。

## D 建議接續順序

1. 補回 JS timers 與 async XHR 橋接。
2. ~~在桌面環境驗證 GPU Acceleration。~~ 已完成描述性實測；正式 freeze／校準仍待做。
3. 閱讀 Compositing 的圖層快取原理。
4. 實作 Compositing Leaves 的 layer 與 draw list。
5. 接續 opacity CSS Transitions。
6. 接續 Composited Animations 與圖層最佳化。

目前預設為 CPU＋threaded raster。
GPU 原型要求以下設定。在這台 WSL2 上一定要加 `GALLIUM_DRIVER=d3d12`，
否則 Mesa 會選到 llvmpipe（CPU 模擬的 GL），不是硬體加速：

```bash
GALLIUM_DRIVER=d3d12 BROWSER_RENDER_BACKEND=gpu BROWSER_RASTER_MODE=sync python3 browser.py <url>
```

加 `BROWSER_GPU_STRICT=1` 可在拿到軟體 renderer 時直接報錯。

GPU context 與 raster 目前留在 Browser Thread。

## E 執行驗證與判斷限制 {span=2}

| 檢查 | 結果 |
| --- | --- |
| Python AST 語法解析 | ok browser.py、web_server.py、server.py 通過 |
| RAF 排程 | ok 第 1 幀 a,b；回呼新增的 c 留到第 2 幀 |
| RAF opacity 修改 | ok display list 中可見 opacity=0.5 的 Blend |
| 無變更的第 2 幀 | ok commit 的 display_list=None，可重用前幀 |
| transition:opacity 1s | no 單幀直接變成 0；無節點動畫狀態 |
| CPU headless smoke | ok SDL dummy＋threaded raster 成功提交與呈現一幀 |
| GPU 硬體 context | ok 2026-10-05：AMD driver 更新為 31.0.21925.1001 後，renderer 為 D3D12 (AMD Radeon) |
| GPU 吞吐（描述性） | ok 4 場景 × 10 blocks，相對 CPU sync 幾何平均 1.33×（95% CI 1.30–1.36） |
| 捲動／RAF 場景 | warn 1.01×，沒有變快；原因見結果文件 |

10/04 的 GPU offscreen 失敗，原因是 Mesa 預設選到 llvmpipe，以及舊 AMD driver
（2021-08）的 shader compiler 不支援 Shader Model 6.7，見
[硬體診斷](chapter-13-gpu-hardware-diagnosis.md)。更新 driver 後硬體路徑可完整執行，
速度資料見[驗證結果](chapter-13-gpu-verification-results.md)。
這些是描述性結果，正式 `performance_status` 仍為 PENDING（缺畫面校準與 freeze）。

目前的 GPU 只加速 raster 本身。每 frame 仍重新 restyle／layout／paint，並重畫整個 interest
region；捲動／RAF 場景約三分之一時間在與 backend 無關的 Python display-list 執行。
後面的 Compositing、Composited Animations 小節讓動畫只更新合成圖層、不重新 raster，
預期才會讓 GPU 的效益在動畫與捲動上顯現。

現有 `browser.trace` 記錄了 CPU 呈現與多執行緒。
它包含 Browser、Main、Networking、Raster-and-draw threads。
呈現事件為 78 次 CPU，沒有 GPU 呈現事件。

目前 repo 沒有自動測試套件。
未追蹤的 `test.md` 主要列手動測試指令。
本次 probe 放在 `/tmp`，不是持續整合測試。

既有 page/chrome surface 重用對應第 11 章。
第 13 章還需要 display-list 子樹的合成圖層。
修改 opacity 目前仍呼叫完整 restyle、layout、paint。
相關路徑：`setAttribute → set_needs_render → render → relayout`。

## F 查證範圍與來源

檢查基準：HEAD `7d536e0` 與目前工作目錄。
GPU 原型與驗證工具已於 2026-10-05 提交到 `gpu_accel` 分支。
本次更新只修訂 GPU 相關段落；B 節的第 12 章回退已由 `6dc441e` 補回，此處未重寫。

本報告採程式碼檢查及小型執行驗證。
沒有逐一驗證第 1–11 章的所有習題。
`server.py` 是未追蹤的另一份 browser 程式。
本報告以 README 指定的 `browser.py` 為執行入口。

官方來源：

- [第 11 章](https://browser.engineering/visual-effects.html)
- [第 12 章](https://browser.engineering/scheduling.html)
- [第 13 章](https://browser.engineering/animations.html)
- [第 14 章](https://browser.engineering/accessibility.html)
- [第 15 章](https://browser.engineering/embeds.html)
- [第 16 章](https://browser.engineering/invalidation.html)

另見[官方要求查證筆記](browser-book-reference-2026-10-04.md)。
