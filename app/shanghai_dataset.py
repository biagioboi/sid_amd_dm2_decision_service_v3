from __future__ import annotations

import csv
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from .models import (
    TwinDatasetReference,
    TwinGlucoseSample,
    TwinPatientProfile,
    TwinScenario,
    TwinEvent,
)


CSV_CACHE_DIR = Path("/tmp/shanghai_t2dm_csv_cache")


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text).strip().lower())


def _safe_float(value):
    if value is None:
        return None
    text = str(value).strip()
    if text in ("", "/", "None", "none", "nan"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _safe_int(value):
    val = _safe_float(value)
    return None if val is None else int(round(val))


def _parse_datetime(value: str) -> datetime:
    text = str(value).strip()
    for fmt in ("%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unsupported datetime format: {value}")


def _ensure_csv(source_path: Path) -> Path:
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    CSV_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = CSV_CACHE_DIR / f"{source_path.stem}.csv"
    if csv_path.exists() and csv_path.stat().st_mtime >= source_path.stat().st_mtime:
        return csv_path

    try:
        subprocess.run(
            [
                "soffice",
                "--headless",
                "--convert-to",
                "csv",
                "--outdir",
                str(CSV_CACHE_DIR),
                str(source_path),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("LibreOffice (`soffice`) is required to load the Shanghai Excel dataset.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Failed to convert {source_path.name} to CSV: {exc.stderr or exc.stdout}") from exc

    if not csv_path.exists():
        raise RuntimeError(f"CSV conversion did not produce {csv_path}")
    return csv_path


def _read_csv_rows(csv_path: Path) -> List[Dict[str, str]]:
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


def _load_summary_index(summary_path: Path) -> Dict[str, Dict[str, str]]:
    rows = _read_csv_rows(_ensure_csv(summary_path))
    out: Dict[str, Dict[str, str]] = {}
    for row in rows:
        key = str(row.get("Patient Number", "")).strip()
        if key:
            out[key] = row
    return out


def _extract_units(text: str) -> float | None:
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:IU|U)\b", text, flags=re.IGNORECASE)
    if match:
        return float(match.group(1))
    val = _safe_float(text)
    return None if val is None else float(val)


def _split_medications(text: str) -> List[str]:
    if not text:
        return []
    cleaned = str(text).replace("\n", ",")
    meds = []
    for item in cleaned.split(","):
        name = item.strip()
        if name:
            meds.append(name)
    return meds


def _is_basal_insulin(text: str) -> bool:
    normalized = str(text).lower()
    return any(token in normalized for token in ("degludec", "glarig", "glarg", "detemir", "basal"))


def _estimate_food_line_carbs(text: str) -> float:
    line = str(text).strip().lower()
    if not line:
        return 0.0

    grams_match = re.search(r"(\d+(?:\.\d+)?)\s*g\b", line)
    grams = float(grams_match.group(1)) if grams_match else 0.0
    if grams <= 0:
        return 0.0

    if any(token in line for token in ("rice", "grain", "bread", "bun", "noodle", "porridge", "congee", "cake", "dumpling")):
        factor = 0.55
    elif any(token in line for token in ("yam", "potato", "corn", "bean", "fruit")):
        factor = 0.20
    elif any(token in line for token in ("egg", "pork", "beef", "chicken", "shrimp", "fish", "tofu")):
        factor = 0.05
    elif any(token in line for token in ("soup", "cabbage", "broccoli", "vegetable", "bean curd", "fungus", "mushroom")):
        factor = 0.08
    else:
        factor = 0.12
    return grams * factor


def _estimate_meal_from_text(text: str) -> tuple[float, str]:
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    total_carbs = sum(_estimate_food_line_carbs(line) for line in lines)
    label = lines[0] if lines else "meal"
    return round(total_carbs, 2), label[:80]


def _summary_to_profile(summary_row: Dict[str, str]) -> TwinPatientProfile:
    gender = _safe_int(summary_row.get("Gender (Female=1, Male=2)"))
    sex = None
    if gender == 1:
        sex = "female"
    elif gender == 2:
        sex = "male"

    height_m = _safe_float(summary_row.get("Height (m)"))
    oral_classes = _split_medications(summary_row.get("Hypoglycemic Agents", ""))

    return TwinPatientProfile(
        patientId=str(summary_row.get("Patient Number", "")).strip() or None,
        diabetesType=str(summary_row.get("Type of Diabetes", "type2")).strip().lower(),
        ageYears=_safe_int(summary_row.get("Age (years)")),
        sexAtBirth=sex,
        diabetesDurationYears=_safe_float(summary_row.get("Duration of Diabetes (years)")),
        weightKg=_safe_float(summary_row.get("Weight (kg)")),
        heightCm=(height_m * 100.0) if height_m is not None else None,
        bmi=_safe_float(summary_row.get("BMI (kg/m2)")),
        hbA1cPercent=(
            None
            if _safe_float(summary_row.get("HbA1c (mmol/mol)")) is None
            else (_safe_float(summary_row.get("HbA1c (mmol/mol)")) / 10.929) + 2.15
        ),
        eGFRmlMin=_safe_float(summary_row.get("Estimated Glomerular Filtration Rate  (ml/min/1.73m2) ")),
        baselineGlucoseMgdl=_safe_float(summary_row.get("Fasting Plasma Glucose (mg/dl)")),
        oralClasses=oral_classes,
    )


def load_shanghai_record(
    *,
    record_id: str,
    dataset_root: str,
    summary_path: str,
    dt_minutes: int = 15,
) -> tuple[TwinDatasetReference, TwinPatientProfile, TwinScenario, List[TwinGlucoseSample], str, List[str]]:
    dataset_dir = Path(dataset_root)
    summary_file = Path(summary_path)

    if not dataset_dir.exists():
        raise FileNotFoundError(dataset_dir)
    if not summary_file.exists():
        raise FileNotFoundError(summary_file)

    source_path = dataset_dir / f"{record_id}.xlsx"
    if not source_path.exists():
        source_path = dataset_dir / f"{record_id}.xls"
    if not source_path.exists():
        raise FileNotFoundError(f"No source file found for recordId {record_id}")

    summary_index = _load_summary_index(summary_file)
    summary_row = summary_index.get(record_id)
    if summary_row is None:
        raise ValueError(f"Record {record_id} not found in Shanghai summary file")

    rows = _read_csv_rows(_ensure_csv(source_path))
    if not rows:
        raise ValueError(f"No rows found in dataset file {source_path.name}")

    parsed_rows = []
    for row in rows:
        if not str(row.get("Date", "")).strip():
            continue
        parsed_rows.append(row)

    if not parsed_rows:
        raise ValueError(f"No timestamped rows found in dataset file {source_path.name}")

    first_ts = _parse_datetime(parsed_rows[0]["Date"])
    last_ts = _parse_datetime(parsed_rows[-1]["Date"])
    horizon_minutes = max(int((last_ts - first_ts).total_seconds() / 60), dt_minutes)

    historical_glucose: List[TwinGlucoseSample] = []
    events: List[TwinEvent] = []

    for row in parsed_rows:
        ts = _parse_datetime(row["Date"])
        minute_offset = int((ts - first_ts).total_seconds() / 60)

        cgm = _safe_float(row.get("CGM (mg / dl)"))
        if cgm is not None:
            historical_glucose.append(
                TwinGlucoseSample(
                    minuteOffset=minute_offset,
                    glucoseMgdl=cgm,
                    source="cgm",
                )
            )

        dietary = str(row.get("Dietary intake", "")).strip()
        if dietary:
            carbs, label = _estimate_meal_from_text(dietary)
            if carbs > 0:
                events.append(
                    TwinEvent(
                        eventType="meal",
                        startMinutes=minute_offset,
                        durationMinutes=30,
                        mealCarbsGrams=carbs,
                        glycemicIndex=95.0,
                        label=label,
                        notes=dietary[:240],
                    )
                )

        sc_text = str(row.get("Insulin dose - s.c.", "")).strip()
        sc_units = _extract_units(sc_text)
        if sc_units:
            events.append(
                TwinEvent(
                    eventType="insulin-basal" if _is_basal_insulin(sc_text) else "insulin-bolus",
                    startMinutes=minute_offset,
                    durationMinutes=12 * 60 if _is_basal_insulin(sc_text) else 0,
                    insulinUnits=sc_units,
                    label=sc_text[:80],
                    notes=sc_text[:240],
                )
            )

        csii_bolus = _safe_float(row.get("CSII - bolus insulin (Novolin R, IU)"))
        if csii_bolus is not None and csii_bolus > 0:
            events.append(
                TwinEvent(
                    eventType="insulin-bolus",
                    startMinutes=minute_offset,
                    insulinUnits=csii_bolus,
                    label="CSII bolus insulin",
                )
            )

        csii_basal = _safe_float(row.get("CSII - basal insulin (Novolin R, IU / H)"))
        if csii_basal is not None and csii_basal > 0:
            events.append(
                TwinEvent(
                    eventType="insulin-basal",
                    startMinutes=minute_offset,
                    durationMinutes=dt_minutes,
                    insulinUnits=csii_basal,
                    label="CSII basal insulin",
                )
            )

        non_insulin = str(row.get("Non-insulin hypoglycemic agents", "")).strip()
        if non_insulin:
            for med in _split_medications(non_insulin):
                events.append(
                    TwinEvent(
                        eventType="medication",
                        startMinutes=minute_offset,
                        medicationClass=med,
                        label=med[:80],
                    )
                )

    profile = _summary_to_profile(summary_row)
    if historical_glucose:
        profile = profile.copy(update={"baselineGlucoseMgdl": historical_glucose[0].glucoseMgdl})

    scenario = TwinScenario(
        name=f"shanghai-{record_id}",
        description=f"Replay scenario extracted from Shanghai T2DM record {record_id}",
        horizonMinutes=horizon_minutes,
        dtMinutes=dt_minutes,
        startingGlucoseMgdl=historical_glucose[0].glucoseMgdl if historical_glucose else profile.baselineGlucoseMgdl,
        events=events,
    )

    assumptions = [
        "Meal carbohydrates were estimated from the English dietary text using simple food-category heuristics.",
        "Subcutaneous insulin entries were mapped to basal vs bolus by insulin name heuristics when possible.",
        "The Shanghai session is replayed as an event timeline aligned to the recorded CGM trace.",
    ]

    dataset_ref = TwinDatasetReference()
    return dataset_ref, profile, scenario, historical_glucose, str(source_path), assumptions
