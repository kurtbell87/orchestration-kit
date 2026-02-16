#!/usr/bin/env python3
"""
Math Sub-Agent Monitor — a live dashboard for math.sh stream-json logs.

Usage:
    python3 scripts/math-watch.py              # auto-detects most recent phase
    python3 scripts/math-watch.py prove        # watch the prove phase
    python3 scripts/math-watch.py audit        # watch the audit phase
    python3 scripts/math-watch.py prove --resolve  # one-shot summary of prove phase
    python3 scripts/math-watch.py prove --verbose  # show tool result output

Parses the stream-json events emitted by `claude --output-format stream-json`
and renders a compact, human-readable live view with Lean4-specific tracking.
"""

import json
import os
import re
import sys
import time
import glob
import signal
import textwrap
from datetime import datetime
from pathlib import Path

# ── ANSI helpers ──────────────────────────────────────────────────────────────

BOLD      = "\033[1m"
DIM       = "\033[2m"
RESET     = "\033[0m"
RED       = "\033[31m"
GREEN     = "\033[32m"
YELLOW    = "\033[33m"
BLUE      = "\033[34m"
MAGENTA   = "\033[35m"
CYAN      = "\033[36m"
WHITE     = "\033[37m"

CLEAR_LINE = "\033[2K"
MOVE_UP    = "\033[A"

def strip_ansi(s: str) -> str:
    return re.sub(r'\033\[[0-9;]*m', '', s)


# Phase colors
PHASE_COLORS = {
    "survey":    CYAN,
    "specify":   BLUE,
    "construct": MAGENTA,
    "formalize": YELLOW,
    "prove":     GREEN,
    "audit":     RED,
    "log":       MAGENTA,
}

PHASES = set(PHASE_COLORS.keys())


# ── State tracker ─────────────────────────────────────────────────────────────

