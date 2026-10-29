"""Rule-based feedback classifier: category, sentiment, priority."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

TOKEN_RE = re.compile(r"[a-z0-9']+")


@dataclass
class Classification:
    category: str
    department: str
    score: int
    matched_keywords: list[str] = field(default_factory=list)
    sentiment: str = "Neutral"
    priority: str = "Normal"
    needs_review: bool = False
    reason: str = ""


class FeedbackClassifier:
    def __init__(self, rules: dict):
        self.rules = rules
        self.default_category = rules["default_category"]
        self.default_department = rules["default_department"]
        self.min_score = rules.get("min_score_for_auto_route", 2)
        # Pre-compile one regex per keyword. Word boundaries stop "sue" matching "issue".
        self._categories = []
        for cat in rules["categories"]:
            compiled = [
                (kw, w, re.compile(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])"))
                for kw, w in cat["keywords"].items()
            ]
            self._categories.append((cat["name"], cat["department"], compiled))
        self._urgent = [
            re.compile(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])")
            for k in rules["urgent_keywords"]
        ]
        sent = rules["sentiment"]
        self._pos = set(sent["positive"])
        self._neg = set(sent["negative"])
        self._negators = set(sent["negators"])

    @classmethod
    def from_file(cls, path: str | Path) -> "FeedbackClassifier":
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    # --- individual rules -------------------------------------------------
    def score_categories(self, text: str) -> list[tuple[str, str, int, list[str]]]:
        """Return (category, department, score, matched) for every category with a hit,
        ranked best first. Ties keep rules.json order, so earlier = higher precedence."""
        results = []
        for name, dept, compiled in self._categories:
            hits = [(kw, w) for kw, w, rx in compiled if rx.search(text)]
            if hits:
                results.append((name, dept, sum(w for _, w in hits), [kw for kw, _ in hits]))
        results.sort(key=lambda r: -r[2])  # stable sort preserves rule order on ties
        return results

    def sentiment(self, text: str, rating: int | None = None) -> str:
        tokens = TOKEN_RE.findall(text)
        score = 0
        for i, tok in enumerate(tokens):
            polarity = 1 if tok in self._pos else -1 if tok in self._neg else 0
            if not polarity:
                continue
            if any(t in self._negators for t in tokens[max(0, i - 2):i]):
                polarity = -polarity
            score += polarity
        # The star rating, when present, is a strong tie-breaker.
        if rating is not None:
            score += 2 if rating >= 4 else -2 if rating <= 2 else 0
        return "Positive" if score > 0 else "Negative" if score < 0 else "Neutral"

    def priority(self, text: str, category: str, sentiment: str, rating: int | None) -> str:
        if any(rx.search(text) for rx in self._urgent) or category == "Legal & Privacy":
            return "Urgent"
        if sentiment == "Negative" and rating is not None and rating <= 2:
            return "High"
        if sentiment == "Negative":
            return "Normal"
        return "Low" if sentiment == "Positive" else "Normal"

    # --- public API -------------------------------------------------------
    def classify(self, text: str, rating: int | None = None) -> Classification:
        norm = " ".join(text.lower().split())
        ranked = self.score_categories(norm)
        sentiment = self.sentiment(norm, rating)

        if not ranked or ranked[0][2] < self.min_score:
            category, dept, score, matched = self.default_category, self.default_department, 0, []
            if ranked:
                matched = ranked[0][3]
            reason = "No strong keyword match; routed to default queue for manual review"
            review = True
        else:
            category, dept, score, matched = ranked[0]
            review = False
            reason = f"Matched: {', '.join(matched)}"
            # Close second place means the text spans two topics - flag, don't guess silently.
            if len(ranked) > 1 and ranked[1][2] == score:
                review = True
                reason += f" | tie with '{ranked[1][0]}' - review"

        prio = self.priority(norm, category, sentiment, rating)
        return Classification(category, dept, score, matched, sentiment, prio, review, reason)
