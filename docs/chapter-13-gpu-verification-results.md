# Chapter 13 實作與驗證結果

日期：2026-10-05。依據 [GPU 驗證研究](chapter-13-gpu-verification-research.md) 實作。
設定政策、瀏覽器整合、診斷、量測／分析工具及獨立 code review 已完成；完整 GPU 驗收尚未通過。
重現方式見 [操作文件](chapter-13-gpu-verification-usage.md)。

2026-10-05 修訂：使用者要求 CPU raster／draw 佔比不作門檻；`chapter13-v2` 已移除最低值，
31.20% 這類低佔比資料可進入正式比較，並作為 `cpu_work_attribution` 報告。
原有可見變動／重新 raster、畫面校準、原始資料、固定樣本及收益／退步門檻仍需核對。
此次規則修訂沒有產生新的正式速度資料，也沒有將既有結果提升為 PASS。

本次修訂驗證：完整 **133 項測試 PASS**，包含 Chapter 12 回歸；
分析測試涵蓋 0%、1%、31.2%、49% 等佔比仍可比較，及低佔比測得退步仍報 FAIL。
CLI 測試以完整可重算的 30% 原始資料通過 freeze／正式 runner 檢查；
資料竄改、缺漏畫面校準、漏工作等既有拒絕條件仍受測試保護。
兩位 subagent 完成唯讀程式／流程及文件審查，沒有 blocker；獨立重跑同樣為 133 tests PASS。
另以 synthetic raw `6 / (6 + 594) = 1%` 通過 freeze／formal validation，
同樣低佔比且吞吐比為 0.8 時仍報 FAIL；這些是流程驗證，不是新增的 GUI benchmark 結果。

| 獨立判定 | 本環境結果 | 依據 |
| --- | --- | --- |
| `gl_path_status` | PENDING | 實際 GL raster 可執行；缺少 L2 call trace、完整凍結畫面 gate、DPI transition 及可核對的最小化事件 |
| `hardware_status` | FAIL | Tai Gar 自己的 current context 回報已知 software renderer：llvmpipe |
| `performance_status` | PENDING | 尚缺凍結畫面校準與完整正式配對效能資料；CPU 繪圖佔比已不再阻擋量測 |

PASS 的單元測試、預期拒絕、部分生命週期操作與畫面相等，均沒有提升上述三項判定。

後續 [AMD／WSLg 診斷](chapter-13-gpu-hardware-diagnosis.md) 確認：只在程序指定
`GALLIUM_DRIVER=d3d12` 可選到 `D3D12 (AMD Radeon(TM) Graphics)`，基本 GL／Skia context
隔離探針成功。但完整 Tai Gar diagnostic 兩次都在第一幀前原生崩潰（exit 139）。
這組獨立診斷沒有速度樣本，也沒有提升首次預設 llvmpipe runs 的判定。

## 交付內容

- `gpu_evidence.py`：保留原始設定、明確拒絕非法配置、嚴格 renderer 政策、程序／native thread／context／frame 身分、時鐘錨點、獨立 gates 及原子匯出。
- `browser.py`：GL owner 與 current context 檢查、實際 attributes／drawable／renderer、navigation／scene 世代、因果輸入與 present 返回時間、正常及失效 context 的分別清理。正常 GPU frame 不 readback。
- `gpu_query.py`：optional、有界 query ring，先檢查 availability，再讀完整 64-bit 結果；失效 context 不呼叫 GL。
- `gpu_workload.py`：四個固定的真實 DOM／CSS／JS fixtures；snapshot／geometry／font 身分，交替狀態確實重新 raster 並改變畫面。
- `gpu_verification.py`：prepare、qualify、replay、正常互動、capture、query、嚴格負例、校準／比較／freeze、正式 runner、重算、OS worksheet 及可攜 raw bundle。
- `gpu_analysis.py`：完整矩陣與公平性核對、paired block bootstrap、吞吐及產品 p95 門檻、分區與 8×8 tiles 畫面比較、負例及凍結 hash。
- `tests/`：設定、生命週期、真實 JS bridge／CPU raster、query、統計／負例及 CLI 證據契約；另有 opt-in 的實際開窗生命週期腳本。

