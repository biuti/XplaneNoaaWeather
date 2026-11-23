import logging
import sys
import threading
import time

from . import xp, __version__

# alpha/beta builds log everything, releases only INFO and above
DEBUG = any(tag in __version__.lower() for tag in ("alpha", "beta"))

THROTTLE_SECONDS = 60.0  # an identical warning/error is shown at most once per this many seconds


class Formatter(logging.Formatter):
    """Level marker in front of the message, like the old ' * ' / ' *** ' prefixes."""
    MARKS = {
        logging.DEBUG: " * ",
        logging.WARNING: " ** ",
        logging.ERROR: " *** ",
        logging.CRITICAL: " *** ",
    }

    def format(self, record: logging.LogRecord) -> str:
        return f"[{record.module}]{self.MARKS.get(record.levelno, ' ')}{super().format(record)}"


class Throttle(logging.Filter):
    """Let an identical warning/error through at most once per `window` seconds.

    The first message after the window says how many were muted.
    Debug and info are never muted.
    """

    def __init__(self, window: float = THROTTLE_SECONDS) -> None:
        super().__init__()
        self.window = window
        self._seen: dict[tuple, list] = {}  # key -> [time of last emit, muted since then]
        self._lock = threading.Lock()  # handler filters run outside the handler lock

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.WARNING:
            return True

        message = record.getMessage()
        error = record.exc_info[1] if record.exc_info else None
        key = (record.pathname, record.lineno, message, repr(error))
        now = time.monotonic()

        with self._lock:
            if len(self._seen) > 256:  # keys can carry varying data: stay bounded
                self._seen.clear()
            last, muted = self._seen.get(key, (None, 0))
            if last is not None and now - last < self.window:
                self._seen[key] = [last, muted + 1]
                return False
            self._seen[key] = [now, 0]

        if muted:
            record.msg, record.args = "%s (%d more identical messages muted)", (message, muted)
        return True


class Handler(logging.Handler):
    """xp.log inside X-Plane, stderr in the server subprocess.

    sys.stderr is looked up at every emit: weatherserver.py swaps it for its own
    LogFile after startup, so the handler must not keep a reference to the old one.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            if xp:
                xp.log(message)
            else:
                print(message, file=sys.stderr)
        except Exception:
            self.handleError(record)


def setup() -> logging.Logger:
    """Configure the plugin logger. Idempotent: a plugin reload does not stack handlers."""
    logger = logging.getLogger("NOAAWeather")
    logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)
    logger.propagate = False

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()

    handler = Handler()
    handler.setFormatter(Formatter("%(message)s"))
    # filter repeated warning / error messages in a short time window, to avoid spamming the log
    handler.addFilter(Throttle())
    logger.addHandler(handler)

    logger.debug("logging to %s, level %s", "xp.log" if xp else "stderr", logging.getLevelName(logger.level))
    return logger
