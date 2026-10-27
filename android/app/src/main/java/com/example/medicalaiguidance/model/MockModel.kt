package com.example.medicalaiguidance.model

data class MockSchedule(
    val date: String,
    val dayOfWeek: String,
    val timeSlot: String,
    val doctorName: String,
    val clinicName: String,
    val status: String = "可掛號",       // 保留：判定是否額滿
    val roomNumber: String? = null     // 保留：區分多重複同名醫生診別
)