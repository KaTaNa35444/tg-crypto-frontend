"""Redact credentials and suppress exception payloads before formatting logs."""
import logging
import re

class Redact(logging.Filter):
    def __init__(self,secrets): super().__init__(); self.secrets=tuple(x for x in secrets if x)
    def filter(self,record):
        text=record.getMessage()
        for value in self.secrets: text=text.replace(value,'[REDACTED]')
        record.msg=re.sub(r'\b\d+:[A-Za-z0-9_-]{20,}\b','[REDACTED]',text)
        record.args=()
        if record.exc_info:
            record.msg+=' [exception='+record.exc_info[0].__name__+']'
            record.exc_info=None; record.exc_text=None
        return True

def configure(settings):
    handler=logging.StreamHandler()
    handler.addFilter(Redact([settings.bot_token,settings.database_url]))
    handler.setFormatter(logging.Formatter('%(levelname)s %(name)s: %(message)s'))
    logging.basicConfig(level=logging.INFO,handlers=[handler],force=True)
