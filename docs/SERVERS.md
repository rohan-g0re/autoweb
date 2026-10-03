# What Kind of Server Runs a Browser

*This is the whole answer to "can I just rent a Linux box and run this?" — about ten minutes, read straight through. It assumes you can drive a terminal and have met Docker, and it is scoped to one decision: what to provision when AutoWeb stops living on your laptop. It is not a sysadmin course, and you are not trying to become one here.*

---

## 1. The short answer, so you can stop worrying

**Yes. A plain Linux virtual machine is enough.** An AWS `t3.medium`, an Azure `B2s`, a €5 Hetzner box, a Mac mini in a cupboard — any of them. You do not need a GPU instance, a "desktop" SKU, a machine with a monitor attached, or anything with nested virtualization. There is no special instance type for this. If you can SSH into it and it runs Ubuntu, it runs a browser.

That sentence is worth more than it looks, because the intuition pulling against it is strong and wrong. A browser *feels* like a thing that needs a screen. It has windows. It draws pixels. Surely something has to display them? No — and understanding exactly why is the most useful thing in this document, because every other decision falls out of it.

**If you get one section solid, make it the next one.**

---

## 2. Headless is the whole mechanism

**What it is:** **headless** means Chrome runs with its rendering pipeline fully intact but its output never reaching a screen. It parses HTML, builds the DOM, computes CSS layout, runs JavaScript, rasterizes the page — all of it — into a block of memory instead of into a window on a display. `--headless` is not a reduced mode or a stripped-down parser. It is the same browser with the last six inches of the journey removed.

**Why that matters so much:** the display is downstream of everything you care about. By the time a page could be shown to a human, the browser already knows the full layout — where every element sits, what text it contains, what is clickable, what is hidden. Your agent reads *that*, not the picture. When Playwright hands your model a `browser_snapshot`, it is serializing the **accessibility tree** — the same structured description a screen reader consumes — into text. Nothing was ever looked at.

This is the mental model to carry out of the room: **the picture is a side effect, not the point.** A screenshot is a courtesy extended to you, the human debugging at 1am. The agent never needed it. You saw this yourself when AutoWeb's smoke test clicked `ref=e13` — that `ref` is a node in the accessibility tree, not a coordinate on a screen. There was nothing to aim at.

**Where it shows up:** both of the agent platforms you pointed me at settled here independently. OpenDots hard-codes `COMPUTER_BROWSER_MODE: headless` with no toggle in its compose file. OpenMausBot ships `AGENT_BROWSER_HEADLESS: "1"` and documents its hosted tier flatly as *"a headless Linux server."* Neither ships a display server in its agent image. Two teams solving the same problem, both concluding the screen is optional — which it is.

**The practitioner's detail:** open any Playwright Dockerfile and you'll see `libx11-6`, `libgtk-3-0`, `libgbm1` in the `apt-get install` list, and conclude the thing needs X11 after all. It doesn't. Those are **link-time dependencies** — the Chrome binary was compiled against them and refuses to load without the shared objects present, whether or not it ever calls into them. The library existing on disk is not the same as a display existing. OpenMausBot's Dockerfile installs that entire list and contains no Xvfb, no VNC, and no desktop. If you ever see a `libgtk` error on a server, you have a missing-package problem, not a graphics problem.

---

## 3. What the box actually needs

**What it is:** a short, boring list. **2 vCPU and 4 GB of RAM**, under a gigabyte of disk for the browser, and a **glibc-based Linux distribution** — officially Debian 12 or 13, or Ubuntu 22.04, 24.04 or 26.04, on x86-64 or arm64.

**Why those numbers:** be clear about what the RAM is for, because the two figures you'll see quoted measure different things. *Chrome alone* idles around 50–150 MB and sits at 300–500 MB with a real page open; 512 MB will OOM, 1 GB is the floor, 2 GB is comfortable. The 4 GB figure is for the browser **plus your harness** — OpenMausBot's guide says *"2 CPUs and 4 GB of RAM is plenty"* for its whole stack, and OpenDots caps each agent container at 2 GiB. Chrome is bursty rather than steady: a page load spikes CPU for a second or two while JavaScript runs and layout resolves, then idles while your model thinks for eleven seconds. Provision for the spike and the resident footprint, not for throughput. One vCPU genuinely suffices if you run serially — Playwright's own CI guidance is to set workers to 1.

Disk is smaller than people expect. Playwright's own measurement is 281 MB for Chromium, 648 MB for all three engines; the headless shell alone is about 100 MB. `--only-shell` skips the ~400 MB headed build entirely if you know you'll never go headed.

