"""``python -m docich.eval <command>`` — offline, side-effect-free eval base (#1308).

    python -m docich.eval build   --suite evals/comment/v1
    python -m docich.eval run     --suite evals/comment/v1 --candidate heuristic --split validation
    python -m docich.eval compare --suite evals/comment/v1 --base-jsonl base.jsonl --candidate-jsonl cand.jsonl
    python -m docich.eval report  --campaign runs/campaign.jsonl
    python -m docich.eval hillclimb --suite evals/comment/v1 --target comment-prompt --rounds 8 --dry-run

The CLI never contacts a provider, never mutates a file and refuses to touch
``sealed_test`` without the explicit ``--allow-sealed`` flag. Candidate outputs
are supplied either by the keyless local heuristic or by a JSONL file produced
elsewhere; the base runner itself holds no network client.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import (campaign as campaign_mod, candidates, contracts, hillclimb,
               report as report_mod, runner)
from . import load_suite


def parse_candidate(spec: str):
    if spec == "heuristic":
        return candidates.heuristic_candidate(), "heuristic"
    if spec.startswith("jsonl:"):
        path = spec[len("jsonl:"):]
        return candidates.jsonl_candidate(path), "jsonl:" + Path(path).name
    if spec.startswith("echo:"):
        return candidates.echo_candidate(spec[len("echo:"):]), "echo"
    raise contracts.ContractError("unknown_candidate")


def select_cases(suite: dict, name: str, allow_sealed: bool) -> list:
    by_split = suite["by_split"]
    critical = [{**case, "split": "critical"} for case in suite["critical"]]
    if name == "all":
        return list(by_split["train"]) + list(by_split["validation"]) + critical
    if name == contracts.SPLIT_SEALED:
        if not allow_sealed:
            raise contracts.ContractError("sealed_test_locked")
        return list(by_split[contracts.SPLIT_SEALED])
    if name == "critical":
        return critical
    if name not in by_split:
        raise contracts.ContractError("unknown_split")
    return list(by_split[name])


def _build(args) -> int:
    suite = load_suite(args.suite, seed=args.seed)
    manifest = suite["manifest"]
    split_manifest = dict(manifest["split"])
    if not args.with_case_ids:
        split_manifest.pop("case_ids", None)
    print(json.dumps({"manifest": {**manifest, "split": split_manifest}},
                     ensure_ascii=False, indent=2))
    return 0


def _run_command(args) -> int:
    suite = load_suite(args.suite, seed=args.seed)
    cases = select_cases(suite, args.split, args.allow_sealed)
    candidate, name = parse_candidate(args.candidate)
    run = runner.run_cases(cases, candidate, suite=suite["suite_manifest"]["suite"],
                           candidate_name=name, split=args.split,
                           allow_sealed=args.allow_sealed)
    base = None
    if args.base_candidate:
        base_fn, base_name = parse_candidate(args.base_candidate)
        base = runner.run_cases(cases, base_fn, suite=suite["suite_manifest"]["suite"],
                                candidate_name=base_name, split=args.split,
                                allow_sealed=args.allow_sealed)
    built = report_mod.build_report(run, base=base, include_case_outputs=args.include_outputs,
                                    seed=suite["suite_manifest"]["seed"])
    if args.report:
        report_mod.write_report(args.report, built)
    print(json.dumps(built, ensure_ascii=False, indent=2) if args.json
          else report_mod.render_text(built))
    return 0


def _compare(args) -> int:
    suite = load_suite(args.suite, seed=args.seed)
    cases = select_cases(suite, args.split, args.allow_sealed)
    base_fn, base_name = parse_candidate("jsonl:" + args.base_jsonl)
    cand_fn, cand_name = parse_candidate("jsonl:" + args.candidate_jsonl)
    base = runner.run_cases(cases, base_fn, suite=suite["suite_manifest"]["suite"],
                            candidate_name=base_name, split=args.split,
                            allow_sealed=args.allow_sealed)
    cand = runner.run_cases(cases, cand_fn, suite=suite["suite_manifest"]["suite"],
                            candidate_name=cand_name, split=args.split,
                            allow_sealed=args.allow_sealed)
    built = report_mod.build_report(cand, base=base, seed=suite["suite_manifest"]["seed"])
    if args.report:
        report_mod.write_report(args.report, built)
    print(json.dumps(built, ensure_ascii=False, indent=2) if args.json
          else report_mod.render_text(built))
    return 0


def _report(args) -> int:
    records = campaign_mod.read_experiments(args.campaign) if args.campaign.is_file() else []
    summary = campaign_mod.campaign_report(records)
    if not args.campaign.is_file():
        summary["note"] = "no_experiment_log"
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _hillclimb(args) -> int:
    suite = load_suite(args.suite, seed=args.seed)
    heuristic, name = candidates.heuristic_candidate(), "heuristic"

    def evaluate(cases):
        return runner.run_cases(cases, heuristic, suite=suite["suite_manifest"]["suite"],
                                candidate_name=name, split=None, allow_sealed=False)

    result = hillclimb.run_campaign(
        campaign_id=args.campaign_id, target=args.target,
        cases_by_split=suite["by_split"], split_manifest_hash=suite["manifest"]["split"]["split_manifest_hash"],
        generate=None, evaluate=evaluate, rounds=args.rounds, dry_run=True,
        experiment_path=args.experiment, suite=suite["suite_manifest"]["suite"],
        suite_version=suite["suite_manifest"]["suite"],
        rubric_version=suite["suite_manifest"]["rubric_version"],
        grader_versions=suite["suite_manifest"]["grader_versions"],
        seed=suite["suite_manifest"]["seed"],
        sealed_evaluate=(None if not args.sealed_final else
                         lambda cases: report_mod.build_report(
                             runner.run_cases(cases, heuristic,
                                              suite=suite["suite_manifest"]["suite"],
                                              candidate_name=name, split=contracts.SPLIT_SEALED,
                                              allow_sealed=True))),
    )
    print(json.dumps({key: result[key] for key in ("campaign_id", "dry_run", "report")},
                     ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich.eval", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_suite(ap):
        ap.add_argument("--suite", type=Path, required=True)
        ap.add_argument("--seed", type=int, default=None)

    build = sub.add_parser("build", help="validate a suite and print its manifest")
    add_suite(build)
    build.add_argument("--with-case-ids", action="store_true")
    build.set_defaults(func=_build)

    run = sub.add_parser("run", help="run one candidate over a split")
    add_suite(run)
    run.add_argument("--candidate", required=True)
    run.add_argument("--base-candidate")
    run.add_argument("--split", default=contracts.SPLIT_VALIDATION)
    run.add_argument("--allow-sealed", action="store_true")
    run.add_argument("--include-outputs", action="store_true")
    run.add_argument("--report", type=Path)
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=_run_command)

    compare = sub.add_parser("compare", help="paired compare two JSONL candidate outputs")
    add_suite(compare)
    compare.add_argument("--base-jsonl", required=True)
    compare.add_argument("--candidate-jsonl", required=True)
    compare.add_argument("--split", default=contracts.SPLIT_VALIDATION)
    compare.add_argument("--allow-sealed", action="store_true")
    compare.add_argument("--report", type=Path)
    compare.add_argument("--json", action="store_true")
    compare.set_defaults(func=_compare)

    report = sub.add_parser("report", help="summarise a campaign experiment log")
    report.add_argument("--campaign", type=Path, required=True)
    report.set_defaults(func=_report)

    climb = sub.add_parser("hillclimb", help="offline (dry-run) campaign loop")
    add_suite(climb)
    climb.add_argument("--target", required=True)
    climb.add_argument("--rounds", type=int, default=8)
    climb.add_argument("--campaign-id", default="campaign-local")
    climb.add_argument("--experiment", type=Path)
    climb.add_argument("--sealed-final", action="store_true")
    climb.set_defaults(func=_hillclimb)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except contracts.ContractError as exc:
        print("eval error: " + str(exc), file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError):
        print("eval error: unreadable input", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
