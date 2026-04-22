import logging
import os
from logging.handlers import RotatingFileHandler

# Create logs directory if not exists
log_dir = "logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Helper function to configure a logger
def setup_logger(name, log_file, level):
    """Configure and return a logger."""
    log_path = os.path.join(log_dir, log_file)
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid duplicate handlers when Django reloads in development.
    has_file_handler = any(
        isinstance(existing_handler, RotatingFileHandler)
        and getattr(existing_handler, 'baseFilename', '') == os.path.abspath(log_path)
        for existing_handler in logger.handlers
    )
    if not has_file_handler:
        handler = RotatingFileHandler(log_path, maxBytes=500 * 1024 * 1024, backupCount=3)
        handler.setLevel(level)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.propagate = False  # Prevent duplicate logs

    return logger

# Master Logger - Logs Everything
master_logger = setup_logger("master_logger", "all_logs.log", logging.DEBUG)


def get_master_logger():
    """Return the shared application logger instance."""
    return master_logger


# Root logger setup (for general-purpose logging)
logging.basicConfig(level=logging.INFO)