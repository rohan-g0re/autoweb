# The goal file format

A goal is an end state **plus the assertion that proves it was reached**. A goal without
a checkable assertion is not a goal yet, it is a request, and `/autoweb:aw-run` has
nothing to report pass or fail against.

One goal is one markdown file in this directory. `/autoweb:aw-run <name>` reads
`goals/<name>.md`, and the argument may be given with or without `.md`.

Goals are **per project**. They describe one app, one account and one set of data, so
none of them ship with the plugin and none of them are visible from another project.
This file is the format; the goals beside it are yours.

`runs/` is where the deliverables land, and `/autoweb:aw-setup --init-project` adds it to
`.git/info/exclude` along with the identity and the lane files. Run output is evidence,
not source.

## The template

Copy this into `goals/<name>.md` and fill it in. Keep the headings exactly as they are —
`aw-run` reads them by name.

```markdown
# Goal: <one line saying what state the world is in when this is done>

## Target

<The URL or URLs this goal acts on, one per line. Name the exact page, not the
site, when the goal starts somewhere specific.>

https://example.com/dashboard
https://example.com/reports

## Resources

<Everything the run is given. Data to fill, in the exact wording it should be
typed. Files to read, by path relative to the project. Directions that are not
obvious from the page.>

- Form values: name `Example Co`, quantity `3`, region `EU`
- Input file: `data/january.csv`
- The report list is paginated; the newest entry is on page 1

<Credentials never appear in this file.> The run uses the saved identity in
`root.json`, created once by `state export <url>` with a human logging in by
hand. AutoWeb never sees a password, and a goal file that holds one is a
credential committed to a repository.

## The work

<The sub-goals, numbered. One numbered item is one piece of work. Items that
need nothing from each other can each take a lane; items that must happen in
order say so.>

1. <sub-goal>
2. <sub-goal>
3. <sub-goal, which needs 2 to have finished>

## Assertion

<Numbered, and every one deterministic. These are what `aw-run` executes and
reports pass or fail against, so each has to be checkable without judgement.
The five shapes that qualify:>

1. A URL match — the final URL contains `/reports/confirmed`
2. Visible text — the page shows `Submitted successfully`
3. An element state — the `Submit` button is disabled after submission
4. A file that must exist — `runs/<date>-<name>/report.csv` is present and non-empty
5. A shell command that exits 0:
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/aw.py" -C "$PWD" state verify https://example.com/dashboard --expect-text "Signed in"`

<No LLM judge, no "looks right", no "the agent reports success". If you cannot
state the assertion before the run, the goal is not specified yet.>

## Constraints

<The tool-call budget, and anything the run must not do.>

- Tool-call budget: <n> browser calls. Each browser call is one LLM round trip,
  measured at about 11 seconds, so a 155-call page walk is 28 minutes. Design
  against the call count rather than the step count: snapshot once, plan, act,
  verify once.
- A sub-goal that cannot be completed is reported as **blocked, with the
  reason**. A plausible value nobody read off a page fails the whole run, not
  just the step it appears in — the gap is visible and the invented number is
  not.
- <Anything else: sites to leave alone, a record not to modify, one lane only
  for a site that holds money or rotates its session token.>

## Reporting

<Where the deliverable goes and what it contains.>

The deliverable goes to `runs/<YYYY-MM-DD>-<name>/`, beside the `run-log.md`
that `aw-run` writes there. Name the file and say what shape it is:

- `runs/<YYYY-MM-DD>-<name>/report.csv` — one row per record, columns
  `id,status,url`
```

## Notes on filling it in

- **`## Target` decides whether the identity is checked.** When it names a URL, `aw-run`
  runs `state verify` against it before any lane starts, so a session that did not
  survive is a failing command rather than four lanes quietly logged out.
- **`## The work` decides the lane count.** Count the numbered items that need nothing
  from each other. Four price checks on four retailers is four lanes; one checkout flow
  is one lane, because step two cannot start until step one finishes. The ceiling is what
  `lanes list` reports as generated, and `lanes.max` in `autoweb.toml` is a brake rather
  than a target.
- **`## Assertion` is the only thing that decides pass or fail.** `aw-run` makes one pass
  and reports each numbered assertion; it does not retry, heal or re-plan. Re-running is
  the user's call.
- **Keep the headings.** `aw-run` reads `## Target`, `## Resources`, `## The work`,
  `## Assertion`, `## Constraints` and `## Reporting` by name. Extra prose under a
  heading is fine; a renamed heading is not found.
