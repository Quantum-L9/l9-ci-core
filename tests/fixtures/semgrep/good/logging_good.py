import logging
logger = logging.getLogger(__name__)

def ok(trace_id: str):
    logger.info("request complete", extra={"trace_id": trace_id})
