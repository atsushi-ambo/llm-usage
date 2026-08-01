"""Retry logic for API failures with exponential backoff."""

from __future__ import annotations

import time
from typing import Any, Callable

import httpx


class RetryConfig:
    """Configuration for retry behavior."""
    
    def __init__(
        self,
        max_retries: int = 3,
        initial_delay: float = 1.0,
        max_delay: float = 30.0,
        backoff_factor: float = 2.0,
        retryable_status_codes: set[int] | None = None,
    ):
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor
        self.retryable_status_codes = retryable_status_codes or {
            429,  # Too Many Requests
            500,  # Internal Server Error
            502,  # Bad Gateway
            503,  # Service Unavailable
            504,  # Gateway Timeout
        }


def with_retry(
    func: Callable[..., Any],
    config: RetryConfig | None = None,
) -> Callable[..., Any]:
    """Decorator to add retry logic to a function.
    
    The function should raise httpx.HTTPStatusError for HTTP errors
    that should trigger retries.
    """
    if config is None:
        config = RetryConfig()
    
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        last_exception = None
        delay = config.initial_delay
        
        for attempt in range(config.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except httpx.HTTPStatusError as e:
                last_exception = e
                if e.response.status_code not in config.retryable_status_codes:
                    # Non-retryable status code, raise immediately
                    raise
                if attempt == config.max_retries:
                    # Max retries reached
                    raise
            except (httpx.RequestError, httpx.TimeoutException) as e:
                last_exception = e
                if attempt == config.max_retries:
                    raise
            except Exception:  # noqa: BLE001
                # Unexpected error, don't retry
                raise
            
            # Exponential backoff
            time.sleep(min(delay, config.max_delay))
            delay *= config.backoff_factor
        
        # Should never reach here, but just in case
        if last_exception:
            raise last_exception
    
    return wrapper


def retry_http_request(
    client: httpx.Client,
    method: str,
    url: str,
    config: RetryConfig | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """Make an HTTP request with retry logic.
    
    This is a convenience function for simple HTTP requests.
    """
    if config is None:
        config = RetryConfig()
    
    last_exception = None
    delay = config.initial_delay
    
    for attempt in range(config.max_retries + 1):
        try:
            response = client.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as e:
            last_exception = e
            if e.response.status_code not in config.retryable_status_codes:
                raise
            if attempt == config.max_retries:
                raise
        except (httpx.RequestError, httpx.TimeoutException) as e:
            last_exception = e
            if attempt == config.max_retries:
                raise
        except Exception:  # noqa: BLE001
            raise
        
        time.sleep(min(delay, config.max_delay))
        delay *= config.backoff_factor
    
    if last_exception:
        raise last_exception
    
    raise RuntimeError("Unexpected error in retry logic")
