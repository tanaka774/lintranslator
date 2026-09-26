# Install

Everything between a fresh Linux desktop and a translated dialogue box: the
system packages, the one-time model conversion and what it costs in disk, where
state is kept, and the licences the models come under. The short version is in the
[README](../README.md#install), which is also where the command line, the backends
and the keep-above recipes live.

## Compatibility

| target | status | why |
|---|---|---|
| **Linux + KDE Plasma 6 / Wayland** | **verified** | the environment every measurement in [`probe/`](../probe/) was taken on |
| **Linux + X11 / XWayland** | works | and window placement and keep-above genuinely work here, unlike native Wayland |
| **Linux + Wayland, other compositors** | depends on the portal backend | the parts that are not KDE are the hotkey and the keep-above workaround |
| **Linux + GNOME** | core expected to work, untested here | `xdg-desktop-portal-gnome` implements Screenshot and ScreenCast, so capture should be fine; the automatic hotkey will not bind |
| **macOS / Windows** | **not supported** | not merely untested: the capture and hotkey layers have nothing to talk to |

Linux is a hard requirement, not a preference. Three layers assume it, and none of
them has a fallback:

* **Capture** is `org.freedesktop.portal.Screenshot` / `ScreenCast` over D-Bus
  (`portal.py`). There is no other grabber in the tree, because on Wayland
  `mss`, `import` and X11 grabs return black.
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
portal, plus the KWin window rule recommended for keep-above under native Wayland.
Under GNOME, expect to bind the shortcut by hand and to manage stacking yourself.

In this documentation, "verified" and "measured" mean this machine: KDE Plasma 6,
Wayland, tesseract on CPU, and (for translation) the OCR and latency numbers in
`probe/`. Every other row of the table above is reasoned from the code, not
observed.

## Requirements

Requires Linux with a Wayland session (or X11), Python 3.12-3.14, and tesseract
(see [Compatibility](#compatibility) above).

There is no package yet - no AUR, Flatpak or PyPI entry - so this begins with the
source. Four commands, and no model download: a fresh config has no translation
backend selected, so nothing is sent anywhere and the local weights are opt-in.

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
uv pip install --python .venv/bin/python -e .

# 4. and what is missing, in plain language, with a non-zero exit if anything is.
.venv/bin/python -m lintranslator check
```

Then `.venv/bin/python -m lintranslator gui`, and choose a backend, an API key and
a model id in the picker's **Settings**. The only fetch along the way is the
tesseract language data, 4.1 MB for `eng`.

Running a model locally is a server, not an extra: this app loads no model
itself. [Local translation](../README.md#local-translation) in the README is the
whole recipe - a 1.13 GB GGUF, a Modelfile, one `ollama create`, and the four
fields to fill in Settings.

It does not have to be a checkout. Installing from the repository works the same
way and keeps no source tree around:

```bash
uv venv --python /usr/bin/python3 --system-site-packages ~/.venvs/lintranslator
uv pip install --python ~/.venvs/lintranslator/bin/python \
    "lintranslator @ git+https://github.com/tanaka774/lintranslator"
~/.venvs/lintranslator/bin/lintranslator check
```

## What is downloaded, and when

Nothing is downloaded at install time, and the default configuration does not
need anything downloaded afterwards either: a fresh config has no translation
backend selected, so it reads the region and translates nothing until one is
chosen in Settings. Which backend you use decides the rest, and the difference is
large enough to be worth choosing deliberately.

| backend | what is fetched | when |
|---|---|---|
| **`none`, the default in a fresh config** | **nothing at all** - and nothing is sent anywhere either | - |
| DeepL, Google, OpenRouter, OpenAI | **nothing from disk** - no weights, no cache, no model | - |
| `chat` - Ollama, llama.cpp, vLLM, a gateway | **nothing by this app**. The model is yours, fetched by your server | - |

No path through this app starts a download of a translation model, because it has
none to download. Tesseract language data is the only thing it fetches for
itself, and it is described at the end of this section.

So an API user installs: the base package, `tesseract`, PyGObject from the
distro, and 4.1 MB of language data. A local-model user installs a model server
and its weights, which are theirs to place and to delete.

The commands in this document are then written as `.venv/bin/python -m
lintranslator ...`; with the venv above, use its `lintranslator` script instead.
Either way, `lintranslator install-desktop` is what puts the app in the
application menu, which is what gives it the application id the global Re-read
hotkey needs. `lintranslator shortcut` prints the setup.

The other extras are for things the app does not need to translate a line:
`.[calibrate]` adds numpy for the region auto-detection helper, `.[x11]` adds
python-xlib for keeping the panel above other windows under XWayland (without it
the panel runs and says why it cannot stay on top), and `.[test]` is what the
suite needs. There is no translation extra: the `ct2` and `local` extras were
removed with the backends they installed.

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

## Where it keeps things

State lives in the XDG directories, not next to the source:

| what | where | mode |
|---|---|---|
| `config.json` | `~/.config/lintranslator/config.json` (`$XDG_CONFIG_HOME`) | **0600** |
| tessdata | `~/.local/share/lintranslator/` (`$XDG_DATA_HOME`) | — |
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
translate backend: openrouter (English->Japanese)
  model: google/gemini-2.5-flash-lite
  api base: (provider default)
  api key: set via config.json (73 chars)
languages        : eng_Latn -> jpn_Jpan
PyGObject (GUI)  : installed
RESULT: ready
```

A config left on a backend this version has no implementation for - `ct2` and
`local` are the two such values - is reported and moved to `none`, rather than
loading and then failing on every line.

(`languages` shows the FLORES-200 pair the app works in; each backend is handed
its own form of it. The key is described rather than printed - ten characters of
it would be ten characters too many.)

Anything wrong is named rather than implied: a missing tesseract, a language pair
outside the FLORES-200 table, a missing PyGObject - the
one dependency pip cannot install, so it is checked by name and the distro
package is printed - and any config problem, including a key this version does
not know (`config warning   : ignoring unknown config key(s): ocr.typo_key`) or a
file that will not parse, in which case the defaults are used and the reason is
shown. The exit status is 0 only when everything checked out, so `check` is
usable from a packaging script.

## Model licences

There are none to state, and that is deliberate. LinTranslator ships no model,
downloads no model and converts no model: every backend talks to an endpoint,
either a hosted API or a server you run yourself, so the only model licence in
play is the one on whatever you choose to run.

The model the README suggests for local translation, `tencent/Hy-MT2-1.8B`, is
**Apache-2.0**, and it is fetched by *your* Ollama or llama.cpp, not by this app.
Whatever you serve instead is your choice, under its own terms — including the
language codes it expects, which is why the app's own picker is FLORES-200 and the
prompt carries the language names.

What remains third-party is data and libraries: tesseract language data
(Apache-2.0), the Python dependencies, and the LGPL GUI bindings that come from
the distribution. `NOTICE` — shipped with the package as well as kept in the
repository root — lists all of it. LinTranslator itself is MIT licensed, separate
from every data and library licence above — see `LICENSE`.
