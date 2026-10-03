# Goal: daily digest

Produce one markdown document containing three things. No starting URL is given —
work out where to go.

## Outputs

1. **Index prices.** Current level of the major stock indexes in **India** and the
   **US**. For each: name, level, change on the day. State the timestamp and whether
   the market was open or closed when read.

2. **Today's Google Doodle.** The doodle image for today, saved as a file, plus what
   it commemorates.

3. **A Medium article, formatted for LinkedIn.** Pick a Medium article on *JEV*.
   Take it to [Typefully](https://typefully.com), format it as a LinkedIn post with
   bold text, and capture Typefully's LinkedIn preview as a screenshot.

## Assertion — how we know this passed

Deterministic checks, all must hold:

- The document names **at least 2 Indian indexes** and **at least 2 US indexes**, each
  with a numeric level and a numeric daily change.
- A doodle image file exists on disk, is a valid image, and is referenced by the doc.
- A Typefully LinkedIn-preview screenshot file exists on disk and is referenced.
- Every number and quote is attributed to the URL it came from.

A sub-goal that could not be completed is **reported as blocked, with the exact
reason**. A fabricated value fails the whole run, even if the other two parts pass.

## Constraints

- Model: Sonnet.
- Browser: project-local Playwright MCP (`.mcp.json`, no user profile).
- No logged-in session exists. Anything behind a login wall is expected to block.
