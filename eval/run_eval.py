#!/usr/bin/env python3
"""Run the golden question set against the prompt and score the answers.

For each question in eval/questions.yaml this script:
  1. asks the model, using prompts/system_prompt.md plus the extracted CMS source text;
  2. runs cheap deterministic checks (source line present, right year, no unrequested
     figures or next steps, ...) driven by the YAML fields;
  3. asks a judge model to grade the answer against `expected_behavior`;
  4. writes a report to eval/results/<run>/, refreshes eval/results/latest.*, and compares
     against the previous run so you can see whether a change made things better or worse.

Run from the repo root:
    python3 eval/run_eval.py --dry-run                  # no API calls; validates inputs
    python3 eval/run_eval.py                            # full run
    python3 eval/run_eval.py --ids concept-parts-overview,cost-part-b-premium-pending
    python3 eval/run_eval.py --label tighten-item-5     # name the run
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("Missing dependency: pip3 install -r requirements.txt")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "claude-sonnet-5-5"

# Judge criteria: SCOPE.md section 8 pass criteria, plus on_question (over-answering)
# and matches_expected (overall behavior, including clarifying-question cases).
CRITERIA = [
    "accurate",
    "year_label",
    "citation",
    "in_scope",
    "escalation",
    "plain_language",
    "on_question",
    "matches_expected",
]
REQUIRED_FIELDS = ["id", "category", "question", "expected_behavior"]

SOURCES_INTRO = (
    "These CMS publications are your only source of truth. Each page starts with a "
    '"[PDF page N of M]" marker; cite the printed page number shown in the page text.\n\n'
)

JUDGE_SYSTEM = """You are grading answers from a Medicare information assistant.
You get the user's question, a description of what a correct answer does, the assistant's
answer, and the CMS source documents the assistant was allowed to use. Grade the answer
against those; do not use outside knowledge about Medicare.

Score each criterion "pass", "fail", or "n/a", with a one-sentence reason:
- accurate: every factual claim is supported by the source documents (no invented figures,
  rules, or page claims).
- year_label: every dollar figure or threshold states its plan year, and a 2026 number is
  never presented as a 2027 number. n/a if the answer states no figures.
- citation: a source line names a real document from the sources, with a page consistent with
  the content. Accept printed page numbers or PDF page markers. n/a if must_cite is false.
- in_scope: stays in scope: no plan recommendations, no medical/legal/financial advice,
  out-of-scope requests are routed rather than answered.
- escalation: points to the expected official contact when one is expected. n/a if none.
- plain_language: plain words (about 8th-grade level) and short.
- on_question: answers only what was asked. Fail if it adds dollar figures, deadlines,
  penalties, or next steps the question did not call for. Items required by the expected
  behavior do not count against it.
- matches_expected: overall, does what the expected behavior describes, including asking one
  clarifying question when that is expected.

Return ONLY a JSON object shaped like:
{"accurate": {"verdict": "pass", "reason": "..."}, "year_label": {...}, "citation": {...},
 "in_scope": {...}, "escalation": {...}, "plain_language": {...}, "on_question": {...},
 "matches_expected": {...},
 "failure_types": []}
failure_types holds zero or more of: wrong_figure, wrong_year, missing_citation, out_of_scope,
missed_escalation, unneeded_or_missing_clarification, over_answering, other."""

DOC_NAMES = r"(?:Medicare\s*&\s*You|Get Ready for Medicare|Medicare:\s*Getting Started|2026 Medicare Costs)"
SOURCE_LINE = re.compile(DOC_NAMES + r"[^\n]{0,60}?\bpp?\.?\s*\d", re.I)
DOLLAR = re.compile(r"\$\s?\d")
NEXT_STEP = re.compile(
    r"1-800|Medicare\.gov|shiphelp|\bSHIP\b|\bnext step\b|SSA\.gov|Social Security (?:at|office)", re.I
)


# ---------------------------------------------------------------- loading

def load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_prompt(path: Path) -> tuple[str, str, str]:
    """Return (prompt text, version label, short hash). Uses the first fenced block."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^```[^\n]*\n(.*?)^```", text, re.S | re.M)
    prompt = (m.group(1) if m else text).strip()
    v = re.search(r"Draft (v[\d.]+)", text)
    return prompt, (v.group(1) if v else "unversioned"), hashlib.sha1(prompt.encode()).hexdigest()[:8]


