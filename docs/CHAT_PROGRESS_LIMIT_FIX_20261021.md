# 有效回答不再被第 8 輪截斷

## 行為

- `turn_count` 繼續記錄總文字回覆數。
- 新增 `consecutive_no_progress_turns`，預設為 0，舊 snapshot 可相容載入。
- 一輪有新接受的臨床事實、新完成的 pending intent，或新完成安全確認，會重設連續無進展計數。比較已驗證的結構化證據，不以 AI 自稱有進展判定，也不把來源字句的改寫當新事實。
- 沒有新增上述證據時才累計無進展次數。重複同一事實不會反覆重設計數；低信心、未採納 proposal 不算進展。
- 保留連續 8 次無進展保護，新增總計 24 次仍未完成的上限。已通過既有完成條件的最後一輪可正常進入等待確認。
- 安全答案的既有三次失敗限制及安全解釋的既有輪數限制維持；絕對總上限也套用至安全澄清。沒有安全結果不能因進展而完成或進推薦。
- 明確重新整理問診時，同時清除兩個計數。

## 範圍

修改 `backend/app/schemas.py`、`backend/app/services/conversation_service.py`、`backend/app/routes/chat.py`。不改科別收斂、醫師排序、SQL、問句內容、前端或環境設定。

技術失敗是否應與無進展分開計數、科別狀態保存與其他前次列出問題，未在這輪一併修改。

舊的 unresolved 案件不會因修改而自動重開；需啟動新版本後端並建立新問診。這輪沒有重啟現有服務，避免清除 in-memory 案件。

## 驗證

新增 `backend/tests/test_conversation_progress_limits.py`，涵蓋第 8、9、13 輪的中英文否定回答、新臨床證據、重複證據、低信心 proposal、8 次無進展、24 次總保護、邊界完成與確認、修訂重設、空請求、舊 snapshot，以及不能跳過安全確認。

既有 3 個舊上限測試更新為新需求：第 9 輪已完成安全確認的案件可繼續；停止測試改為明確建立 7 次連續無進展，再確認第 8 次仍會停止。未刪除原本的安全／科別保護斷言。

完整離線 Backend 測試結果：**773 passed、93 subtests passed、0 failures**，另有 7 個 FastAPI／Starlette 棄用警告。測試使用既有 fixture 阻擋真實 AI 與 SQL，不能視為真實模型或手機端到端驗收。

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp=.pytest_tmp_progress_full
```
