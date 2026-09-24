# Install

Everything between a fresh Linux desktop and a translated dialogue box: the
system packages, the one-time model conversion and what it costs in disk, where
state is kept, and the licences the models come under. The three-command
version is in the [README](../README.md#install).

See also [using LinTranslator](guide.md) once it is running, and
[why it works this way](design.md) for the reasoning behind the choices.

## Compatibility

| target | status | why |
|---|---|---|
| **Linux + KDE Plasma 6 / Wayland** | **verified** | the environment every measurement in the [design notes](design.md) was taken on |
| **Linux + X11 / XWayland** | works | and window placement and keep-above genuinely work here, unlike native Wayland |
| **Linux + Wayland, other compositors** | depends on the portal backend | the parts that are not KDE are the hotkey and the keep-above workaround |
| **Linux + GNOME** | core expected to work, untested here | `xdg-desktop-portal-gnome` implements Screenshot and ScreenCast, so capture should be fine; the automatic hotkey will not bind |
| **macOS / Windows** | **not supported** | not merely untested: the capture and hotkey layers have nothing to talk to |

Linux is a hard requirement, not a preference. Three layers assume it, and none of
them has a fallback:

* **Capture** is `org.freedesktop.portal.Screenshot` / `ScreenCast` over D-Bus
  (`portal.py`). There is no other grabber in the tree, because on Wayland
  `mss`, `import` and X11 grabs return black - see
  [why it works this way](design.md#why-it-works-this-way).
  No portal, no pixels.
* **The global hotkey** is `org.freedesktop.portal.GlobalShortcuts` (`hotkey.py`).
  KDE implements it; the compositor shows its own binding dialog and then sends
  the key. Coverage elsewhere is patchy: `xdg-desktop-portal-gnome` does not
  implement the interface at all
  ([issue 197](https://gitlab.gnome.org/GNOME/xdg-desktop-portal-gnome/-/work_items/197)),
  so outside KDE expect to bind the shortcut by hand. Where the portal is absent
  the app still runs - the hotkey is best-effort by design, every failure goes to
  the status line, and the fallback is a desktop custom shortcut running
  `lintranslator reread` over the control socket.
* **The control socket** lives in `$XDG_RUNTIME_DIR` (`control.py`), and the
  launcher that grants the app the *application id* the portal demands is a
  `.desktop` file, installed by `lintranslator install-desktop`.

There are no `sys.platform` checks anywhere in `lintranslator/` - not as a guard, and not
as a portability shim. Nothing was written with another OS in mind.

The GUI itself is plain GTK4, so it is not a KDE application: it runs on any
Wayland or X11 desktop with PyGObject. What is KDE-specific is the shortcut
portal, plus the KWin window rule recommended for keep-above under native Wayland
(see [Always on top](guide.md#always-on-top-read-this-first)). Under GNOME, expect
to bind the shortcut by hand and to manage stacking yourself.

In this documentation, "verified" and "measured" mean this machine: KDE Plasma 6,
Wayland, CT2 on CPU. Every other row of the table above is reasoned from the code,
not observed.

## Requirements

Requires Linux with a Wayland session (or X11), Python 3.12-3.14, and tesseract
(see [Compatibility](#compatibility) above).

There is no package yet - no AUR, Flatpak or PyPI entry - so this begins with the
source. It is five commands and one long download.

```bash
# 1. the code
git clone https://github.com/tanaka774/lintranslator
cd lintranslator

# 2. tesseract, and the GTK stack. PyGObject and pycairo come from the distro
#    rather than from pip: they are bindings to system libraries, and a pip
#    build of them needs a compiler and the cairo headers.
sudo pacman -S tesseract python-gobject python-cairo   # Arch / CachyOS
# sudo apt install tesseract-ocr python3-gi python3-gi-cairo python3-cairo \
#                  gir1.2-gtk-4.0                       # Debian / Ubuntu

# 3. environment. The interpreter has to be the distro's own Python: PyGObject is
#    a compiled binding to that exact interpreter, so a uv-managed 3.12 cannot
#    see it however the venv is built. --system-site-packages is what exposes it.
uv venv --python /usr/bin/python3 --system-site-packages .venv
# without uv:  python3 -m venv --system-site-packages .venv
uv pip install --python .venv/bin/python -e '.[ct2]'

# 4. convert the model to int8 - one time, and it is a download, not a build:
#    ~2.5 GB of fp32 checkpoint into the HuggingFace cache, then ~630 MB of
#    int8 weights into ~/.local/share/lintranslator/ct2/. Both numbers are on
#    disk at once - see "What the conversion actually costs".
.venv/bin/python -m lintranslator convert

# 5. and what is missing, in plain language, with a non-zero exit if anything is.
.venv/bin/python -m lintranslator check
```

It does not have to be a checkout. Installing from the repository works the same
way and keeps no source tree around:

```bash
uv venv --python /usr/bin/python3 --system-site-packages ~/.venvs/lintranslator
uv pip install --python ~/.venvs/lintranslator/bin/python \
    "lintranslator[ct2] @ git+https://github.com/tanaka774/lintranslator"
~/.venvs/lintranslator/bin/lintranslator check
```

## What is downloaded, and when

Nothing is downloaded at install time. Which backend you translate with decides
everything after that, and the difference is large enough to be worth choosing
deliberately. **Translating through a hosted backend means skipping step 4
entirely - install plain `-e .` and no model is involved at all.**

| backend | what is fetched | when |
|---|---|---|
| DeepL, OpenRouter, OpenAI, your own endpoint | **nothing from HuggingFace** - no ctranslate2, no torch, no weights | - |
| `ct2`, the default in a fresh config | the converted int8 weights, 629 MB, plus ~22 MB of tokenizer | the weights when *you* run `lintranslator convert`; the tokenizer on first use |
| `local` | the fp32 checkpoint, 2.46 GB, plus the same tokenizer | automatically, on the first translation |

The local model is what a fresh config defaults to, not something the install
requires: it is a settings change, and the picker's Settings dialog is where it
happens.

In every one of those cases the OCR side fetches tesseract language data on first
use - 4.1 MB for `eng`, pinned to a revision and checksum-verified. That one
cannot be avoided, because OCR always runs locally: reading the screen is the
part of the pipeline that has no remote equivalent. A language without a pinned
checksum is not fetched automatically (see `ocr.allow_unverified_tessdata`).

So an API user installs: the base package, `tesseract`, PyGObject from the
distro, and 4.1 MB of language data. Not three gigabytes.

The commands in this document are then written as `.venv/bin/python -m
lintranslator ...`; with the venv above, use its `lintranslator` script instead.
Either way, `lintranslator install-desktop` is what puts the app in the
application menu - see [the hotkey section](guide.md#re-read-when-a-line-comes-out-wrong).

`.[ct2]` is the fast path. `.[local]` is the older transformers route: the same
2.5 GB checkpoint, loaded through torch instead of CTranslate2, which is slower
and needs torch installed as well. It exists for models that cannot be converted
and is no longer the default.

The other extras are for things the app does not need to translate a line:
`.[calibrate]` adds numpy for the region auto-detection helper, `.[x11]` adds
python-xlib for keeping the panel above other windows under XWayland (without it
the panel runs and says why it cannot stay on top), and `.[test]` is what the
suite needs.

## What the conversion actually costs

`lintranslator convert` is the only step that downloads a *model* — the first OCR
run separately fetches the language data described below, and a remote backend
needs the network while it translates — and it moves more data than the "~600 MB"
this project used to claim. Measured against the current `main` of the model repo:

| | size | where it goes |
|---|---|---|
| fp32 checkpoint | **2.46 GB** — one `pytorch_model.bin` | `~/.cache/huggingface/hub/` (or `$HF_HOME`) |
| tokenizer | 17 MB `tokenizer.json` + 4.9 MB `sentencepiece.bpe.model` | same cache |
| **int8 weights** | **629 MB** — `model.bin` 623 MB + `shared_vocabulary.json` 5.9 MB | `~/.local/share/lintranslator/ct2/` |

So budget **~3 GB** for the default backend, and **~630 MB** if you clean up
afterwards.

**Nothing deletes the checkpoint.** After the conversion only the tokenizer is
still read — the `ct2` backend loads it once per run — so the 2.5 GB of fp32
weights are dead weight, and re-converting a second model adds another 2.5 GB
next to them. `remove` shows what is there and takes it back:

```bash
lintranslator remove                # what is on disk, and what each piece costs to get back
lintranslator remove checkpoint     # the 2.5 GB, safe once convert has finished
lintranslator remove model          # the converted weights (asks first)
```

The report names the exact paths, so nothing is deleted blindly, and it says how
much of the HuggingFace cache belongs to *other* projects — the app shares that
directory with whatever else you run, and only ever removes its own repository
inside it. It also flags a config that points `ct2_model_dir` somewhere the
weights are not, and says where they actually are.

`remove checkpoint` is the same thing as `huggingface-cli delete-cache`, without
the menu. It costs a
22 MB re-download of the tokenizer the next time the model is loaded, which is
the whole of what the `ct2` path fetches.

If you ever find a HuggingFace cache twice the size of one checkpoint, it is two
*revisions* of the same model rather than two formats: this one changed its
weights file from `model.safetensors` to `pytorch_model.bin`, and both revisions
got cached. A fresh install downloads one. The `.no_exist/` directory in the
cache is what records the lookup that decides it — transformers asks for
safetensors, is told 404 for the current revision, and falls back to the `.bin`.

`eng.traineddata` / `jpn.traineddata` are downloaded on first use, so no root is
needed — and the first run says so rather than going quiet: the panel's status
row and the CLI both print `downloading eng.traineddata (4.1 MB) -> …` while it
happens, and the picker does its preparation on a background thread so a slow
link cannot freeze the window it is drawing in. The fetch is pinned to a `tessdata_fast`
revision and the file is checked against a SHA-256 before it is installed — a
language file is a binary that tesseract parses. The pinned set is eng, jpn, kor,
chi_sim, chi_tra, rus, deu, fra, spa, por, ita, pol, tur, vie, tha, ara; a
language without a pinned checksum is not downloaded automatically — install it
from the distro, or set `ocr.allow_unverified_tessdata: true` to fetch it anyway.

The HuggingFace cache is used by the converter and by the `local` fallback
backend. To share it with your other projects, export
`HF_HOME=~/.cache/huggingface` before running; the `ct2` path only ever reads
the tokenizer out of it.

## Where it keeps things

State lives in the XDG directories, not next to the source:

| what | where | mode |
|---|---|---|
| `config.json` | `~/.config/lintranslator/config.json` (`$XDG_CONFIG_HOME`) | **0600** |
| converted weights, tessdata | `~/.local/share/lintranslator/` (`$XDG_DATA_HOME`) | — |
| the optional translation cache, the control socket | `~/.cache/lintranslator/` (`$XDG_CACHE_HOME`); the socket prefers `$XDG_RUNTIME_DIR/lintranslator.sock` | — |

`LINTRANSLATOR_HOME` puts all three under one directory instead, which is what a
portable install (or a test run) wants.

The config is written **0600 at creation**, not chmodded afterwards, because it
can hold an API key in plain text; an existing config file is tightened to 0600
when it is read.

Verify everything:

```bash
.venv/bin/python -m lintranslator check
```

```
tesseract binary : tesseract 5.5.3
tessdata dir     : /home/you/.local/share/lintranslator/tessdata
  eng           : ok
portal ScreenCast: v5
portal Screenshot: v2
region           : fraction (0.10, 0.78, 0.80, 0.12)
translate backend: ct2 (eng_Latn->jpn_Jpan)
  model: /home/you/.local/share/lintranslator/ct2/nllb-600m-int8 (629 MB)
  ctranslate2: installed
languages        : eng_Latn -> jpn_Jpan
PyGObject (GUI)  : installed
RESULT: ready
```

Anything wrong is named rather than implied: a missing tesseract, a model that has
not been converted, a language pair NLLB cannot score, a missing PyGObject - the
one dependency pip cannot install, so it is checked by name and the distro
package is printed - and any config problem, including a key this version does
not know (`config warning   : ignoring unknown config key(s): ocr.typo_key`) or a
file that will not parse, in which case the defaults are used and the reason is
shown. The exit status is 0 only when everything checked out, so `check` is
usable from a packaging script.

## Model licences

The default local model, `facebook/nllb-200-distilled-600M`, is **CC-BY-NC-4.0**,
which permits non-commercial use only. LinTranslator does not bundle or redistribute the
weights — `lintranslator convert` downloads them from HuggingFace on your machine — but
anyone shipping this app with pre-converted weights, or using the local backend
commercially, is bound by that licence. Settings states the licence next to the
Local backends, and `NOTICE` — shipped with the package as well as kept in the
repository root — lists the third-party terms, along with the permissive
alternatives: `facebook/m2m100_418M` is MIT
and `Helsinki-NLP/opus-mt-en-jap` is Apache-2.0, though a different model family
expects its own language codes, not NLLB's FLORES-200 ones. The hosted backends
have no local model licence at all. LinTranslator itself is MIT licensed, separate
from every model and data licence above — see `LICENSE`.
