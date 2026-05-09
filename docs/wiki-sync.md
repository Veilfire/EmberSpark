# Wiki Sync

EmberSpark keeps its user-facing wiki content in `./wiki/` (a normal folder in the main repo) and syncs it to the GitHub wiki (a separate git repo) on demand. This page documents how that works and why.

---

## How GitHub wikis actually work

A common misconception: GitHub does **not** render a `./wiki/` folder in your main repo as the project wiki. You can keep content there for visibility, but GitHub won't touch it.

The real mechanism: **every GitHub repo with the wiki feature enabled has a sibling git repo** at:

```
git@github.com:<owner>/<repo>.wiki.git
https://github.com/<owner>/<repo>.wiki.git
```

That second repo is where the wiki content actually lives. You push markdown files there, and GitHub renders them as the project wiki. Internal links use `[[Page Name]]` or standard markdown links to `Page-Name.md` (hyphens as spaces).

So if you want your wiki to be:

1. **Versioned alongside your code** — you need the content in the main repo
2. **Rendered by GitHub as the wiki** — you also need it in the wiki repo

The workflow that satisfies both is: edit in the main repo, sync to the wiki repo when you're ready to publish.

---

## EmberSpark's approach

EmberSpark keeps its wiki content at `./wiki/`. The sync script at `scripts/sync-wiki.sh`:

1. Resolves the wiki repo URL (`<owner>/<repo>.wiki.git`) — via `gh` CLI if available, or a `--repo` flag
2. Clones it to a temp directory
3. Copies every `.md` file from `./wiki/` into the clone (overwriting existing files)
4. Commits with a message like `Sync wiki from main (abc1234)` where `abc1234` is the main repo's current HEAD
5. Pushes to the wiki repo's default branch (`master` or `main` — it tries both)

You run it manually when you want to publish:

```bash
scripts/sync-wiki.sh                        # uses gh to resolve repo
scripts/sync-wiki.sh --repo Veilfire/EmberSpark  # explicit
scripts/sync-wiki.sh --dry-run              # show what would change
```

---

## First-time setup

Before the sync script can push to your wiki repo, three things need to be true:

1. **The repo exists on GitHub.** `git init` + `git push -u origin main` for the main repo.
2. **The wiki feature is enabled.** Go to the repo's Settings → General → Features and check "Wikis".
3. **The wiki has at least one page.** GitHub won't clone `<repo>.wiki.git` until the wiki has been initialized. Go to `https://github.com/<owner>/<repo>/wiki`, click "Create the first page", add anything (it'll be overwritten on the first sync), save.

Once those three are done, `scripts/sync-wiki.sh` can clone and push.

---

## What the script does NOT do

- **It doesn't delete pages.** If you remove a file from `./wiki/`, the sync script won't remove it from the GitHub wiki — you'd have to delete it manually in the wiki repo. This is deliberate safety.
- **It doesn't handle image uploads.** If you add images to wiki pages, you need to drop them into the wiki repo manually (or extend the script).
- **It doesn't handle internal-link rewriting.** The wiki pages reference each other via `[Page Name](Page-Name)` markdown links that GitHub's wiki renderer understands. Source-code links to `docs/*.md` use absolute `https://github.com/...` URLs so they work on both the wiki and in the main repo browser.
- **It doesn't auto-run on push.** You run it manually when you're ready to publish a wiki update. If you want CI-based sync, add a GitHub Action later — see "Alternative: GitHub Action" below.

---

## Why manual, not automatic

The default for documentation tools is "sync on every push." EmberSpark's approach is manual for two reasons:

1. **Docs and code land together.** A PR that adds a feature should include the wiki updates alongside the code. Manual sync means you bundle the two in one mental operation — edit code, edit wiki, push both, run sync when you're happy.
2. **Bad docs shouldn't leak.** If your CI flakes and the wiki sync fails silently, you get mid-sync broken content on the public wiki. A manual flow lets you see the diff before it publishes.

If you outgrow the manual flow, switching to automated is easy.

---

## Alternative: GitHub Action

