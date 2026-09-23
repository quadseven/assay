# This repository is public

Everything here — code, commit history, issues, pull requests, comments,
review threads, discussions, releases, wiki pages, GitHub Pages content —
is visible to anyone on the internet, forever, including after deletion
(forks, caches, and search-engine indexes outlive an edit or a delete).
Treat every write to this repo, in any surface, as something a stranger
reads the moment you make it.

This file exists because that got violated repeatedly before it was
written down. Real personal names, a real home network's addresses, real
device identifiers, and a live unrotated credential all ended up in public
issue trackers and public git history — not through carelessness in the
code, but through ordinary conversational writing in issues, PR bodies, and
code comments, where the discipline that already existed for the shipped
code was never applied. This file is the fix: the same discipline,
extended to every surface an agent writes to, not just the diff.

## The rule

**Nothing that identifies a specific person, a specific private network,
or a specific credential may appear anywhere in this repository, in any
form, ever.** Not in code. Not in a code comment. Not in an issue body. Not
in a PR description. Not in a commit message. Not in a comment reply. Not
in a test fixture. Not "just this once because it's only in a closed
issue" — closed does not mean hidden, and neither does deleted.

This is broader than "don't commit secrets." A secret scanner catches an
API key. It does not catch a sentence like *"[a real first name]'s home
server, reachable at their usual address, needed a restart"* — nothing
there matches a secret
pattern, and it is exactly the kind of sentence that put a real name, a
real domain, and a real IP into a public tracker tonight. Write for a
stranger from the first word, not just the code.

### Concretely, never write any of the following into this repo, on any surface

- A real personal name — yours, a collaborator's, anyone's. Refer to
  people by role ("the maintainer," "the operator," "a reviewer") the same
  way this file does.
- A real hostname, domain, or subdomain that resolves to a private network
  or a real person's infrastructure (a home VPN suffix, a personal tailnet
  domain, a work-in-progress product's real URL before it's meant to be
  public). Use a placeholder that is visibly fake: `example.com`,
  `your-server.internal`, `<your-domain>`.
- A real IP address on any network you actually operate — home, cloud, or
  otherwise. Use an RFC 5737 documentation range (`192.0.2.0/24`,
  `198.51.100.0/24`, `203.0.113.0/24`) or an obviously fictional one
  (`10.0.0.X` as a *labeled example* is fine; a live address copy-pasted
  from a real `curl`/`dig`/log output is not).
- A real device identifier: a serial number, a MAC address, an IMEI, a
  hardware ID, an account ID, a database GUID tied to a live system.
- A credential of any kind, live or "already rotated" — a key, a token, a
  password, a signing certificate, a webhook URL with a token embedded in
  the path. "It's already been rotated" is not a reason to leave the old
  value visible; redact it anyway, because the *pattern* (which SSM path,
  which naming convention, which provider) is itself information.
- A path that reveals a real local username (`/Users/<name>/...`,
  `C:\Users\<name>\...`) or a real machine's hostname.
- A quote attributed to a specific named person, even an accurate one.
  Paraphrase instead: "the operator decided..." not "Alice said...".
- An `@`-mention of anyone who is not already part of the conversation.
  A mention notifies that account and subscribes it to the thread, and
  neither can be undone by editing or deleting the text. `@grug` in
  particular is an unrelated real user, not the review bot: the bot is
  `grug-tribe[bot]` and takes slash commands (`/grug improve` re-runs the
  code review, `/grug recheck` re-runs the plan check). To name a handle
  in prose, put it in backticks, which GitHub does not treat as a mention.
- The name of another private repository, service, or internal system
  that isn't itself meant to be discoverable. Cross-repo references
  belong in the *private* tracker, not migrated wholesale into a public
  one.

### If you are migrating or importing content

Content that already exists elsewhere — an issue being moved from a
private repo, a comment thread being copied in, history being subtree-split
into a new repo — is not exempt from this rule because it was written
before this file existed. **Migration is not a scrub.** Before content
from anywhere else lands in this repo, on any surface, re-read it against
every bullet above and rewrite what fails. If a whole issue's substance is
inseparable from the personal/private detail it's built on, don't migrate
it — summarize the generic problem it represents instead, or leave it out.

Wholesale-copying a private issue tracker into a public one because it was
"faster" is exactly how this happened the first time.

### If you find a violation already in the repo

Fix the current tree, then say plainly in your response that older
issues/PRs/comments/history may still carry it and that this needs a
human decision, not a silent edit-and-move-on. Do not delete or rewrite
someone else's public comment without asking first — you may not always
know why it was worded that way. Editing your own agent-authored content
to remove a violation is always fine and encouraged.

## The other half: this repo must be genuinely reusable

A stranger must be able to clone this repository, supply their **own**
configuration and secrets, and have it work — without reading anything
beyond the README and an example config file to know what to change.

- Every value specific to one deployment (a hostname, an IP, a region, an
  account ID, a device identifier) is a variable, an environment variable,
  or a config file entry — never a literal baked into source, a workflow
  file, or a script.
- Ship a `.env.example` / `config.example.*` alongside any file that reads
  real config, with every key present and an obviously-placeholder value
  (`YOUR_DOMAIN_HERE`, not a real one with the last octet changed).
