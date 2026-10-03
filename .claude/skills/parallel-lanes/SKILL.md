---
name: parallel-lanes
description: Split browser work across parallel lanes. Use when a task touches several independent sites or several independent records, when the user asks to run things in parallel, or when a sequential browser plan would take more than a few minutes.
---

# Parallel lanes

A lane is a subagent with its own browser. Lanes cannot see each other's tabs, cookies
or storage. Under the default `lanes.isolated = true` every lane is seeded from
`root.json`, so they all start from the same logged-in identity and nothing is written
to disk.

The exception is `lanes.isolated = false`. Those lanes are never seeded from
`root.json`. Each one gets its own profile directory under `.autoweb/profiles/lane-N`
instead, keeps whatever it was logged in to last time, and a profile that has never
been used starts logged out. If the lanes you dispatch behave as though nobody logged
in, check `autoweb config show` for that flag before blaming the sites. Parallel work
wants the default.

Dispatch a lane by delegating to the subagent named `lane-1`, `lane-2` and so on, one
task each, in a single message so they run at the same time.

## Decide how many

Count the **independent** pieces of work, then use that many lanes, up to the number of
lanes that `autoweb lanes list` reports as **generated**. Independent means no piece
needs another's output.

Read that list carefully, because the ceiling is the generated count and not the file
count. `lanes list` also reports hand-written files, and a hand-written `lane-2.md`
without an inline `mcpServers` block shares the session's one browser instead of owning
its own. Dispatching it looks like a lane and then quietly collides with every other
lane in a single tab. Run `autoweb lanes sync` and delete the hand-written file if you
want that lane number back.

Use one lane when the work is a single chain of steps on one site: log in, navigate,
fill, submit. Splitting that across lanes adds coordination and wins nothing, because
step two cannot start until step one finishes.

Use several lanes when the task names several sites, several accounts, or several
records that each get the same treatment. Four price checks on four retailers is four
lanes. One checkout flow is one lane.

**Lane count is a decision, not a setting.** `lanes.max` in `autoweb.toml` is the
ceiling that stops a runaway decomposition, never a target to fill.

## Keep a rotating site in one lane

Some sites reissue their session token on every use and treat the old one as stolen if
it appears again. Two lanes holding that token look exactly like a replay attack, and
the server is entitled to revoke the whole family, which logs out every lane at once
including the identity they all came from.

Give such a site to **one** lane and route the rest elsewhere. Sites known to behave
this way carry `rotates = true`, set by hand under `[origins]` in `autoweb.toml`.
`autoweb config show` prints them. There is a matching `rotates` list in
`.autoweb/learned.json` for a loop to record a site it catches rotating mid-run, but
nothing writes that file yet, so today the only entries you will see are the ones
somebody typed into `autoweb.toml`.

When in doubt about a site that holds money or credentials, one lane.

## Write the lane's task

Each lane sees only what you hand it. Give it the URL, the data it needs, what counts
as done, and how to recognise failure. It cannot ask you a question mid-run.

Tell it the assertion. "Find the price" is a request; "report the price shown on the
product page, with the URL you read it from" is checkable.

## A lane that uses your tools is not isolated

This is the one failure that undoes everything above, and it was measured rather than
guessed. A lane can see your own `mcp__playwright__*` tools alongside its own
`mcp__laneN__*` ones. Yours belong to the session's single shared browser, so a lane
that calls them lands in the same tab as every other lane that did the same, and the
lanes then navigate each other's pages. The generated lane body forbids it in writing,
but the tools are still visible to the lane.

So when a lane reports that it used `mcp__playwright__*`, it has invalidated its own
isolation and anything it says about which page it was on is unreliable. Treat that run
as void: re-dispatch the work rather than reconciling the results, and if several lanes
did it, re-dispatch them one at a time.

## Read what comes back

A lane reports what it did, what it found, and the URL behind every fact. Treat a
blocked sub-goal as information: the lane that says "this needed a login I do not
have" has told you something true, and a lane that invented a plausible answer has
not.

When two lanes disagree about the same site, believe neither until you know which one
actually loaded the page.

## Done when

- Every independent piece of work went to its own lane.
- Every lane dispatched was one `autoweb lanes list` reports as generated.
- Any rotating or money-handling site went to exactly one lane.
- Each lane was given a checkable assertion rather than a request.
- No lane reported using `mcp__playwright__*`; any that did was re-dispatched.
- Results name their sources, and blocked work is reported as blocked.

## If there are no lanes

`autoweb lanes list` shows which lane agent files exist and which of them are
generated. `autoweb lanes sync` creates them from config. They are generated rather
than committed, because each one holds an absolute path to this machine's identity
file.

Syncing is not enough on its own. An isolated lane is launched pointing at `root.json`,
so somebody must have run `autoweb state export <url>` to create it first. Without that
file a lane does not merely start logged out: **every** browser call it makes fails with
ENOENT, so the lane comes back having done nothing at all. `autoweb lanes sync` only
warns about this and still exits zero, so check for the file rather than trusting the
exit code. `autoweb config show` prints its path and whether it exists, and
`autoweb state verify <url>` proves the session inside it still works.
