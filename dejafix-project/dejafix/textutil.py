"""Deterministic text helpers: fault detection, similarity, environment parsing,
PII scrubbing, and confirmation detection.

Rule-based on purpose: predictable, testable offline. The LLM (when configured) layers on top
(judging borderline matches, doing extraction); these helpers are the guard rails around it.
"""
from __future__ import annotations

import math
import re
from collections import Counter

# --------------------------------------------------------------------------- tokens
STOPWORDS = set(
    """a an the and or but if then so of to in on at by for with from into onto over under about as is are
    was were be been being am do does did done doing have has had having i me my mine we our you your it its
    this that these those there here they them their he she his her what which who whom when where why how
    can could would should will shall may might must not no nor never than too very just also only own same
    other such some any each every all both few more most up down out off again further once now still yet
    even ever one two""".split()
)
GENERIC = set(
    """app apps application device devices issue issues problem problems help please thanks thank hi hello hey
    tried try trying want need like get got getting lately recently suddenly anymore really always sometimes
    time thing things something anything work working works worked doesn don didn won isn aren couldn wouldn
    shouldn ll ve re t d m keep keeps kept stopped stop""".split()
)
_TOKEN = re.compile(r"[a-z][a-z0-9]*")


def normalize(text: str) -> str:
    return text.lower().replace("\u2019", "'").replace("wi-fi", "wifi").replace("wi fi", "wifi")


def stem(w: str) -> str:
    if len(w) <= 3:
        return w
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    else:
        for suf in ("ing", "ed", "s"):
            if w.endswith(suf) and len(w) - len(suf) >= 3:
                w = w[: -len(suf)]
                break
    if w.endswith("e") and len(w) > 4:
        w = w[:-1]
    return w


def content_tokens(text: str) -> list[str]:
    return [stem(w) for w in _TOKEN.findall(normalize(text))
            if w not in STOPWORDS and w not in GENERIC and len(w) >= 2]


def bag(text: str) -> Counter:
    return Counter(content_tokens(text))


def similarity(a: Counter, b: Counter) -> float:
    """Cosine similarity of two token bags (0..1)."""
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def text_sim(a: str, b: str) -> float:
    return similarity(bag(a), bag(b))


# --------------------------------------------------------------------------- fault types
def _rx(*patterns: str) -> list[re.Pattern]:
    return [re.compile(p) for p in patterns]


_NEG = r"(?:not|never|no longer|stopped|stops?|won'?t|doesn'?t|don'?t|isn'?t|aren'?t|can'?t|cannot|fail\w*|refus\w*|unable to)"
FAULT_RULES: dict[str, list[re.Pattern]] = {
    "sync": _rx(
        r"\bsync\w*", r"out of sync", r"\bstale\b",
        _NEG + r"\b(?:\s+\w+){0,3}?\s+(?:updat\w*|refresh\w*|show(?:ing)?\s+up|turn(?:s|ing)?\s+up|appear\w*"
               r"|come\s+through|carry\s+over|match\w*|mirror\w*)",
    ),
    "crash": _rx(
        r"\bcrash\w*", r"\bkeeps?\s+(?:closing|quitting|crashing|freezing|restarting)",
        r"\bforce[- ]?(?:clos|quit)\w*", r"\bfreez\w*|\bfroze\b", r"\bcloses?\s+(?:itself|by itself|immediately)",
        r"(?:won'?t|doesn'?t|not|never)\s+(?:open|launch|start)\b",
    ),
    "login": _rx(
        r"\blog[\s-]?in\b", r"\blogging\s+in\b", r"\bsign(?:ing)?[\s-]?in\b", r"\bpassword\w*",
        r"\blocked out\b", r"\b2fa\b|two[- ]factor|verification code",
    ),
    "wifi": _rx(r"\bwifi\b", r"\binternet\b", r"\bconnect\w*\s+(?:to\s+)?(?:the\s+)?network"),
    "bluetooth": _rx(r"\bbluetooth\b", r"\bpair(?:ing|ed)?\b"),
    "print": _rx(r"\bprint\w*"),
    "battery": _rx(r"\bbattery\b", r"\bdrain\w*", r"\bcharg(?:e|es|ing|er)\b"),
    "slow": _rx(r"\bslow\w*", r"\blag(?:s|gy|ging)?\b", r"\bsluggish\b", r"takes? (?:forever|ages)"),
    "notify": _rx(r"\bnotification\w*", r"\balerts?\b"),
    "install": _rx(r"\binstall\w*", r"(?:app|software|ios|os|system)\s+updat\w*",
                   r"\bupdat\w*\s+(?:fail\w*|stuck)", r"(?:won'?t|can'?t|cannot|unable to)\s+(?:\w+\s+){0,2}download"),
}
_FINE = re.compile(r"\b\w+\s+(?:is|are|works?|working|seems?|looks?)\s+(?:fine|ok|okay|good|great|normal)\b")

