# Deployment and operations

Everything here that touches Google Cloud was written from documentation and **has not been run
from the environment that produced this repository**. Treat each step as a checklist item to
confirm, and run dry runs first.

## 0. Prerequisites

- A Google Cloud project with billing enabled (DML and load jobs do not work in the free BigQuery
  sandbox).
- `gcloud`, `bq`, Python 3.11+.
- Enable APIs: `gcloud services enable aiplatform.googleapis.com bigquery.googleapis.com
  run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com`.
- Enable the BigQuery remote MCP server for the project as described in the official lab. The
  agent will talk to `https://bigquery.googleapis.com/mcp`.

## 1. Our dataset

Create it in the **US multi-region** (the public datasets live there; a query cannot join across
regions): `bq --location=US mk --dataset $GOOGLE_CLOUD_PROJECT:cyclesite`. `build-city` also
creates it if missing.

## 2. Prepare a city (a person runs this)

```bash
cyclesite build-city nyc --dry-run          # read the estimate
cyclesite build-city nyc --max-gb 600
```

Cost notes:

- The OpenStreetMap public table is very large and may not be pruned by the bounding box. The
  extract is the single expensive step; it is paid once per city, and everything after reads our
  small copy. The dry run tells you the real figure. The build refuses to start above `--max-gb`.
- The on-demand price used in the estimate text (about $6.25 per TB) is the list price when this
  was written; check current pricing.
- Re-running a build replaces the city's rows in one transaction.

## 3. Check the first build by eye

```sql
SELECT kind, COUNT(*) n, AVG(track_m) track, AVG(poi_food) food
FROM `PROJECT.cyclesite.city_points` WHERE city_id = 'nyc' GROUP BY kind;
SELECT * FROM `PROJECT.cyclesite.city_meta`;
```

Expect stations in the hundreds, cells in the thousands, `newest_data` around 2018 for Citi Bike.
If a public table's column names differ from `cities.yaml`, fix the YAML (it is validated before
it is placed in SQL).

## 4. Service account and permissions (least privilege)

`deploy/deploy_cloud_run.sh` creates `cyclesite-agent@PROJECT` with: Vertex AI User, BigQuery Job
User, MCP Tool User, Logs Writer, Trace Agent. It deliberately does **not** grant project-wide
BigQuery Data Viewer or any data editor role. Grant it **BigQuery Data Viewer on the dataset
`cyclesite` only** (BigQuery console, Dataset, Sharing, Permissions). Public datasets are readable
by any authenticated principal. Confirm the exact role names against the current lab instructions.

## 5. Deploy

```bash
GOOGLE_CLOUD_PROJECT=your-project ./deploy/deploy_cloud_run.sh
gcloud run services proxy cyclesite-copilot --region us-central1
```

- The service is **not public** (`--no-allow-unauthenticated`). Use the proxy, or put Identity-
  Aware Proxy in front of it for a team.
- The script runs the test suite first and ships `cyclesite/` next to the agent with
  `--extra_packages`.
- Cloud Trace and OpenTelemetry export are on. Guardrail decisions are in session state and in the
  memo; add a log sink if you need them centrally.
- Sessions are in memory by default and are lost on restart. For persistence pass
  `--session_service_uri` (for example a SQL URI) to the ADK deployer.

## 6. Cost controls in place

| Control | Where |
|---|---|
| Per-query byte cap for agent SQL | `CYCLESITE_MAX_BYTES_BILLED` (default 10 GB), enforced by dry run |
| Planet tables need a spatial filter | `guardrails.py` |
| Build refuses above a stated size | `--max-gb` |
| Job labels `app=cyclesite-copilot` | Filter spend by label in billing export |
| Max 3 Cloud Run instances, scale to zero | deploy script |

Also set a project **budget alert** in Cloud Billing; code cannot replace it.

## 7. Evaluating the real model

`evals/` holds cases for Gemini's behaviour (does it ask before using expired data, does it quote
the low case, does it refuse to call demand revenue). Run them with `adk eval` after deploying to
a dev project, and re-run whenever the prompt or model changes.
