-- Replace one city's rows in city_points and city_meta, atomically.
-- Features are measured within @radius_m of every point (station or grid cell).
BEGIN TRANSACTION;

DELETE FROM `{project}.{dataset}.city_points` WHERE city_id = @city;

INSERT INTO `{project}.{dataset}.city_points`
  (city_id, point_id, kind, lat, lon, track_m, lane_m, road_m, lit_share, paved_share,
   poi_food, poi_office, poi_edu, poi_transit, poi_leisure, trips_low, trips_mid, trips_high)
WITH pts AS (
  SELECT point_id, kind, lat, lon, trips_low, trips_mid, trips_high, ST_GEOGPOINT(lon, lat) AS g
  FROM `{project}.{dataset}.pts_{city}`
),
near_lines AS (
  SELECT
    p.point_id,
    o.is_track, o.is_lane, o.is_road, o.lit, o.paved, o.has_surface,
    ST_LENGTH(ST_INTERSECTION(o.geometry, ST_BUFFER(p.g, @radius_m))) AS seg_m
  FROM pts AS p
  JOIN `{project}.{dataset}.osm_{city}` AS o
    ON o.layer = 'line' AND ST_DWITHIN(o.geometry, p.g, @radius_m)
),
lines AS (
  SELECT
    point_id,
    SUM(IF(is_track, seg_m, 0)) AS track_m,
    SUM(IF(is_lane, seg_m, 0)) AS lane_m,
    SUM(IF(is_road, seg_m, 0)) AS road_m,
    SAFE_DIVIDE(SUM(IF(is_road AND lit, seg_m, 0)), SUM(IF(is_road, seg_m, 0))) AS lit_share,
    SAFE_DIVIDE(SUM(IF(is_road AND paved, seg_m, 0)),
                SUM(IF(is_road AND has_surface, seg_m, 0))) AS paved_share
  FROM near_lines
  GROUP BY point_id
),
poi AS (
  SELECT
    p.point_id,
    COUNTIF(o.poi_class = 'food') AS poi_food,
    COUNTIF(o.poi_class = 'office') AS poi_office,
    COUNTIF(o.poi_class = 'edu') AS poi_edu,
    COUNTIF(o.poi_class = 'transit') AS poi_transit,
    COUNTIF(o.poi_class = 'leisure') AS poi_leisure
  FROM pts AS p
  JOIN `{project}.{dataset}.osm_{city}` AS o
    ON o.layer = 'poi' AND ST_DWITHIN(o.geometry, p.g, @radius_m)
  GROUP BY p.point_id
)
SELECT
  @city AS city_id,
  p.point_id, p.kind, p.lat, p.lon,
  COALESCE(l.track_m, 0) AS track_m,
  COALESCE(l.lane_m, 0) AS lane_m,
  COALESCE(l.road_m, 0) AS road_m,
  COALESCE(l.lit_share, 0) AS lit_share,
  COALESCE(l.paved_share, 0.5) AS paved_share,
  COALESCE(q.poi_food, 0) AS poi_food,
  COALESCE(q.poi_office, 0) AS poi_office,
  COALESCE(q.poi_edu, 0) AS poi_edu,
  COALESCE(q.poi_transit, 0) AS poi_transit,
  COALESCE(q.poi_leisure, 0) AS poi_leisure,
  p.trips_low, p.trips_mid, p.trips_high
FROM pts AS p
LEFT JOIN lines AS l USING (point_id)
LEFT JOIN poi AS q USING (point_id);

DELETE FROM `{project}.{dataset}.city_meta` WHERE city_id = @city;

INSERT INTO `{project}.{dataset}.city_meta` (city_id, name, demand_kind, newest_data, built_at)
VALUES (@city, @name, @demand_kind, @newest_data, CURRENT_TIMESTAMP());

COMMIT TRANSACTION;
