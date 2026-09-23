"""Live diagnostic: why did a line get translated, or not?

Run:  .venv-gi/bin/python probe/live_trace.py [seconds]

Logs the pipeline's own settle state on every poll, so a dropped line can be
attributed to a specific cause instead of guessed at:

  * a genuinely new reading (the screen text really changed), vs
  * a wild read of the same line (a bad OCR read resetting the settle window).

Also flags the failure this was written to chase: a long stretch where the
reading keeps changing but nothing is ever emitted.
"""
import sys
import time

sys.path.insert(0, "/home/chiba/workspace/tl-kun")

from tlkun.config import Config
from tlkun.detect import _edit_distance, is_same_reading
from tlkun.pipeline import Pipeline

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0

cfg = Config.load("/home/chiba/workspace/tl-kun/config.json")
cfg.translate.backend = "none"          # translation is not what is under test
p = Pipeline(cfg)
p.warmup()

cfg_detect = cfg.detect
print(
    f"settle_window={cfg_detect.settle_window}s settle_max_wait={cfg_detect.settle_max_wait}s "
    f"refresh_interval={cfg_detect.refresh_interval}s ocr_min_interval={cfg_detect.ocr_min_interval}s "
    f"fps={cfg.capture.fps}",
    flush=True,
)

emits: list = []
p.on_event = lambda ev: emits.append((round(time.monotonic() - t0, 1), ev.source))

prev_held: str | None = None
changed_since_emit = 0
t0 = time.monotonic()

print(f"\n{'t':>6} {'decision':<18} {'ocr':>4} {'stable':>7}  note", flush=True)
while time.monotonic() - t0 < SECONDS:
    p.step()
    now = time.monotonic()
    held = p.settler.held
    stable = p.settler.stable_seconds(now)
    note = ""
    if held != prev_held:
        if prev_held is None:
            note = "new read"
        elif held and is_same_reading(held, prev_held):
            note = "jitter (same line, tolerated)"
        elif held:
            note = "RESET (treated as a new line)"
            changed_since_emit += 1
    # a reset while the screen text is unchanged is the expensive false positive
    if note.startswith("RESET"):
        print(
            f"{now-t0:6.1f} {p.last_decision:<18} {p.stats.ocr_runs:4d} {stable:6.2f}s  {note}"
            f"  was={(prev_held or '')[:38]!r} now={(held or '')[:38]!r}"
            f"  edits={_edit_distance(prev_held, held) if prev_held and held else -1}",
            flush=True,
        )
    if emits and emits[-1][0] >= last_report:
        pass
    prev_held, prev_stable = held, stable
    time.sleep(p.sleep_time())

print(f"\npolls={p.stats.polls} changed={p.stats.changed} ocr={p.stats.ocr_runs} "
      f"refreshed={p.stats.refreshed_reads} errors={p.stats.errors}")
print(f"emitted {len(emits)} translation(s); {changed_since_emit} settle-window reset(s) total")
for t, src in emits:
    print(f"   [+{t:5.1f}s] {src[:70]!r}")
p.close()
