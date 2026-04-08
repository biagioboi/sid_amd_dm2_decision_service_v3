from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple

import numpy as np


IDX = {
    "Q1": 0,
    "Q2": 1,
    "S1": 2,
    "S2": 3,
    "I": 4,
    "X1": 5,
    "X2": 6,
    "X3": 7,
    "M1": 8,
    "M2": 9,
    "Z": 10,
    "D": 11,
    "Gs": 12,
}


@dataclass
class Type2TwinParams:
    # volumes and thresholds
    VG: float = 140.0
    VI: float = 12.0
    Ib: float = 18.0
    G_low: float = 80.0
    G_renal: float = 180.0

    # glucose turnover
    F01: float = 70.0
    EGP0: float = 110.0
    k12: float = 0.03
    kR: float = 0.6

    # exogenous insulin absorption and effect
    tmaxI: float = 55.0
    ke: float = 0.12
    k_uI: float = 40.0

    # delayed insulin action
    ka1: float = 0.020
    ka2: float = 0.012
    ka3: float = 0.015
    kb1: float = 1.6e-4
    kb2: float = 1.2e-4
    kb3: float = 0.9e-4

    # meal absorption
    k_ge: float = 0.030
    k_abs: float = 0.024
    fg: float = 0.90

    # exercise
    tau_Z: float = 180.0
    kappa_Z: float = 0.004
    alpha_ex: float = 90.0

    # disturbance and sensor lag
    tau_d: float = 240.0
    tau_s: float = 10.0

    # Type 2 specific
    beta_basal: float = 1.6
    beta_slope: float = 0.05
    beta_sat: float = 0.012
    G_beta: float = 95.0

    # insulin resistance
    rho_P: float = 2.8
    rho_H: float = 2.0

    # simulation step (minutes)
    dt: float = 5.0


def derive_parameter_modifiers_from_facts(facts: Any) -> Dict[str, float]:
    """Derive a conservative personalization layer from clinical facts.

    This function is *not* a dosing model. It only tunes a few coarse parameters
    representing insulin resistance, residual beta-cell function, and renal
    glucose loss. It also applies medication-class proxy effects.
    """

    def _get(path: str, default=None):
        cur = facts
        for part in path.split("."):
            if cur is None:
                return default
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                cur = getattr(cur, part, None)
        return cur if cur is not None else default

    hbA1c = _get("labs.hbA1cPercent")
    bmi = _get("labs.bmi")
    egfr = _get("labs.eGFRmlMin")
    oral_classes = _get("currentTherapy.oralClasses", []) or []
    oral_set = {str(x).lower() for x in oral_classes}

    mods: Dict[str, float] = {}

    # Insulin resistance personalization (very coarse): obesity and poor control.
    if bmi is not None:
        try:
            bmi_f = float(bmi)
            if bmi_f >= 30:
                mods["rho_P"] = mods.get("rho_P", 1.0) * 1.15
            if bmi_f >= 35:
                mods["rho_P"] = mods.get("rho_P", 1.0) * 1.10
        except Exception:
            pass

    if hbA1c is not None:
        try:
            a = float(hbA1c)
            if a >= 8.5:
                mods["rho_P"] = mods.get("rho_P", 1.0) * 1.10
                mods["rho_H"] = mods.get("rho_H", 1.0) * 1.10
                mods["beta_slope"] = mods.get("beta_slope", 1.0) * 0.85
            elif a >= 7.5:
                mods["rho_P"] = mods.get("rho_P", 1.0) * 1.05
                mods["rho_H"] = mods.get("rho_H", 1.0) * 1.05
        except Exception:
            pass

    # Reduced renal function: reduce glycosuria proxy.
    if egfr is not None:
        try:
            e = float(egfr)
            if e < 60:
                mods["kR"] = mods.get("kR", 1.0) * 0.85
            if e < 30:
                mods["kR"] = mods.get("kR", 1.0) * 0.85
        except Exception:
            pass

    # Medication-class proxy effects.
    if "sglt2i" in oral_set:
        mods["kR"] = mods.get("kR", 1.0) * 1.30
        mods["G_renal"] = mods.get("G_renal", 1.0) * 0.97
    if "metformin" in oral_set:
        mods["rho_H"] = mods.get("rho_H", 1.0) * 0.92
    if "glp1ra" in oral_set:
        mods["fg"] = mods.get("fg", 1.0) * 0.95

    return mods


