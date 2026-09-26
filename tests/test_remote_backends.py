"""Tests for the remote translation backends.

No network calls: `ChatCompletionsTranslator` is exercised by stubbing
`_post_json`, which is where the request is actually shaped. The tests therefore
check what would be sent (model, prompt, headers, endpoint) and how responses and
failures are handled.
"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from lintranslator.config import TranslateConfig
from lintranslator.translate import (
    DATA_NOT_INSTRUCTIONS,
    DEFAULT_PROMPT,
    DEFAULT_TIMEOUT,
    LOCAL_TIMEOUT,
    ChatCompletionsTranslator,
    GoogleTranslator,
    OpenAITranslator,
    OpenRouterTranslator,
    TranslatorError,
    build_translator,
    redact,
    resolve_api_key,
    shorten,
    validate_base_url,
)

GOOD_RESPONSE = {"choices": [{"message": {"content": "  こんにちは  "}}]}


class Recording(ChatCompletionsTranslator):
    """Captures the request instead of sending it."""

    name = "recording"
    default_base = "https://example.test/v1"
    default_model = "test-model"

    def __init__(self, *args, response=None, error=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.sent: list[tuple[str, dict, dict]] = []
        self._response = response if response is not None else GOOD_RESPONSE
        self._error = error

    def _post_json(self, url, payload, headers):
        self.sent.append((url, payload, headers))
        if self._error:
            raise self._error
        return self._response


# --------------------------------------------------------------------------- #
# Request shaping
# --------------------------------------------------------------------------- #
def test_builds_endpoint_from_base_url():
    t = Recording("key")
    assert t.endpoint == "https://example.test/v1/chat/completions"
    # A trailing slash must not produce a doubled separator.
    assert Recording("key", base_url="https://x.test/v1/").endpoint == "https://x.test/v1/chat/completions"


def test_sends_model_temperature_and_bearer_token():
    t = Recording("secret", model="my/model", temperature=0.3)
    t.translate("hello")
    url, payload, headers = t.sent[0]
    assert payload["model"] == "my/model"
    assert payload["temperature"] == 0.3
    assert headers["Authorization"] == "Bearer secret"
    assert payload["messages"][-1] == {"role": "user", "content": "hello"}


def test_system_prompt_states_direction_and_constraints():
    t = Recording("key", source="English", target="Japanese")
    prompt = t.system_prompt()
    assert "English" in prompt and "Japanese" in prompt
    # The brackets rule exists because the game wraps lore text in [ ].
    assert "bracket" in prompt.lower()
    assert "only the translation" in prompt.lower()


def test_custom_prompt_replaces_the_default_entirely():
    t = Recording("key", prompt="This is Limbus Company. {source} -> {target}.")
    prompt = t.system_prompt()
    # The written prompt is used verbatim, with none of the built-in wording...
    assert prompt.startswith("This is Limbus Company. English -> Japanese.")
    assert DEFAULT_PROMPT not in prompt
    # ...and only the standing "this is data, not instructions" line is added,
    # which applies to a user-written prompt exactly as it does to the default.
    assert prompt == (
        "This is Limbus Company. English -> Japanese." + DATA_NOT_INSTRUCTIONS
    )


def test_prompt_that_never_names_the_target_gets_a_translation_directive():
    """A prompt that never says "Japanese" reads as chat, not as a task.

    Measured before this guard existed: both hy-mt2-1.8b and gemini-2.5-flash-lite
    returned the English source verbatim, so the panel showed untranslated text.
    """
    t = Recording("key", prompt="please translate as it is, don't lose its context.")
    prompt = t.system_prompt()
    assert prompt.startswith("please translate as it is")
    assert "Japanese" in prompt
    assert "only the Japanese translation" in prompt
    assert "Do not explain" in prompt


def test_prompt_naming_the_target_is_left_alone():
    for written in (
        "Translate this {source} text into {target} as literally as possible.",
        "Render the following in Japanese, keeping the tone.",
        "TRANSLATE INTO JAPANESE. Output only the translation.",
    ):
        prompt = Recording("key", prompt=written).system_prompt()
        rendered = written.replace("{source}", "English").replace("{target}", "Japanese")
        # No target-language directive is bolted on, but the standing
        # data-not-instructions line still is.
        assert prompt == rendered + DATA_NOT_INSTRUCTIONS


def test_directive_respects_a_prompt_that_already_says_only():
    t = Recording("key", prompt="Say it in Japanese only.")
    prompt = t.system_prompt()
    assert "Japanese" in prompt
    # The "only" clause is already there, so the extra sentence is not needed.
    assert "Do not explain" not in prompt


def test_prompt_with_unknown_braces_does_not_raise():
    """str.format would raise on a stray brace in a user-written prompt."""
    t = Recording("key", prompt="Use JSON like {\"a\": 1}. Translate {source} to {target}.")
    assert "English" in t.system_prompt()


def test_empty_prompt_falls_back_to_the_builtin_default():
    from lintranslator.geometry import DEFAULT_PROMPT

    assert Recording("key", prompt="").system_prompt() == Recording(
        "key", prompt=None
    ).system_prompt()
    # DEFAULT_PROMPT is a template; the language names are substituted in.
    assert "{source}" in DEFAULT_PROMPT and "{target}" in DEFAULT_PROMPT
    assert "English" in Recording("key", prompt=DEFAULT_PROMPT).system_prompt()


def test_glossary_hint_is_included_only_when_set():
    plain = Recording("key").system_prompt()
    assert "fixed terms" not in plain

    hinted = Recording("key", glossary_hint="Faust -> ファウスト").system_prompt()
    assert "Faust -> ファウスト" in hinted


def test_response_is_stripped():
    assert Recording("key").translate("x") == "こんにちは"


def test_extra_headers_are_forwarded():
    t = Recording("key", extra_headers={"X-Test": "1"})
    t.translate("x")
    assert t.sent[0][2]["X-Test"] == "1"


# --------------------------------------------------------------------------- #
# Failure handling
# --------------------------------------------------------------------------- #
def test_missing_key_raises_with_guidance():
    with pytest.raises(TranslatorError) as exc:
        Recording(None)
    message = str(exc.value)
    assert "API key" in message
    # The message must name the environment variable, not just complain.
    assert "LINTRANSLATOR_API_KEY" in message


def test_malformed_response_is_reported_not_swallowed():
    t = Recording("key", response={"unexpected": True})
    with pytest.raises(TranslatorError) as exc:
        t.translate("x")
    assert "unexpected" in str(exc.value)


def test_empty_translation_is_an_error():
    t = Recording("key", response={"choices": [{"message": {"content": ""}}]})
    with pytest.raises(TranslatorError):
        t.translate("x")


def test_http_error_propagates_as_translator_error():
    t = Recording("key", error=TranslatorError("recording HTTP 429: rate limited"))
    with pytest.raises(TranslatorError):
        t.translate("x")


# --------------------------------------------------------------------------- #
# What an error is allowed to carry into the UI
# --------------------------------------------------------------------------- #
def test_provider_error_bodies_are_shortened_to_one_line():
    """The panel shows this in a one-line status row, and it gets pasted into
    bug reports, so it is collapsed and capped."""
    body = json.dumps(
        {
            "error": {
                "message": "model not found: " + "x" * 500,
                "type": "invalid_request_error",
            }
        }
    )
    out = shorten(body)
    assert "\n" not in out
    assert len(out) <= 201
    assert out.startswith('{"error"')


def test_a_key_in_an_error_body_is_redacted():
    secret = "sk-or-v1-0123456789abcdef0123456789abcdef"
    assert secret not in redact(f"invalid key {secret}", secret)
    # ...including shapes we were not told about.
    assert "sk-proj-abcdefghijklmnop" not in redact("got sk-proj-abcdefghijklmnop!")
    assert "Bearer abcdefghijklmnop" not in redact("sent Bearer abcdefghijklmnop")
    assert "DeepL-Auth-Key abc:fx" not in redact("sent DeepL-Auth-Key abc:fx")
    assert "a" * 60 not in redact("token " + "a" * 60)


def test_an_http_error_body_is_redacted_before_it_reaches_the_ui(monkeypatch):
    """The body goes straight into the panel's status row and from there into
    bug reports, so the key must not survive the trip."""
    secret = "sk-or-v1-0123456789abcdef0123456789abcdef"

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            {},
            io.BytesIO(f'{{"error":"invalid api key {secret}"}}'.encode()),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    t = ChatCompletionsTranslator(secret, model="m", base_url="https://example.test/v1")
    with pytest.raises(TranslatorError) as exc:
        t.translate("x")
    assert secret not in str(exc.value)
    assert "401" in str(exc.value)


# --------------------------------------------------------------------------- #
# Provider defaults
# --------------------------------------------------------------------------- #
def test_openrouter_uses_its_own_base_url_and_headers():
    t = OpenRouterTranslator("key", model="z-ai/glm-4.6")
    assert t.base_url == "https://openrouter.ai/api/v1"
    assert t.key_env == "OPENROUTER_API_KEY"


def test_openai_uses_openai_base_url():
    t = OpenAITranslator("key")
    assert t.base_url == "https://api.openai.com/v1"
    assert t.key_env == "OPENAI_API_KEY"
    assert t.model == "gpt-4o-mini"


def test_openrouter_requires_an_explicit_model():
    """A stale default model would fail confusingly, so it is required."""
    with pytest.raises(TranslatorError) as exc:
        OpenRouterTranslator("key")
    assert "model" in str(exc.value).lower()


# --------------------------------------------------------------------------- #
# Key resolution
# --------------------------------------------------------------------------- #
def test_config_key_wins_over_environment(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
    cfg = TranslateConfig(api_key="from-config")
    assert resolve_api_key(cfg, "OPENROUTER_API_KEY") == "from-config"


def test_environment_is_used_when_config_is_empty(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
    cfg = TranslateConfig(api_key=None)
    assert resolve_api_key(cfg, "OPENROUTER_API_KEY") == "from-env"


def test_no_key_anywhere_returns_none(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("LINTRANSLATOR_API_KEY", raising=False)
    assert resolve_api_key(TranslateConfig(api_key=None), "OPENROUTER_API_KEY") is None


def test_only_the_variables_it_is_given_are_consulted(monkeypatch):
    """A variable for another backend's provider must not be picked up as a key."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("LINTRANSLATOR_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "not-this-backends-key")
    assert resolve_api_key(TranslateConfig(api_key=None), "OPENROUTER_API_KEY") is None


