"""Local read-only specification search and cited source viewer."""

import argparse
import json
import mimetypes
import re
import sqlite3
import threading
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

from sync import ROOT

STOP = set(
    [
        "what",
        "which",
        "whats",
        "is",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "and",
        "or",
        "for",
        "with",
        "on",
        "of",
        "in",
        "the",
        "that",
        "to",
        "a",
        "an",
        "does",
        "it",
        "do",
        "me",
        "my",
        "tell",
        "show",
        "give",
        "provide",
        "from",
        "can",
        "how",
        "about",
        "find",
        "get",
        "i",
        "this",
        "will",
        "would",
        "be",
        "its",
        "at",
        "as",
    ]
)


def terms(q):
    return list(
        dict.fromkeys(w for w in re.findall(r"[a-z0-9]+", q.lower()) if w not in STOP)
    )[:24]


def connect(base):
    db = sqlite3.connect((base / "index.sqlite").as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def catalogue(db):
    row = db.execute("""SELECT count(*) total,sum(state='searchable') searchable,sum(pdf) pdfs,
        sum(kind='url') websites,sum(pages) pages,sum(sections) passages FROM docs""").fetchone()
    return {
        **dict(row),
        "vectors": db.execute("SELECT count(*) FROM vectors").fetchone()[0],
    }


def rating_options(db, q, doc_id=None):
    q = q.lower()
    chosen = [
        name
        for pattern, name in [
            (r"\bu[\s-]*factor\b", "U-factor"),
            (r"\bshgc\b|solar heat gain", "SHGC"),
            (r"\bvt\b|visible transmittance", "VT"),
        ]
        if re.search(pattern, q)
    ]
    if len(chosen) != 1:
        return None
    families = {r[0] for r in db.execute("SELECT DISTINCT family FROM ratings")}
    explicit = [
        f for f in families if re.search(r"\b" + re.escape(f.lower()) + r"\b", q)
    ]
    velux = bool(re.search(r"\bvms\b", q) and re.search(r"\bvelux\b", q))
    if not explicit and not velux and not doc_id:
        return None
    rows = db.execute(
        """SELECT r.*, d.name FROM ratings r JOIN docs d ON r.doc=d.id
                         WHERE metric=? ORDER BY d.name, r.id""",
        (chosen[0],),
    ).fetchall()
    if velux:
        rows = [r for r in rows if r["family"] in {"HFC", "HVC"}]
    if doc_id:
        rows = [r for r in rows if r["doc"] == doc_id]
    if explicit:
        rows = [r for r in rows if r["family"] in explicit]
    if velux and ("fixed" in q or "venting" in q):
        enabled = {f for word, f in (("fixed", "HFC"), ("venting", "HVC")) if word in q}
        rows = [r for r in rows if r["family"] in enabled]
    variants = re.findall(r"\b([a-z][a-z0-9-]+/[a-z0-9.-]+)", q)
    if variants:
        rows = [
            r
            for r in rows
            if any(
                re.match(re.escape(v) + r"(?![a-z0-9])", r["variant"], re.IGNORECASE)
                for v in variants
            )
        ]
    # Require explicit selection of the full printed variant before exposing a numeric answer.
    return {
        "metric": chosen[0],
        "options": [
            {k: r[k] for k in ("id", "doc", "name", "page", "variant")} for r in rows
        ],
    }


class Semantic:
    def __init__(self, base):
        self.lock = threading.Lock()
        self.model = None
        self.ids = []
        with connect(base) as db:
            self.spec = db.execute(
                "SELECT value FROM meta WHERE key='model'"
            ).fetchone()
            if self.spec:
                import numpy as np

                rows = db.execute(
                    "SELECT doc,vector FROM vectors ORDER BY doc"
                ).fetchall()
                self.ids = [r[0] for r in rows]
                self.array = np.stack(
                    [np.frombuffer(r[1], dtype="float32") for r in rows]
                )

    @lru_cache(maxsize=96)
    def rank(self, q):
        if not self.ids:
            return []
        from fastembed import TextEmbedding

        with self.lock:
            if self.model is None:
                self.model = TextEmbedding(
                    model_name=self.spec[0],
                    cache_dir=str(ROOT / "data" / "models"),
                    local_files_only=True,
                    threads=3,
                )
            vector = next(iter(self.model.query_embed(q)))
            scores = self.array @ vector
        return [self.ids[i] for i in scores.argsort()[::-1][:12]]


def search(db, q, sem=None, doc_id=None):
    words = terms(q)
    rating = rating_options(db, q, doc_id)
    if not words:
        return {"hits": [], "rating": rating}
    expression = " OR ".join('"' + w + '"' for w in words)
    filt = " AND p.doc=?" if doc_id else ""
    params = (expression, doc_id) if doc_id else (expression,)
    rows = db.execute(
        """SELECT p.id,p.doc,p.page,p.label,p.body,d.name,d.filename,d.pdf,d.url
        FROM search JOIN passages p ON p.id=search.rowid JOIN docs d ON d.id=p.doc
        WHERE search MATCH ?"""
        + filt
        + " ORDER BY bm25(search,2,1) LIMIT 180",
        params,
    ).fetchall()
    semantic = sem.rank(q) if sem and not doc_id else []
    ranks = {d: i for i, d in enumerate(semantic)}
    hits = []
    for pos, r in enumerate(rows):
        names = r["name"] + " " + r["filename"]
        matches = [r"\b" + re.escape(w) + r"\b" for w in words]
        matched = sum(
            bool(re.search(term, r["body"] + " " + names, re.IGNORECASE))
            for term in matches
        )
        title_hits = sum(
            bool(re.search(term, names, re.IGNORECASE)) for term in matches
        )
        identifiers = re.findall(r"\b[a-z][a-z0-9]*(?:[-/][a-z0-9]+)+\b", q.lower())
        exact_title = any(
            re.search(re.escape(i) + r"(?![a-z0-9])", names, re.IGNORECASE)
            for i in identifiers
        )
        score = (
            matched / len(words) * 2
            + title_hits / len(words) * 0.65
            + float(exact_title)
            + 20 / (20 + pos)
            + (0.26 / (1 + ranks[r["doc"]] / 4) if r["doc"] in ranks else 0)
            + (0.15 if r["page"] else 0)
        )
        hits.append((score, dict(r)))
    hits.sort(key=lambda x: -x[0])
    selected = []
    counts = {}
    seen = set()
    for _, hit in hits:
        key = (hit["name"], hit["body"][:200])
        if key in seen or counts.get(hit["doc"], 0) >= (8 if doc_id else 2):
            continue
        seen.add(key)
        counts[hit["doc"]] = counts.get(hit["doc"], 0) + 1
        selected.append(hit)
        if len(selected) == 12:
            break
    # Semantic suggestions identify documents only. A near match is never a quoted value.
    related = []
    for did in semantic:
        if did in counts:
            continue
        d = db.execute("SELECT id,name FROM docs WHERE id=?", (did,)).fetchone()
        if d:
            related.append(dict(d))
        if len(related) == 3:
            break
    return {"hits": selected, "rating": rating, "related": related}


def browse(db, q, offset):
    words = terms(q)
    clause = " AND ".join("lower(name||' '||filename) LIKE ?" for _ in words) or "1"
    params = ["%" + w + "%" for w in words]
    total = db.execute("SELECT count(*) FROM docs WHERE " + clause, params).fetchone()[
        0
    ]
    docs = db.execute(
        "SELECT id,name,kind,pages,state,pdf FROM docs WHERE "
        + clause
        + " ORDER BY name,id LIMIT 30 OFFSET ?",
        [*params, offset],
    )
    return {"total": total, "items": [dict(d) for d in docs]}


def handler(base, sem, demo):
    class Handler(BaseHTTPRequestHandler):
        def send(self, data, mime, status=200, filename=None):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            if filename:
                self.send_header(
                    "Content-Disposition",
                    "attachment; filename*=UTF-8''" + quote(filename, safe=""),
                )
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.headers.get("Host", "").split(":")[0].lower() not in {
                "127.0.0.1",
                "localhost",
            }:
                return self.send(b"Local access only", "text/plain", 403)
            path = urlsplit(self.path)
            args = {k: v[0] for k, v in parse_qs(path.query).items()}
            q = args.get("q", "")[:400]
            static = {
                "/": "index.html",
                "/app.js": "app.js",
                "/style.css": "style.css",
                "/favicon.svg": "favicon.svg",
            }
            try:
                if path.path in static:
                    asset = ROOT / "web" / static[path.path]
                    return self.send(
                        asset.read_bytes(),
                        mimetypes.guess_type(asset.name)[0]
                        or "application/octet-stream",
                    )
                db = connect(base)
                try:
                    if path.path == "/api/stats":
                        result = {**catalogue(db), "demo": demo}
                    elif path.path == "/api/search":
                        result = search(db, q, sem, args.get("doc"))
                    elif path.path == "/api/documents":
                        result = browse(db, q, max(0, int(args.get("offset", "0"))))
                    elif path.path == "/api/document":
                        doc = db.execute(
                            "SELECT * FROM docs WHERE id=?", (args.get("id"),)
                        ).fetchone()
                        if not doc:
                            return self.send(b"Source not found", "text/plain", 404)
                        result = dict(doc)
                        result["preview"] = [
                            dict(r)
                            for r in db.execute(
                                "SELECT page,label,body FROM passages WHERE doc=? ORDER BY id LIMIT 3",
                                (doc["id"],),
                            )
                        ]
                    elif path.path == "/api/rating":
                        row = db.execute(
                            "SELECT r.*,d.name FROM ratings r JOIN docs d ON r.doc=d.id WHERE r.id=?",
                            (args.get("id"),),
                        ).fetchone()
                        if not row:
                            return self.send(b"Row not found", "text/plain", 404)
                        result = dict(row)
                    elif path.path.startswith(("/pdf/", "/original/")):
                        did = path.path.split("/")[-1]
                        row = db.execute(
                            "SELECT filename,pdf FROM docs WHERE id=? AND kind='file'",
                            (did,),
                        ).fetchone()
                        if not row or not re.fullmatch(r"[a-zA-Z0-9_-]+", did):
                            return self.send(b"Source not found", "text/plain", 404)
                        file = base / "sources" / did / "original"
                        if not file.is_file():
                            return self.send(b"Original unavailable", "text/plain", 404)
                        if path.path.startswith("/pdf/"):
                            if not row["pdf"]:
                                return self.send(
                                    b"Source is not a PDF", "text/plain", 404
                                )
                            return self.send(file.read_bytes(), "application/pdf")
                        return self.send(
                            file.read_bytes(),
                            "application/octet-stream",
                            filename=row["filename"],
                        )
                    elif path.path == "/api/page.png":
                        from io import BytesIO

                        import pypdfium2 as pdfium

                        did = args.get("doc", "")
                        n = int(args.get("page", 0))
                        row = db.execute(
                            "SELECT pages FROM docs WHERE id=? AND pdf=1", (did,)
                        ).fetchone()
                        if (
                            not row
                            or not re.fullmatch(r"[a-zA-Z0-9_-]+", did)
                            or not 1 <= n <= row[0]
                        ):
                            return self.send(b"Invalid page", "text/plain", 404)
                        with (
                            RENDER_LOCK,
                            pdfium.PdfDocument(
                                str(base / "sources" / did / "original")
                            ) as pdf,
                        ):
                            pg = pdf[n - 1]
                            bitmap = pg.render(
                                scale=min(1.8, 1500 / max(pg.get_size()))
                            )
                            buf = BytesIO()
                            bitmap.to_pil().save(buf, "PNG")
                            bitmap.close()
                            pg.close()
                        return self.send(buf.getvalue(), "image/png")
                    else:
                        return self.send(b"Not found", "text/plain", 404)
                finally:
                    db.close()
                self.send(
                    json.dumps(result, ensure_ascii=False).encode(),
                    "application/json; charset=utf-8",
                )
            except (ValueError, KeyError):
                self.send(b"Invalid request", "text/plain", 400)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

        def log_message(self, *args):
            pass

    return Handler


RENDER_LOCK = threading.Lock()

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--library", choices=["library", "demo"], default="library")
    p.add_argument("--port", type=int, default=8769)
    args = p.parse_args()
    base = ROOT / "data" / args.library
    if not (base / "index.sqlite").exists():
        p.error("Build the index first. See README.md.")
    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port), handler(base, Semantic(base), args.library == "demo")
    )
    print(f"Spec Finder is available at http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()
