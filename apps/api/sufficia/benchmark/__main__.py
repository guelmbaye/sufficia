"""CLI: ``python -m sufficia.benchmark [--cases DIR] [--reports DIR] [--json]``.

Exits 0 only when every case matches its committed expectation, every explanation is faithful,
every replay matches, and no authorization exists for a non-AUTHORIZE decision.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .report import benchmark_json, console_summary, write_reports, _json_default
from .runner import BenchmarkError, default_benchmark_dir, default_reports_dir, run_benchmark


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sufficia.benchmark", description="Run the canonical SUFFICIA benchmark.")
    parser.add_argument("--cases", type=Path, default=None,
                        help="benchmark directory (containing cases/, evidence/, contracts/, actions/) or its cases/ dir "
                             f"(default: {default_benchmark_dir()})")
    parser.add_argument("--reports", type=Path, default=None,
                        help=f"directory for the Markdown/JSON reports (default: {default_reports_dir()})")
    parser.add_argument("--no-reports", action="store_true", help="do not write report files")
    parser.add_argument("--json", action="store_true", help="print the full benchmark JSON report on stdout")
    parser.add_argument("--strict", action="store_true", help="treat advisories as failures")
    parser.add_argument("--no-cross-process", action="store_true", help="replay in-process only")
    args = parser.parse_args(argv)

    try:
        run = run_benchmark(args.cases, strict=args.strict, cross_process=not args.no_cross_process)
    except BenchmarkError as exc:
        print(f"benchmark error: {exc}", file=sys.stderr)
        return 2
    written = {}
    if not args.no_reports:
        written = write_reports(run, args.reports or default_reports_dir())
    if args.json:
        print(json.dumps(benchmark_json(run), indent=2, default=_json_default))
    else:
        print(console_summary(run))
        for path in written.values():
            print(f"  wrote {path}")
    return 0 if run.ok else 1


if __name__ == "__main__":
    sys.exit(main())
