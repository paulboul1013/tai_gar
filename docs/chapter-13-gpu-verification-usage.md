# Chapter 13 驗證工具操作

實作依據：[驗證研究](chapter-13-gpu-verification-research.md)。一般 CPU 仍是預設；GPU 指 GL backend，實際 renderer 另外分類。

## 啟動及證據

```bash
BROWSER_RENDER_BACKEND=gpu BROWSER_RASTER_MODE=sync BROWSER_GPU_EVIDENCE=/tmp/gpu-evidence.json python3 -B browser.py file:///absolute/path/page.html
```

`BROWSER_GPU_STRICT=1` 拒絕 software／unknown renderer；前者 hardware FAIL，後者 PENDING。非法 backend／raster mode、GPU threaded 及初始化失敗均報錯，沒有 CPU fallback。原始 requested 值、程序／native thread、context／window／frame／navigation、UTC／monotonic／perf_counter 錨點與依賴保存於 JSON。沒有 export 時資料有界，正常互動不累積 causal input history。

`BROWSER_GPU_SWAP_INTERVAL=-1|0|1` 記錄請求及實際值。`BROWSER_GPU_QUERY=1` 只用於獨立診斷；`BROWSER_GPU_RESOURCE_EVIDENCE=1` 啟用 surface 資源診斷。正常 GPU frame 不 readback；capture worker 明確標記讀回。

## 凍結實驗前

```bash
python3 -B gpu_verification.py prepare --output /tmp/manifest.json
python3 -B gpu_verification.py qualify --manifest /tmp/manifest.json --output /tmp/qualification.json
python3 -B gpu_verification.py run --manifest /tmp/manifest.json --output /tmp/smoke --smoke
```

固定四個真實 DOM／CSS 場景：`text_rect`、`small`、`blend_clip`、`scroll_raf`。外部 script 使用現有 JS bridge、RAF 與完整 style 字串；replay 使用真正 Tab style／layout／paint 產生的兩個交替 snapshots。Hash 涵蓋實際 drawing commands、geometry、font size、內容與序列；字型身分保存實際使用的 resolved typeface 及內嵌 font data hash。

Prepare 固定至少 10 blocks，每個包含 CPU sync、GPU sync、CPU threaded 的預定隨機順序；預設每 run 60 warmup／300 frames、100 required inputs、10,000 次 paired bootstrap、固定 seed 與 timeout。主場景透過 CPU pixel hash、真實重新 raster 及 raw main／renderer timings 核對有效工作；**raster／draw 佔比不設最低值，只供歸因，低佔比不會阻止 freeze 或正式比較**。比例須是可從原始資料重算的有效數值；錯誤或被竄改的資料仍被拒絕。不得因佔比低或正式速度未改善而調整場景、排除 run 或加樣本。

`qualify` 沿用既有 JSON 欄位名 `qualification`／`cpu_raster_fraction`，但不再判定佔比門檻。
目前取得兩個狀態的 main／renderer timings，未暖機的原始資料只作初步瓶頸歸因。
`analyze` 的 `analysis_version` 為 `chapter13-v2`；逐場景 `cpu_work_attribution` 保存該資訊，
速度比與收益門檻仍由正式配對 runs 計算。舊 v1 證據若需逐項重現，使用包內 v1 source。

Smoke 每場景只跑一個 block、2 warmup／4 measured frames，種類為 diagnostic。它保存 raw runs、logs、source、完整未提交 diff、checksums 與獨立 verdict，不能當正式速度資料。

## Capture、容差及負例

```bash
python3 -B gpu_verification.py worker --manifest /tmp/manifest.json --scenario small --configuration cpu_sync --kind capture --output /tmp/small-cpu.json
python3 -B gpu_verification.py worker --manifest /tmp/manifest.json --scenario small --configuration gpu_sync --kind capture --output /tmp/small-gl.json
```

每個 capture 匯出 `.phase-0.npy`、`.phase-1.npy` 及最後畫面的 `.npy`，JSON 綁定真實 frame／phase／路徑／SHA；這些 runs 完成分母含 readback，所以只能供畫面驗證。四場景各自取得兩個狀態；校準 fixtures 需包含幾何、混色／裁切、文字區域，區域必須完整覆蓋畫面。

```bash
python3 -B gpu_verification.py calibrate --reference /tmp/reference.npy --positive /tmp/benign.npy --negative missing_text=/tmp/missing.npy offset=/tmp/offset.npy alpha=/tmp/alpha.npy clip=/tmp/clip.npy stale=/tmp/stale.npy --regions /tmp/regions.json --output /tmp/calibration.json
python3 -B gpu_verification.py compare --reference /tmp/reference.npy --candidate /tmp/candidate.npy --calibration /tmp/calibration.json --output /tmp/comparison.json
```

