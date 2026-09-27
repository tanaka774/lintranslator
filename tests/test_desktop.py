"""`lintranslator install-desktop`: the menu entry and its launcher."""
from __future__ import annotations

from lintranslator.cli import cmd_install_desktop


def test_install_desktop_writes_both_files(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setenv("XDG_BIN_HOME", str(tmp_path / "bin"))

    assert cmd_install_desktop(None) == 0

    desktop = tmp_path / "share" / "applications" / "lintranslator.desktop"
    launcher = tmp_path / "bin" / "lintranslator-gui"
    assert desktop.is_file()
    assert launcher.is_file()

    # the desktop entry runs the launcher by name, so it has to be executable
    assert launcher.stat().st_mode & 0o111
    entry = desktop.read_text()
    assert "Exec=lintranslator-gui" in entry
    # KDE matches the window to this file by application id, which lives in this key
    assert "StartupWMClass=dev.lintranslator.translator" in entry
    assert launcher.read_text().startswith("#!/bin/sh")


def test_it_falls_back_to_dot_local_without_the_xdg_variables(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_BIN_HOME", raising=False)

    assert cmd_install_desktop(None) == 0

    assert (tmp_path / ".local/share/applications/lintranslator.desktop").is_file()
    assert (tmp_path / ".local/bin/lintranslator-gui").is_file()


def test_the_two_templates_ship_inside_the_package():
    from importlib.resources import files

    data = files("lintranslator").joinpath("data")
    assert data.joinpath("lintranslator.desktop").is_file()
    assert data.joinpath("lintranslator-gui").is_file()
