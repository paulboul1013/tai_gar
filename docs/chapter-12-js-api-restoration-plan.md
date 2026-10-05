# 第 12 章 JavaScript API 回補計畫

日期：2026-10-05。基準：HEAD `7d536e0` 與目前工作目錄。

目標是恢復先前可用的 timers 與非同步 XHR，並加入能攔住同類回退的測試。五項任務已於 2026-10-05 完成；26 個測試通過，並完成三位 sub-agent 的程式碼與測試流程 review。詳見[實作結果與驗證紀錄](chapter-12-js-api-restoration-results.md)。

## 1. 消失的原因：runtime 遷移漏掉部分 API

造成回退的 commit 是 `7d536e0497730f7b81d36c4a5595388d625411a3`，標題為 `add requestAnimationFrame opacity animation support`，提交日期為 2026-09-04。

該 commit 做了三個相關變更：

1. 刪除 `browser.py` 中整段 `SCHEDULING_RUNTIME_JS`。
2. 刪除 `JSContext.__init__()` 中的 `self.evaljs(SCHEDULING_RUNTIME_JS)`。
3. 在 `runtime.js` 加入 `Node.style` 與 RAF，沒有搬入 timer 與 async XHR 定義。

原本的載入順序是：

```text
evaljs(runtime.js)
  └─ DOM、events、只支援同步的早期 XMLHttpRequest
evaljs(SCHEDULING_RUNTIME_JS)
  └─ timers、RAF、覆蓋早期 XMLHttpRequest 的同步／非同步版本
```

目前只剩第一段載入，加上搬進 `runtime.js` 的 RAF。因此：

- `setTimeout`、`setInterval`、`clearInterval` 與 callback dispatch helpers 沒有 JS 定義。
- 舊的同步版 `XMLHttpRequest` 重新成為唯一版本，`open(..., true)` 直接拋出錯誤。
- Python 的 exports、timer workers、Networking Thread 與 TaskRunner 回呼路徑仍存在。`export_function()` 提供的是 `call_python()` 的呼叫入口，不會自動建立同名 JS 包裝函式。

這是兩份 runtime 定義在整合時漏搬功能造成的回退。Git 能證明刪除與漏搬的內容；它不能證明作者的刪除動機或當時實際跑過哪些檢查。

### 歷史證據

| Commit | 與此次問題相關的變更 |
| --- | --- |
| `33f9121` | 引入 `SCHEDULING_RUNTIME_JS`，包含 `setTimeout`、RAF、async XHR。 |
| `2a26bb3` | 在該區塊加入 `setInterval`／`clearInterval`。 |
| `e5e8ed4` | 回退前的父 commit，仍載入完整橋接程式。 |
| `7d536e0` | 刪掉完整區塊與載入呼叫，只將 RAF 搬至 `runtime.js`。 |

可重查的命令：

```bash
git log --all --oneline -S 'SCHEDULING_RUNTIME_JS' -- browser.py
git log --all --oneline -S 'function setInterval' -- browser.py runtime.js
git show 7d536e0 -- browser.py runtime.js
git show 7d536e0^:browser.py
```

定位目前程式：`browser.py:4425`、`4437`、`4476`，以及 `runtime.js:286`、`292`、`303`、`324`。行號以此次檢查為準。

### 執行對照已確認因果

診斷使用每個版本自己的 `browser.py`、`runtime.js`、`browser.css` 建立真正的 `JSContext`，沒有啟動視窗。歷史快照寫入 `/tmp` 的暫存目錄。

| 執行版本 | 六個缺失的 timer／XHR helpers | RAF 及其 dispatcher | `XHR.open("GET", "/probe", true)` |
| --- | --- | --- | --- |
| 父 commit `e5e8ed4` | 全為 `function` | `function` | accepted |
| 回退 commit `7d536e0` | 全為 `undefined` | `function` | 拋出不支援錯誤 |
| 目前工作目錄 | 全為 `undefined` | `function` | 拋出不支援錯誤 |
| 目前版本＋記憶體注入舊橋接 | 全為 `function` | `function` | accepted |

六個缺失名稱為 `setTimeout`、`runSetTimeout`、`setInterval`、`clearInterval`、`runSetInterval`、`runXHROnload`。

診斷指令：`python3 -B /tmp/tai-gar-ch12-api-diagnosis.py`。加上 `--check-current` 時，會針對目前 API 缺失以 exit code 1 失敗，訊息為 `AssertionError: Chapter 12 JS APIs missing`。此 probe 是此次查證用的暫存工具，後續正式測試需放入 repo。

這個對照證明刪除橋接足以造成 API 缺失。它只驗證定義及 async `open`，完整 callback、執行緒及生命週期行為由下列實作任務驗收。

### 為何沒有被既有測試攔住

父 commit 與回退 commit 的追蹤檔案都沒有自動測試套件。現有未追蹤的 `test.md` 主要是手動指令。缺少「實際建立 JSContext，確認整套 API 並執行回呼」的 repo 測試，是本次要補上的驗證缺口。

