import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


RESET = "\x1b[0m"
BOLD = "\x1b[1m"

LEVEL_COLORS = {
    logging.DEBUG:    "\x1b[36m",   # cyan
    logging.INFO:     "\x1b[32m",   # green
    logging.WARNING:  "\x1b[33m",   # yellow
    logging.ERROR:    "\x1b[31m",   # red
    logging.CRITICAL: "\x1b[35m",   # magenta
}


class ColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        color = LEVEL_COLORS.get(record.levelno, RESET)
        record.levelname = f"{color}{BOLD}{record.levelname:<8}{RESET}"
        record.name = f"\x1b[34m{record.name}{RESET}"
        return super().format(record)


def get_logger(
    name: str,
    level: int = logging.DEBUG,
    log_file: str | None = "app.log",
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(
        ColorFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s — %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    logger.addHandler(console)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        rotator = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        rotator.setLevel(level)
        rotator.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(rotator)

    return logger
