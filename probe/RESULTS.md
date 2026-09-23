# OCR / translate feasibility probe (Limbus Company, KDE Wayland, AMD RX 9070)

Input: user screenshot 1501x852 (in-game dialogue box, bottom area).

## OCR candidates (measured on this machine)

| engine | config | line1 | line2 | warm latency |
|---|---|---|---|---|
| tesseract 5.5.3 | `-l eng --psm 6`, 3x upscale + autocontrast | PERFECT | PERFECT | ~104 ms |
| tesseract 5.5.3 | `-l eng+jpn --psm 6` | PERFECT | PERFECT | ~150 ms |
| tesseract 5.5.3 | `-l jpn --psm 6` | 2 errors | 3 errors | ~250 ms |
| rapidocr-onnxruntime 1.4.4 (PP-OCRv4) | band 3x | 1 err `[`->`l`, spaces LOST | spaces LOST | ~410 ms |
| rapidocr 3.x (PP-OCRv6 det/rec small) | band 3x | 1 err `[`->`l` | correct incl. spaces | ~600 ms |
| rapidocr 3.x | crop @1x (no upscale) | ok | scores 0.869, garbled | ~500 ms |
| tesseract on RAW full screenshot | no crop | GARBAGE | GARBAGE | - |

Ground truth:
```
[It has been determined that this case merits preservation as a record. The following is the case
record pertaining to today's request.]
```

## Translation candidates

| model | latency | quality |
|---|---|---|
| Helsinki-NLP/opus-mt-en-jap | 0.35-0.45 s | UNUSABLE (hallucinated, unrelated output) |
| facebook/nllb-200-distilled-600M | see nllb_result.txt | - |

## Environment facts
- CachyOS, KDE Plasma 6.7.4 (kwin_wayland), session type wayland
- xdg-desktop-portal 1.22.1 + xdg-desktop-portal-kde 6.7.4 with ScreenCast + Screenshot ifaces
- pipewire/wireplumber installed (kpipewire is a portal dep)
- GPU: AMD Navi 48 (RX 9070) - no CUDA; ROCm path only
- python 3.14 system; py3.12 venvs used via uv
- tesseract has NO eng/jpn traineddata by default (only afr, osd) - must be installed
