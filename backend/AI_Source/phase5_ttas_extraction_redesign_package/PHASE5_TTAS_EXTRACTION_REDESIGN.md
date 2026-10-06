# Phase 5：TTAS AI 證據抽取重整設計

## 目的

這份設計只解決目前 Phase 5 的「AI 明明看到了患者原文，卻漏掉 TTAS evidence」問題。

目前 live 例子：

- 患者：「剛剛清潔劑濺到我的右眼，現在眼睛灼痛。」
- AI 成功抽出：
  - `symptom = 灼痛`
  - `body_part = 右眼`
- 但漏掉：
  - `chemical_eye_injury = true`
- 因此 Backend 只能得到 `insufficient_information`，無法命中 `E010509 → Level 2`。

本設計不把系統改回舊版 AILogic，也不讓 AI 直接判 TTAS。

核心仍維持：

> AI 做語意證據抽取，Rule 做裁判。

---

## 一、目前問題不是「關鍵字不夠」

不可以為每種中文說法寫：

```python
if "清潔劑" in text:
    chemical_eye_injury = True
```

也不應在 Prompt 中列出幾十個產品名、同義詞或口語變體。

正確做法是只定義「醫療概念」：

- `chemical_eye_injury`
- 中文概念：化學物質直接接觸／濺入眼睛

AI 本來就負責理解不同自然語言是否與這個概念等價。

所以我們要維護的是「28 個目前 enabled rules 真正使用的 evidence 概念」，不是幾百個關鍵字。

---

## 二、舊版 AILogic/triage.py 要保留什麼、不要保留什麼

### 舊版值得保留的優點

舊版 Prompt 會直接用中文列出人類可讀的醫療情境，例如：

- 化學物質濺入眼睛
- 突發性視覺改變
- 經期逾期且腹痛
- 咖啡色嘔吐物或黑便
- 抽搐後意識已恢復
- 廣泛性紅疹／水泡
- 槍傷
- 外傷後肢體變形疑似骨折／脫臼

這使 AI 很容易知道「該找什麼」。

### 舊版絕對不能搬回來的部分

舊版同時讓 AI 決定：

- TTAS level
- `urgency_score = 90 / 50 / 20`
- `warning_required`
- `is_complete`
- `next_question`
- workflow
- red flags

而且還有舊／簡化規則與「未命中就 low」問題。

這些都不能復活。

### 新版要做的是

只把「舊版 Prompt 的可讀性」搬過來：

```text
患者自然語言
→ AI 對照人類可讀 evidence catalog
→ grounded TTAS evidence
→ Backend validation
→ active_rules.json
→ deterministic TTAS evaluator
```

---

## 三、新增 evidence_catalog.json

新增：

```text
backend/knowledge/ttas/evidence_catalog.json
```

這個檔案只放：

- evidence field 的中文概念名稱
- 該 field 代表什麼
- 什麼不能自行推測
- evidence 類型
- 來源 `source_id`
- `official_locator`
- 對應 `rule_id`
- 舊版 prompt 可參考的中文措辭（如果有）

**不放 TTAS level。**

**不取代 `ttas_evidence_schema.json`。**

**不取代 `active_rules.json`。**

### 三個知識檔的責任

| 檔案 | 責任 |
|---|---|
| `ttas_evidence_schema.json` | 型別、range、allowed values |
| `evidence_catalog.json` | AI 看得懂的中文語意定義 |
| `active_rules.json` | deterministic TTAS 判級 |

---

## 四、Prompt 不應再把整份 schema 直接丟給 AI

現在 Prompt 大致只看到：

```text
chemical_eye_injury: true|false|unknown
seizure_status: ongoing|...
...
```

這就是目前容易漏抽的主要原因。

修改後應由 Backend 在啟動／cache 時：

1. 找出 `active_rules.json` 中 `implementation_status = enabled` 的 rules。
2. 收集 enabled predicates 實際使用到的 evidence fields。
3. 從 `ttas_evidence_schema.json` 取得 type。
4. 從 `evidence_catalog.json` 取得中文語意說明。
5. 只把這些 production fields 組成 `prompt_evidence_catalog`。

目前最新版 enabled rules 共 32 條，實際使用的 evidence field 共 28 個。

`reference_only` 規則使用的欄位不要再塞入 Prompt 干擾 AI。

---

## 五、Loader 必須做一致性驗證

