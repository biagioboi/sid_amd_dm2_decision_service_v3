
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
from uuid import uuid4

from .dmn_runtime import evaluate_table
from .digital_twin import derive_parameter_modifiers_from_facts, evaluate_plan
from .models import (
    Alert,
    AppliedRule,
    AuditInfo,
    DecisionEvaluationRequest,
    DecisionEvaluationResponse,
    DigitalTwinEvaluation,
    DigitalTwinMetrics,
    DigitalTwinPlan,
    FhirInfo,
    MissingData,
    ProposedAction,
    Recommendation,
    Summary,
)

DECISION_MODEL_VERSION = "1.0.0"


def _flatten_request(req: DecisionEvaluationRequest) -> Dict[str, Any]:
    oral = [entry.lower() for entry in req.facts.currentTherapy.oralClasses]
    insulin = req.facts.currentTherapy.insulin
    contraindications = req.facts.currentTherapy.contraindications
    labs = req.facts.labs
    comorbidities = req.facts.comorbidities
    lifestyle = req.facts.lifestyle
    education = req.facts.education
    monitoring = req.facts.monitoring
    glucose = monitoring.recentGlucosePattern

    on_hypo_therapy = (
        ("sulfonylurea" in oral)
        or ("glinide" in oral)
        or bool(insulin.onBasal)
        or bool(insulin.onPrandial)
    )

    return {
        "diabetesType": req.facts.diabetesType,
        "hbA1cPresent": labs.hbA1cPercent is not None,
        "eGFRPresent": labs.eGFRmlMin is not None,
        "therapyPresent": bool(oral or insulin.onBasal or insulin.onPrandial or insulin.onPump),
        "comorbidityAssessmentPresent": comorbidities.priorCardiovascularEvent is not None and comorbidities.heartFailure is not None,
        "labs": {
            "hbA1cPercent": labs.hbA1cPercent,
            "eGFRmlMin": labs.eGFRmlMin,
        },
        "goals": {
            "highHypoglycemiaRisk": req.facts.goals.highHypoglycemiaRisk,
            "individualizedTighterGoalRequested": (
                req.facts.goals.currentHbA1cTargetPercent is not None
                and req.facts.goals.currentHbA1cTargetPercent <= 6.5
            ),
        },
        "onHypoglycemiaInducingTherapy": on_hypo_therapy,
        "lifestyle": {
            "structuredNutritionProgram": bool(lifestyle.structuredNutritionProgram),
            "followsMediterraneanPattern": bool(lifestyle.followsMediterraneanPattern),
            "lowGlycemicIndexPattern": bool(lifestyle.lowGlycemicIndexPattern),
            "regularPhysicalActivity": bool(lifestyle.regularPhysicalActivity),
            "minutesExercisePerWeek": int(lifestyle.minutesExercisePerWeek or 0),
        },
        "education": {
            "structuredEducationDone": bool(education.structuredEducationDone),
            "groupEducationFeasible": bool(education.groupEducationFeasible),
        },
        "comorbidities": {
            "priorCardiovascularEvent": bool(comorbidities.priorCardiovascularEvent),
            "heartFailure": bool(comorbidities.heartFailure),
            "chronicKidneyDisease": bool(comorbidities.chronicKidneyDisease),
            "severeHypoglycemiaHistory": bool(comorbidities.severeHypoglycemiaHistory),
        },
        "monitoring": {
            "smbgStructured": bool(monitoring.smbgStructured),
            "cgmAvailable": bool(monitoring.cgmAvailable),
            "onBasalBolus": bool(insulin.onBasalBolus),
            "hypoglycemiaEpisodes30d": int(glucose.hypoglycemiaEpisodes30d or 0),
            "hyperglycemiaEpisodes30d": int(glucose.hyperglycemiaEpisodes30d or 0),
        },
        "therapy": {
            "oralClasses": oral,
            "onBasal": bool(insulin.onBasal),
            "basalType": insulin.basalType,
            "onPrandial": bool(insulin.onPrandial),
            "onPump": bool(insulin.onPump),
            "metforminContraindicated": bool(contraindications.metformin),
            "sglt2iContraindicated": bool(contraindications.sglt2i),
            "glp1raContraindicated": bool(contraindications.glp1ra),
        },
    }


