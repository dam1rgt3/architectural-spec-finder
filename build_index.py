"""Build an offline, page-cited full-text index and a local semantic catalogue."""

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import uuid
from html.parser import HTMLParser
from xml.etree import ElementTree as ET
from zipfile import ZipFile, is_zipfile

import pypdfium2 as pdfium

from sync import ROOT


def clean(text):
    return re.sub(
        r"[ \t]+", " ", text.replace("\x00", "").replace("\r\n", "\n")
    ).strip()


def chunks(text, limit=1350):
    text = clean(text)
    while len(text) > limit:
        end = max(
            text.rfind("\n", limit // 2, limit), text.rfind(" ", limit // 2, limit)
        )
        split = end if end > 0 else limit
        yield text[:split].strip()
        text = text[split:].lstrip()
    if text:
        yield text


class TextReader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip = True
        if tag in ("tr", "p", "div", "li", "h1", "h2", "h3", "br"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = False

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def extracted(path):
    if not path.exists():
        return ""
    raw = path.read_bytes()
    if b"\x00" in raw[:512] or raw.startswith((b"%PDF", b"PK\x03\x04")):
        return ""
    text = raw.decode("utf-8-sig", "replace")
    if re.search(r"<(?:html|body|div|p|table|h1)\b", text, re.IGNORECASE):
        parser = TextReader()
        parser.feed(text)
        return " ".join(parser.parts)
    return text


def digest(path):
    with path.open("rb") as src:
        return hashlib.file_digest(src, "sha256").hexdigest()


SCHEMA = """
CREATE TABLE docs(id TEXT PRIMARY KEY, name TEXT, filename TEXT, kind TEXT, url TEXT,
    pdf INTEGER, pages INTEGER, sections INTEGER, state TEXT, note TEXT, sha TEXT);
CREATE TABLE passages(id INTEGER PRIMARY KEY, doc TEXT, page INTEGER, label TEXT, body TEXT);
CREATE INDEX passage_doc ON passages(doc,page);
CREATE VIRTUAL TABLE search USING fts5(title,body,tokenize='unicode61 remove_diacritics 2');
CREATE TABLE ratings(id INTEGER PRIMARY KEY, doc TEXT, page INTEGER, family TEXT, variant TEXT,
    metric TEXT, val TEXT, units TEXT, y0 REAL, y1 REAL, caption TEXT, validity TEXT);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE vectors(doc TEXT PRIMARY KEY, vector BLOB);
"""


def table_ratings(db, doc_id, pdf_path, candidates, validity):
    """Only accept the explicitly labelled modular-skylight or demo table layout."""
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        for n in candidates:
            pg = pdf.pages[n - 1]
            for table in pg.find_tables():
                content = table.extract()
                header = " ".join(
                    str(c or "") for row in content[:3] for c in row
                ).lower()
                if not (
                    "model/glazing" in header
                    and "u-factor" in header
                    and "shgc" in header
                ):
                    continue
                rows = []
                for geom, cells in zip(table.rows, content):
                    parts = [
                        clean(c).replace("\n", " ") for c in cells if c and c.strip()
                    ]
                    if (
                        len(parts) >= 4
                        and re.match(r"^[A-Z][A-Z0-9-]+/", parts[0])
                        and all(re.fullmatch(r"\d+(?:\.\d+)?", c) for c in parts[1:4])
                    ):
                        rows.append([parts[0], parts[1:4], geom.bbox[1], geom.bbox[3]])
                    elif (
                        len(parts) == 1
                        and rows
                        and len(parts[0]) < 65
                        and re.match(r"^(?:\d|IG\b|LowE)", parts[0])
                    ):
                        rows[-1][0] += " " + parts[0]
                        rows[-1][3] = geom.bbox[3]
                for variant, numbers, top, bottom in rows:
                    for (metric, unit), value in zip(
                        (("U-factor", "Btu/(h·ft²·°F)"), ("SHGC", ""), ("VT", "")),
                        numbers,
                    ):
                        # This adapter accepts only the imperial U-factor header and known column order.
                        flat = re.sub(r"\s", "", header)
                        if (
                            "btu" not in flat
                            or flat.index("u-factor") > flat.index("shgc")
                            or "transmittance" not in flat
                            or flat.index("shgc") > flat.index("transmittance")
                        ):
                            continue
                        db.execute(
                            "INSERT INTO ratings VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                doc_id,
                                n,
                                variant.split("/")[0],
                                variant,
                                metric,
                                value,
                                unit,
                                top / pg.height,
                                bottom / pg.height,
                                "Model / glazing option",
                                validity,
                            ),
                        )


def index_doc(db, record, folder):
    doc_id, title = record["id"], record["name"]
    original = folder / "original"
    is_pdf = False
    if original.exists():
        with original.open("rb") as f:
            is_pdf = b"%PDF-" in f.read(1024)
    pages, count, candidates, validity = 0, 0, [], ""
    warnings = list(record.get("errors", []))

    def add(text, page, label):
        nonlocal count
        for body in chunks(text):
            if len(body) < 2:
                continue
            rid = db.execute(
                "INSERT INTO passages(doc,page,label,body) VALUES(?,?,?,?)",
                (doc_id, page, label, body),
            ).lastrowid
            search_title = title + " " + (record.get("filename") or "")
            db.execute(
                "INSERT INTO search(rowid,title,body) VALUES(?,?,?)",
                (rid, search_title, body),
            )
            count += 1

    if is_pdf:
        try:
            pdf = pdfium.PdfDocument(str(original))
            try:
                pages = len(pdf)
                for i in range(pages):
                    pg = pdf[i]
                    tp = pg.get_textpage()
                    try:
                        txt = tp.get_text_bounded()
                    finally:
                        tp.close()
                        pg.close()
                    add(txt, i + 1, "PDF page")
                    if not validity:
                        found = re.search(
                            r"Valid Through:\s*([\d/-]+)", txt, re.IGNORECASE
                        )
                        if found:
                            validity = found.group(1)
                    if (
                        re.search(r"Model\s*/\s*Glazing", txt, re.IGNORECASE)
                        and re.search("U-Factor", txt, re.IGNORECASE)
                        and "SHGC" in txt
                    ):
                        candidates.append(i + 1)
            finally:
                pdf.close()
        except Exception as exc:
            warnings.append("PDF extraction: " + type(exc).__name__)
    if candidates:
        try:
            table_ratings(db, doc_id, original, candidates, validity)
        except Exception as exc:
            warnings.append("Structured table: " + type(exc).__name__)
    if original.exists() and not is_pdf and is_zipfile(original):
        try:
            with ZipFile(original) as archive:
                files = archive.namelist()
                if "word/document.xml" in files:
                    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
                    xml = ET.fromstring(archive.read("word/document.xml"))
                    paragraphs = [
                        "".join(t.text or "" for t in p.iter(w + "t"))
                        for p in xml.iter(w + "p")
                    ]
                    add("\n".join(paragraphs), None, "Word document")
                if "xl/workbook.xml" in files:
                    import openpyxl

                    with original.open("rb") as source:
                        book = openpyxl.load_workbook(
                            source, read_only=True, data_only=True
                        )
                        try:
                            for sheet in book:
                                lines = [
                                    " | ".join(
                                        str(v) if v is not None else "" for v in row
                                    ).strip(" |")
                                    for row in sheet.values
                                ]
                                add(
                                    "\n".join(x for x in lines if x),
                                    None,
                                    "Sheet • " + sheet.title,
                                )
                        finally:
                            book.close()
        except Exception as exc:
            warnings.append("Office extraction: " + type(exc).__name__)
    text = extracted(folder / "content")
    if text:
        add(text, None, "Indexed source text")
    hashed_path = original if original.exists() else folder / "content"
    sha = digest(hashed_path) if hashed_path.exists() else ""
    ocr_path = folder.parent.parent / "ocr" / (sha + ".json")
    if ocr_path.is_file():
        for part in json.loads(ocr_path.read_text(encoding="utf-8")):
            add(part["body"], part["page"], part["label"])
    state = "searchable" if count else "metadata only"
    db.execute(
        "INSERT INTO docs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            doc_id,
            title,
            record.get("filename", title),
            record["kind"],
            record.get("url"),
            is_pdf,
            pages,
            count,
            state,
            "; ".join(warnings),
            sha,
        ),
    )


