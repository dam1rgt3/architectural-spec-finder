# Architectural Spec Finder

A local search workspace for architectural product literature. Search all connected documents, inspect the original PDF page, and resolve a product's exact glazing option before quoting a performance value.

## Try the demo

The repository includes two original, fictional skylight data sheets. The demo needs no ElevenLabs account, API key or model download. After installing the Python dependencies it runs offline.

On Windows with Python 3.12, run these commands from the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe demo.py
.\.venv\Scripts\python.exe app.py --library demo
```

Open [the local application](http://127.0.0.1:8769). Search **AFV/20 U-factor**. Both rows have the same short product code. Select **coated double glazing** to see 0.46 Btu/(h·ft²·°F) and the highlighted row on page 1. The clear-glazing row reports 0.50. These are fictional demonstration figures.

## Use the complete local library

The configured workstation has a separate private library. Double-click **Open Spec Finder.cmd** to open it. From a terminal, run `.\.venv\Scripts\python.exe app.py`. The default address is the same as above. Use `--port 8770` to run a second instance alongside the demo.

- **Specification search** searches full text. Matching passages link to their source. A scanned page is marked OCR and can be inspected as an image.
- **Source library** lists every entry attached to the agent, including entries with similar titles. Open a document to see its original filename, search within it, open the PDF, download the original file, or visit the source website when the importer received a URL.
- **Performance tables** show the printed glazing choices for supported layouts. Choose the full row before quoting a value. Dates shown on a source are the dates printed in that edition. General search retrieves passages throughout the library. Structured numerical answers currently support U-factor, SHGC and visible transmittance in the recognized model/glazing table layout.

## Import an authorized ElevenLabs library

Copy `.env.example` to `.env` and enter a read-only ElevenLabs key with `convai_read` and the full agent display name. Alternatively, provide an existing environment file with `--env-file` and an exact name with `--agent`. The application itself reads the local index and needs no API key.

```powershell
.\.venv\Scripts\python.exe sync.py
.\.venv\Scripts\python.exe ocr.py
.\.venv\Scripts\python.exe build_index.py
.\.venv\Scripts\python.exe app.py
```

The importer inventories each attached document ID, retrieves original files and extracted content, and records failures per source. It reuses completed downloads on a retry. Pass `--refresh` when source content has changed. The OCR step checks PDFs with little native text and embedded Word images. The first run downloads English Tesseract language data and the FastEmbed model. To build a text-search-only index without downloading an embedding model, run `build_index.py --lexical-only`.

API keys, originals, extracted text, OCR results, embeddings and indexes stay under the ignored local configuration or `data/` directory. The sample PDFs under `examples/` were created for this project. The server listens only on loopback.

## How search works

SQLite FTS5 indexes passages from PDF pages, extracted web content, Office documents and OCR. Exact article numbers, filenames and page references receive additional weight. FastEmbed runs locally and ranks documents using an embedding of their title and opening passage. Related-document suggestions are displayed separately from source passages. No document is sent to a hosted language model to generate an answer.

For supported tables, the parser checks the column headings and units, joins wrapped glazing descriptions, and retains the row coordinates on the original page. A click on a row displays the stored cell and an overlay on the source image. This prevents a value from moving between nearly identical variants. Short model codes can be ambiguous even when they match the document exactly. General passages and OCR extracts remain available for manual verification.

## Verify

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_search
```

The tests rebuild only the two-sheet demo. They check variants with the same short code, unknown variants, document-scoped search, PNG page rendering and original PDF access.

Built with [FastEmbed](https://github.com/qdrant/fastembed), [pdfplumber](https://github.com/jsvine/pdfplumber), [pypdfium2](https://github.com/pypdfium2-team/pypdfium2), [openpyxl](https://openpyxl.readthedocs.io/) and [Tesseract](https://github.com/tesseract-ocr/tesseract). English OCR weights come from [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast).
