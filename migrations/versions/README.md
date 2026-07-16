# Revision files

`20260713_0001_existing_schema_baseline.py` freezes the initial `rpa_engine`
schema: nine tables, 142 columns, four trigger functions, and twelve triggers.
It consumes `sql/0002_rpa_engine_initial_schema.sql`.

An empty approved schema may be upgraded through this revision. A schema whose
baseline objects were created outside Alembic must be drift-reviewed and then
**stamped**, never blindly upgraded through the initial revision. Neither stamp
nor upgrade is part of the offline test suite.
