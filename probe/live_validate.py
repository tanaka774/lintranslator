"""Live end-to-end validation: real portal capture + tesseract + the backend.

Run:  .venv/bin/python probe/live_validate.py [seconds]

Prints every translation the pipeline emits, flags duplicate emissions, and
reports the OCR edit-distance jitter seen between consecutive reads on the live
screen (the quantity the settle/dedupe rules have to tolerate).

The backend is whatever the config says, and it is printed before the run, so a
validation run is never a surprise spend against an API.
"""
import sys
import time
from pathlib import Path

# Run from anywhere: the package is imported from this checkout, not from
# whatever happens to be on `sys.path`.
APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from lintranslator.config import Config
from lintranslator.detect import _edit_distance, is_same_reading
from lintranslator.pipeline import Pipeline

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 75.0

cfg = Config.load()
print(
    f"backend: {cfg.translate.backend}"
    f" ({cfg.translate.model or 'no model id'})"
    f" {cfg.translate.source_lang} -> {cfg.translate.target_lang}",
    flush=True,
)
p = Pipeline(cfg)
print("warming up (portal + tesseract + the configured backend)...", flush=True)
t_warm = time.monotonic()
p.warmup()
print(f"warm in {time.monotonic() - t_warm:.1f}s", flush=True)

rows: list = []
reads: list = []
t0 = time.monotonic()


def on_ocr(result) -> None:
    reads.append((round(time.monotonic() - t0, 1), result.text))


def on_event(event) -> None:
    rows.append(
        (round(time.monotonic() - t0, 1), event.source, event.target,
         event.cached, event.translate_elapsed)
    )


p.on_ocr = on_ocr
p.on_event = on_event
p.start()
while time.monotonic() - t0 < SECONDS:
    p.step()
    time.sleep(p.sleep_time())

print(
    f"\n=== {SECONDS:.0f}s live run: real portal capture + tesseract + "
    f"{cfg.translate.backend} ==="
)
print(
    f"polls={p.stats.polls} changed={p.stats.changed} ocr={p.stats.ocr_runs} "
    f"refreshed={p.stats.refreshed_reads} throttled={p.stats.throttled} "
    f"emits={len(rows)} errors={p.stats.errors}"
)

jitter = [
    _edit_distance(a, b)
    for (_, a), (_, b) in zip(reads, reads[1:])
    if a and b
]
if jitter:
    ordered = sorted(jitter)
    print(
        f"consecutive OCR reads: n={len(jitter)}  edit distance "
        f"min/median/max = {ordered[0]}/{ordered[len(ordered) // 2]}/{ordered[-1]}"
    )

dups = 0
for i, (t, src, ja, cached, elapsed) in enumerate(rows):
    flag = ""
    if i and is_same_reading(src, rows[i - 1][1]):
        flag = "   <-- DUPLICATE of previous"
        dups += 1
    print(f"\n[+{t:5.1f}s] cached={cached} translate={elapsed * 1000:.0f}ms{flag}")
    print(f"   EN: {src[:90]}")
    print(f"   JA: {ja[:90]}")

print(f"\nlines translated: {len(rows)}   duplicate emissions: {dups}")
p.close()
