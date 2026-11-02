"""Untrusted-content handling for inbound email.

Layer 1 (here): deterministic heuristics that catch common injection phrasing, hidden characters
and exfiltration asks. Layer 2: the Guard agent classifies the *datamarked* text. Layer 3 is
structural: no agent has a tool that approves, sends or spends, so a successful injection can at
worst distort a draft that a human still has to approve.
"""

import re
import unicodedata

MARK = "^"  # Spotlighting "datamarking" (Hines et al., 2024): interleave a marker between words.

_PATTERNS: list[tuple[str, str, str]] = [
    # (signal id, severity, regex)
    ("override_instructions", "high", r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|all|earlier|system)\b[^.\n]{0,20}\b(instructions?|prompts?|rules?|guidelines?)"),
    ("role_spoof", "high", r"(^|\n)\s*(system|assistant|developer)\s*:"),
    ("persona_switch", "medium", r"\byou are (now|no longer)\b|\bact as\b|\bdeveloper mode\b|\bjailbreak\b"),
    ("force_approval", "high", r"\b(mark|set|flag)\b[^.\n]{0,30}\b(as )?(approved|resolved|closed)\b|\bauto[- ]?approve\b|\bapprove (it|this|immediately|now) without\b"),
    ("exfiltration", "high", r"\b(bank (account|details)|ifsc|password|api key|credentials|otp)\b[^.\n]{0,60}\b(send|share|email|forward)\b|\b(send|share|email|forward)\b[^.\n]{0,60}\b(bank (account|details)|ifsc|password|api key|credentials|otp)\b"),
    ("secrecy", "medium", r"\b(do not|don't) (tell|inform|notify|mention)\b|\bkeep this (secret|confidential) from\b"),
    ("prompt_reveal", "medium", r"\b(reveal|print|show)\b[^.\n]{0,20}\b(system prompt|instructions|hidden)\b"),
    ("markup_payload", "medium", r"<\s*(script|iframe|img[^>]+onerror)|javascript:"),
]
_COMPILED = [(sid, sev, re.compile(rx, re.I)) for sid, sev, rx in _PATTERNS]
_INVISIBLE = {"Cf"}  # zero-width and bidi control characters


def scan(text: str) -> list[dict]:
    """Return matched signals with the offending span, for highlighting in the UI."""
    signals: list[dict] = []
    for sid, sev, rx in _COMPILED:
        for m in rx.finditer(text):
            signals.append({"signal": sid, "severity": sev, "span": m.group(0).strip()[:160], "source": "heuristic"})
            break
    hidden = [c for c in text if unicodedata.category(c) in _INVISIBLE]
    if hidden:
        signals.append({"signal": "hidden_characters", "severity": "medium", "span": f"{len(hidden)} invisible chars", "source": "heuristic"})
    if re.search(r"[A-Za-z0-9+/]{120,}={0,2}", text):
        signals.append({"signal": "encoded_blob", "severity": "medium", "span": "long base64-like run", "source": "heuristic"})
    return signals


def heuristic_label(signals: list[dict]) -> str:
    if any(s["severity"] == "high" for s in signals):
        return "malicious"
    if signals:
        return "suspicious"
    return "safe"


def strip_invisible(text: str) -> str:
    return "".join(c for c in text if unicodedata.category(c) not in _INVISIBLE)


def datamark(text: str) -> str:
    """Replace inter-word whitespace with the marker, preserving line breaks for readability."""
    lines = strip_invisible(text).split("\n")
    return "\n".join(MARK.join(line.split()) for line in lines)


def unmark(text: str) -> str:
    return text.replace(MARK, " ")


LABEL_RANK = {"safe": 0, "suspicious": 1, "malicious": 2}


def combine(a: str, b: str | None) -> str:
    if b is None:
        return a
    return a if LABEL_RANK[a] >= LABEL_RANK[b] else b
