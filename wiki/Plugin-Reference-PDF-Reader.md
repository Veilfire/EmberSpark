# Plugin Reference: `pdf_reader`

Extract text (and optional metadata) from PDF files under an operator-allowlisted path tree. Pure offline, no network.

- **Required permissions:** `fs.read`
- **Required secrets:** none
- **Sensitivity:** `MODERATE`
- **Network:** not needed
- **Dependencies:** `pypdf` (pure Python)

---

## What the plugin does

- Accepts a path to a PDF file plus an optional page-range spec (`"1-10"`, `"3"`, `"5-"`, or `null` for all pages).
- Validates the path against `PathPolicy` (same `allow_paths`/`deny_paths` semantics as the filesystem plugin).
- Opens the PDF with `pypdf.PdfReader`, iterates over the selected pages, and extracts each page's text.
- Caps total pages (`max_pages`) and per-page characters (`max_chars_per_page`) so an abusive PDF can't blow up the model context.
- Optionally returns PDF metadata (title, author, subject, creator, producer, creation/modification dates).

---

## Configuration fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `allow_paths` | list of paths | `[]` | Directories the plugin may open PDFs from. **When empty**, falls back to the data-volume's `scratch` and `deliverables` paths so an agent can read PDFs the user dropped there without operator config. Configure explicit paths for production. |
| `deny_paths` | list of paths | `[]` | Nested denies inside allow paths. |
| `max_pages` | int | `200` | Pages per call ceiling. |
| `max_chars_per_page` | int | `20_000` | Truncation cap per page. |
| `include_metadata` | bool | `true` | Return the PDF metadata block. |

---

## What the model sends per call

```json
{
  "path": "~/Documents/research/white-paper.pdf",
  "pages": "1-5"
}
```

Returns:

```json
{
  "path": "/home/me/Documents/research/white-paper.pdf",
  "metadata": {
    "title": "A White Paper",
    "author": "Some Author",
    "page_count": 42
  },
  "pages": [
    {"page_num": 1, "text": "...", "char_count": 1234},
    ...
  ],
  "truncated_page_count": false,
  "truncated_text": false
}
```

---

## Operator workflow

**Start narrow.** Point `allow_paths` at a single directory the agent needs to read from — e.g. `~/Documents/spark-workspace/inputs`. Don't expose `~` or a broader tree.

**Use with `filesystem` for discovery.** The `pdf_reader` plugin extracts text from a specific file. To find PDFs in a directory, pair it with the `filesystem` plugin's `list` op first — the agent can enumerate, then read each one.

**Combine with `web_search` + `http_tool` for document research.** Flow: search for papers → download PDF via `http_tool` to the scratch path → `pdf_reader` extracts the text → the model summarizes.

---

## Common pitfalls

- **Scanned PDFs** — `pypdf` is a text extractor. Image-based scans return empty text. For scanned documents you need OCR (not shipped in v1).
- **Encrypted PDFs** — refused by `pypdf` at open time. Decrypt out of band.
- **Malformed PDFs** — `pypdf` may return partial or garbled text. The plugin catches per-page errors and returns empty strings rather than failing the whole call.

---

## Further reading

- [Plugin Reference: filesystem](Plugin-Reference-Filesystem) — enumerate directories
- [Using Plugins](Using-Plugins) — operator workflow for all built-ins
- [docs/plugin-config.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/plugin-config.md)
