# PostgreSQL 17 (PostgreSQL License) + PostGIS (GPL-2.0-or-later, runs inside the database only)
# + pgvector (PostgreSQL License).  pg_trgm and unaccent ship with PostgreSQL.
FROM postgres:17
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-17-postgis-3 postgresql-17-pgvector \
 && rm -rf /var/lib/apt/lists/*
