"""lintranslator: screen-region OCR + auto-translation overlay for games.

Phase 1 (headless pipeline):
    capture -> change detection -> OCR -> translate -> print

Modules
-------
config      configuration model + JSON load/save
portal      xdg-desktop-portal D-Bus clients (Screenshot, ScreenCast)
capture     high-level frame grabber returning an already-cropped frame
detect      change detection: pixel hashing + typewriter settle detection
ocr         tesseract wrapper with game-UI preprocessing
translate   translation backends (local NLLB, cloud APIs) behind one interface
pipeline    wires the above into a polling loop
"""

__version__ = "0.1.0"
