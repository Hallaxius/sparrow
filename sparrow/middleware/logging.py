from __future__ import annotations

import logging

logger = logging.getLogger("sparrow")


class RequestLogger:

    def log_request(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
    ) -> None:
        logger.info(
            "%s %s %d %.1fms",
            method,
            path,
            status_code,
            duration_ms,
        )