**Where it shows up:** the moment you want *concurrency*, the per-browser figure is what you multiply. Four parallel browsers is a 2 GB problem becoming an 8 GB problem, and that arithmetic decides whether northstar lines 5 and 6 cost you one machine or a fleet. Budget per browser, not per server.

**The practitioner's detail that will save you an afternoon: Alpine will not work.** Alpine uses **musl** instead of glibc as its C library, and Playwright states plainly that its browser builds target glibc and musl-based distributions are not supported. No amount of `apk add` fixes it — and the failure is ugly, because it happens in the dynamic loader: `ENOENT` on `ld-linux-x86-64.so.2`, before your code runs, with nothing to catch. Alpine is the reflexive choice for small images, and this is the one place that reflex costs you a working system rather than a few megabytes. Use `node:24-bookworm-slim` or Microsoft's `mcr.microsoft.com/playwright` image.

Getting the system libraries is one command, and you should run it rather than hand-curating a package list:

```
npx playwright install --with-deps chromium
```

The `--with-deps` flag shells out to `apt-get` and installs the whole dependency set for you. On a bare VM this is essentially the entire setup.

---

## 4. The Docker tax

**This is the second-most-important section, and the one with the real trap in it.**

**What it is:** containerizing a browser introduces two failure modes that a bare VM simply does not have. They are unrelated to each other, both are invisible until they bite, and both have a fix you must apply deliberately.

**The first is shared memory.** Chrome uses `/dev/shm` — a RAM-backed filesystem — to pass rendered frames between its processes. Docker mounts `/dev/shm` at **64 MB by default**, which is nowhere near enough. Chrome does not fail cleanly when it runs out; tabs die, renderers crash, and your logs fill with "no space left on device" errors that read like a flaky site rather than an exhausted mount. There are three fixes and it's worth knowing which is which. Playwright's own prescription is `--ipc=host` — *"Without it, Chromium can run out of memory and crash"* — paired with `--init` to reap zombie processes. Failing that, raise the mount directly: OpenDots allocates **1 GiB per agent**, with the comment *"Chromium's sandbox wants shared memory and will crash on the 64MB default,"* and `--shm-size=2g` is common. The last resort is `--disable-dev-shm-usage`, which redirects Chrome to `/tmp` — usually disk-backed, so slower, and it leaks file-backed memory in long-running containers. Reach for it only where the platform forbids the other two.

**The second is the sandbox.** Chrome isolates renderer processes using **user namespaces**, a kernel feature. Docker's default seccomp profile denies `clone(CLONE_NEWUSER)`, `unshare` and `setns`, so a container that does nothing special gets `No usable sandbox!` and the browser refuses to start. Separately, Chrome hard-exits as root with `Running as root without --no-sandbox is not supported` — and containers run as root by default.

The reflexive fix is `--no-sandbox`, and it is worse than it looks: it disables **both** layers, the namespace sandbox *and* the seccomp-BPF baseline. A renderer compromise from a hostile page then runs with browser-process privileges inside your container. Puppeteer's docs call running without a sandbox *"strongly discouraged."*

**Playwright's official answer is neither** — a non-root user plus a permissive profile that re-allows exactly those three syscalls, shipped in their repo at `utils/docker/seccomp_profile.json`:

```
docker run --ipc=host --user pwuser \
  --security-opt seccomp=seccomp_profile.json \
  mcr.microsoft.com/playwright:v1.63.0-noble
```

Your two reference repos each took a different door. **OpenDots**, inside Docker, accepts `--no-sandbox` because it must — *"OFF BY DEFAULT, AND THAT IS NOT A PREFERENCE"* — and compensates with `cap_drop: ALL`, `no-new-privileges`, a non-root user and a 512-process limit. **OpenMausBot**, on bare Ubuntu, refuses it — *"Do not add `--no-sandbox` or disable `kernel.apparmor_restrict_unprivileged_userns` globally"* — and installs a one-permission AppArmor profile instead.

**All three are correct for their host**, and that is the real lesson. On Cloud Run, Fargate and Lambda you cannot pass `--security-opt` at all, so `--no-sandbox` is forced and the platform's own isolation — gVisor, Firecracker — becomes your only boundary. On a VM you control the kernel and keep the genuine one.

**Where this lands for you:** it is an argument for the bare VM. **Every layer of isolation you add buys you a footgun** — the container gets you shm and seccomp; serverless gets you those plus a read-only filesystem and a timeout. You are not currently running untrusted code that needs container isolation; you are running your own automation. Start on the VM, and containerize later when you have a reason you can name.

