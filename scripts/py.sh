#!/bin/sh
# py.sh - find a Python 3.10 or newer, then run a bundled script with it.
#
#   bash py.sh /path/to/script.py [args...]
#
# 3.10, not 3.11: that is the `autoweb` package's own floor (pyproject.toml
# requires-python = ">=3.10"), and a launcher that demands more than the package
# does would refuse to run on a machine the package supports.
#
# Hooks fire on every session start, so this has to be cheap, and it has to work
# on machines where the obvious lookup fails:
#
#   * macOS has no `python`, only `python3`.
#   * Windows ships a `python3` stub that advertises the Store and exits 9009.
#   * Git Bash under Claude Code can inherit a WINDOWS-format PATH (semicolons,
#     backslashes, drive letters). `command -v python` then finds nothing at all
#     even though Python is installed. Absolute candidates are the only reliable
#     answer, which is why most of this file is a list of places interpreters
#     actually live.
#   * An interpreter's path can contain SPACES, as a per-user install under
#     C:/Users/John Smith/AppData/Local/Programs/Python/Python312/ does. So the
#     command is kept in two parts: $AW_PY is one word and is always quoted,
#     $AW_PYARG holds extra arguments (`-3` for the Windows launcher, the whole
#     `run --no-project ...` line for uv) and is deliberately left unquoted.
#   * /usr/bin is sometimes missing from that same PATH, so this script uses
#     shell builtins only. No mkdir, no tr, no sed. The cache file is written by
#     the interpreter that just passed the probe.
#
# Search order, first candidate that reports version >= 3.10 wins:
#
#   1. $AW_PYTHON                                     explicit override
#   2. ${AW_HOME:-$HOME}/.autoweb-plugin/python_path   what won last time
#   3. PATH names: python, py -3, python3 on Windows; python3, python elsewhere
#   4. uv:  ~/.local/bin/uv[.exe], then uv on PATH
#           -> uv run --no-project --python >=3.10 python
#   5. absolute interpreters: /c/Windows/py.exe -3, the Python installer's
#      per-user directory, uv's downloaded builds, /usr/bin, /usr/local/bin,
#      /opt/homebrew/bin
#
# The winner is cached as TWO LINES: the interpreter on the first, its extra
# arguments (empty line when there are none) on the second. Two lines, rather
# than one string, is what lets a path with spaces survive the round trip. A
# one-line cache written by an older version still reads correctly - the second
# `read` just leaves the arguments empty - so a bare `python` or an absolute path
# is used as it is, and only a one-line cache that carried arguments (or an
# interpreter that has since moved) fails the probe and is re-probed.
#
# When nothing works this prints ONE line to stderr and exits 0: a hook must
# never block a session, whatever is wrong with the machine. Same for a script
# that is not there.
#
# POSIX sh. LF line endings. Never relies on an exec bit or a shebang: it is
# always invoked as `bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" <script> ...`,
# and the first line above is a courtesy to anyone who runs it by hand.

AW_STATE="${AW_HOME:-$HOME}/.autoweb-plugin"
AW_CACHE="$AW_STATE/python_path"
AW_PROBE='import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'

if [ "$#" -eq 0 ]; then
    printf '%s\n' 'autoweb: usage: bash py.sh <script.py> [args...]' >&2
    exit 1
fi

AW_SCRIPT="$1"
shift

# After `/plugin update` an already-open session still points at the PREVIOUS
# ${CLAUDE_PLUGIN_ROOT} (the version is a path segment), whose scripts/ no longer
# exists. Python exits 2 on a missing file, and a non-zero hook is a hook that
# can interfere with the session, so the launcher reports the real problem and
# exits 0. Checked before the interpreter probe: there is nothing to run either
# way.
if [ ! -f "$AW_SCRIPT" ]; then
    printf 'autoweb: %s missing (plugin updated?); restart the session\n' "$AW_SCRIPT" >&2
    exit 0
fi

