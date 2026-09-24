"""Starting the program: what a bare `lintranslator` means.

A desktop app is expected to open when it is run. `gui` is labelled the default in
`--help`, so an argument-less invocation has to reach it rather than print a usage
error and exit 2 - that is the path a `.desktop` launcher takes when someone
clicks the menu entry.
"""
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
    # Including one that reads the same namespace the default does.
    assert parse_argv(["gui", "--panel"]).panel is True


def test_the_default_reads_arguments_as_well_as_the_absent_case():
    """`lintranslator --config x` must not lose the flag to the default."""
    args = parse_argv(["--config", "/tmp/other.json"])
    assert args.command == "gui"
    assert str(args.config) == "/tmp/other.json"
