# The goal file format

A goal is an end state **plus the assertion that proves it was reached**. A goal without
a checkable assertion is not a goal yet, it is a request, and `/autoweb:aw-run` has
nothing to report pass or fail against.

One goal is one markdown file in this directory. `/autoweb:aw-run <name>` reads
`goals/<name>.md`, and the argument may be given with or without `.md`.

Goals are **per project**. They describe one app, one account and one set of data, so
none of them ship with the plugin and none of them are visible from another project.
This file is the format; the goals beside it are yours.

`runs/` is where the deliverables land, and `/autoweb:aw-setup` adds it to the project's
`.gitignore` along with the identity and the lane files. Run output is evidence,
not source.

## The template

Copy this into `goals/<name>.md` and fill it in. Keep the headings exactly as they are —
`aw-run` reads them by name.

````markdown
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

## Assertions

<One fenced `toml` block, one `[[assert]]` table per assertion. `autoweb goal
check` evaluates them after the run and writes `runs/<run>/status.json`; the
model that did the work never judges them. One example of each kind:>

```toml
[[assert]]
id = "dashboard-loads"               # unique: a-z, 0-9 and -
kind = "url"                         # the final URL must start with expect
url = "https://example.com/dashboard"
expect = "https://example.com/dashboard"

[[assert]]
id = "signed-in"
kind = "text"                        # expect must be in the page's visible text
url = "https://example.com/dashboard"
expect = "Signed in"

[[assert]]
id = "report-confirmed"
kind = "element"                     # a CSS selector that must be visible
url = "https://example.com/reports"
expect = "tr.report-row.confirmed"
timeout_s = 45                       # optional; the default is 30

[[assert]]
id = "report-written"
kind = "command"                     # run in the project root; exit 0 passes
run = "test -s runs/{run}/report.csv"
```

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
and `status.json` that `aw-run` writes there. Name the file and say what shape it is:

- `runs/<YYYY-MM-DD>-<name>/report.csv` — one row per record, columns
  `id,status,url`
````

## Notes on filling it in

- **`## Target` decides whether the identity is checked.** When it names a URL, `aw-run`
  runs `state verify` against it before any lane starts, so a session that did not
  survive is a failing command rather than four lanes quietly logged out.
- **`## The work` decides the lane count.** Count the numbered items that need nothing
  from each other. Four price checks on four retailers is four lanes; one checkout flow
  is one lane, because step two cannot start until step one finishes. The ceiling is what
  `lanes list` reports as generated, and `lanes.max` in `autoweb.toml` is a brake rather
  than a target.
- **`## Assertions` is the only thing that decides pass or fail.** `aw-run` makes one
  pass, then runs `autoweb goal check goals/<name>.md --run <YYYY-MM-DD>-<name>` and
  reports what it printed. It does not retry, heal or re-plan. Re-running is the user's
  call.
- **Keep the headings.** `aw-run` reads `## Target`, `## Resources`, `## The work`,
  `## Assertions`, `## Constraints` and `## Reporting` by name. Extra prose under a
  heading is fine; a renamed heading is not found.

## The four kinds of assertion

| `kind` | needs | passes when |
|---|---|---|
| `url` | `url`, `expect` | the final URL starts with `expect` |
| `text` | `url`, `expect` | `expect` is in the page's visible text, ignoring case and spacing |
| `element` | `url`, `expect` | an element matching the CSS selector `expect` is visible |
| `command` | `run` | the command exits 0 |

Every kind takes `timeout_s`, which defaults to 30. Any other key is an error, so a typo
cannot quietly change what is checked.

- **The browser kinds open `url` in a new headless browser seeded from `root.json`.**
  Nothing a lane did carries over. The check waits for the page to load, for the network
  to go quiet, and then two seconds more, so a single-page app that sends a logged-out
  visitor to its login page has done so before anything is judged. A page that answers
  with an HTTP error fails, whatever it shows.
- **`command` runs through the shell, in the project root.** `{run}` becomes the run id
  and `{goal}` the goal's file name without `.md`; no other braces are touched. The
  detail in `status.json` holds the exit code and the end of the output.
- **An observation is a command.** Some facts are only in what the run wrote down, such
  as a value a lane read off a page. Have `## Reporting` put that fact in the
  deliverable, then check the deliverable:

  ```toml
  [[assert]]
  id = "order-confirmed"
  kind = "command"
  run = "grep -q 'Order status: confirmed' runs/{run}/report.md"
  ```

- **The result is a file.** `runs/<run>/status.json` lists each assertion with its
  `status` (`PASS`, `FAIL`, or `ERROR` when it could not be evaluated) and a `detail`.
  `done` is true only when every assertion passed. The command exits 0 when `done` is
  true, 1 when it is not, and 2 when the goal is malformed or an input is missing.
- **Try it before the run.** `autoweb goal check goals/<name>.md --run test --dry-run`
  parses the block and prints the plan. It opens no browser and runs no command. A goal
  with no `## Assertions` block exits 2: a goal without a checkable assertion is not a
  goal.
