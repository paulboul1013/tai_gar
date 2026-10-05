# 第 13 章 GPU Acceleration：背景、完整實作計畫與驗證方法

日期：2026-10-05。分支：`gpu_accel`。基準提交：`6dc441e`。

[視覺總覽](chapter-13-gpu-acceleration-plan.html)；[官方來源與驗證研究](chapter-13-gpu-verification-research.md)。

本次先完成計畫，不修改瀏覽器執行邏輯。分支保留既有、尚未提交的 GPU 試作與事件輪詢調整；它們不能視為已完成或已通過硬體驗證。

2026-10-05 的 `grill-with-docs` 四輪 27 項設計決策已逐項確認；本計畫與研究文件同步採用定案的驗收契約，整體共同理解仍待最後確認。詞彙見 [CONTEXT.md](../CONTEXT.md)，決策見 [ADR 0001](adr/0001-platform-scoped-gpu-acceptance.md)、[0002](adr/0002-gpu-backend-software-gl-policy.md)、[0003](adr/0003-gpu-performance-baselines.md)、[0004](adr/0004-gpu-context-loss-stop-policy.md)。任一目標平台完成三項驗收即可滿足平台要求，結論限定該平台；未完成的平台保留 PENDING。

**完成標準是三個獨立結果：GPU backend 接通、實體 GPU 執行繪圖、代表性負載的效能改善。** 開啟 `gpu` 開關、畫面正常或看到 OpenGL 初始化訊息，都不足以一次證明這三件事。

## 1. 背景知識：瀏覽器把哪些工作交給 GPU？

### 1.1 從網頁到螢幕

目前的瀏覽器大致經過以下流程：

```text
HTML / CSS / JavaScript
    ↓ CPU：DOM、樣式計算、layout
display list：文字、矩形、裁切、透明度等繪圖指令
    ↓ raster：把指令轉成像素
各區域的 surface
    ↓ draw / composition：把區域組合成視窗畫面
呈現到螢幕
```

