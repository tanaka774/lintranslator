"""Verify the ScreenCast backend end to end: frames in, nothing on disk.

Uses the real ScreenCastStream + ScreenGrabber, and watches the Pictures
directory for any screenshot the old path would have left there.

This asks the compositor for screen-sharing consent: approve the dialog.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lintranslator.capture import SCREENCAST, ScreenGrabber, build_grabber  # noqa: E402
from lintranslator.config import CaptureConfig, Config, Region  # noqa: E402
from lintranslator.portal import pictures_dir  # noqa: E402

WATCH = [pictures_dir(), pictures_dir() / "Screenshots", Path("/tmp")]


def snapshots() -> dict:
    seen = {}
    for d in WATCH:
        if not d.is_dir():
            continue
        for f in d.glob("Screenshot_*.png"):
            seen[f] = f.stat().st_mtime
    return seen


before = snapshots()
print(f"watching {[str(w) for w in WATCH]}")
print(f"before: {len(before)} screenshot files present\n")

notes: list[str] = []
config = Config()
config.capture = CaptureConfig(backend=SCREENCAST, fps=2.0, region=Region(0.05, 0.05, 0.3, 0.1, "fraction"))

print("--- building the grabber (this triggers the consent dialog) ---")
t0 = time.monotonic()
grabber = build_grabber(config, on_note=notes.append)

try:
    for i in range(1, 6):
        t0 = time.monotonic()
        frame = grabber.grab()
        print(
            f"  grab {i}: region={frame.image.size} from full={frame.full_size} "
            f"in {1000 * (time.monotonic() - t0):.0f} ms"
        )
        time.sleep(0.5)
    print(f"\nstats: {grabber.stats}")
finally:
    grabber.close()

after = snapshots()
new = {k: v for k, v in after.items() if k not in before}
print(f"\nafter: {len(after)} screenshot files; NEW files on disk: {len(new)}")
for k in sorted(new):
    print(f"   LEFTOVER {k}")
if notes:
    print("\nnotes the UI would have shown:")
    for n in notes:
        print(f"   {n}")
if not new:
    print("\nOK: five frames captured and nothing was written to disk.")
