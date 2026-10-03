# Fanning Out From One Browser Profile

*Three minutes. The question was: can five lanes run from one profile and merge their state back at the end? Someone has solved this — just not the way you'd hope. Read straight through; this is the whole answer.*

## 1. Nobody merges profiles

**What it is:** I went looking for fan-out-then-merge in both the open-source world and the anti-detect browser industry, which sells exactly this. **Two projects merge anything at all, and both only merge cookies.** Skyvern does a genuine three-way union keyed on `(domain, name, path)`, fingerprinting the seed archive by its S3 ETag, with a deliberate rule: *"Deletions are intentionally not propagated — a stale run must never remove a cookie a concurrent fresher login added."* browser-use merges `storage_state.json` read-modify-write, new-wins per cookie. Neither merges the profile *directory* — both treat those bytes as last-writer-wins.

**Why everyone else refuses:** they serialize instead. Kameleo returns `409 profile_locked` and states it flatly — *"Can two devices run one cloud profile? **No**, Lock enforces single active runner."* Dolphin{anty} downloads the data dir at launch and uploads it at close, with the manual warning *"don't launch these profiles on another computer"* until sync finishes. Browserbase just says *"Avoid having multiple sessions using the same Context at once. Sites may force a log out."*

**The one shipped answer to your actual question** is the opposite of merging: **make the base immutable and give up write-back.** Browserless (`changes stay local to that session and won't affect the original profile`), Hyperbrowser (`persistChanges: false` — *"Run parallel sessions with the same profile safely"*), and Steel all fan out from a read-only snapshot that nothing writes back to.

## 2. Why merging auth is worse than wrong

**What it is:** a session cookie is not a value. It is an **opaque capability** — a bearer token is, by RFC 6750, something *"any party in possession of can use in any way any other party in possession of it can."* There is no correct way to combine two of them, because a combined one was never issued by the server and authenticates nothing.

**Why that's the end of the argument:** it gets actively dangerous. Under refresh-token rotation (RFC 9700 §4.14), two lanes refreshing the same token looks exactly like a replay attack, and the authorization server *"SHOULD revoke all refresh tokens issued to that client."* **A bad merge of ordinary data gives you a wrong value; a bad merge of auth state kills both lanes and logs you out everywhere.** Chrome Sync, incidentally, doesn't even attempt it — its conflict resolver can only pick a side, and its own design doc says *"merging is not supported during conflict resolution."*

## 3. Keep the base lean — your numbers

Your actual profile, measured: **~810 MB, of which 97% is disposable cache** — 360 MB Code Cache, 291 MB HTTP cache, 114 MB Service Worker CacheStorage. **The state that actually logs you in is 31 MB**, and only **1.5 MB of that is cookies**; the other 30 MB is IndexedDB and localStorage, which is where modern auth (Firebase, Supabase, Auth0) really lives. Copying the profile whole takes 22.6 s; cache-stripped it's **1.32 s** — 17× better on time and disk. Five lanes: 4 GB and two minutes, or a few hundred MB and seven seconds.

**The practitioner's detail, and it will bite you:** `Local State` at the profile root is 6 KB of JSON and looks like junk. It holds `os_crypt.encrypted_key` — the AES-256-GCM key for every cookie, itself wrapped by Windows DPAPI. Drop it while pruning and Chromium silently generates a fresh key, fails to decrypt every `v10` cookie row, and **discards them without an error**. You get a profile that looks fine and is logged out of everything. Keep `Local State`, `Preferences`, `Network/`, `Local Storage/`, `IndexedDB/`; delete `Service Worker/` wholesale or not at all.

## 4. Four things worth knowing in one line each

**Microsoft already answered your question, in the playwright-mcp README:** *"A persistent profile can only be used by one browser instance at a time… To run several clients in parallel, start each additional client with `--isolated` or point it at a distinct `--user-data-dir`."* Those are the only two lane designs that exist.

AutoWeb takes the first design and keeps the second honest. `isolated = true` is the default and the only mode that is seeded from `root.json`. Set `isolated = false` and `autoweb lanes sync` stops passing `--storage-state`, because `--help` documents that flag as *"path to the storage state file for isolated sessions"*, and hands each lane its own directory under `.autoweb/profiles/lane-N` instead. **What a persistent-mode server does when handed one anyway is unverified.** It was never measured, so the flag is simply not passed rather than trusted to be ignored. The measured neighbouring fact is about the library rather than the flag: `launchPersistentContext({ storageState })` launches, reports success, and restores nothing. Either way a persistent lane is seeded by its own profile, so a fresh one starts logged out. That mode is for watching one lane across restarts. It is not the parallel path.

**State flows out of a profile but not back in.** `launchPersistentContext()` has no `storageState` option at all — you can *export* with `.storageState()` from a persistent context, but you can only *import* into an isolated one. That asymmetry is why `--storage-state` pairs with `--isolated`, and it quietly decides your architecture.

**A three-way merge needs a common ancestor.** Git finds a merge base; Unison keeps an archive of the last agreed state — without a recorded base you cannot distinguish "changed" from "always differed," and Unison degrades to marking everything as conflicting.

**You may not need to copy at all.** Playwright's `newContext()` gives N fully isolated cookie/storage jars inside *one* browser process, each seeded from its own `storageState` — lanes without a profile directory each.

---

*The reframe is this: **the merge problem is the wrong problem**, because browser auth state is a capability rather than a value, and the industry's answer is to never have two writers rather than to reconcile them. The design that falls out — an immutable lean base, lanes that read it, and at most one elected writer refreshing it per origin — is what both the vendors and the merge-capable projects converge on. Prune first; it buys more than merging ever will.*
