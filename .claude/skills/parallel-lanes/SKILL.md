---
name: parallel-lanes
description: Split browser work across parallel lanes. Use when a task touches several independent sites or several independent records, when the user asks to run things in parallel, or when a sequential browser plan would take more than a few minutes.
---

# Parallel lanes

A lane is a subagent with its own browser. Lanes cannot see each other's tabs,
cookies or storage, and all of them start from the same logged-in identity.

Dispatch a lane by delegating to the subagent named `lane-1`, `lane-2` and so on, one
task each, in a single message so they run at the same time.

## Decide how many

Count the **independent** pieces of work, then use that many lanes, up to the number
of lane agent files that exist. Independent means no piece needs another's output.

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
this way carry `rotates = true`: set by hand under `[origins]` in `autoweb.toml`, or
recorded by the loop in `.autoweb/learned.json` when it catches one mid-run.
`autoweb config show` prints both.

When in doubt about a site that holds money or credentials, one lane.

## Write the lane's task

Each lane sees only what you hand it. Give it the URL, the data it needs, what counts
as done, and how to recognise failure. It cannot ask you a question mid-run.

Tell it the assertion. "Find the price" is a request; "report the price shown on the
product page, with the URL you read it from" is checkable.

## Read what comes back

A lane reports what it did, what it found, and the URL behind every fact. Treat a
blocked sub-goal as information: the lane that says "this needed a login I do not
have" has told you something true, and a lane that invented a plausible answer has
not.

When two lanes disagree about the same site, believe neither until you know which one
actually loaded the page.

## Done when

- Every independent piece of work went to its own lane.
- Any rotating or money-handling site went to exactly one lane.
- Each lane was given a checkable assertion rather than a request.
- Results name their sources, and blocked work is reported as blocked.

## If there are no lanes

`autoweb lanes list` shows which lane agent files exist. `autoweb lanes sync` creates
them from config. They are generated rather than committed, because each one holds an
absolute path to this machine's identity file.
