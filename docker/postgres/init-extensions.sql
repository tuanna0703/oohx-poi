-- Enabled at DB init for poi_lake database.
-- postgis is already enabled by the postgis base image in template1,
-- but we make it explicit and add the rest.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
