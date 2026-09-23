"""Convert a HuggingFace seq2seq model to CTranslate2 int8.

Why this exists: the transformers path for NLLB-600M needs ~4.7 GB of weights
(both safetensors and .bin, because that stack loads the .bin) and runs at
0.8-1.6 s/line on CPU. The int8 CTranslate2 build is ~600 MB and measured 4-5x
faster on this project's sample lines, with equivalent output.

    python -m tlkun.convert --model facebook/nllb-200-distilled-600M \
        --out data/ct2/nllb-600m-int8
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import paths

DEFAULT_MODEL = "facebook/nllb-200-distilled-600M"
DEFAULT_OUT = paths.default_ct2_dir()


def convert(
    model: str = DEFAULT_MODEL,
    out: Path | str = DEFAULT_OUT,
    quantization: str = "int8",
    force: bool = False,
) -> Path:
    """Convert `model` into a CTranslate2 directory at `out`."""
    try:
        from ctranslate2.converters import TransformersConverter
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "ctranslate2 is required for conversion:\n  uv pip install ctranslate2"
        ) from exc

    out = Path(out)
    if out.exists() and any(out.iterdir()) and not force:
        print(f"{out} already exists; pass --force to overwrite", file=sys.stderr)
        return out

    print(f"converting {model} -> {out} ({quantization}) ...", file=sys.stderr)
    started = time.monotonic()
    # low_cpu_mem_usage avoids holding two full copies of the weights in RAM,
    # which matters for a 600M-parameter model on a normal desktop.
    converter = TransformersConverter(model, low_cpu_mem_usage=True)
    converter.convert(str(out), quantization=quantization, force=force)
    elapsed = time.monotonic() - started

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(
        f"done in {elapsed:.0f}s: {out} ({size / 1e6:.0f} MB)",
        file=sys.stderr,
    )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tlkun.convert",
        description="convert a HuggingFace model to CTranslate2 int8 for the ct2 backend",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="HuggingFace model id")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output directory")
    parser.add_argument(
        "--quantization",
        default="int8",
        choices=["int8", "int8_float32", "int16", "float32"],
        help="weight quantization (int8 is the smallest and fastest here)",
    )
    parser.add_argument("--force", action="store_true", help="overwrite an existing output")
    args = parser.parse_args(argv)
    convert(args.model, args.out, args.quantization, args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