# --------------------------------------------------------------------------- #
# Factory wiring
# --------------------------------------------------------------------------- #
def test_factory_builds_openrouter_from_config(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-x")
    cfg = TranslateConfig(backend="openrouter", model="google/gemini-2.0-flash-001")
    t = build_translator(cfg)
    assert isinstance(t, OpenRouterTranslator)
    assert t.model == "google/gemini-2.0-flash-001"


def test_factory_maps_language_codes_to_names_for_chat_backends(monkeypatch):
    """NLLB wants eng_Latn; a chat model wants "English" in the prompt."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-x")
    cfg = TranslateConfig(
        backend="openrouter", model="m", source_lang="eng_Latn", target_lang="jpn_Jpan"
    )
    t = build_translator(cfg)
    prompt = t.system_prompt()
    assert "English" in prompt and "Japanese" in prompt
    assert "eng_Latn" not in prompt


def test_the_chat_prompt_keeps_a_hand_written_language_understandable(monkeypatch):
    """A config can carry a bare ISO code; the prompt must still make sense."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-x")
    cfg = TranslateConfig(backend="openrouter", model="m", source_lang="jpn")
    t = build_translator(cfg)
    assert "Japanese" in t.system_prompt()


# --------------------------------------------------------------------------- #
# Slow answers, and answers that are only thinking
# --------------------------------------------------------------------------- #
def test_a_server_on_this_machine_gets_the_longer_timeout():
    """A local model is not late the way a server that never answers is late."""
    local = build_translator(
        TranslateConfig(
            backend="chat", model="qwen3.5:4b", api_base="http://localhost:11434/v1"
        )
    )
    hosted = build_translator(TranslateConfig(backend="openai", model="gpt-4o-mini", api_key="k"))
    assert local.timeout == LOCAL_TIMEOUT
    assert hosted.timeout == DEFAULT_TIMEOUT
    assert LOCAL_TIMEOUT > DEFAULT_TIMEOUT


