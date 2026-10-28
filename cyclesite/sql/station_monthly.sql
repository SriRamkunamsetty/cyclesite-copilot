-- Trips touching each bikeshare station per full calendar month (starts and ends both count:
-- a rider arriving is as much a cyclist on the street as one leaving).
-- The newest, possibly partial, month is excluded so it cannot understate demand.
WITH bounds AS (
  SELECT
    DATE_TRUNC(DATE(MAX({time_col})), MONTH) AS last_month,
    MAX({time_col}) AS newest
  FROM `{trips_table}`
),
ev AS (
  SELECT CAST({start_id} AS STRING) AS station_id, {start_lat} AS lat, {start_lon} AS lon,
         DATE_TRUNC(DATE({time_col}), MONTH) AS month
  FROM `{trips_table}`
  UNION ALL
  SELECT CAST({end_id} AS STRING) AS station_id, {end_lat} AS lat, {end_lon} AS lon,
         DATE_TRUNC(DATE({time_col}), MONTH) AS month
  FROM `{trips_table}`
)
SELECT
  ev.station_id,
  APPROX_QUANTILES(ev.lat, 2)[OFFSET(1)] AS lat,
  APPROX_QUANTILES(ev.lon, 2)[OFFSET(1)] AS lon,
  ev.month AS month,
  COUNT(*) AS trips,
  ANY_VALUE(bounds.newest) AS newest
FROM ev
CROSS JOIN bounds
WHERE ev.station_id IS NOT NULL
  AND ev.month < bounds.last_month
  AND ev.month >= DATE_SUB(bounds.last_month, INTERVAL @window_months MONTH)
  AND ev.lat BETWEEN @south AND @north
  AND ev.lon BETWEEN @west AND @east
GROUP BY ev.station_id, ev.month
