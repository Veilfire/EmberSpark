# Template: `code-reviewer`

A code-review agent pointed at a local git repo. Runs `git diff` against
a baseline ref, reads the changed files, and writes a markdown review
(pros / cons / suggestions / blocker-level issues) to the deliverables
directory.

## Required plugins

| Plugin | Purpose |
|---|---|
| `git` | Read-only `status`, `log`, `diff`, `show` |
| `filesystem` | Read the actual file contents around the diffs |
| `markdown_writer` | Write the review |

## Required secrets

- `anthropic_key` (or whichever provider)

## Install

```bash
spark template install code-reviewer
```

Configure `git.allow_repos` to include the repo you want reviewed.
Configure `filesystem.allow_paths` to include the repo root (read-only).
