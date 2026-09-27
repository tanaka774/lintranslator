"""Term glossary: deterministic fixes on top of machine translation."""
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

    @classmethod
    def from_config(cls, raw: Any) -> "Glossary":
        """Build from the config's `translate.glossary` value."""
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

    @staticmethod
    def _compile(mapping: Mapping, case_sensitive: bool) -> list[tuple[re.Pattern[str], str]]:
        if not mapping:
            return []
        flags = 0 if case_sensitive else re.IGNORECASE
        # longest key first so a specific phrase wins over its own substring
        entries: list[tuple[re.Pattern[str], str]] = []
        for key in sorted(mapping, key=len, reverse=True):
            if not key:
                continue
            # ASCII-only lookaround on purpose: \b treats CJK as word characters,
            # so a \b-anchored 管理者 fails to match inside 管理者異常, and English
            # terms would otherwise match mid-word.
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

    def apply_pre(self, text: str) -> str:
        """Rewrite the source text before translation."""
        for pattern, replacement in self._pre_entries():
            text = pattern.sub(replacement, text)
        return text

    def apply_post(self, text: str) -> str:
        """Rewrite translated output in a single non-cascading pass."""
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


LIMBUS_GLOSSARY = Glossary(
    post={
        "管理者": "マネージャー",
    },
)

