---
status: accepted
---

# GPU backend 的一般執行允許明示的 software GL

GL backend 可由軟體實作執行，功能驗證也需要這條路徑。2026-10-05 訪談決定：一般 GPU 執行模式允許 software GL 與 unknown renderer，必須明示實際分類；嚴格硬體驗證模式拒絕 software GL，遇到 unknown 則停止並保存診斷，hardware 為 PENDING。相較於讓所有 `gpu` 啟動都要求實體 GPU，這保留無硬體環境的功能測試能力，代價是設定名稱只承諾 GL 路徑，報告與使用者用語必須持續區分 GL backend 和硬體加速。

允許 software GL 不等於改走 CPU backend：未指定 backend 才使用預設 CPU；非法設定、不支援的 GPU + threaded 組合或 GPU 初始化失敗均明確報錯，不默默 fallback，並保存原始 requested 值與原因。
