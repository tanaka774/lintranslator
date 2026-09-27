"""Starting the program: what a bare `lintranslator` means."""
from __future__ import annotations

from lintranslator.cli import parse_argv


def test_no_arguments_means_the_gui():
    args = parse_argv([])
    assert args.command == "gui"
    # The attributes `cmd_gui` reads have to exist, or the default is a crash.
    assert args.pick is False
    assert args.panel is False
    assert args.position is None
    assert args.func.__name__ == "cmd_gui"


def test_a_subcommand_still_wins():
    assert parse_argv(["check"]).command == "check"
    assert parse_argv(["read", "--repeat", "2"]).command == "read"
    assert parse_argv(["gui", "--panel"]).panel is True


def test_the_default_reads_arguments_as_well_as_the_absent_case():
    """`lintranslator --config x` must not lose the flag to the default."""
    args = parse_argv(["--config", "/tmp/other.json"])
    assert args.command == "gui"
    assert str(args.config) == "/tmp/other.json"


def test_the_ocr_recipe_can_be_set_for_one_run(tmp_path):
    """`read` and `run` are how a box is tuned without the GUI."""
    from lintranslator.cli import _load

    path = tmp_path / "config.json"
    path.write_text("{}")
    args = parse_argv(["read", "--config", str(path), "--invert", "--threshold", "144"])
    cfg = _load(args)
    assert cfg.ocr.invert is True
    assert cfg.ocr.threshold == 144


def test_threshold_zero_on_the_command_line_means_no_cut(tmp_path):
    """0 is a value, not "unset", so it has to override a stored cut."""
    from lintranslator.cli import _load

    path = tmp_path / "config.json"
    path.write_text('{"ocr": {"threshold": 128}}')
    args = parse_argv(["read", "--config", str(path), "--threshold", "0"])
    assert _load(args).ocr.threshold == 0