MODEL = "BAAI/bge-small-en-v1.5"


def make_vectors(db):
    import numpy as np
    from fastembed import TextEmbedding

    model = TextEmbedding(
        model_name=MODEL, cache_dir=str(ROOT / "data" / "models"), threads=4
    )
    ids, descriptions = [], []
    for did, title in db.execute("SELECT id,name FROM docs ORDER BY id"):
        first = db.execute(
            "SELECT body FROM passages WHERE doc=? ORDER BY id LIMIT 1", (did,)
        ).fetchone()
        ids.append(did)
        descriptions.append(title + "\n" + (first[0][:1200] if first else ""))
    for did, vector in zip(ids, model.embed(descriptions, batch_size=12), strict=True):
        db.execute(
            "INSERT INTO vectors VALUES(?,?)",
            (did, np.asarray(vector, dtype="float32").tobytes()),
        )
    db.execute("INSERT INTO meta VALUES('model',?)", (MODEL,))


def build(base, semantic=True):
    records = json.loads((base / "inventory.json").read_text(encoding="utf-8"))
    tmp = base / ("build-" + uuid.uuid4().hex + ".sqlite")
    db = sqlite3.connect(tmp)
    try:
        db.executescript(SCHEMA)
        for i, record in enumerate(records, 1):
            index_doc(db, record, base / "sources" / record["id"])
            if i % 40 == 0:
                print(f"Indexed {i}/{len(records)}", flush=True)
        if semantic:
            make_vectors(db)
        print(
            "Coverage:",
            db.execute("SELECT state,count(*) FROM docs GROUP BY state").fetchall(),
            "passages:",
            db.execute("SELECT count(*) FROM passages").fetchone()[0],
            "table values:",
            db.execute("SELECT count(*) FROM ratings").fetchone()[0],
            flush=True,
        )
        db.commit()
        db.close()
        tmp.replace(base / "index.sqlite")
    finally:
        db.close()
        tmp.unlink(missing_ok=True)


def add_vectors(base):
    tmp = base / ("vectors-" + uuid.uuid4().hex + ".sqlite")
    try:
        shutil.copyfile(base / "index.sqlite", tmp)
        with sqlite3.connect(tmp) as db:
            db.execute("DELETE FROM vectors")
            db.execute("DELETE FROM meta WHERE key='model'")
            make_vectors(db)
        db.close()
        tmp.replace(base / "index.sqlite")
    finally:
        tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--library", choices=["library", "demo"], default="library")
    ap.add_argument("--lexical-only", action="store_true")
    ap.add_argument("--vectors-only", action="store_true")
    args = ap.parse_args()
    base = ROOT / "data" / args.library
    if args.vectors_only:
        add_vectors(base)
    else:
        build(base, not args.lexical_only)
