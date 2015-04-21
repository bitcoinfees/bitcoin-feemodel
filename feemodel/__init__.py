import logging

# Default logging config: print to stderr with level DEBUG
logger = logging.getLogger(__name__)

formatter = logging.Formatter('[%(levelname)s] %(message)s')
handler = logging.StreamHandler()
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)