# Accept a candidate only when it really is Python 3.10+. Every byte of output is
# discarded: the Windows Store stub prints an advert, and a hook's stdout is
# injected into the session.
aw_try() {
    [ -n "${1:-}" ] || return 1
    "$@" -c "$AW_PROBE" >/dev/null 2>&1
}

# The same probe for a candidate that is already split the way it will be run:
# $1 is the interpreter (quoted, so spaces in it are safe), $2 is the extra
# arguments as one string (unquoted, so it word-splits - that is the point).
aw_try_pair() {
    [ -n "${1:-}" ] || return 1
    # shellcheck disable=SC2086
    "$1" ${2:-} -c "$AW_PROBE" >/dev/null 2>&1
}

# Split "py -3" into AW_SPLIT_CMD=py and AW_SPLIT_ARG=-3, for the candidates that
# carry their own arguments. No arguments leaves AW_SPLIT_ARG empty.
aw_split() {
    AW_SPLIT_CMD="${1%% *}"
    AW_SPLIT_ARG="${1#* }"
    if [ "$AW_SPLIT_ARG" = "$1" ]; then
        AW_SPLIT_ARG=""
    fi
}

# The interpreter that just passed the probe writes its own cache entry: mkdir
# may not be on this PATH, but Python is, by definition, right here.
aw_cache_write() {
    # shellcheck disable=SC2086
    "$AW_PY" $AW_PYARG -c 'import os, sys
path, py, arg = sys.argv[1], sys.argv[2], sys.argv[3]
parent = os.path.dirname(path)
if parent:
    os.makedirs(parent, exist_ok=True)
with open(path, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(py + "\n" + arg + "\n")
' "$AW_CACHE" "$AW_PY" "$AW_PYARG" >/dev/null 2>&1 || true
}

aw_is_windows() {
    case "${OSTYPE:-}" in
        msys* | cygwin* | win32*) return 0 ;;
    esac
    for aw_u in /usr/bin/uname /bin/uname uname; do
        case "$("$aw_u" -s 2>/dev/null)" in
            MINGW* | MSYS* | CYGWIN*) return 0 ;;
            ?*) return 1 ;;
        esac
    done
    return 1
}

# $LOCALAPPDATA is a Windows path; cygpath converts it, but cygpath is not always
# present. $HOME/AppData/Local is the same directory in practice.
aw_localappdata() {
    if [ -n "${LOCALAPPDATA:-}" ] && command -v cygpath >/dev/null 2>&1; then
        aw_conv="$(cygpath -u "$LOCALAPPDATA" 2>/dev/null)"
        if [ -n "$aw_conv" ]; then
            printf '%s\n' "$aw_conv"
            return 0
        fi
    fi
    printf '%s\n' "$HOME/AppData/Local"
}

AW_PY=""
AW_PYARG=""
AW_FROM_CACHE=""

# 1. explicit override. A bare path wins first, so an override that contains
#    spaces works; only then is it re-read as "command arguments" for the
#    AW_PYTHON="py -3" shape. An override that fails the probe - a 3.9, or the
#    Store stub - is SKIPPED, not fatal: the search carries on below.
if [ -n "${AW_PYTHON:-}" ]; then
    if aw_try_pair "$AW_PYTHON" ""; then
        AW_PY="$AW_PYTHON"
    else
        aw_split "$AW_PYTHON"
        if [ -n "$AW_SPLIT_ARG" ] && aw_try_pair "$AW_SPLIT_CMD" "$AW_SPLIT_ARG"; then
            AW_PY="$AW_SPLIT_CMD"
            AW_PYARG="$AW_SPLIT_ARG"
        fi
    fi
fi