def _f01c(G: float, p: Type2TwinParams) -> float:
    return p.F01 if G >= p.G_low else p.F01 * max(G, 1e-6) / p.G_low


def _renal_loss(G: float, p: Type2TwinParams) -> float:
    return p.kR * max(G - p.G_renal, 0.0)


def _endogenous_secretion(G: float, p: Type2TwinParams) -> float:
    drive = max(G - p.G_beta, 0.0)
    return p.beta_basal + p.beta_slope * drive / (1.0 + p.beta_sat * drive)


def _rhs_type2(x: np.ndarray, u: np.ndarray, p: Type2TwinParams) -> np.ndarray:
    Q1, Q2, S1, S2, I, X1, X2, X3, M1, M2, Z, D, Gs = x
    uI, meal, ex = u

    G = max(Q1 / p.VG, 1e-6)
    Ra = p.fg * p.k_abs * M2 * 1000.0
    Uex = p.alpha_ex * max(ex, 0.0)

    X1e = max((1.0 + Z) * X1 / p.rho_P, 0.0)
    X2e = max((1.0 + Z) * X2 / p.rho_P, 0.0)
    hepatic_supp = np.clip(X3 / p.rho_H, -0.5, 1.0)

    dQ1 = (
        -_f01c(G, p)
        - X1e * Q1
        + p.k12 * Q2
        - _renal_loss(G, p)
        + p.EGP0 * (1.0 - hepatic_supp)
        + Ra
        - Uex
        + D
    )
    dQ2 = X1e * Q1 - (p.k12 + X2e) * Q2

    dS1 = max(uI, 0.0) - S1 / p.tmaxI
    dS2 = (S1 - S2) / p.tmaxI
    dI = (
        S2 / (p.VI * p.tmaxI)
        + p.k_uI * max(uI, 0.0)
        + _endogenous_secretion(G, p)
        - p.ke * I
    )

    dX1 = -p.ka1 * X1 + p.kb1 * (I - p.Ib)
    dX2 = -p.ka2 * X2 + p.kb2 * (I - p.Ib)
    dX3 = -p.ka3 * X3 + p.kb3 * (I - p.Ib)

    dM1 = -p.k_ge * M1 + max(meal, 0.0)
    dM2 = p.k_ge * M1 - p.k_abs * M2

    dZ = -Z / p.tau_Z + p.kappa_Z * max(ex, 0.0)
    dD = -D / p.tau_d
    dGs = (G - Gs) / p.tau_s

    return np.array(
        [dQ1, dQ2, dS1, dS2, dI, dX1, dX2, dX3, dM1, dM2, dZ, dD, dGs],
        dtype=float,
    )


def _rk4_step(x: np.ndarray, u: np.ndarray, p: Type2TwinParams) -> np.ndarray:
    dt = p.dt
    k1 = _rhs_type2(x, u, p)
    k2 = _rhs_type2(x + 0.5 * dt * k1, u, p)
    k3 = _rhs_type2(x + 0.5 * dt * k2, u, p)
    k4 = _rhs_type2(x + dt * k3, u, p)
    x_next = x + (dt / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)
    nonnegative = [
        IDX["Q1"],
        IDX["Q2"],
        IDX["S1"],
        IDX["S2"],
        IDX["I"],
        IDX["M1"],
        IDX["M2"],
        IDX["Gs"],
    ]
    x_next[nonnegative] = np.maximum(x_next[nonnegative], 0.0)
    return x_next


def simulate(x0: np.ndarray, U: np.ndarray, p: Type2TwinParams) -> np.ndarray:
    X = np.zeros((len(U) + 1, x0.shape[0]), dtype=float)
    X[0] = x0
    for k in range(len(U)):
        X[k + 1] = _rk4_step(X[k], U[k], p)
    return X


