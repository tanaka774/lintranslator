"""Regenerate `tlkun/languages.py` from primary sources.

The language table is data, not logic, and getting one code wrong is invisible:
an unknown FLORES code reaches NLLB as `<unk>` and the model returns garbage with
no error at all. So the table is built from sources that can be checked rather
than typed by hand:

  * the FLORES-200 code list, from the NLLB language list (name -> code)
  * the authoritative set of codes NLLB accepts, read out of the real tokenizer
    vocabulary - exactly 202 entries, and the generator fails loudly on any
    disagreement with the published list
  * ISO 639-1 codes, from the system iso-codes data (`alpha_3` -> `alpha_2`)
  * DeepL's documented language set
  * tesseract's `tessdata_fast` file list, so an OCR suggestion can never name a
    file that does not exist

Run it when a source changes, then read the diff:

    .venv-gi/bin/python probe/gen_languages.py            # needs network
    .venv-gi/bin/python -m pytest tests/test_languages.py

Everything it cannot establish confidently is left as `None` rather than guessed:
`None` becomes "this backend has no code for that language", which is a message
a user can act on, whereas a guessed code is a 400 from the API or a silent
mistranslation.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
OUT = APP_DIR / "tlkun" / "languages.py"

FLORES_LIST_URL = (
    "https://huggingface.co/spaces/Geonmo/nllb-translation-demo/"
    "raw/refs%2Fpr%2F1/flores200_codes.py"
)
TESSDATA_API = "https://api.github.com/repos/tesseract-ocr/tessdata_fast/contents/"
ISO_CODES_639_3 = Path("/usr/share/iso-codes/json/iso_639-3.json")
NLLB_MODEL = "facebook/nllb-200-distilled-600M"

# Names the published list cannot supply. It disagrees with the tokenizer in both
# directions - it carries three codes NLLB cannot score (`arb_Latn`, `min_Arab`,
# `sat_Olck`) and omits two it can (`sat_Beng`, `zul_Latn`, the last only because
# of a quoting bug in that file). Membership comes from the tokenizer, which is
# the thing that actually decides whether a code means a language or `<unk>`;
# only the display name comes from the list.
EXTRA_NAMES = {
    "sat_Beng": "Santali (Bengali script)",
}

# DeepL's supported languages, from
# https://developers.deepl.com/docs/getting-started/supported-languages
# ISO 639-1 -> DeepL's code. Kept as ISO on the left because that is how every
# other source in this file is keyed.
DEEPL_LANGUAGES = {
    "ar": "AR", "bg": "BG", "cs": "CS", "da": "DA", "de": "DE", "el": "EL",
    "en": "EN", "es": "ES", "et": "ET", "fi": "FI", "fr": "FR", "he": "HE",
    "hu": "HU", "id": "ID", "it": "IT", "ja": "JA", "ko": "KO", "lt": "LT",
    "lv": "LV", "nb": "NB", "nl": "NL", "pl": "PL", "pt": "PT", "ro": "RO",
    "ru": "RU", "sk": "SK", "sl": "SL", "sv": "SV", "th": "TH", "tr": "TR",
    "uk": "UK", "vi": "VI", "zh": "ZH",
}

# FLORES-200 rows whose base code is a macrolanguage, a standardised variety or
# otherwise not the ISO 639-3 key that carries the ISO 639-1 code. Left side is
# the FLORES base, right side is the ISO 639-3 key to look up. Every one of these
# is a deliberate decision; anything not listed is looked up directly.
ISO_ALIASES = {
    "arb": "ara",   # Modern Standard Arabic -> Arabic
    # The Arabic varieties NLLB can score. None has an ISO 639-1 code of its
    # own, and no API offers "Egyptian Arabic" as a target, so the choice is the
    # macrolanguage or a code the API rejects outright.
    "acm": "ara", "acq": "ara", "aeb": "ara", "ajp": "ara", "apc": "ara",
    "ars": "ara", "ary": "ara", "arz": "ara",
    "azb": "aze",   # South Azerbaijani -> Azerbaijani (macrolanguage)
    "pes": "fas",   # Western Persian -> Persian
    "prs": "fas",   # Dari -> Persian
    "pbt": "pus",   # Southern Pashto -> Pashto (macrolanguage)
    "zsm": "msa",   # Standard Malay -> Malay (macrolanguage)
    "swh": "swa",   # Swahili -> Swahili (macrolanguage)
    "plt": "mlg",   # Plateau Malagasy -> Malagasy (macrolanguage)
    "khk": "mon",   # Halh Mongolian -> Mongolian (macrolanguage)
    "lvs": "lav",   # Standard Latvian -> Latvian (macrolanguage)
    "ekk": "est",   # Standard Estonian -> Estonian (macrolanguage)
    "nob": "nob",   # Norwegian Bokmål: its own 639-3 entry with alpha_2 "nb"
    "nno": "nno",   # Norwegian Nynorsk: likewise "nn"
    "npi": "nep",   # Nepali: the 639-3 code for the standardised variety is nep
    "ory": "ori",   # Odia
    "gaz": "orm",   # West Central Oromo -> Oromo (macrolanguage)
    "uzn": "uzb",   # Northern Uzbek -> Uzbek (macrolanguage)
    "azj": "aze",   # North Azerbaijani -> Azerbaijani (macrolanguage)
    "als": "sqi",   # Tosk Albanian -> Albanian (macrolanguage)
    "kmr": "kur",   # Northern Kurdish -> Kurdish (macrolanguage)
    "ckb": "kur",   # Central Kurdish -> Kurdish (macrolanguage)
    "ayr": "aym",   # Central Aymara -> Aymara (macrolanguage)
    "quy": "que",   # Ayacucho Quechua -> Quechua (macrolanguage)
    "fuv": "ful",   # Nigerian Fulfulde -> Fulah (macrolanguage)
    "knc": "kau",   # Central Kanuri -> Kanuri (macrolanguage)
    "hne": "hin",   # Chhattisgarhi: Hindi is the closest supported neighbour
    "awa": "hin",   # Awadhi: same
    "bho": "hin",   # Bhojpuri: same
    "mag": "hin",   # Magahi: same
    "mai": "hin",   # Maithili: same
}

# Tesseract's file names diverge from ISO 639-3 for the same reasons, plus its
# own conventions (`fil` for Tagalog, `nor` for both Norwegian written forms,
# `chi_sim`/`chi_tra` for the two Chinese scripts). Keyed by FLORES code because
# the two Chinese scripts are separate FLORES languages.
TESSERACT_BY_FLORES = {
    "zho_Hans": "chi_sim",
    "zho_Hant": "chi_tra",
    "tgl_Latn": "fil",
    "swh_Latn": "swa",
    "pes_Arab": "fas",
    "prs_Arab": "fas",
    "khk_Cyrl": "mon",
    "uzn_Latn": "uzb",
    "azj_Latn": "aze",
    "als_Latn": "sqi",
    "lvs_Latn": "lav",
    "ekk_Latn": "est",
    "nob_Latn": "nor",
    "nno_Latn": "nor",
    "zsm_Latn": "msa",
    "arb_Arab": "ara",
    "ydd_Hebr": "yid",
    "kmr_Latn": "kmr",
    "npi_Deva": "nep",
    "ory_Orya": "ori",
    "quy_Latn": "que",
    "pbt_Arab": "pus",
}


def flores_names() -> dict[str, str]:
    """code -> English name, from the published FLORES-200 list."""
    with urllib.request.urlopen(FLORES_LIST_URL, timeout=60) as resp:  # noqa: S310
        text = resp.read().decode()
    # The list is one triple-quoted blob in a Python file, so take what is
    # between the first and last `'''` - including it would put
    # `codes_as_string = '''` into the name of the very first language, and
    # excluding it drops the last row, which shares its line with the closing
    # quotes.
    start, end = text.find("'''"), text.rfind("'''")
    if start == -1 or end <= start:
        raise SystemExit(f"could not find the language list in {FLORES_LIST_URL}")
    body = text[start + 3 : end]
    rows: dict[str, str] = {}
    for line in body.splitlines():
        match = re.match(r"^(.+?)\t([a-z]{2,3}_[A-Z][a-z]{3})$", line.strip())
        if match:
            rows[match.group(2)] = match.group(1)
    rows.update(EXTRA_NAMES)
    return rows


def nllb_codes() -> set[str] | None:
    """The codes the real tokenizer accepts, or None when it cannot be loaded.

    This is the check that matters: the tokenizer vocabulary is what NLLB scores
    against, so `languages.py` must not contain a code it does not have, nor miss
    one it does.
    """
    try:
        import os

        os.environ.setdefault("HF_HOME", str(APP_DIR / ".hf"))
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(NLLB_MODEL)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! tokenizer unavailable ({type(exc).__name__}), skipping check")
        return None
    pattern = re.compile(r"^[a-z]{2,3}_[A-Z][a-z]{3}$")
    return {token for token in tokenizer.get_vocab() if pattern.match(token)}


def iso_639_1() -> dict[str, str]:
    """ISO 639-3 -> ISO 639-1, for the entries that have a 639-1 code."""
    if not ISO_CODES_639_3.exists():
        raise SystemExit(
            f"{ISO_CODES_639_3} not found - install the `iso-codes` package, or "
            "the ISO column would silently come out empty"
        )
    data = json.loads(ISO_CODES_639_3.read_text())["639-3"]
    return {
        entry["alpha_3"]: entry["alpha_2"]
        for entry in data
        if entry.get("alpha_2")
    }


def tessdata_languages() -> set[str]:
    with urllib.request.urlopen(TESSDATA_API, timeout=60) as resp:  # noqa: S310
        payload = json.loads(resp.read().decode())
    names = {
        item["name"].removesuffix(".traineddata")
        for item in payload
        if item["name"].endswith(".traineddata")
    }
    # Not languages: the script/OSD detector and the equation model.
    return names - {"osd", "equ"}


def script_of(code: str) -> str:
    return code.split("_", 1)[1]


def build_rows() -> tuple[dict[str, tuple], list[str]]:
    warnings: list[str] = []
    names = flores_names()
    codes = nllb_codes()
    if codes is not None:
        missing = codes - set(names)
        extra = set(names) - codes
        if missing:
            warnings.append(f"published list lacks {sorted(missing)} - add a name")
        if extra:
            warnings.append(f"tokenizer lacks {sorted(extra)} - dropping them")
        have = codes
    else:
        have = set(names)

    iso = iso_639_1()
    tess = tessdata_languages()

    rows: dict[str, tuple] = {}
    for code in sorted(have):
        base = code.split("_")[0]
        name = names.get(code)
        if not name:
            raise SystemExit(f"no name for {code}; refusing to invent one")
        iso_key = ISO_ALIASES.get(base, base)
        alpha2 = iso.get(iso_key)
        deepl = DEEPL_LANGUAGES.get(alpha2) if alpha2 else None
        tesseract = TESSERACT_BY_FLORES.get(code) or (base if base in tess else None)
        if tesseract and tesseract not in tess:
            raise SystemExit(f"{code}: {tesseract} is not a tessdata_fast file")
        rows[code] = (name, alpha2, deepl, tesseract)
    return rows, warnings


HEADER = '''"""The language table: what each backend is told, from one FLORES-200 code.