# 2. what won last time. Two reads, so an interpreter path with spaces comes back
#    as one word. `read` returns non-zero at EOF but still assigns, so its status
#    is deliberately ignored; a one-line cache leaves AW_C_ARG empty.
if [ -z "$AW_PY" ] && [ -f "$AW_CACHE" ]; then
    AW_C_PY=""
    AW_C_ARG=""
    { read -r AW_C_PY; read -r AW_C_ARG; } < "$AW_CACHE" 2>/dev/null || true
    if aw_try_pair "$AW_C_PY" "$AW_C_ARG"; then
        AW_PY="$AW_C_PY"
        AW_PYARG="$AW_C_ARG"
        AW_FROM_CACHE=1
    fi
fi

# 3. names on PATH. Windows first tries `python`: there `python3` is usually the
#    Store stub, and `py -3` costs a launcher hop.
if [ -z "$AW_PY" ]; then
    if aw_is_windows; then
        AW_NAMES="python|py -3|python3"
    else
        AW_NAMES="python3|python"
    fi
    while [ -n "$AW_NAMES" ]; do
        AW_ONE="${AW_NAMES%%|*}"
        case "$AW_NAMES" in
            *\|*) AW_NAMES="${AW_NAMES#*|}" ;;
            *) AW_NAMES="" ;;
        esac
        aw_split "$AW_ONE"
        if aw_try_pair "$AW_SPLIT_CMD" "$AW_SPLIT_ARG"; then
            # Cache the absolute path, not the bare name: a later hook whose PATH
            # is empty or Windows-format still finds the same interpreter instead
            # of falling through to uv on every turn.
            AW_ABS="$(command -v "$AW_SPLIT_CMD" 2>/dev/null)"
            case "$AW_ABS" in
                /*) AW_PY="$AW_ABS" ;;
                *) AW_PY="$AW_SPLIT_CMD" ;;
            esac
            AW_PYARG="$AW_SPLIT_ARG"
            break
        fi
    done
fi

# 4. uv, by absolute path first. It downloads a private 3.10+ on demand, so it
#    works on a machine with no system Python at all. Costs ~700 ms per call.
if [ -z "$AW_PY" ]; then
    for aw_uv in "$HOME/.local/bin/uv" "$HOME/.local/bin/uv.exe"; do
        [ -f "$aw_uv" ] || continue
        if aw_try "$aw_uv" run --no-project --python ">=3.10" python; then
            AW_PY="$aw_uv"
            AW_PYARG="run --no-project --python >=3.10 python"
            break
        fi
    done
fi
if [ -z "$AW_PY" ] && command -v uv >/dev/null 2>&1; then
    if aw_try uv run --no-project --python ">=3.10" python; then
        AW_PY="uv"
        AW_PYARG="run --no-project --python >=3.10 python"
    fi
fi

# 5a. the Windows launcher, at its fixed location
if [ -z "$AW_PY" ] && [ -f /c/Windows/py.exe ]; then
    if aw_try /c/Windows/py.exe -3; then
        AW_PY="/c/Windows/py.exe"
        AW_PYARG="-3"
    fi
fi

# 5b. absolute interpreters, including the ones uv downloads. Unmatched globs
#     stay literal, which the -f test filters out.
if [ -z "$AW_PY" ]; then
    AW_LA="$(aw_localappdata)"
    for aw_p in \
        "$AW_LA"/Programs/Python/Python3*/python.exe \
        "$HOME"/AppData/Roaming/uv/python/*/python.exe \
        "$HOME"/.local/share/uv/python/*/bin/python3 \
        /usr/bin/python3 \
        /usr/local/bin/python3 \
        /opt/homebrew/bin/python3; do
        [ -f "$aw_p" ] || continue
        if aw_try "$aw_p"; then
            AW_PY="$aw_p"
            AW_PYARG=""   # set in step with AW_PY, like every other rung
            break
        fi
    done
fi

if [ -z "$AW_PY" ]; then
    printf '%s\n' 'autoweb: no Python >= 3.10 found; run /autoweb:aw-setup' >&2
    exit 0
fi

if [ -z "$AW_FROM_CACHE" ]; then
    aw_cache_write
fi

# shellcheck disable=SC2086
exec "$AW_PY" $AW_PYARG "$AW_SCRIPT" "$@"
