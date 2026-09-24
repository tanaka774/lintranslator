"""lintranslator command line interface.

    lintranslator check                  verify tesseract, tessdata, portal, backend
    lintranslator grab  -o shot.png      capture the configured region once
    lintranslator read                    OCR + print the region (no translation)
    lintranslator run                     full pipeline: capture -> OCR -> translate
    lintranslator region --x ... --w ...  update the stored region
    lintranslator gui --pick              pick the region visually
    lintranslator gui                     pick a region, then translate live
    lintranslator reread                  re-read the box now (bind this to a hotkey)
    lintranslator status                  what the running GUI is doing
    lintranslator shortcut                how to bind a global Re-read hotkey
    lintranslator convert                 build the int8 model (629 MB out, ~2.5 GB down)
    lintranslator remove                  what this app has downloaded, and how to free it
    lintranslator models                  list OpenRouter models for your key
    lintranslator languages [filter]      list the language codes NLLB can translate
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import paths
from .config import Config, Region
from .languages import CODES, deepl_code, ordered
from .pipeline import Event, Pipeline


def _load(args) -> Config:
    cfg = Config.load(args.config)
    if getattr(args, "region", None):
        x, y, w, h = (float(v) for v in args.region.split(","))
        mode = "fraction" if max(x, y, w, h) <= 1.0 else "pixels"
        cfg.capture.region = Region(x, y, w, h, mode)
    if getattr(args, "fps", None):
        cfg.capture.fps = args.fps
    if getattr(args, "backend", None):
        cfg.translate.backend = args.backend
    if getattr(args, "model", None):
        cfg.translate.model = args.model
    if getattr(args, "langs", None):
        cfg.ocr.langs = args.langs
    if getattr(args, "psm", None):
        cfg.ocr.psm = args.psm
    if getattr(args, "target_lang", None):
        cfg.translate.target_lang = args.target_lang
    if getattr(args, "source_lang", None):
        cfg.translate.source_lang = args.source_lang
    return cfg


def _report_fetch(message: str) -> None:
    """Say what is being downloaded, on stderr.

    A first run fetches language data; it used to do that in silence, so the
    command simply took longer. stderr rather than stdout because `run --json`
    writes machine-readable events to stdout.
    """
    print(f"  {message}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
def cmd_check(args) -> int:
    from .ocr import TesseractOcr, find_tessdata, tesseract_version
    from .portal import PortalBus, PortalError

    cfg = _load(args)
    ok = True
    print("lintranslator environment check")
    print("-" * 52)

    version = tesseract_version()
    print(f"tesseract binary : {version or 'NOT FOUND'}")
    if not version:
        ok = False
        print("  -> install tesseract (Arch: sudo pacman -S tesseract)")

    engine = TesseractOcr(
        langs=cfg.ocr.langs,
        tessdata_dir=cfg.ocr.tessdata_dir,
        allow_unverified_tessdata=cfg.ocr.allow_unverified_tessdata,
        on_progress=_report_fetch,
    )
    try:
        tessdata = engine.ensure_ready()
        print(f"tessdata dir     : {tessdata}")
        for lang in engine.lang_list:
            marker = "ok" if (tessdata / f"{lang}.traineddata").exists() else "MISSING"
            print(f"  {lang:<14}: {marker}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"tessdata dir     : ERROR {exc}")

    try:
        with PortalBus() as bus:
            sc = bus.get_property("org.freedesktop.portal.ScreenCast", "version")
            ss = bus.get_property("org.freedesktop.portal.Screenshot", "version")
            print(f"portal ScreenCast: v{sc}")
            print(f"portal Screenshot: v{ss}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"portal           : ERROR {exc}")

    region = cfg.capture.region
    print(f"region           : {region.mode} ({region.x}, {region.y}, {region.w}, {region.h})")

    backend = cfg.translate.backend
    print(f"translate backend: {backend}", end="")
    if backend == "ct2":
        from pathlib import Path

        model_dir = Path(cfg.translate.ct2_model_dir)
        size = (
            sum(f.stat().st_size for f in model_dir.rglob("*") if f.is_file())
            if model_dir.exists()
            else 0
        )
        print(f" ({cfg.translate.source_lang}->{cfg.translate.target_lang})")
        if size:
            print(f"  model: {model_dir} ({size / 1e6:.0f} MB)")
        else:
            ok = False
            print(f"  model: MISSING at {model_dir}")
            print("  -> python -m lintranslator convert")
        try:
            import ctranslate2  # noqa: F401

            print("  ctranslate2: installed")
        except ImportError:
            ok = False
            print("  ctranslate2: MISSING -> uv pip install ctranslate2")
    elif backend in ("openrouter", "openai", "chat"):
        from .languages import language_name
        from .translate import resolve_api_key

        env_names = {
            "openrouter": ("OPENROUTER_API_KEY", "LINTRANSLATOR_API_KEY"),
            "openai": ("OPENAI_API_KEY", "LINTRANSLATOR_API_KEY"),
            "chat": ("LINTRANSLATOR_API_KEY",),
        }[backend]
        print(
            f" ({language_name(cfg.translate.source_lang)}"
            f"->{language_name(cfg.translate.target_lang)})"
        )
        if not cfg.translate.model:
            ok = False
            print("  model: MISSING (this backend requires a model id)")
            print("  -> lintranslator models --backend openrouter")
        else:
            print(f"  model: {cfg.translate.model}")
        base = cfg.translate.api_base or "(provider default)"
        if backend == "chat" and not (cfg.translate.api_base or "").strip():
            # The endpoint *is* this backend, so there is no default to fall
            # back to - and guessing one would send the text somewhere the user
            # never chose.
            ok = False
            print("  api base: MISSING (this backend has no default endpoint)")
            print("  -> e.g. http://localhost:11434/v1 for Ollama")
        else:
            print(f"  api base: {base}")
        # The key and the screen text both travel in this request, so a plain
        # http endpoint is worth saying out loud rather than discovering later.
        try:
            from .translate import validate_base_url

            validate_base_url(
                cfg.translate.api_base or "https://provider.invalid/v1",
                allow_insecure_http=cfg.translate.allow_insecure_http,
            )
        except Exception as exc:  # noqa: BLE001 - a check reports, it does not raise
            ok = False
            print(f"  ⚠ {exc}")
        key = resolve_api_key(cfg.translate, *env_names)
        if key:
            source = (
                "config.json"
                if cfg.translate.api_key
                else next((n for n in env_names if os.environ.get(n)), "unknown")
            )
            print(f"  api key: set via {source} ({len(key)} chars)")
        elif backend == "chat":
            # A local llama.cpp / Ollama / vLLM server usually wants no key.
            print("  api key: none (fine for a local server that ignores auth)")
        else:
            ok = False
            print(f"  api key: MISSING -> export {env_names[0]}=...")
            if backend == "openrouter":
                print("           get one at https://openrouter.ai/keys")
    elif backend == "local":
        print(f" ({cfg.translate.model}, {cfg.translate.source_lang}->{cfg.translate.target_lang})")
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401

            print("  torch/transformers: installed")
        except ImportError:
            ok = False
            print("  torch/transformers: MISSING -> uv sync --extra local")
    else:
        print()

    # The language pair decides what the model is asked for, and a code it does
    # not know is not an error anywhere else: it becomes `<unk>` and the output
    # is fluent nonsense. This is the only place that can say so before a run.
    print(f"languages        : {cfg.translate.source_lang} -> {cfg.translate.target_lang}")
    for role, code in (
        ("source", cfg.translate.source_lang),
        ("target", cfg.translate.target_lang),
    ):
        if code in CODES:
            continue
        ok = False
        print(f"  {role}: {code!r} is not a FLORES-200 code, so NLLB scores it as <unk>")
        print("  -> run `python -m lintranslator languages` for the codes it does know")
    if backend == "deepl" and deepl_code(cfg.translate.target_lang) is None:
        ok = False
        print(
            f"  target: DeepL cannot translate into {cfg.translate.target_lang!r}"
            " -> pick another language, or another backend"
        )

    # The GUI is the app's front door, and PyGObject is the one dependency pip
    # cannot install: it binds system libraries. Without this row `check` could
    # say "ready" and `gui` then died with a bare ModuleNotFoundError.
    try:
        import gi  # noqa: F401

        print("PyGObject (GUI)  : installed")
    except ImportError:
        ok = False
        print("PyGObject (GUI)  : MISSING - the GUI cannot start (the CLI still can)")
        print("  Arch/CachyOS : sudo pacman -S python-gobject python-cairo")
        print(
            "  Debian/Ubuntu: sudo apt install python3-gi python3-gi-cairo "
            "gir1.2-gtk-4.0"
        )

    # Unknown keys and an unreadable file are collected while loading, and this
    # is the one command whose job is to say what is wrong with the setup.
    for warning in cfg.warnings:
        print(f"config warning   : {warning}")

    print("-" * 52)
    print("RESULT:", "ready" if ok else "problems found (see above)")
    return 0 if ok else 1


def cmd_grab(args) -> int:
    from .capture import ScreenGrabber

    cfg = _load(args)
    out = Path(args.output)
    with ScreenGrabber(cfg.capture.region) as grabber:
        frame = grabber.grab()
        out.parent.mkdir(parents=True, exist_ok=True)
        frame.save(out)
    print(f"saved {out}  region={frame.region}  full={frame.full_size}  {frame.elapsed * 1000:.0f} ms")
    return 0


def cmd_read(args) -> int:
    """OCR-only: useful for tuning a region without waiting on translation."""
    from .capture import ScreenGrabber
    from .ocr import TesseractOcr

    cfg = _load(args)
    engine = TesseractOcr(
        langs=cfg.ocr.langs,
        psm=cfg.ocr.psm,
        upscale=cfg.ocr.upscale,
        autocontrast=cfg.ocr.autocontrast,
        tessdata_dir=cfg.ocr.tessdata_dir,
        min_confidence=cfg.ocr.min_confidence,
        allow_unverified_tessdata=cfg.ocr.allow_unverified_tessdata,
        on_progress=_report_fetch,
    )
    with ScreenGrabber(cfg.capture.region) as grabber:
        for i in range(args.repeat):
            frame = grabber.grab()
            result = engine.read(frame.image)
            print(f"[{i}] {result.elapsed * 1000:.0f} ms  conf={result.confidence:.1f}  {len(result.lines)} line(s)")
            for line in result.lines:
                print(f"    {line.confidence:5.1f}  {line.text}")
            if args.save:
                frame.save(Path(args.save))
            if i + 1 < args.repeat and args.interval:
                time.sleep(args.interval)
    return 0


def cmd_gui(args) -> int:
    """Launch the GTK4 GUI.

    One GTK application owns both windows: the picker creates and manages the
    panel. An earlier version ran the picker in one application and then started
    a second one for the panel, which meant quitting the first let the second
    launch - so a fresh panel appeared right after pressing Quit.
    """
    # `gui.py` imports PyGObject inside `run()` so the CLI stays importable
    # without it. That import is the one thing pip cannot supply, and letting it
    # escape as a traceback tells a new user nothing; name the packages instead.
    try:
        import gi  # noqa: F401
    except ImportError:
        print(
            "the GUI needs PyGObject (and pycairo), which come from your distro "
            "rather than from pip:\n"
            "  Arch/CachyOS : sudo pacman -S python-gobject python-cairo\n"
            "  Debian/Ubuntu: sudo apt install python3-gi python3-gi-cairo "
            "gir1.2-gtk-4.0\n"
            "The terminal subcommands (check, grab, read, run) work without it.",
            file=sys.stderr,
        )
        return 1

    from .gui import MODE_PANEL, MODE_PICK, run_gui

    cfg = _load(args)
    pick_only = args.pick
    skip_pick = args.panel

    position = None
    if args.position:
        try:
            px, py = (int(v) for v in args.position.split(","))
            position = (px, py)
        except ValueError:
            print("--position expects x,y (e.g. --position 700,900)", file=sys.stderr)
            return 2

    if skip_pick:
        return run_gui(
            cfg,
            MODE_PANEL,
            position=position,
            autostart=False if args.no_start else None,
            screenshot_to=getattr(args, "screenshot", None),
            demo=getattr(args, "demo", False),
        )

    # Default: the picker, which shows the panel itself and can start it.
    return run_gui(
        cfg,
        MODE_PICK,
        autostart=False,
        screenshot_to=getattr(args, "screenshot", None) if pick_only else None,
        from_file=getattr(args, "from_file", None),
        initial_capture=not getattr(args, "no_capture", False),
    )


def cmd_run(args) -> int:
    cfg = _load(args)

    state = {"count": 0}

    def on_event(event: Event) -> None:
        state["count"] += 1
        stamp = time.strftime("%H:%M:%S", time.localtime(event.at))
        tag = "cache" if event.cached else f"{event.translate_elapsed * 1000:.0f}ms"
        if args.json:
            print(json.dumps(event.as_dict(), ensure_ascii=False), flush=True)
        else:
            print(f"\n[{stamp}] conf={event.confidence:.0f} ({tag})", flush=True)
            print(f"  EN: {event.source}", flush=True)
            print(f"  JA: {event.target}", flush=True)

    def on_error(exc: Exception) -> None:
        print(f"! {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)

    pipe = Pipeline(cfg, on_event=on_event, on_note=_report_fetch)
    pipe.on_error = on_error  # type: ignore[attr-defined]

    print(f"loading backend '{cfg.translate.backend}' ...", file=sys.stderr, flush=True)
    t0 = time.monotonic()
    try:
        pipe.warmup()
    except Exception as exc:  # noqa: BLE001
        print(f"warmup failed: {exc}", file=sys.stderr)
        pipe.close()
        return 1
    print(f"ready in {time.monotonic() - t0:.1f}s; polling at {cfg.capture.fps} fps", file=sys.stderr)
    print("watching the configured region; Ctrl-C to stop", file=sys.stderr, flush=True)

    pipe.start()
    last_decision = ""
    try:
        while True:
            event = pipe.step()
            if args.verbose:
                decision = pipe.last_decision
                if decision != last_decision:
                    held = pipe.settler.held
                    if held and len(held) > 40:
                        held = held[:37] + "…"
                    print(
                        f"  poll {pipe.stats.polls:>4}  {decision:<24}"
                        f" ocr={pipe.stats.ocr_runs:<4} held={held or '-'}",
                        file=sys.stderr,
                        flush=True,
                    )
                    last_decision = decision
            if event and args.count and state["count"] >= args.count:
                break
            if args.duration and (time.monotonic() - t0) >= args.duration:
                break
            time.sleep(pipe.sleep_time())
    except KeyboardInterrupt:
        print("\nstopping", file=sys.stderr)
    finally:
        report = pipe.report()
        pipe.close()
        if args.json:
            print(json.dumps({"type": "report", **report}, ensure_ascii=False), flush=True)
        else:
            print("\n--- report ---")
            for section, values in report.items():
                print(f"{section}: {values}")
    return 0


def cmd_reread(args) -> int:
    """Ask the running GUI to read the box again and translate it.

    This is what a KDE global shortcut should be bound to (System Settings ->
    Shortcuts -> Custom): Wayland forbids reading global keys, so the shortcut runs
    this command, and this command talks to the window over its control socket.
    The app also registers a global shortcut itself through
    `org.freedesktop.portal.GlobalShortcuts`, so this is the fallback and the
    scriptable path.
    """
    from .control import send

    try:
        reply = send("reread")
    except ConnectionError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    print(reply)
    return 0


def cmd_status(args) -> int:
    """Ask the running GUI what it is doing."""
    from .control import send

    try:
        reply = send("status")
    except ConnectionError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    print(reply)
    return 0


def cmd_shortcut(args) -> int:
    """Print the exact setup for a global Re-read hotkey.

    Wayland forbids reading global keys, so the shortcut belongs to the desktop,
    not to the app: a KDE custom shortcut runs `lintranslator reread`, which talks to the
    running window over its control socket. Printing the absolute paths here saves
    the user from guessing which interpreter has lintranslator installed.
    """
    from .control import socket_path

    command = Path(sys.executable).with_name("lintranslator")
    if not command.exists():
        command = Path(sys.executable)  # python -m fallback below
        invocation = f"{command} -m lintranslator reread"
    else:
        invocation = f"{command} reread"

    print("Global hotkey for Re-read")
    print("-" * 60)
    print("Option A - let the app register it (no setup):")
    print("  Launch lintranslator from the application menu, or as a systemd user unit,")
    print("  and it asks the compositor for Ctrl+Alt+R itself. The compositor")
    print("  refuses callers with no application id, so a plain terminal launch")
    print("  cannot do this (the panel will say so when it happens).")
    print()
    print("Option B - bind it in the desktop (works from any launch):")
    print("  System Settings -> Shortcuts -> Custom -> Edit -> New -> Command/URL")
    print("    Trigger: Ctrl+Alt+R")
    print(f"    Action : {invocation}")
    print()
    print("Option C - while the translation panel has focus:")
    print("  Ctrl+R, or F5.")
    print()
    print(f"Control socket: {socket_path()}")
    print(f"Check it    : {command} status   (run the GUI first)")
    return 0


def cmd_models(args) -> int:
    """List models available to the configured remote backend."""
    from .translate import OpenRouterTranslator, TranslatorError, resolve_api_key

    cfg = _load(args)
    backend = (args.backend or cfg.translate.backend).lower()
    if backend != "openrouter":
        print(
            f"`models` queries OpenRouter only (current backend is {backend!r}).\n"
            "Use it with: lintranslator models --backend openrouter",
            file=sys.stderr,
        )
        return 2

    key = resolve_api_key(cfg.translate, "OPENROUTER_API_KEY", "LINTRANSLATOR_API_KEY")
    if not key:
        print(
            "no OpenRouter key found.\n"
            "  export OPENROUTER_API_KEY=sk-or-...   (preferred)\n"
            "  or set translate.api_key in config.json\n\n"
            "Get a key at https://openrouter.ai/keys",
            file=sys.stderr,
        )
        return 2

    translator = OpenRouterTranslator(key, model="placeholder")
    try:
        models = translator.available_models()
    except Exception as exc:  # noqa: BLE001
        print(f"could not list models: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    needle = (args.filter or "").lower()
    shown = [m for m in models if needle in m.lower()] if needle else models
    if args.free:
        shown = [m for m in shown if m.endswith(":free")]
    for model in shown:
        marker = " (free)" if model.endswith(":free") else ""
        print(f"{model}{marker}")
    print(f"\n{len(shown)} of {len(models)} models", file=sys.stderr)
    return 0


def cmd_convert(args) -> int:
    from .convert import convert

    out = args.out or paths.default_ct2_dir()
    convert(args.model, out, args.quantization, args.force)
    return 0


def cmd_remove(args) -> int:
    """Report what the app has on disk, or delete a named piece of it.

    With no target this changes nothing: it prints the paths, their sizes and
    what each one costs to get back. That is the more useful half - the app
    downloads 2.5 GB that nothing ever cleans up, and until now the only way to
    find that out was to go looking in `~/.cache`.
    """
    from . import cleanup

    cfg = _load(args)
    found = cleanup.targets(cfg)

    if args.target is None:
        print("what lintranslator has downloaded:")
        print("-" * 60)
        total = 0
        for target in found:
            size = target.size
            total += size
            marker = "" if size else "  (absent)"
            print(f"{target.name:<11} {cleanup.format_size(size):>9}  {target.path}{marker}")
            print(f"            {target.detail}")
            if target.cost:
                print(f"            removing it costs {target.cost}")
            if target.note:
                print(f"            note: {target.note}")
        print("-" * 60)
        print(f"{'total':<11} {cleanup.format_size(total):>9}")
        # The cache this app shares with everything else the user runs. Saying so
        # is the difference between "remove freed 2.5 GB" and "remove deleted a
        # directory full of my other models".
        others = cleanup.others_in_hf_cache(next(t for t in found if t.name == "checkpoint"))
        if others:
            other_total = sum(size for _path, size in others)
            print(
                f"\n(not ours, not touched: {len(others)} other model(s) in "
                f"{cleanup.hf_cache_root()} take {cleanup.format_size(other_total)})"
            )
        print()
        print("nothing was removed. to free the space:")
        print("  lintranslator remove checkpoint   # the 2.5 GB download, safe to drop")
        print("  lintranslator remove model        # the converted weights (asks first)")
        print("  lintranslator remove tessdata     # language data, re-fetched on demand")
        if any(t.name == "cache" for t in found):
            print("  lintranslator remove cache        # the screen-text transcript")
        print("  add --yes to skip the confirmation")
        return 0

    if args.target == "all":
        chosen = [t for t in found if t.exists]
    else:
        chosen = [t for t in found if t.name == args.target and t.exists]

    if not chosen:
        print(f"nothing to remove ({args.target}: not found)")
        return 0

    # Never delete outside the app's own directories: the config can point
    # `ct2_model_dir` or `cache_path` at anything, and a path the user chose
    # themselves is not this command's to remove.
    for target in chosen:
        why = cleanup.refusal(target.path)
        if why:
            print(f"refusing to remove {target.name}: {why}", file=sys.stderr)
            return 1

    total = sum(t.size for t in chosen)
    _warn_if_a_gui_is_running(chosen)

    if not args.yes:
        for target in chosen:
            print(f"  {target.name:<11} {cleanup.format_size(target.size):>9}  {target.path}")
        if not sys.stdin.isatty():
            print(
                f"refusing to delete {cleanup.format_size(total)} without --yes "
                "(no terminal to ask on)",
                file=sys.stderr,
            )
            return 1
        answer = input(f"remove {len(chosen)} item(s), {cleanup.format_size(total)}? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("nothing removed")
            return 0

    freed = 0
    for target in chosen:
        freed += cleanup.remove(target)
        print(f"removed {target.name}: {cleanup.format_size(target.size)} ({target.path})")
    print(f"freed {cleanup.format_size(freed)}")
    return 0


def _warn_if_a_gui_is_running(chosen) -> None:
    """Say so when a running GUI is about to lose the weights under it."""
    if not any(t.name in ("model", "checkpoint") for t in chosen):
        return
    from .control import send

    try:
        send("status", timeout=1.0)
    except Exception:  # noqa: BLE001 - no GUI is the normal case
        return
    print(
        "note: a GUI is running. Deleting the weights does not disturb it, but "
        "its next Start or re-read will fail until they are converted again.",
        file=sys.stderr,
    )


def cmd_region(args) -> int:
    cfg = _load(args)
    path = cfg.save(args.config)
    r = cfg.capture.region
    print(f"region saved to {path}: {r.mode} ({r.x}, {r.y}, {r.w}, {r.h})")
    return 0


def cmd_languages(args) -> int:
    """List the codes NLLB can score, so `check` can point somewhere useful."""
    from .languages import search, short_code, tesseract_lang

    matches = search(args.filter or "")
    for language in matches:
        notes = []
        if language.iso:
            notes.append(f"iso:{language.iso}")
        notes.append(f"deepl:{language.deepl}" if language.deepl else "deepl:-")
        ocr = tesseract_lang(language.code) or short_code(language.code)
        notes.append(f"ocr:{ocr}")
        print(f"{language.code:<10} {language.name:<34} {'  '.join(notes)}")
    print(f"\n{len(matches)} language(s)", file=sys.stderr)
    return 0


def cmd_install_desktop(args) -> int:
    """Put the menu entry and its launcher where the desktop will find them.

    This is the one piece of setup the app cannot do for itself at run time: the
    compositor grants the global hotkey only to callers that have an *application
    id*, and an application id comes from being started by a `.desktop` file. A
    source checkout could copy the two files by hand - they ship inside the
    package as data now - but an installed wheel had no way to reach them at all.
    """
    from importlib.resources import files

    data = files("lintranslator").joinpath("data")
    desktop_text = data.joinpath("lintranslator.desktop").read_text(encoding="utf-8")
    launcher_text = data.joinpath("lintranslator-gui").read_text(encoding="utf-8")

    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    bin_home = Path(os.environ.get("XDG_BIN_HOME") or Path.home() / ".local/bin")
    desktop_path = data_home / "applications" / "lintranslator.desktop"
    launcher_path = bin_home / "lintranslator-gui"

    for directory in (desktop_path.parent, launcher_path.parent):
        directory.mkdir(parents=True, exist_ok=True)
    desktop_path.write_text(desktop_text, encoding="utf-8")
    launcher_path.write_text(launcher_text, encoding="utf-8")
    # The desktop entry runs this by name, so it has to be executable.
    launcher_path.chmod(0o755)

    print(f"launcher  : {launcher_path}")
    print(f"menu entry: {desktop_path}")
    if str(bin_home) not in os.environ.get("PATH", "").split(os.pathsep):
        print(
            f"note: {bin_home} is not on PATH, so the menu entry cannot find "
            "lintranslator-gui;\n"
            "      add it to PATH (most desktops do this already) and log back in."
        )
    print(
        "\nStart LinTranslator from the application menu rather than a terminal: "
        "that is what\nlets it register the global Re-read hotkey with the "
        "compositor. Everything else\nworks from any launch."
    )
    return 0


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lintranslator",
        description=(
            "screen translator for Linux: watches a screen region, reads it with OCR "
            "and translates it"
        ),
    )
    p.add_argument(
        "--config", help=f"config path (default: {paths.DEFAULT_CONFIG_PATH})"
    )
    # Not `required`: no subcommand means the GUI, and `parse_argv` decides that
    # after parsing rather than before (see its docstring).
    sub = p.add_subparsers(dest="command", required=False)

    def add_common(sp):
        # `--config` is defined on the top-level parser, so argparse only accepts
        # it *before* the subcommand - `lintranslator check --config x` failed
        # with a bare "unrecognized arguments". Repeating it here accepts both
        # orders. SUPPRESS matters: with an ordinary default, the subparser would
        # overwrite the value parsed before it with None.
        sp.add_argument("--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        sp.add_argument("--region", help="x,y,w,h (fractions if all <= 1.0)")
        sp.add_argument("--fps", type=float, help="polls per second")
        sp.add_argument(
            "--backend",
            help="ct2 | local | deepl | openrouter | openai | chat | none",
        )
        sp.add_argument("--model", help="model name for the local backend")
        sp.add_argument("--langs", help="tesseract languages, e.g. eng or eng+jpn")
        sp.add_argument("--psm", type=int, help="tesseract page segmentation mode")
        sp.add_argument("--source-lang", dest="source_lang", help="source language code")
        sp.add_argument("--target-lang", dest="target_lang", help="target language code")

    sp = sub.add_parser("check", help="verify the environment")
    add_common(sp)
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("grab", help="capture the region to a PNG")
    sp.add_argument("-o", "--output", default="grab.png")
    add_common(sp)
    sp.set_defaults(func=cmd_grab)

    sp = sub.add_parser("read", help="OCR the region without translating")
    sp.add_argument("--repeat", type=int, default=1)
    sp.add_argument("--interval", type=float, default=0.5)
    sp.add_argument("--save", help="also save the captured region here")
    add_common(sp)
    sp.set_defaults(func=cmd_read)

    sp = sub.add_parser("run", help="run the full pipeline")
    sp.add_argument("--json", action="store_true", help="emit JSON lines")
    sp.add_argument(
        "--verbose",
        action="store_true",
        help="print what every poll decided (why nothing translated)",
    )
    sp.add_argument("--count", type=int, default=0, help="stop after N translations")
    sp.add_argument("--duration", type=float, default=0, help="stop after N seconds")
    add_common(sp)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser(
        "gui",
        help="launch the GUI: pick the region, then watch (default)",
    )
    sp.add_argument(
        "--pick",
        action="store_true",
        help="region picker only, then exit (default is picker then panel)",
    )
    sp.add_argument(
        "--panel",
        action="store_true",
        help="skip the picker and go straight to the translation panel",
    )
    sp.add_argument(
        "--position",
        help="panel position as x,y (only honoured under X11/XWayland)",
    )
    sp.add_argument(
        "--no-start",
        action="store_true",
        help="open the panel without translating (it starts by default)",
    )
    sp.add_argument(
        "--screenshot",
        metavar="PATH",
        help="render the window to a PNG and exit (for verification)",
    )
    sp.add_argument(
        "--demo",
        action="store_true",
        help="show a sample translation without running the pipeline",
    )
    sp.add_argument(
        "--no-capture",
        action="store_true",
        help="open the picker without grabbing the screen first",
    )
    sp.add_argument(
        "--from-file",
        metavar="IMAGE",
        help="open the picker on an existing screenshot instead of the live screen",
    )
    add_common(sp)
    sp.set_defaults(func=cmd_gui)

    sp = sub.add_parser(
        "reread",
        help="tell the running GUI to re-read the box and translate it now",
    )
    sp.set_defaults(func=cmd_reread)

    sp = sub.add_parser("status", help="ask the running GUI what it is doing")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser(
        "shortcut", help="print how to set up a global Re-read hotkey"
    )
    sp.set_defaults(func=cmd_shortcut)

    sp = sub.add_parser("models", help="list remote models (OpenRouter)")
    sp.add_argument("--backend", help="backend to query (default: openrouter)")
    sp.add_argument("--filter", help="only show ids containing this substring")
    sp.add_argument("--free", action="store_true", help="only show :free models")
    sp.set_defaults(func=cmd_models)

    sp = sub.add_parser(
        "remove",
        help="show what has been downloaded, or delete part of it",
        description=(
            "With no target: report the fp32 checkpoint, the converted weights, "
            "the language data and the optional cache, with their sizes, and "
            "change nothing. Naming a target deletes it."
        ),
    )
    sp.add_argument(
        "target",
        nargs="?",
        choices=["checkpoint", "model", "tessdata", "cache", "all"],
        help="what to remove; omit it for a report",
    )
    sp.add_argument(
        "--yes", action="store_true", help="do not ask before deleting"
    )
    add_common(sp)
    sp.set_defaults(func=cmd_remove)

    sp = sub.add_parser("convert", help="build the int8 CTranslate2 model")
    sp.add_argument("--model", default="facebook/nllb-200-distilled-600M")
    sp.add_argument("--out", default=None, help="output dir (default: data/ct2/...)")
    sp.add_argument("--quantization", default="int8", choices=["int8", "int16", "float32"])
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_convert)

    sp = sub.add_parser("region", help="save a region into the config")
    sp.add_argument("--x", type=float, required=True)
    sp.add_argument("--y", type=float, required=True)
    sp.add_argument("--w", type=float, required=True)
    sp.add_argument("--h", type=float, required=True)
    sp.add_argument("--fraction", action="store_true", help="values are 0..1 fractions")
    sp.set_defaults(func=cmd_region)

    sp = sub.add_parser(
        "languages",
        help="list the 202 language codes NLLB is trained on (for --source-lang)",
    )
    sp.add_argument("filter", nargs="?", help="only names or codes containing this")
    sp.set_defaults(func=cmd_languages)

    sp = sub.add_parser(
        "install-desktop",
        help="install the menu entry and launcher (what the global hotkey needs)",
    )
    sp.set_defaults(func=cmd_install_desktop)
    return p


def parse_argv(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line, treating "no subcommand" as the GUI.

    `gui` has always been described as the default in `--help`, and for a desktop
    app that is what running it should mean: a bare `lintranslator`, a launcher
    script, or a double-click on the entry point all open the picker rather than
    printing a usage error.

    The subcommand is optional so that this can be decided *after* parsing - a
    flag with no subcommand (`lintranslator --config other.json`) means the GUI
    too. The second parse is what fills in the GUI's own options; assigning
    `func` by hand would leave every `args.*` it reads undefined.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args(argv + ["gui"])
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_argv(argv)
    if args.command == "region":
        cfg = Config.load(args.config)
        mode = "fraction" if args.fraction else "pixels"
        cfg.capture.region = Region(args.x, args.y, args.w, args.h, mode)
        path = cfg.save(args.config)
        r = cfg.capture.region
        print(f"region saved to {path}: {r.mode} ({r.x}, {r.y}, {r.w}, {r.h})")
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