Replay 直接使用真實瀏覽器的 raster／composition 路徑；固定 fixture 的 chrome display list 為空。
完整正常互動另走實際頁面載入與 Browser pipeline，兩種量測範圍在 raw run 中分開記錄。

## 首次交付的實際驗證（v1 歷史）

環境：Linux WSL2，kernel `6.18.40.1-microsoft-standard-WSL2`；Python 3.12.3、
skia-python 144.0.post2、PySDL2 0.9.17／SDL 2.32.10、PyOpenGL 3.1.10、NumPy 2.4.1。
實際 context：vendor `Mesa`，renderer `llvmpipe (LLVM 20.1.2, 256 bits)`，
version `4.5 (Core Profile) Mesa 25.2.8-0ubuntu0.24.04.2`。

| 檢查 | 結果與範圍 |
| --- | --- |
| 完整 headless suite | 129 項 PASS，包含 Chapter 12 回歸；raw log 保存於證據包 |
| 最後一輪 smoke | 四場景 × CPU sync／GPU sync／CPU threaded，12 個獨立程序全部 `ok`；每 run 2 warmup／4 measured frames，全部是 diagnostic |
| `small` capture | CPU／GL 的兩個狀態均為 600×800×4，逐像素完全相等；各 run 讀回 3 次，包含兩個狀態及最後畫面 |
| GL query | 真實 context 取得 360 個 64-bit samples；首個暖機 sample 異常偏大，原始值保留、未推定原因，不能作正式速度資料 |
| hardware strict 負例 | 預期拒絕 software renderer，沒有建立 Skia GL 路徑或切 CPU；原始 hardware FAIL 與負例斷言分開 |
| 正常 GL／CPU threaded 互動 | 各取得 300 measured frames、100 個可歸因輸入效果；沒有 capture／query／readback，不作正式效能 PASS 判定 |
| 原 v1 正式 run gate | 當時未通過繪圖佔比資格／未校準的 manifest 被拒絕，exit 1，沒有建立正式輸出目錄；佔比門檻現已撤除 |

從保留證據包 `final-smoke/runs.json` 的原始 completion timings 直接計算，
GL sync 相對 CPU sync 的繪圖吞吐比為下表。兩者均完成 4 frames，因此比值為
`CPU elapsed_ns / GL elapsed_ns`；分母包含批次尾完成等待，排除 layout 與 present。

| 場景 | CPU sync 批次 ms | GL sync 批次 ms | GL／CPU 繪圖吞吐比 |
| --- | ---: | ---: | ---: |
| 文字＋矩形主場景 | 37.6568 | 23.8489 | 1.579× |
| 小頁面 | 7.8610 | 10.3416 | 0.760× |
| 透明度／裁切 | 55.9147 | 22.8420 | 2.448× |
| 捲動／RAF snapshots | 50.8255 | 40.0335 | 1.270× |

此表只有一個配對 block、2 warmup／4 measured frames，是 **smoke 描述性結果**，
沒有正式配對 CI，也不代表正常互動或整個頁面處理的速度。
實際 renderer 為 llvmpipe；這些數字不能提升硬體或正式效能驗收判定。

主場景 `text_rect` 的原始 renderer fraction 為 **31.20%**；當時低於 v1 的 50% 門檻，
但在目前 v2 中僅作歸因資訊，不是量測資格不足或產品失敗。
Raw 保存兩個真正 CPU raster、不同 pixel hashes 及 main style／layout／paint preparation timings；
工具從 raw 重算 fraction，拒絕只提供 summary。其他早期資格結果也保留。
這是兩個狀態、未暖機的初步歸因資料，沒有主場景正式速度結果，也沒有依速度結果追加樣本或調整負載。

實際生命週期腳本的結果如下；故障注入有獨立斷言，原始診斷仍保存預期停止。

