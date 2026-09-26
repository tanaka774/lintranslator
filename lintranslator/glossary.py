"""Term glossary: deterministic fixes on top of machine translation.

Machine translation gets names, titles and invented jargon wrong in ways that are
predictable and easy to correct. Measured examples from this project:

    Manager!  ->  管理者        (should be a title, not "administrator")
    Faust     ->  left alone, or transliterated inconsistently

Two kinds of replacement are needed, and they are not interchangeable:

* **pre**  - rewrite the source. Use for disambiguation, e.g. turning a bare
  "Manager" into "Executive Manager" so the model stops reading it as a job title.
* **post** - rewrite the output. Use when the model's rendering of a term is
  known and wrong: `管理者` -> `マネージャー` is a direct output fix.

A plain `"Term": "訳"` entry defaults to post, because fixing a known wrong
output is the common case and needs no model cooperation.

Matching is case-insensitive with word boundaries, and longer keys win so
"Executive Manager" is not clobbered by "Manager".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

Mapping = dict[str, str]


@dataclass
class Glossary:
    """Ordered term replacements, applied pre- and/or post-translation."""

    pre: Mapping = field(default_factory=dict)
    post: Mapping = field(default_factory=dict)
    case_sensitive: bool = False
    _pre_re: list[tuple[re.Pattern[str], str]] | None = field(default=None, repr=False)
    _post_re: list[tuple[re.Pattern[str], str]] | None = field(default=None, repr=False)

    # -- construction ------------------------------------------------------ #
    @classmethod
    def from_config(cls, raw: Any) -> "Glossary":
        """Build from the config's `translate.glossary` value.

        Accepted shapes:
            {"Manager": "マネージャー"}
            {"pre": {...}, "post": {...}}
        """
        if not raw:
            return cls()
        if not isinstance(raw, dict):
            raise ValueError("glossary must be a mapping")
        if "pre" in raw or "post" in raw:
            return cls(
                pre=dict(raw.get("pre") or {}),
                post=dict(raw.get("post") or {}),
                case_sensitive=bool(raw.get("case_sensitive", False)),
            )
        return cls(post={str(k): str(v) for k, v in raw.items()})

    # -- compilation ------------------------------------------------------- #
    @staticmethod
    def _compile(mapping: Mapping, case_sensitive: bool) -> list[tuple[re.Pattern[str], str]]:
        if not mapping:
            return []
        flags = 0 if case_sensitive else re.IGNORECASE
        # Longest key first so a specific phrase wins over its own substring.
        entries: list[tuple[re.Pattern[str], str]] = []
        for key in sorted(mapping, key=len, reverse=True):
            if not key:
                continue
            # Boundaries are ASCII-only on purpose. Python's \b treats CJK as
            # word characters, so a \b-anchored entry for 管理者 fails to match
            # inside 管理者異常 - the exact case this glossary exists to fix.
            # Restricting the lookaround to ASCII stops English terms matching
            # mid-word while letting Japanese terms match next to more Japanese.
            pattern = re.compile(
                r"(?<![A-Za-z0-9_])" + re.escape(key) + r"(?![A-Za-z0-9_])", flags
            )
            entries.append((pattern, mapping[key]))
        return entries

    def _pre_entries(self) -> list[tuple[re.Pattern[str], str]]:
        if self._pre_re is None:
            self._pre_re = self._compile(self.pre, self.case_sensitive)
        return self._pre_re

    def _post_entries(self) -> list[tuple[re.Pattern[str], str]]:
        if self._post_re is None:
            self._post_re = self._compile(self.post, self.case_sensitive)
        return self._post_re

    # -- application ------------------------------------------------------- #
    def apply_pre(self, text: str) -> str:
        """Rewrite the source text before translation."""
        for pattern, replacement in self._pre_entries():
            text = pattern.sub(replacement, text)
        return text

    def apply_post(self, text: str) -> str:
        """Rewrite translated output.

        Single pass: a replacement is never rescanned, so glossary entries
        cannot cascade into each other (A->B while B->C).
        """
        if not self._post_entries():
            return text
        parts: list[str] = []
        position = 0
        while position < len(text):
            earliest = None
            for pattern, replacement in self._post_entries():
                match = pattern.search(text, position)
                if match is None:
                    continue
                if earliest is None or match.start() < earliest[0].start():
                    earliest = (match, replacement)
            if earliest is None:
                parts.append(text[position:])
                break
            match, replacement = earliest
            parts.append(text[position : match.start()])
            parts.append(replacement)
            position = match.end()
        return "".join(parts)

    # -- introspection ----------------------------------------------------- #
    def __bool__(self) -> bool:
        return bool(self.pre or self.post)

    def __len__(self) -> int:
        return len(self.pre) + len(self.post)

    def describe(self) -> str:
        if not self:
            return "no glossary entries"
        bits = []
        if self.pre:
            bits.append(f"{len(self.pre)} pre")
        if self.post:
            bits.append(f"{len(self.post)} post")
        return "glossary: " + ", ".join(bits)

    @classmethod
    def merge(cls, *glossaries: "Glossary") -> "Glossary":
        """Combine glossaries; later entries win on conflicting keys."""
        pre: Mapping = {}
        post: Mapping = {}
        case_sensitive = False
        for g in glossaries:
            pre = {**pre, **g.pre}
            post = {**post, **g.post}
            case_sensitive = case_sensitive or g.case_sensitive
        return cls(pre=pre, post=post, case_sensitive=case_sensitive)


# --------------------------------------------------------------------------- #
# A starting glossary for Limbus Company.
#
# Deliberately small and conservative: only terms whose machine rendering was
# actually observed to be wrong. Guessing at flavour text does more harm than
# good, and every entry here is one a user can edit in config.json.
# --------------------------------------------------------------------------- #
LIMBUS_GLOSSARY = Glossary(
    post={
        # Measured: the title "Manager" comes back as the job word 管理者.
        # Patching the output the model actually produces is the reliable fix.
        "管理者": "マネージャー",
    },
)

# NOTE: there is deliberately no pre-map for "Manager". Rewriting the source was
# measured against the real model and made things worse, not better. The four
# inputs below are short fragments of in-game lines, quoted only as the evidence
# for that claim - a handful of words each, not script:
#
#   "Manager! The abnormality is approaching."  -> 管理者異常が近づいてる戦闘準備
#   "Executive Manager! The abnormality ..."    -> 管理者異常が近づいてる戦闘準備  (no change)
#   "Manager, the results are in."              -> 管理者結果が出ました
#   "Executive Manager, the results are in."    -> 経営責任者成果が届きました      (worse: "CEO")
#
# The post entry fixes the rendering deterministically, which is both cheaper and
# more predictable than steering the model.