`paint` 在這個程式中主要產生繪圖指令；`raster` 才真正把指令變成像素。GPU 加速重點落在 raster 與畫面組合。JavaScript、DOM 與排版仍由 CPU 執行。原書把 GPU Acceleration 與後續的 Compositing 分開介紹。[原書第 13 章](https://browser.engineering/animations.html#gpu-acceleration)

### 1.2 CPU 與 GPU 路徑

| 階段 | CPU 路徑 | 本次 GPU 路徑 |
|---|---|---|
| raster | Skia 在 CPU 記憶體中畫像素 | Skia 使用 OpenGL backend 畫入 GPU render target |
| tab / chrome 暫存 | CPU surface | GPU off-screen surface |
| 組合視窗 | CPU root surface | GPU root surface，連到視窗 framebuffer |
| 呈現 | 取出像素、建立 SDL surface、blit | 提交 OpenGL 工作、交換視窗 buffer |
| 資料移動 | 每幀可能複製完整像素資料 | 一般呈現流程不把完整畫面讀回 CPU |

GPU 適合大量像素與混色工作。省掉每幀讀回及複製像素，也是這次實作的效益來源。但文字準備、資源上傳和提交指令仍有 CPU 成本，小畫面未必比較快。這是本計畫需要實測，而不是預設結果的原因。

### 1.3 幾個必要名詞

| 名詞 | 在本 repo 中的用途 |
|---|---|
| OpenGL context | 保存 GL 狀態與物件，必須在正確的執行緒上設為 current |
| framebuffer / render target | 接收繪圖結果；視窗的 target 用於呈現，off-screen target 用於 tab / chrome |
| texture | GPU 上可供取樣與混色的影像資源 |
| Skia `GrDirectContext` | 管理 Skia 的 GPU 資源與指令提交 |
| flush / submit | 把待處理工作送往 backend；通常不代表 GPU 已經完成 |
| swap / vsync | 交換視窗 buffer；vsync 可能讓呈現等待螢幕更新週期 |
| software renderer | 用 CPU 實作 OpenGL，例如 llvmpipe；仍可建立 GL context |

Skia 需要呼叫端建立並維持正確的 GL context。[Skia surface 建立說明](https://skia.org/docs/user/api/skcanvas_creation/) `flushAndSubmit()` 預設不要求 CPU 等待 GPU 完成，計時必須另行處理。[skia-python `GrDirectContext`](https://kyamagu.github.io/skia-python/reference/skia.GrDirectContext.html)

## 2. 文章可用的第三方庫，以及各自提供的功能

沿用現有 Python、SDL2、Skia 與 OpenGL 組合，不另換繪圖框架。

| 元件 | 提供的功能 | 邊界 | 本機已安裝版本 |
|---|---|---|---|
| [PySDL2](https://pysdl2.readthedocs.io/en/latest/) | Python 呼叫 SDL2；視窗、事件、GL context 與 buffer swap | 自己不是 GPU 驅動 | 0.9.17 |
| [SDL2](https://wiki.libsdl.org/SDL2/SDL_GL_CreateContext) | 原生視窗與 GL context 管理 | context 成功不保證硬體執行 | 實際載入 2.32.10 |
| [pysdl2-dll](https://github.com/a-hurst/pysdl2-dll) | 提供可載入的 SDL2 原生動態庫 | 不提供顯示卡驅動 | 2.32.10 |
| [skia-python](https://skia-python.github.io/skia-python/tutorial/overview.html) / [Skia](https://skia.org/docs/user/api/skcanvas_overview/) | 2D 繪圖、字型、裁切、混色、CPU / GPU surface；本次用 Ganesh OpenGL backend | Python binding 有 GPU API，也仍需確認實際 GL backend 能工作 | 144.0.post2 |
| [PyOpenGL](https://pyopengl.sourceforge.net/documentation/index.html) | Python 呼叫 GL；查詢 renderer、版本、能力、計時與同步物件 | 不是 Skia 的替代品，也不建立 OS 視窗 | 3.1.10 |
| Mesa 或顯卡廠商的 GL 驅動 | 實際執行 OpenGL 指令，可能走硬體或軟體 | 有 Mesa 字樣不等於軟體渲染；要看實際 renderer | 依實際 context 探測 |
| [apitrace](https://apitrace.github.io/)（驗證工具） | 擷取 GL 呼叫、檢查繪圖與資源流向 | GL 呼叫存在不等於實體 GPU 執行 | 本次環境尚未安裝 |
| OS / 廠商 GPU profiler（驗證工具） | 對照裝置、程序與實際 GPU 工作 | 系統總 GPU 使用率可能來自其他程式 | 選定驗證機後確認 |

版本來自本次本機 package metadata 與 `SDL_GetVersion()`；它們是環境紀錄，不是最低相容版本。正式實作會記錄成功組合與安裝方法。PyOpenGL 保持 GPU 模式才載入，CPU 模式不增加這個必要依賴。

GL 的軟體實作也可能支援 timer query。計時功能與硬體身分必須分別驗證。[Mesa LLVMpipe](https://docs.mesa3d.org/drivers/llvmpipe.html)

## 3. 本 repo 現況與實作範圍

### 3.1 已有的未提交試作

`browser.py` 工作目錄目前包含：

- `BROWSER_RENDER_BACKEND=cpu|gpu`，預設為 CPU。
- GPU 限定 `BROWSER_RASTER_MODE=sync`，GL / Skia / raster / present 都在 Browser Thread。
- SDL OpenGL 視窗、`SDL_GL_CreateContext()`、Skia `MakeGL()`。
- GPU root surface、tab / chrome 的 GPU off-screen surface。
- GPU 提交與 `SDL_GL_SwapWindow()`；一般 GPU frame 不呼叫 `.tobytes()`。
- GL vendor / renderer / version 的文字輸出，以及 backend trace 欄位。

主要入口是 `make_gpu_root_surface`、`make_gpu_render_target`、`GpuRasterWindowState`、`BrowserWindow`。這些是可延續的起點，還需要資源生命週期、實際 framebuffer 條件、錯誤處理與證據鏈的驗證。

### 3.2 本次環境已知結果

本機是 WSL2。2026-10-05 的 `glxinfo -B` 探測顯示：

```text
direct rendering: Yes
Accelerated: no
OpenGL renderer string: llvmpipe (LLVM 20.1.2, 256 bits)
Mesa version: 25.2.8
```

這份輸出表示**該次 glxinfo 使用的 GLX renderer 是軟體渲染**。`direct rendering: Yes` 在這裡不能當成硬體加速證據。Tai Gar 自己的 SDL context 尚未執行本輪硬體驗收，因此結果仍是待確認。也不能由此推論 Windows 主機沒有實體顯卡。

實作初期先取得應用程式本身的 renderer。如果仍是 llvmpipe，可以驗證 OpenGL 接線與畫面正確性，硬體及效能驗收則需要解決驅動路徑，或改用具備硬體 GL 的驗證機。[探測與官方來源筆記](chapter-13-gpu-verification-research.md)

### 3.3 「完整」涵蓋的內容

本計畫完整涵蓋原書 **GPU Acceleration 小節**：GL context、GPU raster 與 draw、呈現、資源管理、CPU 相容、硬體證明與效能報告。

第一版明確支援以下組合：

| backend | raster mode | 預期行為 |
|---|---|---|
| CPU | sync | 支援；用於公平 A/B baseline |
| CPU | threaded | 支援；維持現有使用方式 |
| GPU | sync | 支援；GL context 由 Browser Thread 持有 |
| GPU | threaded | 明確拒絕並說明限制，不能默默換成 CPU |

後續獨立里程碑包含 GPU context 移往 raster thread、composited layers、只更新合成屬性的動畫，以及 CSS transitions。它們不混入本次 GPU 接通與驗證，以免效能變化同時來自多種機制。

## 4. 實作設計

### 4.1 指令與資源流向

```text
Tab Thread：style / layout / display list
       ↓ 既有 commit / raster snapshot
Browser Thread：設為該視窗的 current GL context
       ↓
Skia GPU surface：tab + chrome
       ↓ GPU 影像組合
Skia root surface：視窗 framebuffer
       ↓ flush + submit
SDL_GL_SwapWindow
```

每個視窗各自持有 context 與 GPU state。初版不共享跨視窗 texture；切換視窗前切換 current context。保留既有不可變 snapshot 與 generation 判斷，避免 navigation / close 後呈現過期 frame。

### 4.2 必須補齊的契約

1. **Context 與執行緒。** 記錄 owner thread / context ID；建立、繪圖、重建與釋放都核對 owner。初始化任何階段失敗都能清理已建立物件。
2. **真實 framebuffer。** 檢查 SDL GL attribute 設定的回傳值，查詢實際 negotiated attributes；使用 drawable 像素尺寸，核對 framebuffer、sample / stencil、格式、origin 與色彩空間。不能只因沒有要求某個 attribute 就假定值為零。
3. **CPU / GPU 分界。** CPU 保留 raster / pixel / blit 流程；GPU 一般 frame 禁止完整 CPU readback。明確診斷或截圖才允許讀回，並標記在 trace 中。
4. **繪圖正確性。** 對照文字、圖形、裁切、透明度、捲動、chrome 與 tab 組合；CPU unpremultiplied 與 GPU premultiplied alpha 的差異需要正確處理。
5. **生命週期。** resize / DPI 改變時重建需要的 surface；最小化的零尺寸暫停繪圖。正常關閉先釋放 GPU 資源再刪 GL context；第一版 context lost 保存診斷、使用失效 context 清理並停止 GPU 程序，不自動重建或切 CPU。自動恢復是後續里程碑。
6. **錯誤可觀察。** 未指定 backend 才使用預設 CPU；非法 backend / raster mode、GPU + threaded 或 GPU 初始化失敗明確報錯，保存原始設定與原因，不默默 fallback。一般模式允許明示的 software / unknown GL，嚴格模式拒絕；software 的 hardware 為 FAIL，unknown 為 PENDING。不能清掉 GL error 後直接當作成功。

SDL 的 drawable 尺寸可能不同於視窗邏輯尺寸。[SDL_GL_GetDrawableSize](https://wiki.libsdl.org/SDL2/SDL_GL_GetDrawableSize) buffer swap 的等待行為需要另記錄。[SDL_GL_SwapWindow](https://wiki.libsdl.org/SDL2/SDL_GL_SwapWindow)、[SDL_GL_SetSwapInterval](https://wiki.libsdl.org/SDL2/SDL_GL_SetSwapInterval) 正常 context 與失效 context 的 Skia 清理語意不同。[GrDirectContext 清理 API](https://kyamagu.github.io/skia-python/reference/skia.GrDirectContext.html)

## 5. 最重要：如何證明真的使用 GPU 加速？

把驗證分成五層。每層保留原始證據，缺資料就寫待確認，不能由下一個畫面看起來正常來補上。

| 層級 | 要回答的問題 | 必須取得的證據 | 單獨能否證明硬體加速？ |
|---|---|---|---|
| L0 配置 | 是否要求 GPU backend？ | 設定、啟動參數、實際採用的 backend | 不能 |
| L1 裝置 | 應用程式的 GL context 由誰執行？ | Tai Gar 內部 GL_VENDOR / GL_RENDERER / GL_VERSION、SDL driver、軟體分類 | 排除已知 software；硬體候選仍需後續交叉核對 |
| L2 路徑 | Tai Gar 的實際 frame 是否走 Skia GL？ | backend 類型、有效 GPU target、同 frame 的 draw / submit / swap、無 CPU pixel 呈現 | 能證明 GL 路徑，還不能排除 software GL |
| L3 裝置工作 | 這批 frame 是否由實體 GPU 執行？ | 已辨識的硬體驅動，與 frame / 程序可對照的裝置執行證據 | 是硬體驗收核心 |
| L4 效能 | 相同工作是否比 CPU 快？ | 公平 A/B、完成時間、重複結果與統計 | 證明量測負載中的改善，不能泛化到所有頁面 |

### 5.1 L1：在 Tai Gar 自己的 context 查詢

新增啟動診斷 JSON，至少記錄：

- commit、工作目錄 diff 身分、OS、Python、SDL2、skia-python、PyOpenGL 版本。
- requested / actual backend、raster mode、SDL video driver。
- 每個視窗的 context ID、owner thread、GL vendor / renderer / version。
- 邏輯尺寸、drawable 尺寸、GL attributes、swap interval 請求與實際設定結果。
- renderer 分類：`hardware_candidate`、`software`、`unknown`，附分類理由。

`llvmpipe`、`softpipe`、已確認的 WARP 等應列為 software。`Mesa`、`D3D12`、`direct rendering` 不能單獨當成分類依據。未知 renderer 不自動算硬體；硬體候選也不是最終通過。

`glxinfo -B` 用於環境預檢。它不取代 Tai Gar 的 context 證據，因為兩個程序可能選擇不同的 GL / EGL / 驅動路徑。

### 5.2 L2：驗證真實 frame 的 GPU 繪圖路徑

每個驗證 frame 以 `window_id + generation + frame_id` 串接以下資訊：

```text
收到 raster snapshot
  → current context 正確
  → Skia GL backend + 有效 root / off-screen target
  → draw 實際內容
  → flush / submit
  → swap window
```

驗證一般呈現不走 `MakeRaster → tobytes → SDL blit`。CPU 與 GPU 各跑一次負向對照；CPU 結果不能被標成 GPU。Mock 測試只驗證控制流程，GPU 驗收需要真正建立視窗、context、surface 並繪圖。

需要時擷取一段 apitrace，檢查 draw、texture / framebuffer 與 swap。回放 trace 是輔助工具；原程序的硬體身分仍需保留，不能用在另一台機器的 replay 身分代替。

### 5.3 L3：實體 GPU 的交叉證明

安排一個固定時長、持續更新的重繪場景，並保留開始、停止與 frame timestamp。

| 實驗 | 用途 |
|---|---|
| GPU 模式，持續繪圖 | 觀察對應裝置上的工作 |
| 同程序靜止、停止重繪 | 核對工作是否隨負載停止 |
| 相同頁面改 CPU 模式 | 區別 Skia raster 工作；視窗合成器仍可能用 GPU |
| 明確 software renderer 的 GL 模式（環境可提供時） | 確認診斷與報告不會把 software GL 當硬體 |

在原生平台，優先使用可關聯程序 / GL context 的 OS 或廠商 profiler。WSLg 可能經過 Linux 程序、Mesa、D3D12 與 Windows 主機；需記錄這條路徑與實體 adapter。[Microsoft WSLg 架構](https://github.com/microsoft/wslg)、[Mesa D3D12](https://docs.mesa3d.org/drivers/d3d12.html)

Windows Task Manager 的總 GPU 百分比或 WSL VM 整體使用率只能提供輔助。若只能看到整個 VM，且無法把 raster 工作與程序 / context 關聯，L3 保持待確認。GL timer query、有效 texture、`/dev/dxg` 存在與 frame rate 高，也都不能各自取代硬體證明。

若工具無法直接關聯程序，必須補上可辨識硬體 adapter 的 GL driver trace / GPU capture，並對照相同 frame 的時間。單靠關閉其他程式後看到總利用率上升仍不足以宣告 L3 通過。

### 5.4 L4：公平量測，分清提交與完成

新增兩組互補資料：

| 資料 | 量測內容 | 注意事項 |
|---|---|---|
| 一般執行 trace | CPU 準備、指令提交、swap wall time、幀間隔、應用程式呈現延遲 | 輸入事件到首個包含其效果的 frame 之 present call 返回；不宣稱 input-to-visible |
| 受控 renderer benchmark | 固定 snapshot 序列的完成吞吐量 | 包含 raster / composition、必要像素準備、GPU 提交及尾完成等待；DOM / layout、33 ms 節拍、present 呼叫及等待均在分母外 |
| 獨立 GL 診斷 | 支援時的 GPU timer，或另列每幀同步 latency | 不混入正式 renderer 吞吐與正常互動 run |

獨立 GL 診斷使用實際支援的 timer query，能力不足則記錄不可用；正式吞吐仍須有批次完成量測，不以 query 支援與否直接決定 performance status。[Khronos timer query 規格](https://registry.khronos.org/OpenGL/extensions/ARB/ARB_timer_query.txt) 計時範圍包含要測的實際提交，避免 Skia 延後 flush 使 query 沒量到工作；查詢結果用有限的非阻塞佇列延後收取。不能把同一筆尚未完成的 query 拿來重用。[Khronos query 結果與 availability](https://raw.githubusercontent.com/KhronosGroup/OpenGL-Refpages/main/gl4/glGetQueryObject.xml)

目前 `raster_and_draw.elapsed` 是 CPU wall time。GPU 提交後可能還在繪圖，報告要明確區分 `cpu_submit_ms` 與 `gpu_draw_ms`。兩者不能直接互換。每幀 `glFinish()` 會改變 pipeline；只可用於另列的診斷 / 同步 latency 實驗，不放入正常呈現熱路徑。

公平 A/B 固定以下條件：

1. 比較 **CPU sync 與 GPU sync**，先隔離執行緒差異。
2. 相同程式版本、頁面、字型、有效 drawable 像素數、繪圖次數與相同視覺輸出。
3. 固定 scheduler、事件輪詢、frame rearm、人工延遲等設定。不得同時比較輪詢優化。
4. 受控 renderer 吞吐由固定頁面產生相同 snapshot 序列，走實際 Browser raster / composition 路徑；不受動畫 33 ms 節拍限制。CPU 包含必要像素準備，GPU 包含提交及批次尾完成等待，暖機先排空。互動實驗保留正常完整 pipeline、節拍及呈現政策。
5. 明確記錄 vsync。present 呼叫及其等待均在 renderer 吞吐分母之外，不用正常 frame wall time 減 swap wall time 推算分母；互動另外記錄實際 cadence 與呈現限制，不拿總 swap 時間直接聲稱 raster 加速。
6. 分開記錄 cold start 與暖機後結果。先暖機至少 60 個 frame，再固定至少 300 個 frame；每場景至少 10 組獨立 runs，每組三種配置並預先隨機化順序；每個互動 run 至少 100 個可歸因輸入。正式實驗前固定數量、timeout、排除條件與分析，不持續加測直到 PASS。
7. 正常互動使用原本一般呈現流程；renderer 吞吐、GL query、每幀同步、capture、apitrace 與 profiler 分別執行，保存實際配置。不把診斷或讀回成本混入正式效能 run。
8. 記錄 median、p95、p99、各 run 分布與速度比。吞吐採配對 run 比值的幾何平均，以完整配對 block 重抽 10,000 次、保存事前固定的 seed，取雙側 95% bootstrap CI；產品延遲採配對差值，不把高度相關的 frame 當獨立樣本。

已確認場景包括小頁面、一個文字 + 矩形綜合主場景、透明度 / 裁切密集頁、捲動與 RAF 重繪，均使用固定本地 Tai Gar 頁面，Skia 微基準只供診斷。主場景須先證明可見內容改變與實際重新 raster；CPU raster / draw 佔有效工作時間的比例排除 idle 與 present 等待並保存原始資料及歸因方式，**只供瓶頸分析，不設最低值，也不阻止量測或排除低佔比場景**。2026-10-05 使用者取消原本的 50% 資格門檻；不能用 cache 重用代替真正重新 raster，也不得為提高繪圖佔比重選較有利場景。驗收前固定場景與參數，公布所有結果，包含無改善與退步。CPU sync / GPU sync 判定 backend 收益；GPU sync / CPU threaded 另判定產品退步，兩種比較分開報告。見 [ADR 0003](adr/0003-gpu-performance-baselines.md)。

**效能通過條件：** 主要文字 + 矩形綜合場景的 GPU sync / CPU sync 完成吞吐速度比，其 run-level 95% 信賴區間下界須 **> 1.10**；每個回歸場景的同一吞吐比，其 CI 下界須 **≥ 0.95**。每個互動場景的 GPU sync 相對 CPU threaded，其 p95 應用程式呈現延遲增加須 **≤ max(2 ms, 基線 p95 的 10%)**，配對 run 超額差值的 CI 上界須 ≤ 0；詳見研究文件的公式。三種比較的畫面與完成邊界皆須有效。

有效完整正式實驗未達門檻為 performance FAIL，表示「未證明所要求的改善」，不推論 GPU 永遠較慢；產品 crash、卡死、漏工作或畫面錯誤也為 FAIL。量測器故障、設定不一致或完成邊界不可核對才為 PENDING。重測需另訂完整實驗並保留舊資料，不加測直到碰巧 PASS；所有失敗或被排除的 runs 都保存並註明理由，不刪掉產品失敗樣本。若 L3 通過、L4 不通過，結論是「使用硬體 GPU，但尚未證明所要求的收益」。定量契約見[研究文件第 5.1 節](chapter-13-gpu-verification-research.md#51-已確認的量測契約)，平台結案與 software GL 政策見[已確認決策](chapter-13-gpu-verification-research.md#設計訪談已確認決策與完成狀態)。

## 6. 分階段實作任務

依序讓每個階段都能執行與驗證。每個 task 以約 1–4 個檔案為單位；新腳本與測試名稱是預計產物，目前尚未建立。

### Task 0：建立可重現的 baseline

- [ ] 記錄 `6dc441e`、既有未提交 diff 與依賴版本；辨識 GPU、輪詢與其餘變更。
- [ ] 固定 CPU sync / threaded 啟動組合與 Chapter 12 回歸測試結果。
- [ ] 定義診斷 JSON、frame ID 與後續驗證場景的契約。

驗證：執行現有 26 個 JS API 測試；baseline 報告清楚區分已提交程式與工作目錄。依賴：無。檔案：本計畫、`docs/chapter-13-gpu-acceleration-results.md`（未來建立）。

### Task 1：取得真實 context 診斷

- [ ] 使用應用程式建立的 SDL context 產出 L1 診斷與 software / unknown 分類。
- [ ] 缺少依賴、GL 初始化失敗都提供具體錯誤；CPU 啟動不需要 PyOpenGL。
- [ ] 一般模式明示 software / unknown；嚴格模式停止並保存診斷，software 為 hardware FAIL、unknown 為 PENDING。
- [ ] 非法 backend / raster mode、GPU + threaded 或初始化失敗不默默 fallback，保存原始設定與原因。

驗證：真實 GL 啟動探測、已知 software 對照，以及分類與失敗分支測試。依賴：Task 0。檔案：`browser.py`、`scripts/gpu_probe.py`、`tests/test_gpu_acceleration.py`。大小：M。

**Checkpoint A：先確認可以在哪一台機器完成 L3。** 目前 glxinfo 為 llvmpipe。功能實作可以繼續，硬體驗收不得填通過。

### Task 2：完成 context 與 framebuffer 的建立契約

- [ ] 完成每視窗 owner thread、current context、初始化失敗 rollback。
- [ ] 檢查實際 GL attributes、drawable 尺寸與 framebuffer 格式，建立正確 root target。
- [ ] 四種 backend / raster 組合符合支援矩陣。

驗證：初始化各階段失敗注入、實際 target 有效性、雙視窗切換與像素尺寸檢查。依賴：Task 1。檔案：`browser.py`、`tests/test_gpu_acceleration.py`。大小：M。

### Task 3：完成一條實際 GPU raster → present 路徑

- [ ] tab / chrome / root 使用 GPU surface，保留必要的 snapshot / cache 行為。
- [ ] 實際 frame 正確 draw、flush / submit、swap，generation 串接完整。
- [ ] 一般 GPU frame 沒有完整 CPU readback 或 SDL pixel blit。

驗證：真實 GPU / software GL smoke draw、L2 trace、CPU 回歸；截圖讀回另標記。依賴：Task 2。檔案：`browser.py`、`tests/test_gpu_acceleration.py`。大小：M。

**Checkpoint B：CPU 仍可用，GPU GL 路徑可畫正確的首幀。** 這個 checkpoint 不等於 L3 硬體通過。

### Task 4：補齊 resize 與資源清理

- [ ] resize / DPI / 最小化正確重建或暫停，恢復後繼續繪圖。
- [ ] 多視窗、tab navigation、close 不使用過期結果或別的 context。
- [ ] 正常關閉與 context lost 分別清理，初始化失敗不遺留 native window / context；context lost 保存診斷並停止程序，不自動恢復或切 CPU。

驗證：反覆開關兩視窗、resize / DPI、最小化 / 恢復、navigation、繪圖期間關窗與初始化 / context loss 故障注入，核對 GL 錯誤與資源成長。任何必測操作未完成就讓完整 `gl_path_status` 保持 PENDING，不以縮小清單結案。依賴：Task 3。檔案：`browser.py`、`tests/test_gpu_acceleration.py`。大小：M。

### Task 5：建立可重現的視覺與對照場景

- [ ] 固定文字、圖形、圓角裁切、alpha、捲動、RAF、chrome / tab 場景。
- [ ] CPU / GPU 視覺對照先用固定 fixtures 校準並凍結區域 tolerance，文字 anti-alias 差異另檢查；缺字、位移、錯誤 alpha、漏裁切、過期 frame 的負例須被抓到。
- [ ] 整合 CPU、software GL、真實 hardware GL 的結果標籤；無可用硬體測試標成 skipped / pending。

驗證：同場景 screenshot comparison 與人工檢查；像素相似只證明畫面，不代替硬體證據。依賴：Task 3；生命週期場景依賴 Task 4。檔案：`tests/test_gpu_acceleration.py`、`tests/gpu_scenes.py`。大小：M。

**Checkpoint C：完成正確性與生命週期回歸。** 既有 Chapter 12 測試維持通過；讀回驗證不混入效能 run。

### Task 6：收集 L3 硬體證據

- [ ] 保存 actual renderer 與實體 adapter / 驅動路徑。
- [ ] 擷取可與 frame / 程序關聯的 GPU 工作，以及開始 / 停止 / CPU 對照。
- [ ] 以獨立 reviewer 核對 hardware verdict，證據不足維持 pending。

驗證：審查原始 trace / profile、裝置資料與時間對照，明確排除 llvmpipe / WARP。依賴：Task 1、3、5；需可用硬體環境。檔案：`scripts/gpu_probe.py`、`docs/chapter-13-gpu-acceleration-results.md`、驗證 artifact。大小：M。

### Task 7：補上完成計時與公平 benchmark

- [ ] 分開 renderer 完成吞吐、正常互動與獨立 GL query 診斷；固定吞吐分母及批次尾完成等待。
- [ ] 使用合格固定負載，三配置至少 10 組隨機化 runs；凍結樣本量、timeout、排除條件與分析，保留所有原始數據。
- [ ] 產出 median / p95 / p99、配對幾何平均速度比與雙側 95% bootstrap 區間，按 > 1.10 / ≥ 0.95 及產品延遲上限逐場景判定。

驗證：檢查 renderer 分母、暖機排空與尾完成等待，沒有人工延遲或吞吐節拍上限；正常互動保留相同 scheduler 配置政策。從原始數據與固定 seed 重算門檻、核對因果 input / frame 身分，區分產品失敗與量測故障。依賴：Task 3、5；硬體效能結論依賴 Task 6。檔案：`browser.py`、`scripts/gpu_benchmark.py`、`tests/test_gpu_acceleration.py`。大小：M。

### Task 8：整合文件與最終 review

- [ ] README 說明依賴、支援矩陣、啟動 / 探測 / benchmark 命令與限制。
- [ ] CPU 回歸、真實 GL 正確性、L3 硬體證據、L4 效能各自有結果。
- [ ] 獨立 reviewer 能取得原始資料、從固定 manifest 重算門檻，核對 GL 生命週期、L3 歸因與各狀態；summary 不一致時不能結案。

驗證：從文件在驗證機重現結果；報告可定位原始 artifact；沒有把 skipped 當 pass。依賴：Task 4–7。檔案：`README.md`、結果報告、必要的依賴說明。大小：M。

**Checkpoint D：只有 backend、hardware、performance 三項都有可審查證據，才能宣告本次完整 GPU Acceleration 交付。** 若受環境限制，程式完成度與待完成驗收分開列出。

## 7. 測試分層與多 agent review

| 驗證類型 | 驗證內容 | 執行位置 |
|---|---|---|
| 無視窗 unit tests | 配置、分類、cleanup、frame 狀態與失敗流程 | 一般測試環境 |
| 現有 JS API 回歸 | timers / XHR / RAF / navigation / close | CPU 模式；既有 26 cases |
| 真實 GL integration | context、surface、draw / swap、尺寸與視覺 | 有顯示環境的 CPU/software GL 或 hardware GL |
| 真實 hardware validation | L1–L3 證據鏈與對照 | 已確認硬體的驗證機 |
| benchmark | L4、吞吐與 latency | 同一驗證機，固定設定 |

已存在的回歸命令：

```bash
BROWSER_RENDER_BACKEND=cpu python3 -B -m unittest discover -s tests -p 'test_ch12_js_api.py' -v
```

目前試作的 GPU 啟動命令如下；啟動成功仍不等於硬體驗收：

```bash
BROWSER_RENDER_BACKEND=gpu BROWSER_RASTER_MODE=sync python3 browser.py
```

未來新增 `scripts/gpu_probe.py` 與 `scripts/gpu_benchmark.py` 後，README 再寫入實際命令及參數，不能先把尚不存在的腳本當成可執行驗證。

多人 review 分工：主 agent 實作；reviewer A 檢查 context / surface / cleanup；reviewer B 檢查 CPU 相容與 integration 測試；reviewer C 檢查硬體證據與 benchmark 公平性。共享 `browser.py` 的修改依序完成；場景與已定義契約的測試可以平行工作。

## 8. 最後交付的證據包

每次正式驗證保存一個可識別的 run ID，至少包含：

```text
environment.json       程式版本、diff 身分、依賴、裝置與驅動
gpu-diagnostics.json   actual context、software 分類、surface / backend
frame-trace.json       frame / window / context / generation 對照
hardware-evidence/     GPU capture 或 profiler 的原始輸出與裝置識別
visual-check/          CPU / GPU 對照與容許差異
benchmark.csv          每個 run / frame 的原始數據與設定
summary.json           backend / hardware / performance 各自 verdict
```

這是預計 artifact 契約，並非本輪已產出的驗收資料。大型 trace / capture 不預設提交到 git；結果報告記錄可取得的位置、雜湊與重現方式。

最終文章必須讓讀者能回答：**哪一台機器、哪個實體 GPU、哪個 Tai Gar frame、經過哪條繪圖路徑、比 CPU 快多少，以及哪些場景沒有改善。** 當前狀態：分支與計畫已建立；應用程式的 L1–L4 驗收尚未完成。
