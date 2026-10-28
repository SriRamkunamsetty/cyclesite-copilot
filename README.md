# CycleSite Copilot

An expansion agent that helps a coffee chain choose its next shops by **how easy they are to reach
by bicycle**, and is honest about how sure it is. Built for the Google Gen AI Academy challenge
*"Optimize Shop Placement with Gemini and BigQuery"* (Track 2, Lab 2) using Gemini, the Agent
Development Kit (ADK), the BigQuery remote MCP server and public datasets.

In one sentence for a judge: **the official lab ranks three old bikeshare stations by trip
counts; this project picks a set of shops that do not steal each other's customers, checks its own
method against real usage, shows low/mid/high outcomes, and says "my data is too old" when it is.**

## What it adds to the official lab

| Official lab | CycleSite Copilot |
|---|---|
| Ranks stations by trip counts | Chooses a *portfolio* of K sites that add the most **new** demand (accounts for shops you already have and for shops overlapping each other) |
| Popularity = cyclist-friendliness | Separate **rideability** score from real bike infrastructure (separated tracks, lanes, lighting, surface, network share) in OpenStreetMap |
| 2013-2018 data used without comment | **Data-age check** in code. Expired data needs the user's explicit OK and is labelled in the memo |
| One answer | **Low / mid / high** outcome, plus a **stability test** (re-run under different assumptions) |
| No validation | **Backtest** on stations the model never saw, with a bootstrap interval. If infrastructure adds no lift, the memo says so |
| One city | **Transfer check** for a city with no bikeshare data (for example Pune): demand is modelled from nearby places and flagged as extrapolation |
| Cost safety is a prompt request | **SQL guardrails in code**: read-only, single statement, spatial filter on planet-sized tables, dry-run byte cap, project lock, audit trail |
| `execute_sql` exposed | Only **read-only** MCP tools exposed (`execute_sql_readonly`) |
| Free-text answer | **Decision memo** built from stored computation, with every query that ran |
| No critic | A separate **critic agent** argues why the pick could fail, using only supplied numbers |

All numbers come from deterministic code (`cyclesite/`). The LLM plans, calls tools and explains;
it never does the arithmetic.

## Try it in two minutes, no cloud account

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m cyclesite.cli demo          # prints a full decision memo from SYNTHETIC data
pytest -q                             # 100+ tests, all offline
```

To chat with the agent on synthetic data (needs a Gemini key or Google Cloud login for the model):

```bash
cp .env.example .env                  # then set CYCLESITE_BACKEND=synthetic and your project
./scripts/run_ui.sh                  # open the UI, choose "cycle_agent"
```

Try: *"Where should we open 3 new shops in demo_a?"*, then *"Does demo_b look trustworthy?"* (its
data is deliberately old) and *"What about demo_c, a city with no bikeshare?"*.

## Real data (BigQuery)

```bash
export GOOGLE_CLOUD_PROJECT=your-project CYCLESITE_BACKEND=bigquery
cyclesite cities                      # nyc, sf (observed demand) and pune (modelled)
cyclesite build-city nyc --dry-run    # prints estimated scan size and cost. Spends nothing.
cyclesite build-city nyc --max-gb 600
cyclesite build-city pune --shops-csv my_shops.csv   # CSV columns: name,lat,lon
./scripts/run_ui.sh                  # open the UI, choose "cycle_agent"
```

`build-city` is run by a person, not by the agent. It refuses to start if the dry run is above
`--max-gb`. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for IAM, Cloud Run and cost notes.

## How it works

```
 user ─▶ Orchestrator (ADK LlmAgent, Gemini)
            ├─ function tools (cyclesite/): freshness · transfer · choose_sites · backtest · red team · memo
            ├─ site_critic (second LlmAgent, no tools; reasons only over supplied findings)
            └─ BigQuery MCP toolset (read-only subset) ─▶ before_tool_callback = SQL guard
                                                          (static checks, dry run, byte cap, audit)
 city_points / city_meta / city_existing_shops  ◀── cyclesite build-city (OSM + public trips)
```

The choice of sites maximises `gain(j | S) = Σ_c D_c · max(0, f(d_cj) − max_{s∈S} f(d_cs))`: extra
demand captured, where `D_c` is demand (weighted by rideability), `f` is a distance-decay
catchment and `S` holds shops already open or already chosen. Greedy selection is the standard
(1 − 1/e) approximation for this kind of coverage problem; a test compares it with brute force.
Details in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## What is verified, and what is not

Verified here (automated tests, no cloud): the optimiser (including against brute force), scoring,
guardrails (including a bypass found and fixed during review), backtest, transfer check, demand
bands, build pipeline orchestration against a fake BigQuery client, SQL templates **parse** as
BigQuery SQL, and the full agent workflow through the real ADK runtime with a scripted model.

**Not verified here, so check before you rely on it:** the SQL has not been *executed* against live
BigQuery; column names for public datasets (Citi Bike, Bay Area bikeshare, OpenStreetMap
`planet_features`) are from documentation and memory, so run `cyclesite build-city <city>
--dry-run` first; real Gemini behaviour (use the eval set in `evals/`); the Cloud Run deploy.

## Honest limitations

- Demand is **modelled bike trips**, not revenue. Coffee sales depend on price, product and rent,
  which are not in public data.
- Bikeshare trips measure bikeshare riders, not all cyclists, and are **historical** (the public
  Citi Bike table ends in 2018). The memo says so. Use a fresher source for real decisions.
- Infrastructure comes from a current OpenStreetMap snapshot, trips from earlier years. The
  backtest carries a caveat for that mismatch.
- The forecast range is a transparent heuristic (month-to-month noise), not a full forecast.
- Rideability weights and the distance-decay curve are assumptions you can change.
- See [docs/RESPONSIBLE_AI.md](docs/RESPONSIBLE_AI.md).

## Layout

```
cycle_agent/   ADK agent: agent.py (root_agent), tools.py, prompts.py, mcp.py, services.py
cyclesite/     core library: optimizer, scoring, model, backtest, transfer, freshness,
               guardrails, memo, redteam, build pipeline, cities.yaml, sql/
tests/         offline test suite (incl. ADK end-to-end with a scripted model)
evals/         evaluation set for real Gemini behaviour
deploy/        Cloud Run deploy script
docs/          architecture, deployment, responsible AI
```

Licensed under Apache-2.0.
