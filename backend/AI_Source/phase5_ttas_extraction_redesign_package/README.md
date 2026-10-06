# Phase 5 TTAS Evidence Extraction Redesign Package

這個資料夾是交給 Codex 的設計／替換資料包。

## 為什麼需要

310 live 已確認：

```text
「剛剛清潔劑濺到我的右眼，現在眼睛灼痛。」
```

AI 抽到：

- 灼痛
- 右眼

但漏掉：

- `chemical_eye_injury=true`

因此問題不是 TTAS evaluator，而是目前 Turn Interpreter 只看到 field name + type，缺少人類可讀的 evidence 概念。

## 解法

新增：

```text
evidence_catalog.json
```

把舊版 `AILogic/triage.py` 的「中文概念清楚」優點帶進新版，但：

- 不搬舊 TTAS 判級
- 不搬 90/50/20
- 不搬 default low
- 不把 workflow 交給 AI

新版仍然：

```text
AI 抽 evidence
→ Backend 驗證
→ official rule 判級
```

## 檔案

- `PHASE5_TTAS_EXTRACTION_REDESIGN.md`
  - 完整架構與修改方式。
- `evidence_catalog.json`
  - 28 個目前 enabled rules 實際需要的 evidence 中文概念。
- `TURN_INTERPRETER_TTAS_PROMPT_SPEC.md`
  - Prompt 的具體替換內容與正反例。
- `SOURCE_BASIS.md`
  - 新版 TTAS knowledge、官方來源、舊 triage.py 的權威順序。
- `TEST_MATRIX.md`
  - 單元測試與 310 live 驗收。
- `CODEX_REPLACEMENT_INSTRUCTIONS.md`
  - 可直接交給 Codex 的實作指令。

## 使用方式

1. 把整個資料夾放到 `New_Android_Backend` repo root。
2. 叫 Codex 先完整閱讀本資料夾。
3. 以 `CODEX_REPLACEMENT_INSTRUCTIONS.md` 為實作主指令。
4. Codex 修改後不要直接進 Phase 6，先回報／檢查。