| 操作 | 結果 |
| --- | --- |
| 雙窗口 context 切換、resize | PASS |
| 切 tab／navigation、提交繪圖後關窗 | PASS |
| 初始化失敗清理、context loss 停止及 abandon 清理 | PASS |
| DPI transition | PENDING：本輪沒有取得實際 DPI 變化 |
| 最小化／恢復 | PENDING：WSLg 未提供本測試可核對的最小化事件 |

## 獨立審查與修正

三位 subagent 分工審查 evidence／query／workload、analysis，以及 browser lifecycle／CLI；
並以另一套測試核對實際 CPU raster、JS bridge、64-bit query 及原始證據綁定。
修正後最後一輪 reviewer 報告為 129 tests PASS，沒有剩餘 blocker。

審查與實測修正了 snapshot hash 漏 geometry、無關 font cache 影響身分、
drawable 重建前未切 current context、重複 dirty mutation 漏 input ID、
初始化例外清理、失效 context 的 present、worker crash／exit 的錯誤分類，
以及 summary 冒充完整 frozen proof 等問題。
真實 PyOpenGL 的自動 uint64 converter 也曾失敗；改用明確 `ctypes.c_uint64` buffer 後重跑成功。
失敗原始資料一併保留，沒有刪除再計成 PASS。

整理 README 時曾改變已建立 manifest 的未提交 diff hash，該批 12 runs 被判為
`measurement_fault`，未進正式統計。最後交付 smoke 使用新的 manifest，12 runs 全部成功。
早期診斷與最後版本在資料目錄中分開保存；其不同 program identity 不被混成同一正式實驗。

## 原始證據與尚未完成的驗收

下載 [證據包](gpu-verification-evidence-2026-10-05.zip)。內含最後 smoke 的 source、完整未提交 diff、
manifest、raw runs／frames／logs／summary、資格資料、capture pixels、query／strict 負例、
正常互動及生命週期 raw evidence、完整測試 log、審查紀錄、早期失敗資料與 SHA-256 checksums。
`artifact-index.json` 將原始絕對路徑映射到包內路徑；原始 JSON 不被改寫。
此包為保留原規則與原判定的 v1 歷史資料，不被本次修訂覆寫。
解壓後以包內 v1 source 從最後 smoke 的 manifest／runs 逐項重算 summary；它仍為 PENDING／FAIL／PENDING。

完整結案還需要：對目前固定場景建立新版本 manifest 與同程式版本的有效工作原始證據、
四場景完整凍結的分區容差及五類負例、
L2 GL call evidence、實際 DPI／最小化驗證、可歸因到 Tai Gar 程序／context 的 L3 原始硬體證據，
以及符合既定順序／樣本數的正式配對實驗。
本輪沒有安裝選用 apitrace，也沒有修改 driver 或 WSLg 設定；OS worksheet 保持待填狀態。
目前環境的 llvmpipe 結果不能支持硬體加速或效能改善的主張。

## AMD 硬體 GL 與 CPU 吞吐比較（2026-10-05，描述性）

設定：Windows AMD driver `31.0.21925.1001`，GPU runs 只在程序設 `GALLIUM_DRIVER=d3d12` 並加 `--strict`；
40 個 GPU runs 的實際 renderer 都是 `D3D12 (AMD Radeon(TM) Graphics)`。另以未設
`GALLIUM_DRIVER` 的 manifest 跑 `gpu_sync`，renderer 為 llvmpipe，作為軟體 GL 對照。
以 `worker --kind throughput` 依 manifest 的 10 blocks 預定順序執行，每 run 60 warmup／300 frames，
四場景 × 4 配置共 160 個獨立程序，全部 `status: ok`。原始資料在 `.gpu-diagnosis/bench/`。

吞吐比 = 對照 ms/frame ÷ AMD GPU ms/frame（> 1 表示 GPU 較快）；計時是 `completion.elapsed_ns`，
含 GPU 批次尾 `glFinish`，不含 layout 與 present。CI 為 10 blocks 的 paired block bootstrap（10,000 次）。

