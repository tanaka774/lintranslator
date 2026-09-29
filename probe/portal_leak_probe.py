"""Probe: where does the portal actually write, and does the app clean it up?

Run with the project venv. Prints the URI the portal returns and whether the
file survives the app's own removal step.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lintranslator import portal  # noqa: E402


def snapshot(dirs):
    seen = {}
    for d in dirs:
        d = Path(d)
        if d.is_dir():
            for f in d.glob("Screenshot_*.png"):
                seen[f] = f.stat().st_mtime
    return seen


WATCH = [Path.home() / "Pictures", Path.home() / "Pictures" / "Screenshots"]

print("screenshot_dirs() the app is willing to delete from:")
for d in portal.screenshot_dirs():
    print(f"   {d}   (exists={d.is_dir()})")
print()

before = snapshot(WATCH)
print(f"before: {len(before)} Screenshot_*.png in {[str(w) for w in WATCH]}")

bus = portal.PortalBus()
counter = 0
for attempt in range(1, 4):
    counter += 1
    token = f"leakprobe_{os.getpid()}_{counter}"
    print(f"\n--- portal Screenshot call #{attempt} (interactive=false) ---")
    try:
        code, results, req = bus.portal_request(
            "org.freedesktop.portal.Screenshot",
            "Screenshot",
            "sa{sv}",
            ("", {"handle_token": ("s", token), "interactive": ("b", False)}),
            token=token,
            timeout=30.0,
        )
    except Exception as exc:
        print(f"   portal call failed: {type(exc).__name__}: {exc}")
        break
    print(f"   response code = {code}")
    print(f"   results       = {results}")
    uri = results.get("uri")
    if not uri:
        continue
    path = portal.screenshot_path(str(uri))
    print(f"   -> local path = {path}")
    print(f"   -> exists={path.exists()} size={path.stat().st_size if path.exists() else '-'}")
    print(f"   -> app would delete it? {any(str(path.resolve()).startswith(str(d.resolve())) for d in portal.screenshot_dirs() if d.exists())}")
    print(f"   -> remove_screenshot() returned: {portal.remove_screenshot(path)}")
    print(f"   -> exists after remove: {path.exists()}")

after = snapshot(WATCH)
new = {k: v for k, v in after.items() if k not in before}
print(f"\nafter: {len(after)} files; NEW files left behind: {len(new)}")
for k in sorted(new):
    print(f"   LEFTOVER {k} ({k.stat().st_size} bytes)")
bus.close()