If you want CI-based sync instead of (or in addition to) manual, the simplest version is a GitHub Actions workflow that runs on pushes to main:

```yaml
# .github/workflows/sync-wiki.yml
name: Sync wiki

on:
  push:
    branches: [main]
    paths:
      - 'wiki/**'

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Sync wiki
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          # Clone the wiki repo
          git clone "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.wiki.git" wiki-repo
          cp wiki/*.md wiki-repo/
          cd wiki-repo
          git config user.name "github-actions"
          git config user.email "github-actions@github.com"
          git add -A
          if ! git diff --cached --quiet; then
            git commit -m "Sync wiki from ${GITHUB_SHA::7}"
            git push
          fi
```

Prerequisites:

- The wiki must already exist (first page created manually once)
- The default `GITHUB_TOKEN` needs `contents: write` permission on the workflow — which it has by default

Caveat: some orgs restrict the default `GITHUB_TOKEN` from pushing to wiki repos. If that's you, you'll need a PAT with `repo` scope stored as a secret.

---

## Naming conventions

GitHub wikis use specific filename conventions:

- `Home.md` is the wiki landing page
- Other pages are `Page-Name.md` where hyphens render as spaces in the page title
- Internal links can use `[[Page Name]]` (GitHub syntax) or standard markdown `[text](Page-Name)`

EmberSpark's wiki uses the second form consistently (`[Getting Started](Getting-Started)`) because standard markdown renders correctly in both the wiki and in the main repo's file browser.

---

## Current wiki pages

The `./wiki/` directory currently has:

### Onboarding
- `Home.md` — landing page
- `Getting-Started.md` — install + first run in 10 minutes
- `Installation.md` — complete install reference
- `First-Task.md` — a guided walkthrough

### Concepts
- `Concepts-Bounded-Autonomy.md`
- `Concepts-Sandbox.md`
- `Concepts-Plugins.md`
- `Concepts-Permissions.md`
- `Concepts-Personas.md`
- `Concepts-Memory.md`
- `Concepts-Learning.md`
- `Concepts-Skills.md`
- `Concepts-Privacy.md`
- `Concepts-Budgets.md`

### Working with plugins
- `Using-Plugins.md` — operator workflow
- `Plugin-Reference-Filesystem.md`
- `Plugin-Reference-HTTP-Client.md`
- `Plugin-Reference-Markdown-Writer.md`
- `Plugin-Reference-Shell.md`
- `Plugin-Reference-SQLite.md`
- `Plugin-Authoring.md`

### Permissions, deployment, ops
- `Permissions-Guide.md`
- `Security-Center-Guide.md`
- `Deployment-Guide.md`
- `Daemon-Modes.md`
- `Logging-And-Tracing.md`

### Feature guides
- `Persona-Manager-Guide.md`
- `Scheduling-Guide.md`
- `Web-UI-Guide.md`
- `Cost-And-Budgets.md`
- `Memory-Browser.md`
- `Skill-Catalog.md`
- `Command-Palette.md`

### Reference
- `API-Reference.md`
- `Configuration-Reference.md`
- `Troubleshooting.md`
- `FAQ.md`
- `Contributing.md`

---

## Updating after the repo is on GitHub

Once your main repo is initialized and pushed to `github.com/<owner>/spark`, the first sync looks like:

```bash
# 1. Enable the wiki in Settings → Features → Wikis
# 2. Create a stub Home page via the web UI so the wiki repo exists
# 3. Run the sync script
scripts/sync-wiki.sh --repo Veilfire/EmberSpark
```

After that, any time you edit `./wiki/*.md` and want to publish:

```bash
scripts/sync-wiki.sh
```

It'll pick up `Veilfire/EmberSpark` from `gh` automatically.

For a dry-run (see what would change without pushing):

```bash
scripts/sync-wiki.sh --dry-run
```

---

## Further reading

- [GitHub Wikis documentation](https://docs.github.com/en/communities/documenting-your-project-with-wikis/about-wikis)
- [wiki/Contributing.md](../wiki/Contributing.md) — how to contribute docs updates
