package com.example.medicalaiguidance

import com.example.medicalaiguidance.service.BookingHighlightStability
import com.example.medicalaiguidance.service.HighlightBounds
import org.junit.Assert.*
import org.junit.Test

class DepartmentHighlightStabilityUnitTest {
    private val final = HighlightBounds(101, 1258, 1035, 1357)

    @Test fun timePersonalAndSubmitEntriesRequireFreshCoordinateChecks() {
        listOf("選擇看診時間", "選擇看診時間 / 醫師", "填寫個人資料", "確認送出").forEach {
            assertTrue(BookingHighlightStability.requiresRecheck(it, false))
        }
    }

    @Test fun departmentKeepsItsExistingRecheckAndOtherStepsAreUnchanged() {
        assertTrue(BookingHighlightStability.requiresRecheck("內科系", true))
        assertFalse(BookingHighlightStability.requiresRecheck("行動掛號", false))
        assertFalse(BookingHighlightStability.requiresRecheck("繼續掛號", false))
        assertFalse(BookingHighlightStability.requiresRecheck("確認取消掛號", false))
    }

    @Test fun reportedRightEdgeFrameMustWaitForNewPosition() {
        assertFalse(BookingHighlightStability.canDisplay(
            HighlightBounds(943, 1258, 1080, 1357), final, 1080, 27))
    }

    @Test fun reportedNearFinalFrameCanSettleWithinExistingTolerance() {
        assertTrue(BookingHighlightStability.canDisplay(
            HighlightBounds(104, 1258, 1038, 1357), final, 1080, 27))
    }

    @Test fun nodeStillEnteringFromOutsideScreenCannotBeDeclaredStable() {
        val outside = HighlightBounds(943, 1258, 1877, 1357)
        assertFalse(BookingHighlightStability.canDisplay(outside, outside, 1080, 27))
    }

    @Test fun verticalMovementAlsoRequiresAnotherObservation() {
        assertFalse(BookingHighlightStability.canDisplay(final, final.copy(top = 1358, bottom = 1457), 1080, 27))
    }

    @Test fun invalidOrMissingGeometryCannotBeDisplayed() {
        assertFalse(BookingHighlightStability.canDisplay(final, final.copy(right = 101), 1080, 27))
        assertFalse(BookingHighlightStability.canDisplay(final, final, 0, 27))
    }

    @Test fun stableOriginalBoundsAreAcceptedWithoutChangingTheirSize() {
        assertTrue(BookingHighlightStability.canDisplay(final, final, 1080, 27))
    }
}
