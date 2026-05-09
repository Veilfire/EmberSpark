# Template: `inbox-processor`

Watches a local directory (the "inbox") for new files, classifies them,
moves them into the appropriate subdirectory of the scratch volume, and
fires a notification when it's done. Optionally emails a summary.

**What it does on each run:**

1. List files in the configured inbox path using `filesystem.list`.
2. For each file, read a preview to determine the document type
   (invoice / receipt / contract / other).
3. Move the file into `{scratch}/inbox/<category>/<name>`.
4. Write a short markdown summary to `{deliverables}/inbox/summary.md`.
5. Optional: send the summary via `email_sender`.

## Required plugins

| Plugin | Purpose |
|---|---|
| `filesystem` | list / read / move files |
| `markdown_writer` | write the summary |
| `email_sender` | email the summary (optional) |

## Required secrets (if using email)

- `smtp_username`, `smtp_password`

## Install

```bash
spark template install inbox-processor
```

Then configure `filesystem.allow_paths` to include your inbox directory.
