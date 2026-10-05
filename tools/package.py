"""Build an installable archive without local data, test stubs, or development downloads."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]


def main():
    target = ROOT / "dist" / "astrbot_plugin_motd-v1.0.0.zip"
    target.parent.mkdir(exist_ok=True)
    files = (
        [
            ROOT / n
            for n in (
                "__init__.py",
                "main.py",
                "metadata.yaml",
                "_conf_schema.json",
                "requirements.txt",
                "README.md",
                "LICENSE",
            )
        ]
        + sorted((ROOT / "services").glob("*.py"))
        + sorted((ROOT / "assets").glob("*.png"))
    )
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, "astrbot_plugin_motd/" + path.relative_to(ROOT).as_posix())
    print(target)


if __name__ == "__main__":
    main()
