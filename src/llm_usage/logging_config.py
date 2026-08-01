"""Logging configuration for debug mode and verbose output."""

from __future__ import annotations

import logging

from llm_usage.config import Settings
from llm_usage.quota import cache_dir


def setup_logging(settings: Settings) -> None:
    """Configure logging based on settings."""
    log_level = logging.DEBUG if settings.debug_mode or settings.verbose_logging else logging.INFO
    
    # Configure root logger
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    
    # If debug mode, also log to file
    if settings.debug_mode:
        log_dir = cache_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        
        log_file = log_dir / "llm-usage-debug.log"
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        
        root_logger = logging.getLogger()
        root_logger.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the specified name."""
    return logging.getLogger(name)


class DebugContext:
    """Context manager for debug logging of specific operations."""
    
    def __init__(self, operation: str, logger: logging.Logger | None = None):
        self.operation = operation
        self.logger = logger or get_logger("llm_usage")
    
    def __enter__(self):
        self.logger.debug(f"Starting: {self.operation}")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.logger.debug(f"Completed: {self.operation}")
        else:
            self.logger.error(f"Failed: {self.operation} - {exc_val}", exc_info=True)
        return False


def log_api_request(logger: logging.Logger, method: str, url: str, **kwargs) -> None:
    """Log API request details in debug mode."""
    logger.debug(f"API Request: {method} {url}")
    if kwargs:
        logger.debug(f"  Parameters: {kwargs}")


def log_api_response(logger: logging.Logger, status_code: int, response_time: float) -> None:
    """Log API response details in debug mode."""
    logger.debug(f"API Response: Status {status_code}, Time: {response_time:.3f}s")


def log_cache_operation(logger: logging.Logger, operation: str, key: str, hit: bool) -> None:
    """Log cache operation in debug mode."""
    result = "HIT" if hit else "MISS"
    logger.debug(f"Cache {operation}: {key} - {result}")
