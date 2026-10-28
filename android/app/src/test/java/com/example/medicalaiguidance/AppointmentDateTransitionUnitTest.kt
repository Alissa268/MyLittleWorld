package com.example.medicalaiguidance

import com.example.medicalaiguidance.service.canLeaveAppointmentDate
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AppointmentDateTransitionUnitTest {
    @Test fun selectedDayAndPreloadedDoctorNameAreNotEnoughWithoutSessionList() {
        assertFalse(canLeaveAppointmentDate(true, true, false, false))
    }

    @Test fun sessionListDoesNotReplaceSelectedDateEvidence() {
        assertFalse(canLeaveAppointmentDate(false, true, true, false))
    }

    @Test fun backgroundSessionListCannotAdvanceWhileDialogIsOpen() {
        assertFalse(canLeaveAppointmentDate(true, true, true, true))
    }

    @Test fun visibleSessionsAfterDateSelectionAllowDoctorStepEvenIfDoctorNeedsScrolling() {
        // The rule deliberately does not require the target doctor's name to be visible.
        assertTrue(canLeaveAppointmentDate(true, true, true, false))
    }

    @Test fun loadingAndOpeningThenClosingDialogWaitForActualSessionScreen() {
        val decisions = listOf(
            canLeaveAppointmentDate(false, true, false, false),
            canLeaveAppointmentDate(true, true, false, false),
            canLeaveAppointmentDate(true, true, true, true),
            canLeaveAppointmentDate(true, true, true, false)
        )
        assertEquals(listOf(false, false, false, true), decisions)
    }

    @Test fun nonDoctorNextStepKeepsExistingDateOnlyBehavior() {
        assertTrue(canLeaveAppointmentDate(true, false, false, false))
        assertFalse(canLeaveAppointmentDate(false, false, true, false))
    }
}
