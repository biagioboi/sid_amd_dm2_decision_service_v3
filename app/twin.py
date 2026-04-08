from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence

import numpy as np

from .models import (
    TwinCalibrationMetrics,
    FactsModel,
    TwinPatientProfile,
    TwinScenario,
    TwinScenarioMetrics,
    TwinScenarioSimulationRequest,
    TwinScenarioSimulationResponse,
    TwinTrajectoryPoint,
)


@dataclass
class _ResolvedProfile:
    baseline_glucose: float
    equilibrium_glucose: float
    carb_sensitivity_mgdl_per_g: float
    insulin_sensitivity_mgdl_per_unit: float
    gastric_absorption_minutes: int
    insulin_action_minutes: int
    exercise_sensitivity_boost: float
    hepatic_release_mgdl_per_hour: float


def _facts_get(facts: FactsModel | dict | None, path: str, default=None):
    current = facts
    for part in path.split("."):
        if current is None:
            return default
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
    return current if current is not None else default


def _normalised_kernel(length: int, peak_fraction: float) -> np.ndarray:
    if length <= 1:
        return np.ones((1,), dtype=float)
    x = np.linspace(0.0, 1.0, length)
    peak = max(peak_fraction, 1e-3)
    weights = (x / peak) * np.exp(1.0 - (x / peak))
    weights = np.clip(weights, 0.0, None)
    total = float(weights.sum())
    if total <= 0:
        return np.full((length,), 1.0 / length, dtype=float)
    return weights / total


def _event_window(start_minutes: int, duration_minutes: int, dt_minutes: int, steps: int, tail_minutes: int = 0) -> range:
    start_idx = max(int(start_minutes / dt_minutes), 0)
    span_minutes = max(duration_minutes + tail_minutes, dt_minutes)
    end_idx = min(steps, start_idx + int(np.ceil(span_minutes / dt_minutes)))
    return range(start_idx, end_idx)


def _estimate_profile(
    profile: TwinPatientProfile,
    facts: FactsModel | None,
    historical_glucose: Sequence,
) -> tuple[_ResolvedProfile, TwinPatientProfile, List[str]]:
    assumptions: List[str] = [
        "Initial twin parametrization is heuristic and intended for scenario exploration, not autonomous therapy dosing.",
        "Shanghai T2DM dataset assumptions follow the published cohort characteristics: 100 patients, 15-minute CGM, 3-14 day recordings.",
    ]

    oral_classes = {str(item).lower() for item in (profile.oralClasses or [])}
    if facts is not None:
        facts_oral = _facts_get(facts, "currentTherapy.oralClasses", []) or []
        oral_classes.update(str(item).lower() for item in facts_oral)

    hbA1c = profile.hbA1cPercent
    if hbA1c is None:
        hbA1c = _facts_get(facts, "labs.hbA1cPercent")

    bmi = profile.bmi
    if bmi is None:
        bmi = _facts_get(facts, "labs.bmi")

    egfr = profile.eGFRmlMin
    if egfr is None:
        egfr = _facts_get(facts, "labs.eGFRmlMin")

    baseline_candidates = [profile.baselineGlucoseMgdl]
    if historical_glucose:
        trailing = [sample.glucoseMgdl for sample in historical_glucose[-12:]]
        baseline_candidates.append(float(np.mean(trailing)))
        assumptions.append("Baseline glucose was aligned to the recent historical CGM/SMBG mean when available.")
    baseline_candidates.append(_facts_get(facts, "monitoring.recentGlucosePattern.fastingMean"))
    baseline_candidates.append(_facts_get(facts, "monitoring.recentGlucosePattern.postPrandialMean"))
    baseline = next((float(value) for value in baseline_candidates if value is not None), 140.0)

    insulin_sensitivity = profile.insulinSensitivityMgdlPerUnit
    if insulin_sensitivity is None:
        insulin_sensitivity = 35.0
        if bmi is not None:
            insulin_sensitivity *= max(0.55, 1.15 - (float(bmi) - 25.0) * 0.015)
        if hbA1c is not None and float(hbA1c) >= 8.5:
            insulin_sensitivity *= 0.9
        assumptions.append("Insulin sensitivity was estimated from BMI/HbA1c because no patient-specific ISF was supplied.")

    carb_sensitivity = None
    if profile.carbRatioGramsPerUnit and insulin_sensitivity:
        carb_sensitivity = float(insulin_sensitivity) / float(profile.carbRatioGramsPerUnit)
    if carb_sensitivity is None:
        carb_sensitivity = 3.2
        if bmi is not None:
            carb_sensitivity *= min(1.25, 0.95 + max(float(bmi) - 25.0, 0.0) * 0.01)
        if "glp1ra" in oral_classes:
            carb_sensitivity *= 0.9
        assumptions.append("Carbohydrate impact was estimated from cohort-level assumptions because no carb ratio was supplied.")

    gastric_absorption = int(profile.gastricAbsorptionMinutes or 180)
    if "glp1ra" in oral_classes:
        gastric_absorption = int(round(gastric_absorption * 1.2))

    hepatic_release = float(profile.hepaticGlucoseReleaseMgdlPerHour)
    if hbA1c is not None and float(hbA1c) >= 8.0:
        hepatic_release += 1.5
    if "metformin" in oral_classes:
        hepatic_release *= 0.88
    if "sglt2i" in oral_classes:
        hepatic_release *= 0.95

    equilibrium = max(95.0, min(160.0, baseline - 12.0))
    if egfr is not None and float(egfr) < 60:
        equilibrium += 4.0

    modifiers = dict(profile.parameterModifiers or {})
    carb_sensitivity *= float(modifiers.get("carbSensitivityMultiplier", 1.0))
    insulin_sensitivity *= float(modifiers.get("insulinSensitivityMultiplier", 1.0))
    hepatic_release *= float(modifiers.get("hepaticReleaseMultiplier", 1.0))
    gastric_absorption = int(round(gastric_absorption * float(modifiers.get("gastricAbsorptionMultiplier", 1.0))))
    equilibrium += float(modifiers.get("equilibriumGlucoseOffset", 0.0))

    resolved = _ResolvedProfile(
        baseline_glucose=float(baseline),
        equilibrium_glucose=float(equilibrium),
        carb_sensitivity_mgdl_per_g=float(carb_sensitivity),
        insulin_sensitivity_mgdl_per_unit=float(insulin_sensitivity),
        gastric_absorption_minutes=gastric_absorption,
        insulin_action_minutes=int(profile.insulinActionMinutes or 300),
        exercise_sensitivity_boost=float(profile.exerciseSensitivityBoost),
        hepatic_release_mgdl_per_hour=hepatic_release,
    )

    resolved_profile = profile.copy(
        update={
            "hbA1cPercent": hbA1c,
            "bmi": bmi,
            "eGFRmlMin": egfr,
            "baselineGlucoseMgdl": resolved.baseline_glucose,
            "oralClasses": sorted(oral_classes),
        }
    )
    return resolved, resolved_profile, assumptions


