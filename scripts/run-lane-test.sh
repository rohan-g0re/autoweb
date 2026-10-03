#!/usr/bin/env bash
# Run the four-lane test end to end, non-interactively, and judge it.
#
# One command instead of nine manual steps, for two reasons. It can be driven over SSH
# without a human at the keyboard, and it takes the timestamp parsing out of human hands:
# the one analysis mistake this project has actually made was grouping a JSON stream by
# event instead of by `message.id`, which invented a dispatch defect that did not exist.
# That grouping is encoded here once, correctly.
#
# The test itself: hand the goal document to a brand new `claude -p` process that knows
# nothing about AutoWeb, and see whether it works out that it needs several lanes at once.
# A nested `claude -p` is a fresh Claude Code instance, so it registers the generated lane
# agents at startup - which is why this needs no restart of any existing session.
#
# Usage:  scripts/run-lane-test.sh [goal-document]
# Needs:  a root.json that works, lane agent files from `autoweb lanes sync`, and the
#         `claude` CLI on PATH. Run `autoweb state verify <a logged-in url>` first.

set -uo pipefail

GOAL="${1:-goals/four-lanes-one-identity.md}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

OUT="${AUTOWEB_RUN_DIR:-${TMPDIR:-/tmp}/autoweb-lane-test-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"

say() { printf '\n== %s\n' "$*"; }

say "repo $REPO at $(git rev-parse --short HEAD 2>/dev/null || echo '?')"
say "output $OUT"

# A venv if there is one, so this works whether the project was set up with uv or pip.
if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  . .venv/bin/activate
fi
AUTOWEB=(autoweb)
command -v autoweb >/dev/null 2>&1 || AUTOWEB=(python -m autoweb.cli)

command -v claude >/dev/null 2>&1 || { echo "FAIL: no 'claude' on PATH" >&2; exit 1; }
[ -f "$GOAL" ] || { echo "FAIL: no goal document at $GOAL" >&2; exit 1; }

# --- preflight ---------------------------------------------------------------
# A lane whose root.json is missing fails every browser call with ENOENT, which looks
# like the lane being broken rather than the identity being absent. Catch it here.
say "preflight"
"${AUTOWEB[@]}" lanes list || exit 1
if ! "${AUTOWEB[@]}" state inspect >"$OUT/inspect.txt" 2>&1; then
  echo "FAIL: state inspect could not read root.json. Export one first." >&2
  cat "$OUT/inspect.txt" >&2
  exit 1
fi
sed -n '1,4p' "$OUT/inspect.txt"

# --- count browsers from outside --------------------------------------------
# No agent can measure its own concurrency: from inside an orchestrator a staggered
# dispatch and a parallel one look identical. So the browsers get counted by a process
# that is not taking part.
( while :; do
    printf '%s %s\n' "$(date -u +%H:%M:%S)" \
      "$(ps -eo args 2>/dev/null | grep -o -- '--user-data-dir=[^ ]*' | sort -u | wc -l)"
    sleep 2
  done ) >"$OUT/browsers.log" 2>/dev/null &
POLLER=$!
trap 'kill "$POLLER" 2>/dev/null' EXIT

# --- the run -----------------------------------------------------------------
say "dispatching a fresh claude -p with the goal document as its whole prompt"
date -u +%Y-%m-%dT%H:%M:%SZ >"$OUT/started"
env -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT \
  claude -p "$(cat "$GOAL")" \
    --model opus \
    --dangerously-skip-permissions \
    --output-format stream-json --verbose --max-turns 80 \
    >"$OUT/stream.jsonl" 2>"$OUT/stderr.txt"
echo "claude exit=$?"
date -u +%Y-%m-%dT%H:%M:%SZ >"$OUT/finished"

kill "$POLLER" 2>/dev/null
PEAK=$(awk '{ if ($2 > max) max = $2 } END { print max + 0 }' "$OUT/browsers.log")
say "peak concurrent browser profiles seen from outside: $PEAK"

# --- extract the marks -------------------------------------------------------
say "reading the stream"
python - "$OUT" <<'PY'
import json, pathlib, re, sys
from collections import defaultdict

