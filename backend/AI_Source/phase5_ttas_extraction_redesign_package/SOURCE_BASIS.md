# Prompt / Evidence Catalog 參考來源

## 權威順序

這次修改請依下列優先順序：

1. **最新版 `New_Android_Backend/backend/knowledge/ttas/source_manifest.json`**
   - 決定官方來源與版本策略。
2. **最新版 `New_Android_Backend/backend/knowledge/ttas/active_rules.json`**
   - 決定哪些 rule 是 `enabled`、哪些是 `reference_only`。
   - 決定 production evaluator 真正使用哪些 field。
3. **最新版 `New_Android_Backend/backend/knowledge/ttas/ttas_evidence_schema.json`**
   - 決定 field type / range / allowed values。
4. **舊版 `AILogic/triage.py`**
   - 只拿來參考「人類可讀的中文醫療情境怎麼寫給 AI 看」。
   - **不得**把舊版醫療門檻、90/50/20、default low、AI workflow authority 搬回來。

---

## 官方來源（直接沿用目前 source_manifest.json）

- 衛生福利部醫事司：公告修正急診五級檢傷分類基準  
  https://dep.mohw.gov.tw/DOMA/lp-5037-106.html

- 105.1.1 施行公告  
  https://dep.mohw.gov.tw/DOMA/cp-5037-57685-106.html

- 105 總表  
  https://www.mohw.gov.tw/dl-66174-ff4e5da2-ddc5-4f07-9f37-67434e5a1177.html

- 105 環境  
  https://www.mohw.gov.tw/dl-66173-8358fe1a-8ffb-4efd-b51b-51c164106409.html

- 105 外傷  
  https://www.mohw.gov.tw/dl-66170-17f9b861-3a3b-4d7d-88c3-c5df33d2743a.html

- 105 成人非外傷  
  https://www.mohw.gov.tw/dl-66171-5e1ceebd-e4bd-48f1-ab23-25b88950620a.html

- 105 兒童  
  https://www.mohw.gov.tw/dl-66172-6c630b47-a239-4923-8cbd-00b4e0ed0169.html

- 107.5.1 修正公告  
  https://dep.mohw.gov.tw/DOMA/cp-5037-57686-106.html

- 107 總表修正  
  https://www.mohw.gov.tw/dl-66176-0c5c4369-a7d9-4bce-9944-13f108d390e4.html

- 107 兒童修正  
  https://www.mohw.gov.tw/dl-66177-07a44c11-5d74-4bc5-8b91-e93c7b1939d1.html

- 108.6.11 成人非外傷修正公告  
  https://dep.mohw.gov.tw/DOMA/cp-5037-57687-106.html

- 108 成人非外傷修正  
  https://www.mohw.gov.tw/dl-66178-5d1feb29-76b1-46fd-99c1-b817a6e4b514.html

- 衛福部政策背景  
  https://www.mohw.gov.tw/cp-3161-26897-1.html

- 醫事司政策背景  
  https://dep.mohw.gov.tw/DOMA/fp-997-20446-106.html

- 台灣急診醫學會政策聲明  
  https://www.sem.org.tw/EJournal/Detail/162

---

## 舊版參考

舊版：

```text
https://github.com/Alissa268/MyLittleWorld/blob/AILogic/triage.py
```

舊版 Prompt 對這次 redesign 的價值只有：

> 它把很多情境寫成 AI 容易理解的中文概念，而不是只給英文 field name。

可參考的舊措辭包含：

- 體溫 >41°C 或 <32°C
- 持續抽搐
- 突發性視覺改變
- 免疫功能不全且發燒
- 槍傷
- 化學物質濺入眼睛
- 經期逾期且腹痛
- 咖啡色嘔吐物或黑便
- 抽搐後意識已恢復
- 廣泛性紅疹／水泡
- 外傷後肢體腫脹變形疑似骨折／脫臼
- 生殖器腫脹變形

但以下舊邏輯禁止採用：

- `<40 mg/dl` 低血糖簡化判法
- `high=90 / medium=50 / low=20`
- 未命中高／中就一律 low
- AI 直接設定 TTAS level
- AI 直接控制 is_complete / next_question / workflow
- 舊版任何已被目前 `active_rules.json` 標成 `reference_only` 的醫療判定

---

## evidence_catalog.json 的來源標示原則

每個 catalog field 都應帶：

```json
"source_basis": {
  "source_ids": [],
  "official_locators": [],
  "rule_ids": [],
  "legacy_prompt_phrase": null
}
```

注意：

- `official_locator` 與 `source_id` 是目前 repo 內 TTAS knowledge 的正式 provenance。
- `legacy_prompt_phrase` 只代表「舊 Prompt 曾用這種中文說法」，不是新的醫療權威。
- 如果某個 `meaning_zh` 是依 field 名稱作語意解釋、而不是官方原文逐字，應在 `basis_note` 明確標示。