def _simulate_values(
    profile_model: TwinPatientProfile,
    scenario: TwinScenario,
    facts: FactsModel | None,
    historical_glucose: Sequence,
) -> tuple[np.ndarray, Dict[str, np.ndarray], _ResolvedProfile, TwinPatientProfile, List[str]]:
    resolved_profile, profile_model, assumptions = _estimate_profile(
        profile_model,
        facts,
        historical_glucose,
    )
    dt = int(scenario.dtMinutes)
    steps = int(np.ceil(scenario.horizonMinutes / dt)) + 1
    traces = _build_effect_traces(resolved_profile, scenario)

    glucose = np.zeros((steps,), dtype=float)
    initial = float(scenario.startingGlucoseMgdl or resolved_profile.baseline_glucose)
    glucose[0] = initial

    equilibrium = resolved_profile.equilibrium_glucose
    homeostatic_time_constant = 240.0

    for idx in range(1, steps):
        pull = ((equilibrium - glucose[idx - 1]) / homeostatic_time_constant) * dt
        net_external = (
            traces["carbs"][idx]
            - traces["insulin"][idx]
            - traces["exercise"][idx]
            + traces["hepatic"][idx]
        )
        glucose[idx] = max(40.0, glucose[idx - 1] + pull + net_external)

    return glucose, traces, resolved_profile, profile_model, assumptions