---

## 5. Headed mode, and when you'd actually want it

**What it is:** **headed** means a real browser window exists. On a server there is no desktop for it to exist *on*, so you fake one — **Xvfb**, the X virtual framebuffer, is an X11 server that renders to memory rather than to hardware. You run `xvfb-run your-command` and Chrome believes it has a screen.

**Why you'd bother:** two reasons, and only two. Some sites fingerprint headless browsers and behave differently — though modern headless is much harder to detect than it used to be. And sometimes you genuinely want to *watch*, streaming the session to a human for debugging or takeover.

**Where it shows up:** note how both reference projects handle the watching case, because it's instructive. Neither streams a desktop. OpenDots sends **CDP screencast frames** — `Page.startScreencast` producing JPEGs over a WebSocket — and OpenMausBot does the same at 15fps. A headless browser can still produce a live video feed, because rendering was never the thing that required a screen. Xvfb appears in OpenDots only behind an explicit opt-in: `useVirtualDisplay: platform === "linux" && mode === "headed"`.

So: you probably don't need headed mode, and if you want to watch, you don't need it either.

---

## 6. Where it genuinely does not work

Short section, because the list is short, and none of these are "servers" in the sense you asked about.

**AWS Lambda** runs Chrome only via `@sparticuz/chromium` — a specially-compiled build Brotli-compressed to 33 MiB to fit the 250 MB package cap, unpacked into `/tmp` at cold start. `/dev/shm` there is **64 MB and not resizable**, so `--disable-dev-shm-usage`, `--no-sandbox` and `--single-process` are all forced. The existence of that package is the clearest signal that the normal path doesn't apply.

**Fargate** works but cannot take `sharedMemorySize` — AWS documents that parameter as unsupported on Fargate, and there's no `--ipc=host` either, so `--disable-dev-shm-usage` is mandatory. **Cloud Run** works best as a Job on gen2, but its filesystem is in-memory, meaning every byte of browser profile counts against your RAM, and `/dev/shm` can't be resized because in-memory mounts are disallowed under `/dev`.

**Edge runtimes** — Cloudflare Workers, Vercel Edge, Netlify Edge — cannot run it at all. They are V8 or Deno isolates with no filesystem and no `child_process`. There is no process to spawn Chrome as.

**Alpine**, as covered: musl, not glibc, not supported.

**Windows and macOS servers** work and almost nobody uses them. Windows costs licensing per hour and carries the profile-locking behaviour in `CONSTRAINTS.md` — a second browser on the same profile fails *silently*, exit 0 or exit 21, nothing to catch. macOS is effectively non-viable: Apple's licence permits virtualization only on Apple hardware, so EC2 Mac is bare-metal Dedicated Hosts with a **24-hour minimum allocation**. It exists for Safari and iOS builds, not for headless Chrome.

---

## 7. The short tail

**Concurrency and profiles.** One browser per persistent profile directory, always. Two Chrome processes on one profile corrupt each other's SQLite and LevelDB stores — and `chromium-headless-shell`, Playwright's default headless binary, has no lock at all, so both start and neither complains. Parallelism means `--isolated` or separate profile directories, never a shared one. This is northstar lines 5 and 6, and `CONSTRAINTS.md` has the full account.

**The official image.** `mcr.microsoft.com/playwright:v1.63.0-noble` ships Ubuntu, Node, all three browsers, every `install-deps` package, Xvfb, fonts and a `pwuser` account. Tags track npm releases 1:1 and mismatch is not cosmetic — Playwright's docs say that if the image version doesn't match your project's, it *"will be unable to locate browser executables."* Pin both to the same number.

**Root.** Chrome refuses to run its sandbox as root. Create an unprivileged user — both reference repos do, and it costs one line.

**Storage.** Browser state lives in the profile directory, so on a container it belongs on a mounted volume or it dies with the container. OpenDots mounts a named volume per agent at `/profiles` for exactly this.

---

*Three threads run through all of this. The first is that **the picture is a side effect** — rendering happens in memory, "seeing" means reading the accessibility tree, and once you believe that, no display, no GPU and no special instance type follow naturally. The second is that **every layer of isolation costs a footgun**: the bare VM is the simplest correct answer, the container adds shared-memory and sandbox problems, and serverless adds enough that people ship custom Chrome builds to escape it. The third is that **you budget per browser, not per server** — 4 GB and one profile directory each, which is what turns parallel runs into an arithmetic problem rather than a configuration one. Provision an Ubuntu VM with 2 vCPU and 4 GB, run `npx playwright install --with-deps chromium`, and you are done.*
