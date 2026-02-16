#!/usr/bin/env python3
"""
TDD Sub-Agent Monitor — a live dashboard for tdd.sh stream-json logs.

Usage:
    python3 scripts/tdd-watch.py              # auto-detects most recent phase
    python3 scripts/tdd-watch.py green         # watch the green phase
    python3 scripts/tdd-watch.py refactor      # watch the refactor phase
    python3 scripts/tdd-watch.py red --resolve  # one-shot summary of red phase
    python3 scripts/tdd-watch.py green --verbose  # show tool result output (build logs, test output)

Parses the stream-json events emitted by `claude --output-format stream-json`
and renders a compact, human-readable live view.
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


# ── State tracker ─────────────────────────────────────────────────────────────

class AgentState:
    def __init__(self):
        self.phase = "?"           # red / green / refactor
        self.start_time = None
        self.model = None
        self.tool_calls = 0
        self.api_turns = 0
        self.files_read = []
        self.files_written = []
        self.files_edited = []
        self.bash_commands = []
        self.agent_texts = []
        self.test_results = []     # list of (kind, count, status)
        self.current_action = None
        self.current_tool_id = None
        self.sub_agents = 0
        self.errors = []
        self.done = False

    @property
    def elapsed(self) -> str:
        if not self.start_time:
            return "—"
        delta = datetime.now() - self.start_time
        mins = int(delta.total_seconds() // 60)
        secs = int(delta.total_seconds() % 60)
        return f"{mins}m {secs:02d}s"

    def phase_color(self) -> str:
        return {
            "red": RED, "green": GREEN, "refactor": BLUE
        }.get(self.phase, WHITE)


# ── Event processor ───────────────────────────────────────────────────────────

def process_banner_line(line: str, state: AgentState):
    """Handle non-JSON banner lines from tdd.sh."""
    plain = strip_ansi(line)
    if "RED PHASE" in plain:
        state.phase = "red"
    elif "GREEN PHASE" in plain:
        state.phase = "green"
    elif "REFACTOR PHASE" in plain:
        state.phase = "refactor"


def process_event(data: dict, state: AgentState, verbose: bool = False) -> list[str]:
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
                _extract_test_results(result_text, state)
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
            line = _format_tool_call(name, inp, state)
            if line:
                lines.append(line)

    return lines


# Max lines of tool result output to show in verbose mode
VERBOSE_MAX_LINES = 30

def _format_tool_result(text: str) -> list[str]:
    """Format tool result output for verbose display. Shows errors prominently."""
    result_lines = text.strip().split("\n")
    is_error = any(kw in text.lower() for kw in [
        "error", "failed", "failure", "fatal", "undefined reference",
        "no such file", "permission denied", "traceback", "assert",
    ])

    # Color: red for errors, dim for normal output
    color = RED if is_error else DIM
    prefix = f"  {color}│{RESET} "

    formatted = []
    if len(result_lines) > VERBOSE_MAX_LINES:
        # Show first few + last few lines with a gap indicator
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
        return f"  {GREEN}📝 Write{RESET} {short}  {DIM}({len(content)} chars){RESET}"

    elif name == "Edit":
        fp = inp.get("file_path", "?")
        short = _short_path(fp)
        old = inp.get("old_string", "")
        new = inp.get("new_string", "")
        state.files_edited.append(short)
        state.current_action = f"Editing {short}"
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

        if any(kw in cmd for kw in ["cmake --build", "make", "cargo build", "npm run build"]):
            return f"  {MAGENTA}🔨 Build{RESET} {display}"
        elif any(kw in cmd for kw in ["pytest", "ctest", "npm test", "cargo test", "jest"]):
            return f"  {MAGENTA}🧪 Test{RESET}  {display}"
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


def _extract_test_results(text: str, state: AgentState):
    """Pull test pass counts from tool results."""
    if not isinstance(text, str):
        return
    # Generic: "XX tests passed" / "XX tests failed"
    m = re.search(r'(\d+) tests? passed', text)
    if m:
        state.test_results.append(("test", int(m.group(1)), "passed"))
    m = re.search(r'(\d+) tests? failed', text)
    if m:
        state.test_results.append(("test", int(m.group(1)), "failed"))
    # pytest: "XX passed" / "XX failed"
    m = re.search(r'(\d+) passed', text)
    if m:
        state.test_results.append(("test", int(m.group(1)), "passed"))
    m = re.search(r'(\d+) failed', text)
    if m:
        state.test_results.append(("test", int(m.group(1)), "failed"))


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
        f"{BOLD}{pc}▌ TDD {phase_display} {RESET}"
        f" {DIM}│{RESET} {BOLD}{state.elapsed}{RESET}"
        f" {DIM}│{RESET} model: {model_short}"
        f" {DIM}│{RESET} turns: {state.api_turns}"
        f" {DIM}│{RESET} tools: {state.tool_calls}"
        f" {DIM}│{RESET} 📖{unique_reads} 📝{unique_writes} ✏️{unique_edits}"
    )

    if state.sub_agents:
        bar += f" {DIM}│{RESET} 🤖{state.sub_agents}"

    last_tests = _latest_test_summary(state)
    if last_tests:
        bar += f" {DIM}│{RESET} {last_tests}"

    print(f"\n{bar}")
    print(f"  {DIM}{'─' * 88}{RESET}")


def _latest_test_summary(state: AgentState) -> str:
    """Summarize the most recent test results."""
    if not state.test_results:
        return ""
    recent = state.test_results[-4:]
    parts = []
    for kind, count, status in recent:
        color = GREEN if status == "passed" else RED
        icon = "✓" if status == "passed" else "✗"
        parts.append(f"{color}{icon}{count}{RESET}")
    return " ".join(parts)


def print_summary(state: AgentState):
    """Print a final summary."""
    pc = state.phase_color()
    phase_display = state.phase.upper()

    print(f"\n{'═' * 60}")
    print(f"{BOLD}{pc}  TDD {phase_display} — COMPLETE{RESET}")
    print(f"{'═' * 60}")
    print(f"  Elapsed:      {state.elapsed}")
    print(f"  Model:        {state.model or '?'}")
    print(f"  API turns:    {state.api_turns}")
    print(f"  Tool calls:   {state.tool_calls}")
    print(f"  Files read:   {len(set(state.files_read))}")
    print(f"  Files written:{len(set(state.files_written))}")
    print(f"  Files edited: {len(set(state.files_edited))}")
    if state.sub_agents:
        print(f"  Sub-agents:   {state.sub_agents}")

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

    print(f"{'═' * 60}\n")


# ── Main loop ─────────────────────────────────────────────────────────────────

def _get_log_dir() -> str:
    """Get the per-project log directory from env or derive it."""
    log_dir = os.environ.get("TDD_LOG_DIR")
    if log_dir:
        return log_dir
    # Derive from git repo name, same logic as tdd.sh
    import subprocess
    try:
        toplevel = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        project = os.path.basename(toplevel)
    except (subprocess.CalledProcessError, FileNotFoundError):
        project = os.path.basename(os.getcwd())
    return f"/tmp/tdd-{project}"


def find_log_file() -> str:
    """Auto-detect the most recent *.log in the project log directory."""
    log_dir = _get_log_dir()
    candidates = glob.glob(f"{log_dir}/*.log")
    if not candidates:
        print(f"{RED}No log files found in {log_dir}/{RESET}")
        print(f"Start a TDD phase first:  ./tdd.sh red docs/feature.md")
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

        for text in state.agent_texts[-1:]:
            if any(phrase in text.lower() for phrase in [
                "all tests pass", "final summary", "implementation complete"
            ]):
                print_header(state)
                print(f"\n  {GREEN}{BOLD}✓ Agent appears to be finishing up{RESET}\n")


def main():
    signal.signal(signal.SIGINT, lambda *_: (print(f"\n{RESET}"), sys.exit(0)))

    PHASES = {"red", "green", "refactor"}

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