建議擴充 `ttas_rule_loader.py`。

### TTASRuleSet 新增

例如：

```python
prompt_evidence_catalog: dict[str, dict[str, Any]]
```

### Loader 載入

再讀：

```text
evidence_catalog.json
```

### 驗證

必須驗：

1. catalog 是 object。
2. `fields` 是 object。
3. 每個 enabled rule predicate 引用的 field：
   - 必須存在 `ttas_evidence_schema.json`
   - 也必須存在 `evidence_catalog.json`
4. catalog entry 至少包含：
   - `label_zh`
   - `meaning_zh`
   - `do_not_infer_zh`
   - `evidence_kind`
   - `source_basis`
5. `source_basis.source_ids` 必須都存在於 `source_manifest.json`。
6. `source_basis.rule_ids` 必須至少能對應目前 rules。
7. 如果 enabled rule 新增 field 但 catalog 沒補：
   - `TTASRuleLoadError`
   - fail closed

### Prompt catalog

Loader 或 helper 提供：

```python
build_enabled_prompt_evidence_catalog()
```

輸出只包含 enabled fields，並把 schema type 合併進去。

---

## 六、Turn Interpreter Prompt 修改方式

不要重寫 Phase 4 已經穩定的 pending-answer contract。

保留目前 semantic extraction / pending answer 的規則。

只替換 TTAS 部分。

### 必須新增的核心觀念

#### 1. 必須「掃描」本輪原文

Prompt 要明確寫：

> 對 current_user_text 的每個明確臨床事實，逐一對照下方 TTAS evidence catalog。凡患者本輪原文直接支持的 evidence，必須輸出；即使同一事實已經出現在 semantic_extractions，也不可因此省略 ttas_evidence。

這是目前最缺的句子。

#### 2. Catalog 是概念，不是 keyword list

Prompt 要寫：

> `label_zh` / `meaning_zh` 描述的是語意概念，不是固定關鍵字。患者可以用不同自然說法；只要語意直接等價且有原文支持即可抽取。不得因只看到相似詞就硬套。

#### 3. confidence 定義

Prompt 要寫：

> confidence 只表示「患者原文有多明確支持這個結構化事實」，不是病情嚴重度、疾病機率，也不是 TTAS level 的機率。

#### 4. 禁止用低 confidence 猜

Prompt 要寫：

> 原文不支持時，不得用較低 confidence 猜一筆 evidence。缺資料就省略；使用者明確表示不知道時才可輸出 grounded unknown / ambiguous。

#### 5. 禁止臨床推導

Prompt 要維持：

- 不從「喘很嚴重」自行變 `respiratory_distress=severe`
- 不自行判 shock
- 不自行判 cardiac suspected
- 不自行判 high-risk mechanism
- 不由文字自行估 GCS
- 不由「很燒」自行估體溫
- 不由症狀猜暴露原因

---

## 七、Prompt 範例要改

目前完整範例最後是：

```json
"ttas_evidence": []
```

這會讓模型學到「TTAS evidence 可以一直空」。

保留原 Phase 4 耳鳴／眩暈範例可以，但要再加 **一個 TTAS 正例、一個反例**。

### 正例

患者：

> 剛剛清潔劑濺到我的右眼，現在眼睛灼痛。

應示範：

```json
{
  "semantic_extractions": [
    {
      "field": "symptom",
      "normalized_value": "灼痛",
      "semantic_status": "available",
      "assertion": "present",
      "confidence": 0.99,
      "source_text": "眼睛灼痛"
    },
    {
      "field": "body_part",
      "normalized_value": "右眼",
      "semantic_status": "available",
      "confidence": 0.99,
      "source_text": "右眼"
    }
  ],
  "ttas_evidence": [
    {
      "field": "chemical_eye_injury",
      "value": true,
      "semantic_status": "available",
      "confidence": 0.99,
      "source_text": "清潔劑濺到我的右眼"
    }
  ]
}
```

重點是同一份患者原文可以同時產生：

- 一般 semantic evidence
- TTAS evidence

不是二選一。

### 反例

患者：

> 我的右眼很痛。

可以抽：

- `symptom = 眼痛`
- `body_part = 右眼`

但：

```json
"ttas_evidence": []
```

因為沒有任何化學暴露原文。

這個反例用來教「不能從結果症狀倒推原因」。

---

## 八、不要增加固定第二個 AI call

