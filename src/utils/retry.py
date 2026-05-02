"""
Exponential-backoff retry decorator.
Usage:
    @retry(max_attempts=3, backoff=2.0, exceptions=(requests.RequestException,))
    def my_api_call():
        ...
"""
import functools
import logging
import time
from typing import Callable, Tuple, Type

logger = logging.getLogger(__name__)


def retry(
    max_attempts: int = 3,
    backoff: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
) -> Callable:
    """
    Decorator that retries the wrapped function on specified exceptions.

    Args:
        max_attempts: Total number of tries including the first attempt.
        backoff:      Multiplier for wait time: wait = backoff^(attempt-1) seconds.
        exceptions:   Tuple of exception classes that trigger a retry.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc: Exception = RuntimeError("retry never executed")
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        logger.error(
                            "Function '%s' failed after %d attempts. Last error: %s",
                            func.__name__,
                            max_attempts,
                            exc,
                        )
                        raise
                    wait = backoff ** (attempt - 1)
                    logger.warning(
                        "Function '%s' attempt %d/%d failed: %s — retrying in %.1fs",
                        func.__name__,
                        attempt,
                        max_attempts,
                        exc,
                        wait,
                    )
                    time.sleep(wait)
            raise last_exc  # unreachable; satisfies type checkers
        return wrapper
    return decorator
