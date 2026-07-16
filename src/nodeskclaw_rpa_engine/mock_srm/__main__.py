from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.getenv("MOCK_SRM_HOST", "127.0.0.1")
    port = int(os.getenv("MOCK_SRM_PORT", "4600"))
    if not 1 <= port <= 65535:
        raise ValueError("MOCK_SRM_PORT must be between 1 and 65535")
    uvicorn.run(
        "nodeskclaw_rpa_engine.mock_srm.app:app",
        host=host,
        port=port,
        log_level=os.getenv("MOCK_SRM_LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
