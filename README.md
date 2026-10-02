# AutoWeb

**A stub for web automation. Not a framework.**

> Status: pre-alpha. Nothing works yet. This README describes what is being built
> and how it is sequenced — see [`northstar.md`](northstar.md) for the live task list.

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

Not yet. There is nothing to install.

## Roadmap

Strictly ordered. Each line gets finished and proven before the next one starts.
See [`northstar.md`](northstar.md).

1. Connect Playwright MCP, self-contained in this repo
2. Prove a browser opens and takes actions
3. Act from a URL + a goal
4. Browser profile persistence
5. Parallel profile spawns
6. Profile merge-back
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
