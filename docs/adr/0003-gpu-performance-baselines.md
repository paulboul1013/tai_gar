---
status: accepted
---

# GPU 效能驗收同時保留 backend 比較與產品基線

CPU sync 與 GPU sync 可隔離 backend 差異，但 GPU sync 相對目前 CPU threaded 的互動退步可能被這個比較掩蓋。2026-10-05 訪談決定：以 CPU sync／GPU sync 判定 backend 收益，另以 GPU sync／CPU threaded 檢查產品退步；主驗收走固定本地 Tai Gar 頁面的實際繪圖流程，文字／矩形密集頁為主場景，小頁面、透明度／裁切、捲動／RAF 為回歸場景，其他場景也須符合預先訂定的退步上限。這比只做 Skia 微基準或只報最快場景需要更多控制與量測，但讓交付承諾同時涵蓋繪圖收益和現有使用方式；定量門檻與資料契約記錄於[研究文件第 5.1 節](../chapter-13-gpu-verification-research.md#51-已確認的量測契約)。

2026-10-05 使用者修訂：取消 CPU raster／draw 至少佔有效工作時間 50% 的硬性資格。
繪圖佔比保留為可核對的工作歸因資訊，低佔比場景仍量測並公布 CPU／GPU 的改善或退步；
以相同有效工作、完成邊界與固定配對實驗保證比較有效，不因佔比低擋下或排除資料。
本次不改收益／退步數值門檻；v1 原始證據與原判定保留，新分析標為 `chapter13-v2`。
