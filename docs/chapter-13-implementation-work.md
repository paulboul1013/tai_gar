# Chapter 13 GPU 驗證實作工作單

使用者已授權依 `chapter-13-gpu-verification-research.md` 實作及使用多 subagent。
開始時保留既有未提交 GPU POC；HEAD diff 同時包含既有修改與本輪實作，不能冒充只有本輪變更。
原工作單記錄的 `/tmp/tai-gar-browser-before-gpu-verification.py` 在交付封存時不存在，
因此證據包沒有聲稱包含實作前 working copy；包內保存最後程式與完整未提交 diff。

- [x] 設定與證據：非法設定報錯、嚴格 renderer 政策、原始 requested、獨立三項 verdict、程式與執行身分。驗證：設定／證據單元測試。
- [x] 真實瀏覽器：context owner、實際 attributes／drawable、逐幀及 navigation 世代、生命週期及失效清理、無正常 readback。驗證：契約測試及實際 CPU／software GL。
- [x] 診斷：有界 optional query ring；capture 與正式 runs 分離。驗證：query 支援／不支援／未就緒／64-bit／lost-context 測試及真實 llvmpipe query。
- [x] 驗收工具：固定本地 DOM 場景、snapshot replay、暖機排空及批次完成、三配置預定順序、原始資料與 bootstrap 分析、分區圖像比較。驗證：負例、原始證據綁定及 CLI 重算。
- [x] 獨立審查：三位 subagent code review、另一路驗證測試程序、修正後重跑。首次交付完整 129 項測試通過，包含 Chapter 12 回歸。

依賴：設定 API → 瀏覽器整合 → replay/capture；分析 API 可獨立實作；審查在整合後進行。
每個階段跑適當測試，最後重跑 Chapter 12 回歸。CLI／結果文件提供可重現命令。
缺少硬體歸因、容差校準或必測生命週期時保持 PENDING，已知 software 的硬體為 FAIL。
不修改驅動與 WSLg 設定，不從單元測試推論硬體或效能驗收成功。

工具實作與審查已完成；完整三項驗收尚未通過。
2026-10-05 使用者修訂：移除 CPU raster／draw 最低佔比，保留原始歸因資料；
低佔比場景可以 freeze／正式比較，無改善或退步也完整報告，不為提高佔比換主場景。
實測、缺少的 gate 及原始證據包見 [實作結果](chapter-13-gpu-verification-results.md)；
重現命令見 [操作文件](chapter-13-gpu-verification-usage.md)。
