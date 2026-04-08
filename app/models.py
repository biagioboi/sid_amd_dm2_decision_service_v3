
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


class TwinDatasetReference(BaseModel):
    datasetId: str = "Shanghai_T2DM"
    cohort: str = "Shanghai T2DM"
    sourceUrl: str = (
        "https://github.com/KartikeyBartwal/Research-Work-On-Detecting-Type-1-and-Type-2-Diabetes-via-Shanghai-Dataset/tree/main/Shanghai_T2DM"
    )
    summaryUrl: str = (
        "https://github.com/KartikeyBartwal/Research-Work-On-Detecting-Type-1-and-Type-2-Diabetes-via-Shanghai-Dataset/blob/main/Shanghai_T2DM_Summary.xlsx"
    )
    publicationUrl: str = "https://www.nature.com/articles/s41597-023-01940-7"
    patientCount: int = 100
    cgmSamplingMinutes: int = 15
    minRecordingDays: int = 3
    maxRecordingDays: int = 14


class TwinGlucoseSample(BaseModel):
    minuteOffset: int = Field(..., ge=0)
    glucoseMgdl: float = Field(..., gt=0)
    source: str = Field(default="cgm")


class TwinPatientProfile(BaseModel):
    patientId: Optional[str] = None
    diabetesType: str = "type2"
    ageYears: Optional[int] = None
    sexAtBirth: Optional[str] = None
    diabetesDurationYears: Optional[float] = None
    weightKg: Optional[float] = None
    heightCm: Optional[float] = None
    bmi: Optional[float] = None
    hbA1cPercent: Optional[float] = None
    eGFRmlMin: Optional[float] = None
    baselineGlucoseMgdl: Optional[float] = 140.0
    carbRatioGramsPerUnit: Optional[float] = None
    insulinSensitivityMgdlPerUnit: Optional[float] = None
    gastricAbsorptionMinutes: int = Field(default=180, ge=30, le=480)
    insulinActionMinutes: int = Field(default=300, ge=60, le=720)
    exerciseSensitivityBoost: float = Field(default=0.15, ge=0.0, le=2.0)
    hepaticGlucoseReleaseMgdlPerHour: float = Field(default=6.0, ge=-10.0, le=30.0)
    oralClasses: List[str] = Field(default_factory=list)
    parameterModifiers: Dict[str, float] = Field(default_factory=dict)


class TwinEvent(BaseModel):
    eventType: str = Field(
        ...,
        description="meal|exercise|insulin-bolus|insulin-basal|medication|stress",
    )
    startMinutes: int = Field(..., ge=0)
    durationMinutes: int = Field(default=0, ge=0)
    label: Optional[str] = None
    mealCarbsGrams: Optional[float] = None
    glycemicIndex: float = Field(default=100.0, ge=10.0, le=150.0)
    insulinUnits: Optional[float] = None
    medicationClass: Optional[str] = None
    intensity: float = Field(default=1.0, ge=0.0, le=5.0)
    stressLoad: float = Field(default=0.0, ge=0.0, le=5.0)
    notes: Optional[str] = None


class TwinScenario(BaseModel):
    name: str = "baseline-day"
    description: Optional[str] = None
    horizonMinutes: int = Field(default=24 * 60, ge=60, le=14 * 24 * 60)
    dtMinutes: int = Field(default=15, ge=5, le=60)
    startingGlucoseMgdl: Optional[float] = Field(default=None, gt=0)
    events: List[TwinEvent] = Field(default_factory=list)


class TwinTrajectoryPoint(BaseModel):
    minute: int
    glucoseMgdl: float
    deltaMgdl: float
    carbsEffectMgdl: float = 0.0
    insulinEffectMgdl: float = 0.0
    exerciseEffectMgdl: float = 0.0
    hepaticEffectMgdl: float = 0.0


class TwinScenarioMetrics(BaseModel):
    horizonMinutes: int
    dtMinutes: int
    initialGlucose: float
    finalGlucose: float
    minGlucose: float
    maxGlucose: float
    meanGlucose: float
    timeInRange70_180: float
    timeBelow70: float
    timeAbove180: float
    timeAbove250: float


class TwinScenarioSimulationRequest(BaseModel):
    dataset: TwinDatasetReference = Field(default_factory=TwinDatasetReference)
    profile: TwinPatientProfile = Field(default_factory=TwinPatientProfile)
    scenario: TwinScenario
    facts: Optional[FactsModel] = Field(
        default=None,
        description="Optional clinical facts to align the simulator with the decision-service payload.",
    )
    historicalGlucose: List[TwinGlucoseSample] = Field(default_factory=list)


class TwinScenarioSimulationResponse(BaseModel):
    status: str
    reason: str
    dataset: TwinDatasetReference
    profile: TwinPatientProfile
    metrics: TwinScenarioMetrics
    assumptions: List[str] = Field(default_factory=list)
    trajectory: List[TwinTrajectoryPoint] = Field(default_factory=list)


class TwinCalibrationMetrics(BaseModel):
    observationCount: int
    rmseMgdl: float
    maeMgdl: float
    bestParameters: Dict[str, float] = Field(default_factory=dict)


class TwinDatasetCalibrationRequest(BaseModel):
    recordId: str = Field(..., description="Example: 2014_1_20210317")
    datasetRoot: str = "dataset/Shanghai_T2DM"
    summaryPath: str = "dataset/Shanghai_T2DM_Summary.xlsx"
    dtMinutes: int = Field(default=15, ge=5, le=60)


class TwinDatasetCalibrationResponse(BaseModel):
    recordId: str
    sourceFile: str
    dataset: TwinDatasetReference
    profile: TwinPatientProfile
    scenario: TwinScenario
    historicalGlucose: List[TwinGlucoseSample] = Field(default_factory=list)
    calibration: TwinCalibrationMetrics
    assumptions: List[str] = Field(default_factory=list)


class TwinDatasetSimulationRequest(BaseModel):
    recordId: str = Field(..., description="Example: 2014_1_20210317")
    datasetRoot: str = "dataset/Shanghai_T2DM"
    summaryPath: str = "dataset/Shanghai_T2DM_Summary.xlsx"
    dtMinutes: int = Field(default=15, ge=5, le=60)
    calibrateProfile: bool = True


class TwinDatasetSimulationResponse(BaseModel):
    recordId: str
    sourceFile: str
    dataset: TwinDatasetReference
    profile: TwinPatientProfile
    scenario: TwinScenario
    historicalGlucose: List[TwinGlucoseSample] = Field(default_factory=list)
    calibration: Optional[TwinCalibrationMetrics] = None
    simulation: TwinScenarioSimulationResponse
