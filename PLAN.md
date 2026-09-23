# LinTranslator — Game Dialogue Auto-Translator (Limbus Company) — Feasibility & Plan

Target: KDE Plasma 6.7.4 / Wayland / kwin_wayland, AMD RX 9070 (Navi 48), Python 3.12 via uv.
Goal: select a fixed screen rectangle over the dialogue box → auto-capture → OCR → EN→JA translate → display.

## 1. Verdict

**Feasible, and the two hard parts are already proven on this machine** (not estimated — measured
against your actual screenshot, `probe/shot.webp`).

Everything needed is present except `tesseract` language data, which is a one-time install.

| Stage | Status | Evidence |
|---|---|---|
| Screen capture on Wayland | available | `xdg-desktop-portal` 1.22.1 + `xdg-desktop-portal-kde` 6.7.4 expose `ScreenCast` + `Screenshot`; pipewire daemon live (`/run/user/1000/pipewire-0`); kwin 6.7.4 |
| OCR | **proven, perfect** | tesseract 5.5.3 `-l eng --psm 6` on the dialogue band = 0 errors |
| Translation | **proven, fluent** | NLLB-200-distilled-600M EN→JA, 0.7–1.5 s/line on CPU |
| Region select + overlay | straightforward | Qt/PySide6 frameless always-on-top window |
| Global hotkey | solved | KGlobalAccel (KDE global shortcut → CLI flag) |

End-to-end budget for one new dialogue line: **~1–1.7 s** (paste-instant once cached).

## 2. Measured results

### OCR (dialogue band extracted at 3x + autocontrast)

Ground truth:
```
[The committee has resolved that this entry warrants retention as a standing record. The material
below is the file concerning today's submission.]
```

| engine | config | line 1 | line 2 | warm latency |
|---|---|---|---|---|
| **tesseract 5.5.3** | `-l eng --psm 6` | ✅ perfect | ✅ perfect | **~105 ms** |
| tesseract 5.5.3 | `-l eng+jpn --psm 6` | ✅ perfect | ✅ perfect | ~150 ms |
| tesseract 5.5.3 | `-l jpn --psm 6` | 2 errors | 3 errors | ~125 ms |
| rapidocr-onnxruntime 1.4.4 (PP-OCRv4) | 3x | 1 err `[`→`l`, **spaces lost** | **spaces lost** | ~410 ms |
| rapidocr 3.x (PP-OCRv6 det/rec small) | 3x | 1 err `[`→`l` | ✅ incl. spaces | ~600 ms |
| rapidocr 3.x | crop @1x, no upscale | ok | score 0.869, garbled | ~500 ms |
| tesseract | raw full screenshot, no crop | ❌ garbage | ❌ garbage | — |

**Conclusions**
- **Tesseract wins here.** On this game's clean UI font, `tessdata_fast` eng is exact and 4–6x faster.
- **Cropping + 3x upscale + autocontrast is mandatory.** On the raw full frame tesseract returns noise.
- RapidOCR v3 is the better *detector*; keep it as a fallback for FP/JP sources where line layout is unknown.
- What the screenshot proves is that the *font is OCR-friendly*. It does not yet prove accuracy on
  italic / speaker-name / battle-log text, so Phase 2 includes an accuracy harness.

### Translation (EN→JA)

| model | latency | quality |
|---|---|---|
| Helsinki-NLP/opus-mt-en-jap | 0.35–0.45 s | ❌ **UNUSABLE** — hallucinated, unrelated output ("わたし が こう い う 理由 は…") |
| **facebook/nllb-200-distilled-600M** | **0.73–1.47 s** | ✅ fluent, faithful |

NLLB samples:
```
[この事件は記録として保存されるべきであると決定された. 今日の要請に関する事件記録は以下のとおりです.]
管理者 異常が近づいてる 戦闘準備                 ← "Manager! The abnormality is approaching. Prepare for combat."
心配しないで 最適な解決策を計算しました          ← "Don't worry. I have already calculated the optimal solution."
```
Note `Manager!` → `管理者` is a name/term issue, handled by the glossary feature (Phase 3).

**Avoid `opus-mt-en-jap`** — it is a documented trap for this pair.

### Environment blockers found

1. `tesseract` ships **no** `eng`/`jpn` traineddata (only `afr`, `osd`) → install required.
2. `gamescope` is not installed → window-isolated capture is not available; use the portal path.
3. Python 3.14 is the system interpreter and has no `onnxruntime`/`torch` wheels → project pins 3.12 via uv.
4. ROCm 7.2.4 + `hip-runtime-amd` are installed → GPU acceleration is possible later (Phase 3).

## 3. Architecture

```
┌─ Capture ──────────┐   ┌─ Detect ────────┐   ┌─ OCR ──────────┐   ┌─ Translate ────┐   ┌─ Display ──────┐
│ portal ScreenCast  │──▶│ region crop     │──▶│ tesseract      │──▶│ NLLB (local)   │──▶│ panel under    │
│ (PipeWire stream)  │   │ pixel-hash diff │   │ psm 6, 3x      │   │ or cloud API   │   │ dialogue box   │
│ or Screenshot call │   │ typewriter      │   │ autocontrast   │   │ LRU cache      │   │ + tray / hotkey│
│ 4–10 Hz poll       │   │ settle-detect   │   │                │   │                │   │                │
└────────────────────┘   └─────────────────┘   └────────────────┘   └────────────────┘   └────────────────┘
```

