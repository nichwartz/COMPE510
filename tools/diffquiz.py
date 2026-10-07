#!/usr/bin/env python3
"""diffquiz: a multiple-choice quiz on your staged changes before every commit.

Commands:
  install   Install diffquiz as this repo's git pre-commit hook.
  run       Quiz on the staged diff (called by the pre-commit hook).
  mark      Record a file the AI edited (called by a Claude Code hook,
            reads the hook event JSON from stdin).

Settings (environment variables):
  ANTHROPIC_API_KEY    Required to generate questions.
  DIFFQUIZ_SCOPE       "all" (default) quizzes every staged code file.
                       "ai" quizzes only files a Claude Code hook marked.
  DIFFQUIZ_QUESTIONS   Number of questions per commit (default 4).
  DIFFQUIZ_PASS        Fraction of questions you must get right to commit,
                       0 to 1 (default 0.7, so 3 of 4).
  DIFFQUIZ_MODEL       Model used to write the questions.
  DIFFQUIZ_SKIP        Set to 1 to skip the quiz for one commit.
"""
import argparse
import datetime
import json
import os
import random
import re
import subprocess
import sys

MODEL = os.environ.get("DIFFQUIZ_MODEL", "claude-haiku-4-5-20251001")
SCOPE = os.environ.get("DIFFQUIZ_SCOPE", "all")
NUM_QUESTIONS = int(os.environ.get("DIFFQUIZ_QUESTIONS", "4"))
PASS_SCORE = float(os.environ.get("DIFFQUIZ_PASS", "0.7"))
MAX_DIFF_CHARS = 12000
CODE_EXTS = (".py", ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx",
             ".ino", ".rs", ".js", ".ts", ".java", ".go", ".cmake")
LETTERS = "ABCD"


# ---------- git helpers ----------

def git(*args, cwd=None):
    result = subprocess.run(["git", *args], capture_output=True, text=True,
                            check=True, cwd=cwd)
    return result.stdout.strip()


def git_dir(cwd=None):
    return git("rev-parse", "--absolute-git-dir", cwd=cwd)


def repo_root(cwd=None):
    return git("rev-parse", "--show-toplevel", cwd=cwd)


def touched_file(cwd=None):
    return os.path.join(git_dir(cwd), "diffquiz-ai-touched.txt")


def log_file():
    return os.path.join(git_dir(), "diffquiz-log.jsonl")


def read_touched():
    try:
        with open(touched_file()) as f:
            return {line.strip() for line in f if line.strip()}
    except FileNotFoundError:
        return set()


def clear_touched(files):
    remaining = read_touched() - set(files)
    with open(touched_file(), "w") as f:
        f.writelines(path + "\n" for path in sorted(remaining))


# ---------- question generation ----------

QUESTION_PROMPT = """You are quizzing a developer on a diff they are about to commit,
to confirm they understand the code rather than just accepting it from an AI.

Write {n} multiple-choice questions, each with exactly four choices and one
correct answer. Test understanding of WHY the code works: edge cases, failure
modes, complexity, design choices, and language semantics (C++: ownership,
lifetimes, undefined behavior, copies vs moves, const; Python: mutability,
iteration, exceptions, scoping). Skip trivia like variable names.

Wrong choices must be plausible mistakes a developer could actually make,
similar in length and style to the correct one, so the answer cannot be
guessed from wording alone. Never use "all of the above" or "none of the above".

Return only JSON in this shape, with "correct" being the 0-based index of the
right choice:
{{"questions": [{{"q": "question text", "choices": ["...", "...", "...", "..."],
  "correct": 0, "explanation": "one or two sentences on why it is right"}}]}}

Diff:
{diff}
"""


def ask_llm(prompt, max_tokens=2500):
    import anthropic  # imported lazily so "mark" and "install" work without it

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in msg.content if block.type == "text")
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("model response contained no JSON")
    return json.loads(match.group(0))


def clean_questions(raw):
    """Keep well-formed questions and shuffle each one's choices.

    Models tend to put the right answer in the same slot, so shuffling
    locally keeps the position from giving it away.
    """
    cleaned = []
    for item in raw:
        choices, correct = item.get("choices"), item.get("correct")
        if (not isinstance(choices, list) or len(choices) != 4
                or not isinstance(correct, int) or not 0 <= correct < 4):
            continue
        order = list(range(4))
        random.shuffle(order)
        cleaned.append({
            "q": item.get("q", ""),
            "choices": [choices[i] for i in order],
            "correct": order.index(correct),
            "explanation": item.get("explanation", ""),
        })
    return cleaned[:NUM_QUESTIONS]


# ---------- quiz ----------

def open_tty():
    """Git hooks get no stdin, so read answers straight from the terminal."""
    try:
        return open("CONIN$" if os.name == "nt" else "/dev/tty")
    except OSError:
        return None