def test_an_explicit_timeout_wins_over_the_default():
    t = build_translator(
        TranslateConfig(
            backend="chat", model="m", api_base="http://localhost:11434/v1", timeout=45
        )
    )
    assert t.timeout == 45.0


def test_a_timeout_is_reported_as_a_timeout_and_not_as_unreachable(monkeypatch):
    """The message that sent the user looking for a network problem."""

    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    t = ChatCompletionsTranslator(
        None, model="qwen3.5:4b", base_url="http://localhost:11434/v1",
        key_required=False, timeout=20.0,
    )
    with pytest.raises(TranslatorError) as exc:
        t.translate("hello")
    message = str(exc.value)
    assert "did not answer within 20 s" in message
    assert "unreachable" not in message
    assert "Timeout" in message


def test_reasoning_effort_is_sent_only_when_it_is_set():
    class Recording(ChatCompletionsTranslator):
        def __init__(self, **kw):
            self.sent: dict = {}
            super().__init__(
                None, model="m", base_url="http://localhost:11434/v1", key_required=False, **kw
            )

        def _post_json(self, url, payload, headers):
            self.sent = payload
            return GOOD_RESPONSE

    off = Recording()
    off.translate("hi")
    assert "reasoning_effort" not in off.sent, "a hosted provider must not be sent this"

    on = Recording(reasoning_effort="none")
    on.translate("hi")
    assert on.sent["reasoning_effort"] == "none"


