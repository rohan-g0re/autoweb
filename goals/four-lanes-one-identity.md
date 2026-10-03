# Goal: four lanes, one identity, at the same time

## Target

Four pages behind a login, on two sites. The browser you are given is already signed in.
You will not be asked for a password and you must never type one.

## Resources

A shared signed-in session. Each lane starts from it independently and cannot see any
other lane's tabs, cookies or storage.

## The work

**There are four independent tasks below. Do them with four separate agents, working at
the same time, one task each.** None of them needs any other's output, so there is no
reason for any of them to wait.

Everything here is read-only. Do not send, delete, archive, follow, connect, post, or
open an unread message. Opening an unread email marks it read, and that is a change.
Read what is already on the screen.

### Task 1 - LinkedIn feed

Go to `https://www.linkedin.com/feed/`. Report the author names of the first three posts
in the feed, in order, and the name on the signed-in account as the page shows it.

### Task 2 - LinkedIn people search

Go to `https://www.linkedin.com/search/results/people/?keywords=recruiter`. Report the
first three people shown: name, and the headline text under the name.

### Task 3 - Gmail inbox

Go to `https://mail.google.com/`. From the **inbox list only**, report the sender and
subject of the top three threads. Do not click into any thread.

### Task 4 - Google account

Go to `https://myaccount.google.com/`. Report the email address of the signed-in account
and the display name shown on the page.

## Timestamps, and why they decide whether this worked

The point of this run is not the four answers. It is whether the four agents really ran
**at the same time** rather than taking turns, and the only way to know is from the
clock inside each browser.

Every agent records four timestamps by calling `browser_evaluate` with
`() => new Date().toISOString()`, which proves its own browser was alive and responding
at that instant. Taking the time from a shell instead does not prove that.

| Mark | When to take it |
|---|---|
| `T0_start` | first thing, before navigating anywhere |
| `T1_loaded` | immediately after the page has loaded |
| `T2_read` | after you have read the facts your task asks for |
| `T3_end` | last thing, before you report |

**Hold the browser open.** After `T1_loaded`, call `browser_wait_for` with `time: 20`
before taking `T2_read`. This is deliberate and it is the measurement: a task that
finishes in three seconds can look sequential purely because browsers take a few seconds
to start. Twenty seconds is long enough that genuine overlap is unambiguous.

Report all four timestamps verbatim, as ISO strings, in your reply. Do not round them, do
not reformat them, and do not substitute a time you got from anywhere else.

## Goal, and the assertion that proves it

Four things have to hold. Each is checkable, and a run that cannot show all four did not
succeed.

1. **Four agents, four browsers.** Each agent used only its own browser tool namespace.
   An agent that used a differently-named namespace than its siblings was in a shared
   browser.
2. **Each lane stayed on its own page.** Before reporting, each agent confirms with
   `browser_evaluate` that `location.href` still matches the URL it was given. If it
   shows another task's site, isolation failed and that is the headline result, not a
   footnote.
3. **Each lane was signed in.** A signed-out browser is redirected to a login or consent
   page. So each lane reports its final URL, and a URL containing `login`, `signin`,
   `authwall` or `checkpoint` is a failure for that lane. The content asked for is the
   corroboration: a name, a subject line and an email address are things no signed-out
   session can see.
4. **The four lanes overlapped in time.** Take the latest `T1_loaded` across all lanes
   and the earliest `T3_end`. If the latest start is before the earliest end, all four
   were alive together and the run was concurrent. If any lane's `T3_end` is before
   another lane's `T0_start`, those two ran in sequence and the run failed its main
   assertion.

## Reporting

Give one table of the four tasks' answers, one table of the sixteen timestamps, and then
your own verdict on each of the four assertions above, with the evidence for it.

If something blocked you, quote the exact wall you hit and say which task it belonged to.
A login wall is a fact about one URL, not about a site, so say which URL. A blocked task
reported honestly is a useful result; a plausible answer that you did not actually read
on the page is worse than nothing, because it cannot be told apart from a real one.
