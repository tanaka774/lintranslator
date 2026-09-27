"""Configuration model with JSON persistence."""
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
        x = max(0, min(x, screen_w - 1))
        y = max(0, min(y, screen_h - 1))
        w = max(1, min(w, screen_w - x))
        h = max(1, min(h, screen_h - y))
        return x, y, w, h


@dataclass
class CaptureConfig:
    backend: str = "portal-screenshot"  # "portal-screenshot" | "portal-screencast"
    fps: float = 2.0
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
    invert: bool = False
    # Ink/paper cut level (1-255), or 0 to let tesseract choose; applied after
    # the contrast stretch
    threshold: int = 0
    # extra user patterns, e.g. "/usr/share/tessdata"
    tessdata_dir: str | None = None
    min_confidence: float = 40.0
    # Also download language data with no pinned checksum, accepting it unverified
    allow_unverified_tessdata: bool = False


@dataclass
class TranslateConfig:
    backend: str = "none"  # "deepl" | "google" | "openrouter" | "openai" | "chat" | "none"
    source_lang: str = "eng_Latn"
    target_lang: str = "jpn_Jpan"
    # Chat model id (an Ollama tag, a llama.cpp/vLLM name, or a provider id);
    # unused by deepl and google
    model: str = ""
    # Term overrides: {"Term": "訳"} applied to the output, or
    # {"pre": {...}, "post": {...}}
    glossary: dict = field(default_factory=dict)
    # Built-in Limbus Company terms, layered *under* the user's entries.
    use_builtin_glossary: bool = True

    # One key per backend, keyed by backend name. Prefer the environment over
    # these: config.json is a file people share, commit and screenshot.
    #   OPENROUTER_API_KEY / OPENAI_API_KEY / DEEPL_API_KEY / GOOGLE_API_KEY /
    #   LINTRANSLATOR_API_KEY
    api_keys: dict = field(default_factory=dict)
    # Legacy field kept so an existing config.json still loads; read only when
    # the map has nothing for the backend
    api_key: str | None = None
    # Override the provider's base URL; empty means the backend's default
    api_base: str | None = None
    # Must be https: the key travels in an Authorization header. http is allowed
    # for loopback only unless this is set
    allow_insecure_http: bool = False
    temperature: float = 0.0
    max_tokens: int = 1024
    # Seconds to wait for one answer, or 0 to pick one: 20 s hosted, 120 s local
    timeout: float = 0.0
    # Sent as `reasoning_effort` on a chat request; empty sends nothing. A local
    # thinking model needs "none", or it can return an empty translation
    reasoning_effort: str = ""
    # Instruction prepended to every remote translation request; `{source}` and
    # `{target}` are substituted with the language names, empty means the
    # built-in default
    prompt: str = ""
    # Extra instruction appended after `prompt`, for short additions.
    glossary_hint: str = ""
    # Recently used chat models, most recent first
    recent_models: list = field(default_factory=list)


@dataclass
class GuiConfig:
    """GUI behaviour."""

    autostart: bool = True


@dataclass
class DisplayConfig:
    """How the translation card looks."""

    width: int = 560
    # Scaled by font_scale for the translation line; the source line is smaller.
    base_font_size: int = 17
    font_scale: float = 1.0
    show_source: bool = True
    # Lines each text area reserves; the card is a fixed size because GTK never
    # shrinks a resizable window back
    target_lines: int = 3
    source_lines: int = 2
    # Height the card was dragged to, in pixels, or 0 to size it from the line
    # budgets above
    height: int = 0
    # Keep the card above other windows; only achievable under XWayland
    # (_NET_WM_STATE_ABOVE) or a compositor rule
    keep_above: bool = True


@dataclass
class DetectConfig:
    """Change detection tuning."""

    min_changed_fraction: float = 0.0005
    settle_frames: int = 2
    # Hard ceiling in seconds on how long one line may be held; must stay above
    # settle_window + incomplete_grace
    settle_max_wait: float = 8.0
    # Seconds the OCR text must stop changing to count as final, measured from
    # the last change
    settle_window: float = 1.2
    # Extra seconds before releasing text that does not look like a finished
    # sentence; must stay under settle_max_wait
    incomplete_grace: float = 4.5
    empty_streak_limit: int = 6
    # Re-read the last frame on this interval (seconds) even if the pixels never
    # settle; 0 disables it
    refresh_interval: float = 0.9
    # Minimum gap in seconds between OCR runs when the screen keeps changing
    ocr_min_interval: float = 0.25
    # A read shorter than a dozen characters needs at least this confidence
    # (percent) before a model is asked about it
    short_text_confidence: float = 75.0


