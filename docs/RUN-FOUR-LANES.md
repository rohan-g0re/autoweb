# Runbook: four lanes from one real profile

Follow this top to bottom on the Linux machine that holds the browser profile. It needs no
other context: if you are a fresh session, read `docs/HANDOFF.md` first and then come back
here.

This exists as a file rather than as instructions in a chat because the step that creates
the lanes also requires restarting Claude Code, which can end the session holding the
instructions. A runbook that dies halfway through is worse than no runbook.

**What this proves.** That several browser lanes can be spawned from one already-logged-in
profile, that they run at the same time rather than taking turns, and that what they bring
back can be merged into the shared identity without destroying it. Nothing before this has
run against a real session; every lane test so far used an empty `storageState`.

**What it must never do.** Commit or push a profile, a `root.json`, or any lane state.
Those hold live cookies and refresh tokens. The repo is public.

---

## 1. Toolchain

```sh
git pull
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"          # or `uv sync` if uv is installed
pytest -q
ruff check .
python -m playwright install chromium
```

Report the raw `pytest` output. Most of this suite has never been executed on any machine,
so failures here are expected and are the point. Do not fix them silently; a failure in
`merge` or `trace` matters more than finishing the run.

## 2. Browser

`autoweb.toml` ships `browser = "chrome"`. On most Linux there is no Google Chrome at all,
so set:

```toml
[lanes]
browser = "chromium"
```

That file is tracked. Change it locally and do not commit the change.

**Then check which browser wrote the profile you are about to use.** Chrome and Chromium
keep the cookie encryption key under different OS keyring entries, so a profile written by
one decrypts to nothing when opened by the other, with no error raised. If the profile came
from Google Chrome and you open it with Chromium, you get an export with zero usable
cookies that otherwise looks fine. `state export` now exits non-zero in that case rather
than pretending.

## 3. Copy the profile. Copy, never point at the original.

Use the Pursuit **LinkedIn** profile, `~/Desktop/pursuit_linkedin/browser-profile-linkedin`.
The alternative is `~/Desktop/pursuit_personal/browser-profile`; either works, and the
LinkedIn one is the default because the tasks lean on LinkedIn. Make sure no browser is
using the directory while you copy it.

```sh
cp -r ~/Desktop/pursuit_linkedin/browser-profile-linkedin .autoweb/profiles/source
git check-ignore -v .autoweb/profiles/source     # must print a match
git status --porcelain                           # must stay empty
```

Launching a browser on a profile directory locks it, and a copy means nothing here can
damage the original.

## 4. Export the identity

```sh
autoweb state export --from-profile .autoweb/profiles/source \
  --visit https://www.linkedin.com/feed/ \
  --visit https://www.linkedin.com/mynetwork/ \
  --visit https://mail.google.com/ \
  --visit https://myaccount.google.com/
```

`--visit` is mandatory and this is the subtle part: a storageState collects localStorage and
IndexedDB by running script in a page per origin, and only for origins the context has
actually visited. A profile launched fresh has visited nothing, so an export without
`--visit` returns cookies and a half-identity that looks like a success.

If this exits non-zero saying nothing was captured, stop and read the message. It is more
likely to be the channel mismatch in step 2 than a problem with the profile.

## 5. Measure what you got

```sh
autoweb state inspect
```

Report the table: origins, cookies, and the split between localStorage and IndexedDB bytes.
**This number decides the next phase of the project.** If almost nothing is in IndexedDB,
lanes can harvest through MCP alone and a much larger piece of work never has to be built.

Report hostnames and byte counts only. Never print a cookie value, a token, or the contents
of a storage key, into a message or a file.

Then confirm the identity actually works before spending a lane on it:

```sh
autoweb state verify https://www.linkedin.com/feed/ --expect-text "<your first name>"
```

### If Google is not signed in, change the tasks

`goals/four-lanes-one-identity.md` assumes both LinkedIn and Google are signed in. A
LinkedIn-specific profile may hold only LinkedIn. If `inspect` shows no Google session,
replace the two Google tasks in that document with two more read-only LinkedIn surfaces,
`https://www.linkedin.com/mynetwork/` and `https://www.linkedin.com/notifications/`, and
say in your report that you changed them and why. Four tasks and four lanes either way. Do
not commit that edit.

Do not work around a missing login. A login wall is a fact about one URL, and an honest
"Google is not in this profile" is a result rather than a failure.

