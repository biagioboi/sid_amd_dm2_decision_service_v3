# SID/AMD DM2 Decision Service v3

Reference implementation per la gestione del diabete mellito tipo 2 secondo linea guida SID/AMD 2022.

## Cosa include

- API JSON per valutazioni cliniche
- Facciata FHIR con `PlanDefinition/$apply`
- Decision table **DMN XML** (subset FEEL) + fallback legacy YAML
- Artefatti FHIR/CQL
- Persistenza SQL (SQLite di default; PostgreSQL via Docker Compose)
- Audit trail base
- Autenticazione OAuth2 Password Flow + JWT (Bearer)
- Validazione **Digital Twin**: prima di restituire i suggerimenti, simula un piano di 24h e produce esito `pass|warn|fail` + metriche in `digitalTwin`
- Simulatore **Digital Twin avanzato** orientato a timeline di eventi (pasti, esercizio, boli, farmaci, stress), impostato per poter usare il dataset **Shanghai_T2DM**
- Test automatici con `pytest`

## Avvio

```bash
pip install -r requirements.txt
export DEV_USERNAME=admin
export DEV_PASSWORD=admin
export JWT_SECRET=dev-jwt-secret
uvicorn app.main:app --reload
```

### Avvio con PostgreSQL (Docker Compose)

```bash
docker compose up --build
```

Il `docker-compose.yml` monta automaticamente `./dataset` in `/app/dataset` per abilitare gli endpoint dataset-driven del digital twin.

## Endpoint principali

- `GET /health`
- `POST /v1/decision-evaluations`
- `GET /v1/decision-evaluations/{evaluationId}`
- `GET /v1/decision-evaluations/{evaluationId}/audit`
- `POST /v1/digital-twin/simulate`
- `POST /v1/digital-twin/simulate-advanced`
- `POST /v1/digital-twin/calibrate-from-dataset`
- `POST /v1/digital-twin/simulate-from-dataset`
- `GET /fhir/PlanDefinition/sid-amd-dm2-2022`
- `GET /fhir/Library/sid-amd-dm2-logic`
- `POST /fhir/PlanDefinition/sid-amd-dm2-2022/$apply`

## Esempio rapido

1) Ottieni un token JWT:

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:9001/auth/token \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'username=admin&password=admin' | python -c "import sys, json; print(json.load(sys.stdin)['access_token'])")
```

2) Valuta un paziente:

```bash
curl -X POST http://127.0.0.1:9001/v1/decision-evaluations \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d @example-request.json
```

3) Lancia una simulazione avanzata del twin:

```bash
curl -X POST http://127.0.0.1:9001/v1/digital-twin/simulate-advanced \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "profile": {
      "patientId": "P-TWIN-001",
      "baselineGlucoseMgdl": 150,
      "hbA1cPercent": 8.0,
      "bmi": 31,
      "oralClasses": ["metformin", "sglt2i"]
    },
    "scenario": {
      "name": "baseline-day",
      "horizonMinutes": 720,
      "dtMinutes": 15,
      "events": [
        {"eventType": "meal", "startMinutes": 30, "mealCarbsGrams": 55, "glycemicIndex": 95, "label": "breakfast"},
        {"eventType": "exercise", "startMinutes": 120, "durationMinutes": 45, "intensity": 1.2, "label": "walk"},
        {"eventType": "insulin-bolus", "startMinutes": 30, "insulinUnits": 2.0, "label": "correction"}
      ]
    }
  }'
```

4) Calibra il twin su una sessione reale del dataset Shanghai T2DM:

```bash
curl -X POST http://127.0.0.1:9001/v1/digital-twin/calibrate-from-dataset \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "recordId": "2014_1_20210317"
  }'
```

5) Esegui replay e simulazione di una sessione del dataset:

```bash
curl -X POST http://127.0.0.1:9001/v1/digital-twin/simulate-from-dataset \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "recordId": "2014_1_20210317",
    "calibrateProfile": true
  }'
```

## Test

```bash
pytest -q
```

## Note

- I cambi terapia sono sempre restituiti come **raccomandazioni draft** e richiedono validazione clinica.
- Il database SQLite viene creato in `data/decision_service.db`.
- Il nuovo endpoint avanzato del twin usa come default il contesto del dataset Shanghai T2DM: 100 pazienti T2DM, CGM ogni 15 minuti, registrazioni di 3-14 giorni, come descritto nell'articolo "Chinese diabetes datasets for data-driven machine learning" (Scientific Data, 2023) e nella repo dataset indicata dall'utente.
- Gli endpoint dataset-driven richiedono l'accesso ai file Excel nel path `dataset` e un binario `soffice` disponibile nel runtime per convertire `.xls/.xlsx` in CSV temporanei.
- Nell'immagine Docker il supporto a `soffice` viene installato dal `Dockerfile`; con Docker Compose il dataset viene montato read-only da `./dataset`.
