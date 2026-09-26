"""Nothing downloads a model without being asked.

Two routes used to fetch weights on their own: `lintranslator convert` (which is
at least explicit, but is 2.5 GB deep before it prints anything useful) and the
`local` backend, which handed the model id to transformers and let it fetch
whatever was missing while the panel said "warming up". Neither is a decision a
first run should make on the user's behalf.

The tessdata download is deliberately not gated: 4.1 MB for `eng`, pinned to a
revision and checksum-verified, announced while it happens.
"""
from __future__ import annotations

import sys

import pytest

from lintranslator.config import Config, TranslateConfig
from lintranslator.convert import confirm_download
from lintranslator.translate import (
    DEFAULT_NLLB_MODEL,
    NllbTranslator,
    TranslatorError,
    model_is_cached,
)

REPO = "models--facebook--nllb-200-distilled-600M"


# --------------------------------------------------------------------------- #
# The default configuration
# --------------------------------------------------------------------------- #
def test_a_fresh_config_defaults_to_no_backend():
    """The default must not download gigabytes, or pick a provider for the user.

    Every other backend does one of the two: `ct2`/`local` need 2.5 GB before they
    can say a word, and `deepl`/`openrouter`/`openai`/`chat` send the text read off
    the screen to somebody. OpenRouter was the default until "needs a key and a
    model id before it works" was judged a broken first run of its own, and
    choosing a provider for the user the part that cannot be taken back.
    """
    cfg = Config()
    assert cfg.translate.backend == "none"
    assert cfg.translate.model == ""  # the backend's own default, if it has one
    assert cfg.translate.allow_model_download is False


def test_the_local_backends_still_resolve_an_empty_model():
    """Empty means "the default repo" for NLLB, not "no model"."""
    from lintranslator.translate import CTranslate2Translator

    assert NllbTranslator(model="").model_name == DEFAULT_NLLB_MODEL
    assert CTranslate2Translator(model_dir="d", tokenizer="").tokenizer_name == DEFAULT_NLLB_MODEL


# --------------------------------------------------------------------------- #
# "Is it already here?" has to mean the weights, not the directory
# --------------------------------------------------------------------------- #
def test_a_tokenizer_only_cache_is_not_a_cached_model(monkeypatch, tmp_path):
    """`ct2` caches the tokenizer from the same repo the `local` backend wants."""
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    snapshot = tmp_path / REPO / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (snapshot / "tokenizer.json").write_text("{}")
    assert model_is_cached("facebook/nllb-200-distilled-600M") is False

    (snapshot / "pytorch_model.bin").write_bytes(b"weights")
    assert model_is_cached("facebook/nllb-200-distilled-600M") is True


def test_safetensors_count_too(monkeypatch, tmp_path):
    """The repo this defaults to has shipped both formats."""
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    snapshot = tmp_path / REPO / "snapshots" / "def456"
    snapshot.mkdir(parents=True)
    (snapshot / "model.safetensors").write_bytes(b"weights")
    assert model_is_cached("facebook/nllb-200-distilled-600M") is True


def test_an_absent_cache_is_not_a_cached_model(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "nothing-here"))
    assert model_is_cached("facebook/nllb-200-distilled-600M") is False


# --------------------------------------------------------------------------- #
# The local backend asks
# --------------------------------------------------------------------------- #
def test_the_local_backend_refuses_to_download_on_its_own(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty"))
    translator = NllbTranslator(model="facebook/nllb-200-distilled-600M")
    with pytest.raises(TranslatorError) as exc:
        translator.load()
    message = str(exc.value)
    assert "2.5 GB" in message
    assert "lintranslator convert" in message
    # It says what to change, by name, not just that something went wrong.
    assert "translate.allow_model_download" in message


def test_allowing_the_download_gets_past_that_gate(monkeypatch, tmp_path):
    """With the opt-in set, the gate is not what stops it."""
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty"))
    # Make the next step deterministic and offline: the torch/transformers import
    # is the one that follows, and nothing here may reach the network.
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "transformers", None)

    translator = NllbTranslator(model="facebook/nllb-200-distilled-600M", allow_download=True)
    with pytest.raises(TranslatorError) as exc:
        translator.load()
    message = str(exc.value)
    assert "allow_model_download" not in message
    assert "torch" in message


def test_an_already_cached_model_is_never_gated(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    snapshot = tmp_path / REPO / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (snapshot / "pytorch_model.bin").write_bytes(b"weights")
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "transformers", None)

    with pytest.raises(TranslatorError) as exc:
        NllbTranslator(model="facebook/nllb-200-distilled-600M").load()
    # A cached model goes straight to loading, so the failure is about the
    # missing library rather than about a download that is not needed.
    assert "allow_model_download" not in str(exc.value)


# --------------------------------------------------------------------------- #
# convert says what it will fetch
# --------------------------------------------------------------------------- #
class _Stdin:
    def __init__(self, answer: str | None = None, tty: bool = True) -> None:
        self.answer = answer
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty

    def readline(self) -> str:
        return (self.answer or "") + "\n"


def test_convert_without_a_terminal_refuses_and_says_how(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", _Stdin(tty=False))
    assert confirm_download("facebook/nllb-200-distilled-600M", "/tmp/out") is False
    err = capsys.readouterr().err
    assert "2.5 GB" in err
    assert "--yes" in err


def test_convert_asks_and_honours_no(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", _Stdin("n"))
    assert confirm_download("m", "/tmp/out") is False


def test_convert_asks_and_honours_yes(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", _Stdin("y"))
    assert confirm_download("m", "/tmp/out") is True


def test_convert_skips_the_question_with_yes(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", _Stdin(tty=False))
    assert confirm_download("m", "/tmp/out", assume_yes=True) is True
    # It still says what is about to happen.
    assert "downloads" in capsys.readouterr().err


def test_the_plan_names_both_costs(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", _Stdin("y"))
    confirm_download("facebook/nllb-200-distilled-600M", "/tmp/out")
    err = capsys.readouterr().err
    assert "fp32 checkpoint" in err  # the download
    assert "converted weights" in err  # what it leaves behind
    assert "lintranslator remove" in err  # and how to get the space back