class AgentState:
    def __init__(self):
        self.phase = "?"
        self.start_time = None
        self.model = None
        self.tool_calls = 0
        self.api_turns = 0
        self.files_read = []
        self.files_written = []
        self.files_edited = []
        self.bash_commands = []
        self.agent_texts = []
        self.current_action = None
        self.current_tool_id = None
        self.sub_agents = 0
        self.errors = []
        self.done = False

        # Lean4-specific tracking
        self.sorry_initial = None
        self.sorry_current = None
        self.lake_builds = []       # list of ("pass"/"fail", timestamp)
        self.theorems_proved = []   # list of theorem names
        self.sorry_removals = 0     # count of sorry -> proof edits

        # R4/R2: Revision and timing tracking
        self.revision_cycles = 0
        self.error_classes = []     # list of classified errors
        self.lake_build_total_seconds = 0
        self.agent_think_total_seconds = 0

    @property
    def elapsed(self) -> str:
        if not self.start_time:
            return "—"
        delta = datetime.now() - self.start_time
        mins = int(delta.total_seconds() // 60)
        secs = int(delta.total_seconds() % 60)
        return f"{mins}m {secs:02d}s"

    def phase_color(self) -> str:
        return PHASE_COLORS.get(self.phase, WHITE)

    @property
    def sorry_progress(self) -> str:
        if self.sorry_initial is None or self.sorry_current is None:
            return ""
        if self.sorry_initial == 0:
            return "0/0"
        done = self.sorry_initial - self.sorry_current
        pct = (done / self.sorry_initial) * 100
        return f"{done}/{self.sorry_initial} ({pct:.0f}%)"

    @property
    def lake_build_summary(self) -> str:
        if not self.lake_builds:
            return ""
        passes = sum(1 for s, _ in self.lake_builds if s == "pass")
        fails = sum(1 for s, _ in self.lake_builds if s == "fail")
        last = self.lake_builds[-1][0]
        color = GREEN if last == "pass" else RED
        return f"{color}{last.upper()}{RESET} ({passes}P/{fails}F)"


# ── Event processor ───────────────────────────────────────────────────────────

def process_banner_line(line: str, state: AgentState):
    """Handle non-JSON banner lines from math.sh."""
    plain = strip_ansi(line)
    for phase in PHASES:
        if f"{phase.upper()} PHASE" in plain:
            state.phase = phase
            break
    # Extract initial sorry count from banner
    m = re.search(r'Sorrys:\s*(\d+)', plain)
    if m:
        count = int(m.group(1))
        if state.sorry_initial is None:
            state.sorry_initial = count
        state.sorry_current = count

    # Detect revision cycles
    m = re.search(r'REVISION\s+(\d+)/(\d+)', plain)
    if m:
        state.revision_cycles = int(m.group(1))


def process_event(data: dict, state: AgentState, verbose: bool = False) -> list:
    """Process one stream-json event. Returns lines to display."""
    lines = []
    etype = data.get("type", "")

    if etype == "system":
        if not state.start_time:
            state.start_time = datetime.now()
        return []

    if etype != "assistant":
        msg = data.get("message", {})
        for c in msg.get("content", []):
            if c.get("type") == "tool_result":
                result_text = c.get("content", "")
                if isinstance(result_text, list):
                    result_text = " ".join(
                        r.get("text", "") for r in result_text if isinstance(r, dict)
                    )
                _extract_lean_results(result_text, state)
                if verbose and isinstance(result_text, str) and result_text.strip():
                    lines.extend(_format_tool_result(result_text))
        return lines

    msg = data.get("message", {})
    if msg.get("model") and not state.model:
        state.model = msg["model"]

    state.api_turns += 1
    if not state.start_time:
        state.start_time = datetime.now()

    for c in msg.get("content", []):
        ct = c.get("type")

        if ct == "text":
            text = c["text"].strip()
            if text:
                state.agent_texts.append(text)
                wrapped = textwrap.fill(text, width=90, subsequent_indent="    ")
                lines.append(f"  {CYAN}💬{RESET} {wrapped}")

        elif ct == "tool_use":
            state.tool_calls += 1
            name = c.get("name", "?")
            inp = c.get("input", {})
            tool_id = c.get("id", "")
            state.current_tool_id = tool_id

            # Detect sorry removal edits
            if name == "Edit" and "sorry" in inp.get("old_string", "") and "sorry" not in inp.get("new_string", ""):
                state.sorry_removals += 1
                # Try to extract theorem name from context
                new_str = inp.get("new_string", "")
                fp = inp.get("file_path", "")
                state.theorems_proved.append(f"proof in {_short_path(fp)}")

            line = _format_tool_call(name, inp, state)
            if line:
                lines.append(line)

    return lines


VERBOSE_MAX_LINES = 30

def _format_tool_result(text: str) -> list:
    """Format tool result output for verbose display."""
    result_lines = text.strip().split("\n")
    is_error = any(kw in text.lower() for kw in [
        "error", "failed", "failure", "fatal", "sorry",
        "unknown identifier", "type mismatch", "tactic",
    ])

    color = RED if is_error else DIM
    prefix = f"  {color}│{RESET} "

    formatted = []
    if len(result_lines) > VERBOSE_MAX_LINES:
        head = result_lines[:10]
        tail = result_lines[-15:]
        skipped = len(result_lines) - 25
        for rl in head:
            formatted.append(f"{prefix}{color}{rl[:200]}{RESET}")
        formatted.append(f"{prefix}{DIM}... ({skipped} lines omitted) ...{RESET}")
        for rl in tail:
            formatted.append(f"{prefix}{color}{rl[:200]}{RESET}")
    else:
        for rl in result_lines:
            formatted.append(f"{prefix}{color}{rl[:200]}{RESET}")

    return formatted


def _format_tool_call(name: str, inp: dict, state: AgentState) -> str:
    """Format a tool call into a single display line."""
    if name == "Read":
        fp = inp.get("file_path", "?")
        short = _short_path(fp)
        state.files_read.append(short)
        state.current_action = f"Reading {short}"
        return f"  {DIM}📖 Read{RESET}  {short}"

    elif name == "Write":
        fp = inp.get("file_path", "?")
        short = _short_path(fp)
        content = inp.get("content", "")
        state.files_written.append(short)
        state.current_action = f"Writing {short}"
        # Detect .lean file writes
        icon = "📝"
        if short.endswith(".lean"):
            sorry_count = content.count("sorry")
            return f"  {GREEN}{icon} Write{RESET} {short}  {DIM}({len(content)} chars, {sorry_count} sorry){RESET}"
        return f"  {GREEN}{icon} Write{RESET} {short}  {DIM}({len(content)} chars){RESET}"

    elif name == "Edit":
        fp = inp.get("file_path", "?")
        short = _short_path(fp)
        old = inp.get("old_string", "")
        new = inp.get("new_string", "")
        state.files_edited.append(short)
        state.current_action = f"Editing {short}"

        # Detect sorry -> proof replacement
        if "sorry" in old and "sorry" not in new:
            return f"  {GREEN}✅ Proof{RESET} {short}  {DIM}(sorry → proof){RESET}"

        delta = len(new) - len(old)
        sign = "+" if delta >= 0 else ""
        return f"  {YELLOW}✏️  Edit{RESET}  {short}  {DIM}({sign}{delta} chars){RESET}"

    elif name == "Bash":
        cmd = inp.get("command", "?")
        desc = inp.get("description", "")
        display = desc if desc else cmd
        if len(display) > 80:
            display = display[:77] + "..."
        state.bash_commands.append(cmd)
        state.current_action = f"Running: {display}"

        if "lake build" in cmd or "lake" in cmd:
            return f"  {MAGENTA}🔨 Lake{RESET}  {display}"
        elif "#check" in cmd or "#print" in cmd or "#find" in cmd:
            return f"  {CYAN}🔍 Lean{RESET}  {display}"
        else:
            return f"  {BLUE}$ Bash{RESET}  {display}"

    elif name == "Task":
        state.sub_agents += 1
        desc = inp.get("description", "sub-agent")
        state.current_action = f"Sub-agent: {desc}"
        return f"  {MAGENTA}🤖 Task{RESET}  {desc}"

    elif name == "Glob":
        pattern = inp.get("pattern", "?")
        state.current_action = f"Glob {pattern}"
        return f"  {DIM}🔍 Glob{RESET}  {pattern}"

    elif name == "Grep":
        pattern = inp.get("pattern", "?")
        state.current_action = f"Grep {pattern}"
        return f"  {DIM}🔍 Grep{RESET}  {pattern}"

    else:
        state.current_action = f"{name}"
        return f"  {DIM}🔧 {name}{RESET}"


def _extract_lean_results(text: str, state: AgentState):
    """Extract Lean4-specific results from tool output."""
    if not isinstance(text, str):
        return

    # Detect lake build pass/fail
    if "lake build" in text.lower() or "Build completed" in text or "error:" in text.lower():
        if "error" in text.lower() or "failed" in text.lower():
            state.lake_builds.append(("fail", datetime.now()))
        elif "warning" in text.lower() and "sorry" in text.lower():
            state.lake_builds.append(("pass", datetime.now()))
        elif "Build completed" in text or not any(kw in text.lower() for kw in ["error", "failed"]):
            state.lake_builds.append(("pass", datetime.now()))

    # Count sorry mentions in build output
    sorry_matches = re.findall(r"declaration uses 'sorry'", text)
    if sorry_matches:
        state.sorry_current = len(sorry_matches)

    # Extract sorry count from grep results
    m = re.search(r'Total:\s*(\d+)\s*sorry', text)
    if m:
        count = int(m.group(1))
        if state.sorry_initial is None:
            state.sorry_initial = count
        state.sorry_current = count


def _short_path(fp: str) -> str:
    """Shorten an absolute path to project-relative."""
    cwd = os.getcwd()
    if fp.startswith(cwd):
        return fp[len(cwd):].lstrip("/")
    return os.path.basename(fp)


# ── Display ───────────────────────────────────────────────────────────────────

def print_header(state: AgentState):
    """Print a sticky header bar."""
    pc = state.phase_color()
    phase_display = state.phase.upper() if state.phase != "?" else "STARTING"
    model_short = (state.model or "?").replace("claude-", "").split("-202")[0]

    unique_reads = len(set(state.files_read))
    unique_writes = len(set(state.files_written))
    unique_edits = len(set(state.files_edited))

    bar = (
        f"{BOLD}{pc}▌ MATH {phase_display} {RESET}"
        f" {DIM}│{RESET} {BOLD}{state.elapsed}{RESET}"
        f" {DIM}│{RESET} model: {model_short}"
        f" {DIM}│{RESET} turns: {state.api_turns}"
        f" {DIM}│{RESET} tools: {state.tool_calls}"
        f" {DIM}│{RESET} 📖{unique_reads} 📝{unique_writes} ✏️{unique_edits}"
    )

    if state.sub_agents:
        bar += f" {DIM}│{RESET} 🤖{state.sub_agents}"

    # Lean4-specific metrics
    sorry = state.sorry_progress
    if sorry:
        bar += f" {DIM}│{RESET} sorry: {sorry}"

    lake = state.lake_build_summary
    if lake:
        bar += f" {DIM}│{RESET} lake: {lake}"

    if state.sorry_removals:
        bar += f" {DIM}│{RESET} {GREEN}✅{state.sorry_removals} proved{RESET}"

    if state.revision_cycles:
        bar += f" {DIM}│{RESET} {YELLOW}rev:{state.revision_cycles}{RESET}"

    print(f"\n{bar}")
    print(f"  {DIM}{'─' * 100}{RESET}")


def print_summary(state: AgentState):
    """Print a final summary."""
    pc = state.phase_color()
    phase_display = state.phase.upper()

    print(f"\n{'═' * 70}")
    print(f"{BOLD}{pc}  MATH {phase_display} — COMPLETE{RESET}")
    print(f"{'═' * 70}")
    print(f"  Elapsed:        {state.elapsed}")
    print(f"  Model:          {state.model or '?'}")
    print(f"  API turns:      {state.api_turns}")
    print(f"  Tool calls:     {state.tool_calls}")
    print(f"  Files read:     {len(set(state.files_read))}")
    print(f"  Files written:  {len(set(state.files_written))}")
    print(f"  Files edited:   {len(set(state.files_edited))}")
    if state.sub_agents:
        print(f"  Sub-agents:     {state.sub_agents}")

    # Lean4-specific summary
    print(f"\n  {BOLD}Lean4 Metrics:{RESET}")
    if state.sorry_initial is not None:
        print(f"  Sorry (start):  {state.sorry_initial}")
    if state.sorry_current is not None:
        print(f"  Sorry (end):    {state.sorry_current}")
    if state.sorry_progress:
        print(f"  Sorry progress: {state.sorry_progress}")
    print(f"  Sorry removals: {state.sorry_removals}")
    if state.lake_builds:
        passes = sum(1 for s, _ in state.lake_builds if s == "pass")
        fails = sum(1 for s, _ in state.lake_builds if s == "fail")
        print(f"  Lake builds:    {len(state.lake_builds)} ({passes} pass, {fails} fail)")

    if state.theorems_proved:
        print(f"\n  {GREEN}Theorems proved:{RESET}")
        for t in state.theorems_proved:
            print(f"    ✅ {t}")

    if state.files_written:
        print(f"\n  {GREEN}Files created/written:{RESET}")
        for f in sorted(set(state.files_written)):
            print(f"    + {f}")
    if state.files_edited:
        print(f"\n  {YELLOW}Files edited:{RESET}")
        for f in sorted(set(state.files_edited)):
            print(f"    ~ {f}")

    if state.agent_texts:
        print(f"\n  {CYAN}Agent notes:{RESET}")
        for t in state.agent_texts[-5:]:
            wrapped = textwrap.fill(t, width=80, initial_indent="    ", subsequent_indent="    ")
            print(wrapped)

    print(f"{'═' * 70}\n")


# ── Main loop ─────────────────────────────────────────────────────────────────

def _get_log_dir() -> str:
    """Get the per-project log directory from env or derive it."""
    log_dir = os.environ.get("MATH_LOG_DIR")
    if log_dir:
        return log_dir
    import subprocess
    try:
        toplevel = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        project = os.path.basename(toplevel)
    except (subprocess.CalledProcessError, FileNotFoundError):
        project = os.path.basename(os.getcwd())
    return f"/tmp/math-{project}"


def find_log_file() -> str:
    """Auto-detect the most recent *.log in the project log directory."""
    log_dir = _get_log_dir()
    candidates = glob.glob(f"{log_dir}/*.log")
    if not candidates:
        print(f"{RED}No log files found in {log_dir}/{RESET}")
        print(f"Start a math phase first:  ./math.sh prove specs/my-construction.md")
        sys.exit(1)
    best = max(candidates, key=os.path.getmtime)
    return best


def tail_follow(filepath: str):
    """Generator that yields new lines from a file, following like tail -f."""
    with open(filepath, "r") as f:
        while True:
            line = f.readline()
            if not line:
                break
            yield line

        while True:
            line = f.readline()
            if line:
                yield line
            else:
                time.sleep(0.3)


def run_resolve(filepath: str, verbose: bool = False):
    """One-shot: parse entire log and print summary."""
    state = AgentState()
    with open(filepath, "r") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                data = json.loads(line)
                display_lines = process_event(data, state, verbose=verbose)
                for dl in display_lines:
                    print(dl)
            except json.JSONDecodeError:
                process_banner_line(line, state)

    print_summary(state)


def run_live(filepath: str, verbose: bool = False):
    """Live tail mode: follow the log and display events."""
    state = AgentState()
    header_interval = 15
    event_count = 0

    mode_label = f" {YELLOW}(verbose){RESET}" if verbose else ""
    print(f"{BOLD}Watching:{RESET} {filepath}{mode_label}")
    print(f"{DIM}Press Ctrl+C to stop{RESET}")

    for line in tail_follow(filepath):
        line = line.rstrip("\n")
        if not line:
            continue

        try:
            data = json.loads(line)
            display_lines = process_event(data, state, verbose=verbose)
        except json.JSONDecodeError:
            process_banner_line(line, state)
            plain = strip_ansi(line).strip()
            if plain:
                print(f"  {DIM}{plain}{RESET}")
            continue

        if not display_lines:
            continue

        event_count += 1

        if event_count % header_interval == 1:
            print_header(state)

        for dl in display_lines:
            print(dl)

        # Detect completion signals
        for text in state.agent_texts[-1:]:
            if any(phrase in text.lower() for phrase in [
                "all sorrys", "zero sorry", "audit complete",
                "all theorems proved", "construction complete",
                "final summary", "revision request"
            ]):
                print_header(state)
                print(f"\n  {GREEN}{BOLD}✓ Agent appears to be finishing up{RESET}\n")


def main():
    signal.signal(signal.SIGINT, lambda *_: (print(f"\n{RESET}"), sys.exit(0)))

    args = sys.argv[1:]
    resolve_mode = "--resolve" in args or "--summary" in args
    verbose_mode = "--verbose" in args or "-v" in args
    args = [a for a in args if not a.startswith("-")]

    if args and args[0] in PHASES:
        log_dir = _get_log_dir()
        filepath = f"{log_dir}/{args[0]}.log"
    elif args:
        filepath = args[0]
    else:
        filepath = find_log_file()

    if not os.path.exists(filepath):
        print(f"{RED}File not found: {filepath}{RESET}")
        sys.exit(1)

    if resolve_mode:
        run_resolve(filepath, verbose=verbose_mode)
    else:
        run_live(filepath, verbose=verbose_mode)


if __name__ == "__main__":
    main()
