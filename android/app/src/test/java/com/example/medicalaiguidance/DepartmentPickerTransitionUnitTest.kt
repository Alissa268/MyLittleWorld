package com.example.medicalaiguidance

import com.example.medicalaiguidance.service.hasSubClinicPickerLabels
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DepartmentPickerTransitionUnitTest {
    @Test fun clinicNameOnDepartmentPageIsNotAnOpenPicker() {
        assertFalse(hasSubClinicPickerLabels(listOf("依門診科別", "內科系", "一般內科"), "內科系"))
    }

    @Test fun cancelLabelAloneDoesNotEstablishClinicPicker() {
        assertFalse(hasSubClinicPickerLabels(listOf("CANCEL", "依門診科別", "內科系", "外科系"), "內科系"))
    }

    @Test fun existingPickerMarkersAllowClinicSelection() {
        assertTrue(hasSubClinicPickerLabels(listOf("CANCEL", "一般內科", "心臟內科"), "內科系"))
    }

    @Test fun currentDepartmentIsNotMistakenForChildClinic() {
        assertFalse(hasSubClinicPickerLabels(listOf("CANCEL", "眼科"), "眼科"))
    }

    @Test fun pickerCanBeRecognizedBeforeTargetClinicIsScrolledIntoView() {
        assertTrue(hasSubClinicPickerLabels(listOf(" CANCEL ", "一般內科"), "內科系"))
    }
}