**Capture.** Preferred: `org.freedesktop.portal.ScreenCast` — one permission grant, then a live
PipeWire stream at 10 Hz (via GStreamer/OpenCV), and KWin can share *just the game window*, so the
overlay never captures itself. Fallback: `org.freedesktop.portal.Screenshot` per poll — simpler, no
persistent grant, but a D-Bus round-trip each time. **Both need a running desktop session**; this
cannot be tested from a sandboxed shell.

**Change detection.** Downscale the region to ~64px wide grayscale, hash it. Identical hash → skip.
This is what makes a 4–10 Hz poll nearly free. While the typewriter effect is mid-reveal the OCR text
keeps changing, so only translate when the text is stable across 2 consecutive polls (or when the
pixel hash stops changing).

**Display.** Do **not** try to cover the original text: click-through overlays are awkward on
Wayland. Instead put a small always-on-top panel directly *below* the dialogue box, so original and
translation are both readable. Alternative: copy the translation to the clipboard.

**Hotkeys.** Wayland forbids reading raw global keys. Use KDE's `KGlobalAccel` (present) to bind a
shortcut that invokes the app with `--toggle` / `--capture-now`.

## 4. Tech stack

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12 (uv-managed venv) | best ML/OCR ecosystem; 3.14 has no wheels |
| GUI | PySide6 (Qt6) | frameless always-on-top panel; not installed yet |
| Capture | `dbus`/`gdbus` + GStreamer PipeWire, fallback Screenshot portal | only supported Wayland path |
| OCR | `pytesseract` + system tesseract 5.5.3 + `tessdata_fast` eng | measured fastest + exact |
| OCR fallback | `rapidocr` 3.x (PP-OCRv6) | detector for unknown layout / JP sources |
| MT local | `transformers` + NLLB-200-distilled-600M (int8 via CTranslate2 later) | measured fluent |
| MT cloud | DeepL / Google / OpenAI behind one `Translator` interface | user-selected in settings |
| Packaging | `uv` lock + AUR-style PKGBUILD later | reproducible |

## 5. Phased plan

Phases 0–2 produce a working tool. 3–4 are quality and polish.

- **Phase 0 — Capture spike (0.5 day).** Confirm both portal paths work *inside the real session*,
  and measure real frame latency. Deliverable: a script that grabs the region to PNG at 5 Hz.
  *Exit criteria: consistent grabs while the game is fullscreen and focused.*
- **Phase 1 — Headless pipeline (0.5 day).** Region config + pixel-hash change detection +
  tesseract + NLLB, controlled from the CLI with JSON logs. No GUI.
  *Exit criteria: run it, alt-tab to the game, watch correct JA lines appear in the log.*
- **Phase 2 — GUI (1–2 days).** Drag-rectangle region selector, live panel, backend switch
  (local/cloud), hotkeys, tray icon, per-game region profiles, and an OCR accuracy harness over a
  folder of captured screenshots with edit-distance scoring.
- **Phase 3 — Quality & speed (1–2 days).** Glossary/term overrides (`Manager`→`マネージャー`,
  abnormality names), sentence assembly for multi-line boxes, NLLB int8 via CTranslate2 and/or
  ROCm (`hip-runtime-amd` is installed) to cut latency, RapidOCR fallback wiring.
- **Phase 4 — Hardening.** Multi-monitor and HDR/color-profile handling, packaging, auto-start.

## 6. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Wayland blocks capture / permission dialog each launch | med | portal grant is persistent; test in Phase 0 before building UI |
| OCR degrades on italic / stylized / small battle text | med | Phase 2 accuracy harness; RapidOCR v3 fallback; glossary fixes names |
| Translation latency feels slow | low | 0.7–1.5 s measured; int8 + GPU in Phase 3; caching hides repeats |
| Re-translating during typewriter reveal | low | settle-detection (stable across 2 polls) + pixel hash |
| Prompt-injection-ish garbage when the region is empty/UI changes | low | reject empty/low-confidence OCR results |
| Breaking game ToS | **low** | screen-read only — no memory reading, no injection, no patching |

## 7. Explicitly rejected approaches

- **Memory reading / DLL hooking** (what Textractor does): fragile per-patch, and ToS risk. Screen OCR
  needs no game cooperation and survives updates.
- **Baking translations into the game** (Limbus localization mods): not what was asked, and fragile.
- **`opus-mt-en-jap`**: measured unusable.

## 8. Probe artifacts

- `probe/shot.webp` — source screenshot
- `probe/band.png` — extracted dialogue band (3x + autocontrast) used for OCR tests
- `probe/raw_crop.png` — 1x crop, used to show upscaling matters
- `probe/tessdata/` — `eng` + `jpn` fast traineddata downloaded for testing
- `probe/RESULTS.md` — raw measurements
- `.venv` / `.venv-v5` — throwaway probe environments (RapidOCR v4 / v6 paths)
