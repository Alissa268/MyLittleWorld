package com.example.medicalaiguidance

import com.example.medicalaiguidance.service.GuidanceSessionState
import com.example.medicalaiguidance.service.GuidanceType
import org.junit.Assert.*
import org.junit.Test

class DelayedBookingGuidanceUnitTest {
    private fun started() = GuidanceSessionState().apply {
        start(GuidanceType.BOOKING, listOf("行動掛號", "繼續掛號", "依門診科別"))
    }

    @Test fun activeSameStepMayDisplayDelayedGuidance() {
        val session = started()
        assertTrue(session.canDisplayDelayedGuidance(session.generation, 0))
    }

    @Test fun leavingAppDuringGracePeriodCannotRedisplayOldFrame() {
        val session = started()
        val generation = session.generation
        session.markTargetLeft()
        assertTrue(session.isCurrentGeneration(generation))
        assertFalse(session.canDisplayDelayedGuidance(generation, 0))
    }

    @Test fun advancingStepInvalidatesEarlierStepCallback() {
        val session = started()
        val generation = session.generation
        session.currentStepIndex = 1
        assertFalse(session.canDisplayDelayedGuidance(generation, 0))
        assertTrue(session.canDisplayDelayedGuidance(generation, 1))
    }

    @Test fun restartedSessionRejectsOldCallbackEvenAtSameStep() {
        val session = started()
        val generation = session.generation
        session.start(GuidanceType.BOOKING, listOf("行動掛號", "繼續掛號"))
        assertFalse(session.canDisplayDelayedGuidance(generation, 0))
        assertTrue(session.canDisplayDelayedGuidance(session.generation, 0))
    }

    @Test fun completionCannotRedisplayStepZero() {
        val session = started()
        val generation = session.generation
        session.finish()
        assertFalse(session.canDisplayDelayedGuidance(generation, 0))
        assertFalse(session.canDisplayDelayedGuidance(session.generation, 0))
    }

    @Test fun returningWithinGraceAllowsFreshGuidanceAtSameStep() {
        val session = started()
        session.currentStepIndex = 1
        session.markTargetLeft()
        session.markTargetReturned()
        assertEquals(1, session.currentStepIndex)
        assertTrue(session.canDisplayDelayedGuidance(session.generation, 1))
    }
}
