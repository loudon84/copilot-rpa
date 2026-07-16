from __future__ import annotations

import httpx

from nodeskclaw_rpa_engine.mock_srm.app import SUCCESS_PO, create_mock_srm_app


async def test_mock_srm_page_and_health_are_available() -> None:
    transport = httpx.ASGITransport(app=create_mock_srm_app())
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://mock-srm.test",
    ) as client:
        health = await client.get("/health/live")
        portal = await client.get("/")

    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "service": "autotask-mock-srm",
    }
    assert portal.status_code == 200
    assert portal.headers["cache-control"] == "no-store"
    assert 'id="login-button"' in portal.text
    assert 'id="search-button"' in portal.text
    assert 'id="human-check"' in portal.text
    assert SUCCESS_PO in portal.text


async def test_mock_srm_contract_download_is_deterministic() -> None:
    transport = httpx.ASGITransport(app=create_mock_srm_app())
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://mock-srm.test",
    ) as client:
        contract = await client.get(f"/contracts/{SUCCESS_PO}.pdf")
        missing = await client.get("/contracts/PO-NOT-FOUND.pdf")

    assert contract.status_code == 200
    assert contract.headers["content-type"] == "application/pdf"
    assert contract.headers["content-disposition"] == (
        f'attachment; filename="{SUCCESS_PO}-contract.pdf"'
    )
    assert contract.content.startswith(b"%PDF-1.4")
    assert SUCCESS_PO.encode() in contract.content
    assert missing.status_code == 404