def _build_effect_traces(
    profile: _ResolvedProfile,
    scenario: TwinScenario,
) -> Dict[str, np.ndarray]:
    dt = int(scenario.dtMinutes)
    steps = int(np.ceil(scenario.horizonMinutes / dt)) + 1
    traces = {
        "carbs": np.zeros((steps,), dtype=float),
        "insulin": np.zeros((steps,), dtype=float),
        "exercise": np.zeros((steps,), dtype=float),
        "hepatic": np.zeros((steps,), dtype=float),
    }

    base_hepatic_per_step = (profile.hepatic_release_mgdl_per_hour / 60.0) * dt
    traces["hepatic"] += base_hepatic_per_step

    medication_offsets: Dict[int, float] = {}

    for event in sorted(scenario.events, key=lambda item: item.startMinutes):
        if event.eventType == "meal" and event.mealCarbsGrams:
            window = list(
                _event_window(
                    event.startMinutes,
                    max(event.durationMinutes, profile.gastric_absorption_minutes),
                    dt,
                    steps,
                    tail_minutes=int(profile.gastric_absorption_minutes * 0.5),
                )
            )
            weights = _normalised_kernel(len(window), peak_fraction=0.35)
            gi_factor = max(0.7, min(event.glycemicIndex / 100.0, 1.4))
            total_rise = float(event.mealCarbsGrams) * profile.carb_sensitivity_mgdl_per_g * gi_factor
            traces["carbs"][window] += total_rise * weights

        elif event.eventType == "insulin-bolus" and event.insulinUnits:
            window = list(
                _event_window(
                    event.startMinutes,
                    profile.insulin_action_minutes,
                    dt,
                    steps,
                    tail_minutes=int(profile.insulin_action_minutes * 0.5),
                )
            )
            weights = _normalised_kernel(len(window), peak_fraction=0.45)
            total_drop = float(event.insulinUnits) * profile.insulin_sensitivity_mgdl_per_unit
            traces["insulin"][window] += total_drop * weights

        elif event.eventType == "insulin-basal" and event.insulinUnits:
            duration = max(event.durationMinutes, 12 * 60)
            window = list(
                _event_window(
                    event.startMinutes,
                    duration,
                    dt,
                    steps,
                    tail_minutes=int(profile.insulin_action_minutes * 0.5),
                )
            )
            weights = _normalised_kernel(len(window), peak_fraction=0.65)
            total_drop = float(event.insulinUnits) * profile.insulin_sensitivity_mgdl_per_unit * 0.65
            traces["insulin"][window] += total_drop * weights

        elif event.eventType == "exercise":
            duration = max(event.durationMinutes, dt)
            window = list(
                _event_window(
                    event.startMinutes,
                    duration,
                    dt,
                    steps,
                    tail_minutes=int(duration * 2),
                )
            )
            weights = _normalised_kernel(len(window), peak_fraction=0.55)
            total_drop = 12.0 * float(event.intensity) * profile.exercise_sensitivity_boost * max(duration / 30.0, 0.5)
            traces["exercise"][window] += total_drop * weights

        elif event.eventType == "stress":
            duration = max(event.durationMinutes, dt)
            window = list(_event_window(event.startMinutes, duration, dt, steps))
            weights = _normalised_kernel(len(window), peak_fraction=0.5)
            total_rise = 10.0 * max(float(event.stressLoad), 0.5)
            traces["hepatic"][window] += total_rise * weights

        elif event.eventType == "medication" and event.medicationClass:
            med = str(event.medicationClass).lower()
            offset = 0.0
            if med == "metformin":
                offset = -1.5
            elif med == "sglt2i":
                offset = -2.0
            elif med == "glp1ra":
                offset = -1.0
            if offset != 0.0:
                medication_offsets[int(event.startMinutes / dt)] = medication_offsets.get(int(event.startMinutes / dt), 0.0) + offset

    running_offset = 0.0
    for idx in range(steps):
        running_offset += medication_offsets.get(idx, 0.0)
        traces["hepatic"][idx] += running_offset

    return traces


