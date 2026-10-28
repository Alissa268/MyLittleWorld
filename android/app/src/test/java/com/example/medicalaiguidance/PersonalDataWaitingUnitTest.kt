package com.example.medicalaiguidance

import com.example.medicalaiguidance.service.canLeavePersonalDataWaiting
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PersonalDataWaitingUnitTest {
    @Test fun submitButtonOnPersonalDataFormDoesNotEndPrivacyWaiting() {
        assertFalse(canLeavePersonalDataWaiting(true, true, true, false))
    }

    @Test fun disappearingFormDuringLoadingIsNotEnoughToResumeGuidance() {
        assertFalse(canLeavePersonalDataWaiting(false, false, false, false))
    }

    @Test fun disabledSubmitOrDialogCannotResumeGuidance() {
        assertFalse(canLeavePersonalDataWaiting(false, true, false, false))
        assertFalse(canLeavePersonalDataWaiting(false, true, true, true))
    }

    @Test fun reviewScreenWithAvailableSubmitCanResumeGuidance() {
        assertTrue(canLeavePersonalDataWaiting(false, true, true, false))
    }

    @Test fun formLoadingAndReviewSequenceKeepsWaitingUntilReviewIsReady() {
        assertEquals(listOf(false, false, false, true), listOf(
            canLeavePersonalDataWaiting(true, true, true, false),
            canLeavePersonalDataWaiting(false, false, false, false),
            canLeavePersonalDataWaiting(false, true, false, false),
            canLeavePersonalDataWaiting(false, true, true, false)
        ))
    }
}