def _applied_rules(matched_rules: List[Dict[str, Any]], default_sid: str = "") -> List[AppliedRule]:
    out: List[AppliedRule] = []
    for rule in matched_rules:
        then = rule.get("then", {})
        sid = then.get("sidRef", default_sid)
        out.append(
            AppliedRule(
                ruleId=rule.get("id", "rule"),
                sidAmdReference=sid,
                outcome=then.get("outcome", then.get("phenotype", then.get("recommendation", sid))),
            )
        )
    return out


def _missing_data(flat: Dict[str, Any]) -> Tuple[List[MissingData], List[AppliedRule]]:
    outputs, rules, _ = evaluate_table("DT-DataCompleteness", flat)
    missing: List[MissingData] = []
    for output in outputs:
        if output.get("field"):
            missing.append(
                MissingData(
                    field=output["field"],
                    reason=output.get("reason", "Missing required field"),
                    blocking=bool(output.get("blocking", True)),
                )
            )
    return missing, _applied_rules(rules, "")


def _target_recommendations(flat: Dict[str, Any]) -> Tuple[List[Recommendation], List[AppliedRule], float]:
    outputs, rules, _ = evaluate_table("DT-GlycemicTarget", flat)
    if not outputs:
        return [], [], 7.0
    out = outputs[0]
    upper = float(out.get("targetHbA1cMax", 7.0))
    lower = out.get("targetHbA1cMin")
    # Some decision runtimes may return singletons as one-element lists.
    if isinstance(lower, list) and lower:
        lower = lower[0]
    hbA1c = flat["labs"]["hbA1cPercent"]
    recs: List[Recommendation] = []
    if hbA1c is not None:
        target_text = (
            f"Target HbA1c {lower:.1f}%–{upper:.1f}%"
            if lower is not None
            else f"Target HbA1c <{upper:.1f}%"
        )
        if hbA1c > upper:
            recs.append(
                Recommendation(
                    code="TARGET-OUT-OF-RANGE",
                    title="Rivaluta controllo glicemico",
                    category="target",
                    priority="high",
                    sidAmdReference=out["sidRef"],
                    recommendationText=f"HbA1c {hbA1c:.1f}% sopra il target. {target_text}.",
                    rationale="Il target glicemico dipende dal rischio di ipoglicemia e va individualizzato.",
                    proposedActions=[
                        ProposedAction(
                            actionType="review",
                            payload={"reason": "HbA1c above target", "target": target_text},
                        )
                    ],
                )
            )
        else:
            recs.append(
                Recommendation(
                    code="TARGET-AT-GOAL",
                    title="Mantieni target glicemico",
                    category="target",
                    priority="low",
                    sidAmdReference=out["sidRef"],
                    recommendationText=f"HbA1c {hbA1c:.1f}% in linea con il target. {target_text}.",
                    rationale="Il controllo attuale è compatibile con il profilo di rischio inserito.",
                    proposedActions=[
                        ProposedAction(actionType="continue-medication", payload={"reason": "At goal"})
                    ],
                )
            )
    return recs, _applied_rules(rules, out["sidRef"]), upper


