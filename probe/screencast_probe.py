"""Verify the ScreenCast backend end to end: frames in, nothing on disk.

Uses the real ScreenCastStream + ScreenGrabber, and watches the Pictures
directory for any screenshot the old path would have left there.

Run it twice with the same --token-file to see persistence work: the first run
asks for consent, the second starts from the stored token without a dialog.

This asks the compositor for screen-sharing consent: approve the dialog.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lintranslator.capture import SCREENCAST, ScreenGrabber, build_grabber  # noqa: E402
from lintranslator.config import CaptureConfig, Config, Region  # noqa: E402
from lintranslator.portal import pictures_dir  # noqa: E402

WATCH = [pictures_dir(), pictures_dir() / "Screenshots", Path("/tmp")]

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--token-file",
    help="where to keep the restore token, so a second run can skip the dialog",
)
parser.add_argument("--grabs", type=int, default=5)
args = parser.parse_args()


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
print(f"before: {len(before)} screenshot files present")

token = None
token_path = Path(args.token_file) if args.token_file else None
if token_path and token_path.exists():
    token = token_path.read_text().strip() or None
    print(f"restore token: reusing one from {token_path}")
else:
    print("restore token: none yet, so a consent dialog is expected")

notes: list[str] = []
saved: list[str] = []


def remember(new_token: str) -> None:
    saved.append(new_token)
    if token_path:
        token_path.write_text(new_token)


config = Config()
config.capture = CaptureConfig(
    backend=SCREENCAST,
    fps=2.0,
    region=Region(0.05, 0.05, 0.3, 0.1, "fraction"),
    restore_token=token,
)

print("\n--- building the grabber ---")
grabber = build_grabber(config, on_note=notes.append, on_restore_token=remember)
try:
    for i in range(1, args.grabs + 1):
        t0 = time.monotonic()
        frame = grabber.grab()
        took = 1000 * (time.monotonic() - t0)
        print(
            f"  grab {i}: region={frame.image.size} from full={frame.full_size} "
            f"in {took:.0f} ms"
        )
        time.sleep(0.3)
    print(f"\nstats: {grabber.stats}")
finally:
    grabber.close()

after = snapshots()
new = {k: v for k, v in after.items() if k not in before}
print(f"\nafter: {len(after)} screenshot files; NEW files on disk: {len(new)}")
for k in sorted(new):
    print(f"   LEFTOVER {k}")
if saved:
    print(f"\nrestore token received: {saved[0][:12]}... (stored, so the next run skips the dialog)")
else:
    print("\nNO restore token was returned: the dialog will appear again next run")
if notes:
    print("\nnotes the UI would have shown:")
    for n in notes:
        print(f"   {n}")
if not new:
    print("\nOK: frames captured and nothing was written to disk.")
