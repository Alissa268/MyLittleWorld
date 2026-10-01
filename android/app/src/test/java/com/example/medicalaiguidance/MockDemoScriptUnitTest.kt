package com.example.medicalaiguidance

import androidx.lifecycle.viewModelScope
import com.example.medicalaiguidance.demo.MockDemoScript
import com.example.medicalaiguidance.model.MessageSender
import com.example.medicalaiguidance.model.VisitPlan
import com.example.medicalaiguidance.repository.MedicalRepository
import com.example.medicalaiguidance.viewmodel.ChatViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.cancel
import kotlinx.coroutines.test.*
import org.junit.Assert.*
import org.junit.Test

@OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
class MockDemoScriptUnitTest {
    @Test
    fun scriptContainsExactlyEightOrderedMessagesAndSevenDelays() {
        assertEquals(listOf(
            "最近哪裡不舒服？可以直接跟我說。",
            "了解。通常是什麼時候比較明顯？像是走路、上下樓梯，還是休息的時候也會痛？",
            "收到。這種情況大概持續多久了？",
            "好，有腫的情況我一起記下來。這段時間有沒有跌倒、扭到，或撞到右膝？",
            "了解，沒有明顯外傷。現在還能正常走路嗎？",
            "目前只能先依你描述的狀況協助掛號，還不能只靠對話判斷嚴重程度。不過你目前仍能走路，也沒有提到明顯外傷，我會先幫你找適合評估膝蓋問題的科別。平常什麼時段比較方便？",
            "好，我會優先找上午的門診。如果近期上午沒有合適的時段，下午也可以嗎？",
            "依照你目前提供的資訊，建議先掛一般骨科。看診時間會優先安排上午，下午也可以。"
        ), MockDemoScript.systemMessages)
        assertEquals(listOf(1300L, 1800L, 1500L, 2100L, 1600L, 1400L, 2600L),
            MockDemoScript.replies.map { it.delayMs })
        assertEquals(listOf("正在了解你的情況…", "正在整理症狀…", "正在整理你補充的資訊…",
            "正在分析目前資訊…", "正在確認你的情況…", "正在整理掛號需求…", "正在整理適合的掛號方向…"),
            MockDemoScript.replies.map { it.thinkingLabel })
        for (step in 0..6) {
            assertFalse(MockDemoScript.isComplete(step))
            assertEquals(MockDemoScript.systemMessages[step + 1], MockDemoScript.nextReply(step)?.message)
        }
        assertTrue(MockDemoScript.isComplete(7))
        assertNull(MockDemoScript.nextReply(7))
    }

    @Test
    fun initialFlowAcceptsAnyNonblankInputStopsAtSevenAndRestartsLocally() = runTest {
        Dispatchers.setMain(StandardTestDispatcher(testScheduler))
        val repository = MedicalRepository()
        val model = ChatViewModel(repository, ttsSessionFactory = { error("Demo must not start remote TTS") })
        try {
            model.startNewConversation(VisitPlan.INITIAL)
            assertEquals(listOf(MockDemoScript.openingMessage), model.messages.value.map { it.content })
            assertFalse(model.isAiThinking.value)
            assertNull(repository.getActiveCaseId())
            model.onInputTextChanged(" \n\t ")
            model.sendMessage({ error("Demo must not navigate before confirmation") })
            assertEquals(1, model.messages.value.size)
            for (step in 0..6) {
                val reply = MockDemoScript.nextReply(step)!!
                model.onInputTextChanged("任意文字-$step")
                model.sendMessage({ error("Demo must not navigate before confirmation") })
                assertEquals(MessageSender.USER, model.messages.value.last().sender)
                assertEquals("", model.inputText.value)
                assertTrue(model.isAiThinking.value)
                assertEquals(reply.thinkingLabel, model.thinkingLabel.value)
                model.sendMessage({ error("Duplicate send") })
                runCurrent()
                advanceTimeBy(reply.delayMs - 1)
                assertEquals(MessageSender.USER, model.messages.value.last().sender)
                advanceTimeBy(1)
                runCurrent()
                assertEquals(reply.message, model.messages.value.last().content)
                assertFalse(model.isAiThinking.value)
                assertEquals("", model.thinkingLabel.value)
                assertNull(repository.getActiveCaseId())
                model.prepareSpeech(model.messages.value.last(), "chinese")
                model.prepareSpeech(model.messages.value.last(), "taiwanese")
            }
            assertEquals(15, model.messages.value.size)
            assertTrue(model.showDecisionButtons.value)
            assertTrue(model.showDoctorButton.value)
            assertTrue(model.isDemoComplete.value)
            model.onInputTextChanged("第八句")
            model.sendMessage({ error("Demo must stay complete") })
            runCurrent()
            assertEquals(15, model.messages.value.size)
            model.continueEditing()
            assertEquals(listOf(MockDemoScript.openingMessage), model.messages.value.map { it.content })
            assertFalse(model.showDecisionButtons.value)
            assertFalse(model.isDemoComplete.value)
            assertNull(repository.getActiveCaseId())
            model.onInputTextChanged("重播")
            model.sendMessage({})
            advanceUntilIdle()
            assertEquals(MockDemoScript.replies.first().message, model.messages.value.last().content)
        } finally {
            model.viewModelScope.cancel()
            repository.clearChatMessages()
            repository.clearRecommendationFlow()
            Dispatchers.resetMain()
        }
    }
}