def load_sources(sources_dir: Path, figures: Path) -> tuple[str, list[str]]:
    names, parts = [], []
    for f in sorted(sources_dir.glob("*.txt")):
        names.append(f.name)
        parts.append(f'<document file="{f.name}">\n{f.read_text(encoding="utf-8").strip()}\n</document>')
    if figures.exists():
        names.append(figures.name)
        parts.append(f'<figures_registry file="{figures.name}">\n{figures.read_text(encoding="utf-8").strip()}\n</figures_registry>')
    return "\n\n".join(parts), names


def load_questions(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        sys.exit(f"{path}: expected a YAML list of questions")
    seen = set()
    for i, q in enumerate(data, 1):
        missing = [f for f in REQUIRED_FIELDS if not q.get(f)]
        if missing:
            sys.exit(f"{path.name}: entry {i} is missing {missing}")
        if q["id"] in seen:
            sys.exit(f"{path.name}: duplicate id {q['id']}")
        seen.add(q["id"])
    return data


# ---------------------------------------------------------------- checks

def escalation_present(esc: str, answer: str) -> bool:
    a = answer.lower()
    cands = {esc.lower(), esc.split("(")[0].strip().lower()}
    cands.update(x.lower() for x in re.findall(r"\(([^)]+)\)", esc))
    return any(c and c in a for c in cands)


def deterministic_checks(q: dict, answer: str) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    if q.get("must_cite", True):
        checks["has_source_line"] = bool(SOURCE_LINE.search(answer))
    year = str(q.get("expected_year", "n/a"))
    if year.isdigit():
        checks["mentions_expected_year"] = year in answer
    if q.get("escalation"):
        checks["has_escalation"] = escalation_present(str(q["escalation"]), answer)
    if q.get("forbid_dollar_figures"):
        checks["no_dollar_figures"] = not DOLLAR.search(answer)
    if q.get("forbid_next_step"):
        checks["no_next_step"] = not NEXT_STEP.search(answer)
    for text in q.get("forbid_text") or []:
        checks[f"no_text:{text}"] = text.lower() not in answer.lower()
    return checks


def extract_json(text: str) -> dict | None:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def failed_set(result: dict) -> set[str]:
    out = {f"check:{k}" for k, ok in result["checks"].items() if not ok}
    out |= {f"judge:{k}" for k, v in result["judge"].items() if isinstance(v, dict) and v.get("verdict") == "fail"}
    if result.get("judge_error"):
        out.add("judge:error")
    return out


# ---------------------------------------------------------------- API

class Usage:
    def __init__(self) -> None:
        self.t = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}

    def add(self, u) -> None:
        self.t["input"] += getattr(u, "input_tokens", 0) or 0
        self.t["output"] += getattr(u, "output_tokens", 0) or 0
        self.t["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        self.t["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0


def call(client, model: str, system: list[dict], user: str, max_tokens: int, usage: Usage) -> str:
    resp = client.messages.create(
        model=model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}]
    )
    usage.add(resp.usage)
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def cached_block(text: str) -> dict:
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def judge_answer(client, model: str, judge_system: list[dict], q: dict, answer: str, usage: Usage) -> tuple[dict, str | None]:
    payload = {
        "question": q["question"],
        "expected_behavior": q["expected_behavior"],
        "must_cite": q.get("must_cite", True),
        "expected_year": q.get("expected_year", "n/a"),
        "escalation": q.get("escalation"),
        "notes": q.get("notes", ""),
        "assistant_answer": answer,
    }
    raw = ""
    for _ in range(2):
        raw = call(client, model, judge_system, json.dumps(payload, indent=2), 1500, usage)
        obj = extract_json(raw)
        if obj is not None:
            judge = {c: obj.get(c, {"verdict": "n/a", "reason": "not returned"}) for c in CRITERIA}
            judge["failure_types"] = obj.get("failure_types", [])
            for c in CRITERIA:
                v = str(judge[c].get("verdict", "n/a")).lower()
                judge[c]["verdict"] = v if v in ("pass", "fail", "n/a") else "n/a"
            return judge, None
    return {}, raw[:500]


# ---------------------------------------------------------------- reporting