def _lifestyle_recommendations(flat: Dict[str, Any]) -> Tuple[List[Recommendation], List[AppliedRule]]:
    recs: List[Recommendation] = []
    applied: List[AppliedRule] = []

    outputs, rules, _ = evaluate_table("DT-EducationPlan", flat)
    if outputs:
        out = outputs[0]
        if out.get("recommendStructuredEducation"):
            mode = out.get("educationMode", "individual")
            recs.append(
                Recommendation(
                    code="EDUCATION-STRUCTURED",
                    title="Attiva educazione terapeutica strutturata",
                    category="education",
                    priority="high",
                    sidAmdReference=out["sidRef"],
                    recommendationText=f"Proporre educazione terapeutica strutturata in modalità {mode}.",
                    rationale="La linea guida suggerisce educazione strutturata; se fattibile, preferire il gruppo.",
                    proposedActions=[
                        ProposedAction(actionType="educate", payload={"mode": mode})
                    ],
                )
            )
        applied.extend(_applied_rules(rules, out.get("sidRef", "4.1")))

    lifestyle = flat["lifestyle"]
    if not lifestyle["structuredNutritionProgram"]:
        recs.append(
            Recommendation(
                code="LIFESTYLE-NUTRITION",
                title="Attiva terapia nutrizionale strutturata",
                category="lifestyle",
                priority="medium",
                sidAmdReference="2.1",
                recommendationText="Attivare un percorso nutrizionale strutturato.",
                rationale="La terapia nutrizionale è parte integrante della gestione del DM2.",
                proposedActions=[ProposedAction(actionType="refer", payload={"service": "dietistica"})],
            )
        )
        applied.append(AppliedRule(ruleId="SIDAMD-2.1", sidAmdReference="2.1", outcome="structured nutrition"))
    if not lifestyle["followsMediterraneanPattern"]:
        recs.append(
            Recommendation(
                code="LIFESTYLE-MEDITERRANEAN",
                title="Favorisci pattern mediterraneo",
                category="lifestyle",
                priority="medium",
                sidAmdReference="2.2",
                recommendationText="Promuovere un pattern alimentare mediterraneo/bilanciato.",
                rationale="La linea guida suggerisce una dieta bilanciata e di tipo mediterraneo.",
                proposedActions=[ProposedAction(actionType="educate", payload={"topic": "mediterranean-diet"})],
            )
        )
        applied.append(AppliedRule(ruleId="SIDAMD-2.2", sidAmdReference="2.2", outcome="mediterranean pattern"))
    if not lifestyle["lowGlycemicIndexPattern"]:
        recs.append(
            Recommendation(
                code="LIFESTYLE-LOW-GI",
                title="Preferisci alimenti a basso indice glicemico",
                category="lifestyle",
                priority="low",
                sidAmdReference="2.3",
                recommendationText="Favorire alimenti a basso indice/carico glicemico.",
                rationale="È suggerita una prevalenza di alimenti a basso indice glicemico.",
                proposedActions=[ProposedAction(actionType="educate", payload={"topic": "low-glycemic-index"})],
            )
        )
        applied.append(AppliedRule(ruleId="SIDAMD-2.3", sidAmdReference="2.3", outcome="low GI"))
    if not lifestyle["regularPhysicalActivity"]:
        recs.append(
            Recommendation(
                code="LIFESTYLE-EXERCISE",
                title="Incrementa attività fisica regolare",
                category="lifestyle",
                priority="medium",
                sidAmdReference="3.1",
                recommendationText="Attivare un programma di esercizio fisico regolare e continuativo.",
                rationale="L'esercizio fisico regolare è suggerito nel DM2.",
                proposedActions=[
                    ProposedAction(
                        actionType="educate",
                        payload={"topic": "exercise", "minutesPerWeek": max(150, lifestyle["minutesExercisePerWeek"])},
                    )
                ],
            )
        )
        applied.append(AppliedRule(ruleId="SIDAMD-3.1", sidAmdReference="3.1", outcome="exercise"))
    return recs, applied


def _filter_classes(classes: List[str], flat: Dict[str, Any]) -> List[str]:
    filtered: List[str] = []
    for item in classes:
        key = f"{item}Contraindicated"
        if flat["therapy"].get(key, False):
            continue
        filtered.append(item)
    return filtered


