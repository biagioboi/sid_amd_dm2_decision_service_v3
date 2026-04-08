
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class EncounterModel(BaseModel):
    encounterId: Optional[str] = None
    setting: Optional[str] = Field(default="outpatient")
    clinicianId: Optional[str] = None
    timestamp: Optional[datetime] = None


class PatientModel(BaseModel):
    patientId: str
    birthDate: Optional[date] = None
    sexAtBirth: Optional[str] = "unknown"


class GoalsModel(BaseModel):
    currentHbA1cTargetPercent: Optional[float] = None
    highHypoglycemiaRisk: Optional[bool] = False


class LabsModel(BaseModel):
    hbA1cPercent: Optional[float] = None
    eGFRmlMin: Optional[float] = None
    bmi: Optional[float] = None
    uacrMgG: Optional[float] = None
    lastLabDate: Optional[date] = None


class ComorbiditiesModel(BaseModel):
    priorCardiovascularEvent: Optional[bool] = False
    heartFailure: Optional[bool] = False
    chronicKidneyDisease: Optional[bool] = False
    severeHypoglycemiaHistory: Optional[bool] = False


class InsulinModel(BaseModel):
    onBasal: Optional[bool] = False
    basalType: Optional[str] = "none"
    onPrandial: Optional[bool] = False
    onBasalBolus: Optional[bool] = False
    onPump: Optional[bool] = False


class ContraindicationsModel(BaseModel):
    metformin: Optional[bool] = False
    sglt2i: Optional[bool] = False
    glp1ra: Optional[bool] = False


class CurrentTherapyModel(BaseModel):
    oralClasses: List[str] = Field(default_factory=list)
    insulin: InsulinModel = Field(default_factory=InsulinModel)
    contraindications: ContraindicationsModel = Field(default_factory=ContraindicationsModel)


class LifestyleModel(BaseModel):
    structuredNutritionProgram: Optional[bool] = False
    followsMediterraneanPattern: Optional[bool] = False
    lowGlycemicIndexPattern: Optional[bool] = False
    regularPhysicalActivity: Optional[bool] = False
    minutesExercisePerWeek: Optional[int] = 0


class EducationModel(BaseModel):
    structuredEducationDone: Optional[bool] = False
    groupEducationFeasible: Optional[bool] = False


class RecentGlucosePatternModel(BaseModel):
    fastingMean: Optional[float] = None
    postPrandialMean: Optional[float] = None
    hypoglycemiaEpisodes30d: Optional[int] = 0
    hyperglycemiaEpisodes30d: Optional[int] = 0


class MonitoringModel(BaseModel):
    smbgStructured: Optional[bool] = False
    cgmAvailable: Optional[bool] = False
    recentGlucosePattern: RecentGlucosePatternModel = Field(default_factory=RecentGlucosePatternModel)


class ProvenanceModel(BaseModel):
    sourceSystem: Optional[str] = None
    fhirBundleId: Optional[str] = None


class FactsModel(BaseModel):
    diabetesType: str = "type2"
    goals: GoalsModel = Field(default_factory=GoalsModel)
    labs: LabsModel = Field(default_factory=LabsModel)
    comorbidities: ComorbiditiesModel = Field(default_factory=ComorbiditiesModel)
    currentTherapy: CurrentTherapyModel = Field(default_factory=CurrentTherapyModel)
    lifestyle: LifestyleModel = Field(default_factory=LifestyleModel)
    education: EducationModel = Field(default_factory=EducationModel)
    monitoring: MonitoringModel = Field(default_factory=MonitoringModel)
    provenance: ProvenanceModel = Field(default_factory=ProvenanceModel)


class DecisionEvaluationRequest(BaseModel):
    guidelineVersion: str = "SID-AMD-DM2-2022.12"
    encounter: Optional[EncounterModel] = None
    patient: PatientModel
    facts: FactsModel


class ProposedAction(BaseModel):
    actionType: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class Recommendation(BaseModel):
    code: str
    title: str
    category: str
    priority: str
    sidAmdReference: str
    recommendationText: str
    rationale: str
    requiresClinicianApproval: bool = True
    proposedActions: List[ProposedAction] = Field(default_factory=list)


class AppliedRule(BaseModel):
    ruleId: str
    sidAmdReference: str
    outcome: str


class MissingData(BaseModel):
    field: str
    reason: str
    blocking: bool = True


class Alert(BaseModel):
    code: str
    severity: str
    message: str


class Summary(BaseModel):
    primaryTherapyPath: Optional[str] = None
    monitoringPath: Optional[str] = None
    educationPath: Optional[str] = None


class AuditInfo(BaseModel):
    guidelineVersionUsed: str
    decisionModelVersion: str
    generatedAt: datetime
    traceId: str


class FhirInfo(BaseModel):
    requestOrchestrationId: Optional[str] = None
    bundleId: Optional[str] = None


class DigitalTwinMetrics(BaseModel):
    horizonMinutes: int
    dtMinutes: int
    minGlucose: float
    maxGlucose: float
    meanGlucose: float
    timeInRange70_180: float
    timeBelow70: float
    timeAbove250: float


class DigitalTwinPlan(BaseModel):
    correction_uI: float = 0.0
    dinner_cut_g: float = 0.0
    walk_minutes: float = 0.0
    parameterModifiers: Dict[str, float] = Field(default_factory=dict)


class DigitalTwinEvaluation(BaseModel):
    status: str = Field(description="pass|warn|fail")
    reason: str
    metrics: DigitalTwinMetrics
    plan: DigitalTwinPlan


class DecisionEvaluationResponse(BaseModel):
    evaluationId: str
    status: str
    summary: Summary
    recommendations: List[Recommendation]
    appliedRules: List[AppliedRule]
    missingData: List[MissingData] = Field(default_factory=list)
    alerts: List[Alert] = Field(default_factory=list)
    audit: AuditInfo
    fhir: Optional[FhirInfo] = None
    digitalTwin: Optional[DigitalTwinEvaluation] = None


class DecisionErrorDetail(BaseModel):
    field: str
    issue: str


class DecisionErrorResponse(BaseModel):
    code: str
    message: str
    details: List[DecisionErrorDetail] = Field(default_factory=list)


class DigitalTwinSimulateRequest(BaseModel):
    glucose0_mgdl: float = Field(..., description="Starting glucose (mg/dL)")
    plan: DigitalTwinPlan = Field(default_factory=DigitalTwinPlan)
    facts: Optional[FactsModel] = Field(
        default=None,
        description="Optional clinical facts used to personalize the twin parameters.",
    )
    horizonMinutes: int = 24 * 60
    dtMinutes: int = 5


class DigitalTwinSimulateResponse(BaseModel):
    status: str
    reason: str
    metrics: DigitalTwinMetrics
    plan: DigitalTwinPlan
