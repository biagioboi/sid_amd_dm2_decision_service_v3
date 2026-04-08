import importlib

from fastapi.testclient import TestClient


def _client() -> TestClient:
    import app.main as main

    importlib.reload(main)
    return TestClient(main.app)


def _token(client: TestClient) -> str:
    resp = client.post(
        "/auth/token",
        data={"username": "admin", "password": "admin"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


def test_health():
    client = _client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_auth_required():
    client = _client()
    response = client.post("/v1/decision-evaluations", json={})
    assert response.status_code == 401


def test_json_api_standard_path_and_persistence():
    client = _client()
    tok = _token(client)
    headers = {"Authorization": f"Bearer {tok}"}

    payload = {
        "guidelineVersion": "SID-AMD-DM2-2022.12",
        "patient": {"patientId": "P001"},
        "facts": {
            "diabetesType": "type2",
            "labs": {"hbA1cPercent": 8.2, "eGFRmlMin": 72},
            "comorbidities": {
                "priorCardiovascularEvent": False,
                "heartFailure": False,
                "chronicKidneyDisease": False,
            },
            "currentTherapy": {
                "oralClasses": ["metformin"],
                "insulin": {
                    "onBasal": False,
                    "onPrandial": False,
                    "onBasalBolus": False,
                    "onPump": False,
                },
                "contraindications": {"metformin": False, "sglt2i": False, "glp1ra": False},
            },
            "lifestyle": {
                "structuredNutritionProgram": False,
                "followsMediterraneanPattern": False,
                "lowGlycemicIndexPattern": False,
                "regularPhysicalActivity": False,
                "minutesExercisePerWeek": 60,
            },
            "education": {"structuredEducationDone": False, "groupEducationFeasible": True},
            "monitoring": {
                "smbgStructured": False,
                "cgmAvailable": False,
                "recentGlucosePattern": {"hypoglycemiaEpisodes30d": 0, "hyperglycemiaEpisodes30d": 8},
            },
        },
    }
    response = client.post("/v1/decision-evaluations", json=payload, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("needs-clinician-review", "ready")
    assert any(r["sidAmdReference"] == "5.1" for r in body["recommendations"])

    saved = client.get(f"/v1/decision-evaluations/{body['evaluationId']}", headers=headers)
    assert saved.status_code == 200
    assert saved.json()["evaluationId"] == body["evaluationId"]

    audit = client.get(f"/v1/decision-evaluations/{body['evaluationId']}/audit", headers=headers)
    assert audit.status_code == 200
    assert audit.json()["events"][0]["eventType"] == "decision_evaluated"


def test_json_api_heart_failure_path():
    client = _client()
    tok = _token(client)
    headers = {"Authorization": f"Bearer {tok}"}

    payload = {
        "guidelineVersion": "SID-AMD-DM2-2022.12",
        "patient": {"patientId": "P002"},
        "facts": {
            "diabetesType": "type2",
            "labs": {"hbA1cPercent": 8.5, "eGFRmlMin": 48},
            "comorbidities": {
                "priorCardiovascularEvent": False,
                "heartFailure": True,
                "chronicKidneyDisease": True,
            },
            "currentTherapy": {
                "oralClasses": ["metformin"],
                "insulin": {
                    "onBasal": False,
                    "onPrandial": False,
                    "onBasalBolus": False,
                    "onPump": False,
                },
                "contraindications": {"metformin": False, "sglt2i": False, "glp1ra": False},
            },
            "education": {"structuredEducationDone": True, "groupEducationFeasible": False},
            "lifestyle": {
                "structuredNutritionProgram": True,
                "followsMediterraneanPattern": True,
                "lowGlycemicIndexPattern": True,
                "regularPhysicalActivity": True,
                "minutesExercisePerWeek": 150,
            },
            "monitoring": {
                "smbgStructured": True,
                "cgmAvailable": True,
                "recentGlucosePattern": {"hypoglycemiaEpisodes30d": 1, "hyperglycemiaEpisodes30d": 5},
            },
        },
    }
    response = client.post("/v1/decision-evaluations", json=payload, headers=headers)
    assert response.status_code == 200
    body = response.json()
    pharm = [r for r in body["recommendations"] if r["category"] == "pharmacologic"][0]
    assert pharm["sidAmdReference"] == "5.4"


def test_plandefinition_and_library_and_apply_fhir():
    client = _client()
    tok = _token(client)
    headers = {"Authorization": f"Bearer {tok}"}

    pd = client.get("/fhir/PlanDefinition/sid-amd-dm2-2022")
    assert pd.status_code == 200
    assert pd.json()["resourceType"] == "PlanDefinition"

    lib = client.get("/fhir/Library/sid-amd-dm2-logic")
    assert lib.status_code == 200
    assert "library" in lib.text.lower()

    # Minimal FHIR Parameters wrapper
    params = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "data",
                "resource": {
                    "resourceType": "Bundle",
                    "type": "collection",
                    "entry": [
                        {"resource": {"resourceType": "Patient", "id": "P003"}},
                        {
                            "resource": {
                                "resourceType": "Observation",
                                "code": {"text": "HbA1c"},
                                "valueQuantity": {"value": 8.0},
                            }
                        },
                        {
                            "resource": {
                                "resourceType": "Observation",
                                "code": {"text": "eGFR"},
                                "valueQuantity": {"value": 80},
                            }
                        },
                    ],
                },
            }
        ],
    }
    out = client.post("/fhir/PlanDefinition/sid-amd-dm2-2022/$apply", json=params, headers=headers)
    assert out.status_code == 200
    b = out.json()
    assert b["resourceType"] == "Bundle"
    assert b["entry"][0]["resource"]["resourceType"] == "RequestOrchestration"


