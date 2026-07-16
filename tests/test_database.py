from __future__ import annotations

import pytest

import nodeskclaw_rpa_engine.db.session as session_module
from nodeskclaw_rpa_engine.db.session import (
    DatabaseConfigurationError,
    DatabaseManager,
)


def test_database_manager_rejects_non_asyncpg_driver() -> None:
    with pytest.raises(DatabaseConfigurationError, match=r"postgresql\+asyncpg"):
        DatabaseManager(
            "postgresql://user:secret@db/nodeskclaw_task",
            schema="rpa_engine",
            pool_size=5,
            app_name="test-engine",
        )


async def test_database_manager_sets_connection_local_search_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeEngine:
        async def dispose(self) -> None:
            captured["disposed"] = True

    def fake_create_async_engine(url: str, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return FakeEngine()

    def fake_sessionmaker(*args, **kwargs):
        captured["sessionmaker"] = (args, kwargs)
        return object()

    monkeypatch.setattr(
        session_module,
        "create_async_engine",
        fake_create_async_engine,
    )
    monkeypatch.setattr(session_module, "async_sessionmaker", fake_sessionmaker)

    manager = DatabaseManager(
        "postgresql+asyncpg://user:secret@db/nodeskclaw_task",
        schema="rpa_engine",
        pool_size=7,
        app_name="test-engine",
    )
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["connect_args"] == {
        "server_settings": {
            "search_path": "rpa_engine,public",
            "application_name": "test-engine",
        }
    }
    assert "create_all" not in kwargs

    await manager.close()
    assert captured["disposed"] is True
