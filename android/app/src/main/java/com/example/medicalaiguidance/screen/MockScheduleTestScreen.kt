package com.example.medicalaiguidance.screen

import android.content.Intent
import android.net.Uri
import android.provider.Settings
import android.util.Log
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.navigation.NavHostController
import com.example.medicalaiguidance.model.MockSchedule
import com.example.medicalaiguidance.service.AppointmentType
import com.example.medicalaiguidance.service.MyAccessibilityService
import org.json.JSONArray
import org.json.JSONObject

@Composable
fun MockScheduleTestScreen(navController: NavHostController) {
    val context = LocalContext.current
    var schedules by remember { mutableStateOf<List<MockSchedule>?>(null) }
    var loadError by remember { mutableStateOf<String?>(null) }
    var departmentMap by remember { mutableStateOf<Map<String, List<String>>>(emptyMap()) }
    var showPermissionDialog by remember { mutableStateOf(false) }
    var missingDepartmentClinic by remember { mutableStateOf<String?>(null) }
    val primaryDark = Color(0xFF385E5E)
    val bgGradient = Brush.verticalGradient(listOf(Color(0xFFF5F9F9), Color(0xFFEAF2F2)))

    LaunchedEffect(Unit) {
        departmentMap = runCatching {
            val json = context.assets.open("vgh_departments.json")
                .bufferedReader()
                .use { it.readText() }
            JSONObject(json).toDepartmentMap()
        }.onFailure {
            loadError = "無法載入科別對照資料，請確認 vgh_departments.json。"
            Log.e("MockScheduleTest", "Failed to load departments", it)
        }.getOrDefault(emptyMap())

        schedules = runCatching {
            val json = context.assets.open("mock_doctor_schedules.json")
                .bufferedReader()
                .use { it.readText() }
            val array = JSONArray(json)
            (0 until array.length()).map { index ->
                val item = array.getJSONObject(index)
                MockSchedule(
                    date = item.optString("date"),
                    dayOfWeek = item.optString("dayOfWeek"),
                    timeSlot = item.optString("timeSlot"),
                    doctorName = item.optString("doctorName"),
                    clinicName = item.optString("clinicName"),
                    status = item.optString("status", "可掛號"),
                    roomNumber = item.optString("roomNumber").takeIf { it.isNotBlank() }
                )
            }
        }.onFailure {
            loadError = "無法載入測試班表，請確認 mock_doctor_schedules.json。"
            Log.e("MockScheduleTest", "Failed to load schedules", it)
        }.getOrDefault(emptyList())
    }

    fun isServiceEnabled(): Boolean {
        val expectedService = "${context.packageName}/${MyAccessibilityService::class.java.name}"
        val enabled = runCatching {
            Settings.Secure.getInt(context.contentResolver, Settings.Secure.ACCESSIBILITY_ENABLED)
        }.getOrDefault(0)
        if (enabled != 1) return false
        return Settings.Secure
            .getString(context.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES)
            ?.contains(expectedService) == true
    }

    fun startMockTest(schedule: MockSchedule, appointmentType: AppointmentType) {
        val parentDepartment = findParentDepartment(schedule.clinicName, departmentMap)
        if (parentDepartment == null) {
            missingDepartmentClinic = schedule.clinicName
            return
        }
        Log.d("vgh_id_detect", "Mock科別對應 clinic=${schedule.clinicName} parent=$parentDepartment")

        MyAccessibilityService.updateTarget(
            department = parentDepartment,
            clinic = schedule.clinicName,
            doctor = schedule.doctorName,
            date = schedule.date,
            timeSlot = schedule.timeSlot,
            appointmentType = appointmentType
        )

        val packageName = "tw.com.bicom.VGHTPE"
        val launchIntent = context.packageManager.getLaunchIntentForPackage(packageName)
        if (launchIntent != null) {
            context.startActivity(launchIntent)
        } else {
            context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://www.vghtpe.gov.tw/Index.action")))
        }
    }

    missingDepartmentClinic?.let { clinicName ->
        AlertDialog(
            onDismissRequest = { missingDepartmentClinic = null },
            title = { Text("找不到科別對應", fontWeight = FontWeight.Bold) },
            text = { Text("vgh_departments.json 尚未設定「$clinicName」所屬的第一層科別。") },
            confirmButton = {
                Button(
                    onClick = { missingDepartmentClinic = null },
                    colors = ButtonDefaults.buttonColors(containerColor = primaryDark)
                ) {
                    Text("知道了")
                }
            }
        )
    }

    if (showPermissionDialog) {
        AlertDialog(
            onDismissRequest = { showPermissionDialog = false },
            title = { Text("開啟紅框引導功能", fontWeight = FontWeight.Bold) },
            text = { Text("請先到協助工具開啟 Medical AI Guidance，再回來測試紅框。") },
            confirmButton = {
                Button(
                    onClick = {
                        showPermissionDialog = false
                        context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = primaryDark)
                ) {
                    Text("前往設定")
                }
            },
            dismissButton = {
                Button(onClick = { showPermissionDialog = false }) {
                    Text("取消")
                }
            }
        )
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(bgGradient)
            .statusBarsPadding()
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp, vertical = 18.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Box(
                modifier = Modifier
                    .shadow(4.dp, RoundedCornerShape(14.dp))
                    .background(Color.White, RoundedCornerShape(14.dp))
                    .clickable { navController.popBackStack() }
                    .padding(12.dp),
                contentAlignment = Alignment.Center
            ) {
                Icon(Icons.Default.ArrowBack, contentDescription = "返回", tint = primaryDark)
            }
            Spacer(modifier = Modifier.width(16.dp))
            Column {
                Text("紅框位置測試", fontSize = 24.sp, fontWeight = FontWeight.Bold, color = primaryDark)
                Text("本頁只用 mock JSON，不影響正式確認頁", fontSize = 13.sp, color = Color(0xFF607575))
            }
        }

        val currentSchedules = schedules
        if (loadError != null) {
            Text(loadError.orEmpty(), modifier = Modifier.padding(18.dp), color = primaryDark)
        } else if (currentSchedules == null) {
            Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator(color = primaryDark)
            }
        } else if (currentSchedules.isEmpty()) {
            Text("目前沒有測試班表", modifier = Modifier.padding(18.dp), color = primaryDark)
        } else {
            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(horizontal = 18.dp, vertical = 10.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                items(currentSchedules, key = {
                    "${it.date}-${it.timeSlot}-${it.clinicName}-${it.doctorName}-${it.roomNumber.orEmpty()}"
                }) { schedule ->
                    MockScheduleCard(
                        schedule = schedule,
                        onStart = { appointmentType ->
                            if (isServiceEnabled()) {
                                startMockTest(schedule, appointmentType)
                            } else {
                                showPermissionDialog = true
                            }
                        }
                    )
                }
            }
        }
    }
}