| 場景 | CPU sync ms/frame | CPU threaded | AMD GPU | llvmpipe GL | GPU／CPU sync（95% CI） | GPU／CPU threaded | GPU／llvmpipe |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 文字＋矩形 | 7.83 | 8.10 | 6.37 | 6.84 | 1.21× [1.16, 1.25] | 1.26× [1.21, 1.31] | 1.08× [1.04, 1.11] |
| 小頁面 | 2.13 | 2.37 | 1.15 | 3.61 | 1.81× [1.75, 1.87] | 2.03× [1.93, 2.12] | 2.93× [2.57, 3.21] |
| 透明度／裁切 | 5.63 | 5.72 | 3.97 | 6.12 | 1.41× [1.34, 1.47] | 1.45× [1.38, 1.52] | 1.58× [1.49, 1.67] |
| 捲動／RAF | 11.66 | 11.74 | 11.68 | 10.24 | 1.01× [0.98, 1.03] | 1.02× [0.99, 1.05] | 0.88× [0.84, 0.93] |
| 四場景幾何平均 | | | | | 1.33× [1.30, 1.36] | 1.40× [1.37, 1.42] | 1.45× [1.39, 1.50] |

程序 CPU 時間（中位數 ms/frame，CPU sync → AMD GPU）：文字＋矩形 7.83 → 4.86、小頁面 2.13 → 1.01、
透明度／裁切 5.63 → 3.02、捲動／RAF 11.65 → 8.87；llvmpipe 則是 21–30 ms/frame。

此表的配對設計和統計方法沿用正式規則，但沒有凍結畫面校準與 freeze，所以是描述性結果，
不是正式 `performance_status` PASS；`hardware_status` 的正式判定也還要走 freeze 流程。
捲動／RAF 場景 GPU 沒有比 CPU 快，原因還沒分析。

### 捲動／RAF 沒有變快的原因（診斷，單次 runs）

用計時版 worker（scratch 腳本 patch 各階段計時，非正式 run）拆解每 frame 中位數：

| 階段（ms/frame） | CPU sync | AMD GPU（照 benchmark，不中途等待） | AMD GPU（每 4 frames `glFinish` 一次） |
| --- | ---: | ---: | ---: |
| tab raster（Python 執行 display list＋Skia） | 7.53 | 5.38 | 4.77 |
| compose（CPU：readback；GPU：flush／submit） | 3.63 | 5.03 | 1.53 |
| 整體 | 11.68 | 11.65 | 7.33 |

1. **與 backend 無關的 Python 成本大**：場景有 48 列，display list 每 frame 有 770 個 `DrawText`
   （每個字一個）、102 個 `Blend`、49 個 `DrawRect`，每次執行都重新 `parse_color`／建 Paint。
   只錄製到 `PictureRecorder` 就要 3.88 ms/frame，約佔 CPU 版本的三分之一，GPU 加速不到。
   文件高 1016 px，小於 interest region（4 × 600），所以每 frame 重新 raster 整張 800×1016 surface，
   約是可見區域的 1.7 倍。
2. **GPU flush 在佇列無上限時卡住**：benchmark replay 不 present、只在批次尾 `glFinish`，
   每 frame 的 `flushAndSubmit` 會卡 ~5 ms，但真正 GPU 執行（每 frame 等待）只有 ~2 ms。
   GPU 少做的像素工作（~6 ms）被這個 stall 吃掉。限制 in-flight frames 後：

| `glFinish` 頻率 | 每 1 | 每 2 | 每 4 | 每 8 | 從不 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 捲動／RAF ms/frame | 9.74 | 7.83 | 7.33 | 7.26 | 11.45 |
| 文字＋矩形 ms/frame | 5.31 | 4.83 | 4.80 | 4.22 | 5.85 |

   限制佇列後，捲動／RAF 約為 CPU sync 的 1.6 倍。stall 是在 Mesa d3d12／WSL 的同步路徑內還是 Skia 內，
   還沒有定位；dmesg 的 `create_sync_object -75` 是否相關也未證實。這些是單次診斷 runs，
   不是配對 benchmark，也不改正式判定。

註：文中 `.gpu-diagnosis/` 是本機的診斷暫存目錄（含下載的第三方原始碼與大型 GDB 輸出），沒有提交到 repo。
