
from pydantic import BaseModel, Field
from typing import Literal, Optional

VisitType = Literal["初診", "複診"]


class PatientInput(BaseModel):
    age: Optional[int] = None        
    gender: Optional[str] = None     
    symptom: str = ""
    body_part: Optional[str] = None
    duration: Optional[str] = None
    severity: Optional[str] = None
    onset: Optional[str] = None
    accompanying_symptoms: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    
class Slot(BaseModel):
    day: str = ""       
    session: str = ""    

class Availability(BaseModel):
    available_slots: list[Slot] = Field(default_factory=list)


class Preferences(BaseModel):
    specialty_priority: bool = True
    doctor_preference: str = "不限"        
    hospital_preference: str = "台北榮總"   


class Triage(BaseModel):
    urgency_score: Optional[int] = None    
    urgency_level: Optional[str] = None    
    warning_required: bool = False
    warning_message: Optional[str] = None
    need_more_info: bool = True
    next_question: Optional[str] = None


class ConversationState(BaseModel):
    stage: str = "collecting"  
    is_complete: bool = False


class HistoryRecord(BaseModel):
    role: str = ""      
    content: str = ""


class DepartmentResult(BaseModel):
    parentDept: str = ""
    childDept: str = ""
    confidence: float = 0.0             
    reason: list[str] = Field(default_factory=list)

class TriageCase(BaseModel):
    case_id: str
    visit_type: VisitType = "初診"
    history_records: list[HistoryRecord] = Field(default_factory=list)
    patient_input: PatientInput = Field(default_factory=PatientInput)
    availability: Availability = Field(default_factory=Availability)
    preferences: Preferences = Field(default_factory=Preferences)
    triage: Triage = Field(default_factory=Triage)
    conversation_state: ConversationState = Field(default_factory=ConversationState)
    department_result: Optional[DepartmentResult] = None   

class ChatRequest(BaseModel):
    message: str = ""
    triage_case: Optional[TriageCase] = None
    visit_type: VisitType = "初診"


class TriageResult(BaseModel):
    case_id: str
    triage_case: TriageCase
    conversation_state: ConversationState
    triage: Triage
    department_result: Optional[DepartmentResult] = None
    next_question: Optional[str] = None
    reply: str = ""
    needMoreInfo: bool = True



class RecommendRequest(BaseModel):
    triage_case: TriageCase
    preference: str = "醫師專長優先"


class FollowupChatRequest(BaseModel):
    dept: str = ""                 
    preference_answer: str = ""    
    case_id: str = ""


class FollowupRequest(BaseModel):
    childDept: str                                    
    case_id: str = ""
    parentDept: str = ""                              
    availability: Availability = Field(default_factory=Availability)   
    preferences: Preferences = Field(default_factory=Preferences)      


class RecommendationItem(BaseModel):
    recommendation_id: str
    parentDept: str
    childDept: str
    doctor: str
    date: str
    session: str
    session_time: str = ""          
    room: str = ""
    score: float = 0.0              
    reasons: list[str] = Field(default_factory=list)


class FallbackDepartment(BaseModel):
    parentDept: str
    childDept: str
    reason: str


class RecommendationResult(BaseModel):
    case_id: str
    department: Optional[DepartmentResult] = None
    recommendations: list[RecommendationItem] = Field(default_factory=list)
    fallback_departments: list[FallbackDepartment] = Field(default_factory=list)



class ChatResponse(BaseModel):
    case_id: str
    reply: str = ""
    needMoreInfo: bool = True
    triage_case: TriageCase
    recommendation: Optional[RecommendationResult] = None
    debug: Optional[dict] = None

