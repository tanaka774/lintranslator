"""Where does the time go between "start" and "seeing the selected area"?

Two questions, measured rather than reasoned about:

1. **Start of capturing.** From `Pipeline.start()` (what the panel's worker
   calls) to the *first completed grab*, and to the first OCR result. At 2 fps
   the poll loop itself can add up to `1/fps` before anything is captured, and
   warmup happens first on the worker thread - so "pressed Start" and
   "first pixel read" are not the same instant.

2. **Area fidelity.** Does the crop the pipeline reads correspond exactly to the
   rectangle the picker saved? Verified deterministically by feeding one captured
   PNG through both paths (`Region.to_pixels` + `Image.crop` vs `ScreenGrabber.grab`),
   so a mismatch cannot hide behind a live screen that moved between grabs.

Run:  .venv/bin/python probe/capture_timing.py
"""
import sys
import time
from io import BytesIO
from pathlib import Path

# Run from anywhere: the package is imported from this checkout, not from
# whatever happens to be on `sys.path`.
APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from PIL import Image

from lintranslator.capture import ScreenGrabber
from lintranslator.config import Config
from lintranslator.portal import ScreenshotPortal

cfg = Config.load()
region = cfg.capture.region

print("== portal screenshot path ==")
portal = ScreenshotPortal()
t0 = time.monotonic()
png, size, elapsed = portal.grab()
t_cold = time.monotonic() - t0
print(f"cold grab: {t_cold * 1000:7.1f} ms total, portal-reported {elapsed * 1000:6.1f} ms, screen {size}")

for i in range(5):
    t0 = time.monotonic()
    png, size, elapsed = portal.grab()
    total = time.monotonic() - t0
    print(
        f"warm grab {i}: {total * 1000:7.1f} ms total  "
        f"(portal {elapsed * 1000:6.1f} ms, overhead {(total - elapsed) * 1000:6.1f} ms)"
    )

print("\n== decode + crop (the part `Frame.elapsed` does NOT include) ==")
t0 = time.monotonic()
with Image.open(BytesIO(png)) as im:
    full = im.convert("RGB")
    full.load()
t_decode = time.monotonic() - t0
x, y, w, h = region.to_pixels(*full.size)
t0 = time.monotonic()
crop = full.crop((x, y, x + w, y + h))
t_crop = time.monotonic() - t0
print(f"PNG decode: {t_decode * 1000:7.1f} ms   crop: {t_crop * 1000:.2f} ms")
print(f"region {region.mode} ({region.x}, {region.y}, {region.w}, {region.h})")
print(f"  -> pixels x={x} y={y} w={w} h={h}   screen {full.size}")
print(f"  -> fraction round-trip: x/w={x / full.size[0]:.6f}/{w / full.size[0]:.6f}")

print("\n== area fidelity: one PNG through both paths ==")
# Stub the portal so both paths see *identical* bytes. Any difference is then
# geometry, not a screen that changed between two live grabs.
grabber = ScreenGrabber(region)


class _FixedPortal:
    def __init__(self, data):
        self.data = data

    def grab(self, timeout: float = 30.0):
        return self.data, size, 0.0

    def close(self):
        pass


grabber._portal = _FixedPortal(png)
frame = grabber.grab()
nested = frame.image.tobytes() == crop.tobytes()
print(f"ScreenGrabber.grab() == Image.crop(region) : {nested}")
print(f"frame.region={frame.region}  frame.full_size={frame.full_size}  frame.size={frame.size}")
if not nested:
    diff = sum(1 for a, b in zip(frame.image.tobytes(), crop.tobytes()) if a != b)
    print(f"  !! {diff} differing bytes")

print("\n== start-of-capture latency (what the panel worker does) ==")
# Exactly the sequence PipelineThread._run performs: construct, warmup, start,
# then step. Warmup here is portal + tesseract only (no model load).
from lintranslator.pipeline import Pipeline

cfg.translate.backend = "none"
pipe = Pipeline(cfg)
t_warm0 = time.monotonic()
pipe.warmup()
t_warm = time.monotonic() - t_warm0
t_start = time.monotonic()
pipe.start()
tick = pipe.sleep_time()
t_step0 = time.monotonic()
event = pipe.step()
t_first = time.monotonic() - t_step0
print(f"warmup                       : {t_warm * 1000:7.1f} ms")
print(f"start() -> first step() call : {(t_step0 - t_start) * 1000:7.1f} ms  (sleep_time={tick * 1000:.1f} ms)")
print(f"first step() wall time       : {t_first * 1000:7.1f} ms  (grab + detect + OCR)")
print(f"decision                     : {pipe.last_decision}")
print(f"grabber stats                : {pipe.grabber.stats}")
print(f"frame.elapsed (portal only)  : {pipe._last_frame.elapsed * 1000:.1f} ms" if pipe._last_frame else "")
print(f"ocr runs={pipe.stats.ocr_runs} ocr_avg={pipe.stats.ocr_avg_ms:.1f} ms")
pipe.close()
portal.close()