def read_letter(tty):
    """Prompt until the user types A, B, C or D. Returns "" if input ends."""
    while True:
        print("Your answer (A-D): ", end="", flush=True)
        line = tty.readline()
        if not line:
            return ""
        letter = line.strip().upper()
        if len(letter) == 1 and letter in LETTERS:
            return letter
        print("  Please type A, B, C or D.")


def run_quiz(diff, tty):
    """Ask each question, grade it locally, and return a record per question."""
    raw = ask_llm(QUESTION_PROMPT.format(n=NUM_QUESTIONS, diff=diff))
    questions = clean_questions(raw.get("questions", []))
    if not questions:
        raise ValueError("model returned no usable questions")

    records = []
    for i, item in enumerate(questions, 1):
        print(f"Q{i}. {item['q']}")
        for letter, choice in zip(LETTERS, item["choices"]):
            print(f"   {letter}) {choice}")
        answer = read_letter(tty)
        right = LETTERS[item["correct"]]
        if answer == right:
            print("   Correct.")
        else:
            print(f"   Not quite. The answer is {right}.")
        if item["explanation"]:
            print(f"   {item['explanation']}")
        print()
        records.append({
            "question": item["q"],
            "choices": dict(zip(LETTERS, item["choices"])),
            "correct": right,
            "answer": answer,
        })
    return records


# ---------- commands ----------

def cmd_mark():
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    path = (event.get("tool_input") or {}).get("file_path")
    if not path:
        return 0
    try:
        # Look up the repo from the edited file itself, so this works even when
        # Claude Code was opened in a parent folder holding several repos.
        file_dir = os.path.dirname(os.path.abspath(path))
        rel = os.path.relpath(os.path.abspath(path), repo_root(file_dir)).replace(os.sep, "/")
        with open(touched_file(file_dir), "a") as f:
            f.write(rel + "\n")
    except (subprocess.CalledProcessError, ValueError, OSError):
        pass  # never break the AI session over bookkeeping
    return 0


def cmd_run():
    if os.environ.get("DIFFQUIZ_SKIP"):
        print("diffquiz: skipped (DIFFQUIZ_SKIP is set)")
        return 0

    files = git("diff", "--cached", "--name-only", "--diff-filter=ACMR").splitlines()
    if SCOPE == "ai":
        touched = read_touched()
        files = [f for f in files if f in touched]
    files = [f for f in files if f.lower().endswith(CODE_EXTS)]
    if not files:
        return 0

    diff = git("diff", "--cached", "--unified=5", "--", *files)
    if not diff:
        return 0
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n... [diff truncated]"

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("diffquiz: ANTHROPIC_API_KEY not set, skipping quiz")
        return 0
    tty = open_tty()
    if tty is None:
        print("diffquiz: no terminal available (GUI client?), skipping quiz")
        return 0

    print(f"\ndiffquiz: {len(files)} file(s) staged. Generating questions...\n")
    try:
        records = run_quiz(diff, tty)
    except Exception as exc:  # fail open so an API outage never blocks work
        print(f"diffquiz: quiz failed ({exc}), allowing commit")
        return 0
    finally:
        tty.close()

    num_right = sum(r["answer"] == r["correct"] for r in records)
    fraction = num_right / len(records)
    passed = fraction >= PASS_SCORE

    with open(log_file(), "a") as f:
        f.write(json.dumps({
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
            "files": files,
            "questions": records,
            "num_right": num_right,
            "num_questions": len(records),
            "passed": passed,
        }) + "\n")

    print(f"Score: {num_right} of {len(records)} correct (need {PASS_SCORE:.0%})")
    if passed:
        print("diffquiz: passed, committing.\n")
        clear_touched(files)
        return 0
    print("diffquiz: not passed. Review the explanations, then commit again.\n")
    return 1


def cmd_install():
    hooks_dir = git("rev-parse", "--git-path", "hooks")
    os.makedirs(hooks_dir, exist_ok=True)
    hook = os.path.join(hooks_dir, "pre-commit")
    if os.path.exists(hook):
        with open(hook) as f:
            if "diffquiz" not in f.read():
                print(f"diffquiz: {hook} already exists and is not ours; not overwriting")
                return 1
    python = sys.executable.replace("\\", "/")
    script = os.path.abspath(__file__).replace("\\", "/")
    with open(hook, "w", newline="\n") as f:
        f.write(f'#!/bin/sh\n# diffquiz pre-commit hook\nexec "{python}" "{script}" run\n')
    os.chmod(hook, 0o755)
    print(f"diffquiz: installed pre-commit hook at {hook}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["install", "run", "mark"])
    args = parser.parse_args()
    return {"install": cmd_install, "run": cmd_run, "mark": cmd_mark}[args.command]()


if __name__ == "__main__":
    sys.exit(main())
