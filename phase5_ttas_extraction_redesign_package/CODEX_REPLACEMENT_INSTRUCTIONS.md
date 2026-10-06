# Codex 實作指令：Phase 5 TTAS Evidence Catalog / Prompt Redesign

## 工作目標

只修目前 live C case 的根因：

> AI Turn Interpreter 對 TTAS evidence 的 recall 不夠，因為 runtime prompt 只看到 field name + type，缺少人類可讀的中文概念。

不要開始 Phase 6。

不要重寫 TTAS evaluator。

不要把舊 AILogic/triage.py 搬回 production。

---

## 先完整閱讀本資料夾

必讀：

```text
PHASE5_TTAS_EXTRACTION_REDESIGN.md
TURN_INTERPRETER_TTAS_PROMPT_SPEC.md
SOURCE_BASIS.md
evidence_catalog.json
TEST_MATRIX.md
```

然後再讀 repo 最新：

```text
backend/knowledge/ttas/source_manifest.json
backend/knowledge/ttas/ttas_evidence_schema.json
backend/knowledge/ttas/active_rules.json
backend/app/services/ttas_rule_loader.py
backend/app/services/ttas_evidence.py
backend/app/services/rag_triage_adapter.py
backend/tests/test_phase5_ttas_rules.py
backend/tests/test_phase5_ttas_evidence.py
```

並對照舊版：

```text
AILogic/triage.py
```

---

## 必做 1：新增 knowledge file

將本資料夾：

```text
evidence_catalog.json
```

安裝到：

```text
backend/knowledge/ttas/evidence_catalog.json
```

不要把內容散落硬寫進 Python。

---

## 必做 2：擴充 ttas_rule_loader.py

### 讀取

`load_ttas_rules()` 再讀：

```text
evidence_catalog.json
```

### TTASRuleSet

加入可以取得 prompt catalog 的資料，例如：

```python
prompt_evidence_catalog: dict[str, dict[str, Any]]
```

命名可以調整，但責任要一致。

### 動態挑 field

從：

```text
enabled_rules
```

遞迴收集 predicate 真正引用的 evidence fields。

只把這些 field 放進 runtime prompt catalog。

不要把只被 `reference_only` rule 使用的 field 塞進 prompt。

### 合併 schema + catalog

Prompt entry 至少輸出：

```text
field
type
label_zh
meaning_zh
do_not_infer_zh
evidence_kind
```

其中 `type` 從：

```text
ttas_evidence_schema.json
```

取。

不要在兩份 knowledge file 維護兩套 type。

### Loader validation

fail closed：

- enabled field 不在 schema → error
- enabled field 不在 evidence catalog → error
- catalog source_id 不在 source manifest → error
- catalog rule_id 完全不存在 → error
- catalog 結構 malformed → error

不要要求 `reference_only` field 一定有 catalog entry。

---

## 必做 3：修改 rag_triage_adapter.py 的 prompt

不要重寫整份 `_build_symptom_collection_prompt()`。

Phase 4 已通過的：

- semantic extraction contract
- pending-answer contract
- safety_screen answer_assertion
- source grounding
- repair retry

全部保留。

只替換目前：

```python
ttas_fields = {
    field: spec.get("type")
    for field, spec in load_ttas_rules().evidence_fields.items()
}
```

改成 enabled production prompt catalog。

並依：

```text
TURN_INTERPRETER_TTAS_PROMPT_SPEC.md
```

修改 TTAS 段落。

最重要的 contract：

> 凡 current_user_text 直接支持的 enabled TTAS evidence 必須輸出，即使相同資訊已出現在 semantic_extractions，也不可省略 ttas_evidence。

---

## 必做 4：加入正例＋反例

目前舊範例：

```json
"ttas_evidence": []
```

不要刪掉 Phase 4 的耳鳴／眩暈示範。

但必須再加：

### 正例

```text
剛剛清潔劑濺到我的右眼，現在眼睛灼痛。
```

示範同時輸出：

```text
semantic_extractions:
- 灼痛
- 右眼

ttas_evidence:
- chemical_eye_injury=true
```

### 反例

```text
我的右眼很痛。
```

不得輸出：

```text
chemical_eye_injury=true
```

這兩個例子是教「語意證據／不可推測」，不是建立 keyword rule。

---

## 必做 5：不要改 evaluator authority

仍然：

```text
AI
→ evidence only

Backend validator
→ shape / type / grounding / confidence

TTAS evaluator
→ level
```

AI 不得輸出：

```text
ttas_level
urgency_score
warning_required
red_flags_checked
stage
is_complete
```

---

## 禁止

禁止新增：

```python
if "清潔劑" in text:
...
```

禁止：

- 中文 keyword list
- phrase list
- regex NLU
- 第二個固定 TTAS AI call
- AILogic 的 90/50/20
- default-to-low
- AI 直接判級
- AI 控制 workflow

---

## Tests

### 更新 test_phase5_ttas_rules.py

至少驗：

1. loader 能讀 catalog。
2. enabled predicate fields 全都有 catalog。
3. prompt catalog 不包含 reference_only-only fields，例如：
   - respiratory_distress
   - hemodynamic_status
   - cardiac_chest_pain_suspected
   - high_risk_injury_mechanism
4. 刪掉一個 enabled catalog entry → loader fail closed。
5. catalog 塞不存在 source_id → loader fail closed。

### 更新 test_phase5_ttas_evidence.py

至少驗：

1. prompt 包含 `chemical_eye_injury` 的：
   - 中文 label
   - meaning
   - do-not-infer
2. prompt 包含：
   - evidence 即使已在 semantic_extractions 也不能省略
   - confidence 不是 TTAS 機率
   - 正例
   - 反例
3. prompt 不再把整份 schema 全塞入。
4. provider mock 回 `chemical_eye_injury=true` 時仍只用同一 Turn Interpreter call。
5. `右眼很痛` 的 mock 不輸出 chemical evidence，Backend 不自行補。

### Phase 4 regression

必跑：

```text
tests/test_phase4_pending_answer_classification.py
tests/test_phase4_department_convergence.py
tests/test_phase41_clarification.py
tests/test_phase2_conversation.py
```

---

## 本機測試

從 backend 執行：

```powershell
.\.venv\Scripts\python.exe -m pytest `
    tests/test_phase5_ttas_rules.py `
    tests/test_phase5_ttas_evidence.py `
    tests/test_phase5_ttas_evaluator.py `
    tests/test_phase4_pending_answer_classification.py `
    tests/test_phase4_department_convergence.py `
    tests/test_phase41_clarification.py `
    tests/test_phase2_conversation.py `
    -q --tb=short -p no:cacheprovider
```

再跑 full backend。

---

## 完成後不要開始 Phase 6

回報：

```text
CHANGED FILES

EVIDENCE CATALOG INSTALLATION

ENABLED FIELD SELECTION

LOADER VALIDATION

TURN INTERPRETER PROMPT CHANGE

POSITIVE / NEGATIVE PROMPT EXAMPLES

NO KEYWORD NLU CONFIRMATION

SINGLE AI CALL CONFIRMATION

PHASE 4 NON-REGRESSION

TARGETED TEST RESULTS

FULL BACKEND TEST RESULTS

STOP
```

先停下來讓人檢查。
