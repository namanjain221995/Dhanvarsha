#!/bin/sh
# Runs once, when the PostgreSQL data volume is first initialised.
#
# Creates two roles:
#   stagevote_owner  owns the database and schema; used only by migrations/bootstrap.
#   stagevote_app    used by the API and worker at runtime. Migrations grant it
#                    SELECT/INSERT on votes and audit_log (no UPDATE/DELETE) and normal
#                    DML on the other tables.
set -eu

: "${STAGEVOTE_DB_NAME:=stagevote}"
: "${STAGEVOTE_OWNER_PASSWORD:?STAGEVOTE_OWNER_PASSWORD is required}"
: "${STAGEVOTE_APP_PASSWORD:?STAGEVOTE_APP_PASSWORD is required}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v owner_pw="$STAGEVOTE_OWNER_PASSWORD" \
  -v app_pw="$STAGEVOTE_APP_PASSWORD" \
  -v dbname="$STAGEVOTE_DB_NAME" <<'EOSQL'
CREATE ROLE stagevote_owner LOGIN PASSWORD :'owner_pw' NOSUPERUSER NOCREATEROLE CREATEDB;
CREATE ROLE stagevote_app LOGIN PASSWORD :'app_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE :"dbname" OWNER stagevote_owner;
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$STAGEVOTE_DB_NAME" <<'EOSQL'
ALTER SCHEMA public OWNER TO stagevote_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO stagevote_app;
EOSQL
