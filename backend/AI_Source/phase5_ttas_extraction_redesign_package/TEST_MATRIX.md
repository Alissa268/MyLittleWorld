# 測試矩陣：TTAS Evidence Catalog 改版

## 1. 靜態／單元測試

### Catalog coverage

- 目前 `enabled` rules：32 條。
- 目前 enabled predicates 實際使用 evidence field：28 個。
- 這 28 個 field 必須全部存在 `evidence_catalog.json`。
- `reference_only` 專用 field 不應進 runtime prompt catalog。

### Fail closed

刻意測：

- enabled field 缺 catalog entry
- catalog source_id 不存在
- catalog malformed

都必須 loader error。

---

## 2. Prompt regression

Prompt 必須含：

- `chemical_eye_injury`
- 中文概念「化學物質濺入／直接接觸眼睛」
- 禁止由單純眼痛反推化學暴露
- confidence 定義
- 同一事實可同時出現在 semantic_extractions 與 ttas_evidence
- 正例
- 反例
- 禁止輸出 TTAS level

Prompt 不應再展示全部 schema fields。

---

## 3. 310 Live：Chemical eye positive

新 case：

```text
剛剛清潔劑濺到我的右眼，現在眼睛灼痛。
```

必須：

```text
semantic:
symptom = 灼痛
body_part = 右眼

ttas_evidence:
chemical_eye_injury = true
source_text ∈ 原文

ttas_result:
status = matched
official_code = E010509
level_candidate = 2

workflow:
clarification_status = urgent
stage = done
need_more_info = false
next_question = null
department_result 可為 null
/recommend 被 urgent guard 擋下
```

---

## 4. 310 Live：Chemical eye negative inference

新 case：

```text
我的右眼很痛。
```

必須：

```text
semantic:
有眼痛／右眼

ttas_evidence:
不得出現 chemical_eye_injury=true
```

不能因為「眼痛」而猜化學暴露。

---

## 5. 310 Live：穩定性

對 positive case 建立 5 個全新 case。

要求：

- 5/5 都抽到 `chemical_eye_injury=true`
- 每筆 source_text 都能在患者原文找到
- 5/5 都命中 E010509 Level 2

如果只有偶發 1 次漏抽，先記錄 provider/model variation。
如果明顯多次漏抽，再檢查 prompt 長度、catalog 結構或模型能力；不要先加 keyword list。

---

## 6. Phase 4 不退步

A1：

```text
我這三天一直頭暈，會有眩暈、天旋地轉的感覺。
```

仍先進 safety。

A2：

```text
都沒有，我沒有剛才提到的那些急迫症狀。
```

仍：

```text
red_flags_checked = true
red_flags_status = negative
```

並接回 department reasoning。

A3：

```text
會，轉頭或改變頭部位置時眩暈會更明顯，而且我右耳一直有耳鳴。
```

仍保留：

- 耳鳴 evidence
- 1333 耳科官方 KB 支持
- 多候選自然追問

這次 evidence catalog 改版不能破壞 Phase 4。