# fault -> (predicate, default subject, subject is the app)
FAULT_TITLE = {
    "sync": ("not syncing across devices", "Data", True),
    "crash": ("keeps crashing", "App", True),
    "login": ("login or sign-in problem", "Account", True),
    "wifi": ("Wi-Fi connection problem", "Device", False),
    "bluetooth": ("Bluetooth pairing problem", "Device", False),
    "print": ("printing problem", "Printer", False),
    "battery": ("battery problem", "Device", False),
    "slow": ("running slowly", "App", True),
    "notify": ("notifications not arriving", "App", True),
    "install": ("install or update problem", "App", True),
}
FAULT_NOUN = {"sync": "sync", "crash": "crashing", "login": "login", "wifi": "Wi-Fi", "bluetooth": "Bluetooth",
              "print": "printing", "battery": "battery", "slow": "performance", "notify": "notification",
              "install": "installation"}


def detect_faults(text: str) -> set[str]:
    t = _FINE.sub(" ", normalize(text))
    return {f for f, rules in FAULT_RULES.items() if any(r.search(t) for r in rules)}


def title_for(faults, app: str | None) -> str:
    f = sorted(faults)[0] if faults else None
    if not f:
        return f"{app} problem" if app else "Technical problem"
    pred, subject, uses_app = FAULT_TITLE[f]
    if uses_app and app:
        return f"{app} {pred}"
    return f"{subject} {pred}" if uses_app else (pred[0].upper() + pred[1:])


def label_for(faults, app: str | None, text: str = "") -> str:
    """Short, generic label used in the 'welcome back' greeting."""
    f = sorted(faults)[0] if faults else None
    noun = FAULT_NOUN.get(f, "technical")
    return f"{app + ' ' if app else ''}{noun} problem" if f else (f"{app} problem" if app else "technical problem")


# --------------------------------------------------------------------------- environment
_CANON = {"iphone": "iPhone", "ipad": "iPad", "pixel": "Pixel", "galaxy": "Galaxy", "macbook": "MacBook",
          "oneplus": "OnePlus"}
_DEVICE = re.compile(
    r"\b(iphone\s?(?:se|\d{1,2})(?:\s?(?:pro max|pro|plus|mini))?|ipad(?:\s?(?:pro|air|mini))?(?:\s?\d{1,2})?"
    r"|pixel\s?\d{1,2}(?:\s?(?:pro|a))?|galaxy\s?[a-z]\d{1,2}\w*|macbook(?:\s?(?:pro|air))?|oneplus\s?\d+\w*)\b", re.I)
_OS = re.compile(r"\b(ios|ipados|android|windows|macos|ubuntu|linux)\b\s*(\d+(?:\.\d+)*)?", re.I)
_OS_CANON = {"ios": "iOS", "ipados": "iPadOS", "android": "Android", "windows": "Windows", "macos": "macOS",
             "ubuntu": "Ubuntu", "linux": "Linux"}
_VERSION = re.compile(r"(?:\bv|\bversion\s+)(\d+(?:\.\d+){1,3})\b", re.I)
_APP = re.compile(r"\b([A-Z][A-Za-z0-9]{2,})\s+(?i:app)\b")
_APP_SKIP = {"The", "This", "That", "Your", "Our", "Mobile", "Android", "Desktop", "Web", "Phone", "Which"}


def parse_environment(text: str) -> dict:
    env: dict = {}
    m = _DEVICE.search(text)
    if m:
        d = re.sub(r"\s+", " ", m.group(1)).strip()
        first = d.split(" ")[0]
        key = re.match(r"[a-z]+", first.lower()).group(0)
        rest = d[len(key):].strip()
        env["device"] = f"{_CANON.get(key, first)} {rest}".strip()
    m = _OS.search(text)
    if m:
        env["os"] = f"{_OS_CANON[m.group(1).lower()]} {m.group(2) or ''}".strip()
    for m in _VERSION.finditer(text):
        if re.search(r"(?:ios|android|os|windows)\s*$", text[max(0, m.start() - 12):m.start()], re.I):
            continue
        env["app_version"] = m.group(1)
        break
    for m in _APP.finditer(text):
        if m.group(1) not in _APP_SKIP:
            env["app"] = m.group(1)
            break
    return env