out = pathlib.Path(sys.argv[1])
rows = []
for line in (out / "stream.jsonl").open(encoding="utf-8", errors="replace"):
    line = line.strip()
    if line:
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass

# Dispatch shape, grouped by message.id and NOT by stream event. The stream emits one
# event per content block, so four Agent calls in one assistant message arrive as four
# events; grouping by event turns a correct parallel dispatch into a fabricated defect.
dispatch = defaultdict(list)
for r in rows:
    msg = r.get("message") or {}
    if r.get("parent_tool_use_id"):
        continue                                  # a subagent's own events, not dispatch
    for c in msg.get("content") or []:
        if isinstance(c, dict) and c.get("type") == "tool_use" and \
                c.get("name") in ("Agent", "Task"):
            dispatch[msg.get("id")].append((c.get("input") or {}).get("subagent_type"))

print(f"assistant messages carrying Agent calls: {len(dispatch)}")
for mid, lanes in dispatch.items():
    print(f"  {mid} dispatched {len(lanes)}: {lanes}")

# Which tool_use id belongs to which lane, so marks can be attributed.
lane_of = {}
for r in rows:
    msg = r.get("message") or {}
    for c in msg.get("content") or []:
        if isinstance(c, dict) and c.get("type") == "tool_use" and \
                c.get("name") in ("Agent", "Task"):
            lane_of[c.get("id")] = (c.get("input") or {}).get("subagent_type") or c["id"]

# Marks are ISO instants the lane read out of its own browser. Collected per lane in the
# order they appear, which is the order the goal document asks for them to be taken.
ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})")
marks = defaultdict(list)
for r in rows:
    parent = r.get("parent_tool_use_id")
    if not parent:
        continue
    lane = lane_of.get(parent, parent)
    blob = json.dumps(r.get("message") or r)
    for found in ISO.findall(blob):
        if found not in marks[lane]:
            marks[lane].append(found)

names = ("T0_start", "T1_loaded", "T2_read", "T3_end")
trace, short = {}, []
for lane, found in marks.items():
    if len(found) >= 4:
        trace[str(lane)] = dict(zip(names, found[:4]))
    else:
        short.append((lane, len(found)))

(out / "trace.json").write_text(json.dumps(trace, indent=2), encoding="utf-8")
print(f"\nlanes with four usable marks: {len(trace)} -> {out / 'trace.json'}")
for lane, n in short:
    print(f"  {lane}: only {n} timestamps found, excluded")
if len(trace) < 2:
    print("\nNot enough lanes reported marks to judge overlap. Read the final report in "
          "stream.jsonl by hand; the lanes may have been asked for the timestamps and "
          "not given them.")
PY

# --- the verdicts ------------------------------------------------------------
if [ -s "$OUT/trace.json" ] && [ "$(python -c "import json,sys;print(len(json.load(open(sys.argv[1]))))" "$OUT/trace.json")" -ge 2 ]; then
  say "trace verdict (exit code is the answer, not anyone's opinion)"
  "${AUTOWEB[@]}" trace "$OUT/trace.json"
  echo "trace exit=$?"
else
  say "trace skipped: fewer than two lanes reported four marks"
fi

say "merge dry run"
# Lanes were asked to save their own state as their last action. Harvest whatever landed.
mapfile -t LANE_FILES < <(find . -maxdepth 2 -name 'lane-*.json' -newer "$OUT/started" \
  2>/dev/null | sort)
if [ "${#LANE_FILES[@]}" -gt 0 ]; then
  printf 'lane state files: %s\n' "${LANE_FILES[*]}"
  "${AUTOWEB[@]}" merge "${LANE_FILES[@]}" --dry-run
  echo "merge exit=$?"
else
  echo "no lane-*.json was written, so the merge has no real inputs from this run."
  echo "That is itself a finding: it means lanes cannot hand anything back yet."
fi

say "final report from the run"
python - "$OUT" <<'PY'
import json, pathlib, sys
out = pathlib.Path(sys.argv[1])
rows = [json.loads(l) for l in (out / "stream.jsonl").open(encoding="utf-8",
        errors="replace") if l.strip()]
res = [r for r in rows if r.get("type") == "result"]
sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
print(res[-1].get("result", "") if res else "no result block in the stream")
PY

say "artifacts in $OUT"
ls -la "$OUT"
