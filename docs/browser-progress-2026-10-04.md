---
title: tai_gar 瀏覽器實作進度
subtitle: 對照 Web Browser Engineering 官方線上版
template: sheet
theme: shadcn
cols: 3
date: 2026-10-07
commit: 6c30670
---

## A 目前做到哪裡 {span=2}

**已提交版本：第 13 章 Overlap and Transforms（`2d77708`、`6c30670`，2026-10-06）。**

GPU Acceleration 已在 AMD 硬體實測；第 12 章 API 回退已由 `6dc441e` 補回。

opacity 的 CSS Transitions（`8d7af27`）、Composited Animations（`e226880`）、Optimizing Compositing 的 layer 合併（`419c1eb`）與 Overlap and Transforms（`2d77708`、`6c30670`）已實作。第 13 章正文各節都已實作；之後可以做第 13 章習題（例如 13-3 transform transition）或第 14 章。

依[第 13 章正文順序](https://browser.engineering/animations.html)對照：

| 官方小節 | repo 狀態 | 判斷證據 |
| --- | --- | --- |
| [JavaScript Animations](https://browser.engineering/animations.html#javascript-animations) | ok 已有 | RAF 回呼修改 Node.style；opacity 進入 Blend。 |
| [GPU Acceleration](https://browser.engineering/animations.html#gpu-acceleration) | ok 已有，已實測硬體加速 | GL context、GPU surfaces 與 SwapWindow；實際 renderer 為 `D3D12 (AMD Radeon(TM) Graphics)`。 |
| [Compositing](https://browser.engineering/animations.html#compositing) | ok 已有 | Raster-and-draw 分成 composite → raster_layers → draw 三階段，各自有 trace。 |
| [Compositing Leaves](https://browser.engineering/animations.html#compositing-leaves) | ok 已有 | `CompositedLayer`、`DrawCompositedLayer`、`composite_display_list`；需合成的 effect 複製進 draw list。 |
| [CSS Transitions](https://browser.engineering/animations.html#css-transitions) | ok 已有 | `parse_transition`、`NumericAnimation`、`update_transitions`；Tab 拆成 needs_style／needs_layout／needs_paint，動畫幀跳過 style。 |
| [Composited Animations](https://browser.engineering/animations.html#composited-animations) | ok 已有 | Blend 帶節點鍵；`CommitData.composited_updates`；動畫中間幀只跑 paint，raster thread 以 `refresh_draw_list` 沿用 layer，只 draw。 |
| [Optimizing Compositing](https://browser.engineering/animations.html#optimizing-compositing) | ok 已有 | `needs_compositing` 只標在需要合成的 effect；`CompositedLayer.can_merge`／`add`：祖先鏈相同的 leaf 合併進同一個 layer。另外提早加入重疊檢查（書中於 Overlap and Transforms 才加），以文件座標的足跡判斷，不改變 draw 順序；往回最多找 32 個 layer。 |
| [Overlap and Transforms](https://browser.engineering/animations.html#overlap-and-transforms) | ok 已有 | `parse_transform` 支援 `translate(X[, Y])`；`Transform` 效果包住元素的 Blend，`rect` 為位移後範圍（與書不同）；`map_to_document`／`map_from_document` 讓重疊判斷與 interest region 算進位移；layer surface 另外裁到祖先 Scroll 的範圍；點擊、觸控與輸入框游標換算位移；`Tab.page_height()` 把位移溢出算進文件高度。細節見[計畫](chapter-13-overlap-and-transforms-plan.md)與[結果](chapter-13-overlap-and-transforms-results.md)。 |

正文只實作 opacity transition。
transform 只支援靜態 `translate`；transform transition 屬 Exercise 13-3。

相關設定：`BROWSER_COMPOSITING=0` 退回直接 raster 路徑做 A/B 比對；
`BROWSER_SHOW_LAYER_BORDERS=1` 以紅框標出每個 layer。
`BROWSER_COMPOSITED_ANIMATIONS=0` 保留合成，但每幀都重新 composite／raster，用來量測 Composited Animations 的效益。
`BROWSER_LAYER_MERGING=0` 關閉 layer 合併，每個 compositing leaf 各自一個 layer，用來量測 Optimizing Compositing 的效益。
如果 `BROWSER_COMPOSITING=0`，則這個開關沒有作用。它與 `BROWSER_COMPOSITED_ANIMATIONS` 互相獨立。

`142608b` 另外修正 LineLayout 與所屬 block 共用 node，
造成 opacity、blend、filter、clip 在每個 line box 重複套用的問題。

## B 第 12 章 API 已補回

`7d536e0` 曾刪除 `SCHEDULING_RUNTIME_JS`，`6dc441e` 已把橋接移回 `runtime.js`，
並在視窗關閉時捨棄 JSContext、阻止捨棄後再註冊 interval。

| JavaScript API | 目前狀態 |
| --- | --- |
| requestAnimationFrame | ok `runtime.js:379` |
| setTimeout | ok `runtime.js:290` |
| setInterval | ok `runtime.js:305` |
| clearInterval | ok `runtime.js:312` |
| runXHROnload | ok `runtime.js:364` |
| XHR.open(..., true) | ok 非同步 XHR 可用 |

`setTimeout`、async XHR 是[第 12 章正文](https://browser.engineering/scheduling.html)要求。
`setInterval`、`clearInterval` 屬 Exercise 12-1。

回歸測試：`tests/test_ch12_js_api.py`（26 項）。
細節見[恢復計畫](chapter-12-js-api-restoration-plan.md)與[結果](chapter-12-js-api-restoration-results.md)。

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
| 12 排程與執行緒 | ok 主要功能已有 | task queue、RAF、timers、async XHR、profiling、commit、threaded scrolling；另有 priority、network、raster threads 與 adaptive cadence。 |
| 13 動畫與合成 | ok 正文主要功能已有 | RAF、GPU、Compositing／Leaves、CSS Transitions、Composited Animations、Optimizing Compositing、Overlap and Transforms 已有（見 A）。習題未做。 |
| 14 無障礙 | no 未見章節核心實作 | 有基本 input focus；缺 zoom、dark mode、Tab 導覽、accessibility tree、screen reader。 |
| 15 嵌入內容 | no 未見章節核心實作 | DrawImage 用於 emoji；未見一般 img 載入／layout、iframe、Frame、postMessage。 |
| 16 增量計算 | no 未見章節核心實作 | 使用單一 needs_render；無 protected fields、依賴追蹤或 contenteditable。 |

主要程式位置（`6c30670`）：

- DOM／CSS：`browser.py:10098`（HTMLParser）、`10373`（CSSParser）、`10840`（style）。
- JS／表單：`browser.py:5002`（JSContext）、`7323`（submit_form）。
- 第 11 章：`browser.py:2165`（Blend）、`2229`（Scroll）、`2623`（paint_visual_effects）、`2709`（paint_tree）。
- 第 12 章：`browser.py:659`（MeasureTime）、`895`（TaskRunner）、`6217`（CommitData）、`8169`（RasterAndDrawRunner）、`8327`（BrowserWindow）。
- 第 13 章：`browser.py:2369`（CompositedLayer）、`2470`（DrawCompositedLayer）、`2500`（composite_display_list）、`2594`（refresh_draw_list）、`7920`（_composite_and_raster_layers）、`7938`（_refresh_draw_list）、`8490`（GPU context）、`10736`（parse_transition）、`10756`（NumericAnimation）。
- 第 13 章 Overlap and Transforms：`browser.py:1863`（parse_transform）、`2258`（Transform）、`2306`（map_to_document）、`2330`（map_from_document）、`2755`（hit_test_paint_commands）、`2808`（transformed_overflow_bottom）、`2910`（collect_touch_candidates）、`7029`（Tab.page_height）、`7344`（input_cursor_index_from_x）。

## D 建議接續順序

1. ~~補回 JS timers 與 async XHR 橋接。~~ 已由 `6dc441e` 完成。
2. ~~在桌面環境驗證 GPU Acceleration。~~ 已完成描述性實測；正式 freeze／校準仍待做。
3. ~~閱讀 Compositing 的圖層快取原理。~~
4. ~~實作 Compositing Leaves 的 layer 與 draw list。~~ 已由 `142608b` 完成。
5. ~~實作 opacity CSS Transitions（transition 解析與逐幀插值）。~~ 已完成。
6. ~~實作 Composited Animations：opacity 變更只更新 draw list，跳過 layout 與 layer 重新 raster。~~ 已完成。
7. ~~Optimizing Compositing 的 layer 合併。~~ 已由 `419c1eb` 完成。
8. ~~Overlap and Transforms。~~ 已由 `2d77708`、`6c30670` 完成。

目前預設為 CPU＋threaded raster，合成預設開啟。
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
| 自動測試 `python3 -m pytest tests` | ok 2026-10-07：278 項全數通過 |
| 第 12 章 timers／async XHR | ok `test_ch12_js_api.py` 26 項 |
| 合成 layer 切分 | ok 無效果頁面為單一 layer；opacity／blend 切出多個 layer |
| 合成後畫面一致性 | ok 重組 layer 的結果與直接 raster 相同 |
| committed display list | ok 合成時不被修改；兄弟 leaf 共用同一個祖先 clone |
| interest region | ok 範圍外 leaf 被剔除；composited Scroll 會換算捲動座標 |
| line box 效果 | ok 元素效果只套用一次，不再逐 line box 重複 |
| trace 階段 | ok 記錄 composite、raster_layers、draw |
| transition:opacity | ok `test_ch13_transitions.py` 20 項；1s transition 中間幀只跑 paint，只有開始與結束跑 layout |
| composited animations | ok `test_ch13_composited_animations.py` 22 項；只 draw 路徑像素與完整路徑完全相同；CPU threaded／CPU sync／GPU sync 實測 33 幀中 30 幀走 `draw_only` |
| layer 合併 | ok `test_ch13_compositing.py` 的 `LayerMerging`：相同祖先才合併、重疊時不越過、合併後像素與直接 raster 相同；只 draw 路徑沿用合併後的 layer |
| transform: translate | ok `test_ch13_transforms.py` 75 項：合成與直接 raster 像素相同；書中藍綠方塊例子 3 個 layer；點擊、觸控、游標命中位移後的位置；opacity 動畫在 Transform 之下仍只 draw |
| verify 端到端 | ok `transform` 情境：動畫期間重疊區 34 幀都是綠色；點擊 layout 原位置不命中，點擊畫面位置命中；`BROWSER_COMPOSITING=0` 結果相同 |
| GPU 硬體 context | ok 2026-10-05：AMD driver 31.0.21925.1001，renderer 為 D3D12 (AMD Radeon) |
| GPU 吞吐（描述性） | ok 4 場景 × 10 blocks，相對 CPU sync 幾何平均 1.33×（95% CI 1.30–1.36） |
| 捲動／RAF 場景 | warn 1.01×，沒有變快；原因見結果文件 |

GPU 吞吐數據量測於合成功能加入之前（`cb9fa45`），尚未以合成路徑重測。

10/04 的 GPU offscreen 失敗，原因是 Mesa 預設選到 llvmpipe，以及舊 AMD driver
（2021-08）的 shader compiler 不支援 Shader Model 6.7，見
[硬體診斷](chapter-13-gpu-hardware-diagnosis.md)。更新 driver 後硬體路徑可完整執行，
速度資料見[驗證結果](chapter-13-gpu-verification-results.md)。
這些是描述性結果，正式 `performance_status` 仍為 PENDING（缺畫面校準與 freeze）。

CSS opacity transition 的中間幀現在只跑 paint 與 draw：layout、composite、raster 只在動畫開始與結束各跑一次。
在 120 段文字的頁面上，main thread 每幀約由 44 ms 降到 10 ms，raster thread 約由 28 ms 降到 12 ms（CPU）；
1 秒動畫的實際幀數 CPU 由 14 增為 22、GPU 由 7 增為 18。剩下的 CPU draw 成本來自每段文字各自一個 layer；
Optimizing Compositing 的 layer 合併把這類頁面合成約 3 個 layer（量測見 [驗證流程](chapter-13-optimizing-compositing-verification.md)）。
JS 修改 opacity 的路徑仍是 `setAttribute → set_needs_render → render`（style、layout、paint 全跑）。

現有未追蹤的 `browser.trace`（2026-10-05 22:02）是 GPU sync 執行紀錄，
有 408 次 `present_backend`（`render_backend=gpu`），
包含 Browser、Main、Network I/O worker 等 threads。
它早於 `142608b`（合成功能加入），因此沒有 composite／raster_layers 階段。

未追蹤的 `test.md` 列手動測試指令。

## F 查證範圍與來源

檢查基準：HEAD `6c30670`（`gpu_accel` 分支）與目前工作目錄，2026-10-07。

本報告採程式碼檢查及執行 `tests/` 自動測試。
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
