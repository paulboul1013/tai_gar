# 第 12 章 JavaScript API 回補結果

日期：2026-10-05。依據：[實作計畫](chapter-12-js-api-restoration-plan.md)。

五項任務已完成。根因是 `7d536e0` 刪除 `SCHEDULING_RUNTIME_JS` 時，
只將 RAF 搬入 `runtime.js`，漏掉 timers 與非同步 XHR 的 JavaScript 橋接。
本次將定義集中於正式 runtime，沿用真實 JSContext、Python handlers 與排程器。

## 實作

- `runtime.js`：恢復 timeout、interval／取消及 dispatchers。兩種 timer 共用遞增 handle；
  timeout 執行前移除 callback，interval 取消後的排隊 tick 不再執行。
- XHR：各 context 使用獨立物件表與遞增 handle；`send` 傳入五個 Python 參數，
  同步時更新並回傳結果，非同步 completion 排回 Tab Main Thread。
  `onload` 執行前更新 `responseText`，提供 load Event，`this` 為該 XHR。
- RAF 保留單一定義、同幀合併與巢狀 callback 留待下一幀的行為；
  `Node.style` 仍走正式 DOM mutation 與 render 通知。
- `browser.py`：關閉視窗先 discard 各 Tab；interval 註冊在鎖內檢查 discarded。
  這兩處是整合測試與 review 證明必要的局部修正，保留實作前已有的其他 Python 修改。
- 新增 `tests/test_ch12_js_api.py`，README 記錄正式執行指令與教學 API 範圍。

## 測試與回退驗證

```bash
BROWSER_RENDER_BACKEND=cpu python3 -B -m unittest discover -s tests -p 'test_ch12_js_api.py' -v
git diff --check
```

26 個測試全部通過：RuntimeContractTests 3、TimeoutTests 4、IntervalTests 6、
XHRTests 8、LifecycleTests 4、RAFCoexistenceTests 1。

使用者已在 Tai Gar 視窗完成人工測試，回報沒有問題。提交前另以 HEAD 的
`browser.py` 加入本次兩處生命週期修正，在獨立暫存目錄重跑 26 個測試全數通過，
確認本次 commit 不依賴工作目錄中尚未提交的 GPU／排程修改。

覆蓋一次性 callback、巢狀排程、拋錯後消耗、callback receiver、獨立 handle、
取消前已排隊 tick、自取消、同步與非同步 XHR、反序 completion、事件及 receiver、
CSP／CORS 拒絕與允許、來源頁面快照、導航與關閉後的排隊及延遲 completion、
新 context 使用 API、RAF 幀前執行、下一幀語意與 style render 通知。

整合案例使用真正 TaskRunner、NetworkTaskRunner 與 interval worker，
確認 I/O worker、Networking Thread 與 Tab Main Thread 的身分及順序。
用 Events 與有上限的等待控制執行，不依賴精確毫秒間隔。
測試清理會停止及 join workers；獨立 agent 在同一 process 跑完整套件後，
確認沒有新增存活執行緒。

初始 RuntimeContractTests 得到預期的六個 API 缺失、async open 拒絕；RAF 通過。
timeout、interval、XHR 都先驗證新增測試失敗，再回補正式 runtime。
獨立 agent 僅修改 process 中的 runtime 字串進行回退驗證，未改動 repo：

- 移除 `setTimeout`：API contract 失敗，實際 timeout callback 測試也失敗。
- 恢復 async XHR 拒絕：async open 與反序 completion 測試都失敗。

Python 語法檢查、diff 空白檢查及 runtime 九個 API／dispatcher 單一定義檢查通過。

## 多 agent review

三位 agent 分別檢查 runtime 程式碼、測試流程、生命週期及執行緒。
已處理的具體 findings：

1. interval callback 直接以物件屬性呼叫，讓 `this` 指向 callback 表。
   改為 local callback 呼叫；回歸測試先得到 `[true, false]`，修正後為 `[true, true]`。
2. 關閉視窗未 discard context，長週期 interval 無法立即停止。
   加入 `tab.discard()`；真實 close 測試先失敗，修正後通過。
3. close/discard 後，正在 Main Thread 執行的 JS 仍可註冊新的長週期 interval。
   用 Events 固定這個順序，先重現存活 worker，再加入鎖內 discarded guard。
   獨立 probe 重跑確認 interval 表為空，Main Thread 與 interval worker 均已停止。

## 驗證範圍

測試載入真實 interpreter 與正式 runtime，不注入另一套實作。
timer 到期與網路 I/O 使用可控制的替身；測試不建立 SDL 視窗、存取外部網路，
也不驗證 GPU 或實際 HTTP socket。這符合此次計畫的驗收範圍。

`clearTimeout`、`cancelAnimationFrame`、`fetch` 與額外 XHR state/error API
維持計畫中另行實作的範圍。既有 Python timeout 沒有主動取消長延遲 Timer；
discard guards 阻擋舊 callback，但 Timer 本身會保留到到期。