@Composable
private fun MockScheduleCard(
    schedule: MockSchedule,
    onStart: (AppointmentType) -> Unit
) {
    val primaryDark = Color(0xFF385E5E)
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(18.dp),
        colors = CardDefaults.cardColors(containerColor = Color.White)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                Text(
                    text = "${schedule.clinicName} ${schedule.timeSlot}",
                    color = primaryDark,
                    fontSize = 18.sp,
                    fontWeight = FontWeight.Bold
                )
                Text(
                    text = "${schedule.date} ${schedule.dayOfWeek}",
                    color = Color(0xFF5C6F6F),
                    fontSize = 14.sp
                )
                Text(
                    text = listOfNotNull(schedule.roomNumber, schedule.doctorName).joinToString(" "),
                    color = Color(0xFF1A2E2E),
                    fontSize = 16.sp,
                    fontWeight = FontWeight.Medium
                )
                Text(
                    text = schedule.status,
                    color = if (schedule.status == "可掛號") Color(0xFF1D7A45) else Color(0xFFB00020),
                    fontSize = 13.sp,
                    fontWeight = FontWeight.Bold
                )
            }

            Column(
                verticalArrangement = Arrangement.spacedBy(8.dp),
                horizontalAlignment = Alignment.End
            ) {
                Button(
                    onClick = { onStart(AppointmentType.INITIAL) },
                    colors = ButtonDefaults.buttonColors(containerColor = primaryDark)
                ) {
                    Icon(Icons.Default.PlayArrow, contentDescription = null)
                    Spacer(Modifier.width(4.dp))
                    Text("初診")
                }
                Button(
                    onClick = { onStart(AppointmentType.FOLLOWUP) },
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF5E7777))
                ) {
                    Icon(Icons.Default.PlayArrow, contentDescription = null)
                    Spacer(Modifier.width(4.dp))
                    Text("複診")
                }
            }
        }
    }
}

private fun JSONObject.toDepartmentMap(): Map<String, List<String>> =
    keys().asSequence().associateWith { parent ->
        optJSONArray(parent)?.let { children ->
            (0 until children.length()).mapNotNull { index -> children.optString(index).takeIf(String::isNotBlank) }
        }.orEmpty()
    }

private fun findParentDepartment(clinicName: String, departmentMap: Map<String, List<String>>): String? =
    when {
        clinicName in departmentMap.keys -> clinicName
        else -> departmentMap.entries.firstOrNull { (_, children) ->
            children.any { child -> child == clinicName }
        }?.key
    }