## 6. Lanes

```sh
autoweb lanes sync
autoweb lanes list
```

`lanes.max` in `autoweb.toml` is the ceiling, and the count is never hardcoded anywhere.
`sync` also grants `mcp__laneN` in the gitignored `.claude/settings.local.json`.

**Check that the lane agents are visible to you now.** New agent files are usually picked
up without a restart, and `lane-1`, `lane-3` and so on should appear as agent types you can
delegate to shortly after `sync`. If they do not appear, restart Claude Code and resume from
this step.

The reason to check rather than assume: agent definitions are cached at session start, so a
session that already had lane files keeps serving the old ones after their contents change.
That failure is silent and has already cost one wasted run, where stale lanes all connected
to a single server, shared one tab, and overwrote each other's page with no error anywhere.

### The folder must be trusted, or no lane gets a browser

`lanes sync` checks this and will tell you. If it says the folder is not trusted, stop:
Claude Code refuses to start the `mcpServers` in an agent file from an untrusted folder,
and it refuses silently. The lane runs, its browser tools are absent, `ToolSearch` says
"No matching deferred tools found", and nothing explains why outside a debug log.
`--dangerously-skip-permissions` does not help.

This is not hypothetical. On the first real attempt a naive agent found the lanes
unaided, dispatched four of them in one message, and zero browsers started.

Fix it by running Claude Code once in the repo folder and accepting the trust prompt.
The flag is `projects["<abs path>"].hasTrustDialogAccepted` in `~/.claude.json`, but
editing somebody's Claude config by hand is their decision, not yours: ask.

## 7. The test, and the part that must stay independent

Read `goals/four-lanes-one-identity.md`. **Do not do those tasks yourself.**

Spawn ONE general-purpose subagent and give it the content of that document. Tell it nothing
about AutoWeb, lanes, this repo, or what is being proven. The question under test is whether
a naive agent reads that document and the `parallel-lanes` skill and arrives at four
concurrent lanes unaided. Handing it the answer voids the result.

While it runs, count browsers from outside, because no agent can measure this about itself:

```sh
while :; do
  ps -eo args | grep -o -- '--user-data-dir=[^ ]*' | sort -u | wc -l
  sleep 2
done
```

Four Chromium instances on real sites is the memory-heavy case. If you hit pressure, say so
with numbers rather than quietly reducing the lane count.

## 8. Judge it with arithmetic

The subagent reports four timestamps per lane. Put them in a file:

```json
{
  "lane-1": {"T0_start": "...", "T1_loaded": "...", "T2_read": "...", "T3_end": "..."},
  "lane-3": {"T0_start": "...", "T1_loaded": "...", "T2_read": "...", "T3_end": "..."}
}
```

```sh
autoweb trace that-file.json
```

Exit 0 means every lane was alive at one instant, and it prints the overlap. Exit 1 means
they took turns and it names the pair. Exit 2 means the trace cannot be believed.

**Use the exit code, not the subagent's account of whether it was parallel.** An
orchestrator cannot tell a staggered dispatch from a concurrent one from the inside. This
project once published a defect that did not exist by trusting exactly that.

## 9. The merge

Each lane was asked to save `lane-N.json` as its final action.

```sh
autoweb merge lane-1.json lane-3.json --dry-run
```

Read the output before writing anything. The line to look for is `evicted`: two lanes
changed the same value to different things, so one holds a token the server has already
rotated away. The merge drops that origin rather than guessing, and you will have to sign
in to it again. That is the cheap outcome.

A `warning:` about a rotating site touched by more than one lane means the decomposition was
wrong, not the merge.

Expect caps to complain. The shipped `caps.total_bytes` is 2 MB and a real multi-origin
Google identity can be far larger. A cap violation blocks the write but still prints every
decision, so read them, then raise the cap deliberately in `autoweb.toml` if the size is
legitimate.

**Do not run `merge` without `--dry-run` against a `root.json` holding real sessions until
the dry run has been read and looks right.** The previous root is kept as `.bak`, but a
careful read costs less than a recovery.

## What to report

The `pytest` output, the `state inspect` table, what the independent subagent did and
whether it reached four lanes on its own, the `trace` verdict and its exit code, your
external browser count, and the merge dry run. Commit nothing, push nothing.

If any step fails, stop there and report the command and its exact output. A clear failure
at step 1 is worth more than a step 9 that cannot be trusted.
