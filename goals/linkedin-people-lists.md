# Goal: four people-lists off one LinkedIn identity

Produce **one markdown document** holding four lists of people, each row a name and a
LinkedIn profile link. No starting URL is given and no route is prescribed — work out
where to go and how to get there.

The identity is already logged in: seed from the project's `root.json`. This goal is
only reachable from inside a session, so an authwall is a failure, not an expected
block.

## Outputs

One file. Four sections, in this order, each a markdown table.

| # | Section | Count | Who qualifies |
|---|---|---|---|
| 1 | Amazon Seattle, in my network | **20** | A **1st-degree connection** whose *current* employer is Amazon **and** whose location is the Seattle area |
| 2 | Sinhgad Spring Dale School, Pune | **15** | *Current* employee of that school — teaching or non-teaching staff |
| 3 | Famous tech people | **10** | A public figure in tech. No connection to me required |
| 4 | Hotel management / hospitality | **20** | *Current* role in hotels, restaurants, resorts, catering or hospitality education. No connection to me required |

Every row carries, at minimum:

- **Name** — as it appears on the profile.
- **Profile link** — an absolute `https://www.linkedin.com/in/<slug>` URL.
- **Evidence** — the current title and employer (or, for section 3, the follower count)
  that made this person qualify, plus where it was read.

### Reading of the ask, where the ask was silent

These are decisions, not guesses to re-open. If one is wrong, the run still reports
what it did under this reading.

- **"In my connections" was said only of section 1.** Sections 2, 3 and 4 are therefore
  open to anyone on LinkedIn. Prefer connections where it costs nothing, but do not
  restrict to them.
- **"Work in" means currently employed there.** Section 2 is staff, **not** alumni and
  **not** students. A school's alumni list is the easy wrong answer here and does not
  count.
- **Seattle area** includes Bellevue, Redmond and Seattle metro. Amazon elsewhere does
  not qualify, and neither does "Amazon" as a past employer.
- **The school's name is spelled several ways.** Sinhgad Spring Dale Public School /
  Spring Dale School / Sinhgad Springdale, Pune. Treat them as one target; the ask's
  "sihgad" is a typo for Sinhgad.
- **"Famous"** needs a number to be checkable: a stated follower count, and the
  threshold is **≥ 500,000 followers**. Ten different people, not ten from one company.

## Assertion — how we know this passed

Deterministic, all must hold.

1. The deliverable file exists on disk under `runs/` and is valid markdown.
2. Each of the four sections holds **either** its full requested count, **or** fewer rows
   plus an explicit shortfall line naming the count reached and the reason. `18/20 —
   search stopped returning new Seattle-based results after N pages` passes. Padding to
   20 with people who do not qualify fails.
3. Every profile link matches `https://www.linkedin.com/in/` or `/pub/`. A search-results
   URL, a company URL, a post URL or a bare slug fails that row.
4. No duplicate profile slug **within** a section. A slug appearing in two different
   sections is allowed and must be flagged in the row.
5. Every row in section 1 states a current employer containing "Amazon" and a Seattle-area
   location, and asserts 1st-degree.
6. Every row in section 2 states the school as the *current* employer.
7. Every row in section 3 states a numeric follower count ≥ 500,000.
8. Every row in section 4 states a current hospitality title and employer.
9. **Every link was opened or rendered during the run.** A URL assembled from a person's
   name without the page being seen fails the whole run, even if the URL happens to
   resolve. Same for a plausible-looking name that was not read off a page.

A fabricated row fails the run outright. A short list, honestly short, does not.

## Constraints

- Identity: seed from `root.json`. Do not open the human's day-to-day browser profile.
- Browser: project-local Playwright MCP, `--isolated`.
- Each browser call is an LLM round trip, ~11s measured. Four lists of this size walked
  one profile at a time is a multi-hour run. Read the repo's own guidance on splitting
  browser work before choosing a route, and prefer reading many people off one rendered
  search page over opening one profile per person.
- LinkedIn rate-limits and serves an interstitial under heavy search. Back off rather
  than retrying hard, and record it if it happens.
- The deliverable and any transcript go in `runs/`, which is gitignored. Nothing from this
  run may land in a tracked file.
