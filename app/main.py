from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.security import OAuth2PasswordRequestForm

from .auth import authenticate_user, create_access_token, require_role
from .digital_twin import derive_parameter_modifiers_from_facts, evaluate_plan
from .engine import evaluate_request
from .fhir_adapter import apply_plandefinition, load_library_cql, load_plandefinition
from .models import (
    DecisionEvaluationRequest,
    DigitalTwinMetrics,
    DigitalTwinSimulateRequest,
    DigitalTwinSimulateResponse,
    TwinDatasetCalibrationRequest,
    TwinDatasetCalibrationResponse,
    TwinDatasetSimulationRequest,
    TwinDatasetSimulationResponse,
    TwinScenarioSimulationRequest,
)
from .persistence import get_evaluation, init_db, list_audit_events, save_evaluation
from .shanghai_dataset import load_shanghai_record
from .twin import calibrate_profile_to_history, simulate_scenario

app = FastAPI(
    title="SID/AMD DM2 Decision Service",
    version="3.0.0",
    description="Reference implementation for a DM2 decision service aligned to SID/AMD 2022."
)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": app.version}


@app.post("/auth/token")
def login(form_data: OAuth2PasswordRequestForm = Depends()) -> dict:
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    token = create_access_token(sub=user["username"], roles=user.get("roles", []))
    return {"access_token": token, "token_type": "bearer"}


@app.post("/v1/decision-evaluations", dependencies=[Depends(require_role("clinician"))])
def evaluate(body: DecisionEvaluationRequest) -> dict:
    result = evaluate_request(body)
    payload = body.model_dump(mode="json") if hasattr(body, "model_dump") else body.dict()
    response = result.model_dump(mode="json") if hasattr(result, 'model_dump') else result.dict()
    save_evaluation(result=response, request_payload=payload)
    return response


@app.get("/v1/decision-evaluations/{evaluation_id}", dependencies=[Depends(require_role("clinician"))])
def get_saved_evaluation(evaluation_id: str) -> dict:
    result = get_evaluation(evaluation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return result


@app.get("/v1/decision-evaluations/{evaluation_id}/audit", dependencies=[Depends(require_role("clinician"))])
def get_saved_audit(evaluation_id: str) -> dict:
    result = get_evaluation(evaluation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return {"evaluationId": evaluation_id, "events": list_audit_events(evaluation_id)}


@app.get("/fhir/PlanDefinition/sid-amd-dm2-2022")
def get_plandefinition() -> dict:
    return load_plandefinition()


@app.get("/fhir/Library/sid-amd-dm2-logic", response_class=PlainTextResponse)
def get_library() -> str:
    return load_library_cql()


@app.post("/fhir/PlanDefinition/sid-amd-dm2-2022/$apply", dependencies=[Depends(require_role("clinician"))])
def apply_plan(payload: dict) -> dict:
    try:
        return apply_plandefinition(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/v1/digital-twin/simulate", dependencies=[Depends(require_role("clinician"))])
def digital_twin_simulate(body: DigitalTwinSimulateRequest) -> dict:
    # Merge personalization modifiers (from facts) with any explicit overrides in the plan.
    modifiers = {}
    if body.facts is not None:
        modifiers.update(derive_parameter_modifiers_from_facts(body.facts))
    # Explicit overrides multiply on top.
    for k, v in (body.plan.parameterModifiers or {}).items():
        try:
            modifiers[k] = float(modifiers.get(k, 1.0)) * float(v)
        except Exception:
            continue

    metrics, status, reason = evaluate_plan(
        glucose0_mgdl=float(body.glucose0_mgdl),
        correction_uI=float(body.plan.correction_uI),
        dinner_cut_g=float(body.plan.dinner_cut_g),
        walk_minutes=float(body.plan.walk_minutes),
        parameter_modifiers=modifiers,
        horizon_minutes=int(body.horizonMinutes),
        dt_minutes=int(body.dtMinutes),
    )
    resp = DigitalTwinSimulateResponse(
        status=status,
        reason=reason,
        metrics=DigitalTwinMetrics(**metrics),
        plan=body.plan.model_copy(update={"parameterModifiers": modifiers}),
    )
    return resp.model_dump(mode="json") if hasattr(resp, "model_dump") else resp.dict()


@app.post("/v1/digital-twin/simulate-advanced", dependencies=[Depends(require_role("clinician"))])
def digital_twin_simulate_advanced(body: TwinScenarioSimulationRequest) -> dict:
    resp = simulate_scenario(body)
    return resp.model_dump(mode="json") if hasattr(resp, "model_dump") else resp.dict()


@app.post("/v1/digital-twin/calibrate-from-dataset", dependencies=[Depends(require_role("clinician"))])
def digital_twin_calibrate_from_dataset(body: TwinDatasetCalibrationRequest) -> dict:
    try:
        dataset, profile, scenario, historical, source_file, assumptions = load_shanghai_record(
            record_id=body.recordId,
            dataset_root=body.datasetRoot,
            summary_path=body.summaryPath,
            dt_minutes=int(body.dtMinutes),
        )
        calibrated_profile, calibration, calibration_assumptions = calibrate_profile_to_history(
            profile,
            scenario,
            historical,
            None,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    resp = TwinDatasetCalibrationResponse(
        recordId=body.recordId,
        sourceFile=source_file,
        dataset=dataset,
        profile=calibrated_profile,
        scenario=scenario,
        historicalGlucose=historical,
        calibration=calibration,
        assumptions=assumptions + calibration_assumptions,
    )
    return resp.model_dump(mode="json") if hasattr(resp, "model_dump") else resp.dict()


@app.post("/v1/digital-twin/simulate-from-dataset", dependencies=[Depends(require_role("clinician"))])
def digital_twin_simulate_from_dataset(body: TwinDatasetSimulationRequest) -> dict:
    try:
        dataset, profile, scenario, historical, source_file, assumptions = load_shanghai_record(
            record_id=body.recordId,
            dataset_root=body.datasetRoot,
            summary_path=body.summaryPath,
            dt_minutes=int(body.dtMinutes),
        )
        calibration = None
        profile_to_use = profile
        extra_assumptions = list(assumptions)
        if body.calibrateProfile:
            profile_to_use, calibration, calibration_assumptions = calibrate_profile_to_history(
                profile,
                scenario,
                historical,
                None,
            )
            extra_assumptions.extend(calibration_assumptions)
        simulation = simulate_scenario(
            TwinScenarioSimulationRequest(
                dataset=dataset,
                profile=profile_to_use,
                scenario=scenario,
                historicalGlucose=historical,
            )
        )
        simulation.assumptions = extra_assumptions + list(simulation.assumptions)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    resp = TwinDatasetSimulationResponse(
        recordId=body.recordId,
        sourceFile=source_file,
        dataset=dataset,
        profile=profile_to_use,
        scenario=scenario,
        historicalGlucose=historical,
        calibration=calibration,
        simulation=simulation,
    )
    return resp.model_dump(mode="json") if hasattr(resp, "model_dump") else resp.dict()
