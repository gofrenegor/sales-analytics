"""Request-local timings shared with FastAPI worker threads via contextvars."""

import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

timings = ContextVar("request_timings", default=None)


@contextmanager
def measure(name):
    started = time.perf_counter()
    try:
        yield
    finally:
        values = timings.get()
        if values is not None:
            values[name] = values.get(name, 0.0) + (time.perf_counter() - started) * 1000


def timed(name):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with measure(name):
                return function(*args, **kwargs)
        return wrapped
    return decorate
