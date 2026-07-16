# Dormant database scripts

Scripts here are review artifacts only. The Engine does not run them at startup,
or in tests. A database administrator must review and explicitly approve every
database execution.

- `0001_create_rpa_engine_schema.sql` is the original schema-only handoff.
- `0002_rpa_engine_initial_schema.sql` is the frozen initial baseline for a
  genuinely empty, approved `rpa_engine` schema. It must not run over objects
  that were provisioned previously.

Alembic references `0002` only when an operator explicitly invokes a migration;
the application itself never invokes it. A schema provisioned outside Alembic
requires drift review and an authorized `alembic stamp 20260713_0001`, not an
initial `upgrade` over existing objects.
