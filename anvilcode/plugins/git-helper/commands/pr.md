---
description: Draft a pull-request description from the current changes
---
Draft a pull-request description for the current branch. Run `git diff main` (or the best
matching base branch) and `git log --oneline -15`, then produce:

1. **Title** — one imperative line
2. **What changed** — bullet summary grouped by area
3. **Why** — the motivation
4. **Risk & testing** — what could break and how it was/is tested

Do not create the PR or push anything.
