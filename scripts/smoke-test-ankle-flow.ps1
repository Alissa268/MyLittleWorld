param([string]$BaseUrl = "http://127.0.0.1:8080")

$ErrorActionPreference = "Stop"
# Synthetic test data only. Calls the configured real AI/SQL services;
# generates guidance but never submits an appointment to the hospital.
function Post-Json([string]$Route, [hashtable]$Payload) {
    $json = $Payload | ConvertTo-Json -Depth 20
    $response = Invoke-WebRequest -UseBasicParsing -Uri ($BaseUrl.TrimEnd('/') + $Route) -Method Post `
        -ContentType 'application/json; charset=utf-8' `
        -Body ([Text.Encoding]::UTF8.GetBytes($json)) -TimeoutSec 60
    [Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray()) | ConvertFrom-Json
}

$description = '右腳踝疼痛一星期，微微腫脹，疼痛約3分，仍可正常走路，走路沒有明顯加重，休息會減輕，沒有跌倒或扭傷。'
$first = Post-Json '/chat' @{visit_type='initial'; message=$description}
if ($first.conversation_state.clarification_status -ne 'safety_check') {
    throw "未出現預期的安全確認；實際回覆：$($first.reply)"
}
$caseId = $first.case_id
$triage = Post-Json '/chat' @{case_id=$caseId; message='沒有上述任何急迫症狀。'}
# Follow only questions whose answers are explicitly defined for this synthetic case.
# A different clinical question fails visibly instead of forcing a department.
$followupCount = 0
while (-not $triage.conversation_state.awaiting_confirmation -and $followupCount -lt 6) {
    $question = $triage.next_question
    if ($question -match '疼痛.*(程度|幾分|多少分)|0.*10') { $answer='疼痛約3分，輕微不適，仍可正常走路。' }
    elseif ($question -match '部位|哪裡|位置') { $answer='疼痛位於右腳踝。' }
    elseif ($question -match '腫脹') { $answer='右腳踝微微腫脹。' }
    elseif ($question -match '多久|持續|幾天') { $answer='已經一星期。' }
    elseif ($question -match '受傷|扭傷|跌倒|外傷') { $answer='沒有跌倒、扭傷或其他外傷。' }
    elseif ($question -match '走路|行走|承重|休息') { $answer='仍能正常走路，走路沒有明顯加重，休息會減輕。' }
    else { throw "出現未預設答案的問題，需檢查：$question" }
    $triage = Post-Json '/chat' @{case_id=$caseId; message=$answer}
    $followupCount++
}
if (-not $triage.conversation_state.awaiting_confirmation -or
    -not $triage.triage_case.patient_input.red_flags_checked -or
    $triage.conversation_state.department_status -ne 'resolved' -or
    $null -eq $triage.department_result) {
    throw "未完成科別收斂；測試不會跳過確認。實際回覆：$($triage.reply)"
}
if ($triage.department_result.dept_id -ne 1298) {
    throw "此回歸案例的結果偏離既有一般骨科資料，需人工檢查，不能以有回傳推薦即視為通過。"
}
$confirmed = Post-Json '/chat' @{case_id=$caseId; confirmed=$true}
if (-not $confirmed.conversation_state.confirmed) { throw '案件未確認。' }
$result = Post-Json '/recommend' @{case_id=$caseId; visit_type='initial'; confirmed=$true}
$items = @($result.recommendations.specialty_first) + @($result.recommendations.time_first)
if ($items.Count -eq 0) { throw '正式班表沒有可推薦診次；不補入假資料。' }
foreach ($item in $items) {
    if ([string]::IsNullOrWhiteSpace($item.doctor_id) -or
        [string]::IsNullOrWhiteSpace($item.schedule_id) -or
        $item.dept_id -ne 1298 -or $item.visit_type -ne '初診') {
        throw '推薦缺少正式醫師／班表 ID，或違反科別／初診條件。'
    }
}
$selected = $items[0]
$guidance = Post-Json '/generate_script' @{case_id=$caseId; recommendation_id=$selected.recommendation_id}
if (-not $guidance.isSuccess -or $guidance.recommendation.schedule_id -ne $selected.schedule_id) {
    throw '選醫師後班表重新驗證失敗。'
}
[pscustomobject]@{
    case_id=$caseId
    department=$triage.department_result.childDept
    followup_count=$followupCount
    specialty_count=@($result.recommendations.specialty_first).Count
    time_count=@($result.recommendations.time_first).Count
    selected_doctor=$guidance.recommendation.doctor
    schedule_id=$guidance.recommendation.schedule_id
    script_success=$guidance.isSuccess
    script_steps=@($guidance.steps).Count
    hospital_booking_submitted=$false
} | ConvertTo-Json
