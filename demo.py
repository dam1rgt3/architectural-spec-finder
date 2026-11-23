"""Set up a standalone library of two original, fictional product sheets."""

import shutil

from build_index import build
from sync import ROOT, save_json


def prepare():
    base = ROOT / "data" / "demo"
    records = []
    for source in sorted((ROOT / "examples").glob("*.pdf")):
        did = source.stem.lower().replace(" ", "-")
        folder = base / "sources" / did
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, folder / "original")
        records.append(
            {
                "id": did,
                "name": source.name,
                "filename": source.name,
                "kind": "file",
                "errors": [],
            }
        )
    if not records:
        raise FileNotFoundError("Missing example PDFs")
    save_json(base / "inventory.json", records)
    build(base, semantic=False)


if __name__ == "__main__":
    prepare()
