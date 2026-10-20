"""Entry point for the bundled engine binary (PyInstaller). Same commands as the `saarathi` CLI."""

from saarathi.cli.main import app

if __name__ == "__main__":
    app(prog_name="saarathi")
