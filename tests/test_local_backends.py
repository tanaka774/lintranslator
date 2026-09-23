"""The local NLLB backends must never be handed an empty model id.

Shipping bug this pins down: `translate.model` is shared with the chat backends,
where an empty value means "use the provider's default". The Settings dialog
writes that field for *every* backend, so opening it on the ct2 backend and
hitting Save stored `"model": ""`. That empty string then reached
`AutoTokenizer.from_pretrained("")` and surfaced as

    HFValidationError: Repo id must use alphanumeric chars, '-', '_' or '.'.
    The name cannot start or end with '-' or '.' and the maximum length is 96: ''

which names neither the config field nor the backend. These tests construct the
translators only (no `load()`), so they need neither ctranslate2 nor torch.
"""
from __future__ import annotations

import json

from lintranslator.config import Config, TranslateConfig
from lintranslator.translate import (
    DEFAULT_NLLB_MODEL,
    CTranslate2Translator,
    CachedTranslator,
    ChatCompletionsTranslator,
    NllbTranslator,
    build_translator,
)


# --------------------------------------------------------------------------- #
# Blank model ids
# --------------------------------------------------------------------------- #
def test_ct2_blank_model_falls_back_to_the_default_tokenizer():
    t = build_translator(TranslateConfig(backend="ct2", model=""))
    assert isinstance(t, CTranslate2Translator)
    assert t.tokenizer_name == DEFAULT_NLLB_MODEL


def test_local_blank_model_falls_back_to_the_default_model():
    t = build_translator(TranslateConfig(backend="local", model=""))
    assert isinstance(t, NllbTranslator)
    assert t.model_name == DEFAULT_NLLB_MODEL


def test_whitespace_only_model_is_treated_as_blank():
    """A field containing spaces is just as empty as one containing nothing."""
    assert CTranslate2Translator(model_dir="d", tokenizer="   ").tokenizer_name == DEFAULT_NLLB_MODEL
    assert NllbTranslator(model="\t ").model_name == DEFAULT_NLLB_MODEL


def test_blank_model_survives_a_config_round_trip(tmp_path):
    """What an empty model field in config.json does end to end."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"translate": {"backend": "ct2", "model": ""}}))

    cfg = Config.load(path)
    # The value is kept as written: normalising it at load time would break the
    # chat backends, where "" legitimately means the provider's default.
    assert cfg.translate.model == ""
    # ...and the fallback happens where the model is actually used.
    assert build_translator(cfg.translate).tokenizer_name == DEFAULT_NLLB_MODEL


# --------------------------------------------------------------------------- #
# The fallback must not shadow a real choice
# --------------------------------------------------------------------------- #
def test_explicit_model_is_kept():
    t = build_translator(TranslateConfig(backend="ct2", model="some/other-tokenizer"))
    assert t.tokenizer_name == "some/other-tokenizer"
    assert NllbTranslator(model="Helsinki-NLP/opus-mt-en-jap").model_name == (
        "Helsinki-NLP/opus-mt-en-jap"
    )


def test_empty_model_still_means_provider_default_for_chat_backends():
    """Why the fallback lives in the NLLB constructors and not in config.load."""
    t = build_translator(
        TranslateConfig(
            backend="chat",
            model="",
            api_key="k",
            api_base="http://localhost:11434/v1",
        )
    )
    assert isinstance(t, ChatCompletionsTranslator)
    assert t.model == ChatCompletionsTranslator.default_model


def test_ct2_cache_keys_stay_scoped_to_the_model_dir():
    """The tokenizer fallback must not become a cache namespace.

    Two different converted models can share the default tokenizer, so the ct2
    namespace has to keep using model_dir or their translations would collide.
    The language pair is appended because the same model and source text can be
    asked for two different targets.
    """
    cached = CachedTranslator(CTranslate2Translator(model_dir="/models/a", tokenizer=""))
    assert cached.cache_namespace == "ct2:/models/a:eng_Latn>jpn_Jpan"