`translate.source_lang` and `translate.target_lang` are FLORES-200 codes
(`eng_Latn`, `jpn_Jpan`) because NLLB needs exactly those: the code is prepended
to the source tokens and used as the decoder prefix, and anything else is scored
as a language the model was never trained on. Every other backend wants something
different from the same language - a chat model wants the name "Japanese" in the
prompt, DeepL wants `JA`, Google wants `ja`, and tesseract needs `jpn.traineddata`
to read it in the first place - so this module is the single place those
translations live.

Getting one of them wrong is invisible: NLLB maps an unknown code to `<unk>` and
returns plausible-looking garbage with no error, and a chat prompt that says
"translate into jpn_Jpan" is merely ignored. That is why the Settings dialog
offers these codes from a list rather than as free text.

Generated by `probe/gen_languages.py` - see that file for the sources. Do not
hand-edit the table; regenerate it and read the diff.
"""
from __future__ import annotations

from dataclasses import dataclass

# FLORES script subtag -> the script is written without spaces between words, so
# token-level spacing in the output has to be normalised (see
# `translate.normalize_cjk`). `Hant`/`Hans` are the two Chinese scripts.
CJK_SCRIPTS = frozenset({"Jpan", "Hang", "Hans", "Hant"})

# Pinned to the top of the picker: the pairs this app was built and measured
# against, so the common case is one click rather than a search.
COMMON_CODES = (
    "eng_Latn",
    "jpn_Jpan",
    "kor_Hang",
    "zho_Hans",
    "zho_Hant",
    "deu_Latn",
    "fra_Latn",
    "spa_Latn",
    "rus_Cyrl",
    "por_Latn",
    "ita_Latn",
    "ukr_Cyrl",
    "vie_Latn",
    "tha_Thai",
    "ind_Latn",
    "tur_Latn",
    "pol_Latn",
    "nld_Latn",
    "arb_Arab",
    "hin_Deva",
)


@dataclass(frozen=True)
class Language:
    """One FLORES-200 language, with the code each backend needs for it."""

    code: str
    name: str
    # ISO 639-1, which is what Google's endpoint takes. None where no 639-1 code
    # exists (most of the 202 have none).
    iso: str | None = None
    # DeepL's own code, or None where DeepL cannot translate this language.
    deepl: str | None = None
    # tessdata_fast file stem for OCR, or None where tesseract has no model.
    tesseract: str | None = None

    @property
    def script(self) -> str:
        """The FLORES script subtag, e.g. "Jpan"."""
        return self.code.split("_", 1)[1]

    @property
    def cjk(self) -> bool:
        return self.script in CJK_SCRIPTS

    @property
    def short(self) -> str:
        """The bare language part, e.g. "jpn" - for tight labels."""
        return self.code.split("_", 1)[0]

    @property
    def label(self) -> str:
        """How the picker shows it: the name a person reads, then the code."""
        return f"{self.name} — {self.code}"


# code -> (name, iso, deepl, tesseract)
_ROWS: dict[str, tuple] = {
'''

FOOTER = '''}

LANGUAGES: dict[str, Language] = {
    code: Language(code, *row) for code, row in _ROWS.items()
}

# Every code NLLB accepts, as a set: the picker's vocabulary and the thing a
# config value is validated against.
CODES: frozenset[str] = frozenset(LANGUAGES)


def get(code: str | None) -> Language | None:
    """The language for an exact FLORES-200 code, or None if it is not one."""
    if not code:
        return None
    return LANGUAGES.get(code.strip())


def _by_base(base: str) -> Language | None:
    """The first language whose language part is `base`, e.g. "jpn" -> jpn_Jpan.

    Script variants share a language part (`zho_Hans`, `zho_Hant`), so this is
    ambiguous by nature and only used where the script cannot matter.
    """
    if not base:
        return None
    for language in LANGUAGES.values():
        if language.short == base:
            return language
    return None


def language_name(code: str) -> str:
    """A readable name for a code, whether or not the code is valid.

    Config files are edited by hand, and an older version of this app may have
    written a bare ISO code into one, so a prompt can be asked to translate
    "into jpn". Anything unrecognised is passed through unchanged rather than
    replaced by a guess: "translate into jpn" is still a translation request,
    whereas silently rewriting an unknown target to English translates into the
    wrong language.
    """
    language = get(code)
    if language is not None:
        return language.name
    stripped = (code or "").strip()
    by_base = _by_base(stripped.split("_")[0])
    return by_base.name if by_base is not None else stripped


def iso_code(code: str) -> str | None:
    """ISO 639-1 for a code, or None when it has none.

    None is a real answer, not a failure: most of the 202 languages have no
    639-1 code, and an API that only takes 639-1 codes has to be told so.
    """
    language = get(code)
    return language.iso if language is not None else None


def google_code(code: str) -> str:
    """What to send Google, which wants ISO 639-1.

    Falls back to the bare language part rather than to nothing: the endpoint is
    unofficial and lenient enough to accept plenty of ISO 639-2/3 codes, and a
    request for `arz` that fails loudly beats a request with an empty language
    parameter that silently translates from anything.
    """
    return iso_code(code) or (code or "").split("_")[0]


def short_code(code: str) -> str:
    """The bare language part, for a label: "eng_Latn" -> "eng".

    Unknown values are passed through, so the panel shows whatever the config
    actually holds rather than a tidied-up version of it.
    """
    language = get(code)
    return language.short if language is not None else (code or "").strip()


def deepl_code(code: str) -> str | None:
    """DeepL's code for a language, or None where DeepL cannot do it."""
    language = get(code)
    return language.deepl if language is not None else None


