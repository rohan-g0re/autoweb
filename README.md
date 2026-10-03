# AutoWeb

**A stub for web automation. Not a framework.**

> Status: pre-alpha. There is an installable Python package and a working CLI, and
> northstar lines 1 to 4 are proven: a browser connects, acts, acts from a goal alone,
> and a login done by hand survives into a fresh browser seeded only from exported
> JSON. Line 5, parallel browser lanes, is built but not yet proven. See
> [`northstar.md`](northstar.md) for the live task list.

---

## The idea

Most browser-automation tooling makes a choice for you and then hides it. You get a
nice API, a clever DOM abstraction, a retry layer, a scheduler — and the moment your
site does something unusual, you are fighting the tool instead of the site.

AutoWeb does the opposite. It hands you the thinnest possible connection between an
agent and a real browser, in files you can read in one sitting, and expects you to
rewrite all of them.

There are many web automation harnesses. This one is yours.

## How it works

The engine is **Claude Code**. The browser is **Microsoft's `@playwright/mcp`**.
Between them: markdown. When the loop eventually moves out of Claude Code, it moves
into **Python**.

```
┌─────────────┐   MCP/stdio   ┌──────────────────┐        ┌─────────┐
│ Claude Code │ ────────────▶ │ @playwright/mcp  │ ─────▶ │ Chrome  │
└─────────────┘               └──────────────────┘        └─────────┘
       ▲
       │ reads
       ▼
  targets/ · resources/ · goals/ · skills/   ← all .md, all yours
```

No orchestration layer. No database.

**Language: Python** — final. Node is unavoidable (`@playwright/mcp` is an npm
package), so AutoWeb is polyglot by construction. The rule is that there is exactly
one seam: Python owns the loop, the config and every `.md`; TypeScript appears only
where the real Playwright API is needed, and is reached over MCP or a JSON CLI —
never imported.

## SIMULATION

The one real concept. A simulation is a sequence of runs against a target, where
**each run updates the skill documents, so the next run is better informed.**

```
  build ──▶ run ──▶ assert ──▶ revise skills ──▶ run ──▶ ...
              │                      │
         transcript              skills/*.md
                              (moves to take /
                               moves to avoid)
```

- **Build** — declare the target, the resources (data to fill, directions to
  navigate), and the goal.
- **Run** — one attempt. Produces a transcript.
- **Assert** — a run passes on a checkable condition declared in the goal spec:
  URL match, visible text, element state. Deterministic. No LLM judge.
- **Revise** — distil what worked and what dead-ended into `skills/*.md`. Prune
  what has gone stale. Skills shrink as often as they grow.

Every artifact is a markdown file. You can read the whole state of a simulation in
a text editor, and `git diff` tells you what the last run learned.

### Two blocks

A business problem splits into two document sets, each refined independently by
simulation:

1. **Resources → extraction → business logic.** How to read the pdf/excel/report
   you were handed, and what it implies. *"Company is in loss"* and *"company is in
   profit"* lead to different places.
2. **Actions.** What to do in the browser once the logic has decided. Launch the ad
   campaign, or file the dividend and research podcasts.

They are not a pipeline with a fixed seam. They are two blocks you improve separately
and compose per problem.

## Install

Python 3.10 or newer and [uv](https://docs.astral.sh/uv/). Node 20 or newer too, since
`@playwright/mcp` is an npm package and Claude Code is a Node process. Clone this repo,
then:

```sh
uv sync
uv run playwright install chromium
uv run autoweb --version
```

`uv sync` is enough for the config and lane commands. The `playwright install` step is
only needed by `autoweb state`, which is the one part that drives a browser itself.

## The CLI

Nine commands. Every one of them prints its own help.

```sh
autoweb config show              # effective config, defaults included
autoweb config check             # validate autoweb.toml, non-zero exit if malformed

autoweb state export URL         # headed browser, you log in, the session is saved
autoweb state export --from-profile DIR --visit URL
                                 # read a profile that is already logged in, no human
autoweb state inspect [PATH]     # origins, cookies, IndexedDB, bytes
autoweb state verify URL [PATH]  # seed a fresh browser from the JSON, report what it sees

autoweb lanes sync               # write .claude/agents/lane-N.md, one per lane
autoweb lanes list               # show the lane files that exist

autoweb trace TRACE_JSON         # did the lanes really overlap? exits non-zero if not
autoweb merge LANE_JSON...       # fold lane state back into root.json, three-way
```

Three of those are assertions rather than tools, and exit non-zero when what they check
does not hold: `config check`, `state verify`, and `trace`. `state export` joins them when
it captures nothing at all, because an empty state file that parses is the failure most
likely to be mistaken for a success.

Settings live in [`autoweb.toml`](autoweb.toml), every key has a working default, and
each one is documented where it is defined in `autoweb/config.py` rather than in a
reference that rots. `-C DIR` runs as if started elsewhere; config is searched upward
from there.

`state verify` is an assertion rather than a tool. It takes `--expect-url` and
`--expect-text` and exits non-zero when they do not hold, so a session that did not
survive the round trip is a failing command instead of a paragraph to read.

`lanes sync` writes one agent file per lane, each declaring its own inline MCP server.
That is what gives a lane its own browser rather than a share of the session's one.
The files are generated and gitignored because they embed an absolute path to your
`root.json`; the generator is what is committed.

The loop is still Claude Code. Nothing here drives a browser on your behalf.

## Roadmap

Strictly ordered. Each line gets finished and proven before the next one starts.
See [`northstar.md`](northstar.md).

1. Connect Playwright MCP, self-contained in this repo. Proven.
2. Prove a browser opens and takes actions. Proven.
3. Act from a URL + a goal. Proven.
4. Browser profile persistence. Proven, as `autoweb state`.
5. Parallel profile spawns. Built as `autoweb lanes`, not yet proven.
6. Profile merge-back.
7. `SIMULATION`: build / run / assert / revise

## Non-goals

Deliberately absent, and each one waits its turn: model routing, parallel
simulations, a GUI, a scheduler, a results server, retry and healing layers.

## Prior art

AutoWeb owes its shape to projects worth reading:

- **[Pi](https://github.com/earendil-works/pi)** — *"There are many agent harnesses
  but this one is yours."* The ownership model.
- **[browser-use/browser-harness](https://github.com/browser-use/browser-harness)** —
  *"Don't wrap the LLM. Don't wrap its tools either."* The restraint.
- **[OpenClaw](https://github.com/openclaw/openclaw)** — a versioned skill lifecycle
  (propose → evaluate → apply / reject / quarantine / stale) that AutoWeb reproduces
  in markdown instead of 140 SQL tables.
- **[Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai)** and
  **[Petri](https://github.com/meridianlabs-ai/inspect_petri)** — the discipline of a
  small, documented stub rather than a scaffold generator.

## License

[Apache License 2.0](LICENSE). See [`NOTICE`](NOTICE).
