---
description: Close a step — gate, automated review, HANDOFF update, then hand over for the human to commit
---

Close the current step as a commit checkpoint. Do NOT commit, stage, or push.

1. Run `make check`. If it is red, fix and re-run until green (the Stop hook enforces this too).
2. Review the uncommitted changes (`git status`, `git diff`, untracked files) with the
   `/code-review medium <absolute path of THIS repo>` skill — always pass the explicit path:
   without it the reviewer may pick another git repo from the session's working directories.
   Discard any finding whose file is not inside this repo. For each finding: fix it if it is a real correctness or integrity
   issue (add a regression test first), otherwise note why it was skipped. Re-run `make check`.
3. Update `docs/HANDOFF.md`: status table, what this step delivered, next step, new decisions,
   gotchas. It must let an agent with zero context (possibly not Claude) continue.
4. Append one row to the "Review log" table in `docs/DEVELOPMENT.md`
   (step, findings, fixed, skipped + reason).
5. Report to the human: a short summary of the change, the review outcome, and a suggested
   conventional commit message. Then stop.

$ARGUMENTS
