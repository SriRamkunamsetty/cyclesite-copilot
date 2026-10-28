CREATE TABLE IF NOT EXISTS `{project}.{dataset}.city_meta` (
  city_id STRING NOT NULL,
  name STRING,
  demand_kind STRING,
  newest_data TIMESTAMP,
  built_at TIMESTAMP
)
-- ---
CREATE TABLE IF NOT EXISTS `{project}.{dataset}.city_points` (
  city_id STRING NOT NULL,
  point_id STRING NOT NULL,
  kind STRING,
  lat FLOAT64,
  lon FLOAT64,
  track_m FLOAT64,
  lane_m FLOAT64,
  road_m FLOAT64,
  lit_share FLOAT64,
  paved_share FLOAT64,
  poi_food FLOAT64,
  poi_office FLOAT64,
  poi_edu FLOAT64,
  poi_transit FLOAT64,
  poi_leisure FLOAT64,
  trips_low FLOAT64,
  trips_mid FLOAT64,
  trips_high FLOAT64
)
-- ---
CREATE TABLE IF NOT EXISTS `{project}.{dataset}.city_existing_shops` (
  city_id STRING NOT NULL,
  shop_id STRING,
  name STRING,
  lat FLOAT64,
  lon FLOAT64
)
