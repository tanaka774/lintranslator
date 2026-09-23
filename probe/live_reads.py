"""Dump every live OCR read with a timestamp, then compare consecutive reads.

Run:  .venv/bin/python probe/live_reads.py [seconds] > reads.txt

Two numbers decide whether a line settles, and this measures both from real
screen content:

  * **intra-line** similarity - consecutive reads of one unchanged line. These
    are noise and MUST be accepted as the same line.
  * **inter-line** similarity - reads across a real dialogue change. These MUST
    be rejected.

The gap between the two is what makes a similarity threshold safe. Writes reads
to stdout and a JSON dump for offline analysis.
"""
import json
import sys
import time
from pathlib import Path

# Run from anywhere: the package is imported from this checkout, not from
# whatever happens to be on `sys.path`.
APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from lintranslator.config import Config
from lintranslator.pipeline import Pipeline

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0

cfg = Config.load()
cfg.translate.backend = "none"
p = Pipeline(cfg)
p.warmup()

reads: list = []
t0 = time.monotonic()
p.on_ocr = lambda r: reads.append({"t": round(time.monotonic() - t0, 2), "text": r.text})
p.on_event = lambda ev: reads.append({"t": round(time.monotonic() - t0, 2), "emit": ev.source})

p.start()
while time.monotonic() - t0 < SECONDS:
    p.step()
    time.sleep(p.sleep_time())

with open(APP_DIR / "probe" / "live_reads.json", "w") as fh:
    json.dump(reads, fh, ensure_ascii=False, indent=1)

texts = [r for r in reads if "text" in r]
print(f"{len(texts)} reads over {SECONDS:.0f}s", file=sys.stderr)
for r in reads:
    if "text" in r:
        print(f"[{r['t']:6.2f}] {r['text'][:110]!r}")
    else:
        print(f"[{r['t']:6.2f}] >>> EMIT {r['emit'][:110]!r}")
p.close()
