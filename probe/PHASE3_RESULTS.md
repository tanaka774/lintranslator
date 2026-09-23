# Phase 3 results — glossary and int8 backend

## Speed: CTranslate2 int8 vs transformers

Same model (NLLB-200-distilled-600M), same lines, same 8 CPU threads:

| line | transformers | CTranslate2 int8 | speedup |
|---|---|---|---|
| long record line (137 chars) | 1.56 s | **0.34 s** | 4.6x |
| "Manager! The abnormality is approaching." | 0.90 s | **0.20 s** | 4.5x |
| "Don't worry. I have already calculated..." | 0.84 s | **0.16 s** | 5.3x |

| | transformers | CTranslate2 int8 |
|---|---|---|
| weights on disk | 4.7 GB | **600 MB** |
| warm load | 3.2 s | **2.9 s** |
| pipeline `ready in` | 3.1-4.8 s | **2.5 s** |

Output is equivalent. One quality nuance: the polite form is sometimes dropped
(`計算しました` -> `計算した`). That is a decoding difference, not a bug, and the
glossary or `beam_size` can address it.

Conversion took 5 seconds (`python -m tlkun convert`).

## Glossary

Implemented as `tlkun/glossary.py` with pre- and post-translation maps.

**Post is the default and the useful one.** A plain `{"Term": "訳"}` entry patches
the output, which is reliable and cheap:

```
without glossary  管理者異常が近づいてる戦闘準備
with glossary     マネージャー異常が近づいてる戦闘準備
```

### The pre-map I removed after measuring

The obvious approach for "Manager" is to rewrite the source so the model does not
read it as a job title. Measured against the real model, it made things worse:

| source | NLLB output |
|---|---|
| `Manager! The abnormality is approaching.` | `管理者異常が近づいてる戦闘準備` |
| `Executive Manager! The abnormality ...` | `管理者異常が近づいてる戦闘準備` (no change) |
| `Manager, the results are in.` | `管理者結果が出ました` |
| `Executive Manager, the results are in.` | `経営責任者成果が届きました` (**"CEO"** - worse) |

So the built-in Limbus glossary is post-only, and the code carries a comment
recording the measurements so nobody re-adds the pre-map on intuition.

### The CJK boundary bug

First implementation used `\b` word boundaries. It silently failed on the exact
case the glossary exists for: Python's `\b` treats CJK as word characters, so
between `者` and `異` there is no boundary and `管理者` never matched inside
`管理者異常`.

Fixed with ASCII-only lookarounds (`(?<![A-Za-z0-9_])` / `(?![A-Za-z0-9_])`), which
stops English terms matching mid-word (`Managers` is left alone) while letting
Japanese terms match next to more Japanese.

### Cache interaction

The cache stores the **raw model output**; the glossary is applied on the way out.
Caching post-glossary text would mean an edited entry appears to do nothing for
any line already seen - the config looks right and the output does not change.
Two tests pin this: swapping the glossary re-maps cached entries without calling
the model again, and a `pre` rewrite changes the cache key (so it is not a stale
hit for a different question).

## Multi-line display

`OcrResult` now exposes both forms:

* `text` - lines joined with spaces, for the translator. Limbus wraps dialogue
  mid-sentence, so joining keeps the sentence intact.
* `display_text` - line breaks preserved, for the panel, so the UI does not show
  one long run where the game showed two lines.

Measured on the reference screenshot: `visual_rows: 2`, translation input joined
correctly, output `[この事件は記録として保存されるべきであると決定された. ...]` in 0.35 s.

## Tests

60 total, up from 41. The 19 new ones cover: English and CJK matching, mid-word
rejection, longest-key-wins, no cascading replacements, case sensitivity,
pre/post independence, flat vs structured config, merge precedence, the built-in
Limbus entry, glossary-on-cache-hit, glossary-swap without re-translation, pre-map
cache keys, and the text/display_text split.

## Configuration change

`translate.backend` now defaults to `"ct2"`. Existing `config.json` files that
pin `"local"` keep working - they just stay on the slow path until changed, and
`tlkun check` now reports which model is in use and whether it is present.

## Reproduce

```bash
.venv-gi/bin/python -m tlkun convert          # ~5 s, 600 MB
.venv-gi/bin/python -m tlkun check            # reports model size + backend
.venv-gi/bin/python -m pytest tests/ -q       # 60 tests
```