def make_day_plan(p: Type2TwinParams, correction_uI: float = 0.0, dinner_cut_g: float = 0.0, walk_minutes: float = 0.0) -> np.ndarray:
    """Return U[k] = [insulin_command, meal_rate, exercise_intensity] sampled every p.dt minutes."""
    steps = int(24 * 60 / p.dt)
    U = np.zeros((steps, 3), dtype=float)

    # Meals: breakfast 8:00, lunch 13:00, dinner 19:00
    def add_meal(start_min: int, grams: float, duration_min: int = 20):
        k0 = int(start_min / p.dt)
        k1 = int((start_min + duration_min) / p.dt)
        rate = max(grams, 0.0) / max(duration_min, 1)
        U[k0:k1, 1] = rate

    breakfast_g = 45.0
    lunch_g = 60.0
    dinner_g = max(70.0 - dinner_cut_g, 20.0)
    add_meal(8 * 60, breakfast_g)
    add_meal(13 * 60, lunch_g)
    add_meal(19 * 60, dinner_g)

    # Exercise: a walk at 18:00 for walk_minutes
    if walk_minutes > 0:
        start = int(18 * 60 / p.dt)
        end = int((18 * 60 + walk_minutes) / p.dt)
        U[start:end, 2] = 1.0

    # Insulin correction: small bolus around dinner
    if correction_uI > 0:
        k = int((19 * 60) / p.dt)
        U[k : k + int(30 / p.dt), 0] = correction_uI

    return U


def apply_parameter_modifiers(p: Type2TwinParams, modifiers: Dict[str, float]) -> Type2TwinParams:
    """Return a copy of params with selected multiplicative modifiers applied."""
    p2 = Type2TwinParams(**p.__dict__)
    for k, v in modifiers.items():
        if hasattr(p2, k):
            setattr(p2, k, float(getattr(p2, k)) * float(v))
    return p2


def evaluate_plan(
    *,
    glucose0_mgdl: float,
    correction_uI: float,
    dinner_cut_g: float,
    walk_minutes: float,
    parameter_modifiers: Dict[str, float] | None = None,
    horizon_minutes: int = 24 * 60,
    dt_minutes: int = 5,
) -> Tuple[Dict[str, float], str, str]:
    """Simulate 24h and return metrics + status (pass|warn|fail) + reason."""
    p = Type2TwinParams(dt=float(dt_minutes))
    mods = parameter_modifiers or {}
    p = apply_parameter_modifiers(p, mods)

    # Initial state: set Q1 and sensor to the observed glucose.
    x0 = np.zeros((13,), dtype=float)
    g0 = float(glucose0_mgdl)
    x0[IDX["Q1"]] = g0 * p.VG
    x0[IDX["Gs"]] = g0

    U_full = make_day_plan(p, correction_uI=correction_uI, dinner_cut_g=dinner_cut_g, walk_minutes=walk_minutes)
    steps = int(horizon_minutes / p.dt)
    U = U_full[:steps]
    X = simulate(x0, U, p)
    Gs = X[:-1, IDX["Gs"]]

    min_g = float(np.min(Gs))
    max_g = float(np.max(Gs))
    mean_g = float(np.mean(Gs))
    tir = float(np.mean((Gs >= 70) & (Gs <= 180)))
    tbr = float(np.mean(Gs < 70))
    tab250 = float(np.mean(Gs > 250))

    # Simple acceptance policy (tunable): avoid hypoglycemia; limit extremes.
    if min_g < 65 or tab250 > 0.20:
        return (
            {
                "horizonMinutes": horizon_minutes,
                "dtMinutes": dt_minutes,
                "minGlucose": min_g,
                "maxGlucose": max_g,
                "meanGlucose": mean_g,
                "timeInRange70_180": tir,
                "timeBelow70": tbr,
                "timeAbove250": tab250,
            },
            "fail",
            "Predicted unsafe extremes (hypoglycemia <65 mg/dL or prolonged >250 mg/dL).",
        )
    if min_g < 70 or max_g > 250 or tir < 0.60:
        return (
            {
                "horizonMinutes": horizon_minutes,
                "dtMinutes": dt_minutes,
                "minGlucose": min_g,
                "maxGlucose": max_g,
                "meanGlucose": mean_g,
                "timeInRange70_180": tir,
                "timeBelow70": tbr,
                "timeAbove250": tab250,
            },
            "warn",
            "Predicted suboptimal control (outside 70–180 mg/dL targets).",
        )

    return (
        {
            "horizonMinutes": horizon_minutes,
            "dtMinutes": dt_minutes,
            "minGlucose": min_g,
            "maxGlucose": max_g,
            "meanGlucose": mean_g,
            "timeInRange70_180": tir,
            "timeBelow70": tbr,
            "timeAbove250": tab250,
        },
        "pass",
        "Plan predicted to keep glucose largely in range.",
    )
