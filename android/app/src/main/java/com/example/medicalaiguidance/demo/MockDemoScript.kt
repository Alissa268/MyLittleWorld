package com.example.medicalaiguidance.demo

/** Recording-only initial consultation. Input content never selects a reply. */
object MockDemoScript {
    data class Reply(val message: String, val thinkingLabel: String, val delayMs: Long)

    const val openingMessage = "最近哪裡不舒服？可以直接跟我說。"
    val replies: List<Reply> = listOf(
        Reply("了解。通常是什麼時候比較明顯？像是走路、上下樓梯，還是休息的時候也會痛？", "正在了解你的情況…", 1300L),
        Reply("收到。這種情況大概持續多久了？", "正在整理症狀…", 1800L),
        Reply("好，有腫的情況我一起記下來。這段時間有沒有跌倒、扭到，或撞到右膝？", "正在整理你補充的資訊…", 1500L),
        Reply("了解，沒有明顯外傷。現在還能正常走路嗎？", "正在分析目前資訊…", 2100L),
        Reply("目前只能先依你描述的狀況協助掛號，還不能只靠對話判斷嚴重程度。不過你目前仍能走路，也沒有提到明顯外傷，我會先幫你找適合評估膝蓋問題的科別。平常什麼時段比較方便？", "正在確認你的情況…", 1600L),
        Reply("好，我會優先找上午的門診。如果近期上午沒有合適的時段，下午也可以嗎？", "正在整理掛號需求…", 1400L),
        Reply("依照你目前提供的資訊，建議先掛一般骨科。看診時間會優先安排上午，下午也可以。", "正在整理適合的掛號方向…", 2600L)
    )
    val systemMessages: List<String> = listOf(openingMessage) + replies.map { it.message }

    /** Step is the number of replies already shown after the opening message. */
    fun nextReply(step: Int): Reply? = replies.getOrNull(step)
    fun isComplete(step: Int): Boolean = step >= replies.size
    fun containsMessage(text: String): Boolean = text.trim() in systemMessages
}
