---
name: commit-messages
description: Write clear git commit messages and make small, focused commits. Use whenever committing changes with git.
---

# Commit messages

Before committing, run `git status` and `git diff --staged` and make sure only related changes are staged.
Split unrelated work into separate commits.

Message shape:

```
Summary in the imperative, under 60 characters

Why the change was needed and what it does, wrapped at 72 columns.
Mention anything a reviewer would otherwise have to discover.
```

- The summary says what the commit does ("Fix crash when the list is empty"), not what you did ("Fixed stuff").
- No trailing period on the summary line.
- Don't commit secrets, generated files or large binaries. If one slipped in, say so instead of committing it.
