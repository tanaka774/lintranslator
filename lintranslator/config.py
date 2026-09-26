"""Configuration model with JSON persistence.

Regions can be stored either as absolute pixels or as normalised fractions of
the screen. Normalised is the default because Limbus Company runs at a different
resolution on different setups, and the dialogue box scales with it.

Where the file lives, and why it is 0600, is `lintranslator.paths`.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from . import paths
from .paths import APP_DIR, DEFAULT_CONFIG_PATH, DATA_DIR  # noqa: F401 - re-exported


@dataclass
class Region:
    """A rectangle, in pixels or as normalised fractions."""

    x: float
    y: float
    w: float
    h: float
    mode: str = "pixels"  # "pixels" | "fraction"

    def to_pixels(self, screen_w: int, screen_h: int) -> tuple[int, int, int, int]:
        if self.mode == "fraction":
            x = round(self.x * screen_w)
            y = round(self.y * screen_h)
            w = round(self.w * screen_w)
            h = round(self.h * screen_h)
        else:
            x, y, w, h = round(self.x), round(self.y), round(self.w), round(self.h)
        # Clamp into the screen so a bad config can't produce an empty/negative box.
        x = max(0, min(x, screen_w - 1))
        y = max(0, min(y, screen_h - 1))
        w = max(1, min(w, screen_w - x))
        h = max(1, min(h, screen_h - y))
        return x, y, w, h


@dataclass
class CaptureConfig:
    backend: str = "portal-screenshot"  # "portal-screenshot" | "portal-screencast"
    fps: float = 2.0
    # A starting point, not a measurement: a wide band across the lower part of
    # the screen, which is where dialogue boxes usually sit. The picker replaces
    # it the first time one is chosen; this used to be the dialogue box of the
    # reference screenshot, which made the default one game on one monitor.
    region: Region = field(
        default_factory=lambda: Region(0.10, 0.78, 0.80, 0.12, "fraction")
    )


@dataclass
class OcrConfig:
    engine: str = "tesseract"  # "tesseract" | "rapidocr"
    langs: str = "eng"
    psm: int = 6
    upscale: float = 3.0
    autocontrast: bool = True
    # extra user patterns, e.g. "/usr/share/tessdata"
    tessdata_dir: str | None = None
    min_confidence: float = 40.0
    # Language data is fetched on first use, but only for languages with a pinned
    # checksum (see `lintranslator.ocr.TESSDATA_SHA256`). Set this to also download a
    # language that has none, accepting it unverified.
    allow_unverified_tessdata: bool = False


@dataclass
class TranslateConfig:
    # Nothing, by default: every other backend either spends gigabytes before it
    # can say a word (ct2, local) or sends the text read off the screen to somebody
    # (deepl, openrouter, openai, chat), and a first run should do neither
    # unasked. It used to be OpenRouter, chosen because the local backends could
    # not translate until 2.5 GB had been downloaded - but "needs a key and a model
    # id before it works" is its own kind of broken first run, and picking a
    # provider on the user's behalf is the part that cannot be undone afterwards.
    # `none` still reads the screen and shows the OCR text, so the app is visibly
    # alive while the backend is chosen in Settings - see `allow_model_download`
    # below for the local ones.
    backend: str = "none"  # "ct2" | "local" | "deepl" | "openrouter" | "openai" | "chat" | "none"
    # Where the int8-converted CTranslate2 model lives (ct2 backend). The user
    # data dir, unless an older install left the weights in the source tree.
    ct2_model_dir: str = field(default_factory=lambda: str(paths.default_ct2_dir()))
    source_lang: str = "eng_Latn"
    target_lang: str = "jpn_Jpan"
    # Empty means "whatever this backend defaults to": the NLLB repo for `ct2`
    # and `local` (see their constructors), and for the hosted chat backends the
    # model you chose in Settings. Their ids change too often for a baked-in
    # default to stay right, so there deliberately is not one.
    model: str = ""
    device: str = "cpu"
    threads: int = 8
    max_new_tokens: int = 192
    # `local` hands the model id to transformers, which downloads it without
    # asking: 2.46 GB for the model this project uses, into the shared HuggingFace
    # cache. Off by default, so that is a decision rather than a surprise.
    # `lintranslator convert` does not need it - it says what it will fetch and
    # asks first, and leaves the faster int8 weights behind as well.
    allow_model_download: bool = False
    # Term overrides. Accepts {"Term": "訳"} (applied to the output) or
    # {"pre": {...}, "post": {...}} for explicit control.
    glossary: dict = field(default_factory=dict)
    # Built-in Limbus Company terms, layered *under* the user's entries.
    use_builtin_glossary: bool = True

    # --- remote backends (deepl / google / openrouter / openai / chat) ---
    # One key per backend, keyed by backend name. Prefer the environment over
    # these: config.json is a file people share, commit and screenshot, and a key
    # in it leaks easily.
    #   OPENROUTER_API_KEY / OPENAI_API_KEY / DEEPL_API_KEY / GOOGLE_API_KEY /
    #   LINTRANSLATOR_API_KEY
    #
    # This was one `api_key` for every backend, which meant the key stored for
    # DeepL was offered to OpenRouter and sent to it as a bearer token: the field
    # was refilled from that single value whichever backend was selected, and the
    # key hidden behind a masked box looked like it belonged there. `Config.load`
    # moves an old value into this map, under the backend that was configured when
    # it was written, which is the only answer the file holds.
    api_keys: dict = field(default_factory=dict)
    # The field `api_keys` replaced. Kept so an existing config.json still loads
    # and still works: `resolve_api_key` reads it only when the map has nothing
    # for the backend in hand, and the next save drops it.
    api_key: str | None = None
    # Override the provider's base URL. Empty means the backend's default, and
    # also lets any OpenAI-compatible endpoint be used via backend "chat".
    api_base: str | None = None
    # A base URL must be https, because the key travels in an Authorization
    # header and the text being translated travels in the body. http is allowed
    # for loopback (a local llama.cpp / Ollama server) and nowhere else unless
    # this is set, which is the escape hatch for a server on the LAN.
    allow_insecure_http: bool = False
    temperature: float = 0.0
    max_tokens: int = 1024
    # Seconds to wait for one answer, or 0 for "pick one for me": 20 s for a
    # hosted endpoint, 120 s for one on this machine. A local model is not late
    # the way a server that never answers is late - a 4B one on CPU spends ten
    # seconds on a single line, a 12B one several times that, and the first line
    # pays for loading the weights as well. This was a constant 20 s, which
    # reported exactly that as "unreachable"; making it a config key did not help,
    # because the key had no row in Settings.
    timeout: float = 0.0
    # Sent as `reasoning_effort` on a chat request. Empty sends nothing at all,
    # which is what every hosted provider wants. A local *thinking* model needs
    # "none": asked to translate without it, qwen3.5:4b on Ollama spent 10.3 s
    # producing 3,544 characters of hidden reasoning and returned an empty
    # translation; with "none" the same line came back in 0.4 s. See `_extract`
    # in translate.py, which names this setting when a model answers that way.
    reasoning_effort: str = ""
    # Free-form instruction prepended to every remote translation request. This
    # is where you tell the model what game it is translating, which is the single
    # biggest lever on quality. `{source}` and `{target}` are substituted with the
    # language names. Empty means use the built-in default.
    prompt: str = ""
    # Extra instruction appended after `prompt`, for short additions.
    glossary_hint: str = ""
    # Recently used chat models, most recent first. Purely a UI convenience, but
    # kept in the config so switching back to a model that worked is one click
    # instead of retyping an id like "tencent/hy-mt2-1.8b".
    recent_models: list = field(default_factory=list)


@dataclass
class GuiConfig:
    """GUI behaviour."""

    # Start translating as soon as a window opens. Off would mean opening the
    # panel and seeing nothing happen, which reads as broken.
    autostart: bool = True


@dataclass
class DisplayConfig:
    """How the translation card looks."""

    width: int = 560
    # Scaled by font_scale for the translation line; the source line is smaller.
    base_font_size: int = 17
    font_scale: float = 1.0
    show_source: bool = True
    # How many lines each text area reserves, always - the card's height budget.
    # The card is a *fixed* size on purpose. It floats over the game, so a card
    # that grew with its text creeps further over the dialogue box it is
    # reporting on, and GTK never shrinks a resizable window back: measured, one
    # four-line reply took the card from 172 px to 312 px and it stayed there
    # even after the next line was two characters long. Text longer than the
    # budget scrolls inside its area instead.
    target_lines: int = 3
    source_lines: int = 2
    # Height the card was dragged to, in pixels, or 0 to size it from the line
    # budgets above. The budget stays a floor either way, so the card can never
    # be dragged smaller than the text areas it has to show.
    height: int = 0
    # Try to keep the card above other windows. Only achievable under XWayland
    # (via _NET_WM_STATE_ABOVE) or through a compositor window rule; native
    # Wayland gives clients no way to raise themselves.
    keep_above: bool = True


@dataclass
class DetectConfig:
    """Change detection tuning."""

    min_changed_fraction: float = 0.0005
    settle_frames: int = 2
    # Hard ceiling on how long one line may be held before it is translated
    # regardless. Must stay above `settle_window + incomplete_grace`, or it
    # becomes the effective release time and unfinished text gets through.
    settle_max_wait: float = 8.0
    # How long the OCR text must stop changing before it counts as final. Measured
    # from the last change, so an oscillating read still settles.
    settle_window: float = 1.2
    # Extra wait before releasing text that does not look like a finished
    # sentence. Games pause mid-reveal (observed ~3 s) and "stable for N seconds"
    # cannot tell a pause from the end of a line, so an unfinished read would be
    # translated as a fragment and then again when the rest arrived. Must stay
    # under `settle_max_wait`.
    incomplete_grace: float = 4.5
    empty_streak_limit: int = 6
    # Re-read the last frame on this interval even if the pixels never settle.
    #
    # Necessary because a live game is rarely pixel-stable: a blinking advance
    # cursor, an animated portrait, drifting particles or simply the mouse moving
    # over the region all keep the frame changing. Without a forced re-read, the
    # settler can never confirm a line and nothing is ever translated. This
    # bounds the wait to one interval instead of depending on the screen going
    # still. 0 disables it (pixel-change driven only).
    refresh_interval: float = 0.9
    # Minimum gap between OCR runs when the screen keeps changing. A blinking
    # advance cursor changes pixels every poll without changing the text, so
    # without this OCR would run continuously. Kept well under settle_window so
    # it cannot delay translation meaningfully.
    ocr_min_interval: float = 0.25
    # A read shorter than a dozen characters has no context to be judged by, so it
    # must be at least this confident before a model is asked about it. This is the
    # rail against *confident nonsense*: background art reads as one or two glyphs,
    # measured at 62.5% - above `ocr.min_confidence`, and it was translated until
    # this existed. Raise it to be stricter about short lines, lower it if a game
    # shows very short lines in a poor font.
    short_text_confidence: float = 75.0


@dataclass
class Config:
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    ocr: OcrConfig = field(default_factory=OcrConfig)
    translate: TranslateConfig = field(default_factory=TranslateConfig)
    detect: DetectConfig = field(default_factory=DetectConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    gui: GuiConfig = field(default_factory=GuiConfig)
    # Where translated lines are remembered across restarts, keyed by source
    # text. Empty means "this run only": the cache still collapses repeats within
    # a session, but nothing is written to disk.
    #
    # It used to write `data/cache.json` by default, which quietly accumulates a
    # plaintext transcript of everything that has ever passed through the capture
    # box - which is whatever was on screen, not just the game. Persisting it is
    # now an explicit choice.
    cache_path: str = ""
    # Populated by `load` when the file contains keys this version doesn't know.
    warnings: list[str] = field(default_factory=list, compare=False)
    # Where this instance was loaded from, so a plain `save()` writes back to the
    # same file. Without it, `Config.load("/tmp/x.json").save()` silently rewrites
    # the real config.json, which is a destructive surprise for tests and tools.
    path: Path | None = field(default=None, compare=False, repr=False)

    # -- persistence ------------------------------------------------------- #
    @classmethod
    def load(cls, path: Path | str | None = None) -> "Config":
        p = Path(path) if path else paths.DEFAULT_CONFIG_PATH

        if not p.exists():
            cfg = cls()
            cfg.path = p
        else:
            cfg = cls()
            cfg.path = p
            try:
                raw = json.loads(p.read_text())
                if not isinstance(raw, dict):
                    raise ValueError(
                        f"the top level is {type(raw).__name__}, not a JSON object"
                    )
                cfg = cls.from_dict(raw)
                cfg.path = p
            except (OSError, ValueError, TypeError) as exc:
                # A config the app cannot parse is not a reason to refuse to
                # start: every setting has a working default, and the alternative
                # is a traceback out of `check` or `gui` that says nothing about
                # which key is wrong. Start on the defaults, and say so.
                cfg = cls()
                cfg.path = p
                cfg.warnings.append(
                    f"could not read {p} ({type(exc).__name__}: {exc}); using "
                    "defaults - fix or delete the file to clear this"
                )
            else:
                # Reporting unknown keys prevents a silently ignored setting: a
                # typo or a renamed field would otherwise look as if it had been
                # applied.
                unknown = _unknown_keys(raw)
                if unknown:
                    cfg.warnings.append(
                        "ignoring unknown config key(s): " + ", ".join(sorted(unknown))
                    )

        # A config written before the mode was set at creation is 0644 and holds
        # a key. Tightening on read is what reaches those files; the next save
        # would only fix the ones that get saved.
        if path is None and p.exists():
            if paths.tighten(p):
                cfg.warnings.append(f"config permissions tightened to 0600 ({p})")
        # Not a warning: the old value keeps working, it just belongs to one
        # backend now instead of to all of them.
        _migrate_api_keys(cfg)
        return cfg

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        cfg = cls()
        for section in (f.name for f in fields(cls)):
            if section not in raw:
                continue
            value = raw[section]
            current = getattr(cfg, section)
            if hasattr(current, "__dataclass_fields__"):
                if not isinstance(value, dict):
                    # `{"capture": "off"}` used to be assigned as-is, so the
                    # failure surfaced much later as an AttributeError from
                    # whichever attribute was read first.
                    cfg.warnings.append(
                        f"ignoring {section}: expected a JSON object, got "
                        f"{type(value).__name__}"
                    )
                    continue
                setattr(cfg, section, _build(current, value))
            else:
                setattr(cfg, section, value)
        return cfg

    def save(self, path: Path | str | None = None) -> Path:
        # Prefer an explicit path, then wherever this was loaded from, then the
        # user's config dir.
        p = Path(path) if path else (self.path or paths.DEFAULT_CONFIG_PATH)
        data = asdict(self)
        data.pop("warnings", None)  # transient, never persisted
        data.pop("path", None)  # where it came from, not a setting
        # The map is the setting now. Writing `api_key: null` would leave a field
        # in the file whose whole problem was that it had no backend attached.
        translate = data.get("translate")
        if isinstance(translate, dict) and not translate.get("api_key"):
            translate.pop("api_key", None)
        # 0600: this file can hold an API key in plain text, and it used to be
        # written with the process umask - 0644 on a normal desktop.
        paths.write_private(p, json.dumps(data, indent=2) + "\n")
        return p


def _migrate_api_keys(cfg: "Config") -> None:
    """Move a single old `api_key` into the per-backend map.

    The old field recorded no backend - that was the bug - so the configured one
    is the only answer the file holds, and it is right for every config Settings
    ever wrote: the field was filled in while that backend was selected. A config
    that already has the map keeps it; a leftover legacy value does not overwrite
    an entry that is already there.
    """
    translate = cfg.translate
    legacy = translate.api_key
    if not legacy:
        return
    keys = dict(translate.api_keys or {})
    keys.setdefault(translate.backend, legacy)
    translate.api_keys = keys
    translate.api_key = None


def _unknown_keys(raw: dict[str, Any], cls: type = Config) -> set[str]:
    """Keys present in a config file that this version does not define."""
    unknown: set[str] = set()
    for f in fields(cls):
        if f.name not in raw:
            continue
        current = getattr(cls(), f.name)
        value = raw[f.name]
        if hasattr(current, "__dataclass_fields__") and isinstance(value, dict):
            nested = {nf.name for nf in fields(current)}
            unknown |= {f"{f.name}.{k}" for k in value if k not in nested}
        elif f.name not in ("warnings", "path"):
            # `warnings` and `path` are runtime bookkeeping, not settings a user
            # would ever write into the file.
            continue
    unknown |= {k for k in raw if k not in {f.name for f in fields(cls)}}
    return unknown


def _region_from(values: dict[str, Any]) -> Region:
    """A `Region` from a config dict, refusing anything that is not a number.

    `Region(**values)` accepts a string for `x` quite happily and only fails much
    later, inside `to_pixels`, as `AttributeError: 'str' object has no attribute
    'to_pixels'` on the panel's status card. Refusing here is what turns that
    into "your config is wrong, and here is which key".
    """
    unknown = set(values) - {f.name for f in fields(Region)}
    if unknown:
        raise ValueError(f"unknown region key(s): {', '.join(sorted(unknown))}")
    for key in ("x", "y", "w", "h"):
        if key in values and (
            isinstance(values[key], bool) or not isinstance(values[key], (int, float))
        ):
            raise ValueError(f"region.{key} must be a number, got {values[key]!r}")
    mode = values.get("mode", "pixels")
    if mode not in ("pixels", "fraction"):
        raise ValueError(f"region.mode must be 'pixels' or 'fraction', got {mode!r}")
    return Region(**values)


def _build(obj: Any, values: dict[str, Any]) -> Any:
    """Rebuild a nested dataclass from a dict, honouring nested regions."""
    kwargs: dict[str, Any] = {}
    for f in fields(obj):
        if f.name not in values:
            continue
        v = values[f.name]
        if f.name == "region" and isinstance(v, dict):
            kwargs[f.name] = _region_from(v)
        else:
            kwargs[f.name] = v
    return type(obj)(**kwargs)
