# Turn Interpreter：TTAS 抽取區塊建議稿

> 用途：給 Codex 改 `backend/app/services/rag_triage_adapter.py`。
>
> 不要整份覆蓋目前 Phase 4 prompt。保留現有 semantic extraction、pending-answer、safety pending contract；只替換目前 TTAS 欄位清單與規則 17～23 附近的區塊，並新增下面兩個例子。

## Prompt 內由程式動態插入

建議變數：

```python
prompt_evidence_catalog = build_enabled_prompt_evidence_catalog()
```

每筆至少呈現：

```json
{
  "field": "chemical_eye_injury",
  "type": "true|false|unknown",
  "label_zh": "化學物質濺入／直接接觸眼睛",
  "meaning_zh": "患者原文明確表示化學性物質直接濺入、噴入或接觸眼睛。",
  "do_not_infer_zh": "只有眼痛、眼紅、灼熱、流淚或視力不適，但沒有明確化學物質接觸時，不得推測 chemical_eye_injury=true。",
  "evidence_kind": "direct_event_fact"
}
```

不要把 `source_basis` 全部塞進 runtime prompt；來源留在 knowledge file／audit 即可。

---

## 建議替換的 TTAS Prompt 區塊

```text
17. 同一次 Turn Interpretation 必須完成 TTAS evidence 掃描。請逐一對照下方「目前 production 可執行的 TTAS evidence catalog」與 current_user_text。凡本輪患者原文直接支持的 evidence，都必須輸出；即使同一個事實已經同時出現在 semantic_extractions，也不可因此省略 ttas_evidence。

18. catalog 的 label_zh / meaning_zh 是「語意概念」，不是固定關鍵字清單。患者可以使用不同自然說法；只要語意直接等價，而且 source_text 能逐字在 current_user_text 找到，即可抽取。不得只因出現相似字詞就硬套欄位。

19. 每筆 ttas_evidence 只能包含 field、value、semantic_status、confidence、source_text。field 只能從下方 catalog 選；value 必須符合該 field 的 type。semantic_status 只能是 available、unknown、ambiguous。

20. source_text 必須是本輪 current_user_text 中直接支持該 evidence 的最短連續逐字片段。不得從 prior history 製造本輪 TTAS evidence。若同一事實也出現在 semantic_extractions，兩邊可以引用相同或各自最精準的 source_text。

21. confidence 只表示「患者本輪原文有多明確支持這個結構化事實」，不是病情嚴重度、疾病機率、TTAS level 機率，也不是你對醫療判斷的信心。原文沒有直接支持時，不得用較低 confidence 猜一筆 evidence。

22. 缺少資料時不要補正常值。使用者未提供血壓、心率、SpO2、GCS、體溫、血糖、年齡、懷孕週數等資料時，不得假設正常，也不得自行換算。使用者明確表示不知道時，才可在有 grounded source_text 的前提下輸出 unknown / ambiguous。

23. 不得把患者的主觀形容或一般症狀升格成臨床 modifier、病因或診斷。特別是：
- 不得從「喘得很嚴重」自行產生 respiratory_distress=severe。
- 不得自行判定 shock、ill_appearing、cardiac_chest_pain_suspected、high_risk_injury_mechanism 或 pain_location_class。
- 不得由「昏迷／叫不醒」自行估算 GCS。
- 不得由「很燒／很冷」自行估算 temperature_c。
- 不得由眼痛／灼熱反推化學暴露。
- 不得由紅疹／腫脹反推昆蟲螫傷。
- 不得由疼痛／腫脹反推 open fracture 或骨折／脫臼變形。
只有 current_user_text 直接支持 catalog 所描述的結構化事實時才可輸出。

24. age_years / age_months 只在本輪有逐字年齡證據時抽取；若 Backend 另有 trusted profile，該 trusted fact 由 Backend 合併，不需要你猜。

25. 你只負責抽 TTAS evidence。不得輸出或推算 TTAS level、urgency_score、warning_required、red_flags_checked、stage、is_complete、科別、醫師、掛號決策。

目前 production 可執行的 TTAS evidence catalog：
{json.dumps(prompt_evidence_catalog, ensure_ascii=False, indent=2)}
```

---

## 正例：同一句可同時產生 semantic + TTAS evidence

```text
current_user_text:
「剛剛清潔劑濺到我的右眼，現在眼睛灼痛。」
```

示範輸出：

```json
{
  "semantic_extractions": [
    {
      "field": "symptom",
      "normalized_value": "灼痛",
      "semantic_status": "available",
      "assertion": "present",
      "confidence": 0.99,
      "source_text": "眼睛灼痛",
      "needs_clarification": false,
      "follow_up_reason": null
    },
    {
      "field": "body_part",
      "normalized_value": "右眼",
      "semantic_status": "available",
      "assertion": null,
      "confidence": 0.99,
      "source_text": "右眼",
      "needs_clarification": false,
      "follow_up_reason": null
    }
  ],
  "pending_answer": null,
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

這個例子只是在教「語意對應與輸出責任」，不是在建立 `清潔劑` 關鍵字規則。

---

## 反例：不能從症狀倒推暴露原因

```text
current_user_text:
「我的右眼很痛。」
```

示範：

```json
{
  "semantic_extractions": [
    {
      "field": "symptom",
      "normalized_value": "眼痛",
      "semantic_status": "available",
      "assertion": "present",
      "confidence": 0.99,
      "source_text": "右眼很痛",
      "needs_clarification": false,
      "follow_up_reason": null
    },
    {
      "field": "body_part",
      "normalized_value": "右眼",
      "semantic_status": "available",
      "assertion": null,
      "confidence": 0.99,
      "source_text": "右眼",
      "needs_clarification": false,
      "follow_up_reason": null
    }
  ],
  "pending_answer": null,
  "ttas_evidence": []
}
```

沒有化學暴露原文，就不能輸出 `chemical_eye_injury=true`。

---

## Safety pending 保留現有契約

目前：

```text
pending intent = safety_screen
answer_assertion = present | absent | uncertain
```

這部分不要移除。

但即使本輪是在回答 safety question，只要患者同時補了可抽取的 TTAS evidence，仍要完整輸出 ttas_evidence。

例如：

```text
「剛才那些都沒有，但清潔劑剛剛濺到右眼。」
```

不能只輸出 `answer_assertion=absent` 而漏掉 `chemical_eye_injury=true`。

Backend 最終 workflow 仍依 TTAS evaluator 與 safety state 決定。
