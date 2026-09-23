"""Live end-to-end validation: real portal capture + tesseract + ct2 NLLB.

Run:  .venv-gi/bin/python probe/live_validate.py [seconds]

Prints every translation the pipeline emits, flags duplicate emissions, and
reports the OCR edit-distance jitter seen between consecutive reads on the live
screen (the quantity the settle/dedupe rules have to tolerate).
"""
import sys
import time

sys.path.insert(0, "/home/chiba/workspace/lintranslator")

from lintranslator.config import Config
from lintranslator.detect import _edit_distance, is_same_reading
from lintranslator.pipeline import Pipeline

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 75.0

cfg = Config.load("/home/chiba/workspace/lintranslator/config.json")
# Force the local model by default: the configured backend may be a remote API,
# and a validation run should not spend credits or depend on the network.
if "--here" not in sys.argv:
    cfg.translate.backend = "ct2"
p = Pipeline(cfg)
print("warming up (portal + tesseract + ct2 NLLB)...", flush=True)
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

print(f"\n=== {SECONDS:.0f}s live run: real portal capture + tesseract + ct2 NLLB ===")
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