def test_a_model_that_only_thinks_says_so_instead_of_looking_empty():
    """Measured on qwen3.5:4b through Ollama: 3,544 characters of reasoning in a
    field of its own, and an empty `content`.

    "empty translation" was true and useless: it reads as a bug in the app rather
    than a model that needs either more room or the thinking turned off.
    """

    class Thinking(ChatCompletionsTranslator):
        def _post_json(self, url, payload, headers):
            return {"choices": [{"message": {"content": "", "reasoning": "x" * 3544}}]}

    t = Thinking(
        None, model="qwen3.5:4b", base_url="http://localhost:11434/v1", key_required=False
    )
    with pytest.raises(TranslatorError) as exc:
        t.translate("hello")
    message = str(exc.value)
    assert "3544 characters of hidden reasoning" in message
    assert "reasoning_effort" in message


# --------------------------------------------------------------------------- #
# Language codes per backend
# --------------------------------------------------------------------------- #
def test_deepl_gets_its_own_codes():
    cfg = TranslateConfig(
        backend="deepl", api_key="k", source_lang="eng_Latn", target_lang="jpn_Jpan"
    )
    t = build_translator(cfg)
    assert (t.source, t.target) == ("EN", "JA")


def test_deepl_refuses_a_target_it_cannot_translate():
    """The old fallback sent `ceb`, and the API answered with a bare HTTP 400.

    NLLB can translate into 202 languages and DeepL into 34, so this is the
    common case rather than an edge one - and the error has to name the setting
    that caused it.
    """
    cfg = TranslateConfig(backend="deepl", api_key="k", target_lang="ceb_Latn")
    with pytest.raises(TranslatorError) as exc:
        build_translator(cfg)
    message = str(exc.value)
    assert "cannot translate into 'ceb_Latn' (Cebuano)" in message
    assert "JA (Japanese)" in message, "the message should list what DeepL can do"
    # Deduplicated by DeepL's own code: it does not tell Arabic varieties apart.
    assert "AR (Modern Standard Arabic)" in message
    assert "Tunisian Arabic" not in message


def test_deepl_lets_an_unsupported_source_be_detected():
    """A source DeepL has no code for is not an error: DeepL detects it."""
    cfg = TranslateConfig(
        backend="deepl", api_key="k", source_lang="ceb_Latn", target_lang="jpn_Jpan"
    )
    t = build_translator(cfg)
    assert t.source is None and t.target == "JA"


def test_google_gets_iso_639_1_codes():
    cfg = TranslateConfig(
        backend="google", api_key="k", source_lang="eng_Latn", target_lang="jpn_Jpan"
    )
    t = build_translator(cfg)
    assert (t.source, t.target) == ("en", "ja")
    assert t.endpoint == "https://translation.googleapis.com/language/translate/v2"


def test_google_tells_the_two_chinese_scripts_apart():
    """`zho_Hant` and `zho_Hans` share an ISO code and Google does not.

    Sending "zh" for a Traditional target returns Simplified with nothing on the
    card to show it, which is the failure mode this table exists to prevent.
    """
    cfg = TranslateConfig(backend="google", api_key="k", target_lang="zho_Hant")
    assert build_translator(cfg).target == "zh-TW"
    cfg = TranslateConfig(backend="google", api_key="k", target_lang="zho_Hans")
    assert build_translator(cfg).target == "zh"


def test_google_refuses_a_target_with_no_iso_code():
    """49 of the 202 have no 639-1 code, and the API answers a bad one with a
    bare HTTP 400 that names the parameter, not the setting."""
    cfg = TranslateConfig(backend="google", api_key="k", target_lang="ceb_Latn")
    with pytest.raises(TranslatorError) as exc:
        build_translator(cfg)
    message = str(exc.value)
    assert "cannot translate into 'ceb_Latn' (Cebuano)" in message
    assert "ISO 639-1" in message


def test_google_lets_an_unsupported_source_be_detected():
    cfg = TranslateConfig(
        backend="google", api_key="k", source_lang="ceb_Latn", target_lang="jpn_Jpan"
    )
    assert build_translator(cfg).source is None