- If a CI/CD pipeline assumes infrastructure that doesn't ship with the
  repo (a specific runner pool, a specific cloud account, a specific
  private reusable workflow), say so explicitly in the README rather than
  let a stranger discover it as a mysterious failure. "This requires your
  own self-hosted runner and your own AWS account" is an honest
  dependency; a silent reference to `uses: <this-operator>/infra-private/...`
  is not.
- Prefer this repo's own already-public reusable workflows
  (`quadseven/infra-public/...`) over hand-rolled CI where one already
  exists — they're already written to take config as input rather than
  assume it.

## Why this file, not just a smarter secret scanner

A pattern-matching scanner catches shapes: an AWS key, a PEM block, a
32-character hex string. It cannot catch a paragraph of ordinary prose
that happens to name a real person or describe a real network in plain
words — which is where nearly everything this file exists to prevent
actually showed up. Scanners still belong in CI as a backstop for the
shapes they *can* catch; this file is the layer above that, for the judgment
a scanner doesn't have.

<!-- Everything above the next line is synced from quadseven/infra-public and replaced on every sync. Put this repo's own content below it. -->
<!-- repo-specific below -->

# AGENTS.md

Rules for anyone -- human or agent -- committing to this repository.

**This repo is public. Assume everything you push is permanent and world-readable.**

That is stronger than "everything you merge". See the next section for why.

## The one rule that shapes every other

**A leak is permanent the moment you PUSH, not when you merge.**

GitHub creates a `refs/pull/N/head` ref for every pull request ever opened. It
is server-side, not writable by any client, and **a force-push to `main` cannot
touch it**. Closing the PR does not remove it. Deleting the branch does not
remove it. `git ls-remote` lists these refs by default on a public repo, and
anyone can fetch one.

A normal clone does not fetch them, which is the dangerous part: `git log --all`
on a fresh clone reports **clean** while the leak sits on GitHub's servers.

This repository exists because that was discovered the expensive way. Its
predecessor had its tree scrubbed, its tracker cleaned, and a full history
rewrite prepared and verified -- and `refs/pull/2/head` still held 11
occurrences of private terms across 4 files. Nothing a client can run removes
them. The only fix was a new repository, which is this one.

**So: scan before you push a branch. Not before you merge.** By merge time it is
already too late.

## Run the scan, and read what it says

```bash
python3 tools/leakscan.py --selftest   # ALWAYS first
python3 tools/leakscan.py .
```

**`CLEAN` on its own means nothing.** Two ways this scan reports clean while
leaking, both already hit here:

1. **No terms loaded.** Private nicknames live in `tools/leakscan.local.txt`,
   which is gitignored on purpose -- publishing the scanner must not publish
   what it hunts. If that file is missing, the scanner loads zero terms and
   reports `CLEAN`. On the predecessor repo, `CLEAN` meant **41 leaks in 19
   files**. The scan prints `(loaded N local term(s))` to stderr; if you do not
   see it, you are not scanning for nicknames at all.

2. **A pattern silently stopped compiling.** That is what `--selftest` is for:
   every class is checked against a control string it must match, and it prints
   how many fired. Fewer classes than expected means the report is worthless.

**CI cannot cover you here.** The workflow runs this scan, but the runner has no
local terms file, so it only checks the built-in topology patterns -- private
addresses, cluster naming, username-bearing paths. A bare product name or host
nickname passes CI every time. It has to be that way: on a public repo the
workflow log is public, so a finding would print the term it exists to protect.

**Nickname coverage is a local, pre-push gate. The green check is not it.**

## What must never land here

- Private hostnames or machine nicknames. Name the *shape* instead: `<node-a>`,
  `<target>`, `CLAUDE_OR_TARGET_HOST`. Examples stay runnable that way.
- RFC1918 / CGNAT addresses (`10.x`, `192.168.x`, `172.16-31.x`, `100.64-127.x`).
- Private repository names, private product or project names, internal issue or
  PR references.
- Filesystem paths carrying a username.
- Any credential. A key that is public *by design* (a Firebase Web API key, say)
  is still worth a comment saying so, because the next reader will otherwise
  file it as a leak -- that happened, twice.
- Anything sent to a third party that names private work: `User-Agent`,
  `HTTP-Referer` and `X-Title` end up in someone else's dashboard.

## How to reference private work

**Keep the finding, drop the identity.** The engineering is the value; the repo
name is not.

> On an internal refactor PR, a local model produced a patch that passed 1823
> existing tests and still could not be merged.

That sentence is publishable and loses nothing. A bare cross-repo issue
reference in its place would have added no information a reader outside
that repo could use -- and would have tripped this repo's own scanner, which
flags them as a class.

## What counts as a result

Only numbers measured here, by a suite in this repo, on stated hardware.

**Vendor and aggregator benchmark claims are never recorded as results.** A
blank cell in the scoreboard means *not run*, never *not good* -- and it must
say so, because a reader supplies the harsher reading for free.

Publish results whether or not they flatter. The most useful finding this repo
has produced is that a model measuring ~3x faster at decode was 4x *slower* per
agentic task; nobody would have looked for that if the flattering number had
been allowed to stand alone.

## Suite conventions

- The grading rule is a **pure module** -- evidence in, verdict out, no IO. A
  stored result must be re-gradable later without re-running any model.
- Corpora are **frozen** and carry a hash sidecar. If a fixture set can drift
  under a run, fingerprint it and void the run when it moves.
- A task that can be satisfied by doing nothing is not a task. Assert the
  starting state **fails** before crediting a fix.
- Every suite README states what its numbers are **not**.