def test_digital_twin_simulate_endpoint_with_personalization():
    client = _client()
    tok = _token(client)
    headers = {"Authorization": f"Bearer {tok}"}

    req = {
        "glucose0_mgdl": 180,
        "plan": {"correction_uI": 0.02, "dinner_cut_g": 10, "walk_minutes": 20, "parameterModifiers": {"rho_P": 1.1}},
        "facts": {
            "diabetesType": "type2",
            "labs": {"hbA1cPercent": 9.0, "bmi": 36, "eGFRmlMin": 50},
            "currentTherapy": {"oralClasses": ["metformin", "sglt2i"]},
        },
        "horizonMinutes": 24 * 60,
        "dtMinutes": 5,
    }
    out = client.post("/v1/digital-twin/simulate", json=req, headers=headers)
    assert out.status_code == 200
    body = out.json()
    assert body["status"] in ("pass", "warn", "fail")
    # Personalization layer should have produced at least one modifier beyond the explicit one.
    assert "kR" in body["plan"]["parameterModifiers"]


def test_digital_twin_simulate_advanced_scenario_endpoint():
    client = _client()
    tok = _token(client)
    headers = {"Authorization": f"Bearer {tok}"}

    req = {
        "profile": {
            "patientId": "P-TWIN-001",
            "baselineGlucoseMgdl": 152,
            "hbA1cPercent": 8.1,
            "bmi": 31,
            "oralClasses": ["metformin", "sglt2i"],
        },
        "historicalGlucose": [
            {"minuteOffset": 0, "glucoseMgdl": 148},
            {"minuteOffset": 15, "glucoseMgdl": 151},
            {"minuteOffset": 30, "glucoseMgdl": 154},
            {"minuteOffset": 45, "glucoseMgdl": 150},
        ],
        "scenario": {
            "name": "post-breakfast-walk",
            "horizonMinutes": 12 * 60,
            "dtMinutes": 15,
            "events": [
                {"eventType": "meal", "startMinutes": 30, "mealCarbsGrams": 55, "glycemicIndex": 95, "label": "breakfast"},
                {"eventType": "exercise", "startMinutes": 120, "durationMinutes": 45, "intensity": 1.2, "label": "walk"},
                {"eventType": "insulin-bolus", "startMinutes": 30, "insulinUnits": 2.0, "label": "correction"},
            ],
        },
    }

    out = client.post("/v1/digital-twin/simulate-advanced", json=req, headers=headers)
    assert out.status_code == 200
    body = out.json()
    assert body["dataset"]["datasetId"] == "Shanghai_T2DM"
    assert body["status"] in ("pass", "warn", "fail")
    assert body["metrics"]["horizonMinutes"] == 12 * 60
    assert len(body["trajectory"]) > 10
