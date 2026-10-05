# AMD／WSLg 硬體選擇診斷

日期：2026-10-05。這是首次 llvmpipe 測試之後的獨立診斷，沒有正式效能結果。

先前沒有使用實體 GPU 的直接原因是：預設 Mesa OpenGL context 選到了
`llvmpipe (LLVM 20.1.2, 256 bits)`。預設 `glxinfo -B` 在沙箱外也回報
`Accelerated: no`，沒有發現 `LIBGL_ALWAYS_SOFTWARE` 等強制軟體渲染設定。
尚未確定 Mesa 自動選擇 llvmpipe 的更底層原因。

沙箱外存在 `/dev/dxg`、WSL 的 D3D12／DXCore libraries 與 AMD user-mode driver；
Windows 回報 `AMD Radeon(TM) Graphics`、driver `30.0.13014.8`、Status `OK`。
沙箱內沒有看見 `/dev/dxg`，因此不能以沙箱內裝置列表推定 Windows GPU 不可用。

只在診斷程序設定 `GALLIUM_DRIVER=d3d12` 後，`glxinfo -B` 回報
`D3D12 (AMD Radeon(TM) Graphics)`、`Accelerated: yes`。
這個對照證明硬體後端可被選到；沒有修改永久環境變數、driver 或 WSLg 設定。

隔離探針在實際 GLX context 上完成三幀 `glClear`／`glFinish`／swap；
Skia import、GLX interface validation、預設 `GrDirectContext.MakeGL()`、
明確 GLX interface 的 `MakeGL(interface)` 四個獨立程序都 exit 0。
這些探針沒有執行完整 Tai Gar raster，也不是硬體效能驗收。

完整 Tai Gar `small`／`gpu_sync`／strict diagnostic 在相同 D3D12 設定下，
兩次都已記錄自己的 AMD GL context，但隨後 SIGSEGV（shell exit 139），
兩次 raw run 的 `frames` 均為空，初始狀態保留為 `product_failure`。
原生崩潰沒有經過 Python 清理／例外處理，因此 JSON 沒有完整失敗原因。
不得將這些 runs 計為 PASS 或正式速度樣本。

GDB 曾觀察到 driver worker thread 的 stack 經過 `libd3d12core.so`、
`_Unwind_RaiseException`、`__cxa_throw` 與 AMD `amdxc64.so`。
這是現場觀察筆記，當次 GDB 輸出未保存為 raw 檔案；它不足以證明是哪個元件的 bug，
也不足以將 `MakeGL()` 或任何 driver 更新判為確定原因／修復。
完整流程與隔離探針的差異仍需定位。

下載 [診斷原始資料](gpu-hardware-diagnosis-2026-10-05.zip)。內含 manifest、
兩次失敗 run／evidence／trace、隔離探針及各程序輸出、driver 載入 strace 與 checksums。
shell exit 139 與 GDB stack 是上述現場筆記，不冒充 raw JSON 中已有的欄位。
原 [v1 證據包](gpu-verification-evidence-2026-10-05.zip) 保持不變。

目前結論：預設後端選擇已解釋先前的 CPU 軟體渲染；AMD 基本 GL 可運作，
完整 Tai Gar 硬體路徑仍有未解的原生崩潰，尚未完成硬體加速驗收。

## 根因定位（2026-10-05 續）

結論：第一幀前的 SIGSEGV 不是 Tai Gar 或 Skia 的 bug，是 Windows AMD driver
`30.0.13014.8`（DriverDate 2021-08-26）和目前 WSL D3D12 stack／Mesa 25.2.8
不相容。原始輸出都在 `.gpu-diagnosis/`。

逐步排除（各為獨立程序，`GALLIUM_DRIVER=d3d12`）：

| 探針 | 結果 |
| --- | --- |
| Skia 只 `clear` | exit 0 |
| Skia 矩形／文字 | SIGSEGV |
| 純 OpenGL 三角形，只 link 不 draw | exit 0 |
| 純 OpenGL 三角形，一次 draw（無 Skia、無 Tai Gar） | SIGSEGV |
| 同上，llvmpipe | exit 0，pixel 正確 |

所以只要有 shader 參與的 draw 就會崩潰，與 Tai Gar 的 DOM、排程、網路無關。

