#!/bin/bash
# Creates the four NetSwift databases and the read-only diagnostic role.
# Runs once, on first start of an empty data directory.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
    SELECT 'CREATE DATABASE netswift_payment'
      WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'netswift_payment')\gexec
    SELECT 'CREATE DATABASE netswift_ticketing'
      WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'netswift_ticketing')\gexec
    SELECT 'CREATE DATABASE netswift_notify'
      WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'netswift_notify')\gexec
SQL

# Diagnostic role: login only, no schema rights yet. Grants are applied by core-api's
# bootstrap after the diag views exist, and are limited to SELECT on those views.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${READONLY_DIAG_USER}') THEN
            CREATE ROLE ${READONLY_DIAG_USER} LOGIN PASSWORD '${READONLY_DIAG_PASSWORD}'
                NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
        ELSE
            ALTER ROLE ${READONLY_DIAG_USER} WITH PASSWORD '${READONLY_DIAG_PASSWORD}';
        END IF;
    END
    \$\$;
    REVOKE ALL ON DATABASE netswift_core FROM ${READONLY_DIAG_USER};
    GRANT CONNECT ON DATABASE netswift_core TO ${READONLY_DIAG_USER};
    REVOKE ALL ON SCHEMA public FROM ${READONLY_DIAG_USER};
SQL

echo "db-init: databases and ${READONLY_DIAG_USER} role ready"
