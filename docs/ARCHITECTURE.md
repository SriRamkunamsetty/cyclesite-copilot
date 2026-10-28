# Architecture

## Principle: the model talks, code calculates

Gemini plans the conversation, calls tools and explains results in plain words. Everything
numeric (distances, scores, optimisation, backtests, statistics) happens in `cyclesite/`, which is
plain Python and NumPy. This keeps results reproducible, testable and auditable, and stops the
model from inventing figures. The orchestrator prompt makes this an explicit rule, and the memo is
built from results stored in session state, not from numbers the model retyped.

## Components

| Component | File | Job |
|---|---|---|
| Orchestrator | `cycle_agent/agent.py`, `prompts.py` | Plans the workflow, calls tools, writes the answer |
| Function tools | `cycle_agent/tools.py` | Thin wrappers that validate input, call the library, store results in state |
| Critic agent | `cycle_agent/agent.py` (`site_critic`) | Argues why the pick could fail, only from supplied findings |
| BigQuery MCP toolset | `cycle_agent/mcp.py` | Official remote MCP server, read-only tool subset, ADC bearer token per request |
| SQL guard | `cyclesite/guardrails.py` | `before_tool_callback` on every `execute_sql*` call |
| Optimiser | `cyclesite/optimizer.py` | Greedy coverage maximisation with cannibalisation; sensitivity runs |
| Rideability | `cyclesite/scoring.py` | Infrastructure-only 0-1 score |
| Demand model | `cyclesite/model.py` | Ridge regression on log trips (places only) for cities without bikeshare |
| Backtest | `cyclesite/backtest.py` | Held-out stations, three models, paired bootstrap |
| Transfer check | `cyclesite/transfer.py` | Does the target city look like the tested ones? |
| Freshness | `cyclesite/freshness.py` | fresh / stale / expired / unknown, and whether the user must agree |
| Red team + memo | `cyclesite/redteam.py`, `memo.py` | Number-backed risks; the final write-up |
| Build pipeline | `cyclesite/build.py`, `sql/`, `cities.yaml` | Prepares one city's tables (a human runs it) |
| Repositories | `cyclesite/repository.py`, `bigquery_repo.py`, `synthetic.py` | One interface, two stores: BigQuery and offline synthetic |

## Data model (our own BigQuery dataset)

- `city_points(city_id, point_id, kind, lat, lon, track_m, lane_m, road_m, lit_share,
  paved_share, poi_food, poi_office, poi_edu, poi_transit, poi_leisure, trips_low, trips_mid,
  trips_high)`: one row per station or grid cell. Features are measured within a radius (600 m by
  default) of each point.
- `city_meta(city_id, name, demand_kind, newest_data, built_at)`: what the freshness check reads.
- `city_existing_shops(city_id, shop_id, name, lat, lon)`: shops counted before choosing.

Per-city intermediates (`osm_<city>`, `pts_<city>`) are kept so a rebuild is cheap.

## The optimisation

Demand points are stations (observed) or grid cells (modelled). Each carries low/mid/high yearly
trips and a rideability score. A demand point's weight is `trips × (floor + (1 − floor) ×
rideability)`. A shop at candidate `j` captures a share `f(d) = exp(−d / decay)` of a point at
distance `d`, cut off at a maximum radius. With `S` the shops open or already chosen,

    gain(j | S) = Σ_c w_c · max(0, f(d_cj) − max_{s∈S} f(d_cs))

This is a monotone submodular coverage function, so greedy selection carries a `(1 − 1/e)`
guarantee. Existing shops start inside `S`, which is how cannibalisation enters. Candidates must
meet a minimum rideability and a minimum spacing. Ranking uses the **low** case by default.
Every pick reports its standalone gain, its marginal gain and the share lost to overlap.

**Stability test.** The optimiser is re-run for five catchment sizes × three demand cases. Each
pick is scored by how often it reappears; a low share is flagged as fragile in the memo.

## The guardrails

For every `execute_sql` / `execute_sql_readonly` call (see `guardrails.py`):

1. One left-to-right lexer pass removes comments and string literals (a two-pass version could be
   fooled by `SELECT '#'; DROP TABLE t`; there is a regression test).
2. Must be a single `SELECT`/`WITH`; no DDL/DML/scripting keywords.
3. Planet-sized OpenStreetMap tables need a spatial filter.
4. Dry run through the BigQuery client; blocked above the byte cap; **fails closed** if the dry
   run itself errors.
5. Project id must match ours.
6. Every decision (allowed or blocked, bytes, hash, SQL) is appended to session state and printed
   in the memo's audit trail.

The MCP server's own read-only tool is the second layer. The guard is defence in depth, not a
replacement for IAM: the runtime service account has no write roles on data.

## Why two paths to BigQuery

The MCP toolset gives the agent flexible, schema-aware exploration for follow-up questions. The
ranking, however, must be repeatable, so it reads prepared tables through a typed repository and
never through free-form SQL written by the model.

## Failure behaviour

- Unknown city, bad parameter, or too little data: the tool returns `{"status": "error"}` with a
  message the model can relay. No exceptions leak into the conversation.
- Expired data: `choose_sites` returns `needs_confirmation` until the user agrees.
- No eligible candidate: the result has an empty pick list and a warning.
- BigQuery unreachable: the repository raises; the tool reports the failure.
