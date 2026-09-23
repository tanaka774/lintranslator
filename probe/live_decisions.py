"""Trace the pipeline's own emit decisions, including the dedupe comparison.

Run:  .venv/bin/python probe/live_decisions.py [seconds]

Subclasses Pipeline only to observe; the decision logic is untouched. For every
emission it prints why the previous line did not dedupe against it - which is
how a duplicate translation gets attributed to a specific comparison rather than
guessed at.
"""
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

# Run from anywhere: the package is imported from this checkout, not from
# whatever happens to be on `sys.path`.
APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from lintranslator.config import Config
from lintranslator.detect import _edit_distance, is_same_reading
from lintranslator.pipeline import Pipeline

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0

cfg = Config.load()
cfg.translate.backend = "none"

p = Pipeline(cfg)
p.warmup()

t0 = time.monotonic()
log: list = []
last_emitted: str | None = None


def on_event(ev) -> None:
    global last_emitted
    now = round(time.monotonic() - t0, 1)
    if last_emitted is not None:
        ratio = SequenceMatcher(None, ev.source, last_emitted).ratio()
        edits = _edit_distance(ev.source, last_emitted)
        dup = is_same_reading(ev.source, last_emitted)
        log.append(
            f"[+{now:5.1f}s] EMIT  ratio={ratio:.3f} edits={edits:3d} "
            f"dedupe_says_same={dup}  len={len(ev.source)}/{len(last_emitted)}\n"
            f"          prev={(last_emitted or '')[:78]!r}\n"
            f"          new ={ev.source[:78]!r}"
        )
    else:
        log.append(f"[+{now:5.1f}s] EMIT (first)  {ev.source[:78]!r}")
    last_emitted = ev.source


p.on_event = on_event
p.start()
while time.monotonic() - t0 < SECONDS:
    p.step()
    time.sleep(p.sleep_time())

print(f"=== {SECONDS:.0f}s: {p.stats.polls} polls, {p.stats.ocr_runs} OCR runs, "
      f"{p.stats.translations} emissions, refreshed={p.stats.refreshed_reads} ===")
for entry in log:
    print(entry)
p.close()