Regions JSON 範例（座標為實際 pixels；各區域應依 fixture 配置）：

```json
[
  {"id":"text", "kind":"text", "box":[0,0,800,200], "pixel_tolerance":3},
  {"id":"geometry", "kind":"geometry", "box":[0,200,800,400], "pixel_tolerance":0},
  {"id":"blend", "kind":"blend_clip", "box":[0,400,800,600], "pixel_tolerance":1}
]
```

容差從事先取得的 benign fixtures 校準，五類負例均須被拒絕；比較器使用分區與固定 8×8 tiles，避免局部缺字被平均抵銷。範例 tolerance 不是已校準的驗收值。校準 hash 綁定參考 pixels、區域、正／負例與門檻，不得看正式畫面後放寬。

Capture index 是 JSON 陣列，每個場景／phase 各一個 entry：

```json
[
  {"scenario_id":"small", "phase":0,
   "reference":"/tmp/small-cpu.phase-0.npy", "reference_run":"/tmp/small-cpu.json",
   "candidate":"/tmp/small-gl.phase-0.npy", "candidate_run":"/tmp/small-gl.json",
   "calibration":"/tmp/small-phase-0-calibration.json"}
]
```

```bash
python3 -B gpu_verification.py freeze --manifest /tmp/manifest.json --capture-index /tmp/captures.json --output /tmp/frozen.json
python3 -B gpu_verification.py run --manifest /tmp/frozen.json --output /tmp/formal-experiment
python3 -B gpu_verification.py analyze --manifest /tmp/formal-experiment/manifest.json --runs /tmp/formal-experiment/runs.json --output /tmp/recomputed.json
```

Freeze 驗證全部場景／phase、CPU／GPU 配置、不同 run、真實像素 SHA、校準及原始資格資料，並凍結 actual GL vendor／renderer／version 基線。正式 GPU runs 再核對此基線及 program／platform／dependencies／字型／尺寸／sample／stencil 身分。外部證據若被覆寫便拒絕沿用 PASS；啟動時複製 originals、source、diff、artifacts index 與完整 hashes 到證據包。

## 量測及判定

完成吞吐包含真實 raster／composition、CPU 必要 pixel preparation、GPU flush／submit 與同 current context 的批次尾 `glFinish`；暖機先排空。DOM／layout、scheduler、present 與等待在分母之外。正式 replay 關閉 trace／capture／query，原始 process CPU time 與 frame timings 另存。

互動 runs 另用完整正常 pipeline，從真實頁面 commit 後計 60 warmup，保留 adaptive cadence／poll。SDL timestamp 經時鐘映射、input ID、Tab mutation／scroll、commit、frame，串到第一個包含效果的 present 呼叫返回；不是 input-to-visible。漏 frame、漏輸入效果、crash、卡死及非零 worker exit 保存為產品 FAIL；已確認的量測故障保持 PENDING，不補測或刪樣本。

主要配對吞吐幾何平均 CI 下界須 >1.10；逐回歸場景下界 ≥0.95。產品 p95 相對 CPU threaded 的 paired excess CI 上界須 ≤0；容許增幅為 max(2 ms, 基線的 10%)。以 run block 重抽，不能把 frame 當獨立樣本。

## 獨立診斷及生命週期

```bash
python3 -B gpu_verification.py worker --manifest /tmp/manifest.json --scenario small --configuration gpu_sync --kind query --output /tmp/query.json
python3 -B gpu_verification.py worker --manifest /tmp/manifest.json --scenario small --configuration gpu_sync --kind diagnostic --strict --output /tmp/strict-negative.json
python3 -B gpu_verification.py observe-template --output /tmp/os-observation.json
python3 -B tests/live_gpu_checks.py --output /tmp/lifecycle.json
```

Query 探測版本／extension／entry points／counter bits；有界 ring 先檢查 availability，再用明確 64-bit buffer 取結果，submit 在 query 範圍內，lost context 不呼叫 GL。Query 不提升 hardware verdict。

OS worksheet 需補可歸因到同程序／context 的 adapter／engine／raw trace 與 idle／rendering／pause／resume 時間；全機或 WSL 聚合 counters 不足。L2 GL call evidence 還需獨立 tracer 檢查 draws／uploads／submit／swap，不能只有 Python 計數。可依研究文件使用選用 apitrace；本輪沒有安裝。

Live lifecycle script 實際開窗及故障注入；DPI transition、最小化操作若平台未提供可核對事件，保持 PENDING。預期 context-loss 負例另報 assertion，原始診斷保留停止／失效清理。正常 live context 釋放資源；失效 context 先 abandon，停止 GPU 程序，沒有自動恢復或切 CPU。