def calibrate_profile_to_history(
    profile: TwinPatientProfile,
    scenario: TwinScenario,
    historical_glucose: Sequence,
    facts: FactsModel | None = None,
) -> tuple[TwinPatientProfile, TwinCalibrationMetrics, List[str]]:
    if not historical_glucose:
        metrics = TwinCalibrationMetrics(
            observationCount=0,
            rmseMgdl=0.0,
            maeMgdl=0.0,
            bestParameters={},
        )
        return profile, metrics, ["Calibration skipped because no historical glucose samples were provided."]

    observations = sorted(historical_glucose, key=lambda item: item.minuteOffset)
    obs_minutes = [int(item.minuteOffset) for item in observations]
    obs_values = np.array([float(item.glucoseMgdl) for item in observations], dtype=float)
    dt = int(scenario.dtMinutes)

    base_modifiers = dict(profile.parameterModifiers or {})
    first_glucose = float(obs_values[0])
    best_score = None
    best_profile = profile
    best_pred = None
    best_params: Dict[str, float] = {}

    carb_space = [0.75, 0.9, 1.0, 1.1, 1.25]
    insulin_space = [0.75, 0.9, 1.0, 1.1, 1.25]
    hepatic_space = [0.85, 1.0, 1.15]
    equilibrium_space = [-12.0, -6.0, 0.0, 6.0, 12.0]

    for carb_mult in carb_space:
        for insulin_mult in insulin_space:
            for hepatic_mult in hepatic_space:
                for eq_shift in equilibrium_space:
                    modifiers = {
                        **base_modifiers,
                        "carbSensitivityMultiplier": carb_mult,
                        "insulinSensitivityMultiplier": insulin_mult,
                        "hepaticReleaseMultiplier": hepatic_mult,
                        "equilibriumGlucoseOffset": eq_shift,
                    }
                    candidate_profile = profile.copy(
                        update={
                            "baselineGlucoseMgdl": first_glucose,
                            "parameterModifiers": modifiers,
                        }
                    )
                    glucose, _, _, _, _ = _simulate_values(
                        candidate_profile,
                        scenario,
                        facts,
                        observations,
                    )

                    preds = []
                    actuals = []
                    for minute, actual in zip(obs_minutes, obs_values):
                        idx = min(int(round(minute / dt)), len(glucose) - 1)
                        preds.append(float(glucose[idx]))
                        actuals.append(float(actual))

                    errors = np.array(preds, dtype=float) - np.array(actuals, dtype=float)
                    score = float(np.mean(errors ** 2))
                    if best_score is None or score < best_score:
                        best_score = score
                        best_profile = candidate_profile
                        best_pred = np.array(preds, dtype=float)
                        best_params = dict(modifiers)

    if best_pred is None:
        best_pred = np.array([first_glucose for _ in obs_values], dtype=float)
        best_score = float(np.mean((best_pred - obs_values) ** 2))

    metrics = TwinCalibrationMetrics(
        observationCount=len(obs_values),
        rmseMgdl=float(np.sqrt(best_score)),
        maeMgdl=float(np.mean(np.abs(best_pred - obs_values))),
        bestParameters=best_params,
    )
    assumptions = [
        "Calibration used a small grid search over interpretable twin parameters against the available CGM history.",
        "The calibrated profile is cohort-informed and patient-specific, but still exploratory rather than clinically validated.",
    ]
    return best_profile, metrics, assumptions


def simulate_scenario(request: TwinScenarioSimulationRequest) -> TwinScenarioSimulationResponse:
    scenario = request.scenario
    dt = int(scenario.dtMinutes)
    glucose, traces, _, profile_model, assumptions = _simulate_values(
        request.profile,
        scenario,
        request.facts,
        request.historicalGlucose,
    )
    steps = len(glucose)

    times = np.arange(steps, dtype=int) * dt
    deltas = np.diff(glucose, prepend=glucose[0])

    time_in_range = float(np.mean((glucose >= 70) & (glucose <= 180)))
    time_below = float(np.mean(glucose < 70))
    time_above_180 = float(np.mean(glucose > 180))
    time_above_250 = float(np.mean(glucose > 250))

    if float(np.min(glucose)) < 65.0 or time_above_250 > 0.20:
        status = "fail"
        reason = "Predicted unsafe extremes with relevant risk of hypoglycemia or sustained severe hyperglycemia."
    elif float(np.min(glucose)) < 70.0 or float(np.max(glucose)) > 250.0 or time_in_range < 0.60:
        status = "warn"
        reason = "Predicted suboptimal glycemic control for the configured scenario."
    else:
        status = "pass"
        reason = "Scenario remains largely within the configured target range."

    metrics = TwinScenarioMetrics(
        horizonMinutes=int(scenario.horizonMinutes),
        dtMinutes=dt,
        initialGlucose=float(glucose[0]),
        finalGlucose=float(glucose[-1]),
        minGlucose=float(np.min(glucose)),
        maxGlucose=float(np.max(glucose)),
        meanGlucose=float(np.mean(glucose)),
        timeInRange70_180=time_in_range,
        timeBelow70=time_below,
        timeAbove180=time_above_180,
        timeAbove250=time_above_250,
    )

    trajectory = [
        TwinTrajectoryPoint(
            minute=int(times[idx]),
            glucoseMgdl=float(glucose[idx]),
            deltaMgdl=float(deltas[idx]),
            carbsEffectMgdl=float(traces["carbs"][idx]),
            insulinEffectMgdl=float(traces["insulin"][idx]),
            exerciseEffectMgdl=float(traces["exercise"][idx]),
            hepaticEffectMgdl=float(traces["hepatic"][idx]),
        )
        for idx in range(steps)
    ]

    return TwinScenarioSimulationResponse(
        status=status,
        reason=reason,
        dataset=request.dataset,
        profile=profile_model,
        metrics=metrics,
        assumptions=assumptions,
        trajectory=trajectory,
    )
