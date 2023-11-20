from logging import DEBUG, NullHandler, getLogger
from __init__ import __version__

logger = getLogger("fast_bitrix24")
logger.setLevel(DEBUG)
logger.addHandler(NullHandler())

logger.debug(f"fast_bitrix24 version: {__version__}")

try:
    from IPython import get_ipython

    logger.debug(f"IPython: {get_ipython()}")
except ImportError:
    logger.debug("No IPython found")


def log(func):
    async def wrapper(*args, **kwargs):
        logger.info(f"Starting {func.__name__}({args}, {kwargs})")
        return await func(*args, **kwargs)

    return wrapper