1. **觸發點**：Mesa 依 `CheckFeatureSupport(D3D12_FEATURE_SHADER_MODEL)` 的回報
   （driver 回報 ≥ 6.7）產生 Shader Model 6.7 的 DXIL。AMD compiler `amdxc64.so`
   拋出 `hlsl::Exception` 0x80aa000f：`Unknown shader model 'vs_6_7'`。這個 C++
   exception 一路 unwind 穿過 `libd3d12core.so` 的 C 介面邊界，因而 SIGSEGV。
   三角形探針（`triangle-exception-message.gdb.txt`）和完整 Tai Gar
   `small`/`gpu_sync` worker（`full-taigar-throw.gdb.txt`，Mesa `gdrv0` 執行緒）
   都抓到同一則訊息。
2. **對照**：用 `D3D12_DEBUG=singleton`，再用 GDB 只在診斷程序記憶體中把 shader model
   查詢限制為 6.0（`cap-and-throw.gdb`），exception 消失、不再 SIGSEGV；沒有改系統檔案。
   只設 singleton、不限制時仍會 SIGSEGV。
3. **第二個問題**：限制在 SM 6.0 時，`CreateRootSignature` 和
   `CreateGraphicsPipelineState` 都成功（HRESULT 0），但提交 draw 後 Mesa 印出
   `D3D12: Removing Device.`，`GetDeviceRemovedReason` = `0x887A0001`
   （`DXGI_ERROR_INVALID_CALL`），`glReadPixels` 回報 `GL_OUT_OF_MEMORY`
   （`triangle-pso-hr.gdb.txt`、`triangle-removed-reason.gdb.txt`）。
   因此只降低 shader model 不夠，在這個 driver 上硬體 draw 仍無法完成。

兩個失敗都發生在 AMD user-mode driver 之內或之後，而且這個 driver 比
Shader Model 6.7（2022）還舊；目前證據最支持的修正是在 Windows 端更新 AMD 顯示
driver，再重跑 `.gpu-diagnosis/triangle.py`（應印出 `completed` 和
`[0, 0, 255, 255]`），通過後才重跑完整 Tai Gar。尚未用新版 driver 驗證，
不能宣稱已修好。在那之前 Tai Gar 照常使用預設 llvmpipe，不設 `GALLIUM_DRIVER=d3d12`。

附註：`gpu_verification.py` 的 program identity 會把 `.gpu-diagnosis/*.py` 算進去，
在該目錄加入診斷腳本後，舊 manifest 會讓 worker 回報 `measurement_fault`；
因此完整流程的 GDB 對照另外 prepare 了 `.gpu-diagnosis/manifest-gdb.json`。

## 更新 AMD driver 後（2026-10-05 續）

Windows 端改為 `31.0.21925.1001`（DriverDate 2026-05-20）；Windows 10 19045 22H2、
WSL 3.0.1.0、kernel 6.18.40.1。GDB 確認程序實際載入新的
`u0201039.inf_amd64_.../amdxc64.so`，不是舊的 `u0375201`。

| 探針（`GALLIUM_DRIVER=d3d12`） | 結果 |
| --- | --- |
| `glxinfo -B` | `D3D12 (AMD Radeon(TM) Graphics)`，`Accelerated: yes` |
| Skia clear／rect／text | 皆 exit 0，無 device removal |
| Skia rect 讀回像素（3 次） | 矩形內為藍、外為白，皆正確 |
| 完整 Tai Gar `small`/`gpu_sync` strict diagnostic（3 次） | 皆 exit 0、`status: ok`、300 frames |

`vs_6_7` 的 compiler exception 不再出現，第一幀前的 SIGSEGV 已消失，
完整 Tai Gar 的硬體繪圖可以完成。原始輸出在 `.gpu-diagnosis/full-taigar-newdriver*.json`。

未解：純 OpenGL 三角形探針（`triangle.py`，無 vertex buffer、以 `gl_VertexID`
索引 const array）仍在 draw 後 `D3D12: Removing Device.`
（`CreatePipelineState` HRESULT 0、`GetDeviceRemovedReason` 0x887A0001）。
Tai Gar 和 Skia 都不走這個路徑，所以不影響上面的結果，但原因還沒查。
dmesg 的 `dxgkio_create_sync_object: Ioctl failed: -75` 在能成功的 no-draw
探針中也會出現，跟這個失敗無關。

這些都是 diagnostic run，還不是正式的效能驗收；正式量測要用新 driver 的 manifest 重新跑。

註：文中 `.gpu-diagnosis/` 是本機的診斷暫存目錄（含下載的第三方原始碼與大型 GDB 輸出），沒有提交到 repo。
