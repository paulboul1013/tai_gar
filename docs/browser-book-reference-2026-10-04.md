# Browser Engineering 章節對照基準

查證日期：2026-10-04。以[官方線上版目錄](https://browser.engineering/)為準；下表列的是書中正文的功能，不是本 repo 的完成狀態。`web__run` 查閱正文，Firecrawl 讀取官方 HTML 核對小節 anchors。

## 第 13 章：Animating and Compositing

以下依[正文順序](https://browser.engineering/animations.html)排列。

| 順序 | 官方小節 | 判斷關鍵 |
| --- | --- | --- |
| 1 | [JavaScript Animations](https://browser.engineering/animations.html#javascript-animations) | RAF 驅動 `Node.style` 修改；觸發重繪。 |
| 2 | [GPU Acceleration](https://browser.engineering/animations.html#gpu-acceleration) | OpenGL context；Skia GPU surfaces；swap。 |
| 3 | [Compositing](https://browser.engineering/animations.html#compositing) | 區分快取圖層與 draw list。 |
| 4 | [Compositing Leaves](https://browser.engineering/animations.html#compositing-leaves) | `CompositedLayer`、`DrawCompositedLayer`；保留效果巢狀關係。 |
| 5 | [CSS Transitions](https://browser.engineering/animations.html#css-transitions) | 解析 `transition`；style diff；opacity 插值；節點動畫狀態。 |
| 6 | [Composited Animations](https://browser.engineering/animations.html#composited-animations) | `composited_updates`；分開 dirty flags；跳過 layout／raster。 |
| 7 | [Optimizing Compositing](https://browser.engineering/animations.html#optimizing-compositing) | 合併相容圖層；非合成子樹一起 raster。 |
| 8 | [Overlap and Transforms](https://browser.engineering/animations.html#overlap-and-transforms) | translate、變換後 hit testing、overlap 畫序與 clipped bounds。 |

正文的 transition 僅實作 opacity。transform transition 與完整 threaded animations 留在 [Exercise 13-3](https://browser.engineering/animations.html#exercises)。因此 GPU 可用、RAF 可動，都不能單獨證明合成圖層及後續最佳化已完成。

## 第 11 章：Adding Visual Effects

[官方章節](https://browser.engineering/visual-effects.html)。核心基準是 Skia／SDL、頁面與 chrome surfaces、快速捲動、opacity、`mix-blend-mode`、rounded clipping，以及省略不必要 surfaces。

判斷時區分 [Browser Compositing](https://browser.engineering/visual-effects.html#browser-compositing) 的頁面 surface 重用，與第 13 章針對 display-list 子樹建立的合成圖層；它們不是同一完成條件。可查看 [Blending and Stacking](https://browser.engineering/visual-effects.html#blending-and-stacking)、[Clipping and Masking](https://browser.engineering/visual-effects.html#clipping-and-masking)、[Optimizing Surface Use](https://browser.engineering/visual-effects.html#optimizing-surface-use)。

## 第 12 章：Scheduling Tasks and Threads

[官方章節](https://browser.engineering/scheduling.html)。完成正文須涵蓋 task queue、`setTimeout`、async XHR callback、frame cadence、dirty flags、RAF、rendering profiling、browser／main thread 分工、display-list commit，以及 browser thread 捲動。

重要定位：

- [Timers and `setTimeout`](https://browser.engineering/scheduling.html#timers-and-settimeout)：callback 經 task queue 執行，導航後舊 callback 失效。
- [Long-lived threads](https://browser.engineering/scheduling.html#long-lived-threads)：async XHR request 返回後，排程 `onload`。
- [Animating Frames](https://browser.engineering/scheduling.html#animating-frames)：RAF callbacks 在 render 前執行。
- [Two Threads](https://browser.engineering/scheduling.html#two-threads)、[Committing a Display List](https://browser.engineering/scheduling.html#committing-a-display-list)、[Threaded Scrolling](https://browser.engineering/scheduling.html#threaded-scrolling)。

`setInterval`／`clearInterval` 是 [Exercise 12-1](https://browser.engineering/scheduling.html#exercises)，不是正文的必要 API。比較本 repo 時，Python handlers 存在不足以證明 JS API 可用；須實際核對 runtime 中的 JS 定義及 callback 路徑。

## 第 14 章：Making Content Accessible

[官方章節](https://browser.engineering/accessibility.html)。核心基準是 [Zoom](https://browser.engineering/accessibility.html#zoom)、[Dark Mode](https://browser.engineering/accessibility.html#dark-mode) 與作者客製化、[Keyboard Navigation](https://browser.engineering/accessibility.html#keyboard-navigation)、[Indicating Focus](https://browser.engineering/accessibility.html#indicating-focus)、[The Accessibility Tree](https://browser.engineering/accessibility.html#the-accessibility-tree)、[Screen Readers](https://browser.engineering/accessibility.html#screen-readers)、[Accessible Alerts](https://browser.engineering/accessibility.html#accessible-alerts)、[Voice and Visual Interaction](https://browser.engineering/accessibility.html#voice-and-visual-interaction)。

判斷關鍵：Tab／Enter 焦點操作不等於完整 accessibility tree；深色配色不等於 `prefers-color-scheme` 支援；有語音函式也不等於 DOM roles／alerts 已串通。

## 第 15 章：Supporting Embedded Content

[官方章節](https://browser.engineering/embeds.html)。依序從 [Images](https://browser.engineering/embeds.html#images)、[Embedded layout](https://browser.engineering/embeds.html#embedded-layout)、[Modifying Image Sizes](https://browser.engineering/embeds.html#modifying-image-sizes)，進到 [Interactive Widgets](https://browser.engineering/embeds.html#interactive-widgets)、[Iframe Rendering](https://browser.engineering/embeds.html#iframe-rendering)、[Iframe Input Events](https://browser.engineering/embeds.html#iframe-input-events)、[Iframe Scripts](https://browser.engineering/embeds.html#iframe-scripts)、[Communicating Between Frames](https://browser.engineering/embeds.html#communicating-between-frames)，最後討論 [Isolation and Timing](https://browser.engineering/embeds.html#isolation-and-timing)。

判斷關鍵：圖片下載／decode／尺寸配置／paint；每 frame 的 DOM／layout／scroll／focus；iframe 座標、clipping、輸入與導航；依 origin 管理 JS context；同源 parent 存取與跨源阻擋；非同步 `postMessage`。圖片支援只能算本章前段。完整 site isolation 是書中的現代瀏覽器背景討論，不能當成本書 toy browser 正文的全部要求。

## 第 16 章：Reusing Previous Computations

[官方章節](https://browser.engineering/invalidation.html)。核心基準是 `contenteditable`、protected fields／dependencies、遞迴 invalidation、細分 style／layout 更新，以及跳過無變更更新與樹遍歷。只有粗粒度 `needs_render` 不等於本章的 dependency-based invalidation。

## 對 repo 進度的使用方式

本檔提供官方要求。最後進度應同時列出已提交 HEAD 與未提交工作樹、可執行路徑、測試證據和缺口；不得依 README 小節標題或孤立類別名稱認定完成。若 JS timers／async XHR 定義缺失，即使已存在 RAF 與雙執行緒架構，第 12 章仍有正文缺口。
