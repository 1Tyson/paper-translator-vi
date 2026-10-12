"""Fixed translations for technical terms, applied before the translator sees the text.

Terms become placeholders, so every paper uses the same Vietnamese for them:
- `translate`: the first occurrence becomes "Vietnamese (English)", later ones only "Vietnamese".
- `keep`: stays in English (the translator never sees it).
"""

import os
import re

import yaml

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "glossary.yaml")


class Glossary:
    def __init__(self, translate=None, keep=None):
        self.translate = {k.lower(): v for k, v in (translate or {}).items()}
        self.keep = {k.lower() for k in (keep or [])}
        terms = sorted(set(self.translate) | self.keep, key=len, reverse=True)
        self.pattern = None
        if terms:
            alts = "|".join(re.escape(t) for t in terms)
            # Optional plural; no match inside a longer word or a placeholder.
            self.pattern = re.compile(r"(?<![\w\[-])(" + alts + r")(?:e?s)?(?![\w-])", re.IGNORECASE)
        self.seen = set()

    @classmethod
    def load(cls, path=DEFAULT_PATH):
        if not path:
            return cls()
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(data.get("translate"), data.get("keep"))

    def reset(self):
        self.seen = set()

    def apply(self, masked):
        """Replace terms in `masked` (a latex.Masked) with new placeholders."""
        if self.pattern is None:
            return
        text, masked.parts = masked.text, []
        pos = 0
        for mt in self.pattern.finditer(text):
            masked.parts.append(text[pos : mt.start()])
            term = mt.group(1).lower()
            if term in self.translate:
                vi = self.translate[term]
                masked.ph(vi if term in self.seen else f"{vi} ({mt.group(0)})")
                self.seen.add(term)
            else:
                masked.ph(mt.group(0))
            pos = mt.end()
        masked.parts.append(text[pos:])
