# Alembic migrations

The application never invokes Alembic and never calls `create_all`. Revision
`20260713_0001` is the dormant initial representation of the nine Engine-owned
tables. Creating the revision did not execute any database operation.

## Applying the baseline

- For an approved empty `rpa_engine` schema, an operator may use `alembic
  upgrade head` to create the baseline.
- If the nine tables were provisioned outside Alembic and the schema has no
  matching version record, do **not** run the initial upgrade over them. First
  compare the live schema with the revision, resolve all drift, and then record
  the reviewed baseline with:

```text
alembic stamp 20260713_0001
```

Both operations are administrator actions and require an explicit database
change authorization. Engine startup and the automated test suite perform
neither operation.
