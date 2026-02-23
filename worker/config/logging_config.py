"""
S6: Structured Logging for Worker
Provides JSON-formatted logs in production, colored console in development.
Usage:
    from config.logging_config import get_logger
    logger = get_logger(__name__)
    logger.info("Processing job", job_id=job_id, step="download")
"""
import os
import sys
import json
import logging
from datetime import datetime, timezone


class StructuredFormatter(logging.Formatter):
    """JSON formatter for production, human-readable for development."""

    def __init__(self, service="viralengine-worker", json_format=False):
        super().__init__()
        self.service = service
        self.json_format = json_format

    def format(self, record):
        log_data = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "level": record.levelname,
            "service": self.service,
            "message": record.getMessage(),
        }

        # Add extra fields (job_id, step, etc.)
        if hasattr(record, "_extra"):
            log_data.update(record._extra)

        # Add exception info
        if record.exc_info and record.exc_info[0]:
            log_data["error"] = str(record.exc_info[1])
            log_data["traceback"] = self.formatException(record.exc_info)

        if self.json_format:
            return json.dumps(log_data, ensure_ascii=False)
        else:
            # Human-readable format
            extra_str = ""
            extra_keys = {k: v for k, v in log_data.items() 
                         if k not in ("timestamp", "level", "service", "message", "error", "traceback")}
            if extra_keys:
                extra_str = " " + " ".join(f"{k}={v}" for k, v in extra_keys.items())

            base = f"{log_data['timestamp']} [{self.service}] {record.levelname}: {log_data['message']}{extra_str}"
            
            if "error" in log_data:
                base += f"\n  Error: {log_data['error']}"
            
            return base


class ContextAdapter(logging.LoggerAdapter):
    """Adapter that passes extra kwargs as structured fields."""

    def process(self, msg, kwargs):
        extra = kwargs.pop("extra", {})
        # Merge any direct kwargs into the extra dict
        extra.update({k: v for k, v in kwargs.items() if k not in ("exc_info", "stack_info", "stacklevel")})
        kwargs = {k: v for k, v in kwargs.items() if k in ("exc_info", "stack_info", "stacklevel")}
        kwargs["extra"] = {"_extra": {**self.extra, **extra}}
        return msg, kwargs


def get_logger(name: str = "worker") -> ContextAdapter:
    """Create a structured logger instance."""
    is_production = os.getenv("ENVIRONMENT", "development") == "production"
    
    logger = logging.getLogger(f"viralengine.{name}")
    
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredFormatter(
            service=f"viralengine-{name}",
            json_format=is_production
        ))
        logger.addHandler(handler)
        logger.setLevel(getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO))
        logger.propagate = False

    return ContextAdapter(logger, {})