## 2. 回補範圍與介面

依[第 12 章正文](https://browser.engineering/scheduling.html)，`setTimeout`、async XHR 與 RAF 是正文範圍；`setInterval`／`clearInterval` 是 Exercise 12-1。此次把先前已支援的習題 API 一併恢復。

| 介面 | 回補後應有的行為 |
| --- | --- |
| `setTimeout(callback, ms)` | 儲存 callback，回傳 handle；Python timer 到期後排入 TaskRunner。 |
| `runSetTimeout(handle)` | 未知 handle 直接返回；執行前刪除一次性 callback，避免重複執行及殘留。 |
| `setInterval(callback, ms)` | 儲存 callback，回傳 handle；每個 tick 經既有 Python worker 與 TaskRunner 執行。 |
| `clearInterval(handle)` | 刪除 JS callback 並停止 Python worker；已入隊 tick 變成無作用。 |
| `runSetInterval(handle)` | 查表執行尚未取消的 callback。 |
| `XMLHttpRequest.open(method, url, is_async)` | 保存 method、url、async 旗標；`true` 啟用非同步。省略或 `false` 維持先前教學版的同步行為。 |
| `XMLHttpRequest.send(body)` | 將 method、url、body、async 旗標、handle 共五個參數交給 Python。省略 body 時轉成 `null`。 |
| `runXHROnload(body, handle)` | 找到正確 XHR，先更新 `responseText`，再呼叫 `onload(new Event("load"))`；callback 的 `this` 為該 XHR。 |
| `requestAnimationFrame`／`runRAFHandlers` | 保留目前實作：同幀回呼合併，回呼內新增的 RAF 留到下一幀。 |

同步 `send()` 恢復舊橋接的行為：直接設定 `responseText`、回傳結果，不額外排程 `onload`。非同步 `send()` 提交後返回，不提前填入結果；completion 排入該 Tab 的 Main Thread 才更新物件與呼叫 `onload`。

## 3. 實作決策

1. **JS 定義集中於 `runtime.js`。** 使用舊區塊作為行為參考；逐項移入 timers，並直接替換目前同步版 XHR。RAF 保留一份定義，`JSContext` 維持只載入一份 runtime。
2. **沿用現有 Python 路徑。** `JSContext` 的 exports、TaskRunner、Networking Thread、discard guards 已存在。正式程式預期主要修改 `runtime.js`；只有整合測試指出具體缺口時才局部修改 Python。
3. **明確保存 handle 與 callback。** timeout／interval 共用單調遞增 timer handle，callback 刪除後不回收 ID。XHR 使用各 context 獨立的遞增 handle 與物件表，避免並行回應送錯物件；此次保留物件到 context 結束。
4. **所有 callback 都在 Tab Main Thread 執行。** timer 與 network completion 只排程 Task，不直接操作 JS interpreter。導航及關閉後沿用 `discarded` 檢查，阻擋舊 context 回呼。

本次預計涉及 `runtime.js`、新增 `tests/test_ch12_js_api.py`、README 的測試指令。GPU、frame cadence 與 task priority 的既有工作不需要因此次回補重寫。

`clearTimeout`、`cancelAnimationFrame`、`fetch`、XHR `readyState/status/onerror` 不納入本輪；它們需要另外定義行為及測試。

## 4. 按順序執行的任務

### Task 1：建立會攔住此次回退的測試

建立標準庫 `unittest` 測試入口，使用真實 `JSContext` 載入正式 runtime。Tab／browser 提供最小測試宿主，不建立 SDL 視窗。

**驗收：**

- [x] 檢查 public API 與三個缺失 dispatcher，並確認 async `open` 可接受。
- [x] 在目前版本得到明確的 API 缺失失敗；RAF 的既有檢查通過。
- [x] 測試不補入舊 JS 區塊，也不使用另一套測試 runtime。

**驗證：** 執行 `RuntimeContractTests`，保存目前預期失敗的名稱與訊息。

**依賴：** 無。**檔案：** `tests/test_ch12_js_api.py`。**規模：** 小。

### Task 2：恢復一次性 timeout

在 `runtime.js` 加入 timer handle、callback 表、`setTimeout` 與 `runSetTimeout`，接回既有 Python handler。

**驗收：**

- [x] `setTimeout` 回傳不重複 handle；包含 0 ms 的 callback 都在原 JS 呼叫結束後，經 TaskRunner 執行。
- [x] callback 只執行一次；完成後查表無結果，重送相同 handle 不重跑。
- [x] callback 可排程另一個 timeout；即使 callback 拋錯，已消耗的 callback 也不殘留。

**驗證：** 執行 `TimeoutTests`，同時檢查 Python timer 只入隊、callback 在 Tab Main Thread 執行。

**依賴：** Task 1。**檔案：** `runtime.js`、測試檔。**規模：** 小。

### Task 3：恢復 interval 與取消

加入 `setInterval`、`clearInterval`、`runSetInterval`，使用 Task 2 的 handle 來源，接回既有 interval worker。

**驗收：**

- [x] 多個 interval 可獨立執行，且與 timeout 的 handle 不碰撞。
- [x] 取消後不再執行 callback；取消前已入隊的 tick 也不執行。未知／重複取消的 handle 安全返回。
- [x] callback 可取消自己；取消或 discard 後，既有 Python worker 停止。

**驗證：** 執行 `IntervalTests`；用可控制的 tick 觸發驗證取消前後的次數，不依賴精確毫秒間隔。

**依賴：** Task 2。**檔案：** `runtime.js`、測試檔。**規模：** 小。

**Checkpoint：** timer 測試全數通過；async XHR 的缺失仍由 Task 1 的測試明確指出。

### Task 4：恢復完整的同步／非同步 XHR 橋接

直接修改目前 XHR constructor、`open`、`send`，加入物件表及 `runXHROnload`。串接現有 `NetworkTaskRunner` completion 與 `dispatch_xhr_onload`。

**驗收：**

- [x] 同步 XHR 與省略 body 的行為通過；非同步 `send` 在完成前返回，完成後先填 `responseText` 再觸發一次 `onload`。
- [x] 兩個 XHR 反序完成仍各自收到正確結果；事件 type、callback `this` 與 Tab Main Thread 身分正確。
- [x] 請求仍經既有 CSP／CORS 檢查；被拒絕的請求不觸發成功 `onload`。

**驗證：** 執行 `XHRTests` 與 `RuntimeContractTests`；以可控制的 network completion 驗證返回、入隊與 callback 的順序。

**依賴：** Task 1；實作排在 Task 3 後，避免同時修改同一 runtime。

**檔案：** `runtime.js`、測試檔。**規模：** 小。

### Task 5：鎖定 context 生命週期與 RAF 共存

完成導航、關閉及 RAF 共存的整合測試，並將正式測試指令寫入 README。

**驗收：**

- [x] 舊 context 的 timer／XHR completion 在導航或關閉後不執行 JS，也不改動新頁面；新 context 可以正常使用同一組 API。
- [x] timers、XHR、RAF 共存時，RAF 保持幀前執行與下一幀語意；`Node.style` 修改仍觸發 render。
- [x] 全套測試通過，runtime 只有一套 timers／XHR／RAF 定義；README 說明測試入口與使用範圍。

**驗證：** 執行全套 `test_ch12_js_api.py`，包含 `LifecycleTests` 與 `RAFCoexistenceTests`，再檢查 diff。

**依賴：** Tasks 2–4。**檔案：** 測試檔、`README.md`；必要的局部修正另記原因。**規模：** 小至中。

## 5. 驗證方式與完成條件

預定新增測試後的正式指令：

```bash
BROWSER_RENDER_BACKEND=cpu python3 -B -m unittest discover -s tests -p 'test_ch12_js_api.py' -v
git diff --check
```

測試使用真實 JS interpreter、正式 runtime 與 Python handlers。timer 到期與 network completion 以可控制的替身觸發；執行緒整合檢查使用 Events／有上限的等待，驗證順序和執行身分，不斷言精確延遲。測試清理需 discard context、停止 workers、join threads，避免污染下一個案例。

完成條件：

- [x] 第 12 章正文缺失與先前 interval API 全部恢復。
- [x] 真正的 callback 路徑、取消、導航、關閉與 RAF 共存通過測試。
- [x] 把 `setTimeout` 定義或 async XHR 支援暫時拿掉時，對應測試會失敗。
- [x] 完成程式 diff 檢查，記錄測試結果與此次根因，供後續 review。

## 6. 風險與處理

| 風險 | 處理方式 |
| --- | --- |
| 重複的 XHR／RAF 定義造成覆蓋順序問題 | 直接替換舊 XHR；保持 runtime 單一載入與單一定義。 |
| handle 刪除後重新使用，回應送錯物件 | 使用單調遞增 ID；以並行與反序完成案例驗證。 |
| interval 取消時已有 tick 入隊 | JS callback 表與 Python active state 都檢查；加入排隊後取消案例。 |
| 舊頁面回呼改到新頁面 | 驗證現有 discard guards 與每個 context 的獨立註冊表。 |
| 只測 `typeof`，沒有走到真正 callback | 分別測實際 Python dispatch、TaskRunner 與 network completion。 |
| 執行緒測試偶發失敗或殘留 worker | 使用同步事件、上限等待與明確 shutdown，避免任意 sleep。 |

## 7. 查證來源

- 本 repo 的 `git show 7d536e0`、父 commit 與 symbol history。
- 目前 `runtime.js` 和 `browser.py` 的 runtime 載入、exports、timer／XHR handlers。
- [第 12 章：Timers and setTimeout](https://browser.engineering/scheduling.html#timers-and-settimeout)。
- [第 12 章：Long-lived threads](https://browser.engineering/scheduling.html#long-lived-threads)。
- [第 12 章習題](https://browser.engineering/scheduling.html#exercises)。
- [先前章節查證筆記](browser-book-reference-2026-10-04.md)。
