import logging
logger = logging.getLogger(__name__)

def bad(api_key: str, raw_response: str):
    logger.info("api key is %s", api_key)
    logger.debug(f"raw_response={raw_response}")