def test_google_sends_the_key_in_a_header_and_unescapes_the_reply():
    """The key must not travel in the URL, and v2 escapes its output."""
    seen: dict = {}

    class Recording(GoogleTranslator):
        def _post_json(self, url, payload, headers):
            seen.update(url=url, payload=payload, headers=headers)
            return {"data": {"translations": [{"translatedText": "It&#39;s 8 &amp; up"}]}}

    t = Recording("secret", target="ja", source="en", timeout=20.0)
    assert t.translate("It's 8 & up") == "It's 8 & up"
    assert seen["headers"]["X-goog-api-key"] == "secret"
    assert "secret" not in seen["url"]
    assert seen["payload"] == {"q": ["It's 8 & up"], "target": "ja", "format": "text", "source": "en"}


def test_google_needs_a_key():
    with pytest.raises(TranslatorError) as exc:
        build_translator(TranslateConfig(backend="google", target_lang="jpn_Jpan"))
    assert "google backend needs an api_key" in str(exc.value)


def test_the_custom_endpoint_needs_a_base_url():
    """There is no sensible default: guessing OpenAI would send the user's game
    text to a provider they never chose."""
    with pytest.raises(TranslatorError) as exc:
        build_translator(TranslateConfig(backend="chat", model="llama3.1:8b"))
    assert "base URL" in str(exc.value)


def test_the_custom_endpoint_runs_without_a_key():
    """llama.cpp / Ollama accept anything, so the generic backend must not
    demand a key the way the hosted providers do."""
    t = build_translator(
        TranslateConfig(backend="chat", model="llama3.1:8b", api_base="http://localhost:11434/v1")
    )
    assert t.api_key is None
    assert t.endpoint == "http://localhost:11434/v1/chat/completions"
    # ...and no header is sent, rather than a literal "Bearer None".
    assert "Authorization" not in t.auth_headers()


def test_a_remote_http_endpoint_is_refused():
    """The key rides in a header and the screen text in the body: plain http to
    another host hands both to anyone on the path."""
    cfg = TranslateConfig(
        backend="chat", model="m", api_base="http://inference.example.com/v1"
    )
    with pytest.raises(TranslatorError) as exc:
        build_translator(cfg)
    assert "plaintext" in str(exc.value) or "clear" in str(exc.value)


def test_a_remote_http_endpoint_is_allowed_with_the_opt_in():
    cfg = TranslateConfig(
        backend="chat",
        model="m",
        api_base="http://192.168.1.9:8080/v1",
        allow_insecure_http=True,
    )
    assert build_translator(cfg).endpoint == "http://192.168.1.9:8080/v1/chat/completions"


def test_https_is_never_questioned():
    cfg = TranslateConfig(backend="openai", api_key="k", api_base="https://gw.internal/v1")
    assert build_translator(cfg).base_url == "https://gw.internal/v1"


def test_a_nonsense_scheme_is_refused():
    with pytest.raises(TranslatorError):
        validate_base_url("ftp://example.com/v1")


def test_a_pasted_full_endpoint_url_is_trimmed_back_to_the_base():
    """Copying the URL out of a server's docs usually brings the path with it."""
    t = ChatCompletionsTranslator(
        None,
        model="m",
        base_url="http://localhost:11434/v1/chat/completions",
        key_required=False,
    )
    assert t.endpoint == "http://localhost:11434/v1/chat/completions"


def test_nllb_backends_get_the_flores_code_untouched():
    """Anything but the code NLLB was trained on is scored as `<unk>`."""
    cfg = TranslateConfig(backend="ct2", source_lang="kor_Hang", target_lang="ukr_Cyrl")
    t = build_translator(cfg)
    assert (t.source_lang, t.target_lang) == ("kor_Hang", "ukr_Cyrl")


def test_factory_rejects_unknown_backend():
    with pytest.raises(TranslatorError) as exc:
        build_translator(TranslateConfig(backend="nope"))
    assert "unknown translation backend" in str(exc.value)
    # The message should list the valid options.
    assert "openrouter" in str(exc.value)


def test_factory_passes_api_base_override(monkeypatch):
    monkeypatch.setenv("LINTRANSLATOR_API_KEY", "k")
    cfg = TranslateConfig(backend="chat", model="m", api_base="http://localhost:8080/v1")
    t = build_translator(cfg)
    assert t.endpoint == "http://localhost:8080/v1/chat/completions"