def _therapy_recommendations(flat: Dict[str, Any], target_upper: float) -> Tuple[List[Recommendation], List[AppliedRule], str, List[Alert]]:
    recs: List[Recommendation] = []
    alerts: List[Alert] = []

    outputs, phenotype_rules, _ = evaluate_table("DT-PhenotypeClassification", flat)
    phenotype_out = outputs[0] if outputs else {"phenotype": "STANDARD", "sidRef": "5.1"}
    phenotype = phenotype_out["phenotype"]

    # Feed phenotype + full facts so contraindications can be considered by DMN tables
    dmn_facts = {**flat, "phenotype": phenotype}
    p_outputs, pharm_rules, _ = evaluate_table("DT-PharmacologicPath", dmn_facts)
    pharm = p_outputs[0] if p_outputs else {
        "firstLineClasses": ["metformin"],
        "secondLineClasses": ["sglt2i", "glp1ra"],
        "thirdLineClasses": ["dpp4i", "acarbose", "pioglitazone", "insulin"],
        "avoidClasses": ["sulfonylurea", "glinide"],
        "sidRef": "5.1",
    }

    first_line = _filter_classes(pharm.get("firstLineClasses", []), flat)
    second_line = _filter_classes(pharm.get("secondLineClasses", []), flat)
    third_line = _filter_classes(pharm.get("thirdLineClasses", []), flat)
    avoided = pharm.get("avoidClasses", [])

    if not first_line:
        alerts.append(Alert(code="NO-FIRST-LINE-AVAILABLE", severity="warning", message="Nessuna classe di prima scelta disponibile dopo aver applicato le controindicazioni."))

    hbA1c = flat["labs"]["hbA1cPercent"]
    if hbA1c is not None and hbA1c > target_upper:
        recs.append(
            Recommendation(
                code="THERAPY-INTENSIFY",
                title="Valuta intensificazione farmacologica",
                category="pharmacologic",
                priority="high",
                sidAmdReference=pharm["sidRef"],
                recommendationText=(
                    f"Per il fenotipo {phenotype}, considerare prima scelta: {', '.join(first_line) or 'nessuna disponibile'}; "
                    f"seconda scelta: {', '.join(second_line) or 'nessuna'}."
                ),
                rationale="Il percorso farmacologico va scelto in base a scompenso cardiaco, eventi CV e funzione renale.",
                proposedActions=[
                    ProposedAction(
                        actionType="start-medication",
                        payload={"preferredClasses": first_line, "alternativeClasses": second_line, "avoidClasses": avoided},
                    )
                ],
            )
        )
    else:
        recs.append(
            Recommendation(
                code="THERAPY-MAINTAIN-PATH",
                title="Mantieni il percorso farmacologico raccomandato",
                category="pharmacologic",
                priority="medium",
                sidAmdReference=pharm["sidRef"],
                recommendationText=(
                    f"Fenotipo {phenotype}: mantenere/allineare terapia con classi di prima scelta {', '.join(first_line) or 'nessuna disponibile'}."
                ),
                rationale="Anche a target, il fenotipo clinico guida le classi preferenziali.",
                proposedActions=[
                    ProposedAction(
                        actionType="review",
                        payload={"preferredClasses": first_line, "alternatives": second_line, "thirdLine": third_line},
                    )
                ],
            )
        )

    insulin = flat["therapy"]
    if insulin["onBasal"]:
        recs.append(
            Recommendation(
                code="INSULIN-BASAL",
                title="Preferisci analogo basale a lunga durata",
                category="insulin",
                priority="medium",
                sidAmdReference="5.5-5.6",
                recommendationText="Se è necessaria insulina basale, preferire analoghi basali (specie a durata maggiore) rispetto a NPH.",
                rationale="La linea guida suggerisce analoghi lenti e, se appropriato, quelli a maggiore durata.",
                proposedActions=[ProposedAction(actionType="review", payload={"preferredBasal": "basalAnalogLonger"})],
            )
        )
    if insulin["onPrandial"]:
        recs.append(
            Recommendation(
                code="INSULIN-PRANDIAL",
                title="Preferisci analogo rapido",
                category="insulin",
                priority="medium",
                sidAmdReference="5.7",
                recommendationText="Se è necessaria insulina prandiale, preferire un analogo rapido rispetto all'insulina regolare.",
                rationale="La linea guida suggerisce analoghi rapidi nei pazienti che necessitano di prandiale.",
                proposedActions=[ProposedAction(actionType="review", payload={"preferredPrandial": "rapidAnalog"})],
            )
        )
    if insulin["onPump"]:
        recs.append(
            Recommendation(
                code="INSULIN-PUMP-POLICY",
                title="Non usare routinariamente microinfusore nel DM2",
                category="insulin",
                priority="low",
                sidAmdReference="5.8",
                recommendationText="L'uso routinario del microinfusore nel DM2 non è raccomandato.",
                rationale="La linea guida non raccomanda l'uso routinario del microinfusore nel diabete tipo 2.",
                proposedActions=[ProposedAction(actionType="review", payload={"device": "pump"})],
            )
        )

    applied = _applied_rules(phenotype_rules, phenotype_out["sidRef"]) + _applied_rules(pharm_rules, pharm["sidRef"])
    return recs, applied, phenotype, alerts