def compare(prev: dict, cur: dict) -> dict:
    pmap = {r["id"]: r for r in prev["results"]}
    out = {"fixed": [], "regressed": [], "flips": [], "new": []}
    for r in cur["results"]:
        p = pmap.get(r["id"])
        if p is None:
            out["new"].append(r["id"])
            continue
        if p["passed"] and not r["passed"]:
            out["regressed"].append(r["id"])
        elif r["passed"] and not p["passed"]:
            out["fixed"].append(r["id"])
        pf, cf = failed_set(p), failed_set(r)
        if pf != cf:
            out["flips"].append((r["id"], sorted(cf - pf), sorted(pf - cf)))
    return out


def render_md(meta: dict, results: list[dict], cmp: dict | None, prev_meta: dict | None) -> str:
    n = len(results)
    passed = sum(r["passed"] for r in results)
    L = [
        f"# Eval run: {meta['label']}",
        "",
        f"- Run: {meta['timestamp']}",
        f"- Prompt: {meta['prompt_version']} (hash `{meta['prompt_hash']}`)",
        f"- Answer model: {meta['model']} | Judge model: {meta['judge_model']}",
        f"- Sources: {', '.join(meta['sources'])}",
        f"- **Passed: {passed}/{n}**",
        "",
    ]
    if cmp and prev_meta:
        same = prev_meta["prompt_hash"] == meta["prompt_hash"]
        L += [
            f"## Changes vs previous run ({prev_meta['label']}, prompt `{prev_meta['prompt_hash']}`)",
            "",
        ]
        if same:
            L += ["Same prompt as the previous run, so any difference below is run-to-run noise.", ""]
        L += [
            f"- Fixed: {', '.join(cmp['fixed']) or 'none'}",
            f"- Regressed: {', '.join(cmp['regressed']) or 'none'}",
            f"- New questions: {', '.join(cmp['new']) or 'none'}",
        ]
        for qid, newly, cleared in cmp["flips"]:
            L.append(f"- `{qid}`: newly failing {newly or 'none'}; cleared {cleared or 'none'}")
        L.append("")
    L += ["## Results", "", "| id | category | result | failed |", "|---|---|---|---|"]
    for r in results:
        L.append(f"| {r['id']} | {r['category']} | {'PASS' if r['passed'] else 'FAIL'} | {', '.join(sorted(failed_set(r))) or ''} |")
    counts: dict[str, int] = {}
    for r in results:
        for f in r.get("failure_types", []):
            counts[f] = counts.get(f, 0) + 1
    if counts:
        L += ["", "## Failures by type", ""] + [f"- {k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]
    bad = [r for r in results if not r["passed"]]
    if bad:
        L += ["", "## Failure details", ""]
        for r in bad:
            L += [f"### {r['id']}", "", f"**Question:** {r['question']}", "", "**Answer:**", ""]
            L += ["> " + line for line in r["answer"].splitlines()] + [""]
            for k, ok in r["checks"].items():
                if not ok:
                    L.append(f"- check failed: `{k}`")
            for c in CRITERIA:
                v = r["judge"].get(c)
                if isinstance(v, dict) and v.get("verdict") == "fail":
                    L.append(f"- judge `{c}`: {v.get('reason', '')}")
            if r.get("judge_error"):
                L.append(f"- judge output could not be parsed: {r['judge_error']}")
            L.append("")
    u = meta["usage"]
    L += [
        "## Token usage",
        "",
        f"input {u['input']:,} | cache write {u['cache_write']:,} | cache read {u['cache_read']:,} | output {u['output']:,}",
        "",
    ]
    return "\n".join(L)


# ---------------------------------------------------------------- main

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prompt", type=Path, default=ROOT / "prompts" / "system_prompt.md")
    ap.add_argument("--questions", type=Path, default=ROOT / "eval" / "questions.yaml")
    ap.add_argument("--sources-dir", type=Path, default=ROOT / "data" / "sources")
    ap.add_argument("--figures", type=Path, default=ROOT / "data" / "figures.yaml")
    ap.add_argument("--results-dir", type=Path, default=ROOT / "eval" / "results")
    ap.add_argument("--ids", help="comma-separated question ids to run")
    ap.add_argument("--label", help="name for this run (default: prompt version)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="answer model")
    ap.add_argument("--judge-model", help="judge model (default: same as --model)")
    ap.add_argument("--baseline", type=Path, help="results.json to compare against (default: previous latest.json)")
    ap.add_argument("--no-baseline", action="store_true")
    ap.add_argument("--no-judge", action="store_true", help="deterministic checks only (cheaper)")
    ap.add_argument("--dry-run", action="store_true", help="validate inputs and show sizes; no API calls")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    load_env()

    prompt, version, phash = load_prompt(args.prompt)
    sources, source_names = load_sources(args.sources_dir, args.figures)
    questions = load_questions(args.questions)
    if args.ids:
        wanted = [i.strip() for i in args.ids.split(",") if i.strip()]
        unknown = set(wanted) - {q["id"] for q in questions}
        if unknown:
            sys.exit(f"Unknown question id(s): {', '.join(sorted(unknown))}")
        questions = [q for q in questions if q["id"] in wanted]

    print(f"Prompt {version} (hash {phash}), {len(prompt):,} chars")
    print(f"Sources: {len(source_names)} file(s), {len(sources):,} chars (~{len(sources) // 4:,} tokens)")
    print(f"Questions: {len(questions)}")

    if args.dry_run:
        if not sources:
            print("WARNING: no source text found. Run: python3 data/extract_sources.py <pdf folder>")
        print("Dry run complete; nothing was sent to the API.")
        return
    if not sources:
        sys.exit("No source text in data/sources/. Run: python3 data/extract_sources.py <pdf folder>")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.")
    try:
        import anthropic
    except ImportError:
        sys.exit("Missing dependency: pip3 install -r requirements.txt")

    client = anthropic.Anthropic()
    judge_model = args.judge_model or args.model
    answer_system = [{"type": "text", "text": prompt}, cached_block(SOURCES_INTRO + sources)]
    judge_system = [{"type": "text", "text": JUDGE_SYSTEM}, cached_block("SOURCE DOCUMENTS\n\n" + sources)]

    latest = args.results_dir / "latest.json"
    baseline_path = None if args.no_baseline else (args.baseline or (latest if latest.exists() else None))
    prev = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path else None

    usage = Usage()
    results = []
    for n, q in enumerate(questions, 1):
        answer = call(client, args.model, answer_system, q["question"], 1500, usage)
        checks = deterministic_checks(q, answer)
        result = {"id": q["id"], "category": q["category"], "question": q["question"], "answer": answer,
                  "checks": checks, "judge": {}, "failure_types": []}
        if not args.no_judge:
            judge, err = judge_answer(client, judge_model, judge_system, q, answer, usage)
            result["judge"] = judge
            result["failure_types"] = judge.get("failure_types", [])
            if err:
                result["judge_error"] = err
        result["passed"] = not failed_set(result)
        results.append(result)
        bad = ", ".join(sorted(failed_set(result)))
        print(f"[{n}/{len(questions)}] {q['id']}: {'PASS' if result['passed'] else 'FAIL'}{' (' + bad + ')' if bad else ''}")

    now = dt.datetime.now().astimezone()
    label = args.label or version
    meta = {
        "label": label, "timestamp": now.isoformat(timespec="seconds"), "prompt_version": version,
        "prompt_hash": phash, "model": args.model, "judge_model": "none" if args.no_judge else judge_model,
        "sources": source_names, "questions_file": str(args.questions.name), "usage": usage.t,
    }
    cur = {"meta": meta, "results": results}
    cmp = compare(prev, cur) if prev else None

    slug = re.sub(r"[^a-zA-Z0-9]+", "-", label).strip("-") or "run"
    run_dir = args.results_dir / f"{now.strftime('%Y%m%d-%H%M%S')}_{slug}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "results.json").write_text(json.dumps(cur, indent=2), encoding="utf-8")
    (run_dir / "results.md").write_text(render_md(meta, results, cmp, prev["meta"] if prev else None), encoding="utf-8")
    shutil.copy(run_dir / "results.json", args.results_dir / "latest.json")
    shutil.copy(run_dir / "results.md", args.results_dir / "latest.md")

    passed = sum(r["passed"] for r in results)
    print(f"\nPassed {passed}/{len(results)}. Report: {run_dir / 'results.md'}")
    if cmp:
        print(f"vs previous: fixed {len(cmp['fixed'])}, regressed {len(cmp['regressed'])}")
    print("Tokens:", usage.t)


if __name__ == "__main__":
    main()