維持：

```text
同一個 Turn Interpreter
├── semantic_extractions
├── pending_answer
└── ttas_evidence
```

不要：

```text
Turn Interpreter
+
第二次 TTAS extractor
```

主要延遲仍是一個 AI call。

目前 Phase 4 repair retry 保持原本條件，不要因 TTAS 每輪固定再呼叫一次。

---

## 九、不要用 Prompt 修補 deterministic rule 的 context 缺口

這個 catalog 只改善「AI 看懂 evidence」。

如果某個 official rule 本身缺 complaint/body-region context：

- 不可以靠 Prompt 暗示 AI 不要抽
- 不可以讓 AI 自行選 official code
- 應該由 `active_rules.json` 的 predicate/context 或 `reference_only` 解決

也就是：

> Prompt 解決語意理解；Rule 解決醫療判定。

---

## 十、建議改動檔案

Production 建議：

```text
backend/knowledge/ttas/evidence_catalog.json        # 新增
backend/app/services/ttas_rule_loader.py            # 載入／驗證／enabled field selection
backend/app/services/rag_triage_adapter.py           # Prompt 改讀 prompt_evidence_catalog
```

測試：

```text
backend/tests/test_phase5_ttas_rules.py
backend/tests/test_phase5_ttas_evidence.py
```

原則上不需要改：

```text
backend/app/services/ttas_evaluator.py
backend/app/services/ttas_evidence.py
backend/knowledge/ttas/active_rules.json
backend/knowledge/ttas/ttas_evidence_schema.json
```

除非 Codex 發現單純為 loader wiring 所需的最小調整。

---

## 十一、測試要求

### Catalog integrity

驗：

- enabled rules 實際使用 field 數量目前為 28。
- 28 個都能在 catalog 找到。
- reference_only-only fields 不會出現在 prompt catalog。
- source_id 不存在時 loader fail closed。
- enabled field 缺 catalog entry 時 loader fail closed。

### Prompt contract

驗 Prompt 包含：

- `label_zh`
- `meaning_zh`
- `do_not_infer_zh`
- confidence 定義
- 「semantic_extractions 已有也不能省略 ttas_evidence」
- TTAS positive example
- negative non-inference example
- 不得輸出 TTAS level

### AI mock regression

模擬 provider 回傳：

```json
{
  "ttas_evidence": [
    {
      "field": "chemical_eye_injury",
      "value": true,
      "semantic_status": "available",
      "confidence": 0.99,
      "source_text": "清潔劑濺到我的右眼"
    }
  ]
}
```

確保原 Backend validation / evaluator 照常。

### Live acceptance

至少重跑：

1. 「剛剛清潔劑濺到我的右眼，現在眼睛灼痛。」
   - AI 必須抽 `chemical_eye_injury=true`
   - `E010509`
   - Level 2
   - urgent terminal

2. 「我的右眼很痛。」
   - 不得抽 `chemical_eye_injury=true`

3. Phase 4 A1/A2/A3 全部不退步。

### 穩定性

同一句 chemical eye positive live case 建議連續建立 5 個新 case。

要求不是「每次 source_text 一模一樣」，而是：

- 5/5 都抽到 `chemical_eye_injury=true`
- 5/5 source_text 都可在原文找到
- 5/5 都命中 E010509 Level 2

若仍大量漏抽，再評估 provider/model 或輸出穩定度，而不是先加中文 keyword。

---

## 十二、這次不要做的事

不要：

- 把舊 `triage.py` 搬回 production。
- 恢復 90/50/20。
- 恢復「沒命中就 low」。
- 讓 AI 輸出 TTAS level。
- 新增 `if "清潔劑" in text`。
- 為每個同義詞手寫 regex。
- 新增第二個固定 TTAS AI call。
- 修改 Phase 3 Department KB。
- 修改 Phase 4 candidate convergence。
- 開始 Phase 6。

---

## 最終目標

修改後：

```text
患者自由文字
↓
Turn Interpreter
↓
enabled evidence catalog（中文概念）
↓
grounded ttas_evidence
↓
Backend shape / type / confidence / source_text 驗證
↓
active_rules.json
↓
deterministic TTAS evaluator
```

舊版的優點「AI 清楚知道要找什麼」保留下來。

新版的優點「AI 沒有判級權、Backend 可追蹤官方規則」也保留下來。
