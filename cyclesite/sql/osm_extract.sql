-- Copy the OpenStreetMap features inside one bounding box into our own small table, once.
-- All later feature queries read this extract, not the planet-sized public table.
-- Road length is the full length of each way that carries cycle provision (a documented
-- simplification: a street with a track on one side counts as track along its whole length).
WITH feats AS (
  SELECT
    osm_id,
    feature_type,
    geometry,
    (SELECT value FROM UNNEST(all_tags) WHERE key = 'highway') AS highway,
    (SELECT value FROM UNNEST(all_tags) WHERE key = 'cycleway') AS cycleway,
    (SELECT value FROM UNNEST(all_tags) WHERE key = 'cycleway:left') AS cycleway_left,
    (SELECT value FROM UNNEST(all_tags) WHERE key = 'cycleway:right') AS cycleway_right,
    (SELECT value FROM UNNEST(all_tags) WHERE key = 'cycleway:both') AS cycleway_both,
    (SELECT value FROM UNNEST(all_tags) WHERE key = 'lit') AS lit,
    (SELECT value FROM UNNEST(all_tags) WHERE key = 'surface') AS surface,
    (SELECT value FROM UNNEST(all_tags) WHERE key = 'amenity') AS amenity,
    (SELECT value FROM UNNEST(all_tags) WHERE key = 'office') AS office,
    (SELECT value FROM UNNEST(all_tags) WHERE key = 'building') AS building,
    (SELECT value FROM UNNEST(all_tags) WHERE key = 'railway') AS railway,
    (SELECT value FROM UNNEST(all_tags) WHERE key = 'public_transport') AS public_transport,
    (SELECT value FROM UNNEST(all_tags) WHERE key = 'leisure') AS leisure,
    (SELECT value FROM UNNEST(all_tags) WHERE key = 'tourism') AS tourism
  FROM `{osm_table}`
  WHERE ST_INTERSECTSBOX(geometry, @west, @south, @east, @north)
    AND feature_type IN ('lines', 'points', 'multipolygons')
),
classified AS (
  SELECT
    osm_id,
    feature_type,
    geometry,
    lit,
    surface,
    highway IN ('motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'unclassified',
                'residential', 'living_street', 'service', 'cycleway', 'path',
                'motorway_link', 'trunk_link', 'primary_link', 'secondary_link',
                'tertiary_link') AS is_road,
    (highway = 'cycleway'
      OR COALESCE(cycleway, cycleway_left, cycleway_right, cycleway_both)
         IN ('track', 'opposite_track', 'separate')) AS is_track,
    COALESCE(cycleway, cycleway_left, cycleway_right, cycleway_both)
      IN ('lane', 'opposite_lane') AS is_lane,
    CASE
      WHEN amenity IN ('cafe', 'restaurant', 'fast_food', 'food_court', 'bar', 'pub',
                       'ice_cream', 'biergarten') THEN 'food'
      WHEN office IS NOT NULL OR building IN ('office', 'commercial') THEN 'office'
      WHEN amenity IN ('university', 'college', 'school', 'library') THEN 'edu'
      WHEN railway IN ('station', 'subway_entrance', 'tram_stop', 'halt')
        OR public_transport IN ('station', 'stop_position', 'platform')
        OR highway = 'bus_stop' THEN 'transit'
      WHEN leisure IN ('park', 'playground', 'sports_centre', 'fitness_centre', 'pitch',
                       'garden', 'stadium')
        OR tourism IN ('attraction', 'museum', 'viewpoint') THEN 'leisure'
    END AS poi_class
  FROM feats
)
SELECT
  osm_id,
  'line' AS layer,
  geometry,
  COALESCE(is_road, FALSE) AS is_road,
  COALESCE(is_track, FALSE) AS is_track,
  COALESCE(is_lane, FALSE) AS is_lane,
  COALESCE(lit = 'yes', FALSE) AS lit,
  surface IN ('asphalt', 'paved', 'concrete', 'concrete:plates', 'concrete:lanes',
              'paving_stones', 'sett', 'metal', 'wood') AS paved,
  surface IS NOT NULL AS has_surface,
  CAST(NULL AS STRING) AS poi_class
FROM classified
WHERE feature_type = 'lines' AND COALESCE(is_road, FALSE)
UNION ALL
SELECT
  osm_id,
  'poi' AS layer,
  ST_CENTROID(geometry) AS geometry,
  FALSE AS is_road,
  FALSE AS is_track,
  FALSE AS is_lane,
  FALSE AS lit,
  FALSE AS paved,
  FALSE AS has_surface,
  poi_class
FROM classified
WHERE feature_type IN ('points', 'multipolygons') AND poi_class IS NOT NULL