@dataclass
class Config:
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    ocr: OcrConfig = field(default_factory=OcrConfig)
    translate: TranslateConfig = field(default_factory=TranslateConfig)
    detect: DetectConfig = field(default_factory=DetectConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    gui: GuiConfig = field(default_factory=GuiConfig)
    # Where translated lines are cached across restarts, keyed by source text;
    # empty means this run only, since a persisted cache is a plaintext
    # transcript of the screen
    cache_path: str = ""
    # Populated by `load` when the file contains keys this version doesn't know.
    warnings: list[str] = field(default_factory=list, compare=False)
    # Where this instance was loaded from, so a plain save() writes back to the
    # same file
    path: Path | None = field(default=None, compare=False, repr=False)

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
                cfg = cls()
                cfg.path = p
                cfg.warnings.append(
                    f"could not read {p} ({type(exc).__name__}: {exc}); using "
                    "defaults - fix or delete the file to clear this"
                )
            else:
                unknown = _unknown_keys(raw)
                if unknown:
                    cfg.warnings.append(
                        "ignoring unknown config key(s): " + ", ".join(sorted(unknown))
                    )

        # Tighten on read: a config written before the mode was set at creation
        # is 0644 and may hold a key
        if path is None and p.exists():
            if paths.tighten(p):
                cfg.warnings.append(f"config permissions tightened to 0600 ({p})")
        # Backend migration first: the key migration below reads translate.backend
        _migrate_removed_backends(cfg)
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
        p = Path(path) if path else (self.path or paths.DEFAULT_CONFIG_PATH)
        data = asdict(self)
        data.pop("warnings", None)  # transient, never persisted
        data.pop("path", None)  # where it came from, not a setting
        translate = data.get("translate")
        if isinstance(translate, dict) and not translate.get("api_key"):
            translate.pop("api_key", None)
        # 0600: the file can hold an API key in plain text, and the process umask
        # would make it 0644
        paths.write_private(p, json.dumps(data, indent=2) + "\n")
        return p


# Prefixes that name the provider a key was issued by
KEY_ISSUERS = (
    ("sk-or-", "openrouter"),
    ("AIza", "google"),
)


def key_issuer(key: str | None) -> str | None:
    """The backend a key's prefix names, or None when nothing does."""
    text = str(key or "")
    for prefix, backend in KEY_ISSUERS:
        if text.startswith(prefix):
            return backend
    return None


def _migrate_api_keys(cfg: "Config") -> None:
    """Move a single old `api_key` into the per-backend map."""
    translate = cfg.translate
    legacy = translate.api_key
    if not legacy:
        return
    owner = key_issuer(legacy) or translate.backend
    keys = dict(translate.api_keys or {})
    if owner != translate.backend:
        # Never repeat any part of the key in the warning: `check` prints it
        cfg.warnings.append(
            f"translate.api_key looks like an {owner} key, so it was stored under "
            f"{owner!r} rather than {translate.backend!r} - change it in Settings "
            "if that is wrong"
        )
    keys.setdefault(owner, legacy)
    translate.api_keys = keys
    translate.api_key = None


#: Backends an older config may hold that this version has no implementation
#: for; both migrate to `none`
_REMOVED_BACKENDS = ("ct2", "local")


def _migrate_removed_backends(cfg: "Config") -> None:
    """Move a config off a backend this version has no implementation for."""
    translate = cfg.translate
    removed = (translate.backend or "").strip()
    if removed.lower() not in _REMOVED_BACKENDS:
        return
    translate.backend = "none"
    cfg.warnings.append(
        f"translate.backend {removed!r} is not a backend this version has, so it "
        "is now 'none'. For local translation, serve a model such as "
        "Hy-MT2-1.8B with Ollama or llama.cpp and use the 'chat' backend - see "
        "the README"
    )


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
            # `warnings` and `path` are runtime bookkeeping, not user settings
            continue
    unknown |= {k for k in raw if k not in {f.name for f in fields(cls)}}
    return unknown


def _region_from(values: dict[str, Any]) -> Region:
    """A `Region` from a config dict, refusing anything that is not a number."""
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
