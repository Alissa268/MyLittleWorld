package com.example.medicalaiguidance

import com.example.medicalaiguidance.service.canAdvanceToBookingEntry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ClinicSelectionTransitionUnitTest {
    @Test fun disabledTimeEntryBeforeClinicSelectionDoesNotAdvance() {
        assertFalse(canAdvanceToBookingEntry(true, false, false))
    }

    @Test fun timeEntryBehindDialogDoesNotAdvance() {
        assertFalse(canAdvanceToBookingEntry(true, true, true))
    }

    @Test fun invisibleTimeEntryDoesNotAdvance() {
        assertFalse(canAdvanceToBookingEntry(false, true, false))
    }

    @Test fun enabledTimeEntryAfterClinicSelectionAdvancesWithoutCalendar() {
        // Actual intermediate screen: the calendar opens only after tapping the time entry.
        assertTrue(canAdvanceToBookingEntry(true, true, false))
    }

    @Test fun selectingClinicEnablesTimeEntryAfterPickerCloses() {
        assertEquals(listOf(false, false, false, true), listOf(
            canAdvanceToBookingEntry(false, false, true),
            canAdvanceToBookingEntry(true, false, false),
            canAdvanceToBookingEntry(true, true, true),
            canAdvanceToBookingEntry(true, true, false)
        ))
    }
}