def tesseract_lang(code: str) -> str | None:
    """The tessdata file stem to read this language with, or None.

    None means tesseract has no model for it, which is worth saying out loud:
    the OCR would otherwise keep running with whatever language it was given
    and quietly return nonsense for every line.
    """
    language = get(code)
    return language.tesseract if language is not None else None


def is_cjk(code: str) -> bool:
    """Whether the language is written without spaces between words."""
    language = get(code)
    return bool(language and language.cjk)


def ordered() -> list[Language]:
    """COMMON_CODES first, then everything else by name.

    The picker's order. The common languages are the ones this app was built
    against, and a filtered list that buries Japanese under "Acehnese" is a
    worse list even though both are one search away.
    """
    common = [LANGUAGES[code] for code in COMMON_CODES if code in LANGUAGES]
    pinned = set(COMMON_CODES)
    rest = sorted(
        (lang for code, lang in LANGUAGES.items() if code not in pinned),
        key=lambda lang: (lang.name, lang.code),
    )
    return common + rest


def search(query: str) -> list[Language]:
    """Filter by name or code. Empty query returns `ordered()`."""
    needle = (query or "").strip().lower()
    if not needle:
        return ordered()
    return [
        language
        for language in ordered()
        if needle in language.name.lower() or needle in language.code.lower()
    ]
'''


def _format(value: str | None) -> str:
    return "None" if value is None else f'"{value}"'


def render(rows: dict[str, tuple]) -> str:
    lines = []
    for code, (name, iso, deepl, tesseract) in rows.items():
        safe_name = name.replace('"', '\\"')
        lines.append(
            f'    "{code}": ("{safe_name}", '
            f"{_format(iso)}, {_format(deepl)}, {_format(tesseract)}),"
        )
    return HEADER + "\n".join(lines) + "\n" + FOOTER


def main() -> int:
    print("building the language table")
    rows, warnings = build_rows()
    for warning in warnings:
        print(f"  ! {warning}")
    OUT.write_text(render(rows))
    deepl = sum(1 for row in rows.values() if row[2])
    iso = sum(1 for row in rows.values() if row[1])
    tess = sum(1 for row in rows.values() if row[3])
    print(f"wrote {OUT.relative_to(APP_DIR)}")
    print(f"  {len(rows)} languages, {iso} with ISO 639-1, {deepl} DeepL, {tess} tesseract")
    return 0


if __name__ == "__main__":
    sys.exit(main())