def _monitoring_recommendations(flat: Dict[str, Any]) -> Tuple[List[Recommendation], List[AppliedRule], str]:
    outputs, rules, _ = evaluate_table("DT-MonitoringPlan", flat)
    recs: List[Recommendation] = []
    monitoring_path = "standard"
    if outputs:
        out = outputs[0]
        monitoring_path = out.get("preferredMonitoringMode", "structured-capillary")
        rec_text = out.get("recommendationText") or out.get("text") or (
            f"Impostare monitoraggio: {monitoring_path}."
        )
        recs.append(
            Recommendation(
                code="MONITORING-PLAN",
                title="Definisci piano di monitoraggio glicemico",
                category="monitoring",
                priority="medium",
                sidAmdReference=out["sidRef"],
                recommendationText=rec_text,
                rationale="Nel DM2 è suggerito il monitoraggio capillare strutturato; in basal-bolus non c'è preferenza tra CGM e capillare.",
                proposedActions=[
                    ProposedAction(actionType="monitor", payload={"mode": monitoring_path})
                ],
            )
        )
    return recs, _applied_rules(rules, outputs[0]["sidRef"] if outputs else "6.1"), monitoring_path


def evaluate_request(req: DecisionEvaluationRequest) -> DecisionEvaluationResponse:
    evaluation_id = str(uuid4())
    trace_id = str(uuid4())
    flat = _flatten_request(req)

    recommendations: List[Recommendation] = []
    applied_rules: List[AppliedRule] = []
    alerts: List[Alert] = []

    if flat["diabetesType"] != "type2":
        return DecisionEvaluationResponse(
            evaluationId=evaluation_id,
            status="blocked",
            summary=Summary(primaryTherapyPath="unsupported", monitoringPath="unsupported", educationPath="unsupported"),
            recommendations=[],
            appliedRules=[],
            missingData=[],
            alerts=[Alert(code="UNSUPPORTED-DIABETES-TYPE", severity="error", message="Il servizio di esempio supporta solo il diabete tipo 2.")],
            audit=AuditInfo(
                guidelineVersionUsed=req.guidelineVersion,
                decisionModelVersion=DECISION_MODEL_VERSION,
                generatedAt=datetime.now(timezone.utc),
                traceId=trace_id,
            ),
            fhir=FhirInfo(),
        )

    missing_data, completeness_rules = _missing_data(flat)
    applied_rules.extend(completeness_rules)

    blocking_missing = [item for item in missing_data if item.blocking]
    if blocking_missing:
        return DecisionEvaluationResponse(
            evaluationId=evaluation_id,
            status="needs-more-data",
            summary=Summary(primaryTherapyPath="incomplete", monitoringPath="incomplete", educationPath="incomplete"),
            recommendations=[],
            appliedRules=applied_rules,
            missingData=missing_data,
            alerts=[Alert(code="MISSING-DATA", severity="warning", message="Completare i dati clinici minimi prima della valutazione.")],
            audit=AuditInfo(
                guidelineVersionUsed=req.guidelineVersion,
                decisionModelVersion=DECISION_MODEL_VERSION,
                generatedAt=datetime.now(timezone.utc),
                traceId=trace_id,
            ),
            fhir=FhirInfo(),
        )

    target_recs, target_rules, target_upper = _target_recommendations(flat)
    lifestyle_recs, lifestyle_rules = _lifestyle_recommendations(flat)
    therapy_recs, therapy_rules, therapy_path, therapy_alerts = _therapy_recommendations(flat, target_upper)
    monitoring_recs, monitoring_rules, monitoring_path = _monitoring_recommendations(flat)

    recommendations.extend(target_recs)
    recommendations.extend(lifestyle_recs)
    recommendations.extend(therapy_recs)
    recommendations.extend(monitoring_recs)

    applied_rules.extend(target_rules)
    applied_rules.extend(lifestyle_rules)
    applied_rules.extend(therapy_rules)
    applied_rules.extend(monitoring_rules)

    alerts.extend(therapy_alerts)
    if flat["monitoring"]["hypoglycemiaEpisodes30d"] > 0:
        alerts.append(
            Alert(
                code="HYPO-RECENT",
                severity="warning",
                message="Sono stati riportati episodi recenti di ipoglicemia: rivalutare obiettivi e terapia.",
            )
        )
    if flat["labs"]["eGFRmlMin"] is not None and flat["labs"]["eGFRmlMin"] < 60:
        alerts.append(
            Alert(
                code="RENAL-FUNCTION-LOW",
                severity="info",
                message="Funzione renale ridotta: applicato percorso CKD o HF/CVD se pertinente.",
            )
        )

    status = "needs-clinician-review" if any(rec.requiresClinicianApproval for rec in recommendations) else "ready"

    # ------------------------------------------------------------------
    # Digital twin safety/feasibility check (control-oriented T2D twin)
    # ------------------------------------------------------------------
    glucose0 = (
        req.facts.monitoring.recentGlucosePattern.fastingMean
        or req.facts.monitoring.recentGlucosePattern.postPrandialMean
        or 140.0
    )

    dinner_cut_g = 20.0 if any(r.code == "LIFESTYLE-NUTRITION" for r in recommendations) else 0.0
    walk_minutes = 30.0 if any(r.code == "LIFESTYLE-EXERCISE" for r in recommendations) else 0.0

    # crude mapping: if hyperglycemia present under insulin regimen, propose a small correction signal
    correction_uI = 0.02 if (flat["monitoring"]["hyperglycemiaEpisodes30d"] > 0 and flat["monitoring"]["onBasalBolus"]) else 0.0

    # map medication class recommendations to parameter modifiers
    preferred_classes: list[str] = []
    for r in recommendations:
        for a in r.proposedActions:
            if a.actionType in ("start-medication", "review"):
                pcs = a.payload.get("preferredClasses") or a.payload.get("preferred") or []
                if isinstance(pcs, list):
                    preferred_classes.extend([str(x).lower() for x in pcs])
    preferred_set = set(preferred_classes)

    # Start from a patient-specific personalization layer, then apply plan-specific knobs.
    modifiers: Dict[str, float] = derive_parameter_modifiers_from_facts(req.facts)
    if "sglt2i" in preferred_set:
        modifiers["kR"] = modifiers.get("kR", 1.0) * 1.30
    if "metformin" in preferred_set:
        modifiers["rho_H"] = modifiers.get("rho_H", 1.0) * 0.90
    if "glp1ra" in preferred_set:
        modifiers["fg"] = modifiers.get("fg", 1.0) * 0.95

    twin_metrics, twin_status, twin_reason = evaluate_plan(
        glucose0_mgdl=float(glucose0),
        correction_uI=float(correction_uI),
        dinner_cut_g=float(dinner_cut_g),
        walk_minutes=float(walk_minutes),
        parameter_modifiers=modifiers,
    )

    if twin_status == "fail":
        alerts.append(
            Alert(
                code="DIGITAL-TWIN-FAIL",
                severity="error",
                message="La simulazione del digital twin predice un profilo glicemico potenzialmente non sicuro: richiede revisione clinica.",
            )
        )
        status = "needs-clinician-review"
        recommendations.insert(
            0,
            Recommendation(
                code="DIGITAL-TWIN-REVIEW",
                title="Verifica sul digital twin non superata",
                category="safety",
                priority="critical",
                sidAmdReference="",
                recommendationText="La soluzione suggerita va rivista: il digital twin prevede rischio di ipoglicemia o iperglicemia prolungata.",
                rationale=twin_reason,
                proposedActions=[
                    ProposedAction(
                        actionType="review",
                        payload={"digitalTwin": {"status": twin_status, "metrics": twin_metrics}},
                    )
                ],
            ),
        )
    elif twin_status == "warn":
        alerts.append(
            Alert(
                code="DIGITAL-TWIN-WARN",
                severity="warning",
                message="La simulazione del digital twin predice controllo subottimale: considerare aggiustamenti e revisione clinica.",
            )
        )

    digital_twin = DigitalTwinEvaluation(
        status=twin_status,
        reason=twin_reason,
        metrics=DigitalTwinMetrics(**twin_metrics),
        plan=DigitalTwinPlan(
            correction_uI=float(correction_uI),
            dinner_cut_g=float(dinner_cut_g),
            walk_minutes=float(walk_minutes),
            parameterModifiers=modifiers,
        ),
    )

    return DecisionEvaluationResponse(
        evaluationId=evaluation_id,
        status=status,
        summary=Summary(
            primaryTherapyPath=therapy_path,
            monitoringPath=monitoring_path,
            educationPath="group" if any(r.code == "EDUCATION-STRUCTURED" and "group" in r.recommendationText for r in recommendations) else "standard",
        ),
        recommendations=recommendations,
        appliedRules=applied_rules,
        missingData=missing_data,
        alerts=alerts,
        audit=AuditInfo(
            guidelineVersionUsed=req.guidelineVersion,
            decisionModelVersion=DECISION_MODEL_VERSION,
            generatedAt=datetime.now(timezone.utc),
            traceId=trace_id,
        ),
        fhir=FhirInfo(),
        digitalTwin=digital_twin,
    )
