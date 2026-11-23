"""Read the knowledge base attached to one agent into a private local directory."""

import argparse
import hashlib
import json
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
API = "https://api.elevenlabs.io/v1/convai/"


def request(url, key=None, stream=None):
    for attempt in range(5):
        req = Request(url, headers={"User-Agent": "ArchitecturalSpecFinder/1"})
        if key:
            if not url.startswith(API):
                raise ValueError("Unexpected API origin")
            req.add_unredirected_header("xi-api-key", key)
        try:
            with urlopen(req, timeout=50) as response:
                if stream:
                    with stream.open("wb") as out:
                        shutil.copyfileobj(response, out, 256 * 1024)
                    return
                return response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            status = getattr(exc, "code", None)
            if (
                attempt == 4
                or status
                and status not in (408, 425, 429, 500, 502, 503, 504)
            ):
                raise RuntimeError(
                    f"HTTP {status}" if status else type(exc).__name__
                ) from None
            time.sleep(2**attempt)


def list_documents(agent_name, key):
    agents, cursor = [], None
    while True:
        query = {"search": agent_name, "page_size": 100}
        if cursor:
            query["cursor"] = cursor
        page = json.loads(request(API + "agents?" + urlencode(query), key))
        agents.extend(
            a for a in page["agents"] if a["name"].casefold() == agent_name.casefold()
        )
        cursor = page.get("next_cursor")
        if not cursor:
            break
    if len(agents) != 1:
        raise ValueError("Set the full, unique agent display name.")
    agent = json.loads(
        request(API + "agents/" + quote(agents[0]["agent_id"], safe=""), key)
    )
    docs = agent["conversation_config"]["agent"]["prompt"].get("knowledge_base", [])
    return list({d["id"]: d for d in docs}.values())


def save_json(path, obj):
    temp = path.with_suffix(".partial")
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def local_key(value):
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def fetch_document(doc, cache, key, refresh):
    docid = local_key(doc["id"])
    folder = cache / docid
    folder.mkdir(exist_ok=True)
    saved = folder / "info.json"
    if saved.exists() and not refresh:
        prev = json.loads(saved.read_text(encoding="utf-8"))
        if (
            not prev.get("errors")
            and (folder / "content").exists()
            and (doc["type"] != "file" or (folder / "original").exists())
        ):
            return prev
    title = re.split(r"[\\/]", doc.get("name", ""))[-1] or "Untitled document"
    record = {"id": docid, "name": title, "kind": doc["type"], "errors": []}
    endpoint = API + "knowledge-base/" + quote(doc["id"], safe="")
    try:
        meta = json.loads(request(endpoint, key))
        record["filename"] = re.split(r"[\\/]", meta.get("filename") or title)[-1]
        if doc["type"] == "url" and urlsplit(meta.get("url") or "").scheme in (
            "http",
            "https",
        ):
            record["url"] = meta["url"]
        parsed = meta.get("extracted_inner_html") or meta.get("text")
        raw = (
            parsed.encode()
            if isinstance(parsed, str) and parsed.strip()
            else request(endpoint + "/content", key)
        )
        temp = folder / "content.partial"
        temp.write_bytes(raw)
        temp.replace(folder / "content")
    except Exception as exc:
        record["errors"].append("Extracted content: " + str(exc))
    if doc["type"] == "file":
        try:
            source = json.loads(request(endpoint + "/source-file-url", key))
            url = (
                source
                if isinstance(source, str)
                else next(
                    v
                    for v in source.values()
                    if isinstance(v, str) and v.startswith("https://")
                )
            )
            if urlsplit(url).scheme != "https":
                raise ValueError("Unexpected original-file URL scheme")
            temp = folder / "original.partial"
            request(url, stream=temp)
            temp.replace(folder / "original")
        except Exception as exc:
            record["errors"].append("Original file: " + str(exc))
    save_json(saved, record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--agent")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    env = {}
    if args.env_file.exists():
        for line in args.env_file.read_text(encoding="utf-8-sig").splitlines():
            name, sep, val = line.partition("=")
            if sep:
                env[name.strip()] = val.strip().strip('"').strip("'")
    env.update(os.environ)
    key = env.get("ELEVENLABS_API_KEY") or env.get("ELEVEN_API_KEY")
    name = args.agent or env.get("ELEVENLABS_AGENT_NAME")
    if not key or not name:
        parser.error("Supply an agent name and a read-only ElevenLabs API key.")
    docs = list_documents(name, key)
    if not docs:
        raise SystemExit("This agent has no attached knowledge-base entries.")
    base = ROOT / "data" / "library"
    cache = base / "sources"
    cache.mkdir(parents=True, exist_ok=True)
    print(f"Found {len(docs)} document IDs.", flush=True)
    results = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        tasks = [pool.submit(fetch_document, d, cache, key, args.refresh) for d in docs]
        for future in as_completed(tasks):
            results.append(future.result())
            if len(results) % 25 == 0:
                print(f"Read {len(results)}/{len(docs)}", flush=True)
    save_json(
        base / "inventory.json", sorted(results, key=lambda r: (r["name"], r["id"]))
    )
    issues = sum(bool(r["errors"]) for r in results)
    print(
        f"Inventoried {len(results)} entries; {issues} entries have retrieval warnings.",
        flush=True,
    )


if __name__ == "__main__":
    main()
