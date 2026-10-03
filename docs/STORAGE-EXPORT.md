# Which Export Path Keeps You Logged In

*Two minutes. Five ways of getting login state out of a browser, all tested on this
machine rather than reasoned about. Three of them lose your sessions silently.*

## The verdict

**Export with the Playwright library. Import however you like.**

Modern auth does not live in cookies. In the measured profile, cookies are 1.5 MB of a
31 MB identity set — the rest is IndexedDB and localStorage, where Firebase, Supabase
and Auth0 keep sessions. An export that drops IndexedDB produces a file that looks
healthy and is logged out.

One flag decides it, and it **defaults to false**:

```js
await context.storageState({ indexedDB: true })
```

## What was measured

A cookie, a localStorage key and an IndexedDB record were written to a real origin,
exported each way, then restored into a fresh browser and read back.

| path | IndexedDB | 
|---|---|
| `storageState()` — *what MCP's `browser_storage_state` calls* | **lost** |
| MCP per-key tools (`browser_localstorage_*`, `browser_cookie_*`) | **impossible** — no `browser_indexeddb*` tool exists |
| `launchPersistentContext({ storageState })` | **silently ignored** |
| `storageState({ indexedDB: true })` — library | kept |
| `browser_run_code_unsafe` calling the above | kept |
| `browser_set_storage_state` — MCP **import** | kept |

Export at 273 bytes without the flag, 476 with it, on identical state.

## The trap worth knowing

`launchPersistentContext({ storageState })` does not throw. It launches, reports
success, and discards the state — cookies were **not** restored in the test. The option
simply isn't on that method. A silent no-op is worse than an error, because nothing
tells you until a lane lands on a login page.

**State flows out of a persistent profile but never into one.** That asymmetry is why
the base is a JSON file rather than a Chrome folder.

## The good news, and a config fact

MCP's *import* restores IndexedDB correctly. So lanes can still be driven entirely
through MCP — only the one export call has to leave it. That is a much smaller
concession than it first appeared.

Also discovered while testing: this repo's `.mcp.json` passes **no flags**, so the
storage tools do not exist in our session at all. They need `--caps=storage`. What we
do have is `browser_run_code_unsafe`, a core tool, which exported 124,500 bytes
including IndexedDB and returned it inline.

That makes `run_code_unsafe` a genuine in-band option — but Playwright's own
description is *"executes arbitrary JavaScript in the Playwright server process and is
RCE-equivalent."* Fine for your own harness on your own machine; not something to
leave enabled in anything that takes untrusted input.

---

*Three things to carry: the flag defaults to false and silence means data loss; export
is the only step that must bypass MCP; and a persistent profile can be read from but
never written to, which is what makes `root.json` the right shape for the base.*
