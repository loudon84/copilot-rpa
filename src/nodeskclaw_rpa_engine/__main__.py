from __future__ import annotations

import uvicorn

from nodeskclaw_rpa_engine.core.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run(
        "nodeskclaw_rpa_engine.main:app",
        host=settings.app_host,
        port=settings.app_port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