def _family(env: dict) -> str | None:
    s = f"{env.get('os', '')} {env.get('device', '')}".lower()
    if re.search(r"ios|ipados|iphone|ipad|macos|macbook", s):
        return "apple"
    if re.search(r"android|pixel|galaxy|oneplus", s):
        return "android"
    if "windows" in s:
        return "windows"
    return None


def env_conflict(a: dict, b: dict) -> bool:
    """True when the two environments are clearly different platforms (e.g. iOS vs Android)."""
    fa, fb = _family(a or {}), _family(b or {})
    return bool(fa and fb and fa != fb)


def env_text(env: dict) -> str:
    return " ".join(str(env[k]) for k in ("device", "os", "app_version", "app") if env.get(k))


# --------------------------------------------------------------------------- PII scrubbing
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"']*[^\s<>\"'.,;:!?)]")
_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{7,}\d(?![\w.])")
_ID_KW = re.compile(r"(?i)\b(order|account|acct|customer|ticket|case|invoice|reference|ref|booking|confirmation)\b"
                    r"\s*(?:no\.?|number|num|id|#)?\s*[:#]?\s*(?=[A-Za-z0-9-]*\d)[A-Za-z0-9][A-Za-z0-9-]{2,}")
_ID_BARE = re.compile(r"#?\b[A-Z]{2,5}-\d{3,}\b|#\d{3,}")
_SELF_NAME = re.compile(r"(?i:\b(?:i'?m|i am|my name is|my name'?s))\s+"
                        r"(?!(?:Having|Trying|Getting|Not|Unable|Using|Currently|Still|Facing|Experiencing)\b)"
                        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b")


def scrub(text: str, names=()) -> str:
    """Remove emails, links, IPs, phone numbers, order/account ids and names. Keeps device/OS/app versions."""
    t = _EMAIL.sub("[email]", text or "")
    t = _URL.sub("[link]", t)
    t = _IP.sub("[ip]", t)
    t = _ID_KW.sub(lambda m: f"{m.group(1)} [id]", t)
    t = _ID_BARE.sub("[id]", t)
    t = _PHONE.sub("[phone]", t)
    t = _SELF_NAME.sub(lambda m: m.group(0)[: m.start(1) - m.start(0)] + "[name]", t)
    parts = set()
    for n in names or ():
        n = (n or "").strip()
        if not n:
            continue
        parts.add(n)
        parts.update(p for p in n.split() if len(p) >= 3)
    for p in sorted(parts, key=len, reverse=True):
        t = re.sub(rf"(?i)(?<!\w){re.escape(p)}(?!\w)", "[name]", t)
    return t


def scrub_obj(obj, names=()):
    if isinstance(obj, str):
        return scrub(obj, names)
    if isinstance(obj, list):
        return [scrub_obj(x, names) for x in obj]
    if isinstance(obj, dict):
        return {k: scrub_obj(v, names) for k, v in obj.items()}
    return obj


_GREET = re.compile(r"(?i)^\s*(?:hi|hello|hey|good (?:morning|afternoon|evening))\b[\s,!.-]*(?:there\b[\s,!.-]*)?")
_FILLER = {"email", "order", "phone", "account", "name", "number", "id", "contact", "ref", "reference", "link"}


def clean_sentences(text: str, names=()) -> list[str]:
    """Scrubbed, de-greeted sentences of a customer message that carry real problem content."""
    out = []
    for s in re.split(r"(?<=[.!?])\s+|\n+", scrub(text, names)):
        s = _GREET.sub("", s).strip()
        if not s:
            continue
        has_ph = bool(re.search(r"\[(?:name|email|id|phone|link|ip)\]", s))
        toks = [t for t in content_tokens(re.sub(r"\[[a-z]+\]", " ", s)) if t not in _FILLER]
        if (has_ph and len(toks) < 2) or len(toks) < 1:
            continue
        out.append(s)
    return out


# --------------------------------------------------------------------------- feedback / intent
_POS = _rx(
    r"\bfixed\b", r"\bsolved\b", r"\bresolved\b", r"\bsorted\b", r"\bit worked\b", r"\bthat worked\b",
    r"\bthat did it\b", r"\bdid the trick\b", r"\bworks now\b", r"\ball good\b", r"\bproblem(?:'s)? gone\b",
    r"\bback to normal\b", r"\bworking (?:now|again|fine|properly|correctly)\b",
    r"\b\w+ing (?:again|now|properly|correctly)\b", r"\bthanks?,? (?:that|it) (?:worked|helped|fixed)",
    r"\bthat helped\b", r"\bthat (?:fixed|solved) it\b",
    r"\b(?:lasting|holding|running) (?:much )?longer(?: now)?\b",
)
_NEGP = _rx(
    r"\b(?:didn'?t|did not|doesn'?t|does not|isn'?t|is not|wasn'?t|was not|not|never|no)\s+(?:\w+\s+){0,2}?"
    r"(?:help|helped|work|worked|working|fix|fixed|solve|solved|resolve|resolved|change|better)\b",
    r"\bstill (?:not|no|broken|happening|the same|doesn'?t|can'?t|won'?t|nothing|failing)",
    r"\bnope\b", r"\bno luck\b", r"\bsame (?:problem|issue|thing|error)\b", r"\bno change\b", r"\bgot worse\b",
    r"\bworked (?:for|just for) (?:a|some|about)\s+\w+\s*(?:minute|while|moment|bit|day|hour)s?",
    r"\bbut (?:then )?(?:it )?(?:stopped|broke|failed|came back|is back|died)\b",
)
_TRY_LATER = _rx(r"\blet me try\b", r"\bi'?ll try\b", r"\bi will try\b", r"\bgoing to try\b", r"\bgonna try\b",
                 r"\bwill try (?:it|that|this)\b", r"\bgive (?:it|that|this) a (?:go|try|shot)\b",
                 r"\btrying (?:it|that|this) (?:now|out)\b")
_THANKS_WORDS = {"thanks", "thank", "you", "so", "much", "a", "lot", "very", "ok", "okay", "cheers", "thx", "ty",
                 "great", "cool", "nice", "awesome", "perfect", "appreciate", "it"}


def is_try_later(text: str) -> bool:
    t = normalize(text)
    return any(r.search(t) for r in _TRY_LATER)


def classify_feedback(text: str) -> str:
    """'confirmed' | 'failed' | 'unclear'. When both signals appear, whichever comes LAST wins."""
    t = normalize(text)
    if is_try_later(t):
        return "unclear"
    last_pos = max((m.end() for r in _POS for m in r.finditer(t)), default=-1)
    last_neg = max((m.end() for r in _NEGP for m in r.finditer(t)), default=-1)
    if last_neg < 0 and last_pos < 0:
        return "unclear"
    if last_pos > last_neg:
        return "confirmed"
    return "failed"


def is_thanks_only(text: str) -> bool:
    toks = re.findall(r"[a-z]+", normalize(text))
    return bool(toks) and set(toks) <= _THANKS_WORDS and any(t in ("thanks", "thank", "thx", "ty", "cheers") for t in toks)


_SMALLTALK = re.compile(r"(?i)^\s*(?:hi|hello|hey|hiya|yo|good (?:morning|afternoon|evening)|how are you|"
                        r"can you help(?: me)?|i need help|help|hi there|hello there)[\s!.,?]*$")


def is_smalltalk(text: str) -> bool:
    return bool(_SMALLTALK.match(text or "")) or is_thanks_only(text)


_NEGANS = re.compile(r"(?i)^\s*(?:no|nope|nah|not really|not quite)\b|not the same|different (?:problem|issue|one)|"
                     r"that'?s not (?:it|my)|something else")


def is_negative_answer(text: str) -> bool:
    return bool(_NEGANS.search(text or ""))


_JAIL = _rx(
    r"ignore\s+(?:all\s+|any\s+|your\s+|the\s+|previous\s+|prior\s+|above\s+|these\s+){1,3}(?:instructions?|rules?|prompts?|guidelines?)",
    r"disregard\s+(?:all\s+|any\s+|your\s+|the\s+|previous\s+){1,3}(?:instructions?|rules?|prompts?|guidelines?)",
    r"forget\s+(?:all\s+|any\s+|your\s+|the\s+|previous\s+){1,3}(?:rules?|instructions?|prompts?|guidelines?)",
    r"answer\s+from\s+your\s+own\s+knowledge", r"\bown knowledge\b", r"\bjailbreak\b", r"\bdeveloper mode\b",
    r"(?:reveal|show|print)\s+(?:me\s+)?(?:your\s+)?(?:system\s+)?prompt", r"\byou are now\b", r"\bpretend (?:to be|you)\b",
    r"\bact as (?:a|an|if)\b", r"\bno restrictions\b",
)


def is_jailbreak(text: str) -> bool:
    t = normalize(text)
    return any(r.search(t) for r in _JAIL)
