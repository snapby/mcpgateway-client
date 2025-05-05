import logging

from rich.logging import RichHandler


def setup_logger(level: str | int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(message)s", datefmt="[%X]", handlers=[RichHandler(rich_tracebacks=True)])
