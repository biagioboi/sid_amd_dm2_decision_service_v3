
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4
import json

from .models import DecisionEvaluationRequest
from .engine import evaluate_request

FHIR_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "fhir"


def load_plandefinition() -> Dict[str, Any]:
    with (FHIR_DIR / "PlanDefinition-sid-amd-dm2-2022.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_library_cql() -> str:
    with (FHIR_DIR / "Library-sid-amd-dm2-logic.cql").open("r", encoding="utf-8") as handle:
        return handle.read()


def _collect_bundle_from_parameters(parameters: Dict[str, Any]) -> Dict[str, Any]:
    if parameters.get("resourceType") == "Bundle":
        return parameters
    for parameter in parameters.get("parameter", []):
        if parameter.get("name") == "data" and isinstance(parameter.get("resource"), dict):
            return parameter["resource"]
    raise ValueError("Parameters.resource[data] Bundle is required")


def _resource_text(resource: Dict[str, Any], key: str = "code") -> str:
    value = resource.get(key, {})
    if isinstance(value, dict):
        text = value.get("text")
        if text:
            return str(text).lower()
        codings = value.get("coding", [])
        for coding in codings:
            for candidate in ("display", "code"):
                if coding.get(candidate):
                    return str(coding[candidate]).lower()
    return ""


def _med_text(resource: Dict[str, Any]) -> str:
    for field in ("medicationCodeableConcept", "medication"):
        value = resource.get(field)
        if isinstance(value, dict):
            text = value.get("text")
            if text:
                return str(text).lower()
            code = value.get("code")
            if isinstance(code, dict) and code.get("text"):
                return str(code["text"]).lower()
    return ""


def bundle_to_request(payload: Dict[str, Any]) -> DecisionEvaluationRequest:
    bundle = _collect_bundle_from_parameters(payload)
    patient_id = "unknown"
    hbA1c = None
    egfr = None
    prior_cvd = False
    heart_failure = False
    ckd = False
    oral_classes: List[str] = []
    on_basal = False
    on_prandial = False
    on_basal_bolus = False
    on_pump = False
    structured_education_done = False
    group_education_feasible = False
    structured_nutrition = False
    regular_physical_activity = False
    smbg_structured = False
    cgm_available = False
    hypoglycemia_episodes = 0

    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        rtype = resource.get("resourceType")
        if rtype == "Patient":
            patient_id = resource.get("id", patient_id)
        elif rtype == "Observation":
            label = _resource_text(resource)
            value = None
            if isinstance(resource.get("valueQuantity"), dict):
                value = resource["valueQuantity"].get("value")
            elif resource.get("valueInteger") is not None:
                value = resource.get("valueInteger")
            elif resource.get("valueBoolean") is not None:
                value = resource.get("valueBoolean")

            if "4548-4" in label or "hba1c" in label or "emoglobina glicata" in label:
                hbA1c = value
            elif "egfr" in label or "33914-3" in label or "filtr" in label:
                egfr = value
            elif "hypoglycemia episodes" in label and value is not None:
                hypoglycemia_episodes = int(value)
            elif "structured smbg" in label:
                smbg_structured = bool(value)
            elif "cgm available" in label:
                cgm_available = bool(value)
            elif "structured nutrition" in label:
                structured_nutrition = bool(value)
            elif "regular physical activity" in label:
                regular_physical_activity = bool(value)
        elif rtype == "Condition":
            label = _resource_text(resource)
            if any(token in label for token in ("heart failure", "scompenso")):
                heart_failure = True
            if any(token in label for token in ("myocardial", "stroke", "ischemic", "evento cardiovascolare")):
                prior_cvd = True
            if any(token in label for token in ("chronic kidney disease", "malattia renale", "ckd")):
                ckd = True
        elif rtype in ("MedicationStatement", "MedicationRequest"):
            label = _med_text(resource)
            if "metformin" in label:
                oral_classes.append("metformin")
            if "sglt2" in label:
                oral_classes.append("sglt2i")
            if "glp-1" in label or "glp1" in label:
                oral_classes.append("glp1ra")
            if "dpp-4" in label or "dpp4" in label:
                oral_classes.append("dpp4i")
            if "pioglitazone" in label:
                oral_classes.append("pioglitazone")
            if "acarbose" in label:
                oral_classes.append("acarbose")
            if "sulfonylurea" in label:
                oral_classes.append("sulfonylurea")
            if "glinide" in label:
                oral_classes.append("glinide")
            if "basal insulin" in label or "insulina basale" in label:
                on_basal = True
            if "prandial insulin" in label or "insulina prandiale" in label or "rapid insulin" in label:
                on_prandial = True
            if "pump" in label or "microinfusore" in label:
                on_pump = True
        elif rtype == "QuestionnaireResponse":
            for item in resource.get("item", []):
                link_id = str(item.get("linkId", "")).lower()
                answer = item.get("answer", [])
                if not answer:
                    continue
                val = answer[0].get("valueBoolean")
                if link_id == "groupeducationfeasible":
                    group_education_feasible = bool(val)
                elif link_id == "structurededucationdone":
                    structured_education_done = bool(val)
                elif link_id == "structurednutritionprogram":
                    structured_nutrition = bool(val)
                elif link_id == "regularphysicalactivity":
                    regular_physical_activity = bool(val)

    on_basal_bolus = on_basal and on_prandial

    body = {
        "guidelineVersion": "SID-AMD-DM2-2022.12",
        "patient": {"patientId": patient_id},
        "facts": {
            "diabetesType": "type2",
            "labs": {"hbA1cPercent": hbA1c, "eGFRmlMin": egfr},
            "comorbidities": {
                "priorCardiovascularEvent": prior_cvd,
                "heartFailure": heart_failure,
                "chronicKidneyDisease": ckd or (egfr is not None and egfr < 60),
            },
            "currentTherapy": {
                "oralClasses": oral_classes,
                "insulin": {
                    "onBasal": on_basal,
                    "onPrandial": on_prandial,
                    "onBasalBolus": on_basal_bolus,
                    "onPump": on_pump,
                },
            },
            "lifestyle": {
                "structuredNutritionProgram": structured_nutrition,
                "regularPhysicalActivity": regular_physical_activity,
            },
            "education": {
                "structuredEducationDone": structured_education_done,
                "groupEducationFeasible": group_education_feasible,
            },
            "monitoring": {
                "smbgStructured": smbg_structured,
                "cgmAvailable": cgm_available,
                "recentGlucosePattern": {"hypoglycemiaEpisodes30d": hypoglycemia_episodes},
            },
            "provenance": {"fhirBundleId": bundle.get("id")},
        },
    }
    if hasattr(DecisionEvaluationRequest, 'model_validate'):
        return DecisionEvaluationRequest.model_validate(body)
    return DecisionEvaluationRequest.parse_obj(body)


def response_to_fhir_bundle(decision_response) -> Dict[str, Any]:
    request_orchestration_id = str(uuid4())
    bundle_id = str(uuid4())

    actions: List[Dict[str, Any]] = []
    entries: List[Dict[str, Any]] = []

    def _add_entry(resource: Dict[str, Any]) -> None:
        entries.append({"resource": resource})

    # Convert recommendations into actionable FHIR proposals.
    for idx, rec in enumerate(decision_response.recommendations, start=1):
        action_id = f"action-{idx}"
        actions.append(
            {
                "id": action_id,
                "title": rec.title,
                "description": rec.recommendationText,
                "code": [{"text": rec.code}],
                "extension": [
                    {
                        "url": "https://example.org/fhir/StructureDefinition/sid-amd-reference",
                        "valueString": rec.sidAmdReference,
                    }
                ],
            }
        )

        # Default: a Task proposal.
        _add_entry(
            {
                "resourceType": "Task",
                "id": str(uuid4()),
                "status": "requested",
                "intent": "proposal",
                "description": rec.recommendationText,
                "priority": "routine" if rec.priority in ("low", "medium") else "asap",
            }
        )

        # If the recommendation proposes a medication start/stop, add a draft MedicationRequest.
        for pa in getattr(rec, "proposedActions", []) or []:
            if pa.actionType in ("start-medication", "stop-medication"):
                payload = pa.payload or {}
                _add_entry(
                    {
                        "resourceType": "MedicationRequest",
                        "id": str(uuid4()),
                        "status": "draft",
                        "intent": "proposal",
                        "priority": "routine",
                        "medicationCodeableConcept": {
                            "text": ", ".join(payload.get("preferredClasses", []) or payload.get("avoidClasses", []) or [])
                            or "Medication class proposal",
                        },
                        "note": [
                            {
                                "text": f"SID/AMD ref {rec.sidAmdReference}. Requires clinician approval: {rec.requiresClinicianApproval}"
                            }
                        ],
                    }
                )
            if pa.actionType in ("refer", "educate"):
                payload = pa.payload or {}
                _add_entry(
                    {
                        "resourceType": "ServiceRequest",
                        "id": str(uuid4()),
                        "status": "draft",
                        "intent": "proposal",
                        "code": {"text": payload.get("service") or payload.get("mode") or "Support service"},
                        "note": [{"text": f"SID/AMD ref {rec.sidAmdReference}"}],
                    }
                )

        # Add patient-facing message as CommunicationRequest.
        _add_entry(
            {
                "resourceType": "CommunicationRequest",
                "id": str(uuid4()),
                "status": "draft",
                "payload": [{"contentString": rec.recommendationText}],
                "reasonCode": [{"text": rec.title}],
            }
        )

    # Missing data -> Tasks.
    for missing in decision_response.missingData:
        _add_entry(
            {
                "resourceType": "Task",
                "id": str(uuid4()),
                "status": "requested",
                "intent": "proposal",
                "description": f"Raccogliere dato mancante: {missing.field} ({missing.reason})",
                "priority": "asap" if missing.blocking else "routine",
            }
        )

    # Alerts -> DetectedIssue.
    for alert in decision_response.alerts:
        _add_entry(
            {
                "resourceType": "DetectedIssue",
                "id": str(uuid4()),
                "status": "final",
                "severity": "high" if alert.severity == "error" else "moderate",
                "detail": alert.message,
                "code": {"text": alert.code},
            }
        )

    # Overall status warnings.
    if decision_response.status in ("needs-more-data", "blocked"):
        _add_entry(
            {
                "resourceType": "OperationOutcome",
                "id": str(uuid4()),
                "issue": [
                    {
                        "severity": "error" if decision_response.status == "blocked" else "warning",
                        "code": "incomplete",
                        "diagnostics": f"Decision status: {decision_response.status}",
                    }
                ],
            }
        )

    request_orchestration = {
        "resourceType": "RequestOrchestration",
        "id": request_orchestration_id,
        "status": "draft",
        "intent": "proposal",
        "action": actions,
    }

    bundle = {
        "resourceType": "Bundle",
        "id": bundle_id,
        "type": "collection",
        "entry": [{"resource": request_orchestration}] + entries,
    }
    return bundle


def apply_plandefinition(payload: Dict[str, Any]) -> Dict[str, Any]:
    req = bundle_to_request(payload)
    result = evaluate_request(req)
    bundle = response_to_fhir_bundle(result)
    result.fhir.requestOrchestrationId = bundle["entry"][0]["resource"]["id"]
    result.fhir.bundleId = bundle["id"]
    return bundle
