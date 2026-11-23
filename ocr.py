"""Recover English text from scanned pages and embedded Word images, entirely locally."""

import argparse
import hashlib
import json
import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile, is_zipfile

from sync import ROOT, save_json

ENGINE = None
TESSDATA = ROOT / "data" / "models" / "tessdata"


def detect(image):
    global ENGINE
    import tesserocr

    if ENGINE is None:
        ENGINE = tesserocr.PyTessBaseAPI(
            path=str(TESSDATA), lang="eng", psm=tesserocr.PSM.AUTO
        )
    image.thumbnail((2750, 2750))
    ENGINE.SetImage(image.convert("RGB"))
    text = ENGINE.GetUTF8Text().strip()
    if len(re.sub(r"[^A-Za-z0-9]", "", text)) < 5:
        return ""
    return text


def read_file(task):
    original, target = task
    import pypdfium2 as pdfium
    from PIL import Image

    out = []
    with original.open("rb") as f:
        pdf = b"%PDF-" in f.read(1024)
    if pdf:
        with pdfium.PdfDocument(str(original)) as document:
            for n in range(len(document)):
                page = document[n]
                textpage = page.get_textpage()
                try:
                    native = textpage.get_text_bounded().strip()
                    if len(re.sub(r"\W", "", native)) < 40:
                        bitmap = page.render(
                            scale=min(3.0, 2500 / max(page.get_size()))
                        )
                        try:
                            text = detect(bitmap.to_pil())
                            if text:
                                out.append(
                                    {
                                        "page": n + 1,
                                        "label": "OCR • PDF page",
                                        "body": text,
                                    }
                                )
                        finally:
                            bitmap.close()
                finally:
                    textpage.close()
                    page.close()
    elif is_zipfile(original):
        with ZipFile(original) as z:
            if "word/document.xml" in z.namelist():
                for name in z.namelist():
                    if not re.fullmatch(
                        r"word/media/[^/]+\.(png|jpe?g|tiff?|bmp)", name, re.IGNORECASE
                    ):
                        continue
                    with Image.open(BytesIO(z.read(name))) as image:
                        if min(image.size) < 120:
                            continue
                        txt = detect(image)
                        if txt:
                            out.append(
                                {
                                    "page": None,
                                    "label": "OCR • Word image " + Path(name).name,
                                    "body": txt,
                                }
                            )
    save_json(target, out)
    return len(out)


def prepare(base):
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    trained = TESSDATA / "eng.traineddata"
    if not trained.exists():
        TESSDATA.mkdir(parents=True, exist_ok=True)
        with urlopen(
            "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/eng.traineddata",
            timeout=60,
        ) as remote:
            content = remote.read()
        if (
            hashlib.sha256(content).hexdigest()
            != "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2"
        ):
            raise ValueError("Unexpected English OCR model checksum")
        trained.write_bytes(content)
    cache = base / "ocr"
    cache.mkdir(exist_ok=True)
    tasks = {}
    for r in json.loads((base / "inventory.json").read_text(encoding="utf-8")):
        src = base / "sources" / r["id"] / "original"
        if not src.is_file():
            continue
        with src.open("rb") as f:
            sha = hashlib.file_digest(f, "sha256").hexdigest()
        if not (cache / (sha + ".json")).exists():
            tasks[sha] = (src, cache / (sha + ".json"))
    with ProcessPoolExecutor(max_workers=min(os.cpu_count() or 1, 5)) as pool:
        fs = {pool.submit(read_file, t): s for s, t in tasks.items()}
        errors = []
        for i, job in enumerate(as_completed(fs), 1):
            try:
                job.result()
            except Exception as e:
                errors.append((fs[job][:12], type(e).__name__ + ": " + str(e)[:120]))
            if i % 40 == 0:
                print(f"OCR checked {i}/{len(fs)} originals", flush=True)
    if errors:
        raise RuntimeError(str(errors))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--library", choices=["library", "demo"], default="library")
    prepare(ROOT / "data" / p.parse_args().library)
