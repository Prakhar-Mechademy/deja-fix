"""Conservative PII scrubbing before content enters shared knowledge."""
import re

EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
URL = re.compile(r"https?://\S+|www\.\S+", re.I)
ORDER = re.compile(r"\b(?:order|account|ticket|case|invoice)[\s:#-]*(?:no\.?|number)?[\s:#-]*[A-Z0-9-]{4,}\b", re.I)
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d .()\-]{7,}\d)(?!\w)")

def scrub_text(value: str) -> str:
    text = EMAIL.sub("[email removed]", value)
    text = URL.sub("[link removed]", text)
    text = ORDER.sub("[identifier removed]", text)
    text = PHONE.sub("[phone removed]", text)
    # Remove common direct-address personal names without attempting broad NER.
    text = re.sub(r"\b(?:my name is|i am|i'm)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?", "customer", text, flags=re.I)
    return text

def scrub_environment(environment: dict) -> dict:
    allowed = {"device", "device_type", "device_model", "operating_system", "os", "os_version",
               "app_version", "application_version", "application", "app", "browser", "network"}
    return {str(k): scrub_text(str(v)) for k, v in environment.items() if k.lower() in allowed and v is not None}
