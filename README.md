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

## Endpoint principali

- `GET /health`
- `POST /v1/decision-evaluations`
- `GET /v1/decision-evaluations/{evaluationId}`
- `GET /v1/decision-evaluations/{evaluationId}/audit`
- `GET /fhir/PlanDefinition/sid-amd-dm2-2022`
- `GET /fhir/Library/sid-amd-dm2-logic`
- `POST /fhir/PlanDefinition/sid-amd-dm2-2022/$apply`

## Esempio rapido

1) Ottieni un token JWT:

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/token \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'username=admin&password=admin' | python -c "import sys, json; print(json.load(sys.stdin)['access_token'])")
```

2) Valuta un paziente:

```bash
curl -X POST http://127.0.0.1:8000/v1/decision-evaluations \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d @example-request.json
```

## Test

```bash
pytest -q
```

## Note

- I cambi terapia sono sempre restituiti come **raccomandazioni draft** e richiedono validazione clinica.
- Il database SQLite viene creato in `data/decision_service.db`.
