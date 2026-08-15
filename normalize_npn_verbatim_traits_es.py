#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Normalize existing NPN/NEON verbatimTrait values in Elasticsearch."""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import OrderedDict
from datetime import datetime, timezone

from rename_es_fields import request_json


DEFAULT_HOST = "149.165.170.158"
DEFAULT_PORT = 8081
DEFAULT_SCHEME = "http"
DEFAULT_INDEX = "phenobase2"
DEFAULT_BASE_URL = "https://biscicol.org/phenobase/api/v1/query"
DEFAULT_REPORT = "downloads/npn_verbatim_trait_normalize_report.json"
NPN_DATA_SOURCES = [
    "USA National Phenology Network",
    "National Ecological Observatory Network (USA)",
]


def build_query():
    return {
        "bool": {
            "filter": [
                {"terms": {"dataSource": NPN_DATA_SOURCES}},
                {"exists": {"field": "verbatimTrait"}},
            ]
        }
    }


def build_normalize_script():
    return """
if (!ctx._source.containsKey('verbatimTrait') || ctx._source.verbatimTrait == null) {
  ctx.op = 'noop';
  return;
}

String value = ctx._source.verbatimTrait.toString();
if (value.length() <= 4) {
  ctx.op = 'noop';
  return;
}

String statusSuffix = value.substring(value.length() - 4);
if (statusSuffix.equals(' (0)') || statusSuffix.equals(' (1)')) {
  String status = value.substring(value.length() - 2, value.length() - 1);
  ctx._source.verbatimTrait = value.substring(0, value.length() - 4) + '=' + status;
} else {
  ctx.op = 'noop';
}
""".strip()


def build_update_body():
    return {
        "script": {
            "lang": "painless",
            "source": build_normalize_script(),
        },
        "query": build_query(),
    }


def count_source_docs(args):
    response = request_json(args, "POST", f"{args.index}/_count", {"query": build_query()}, label="NPN/NEON count")
    return int(response.get("count") or 0)


def run_update_by_query(args):
    params = {
        "conflicts": "proceed",
        "wait_for_completion": "false",
        "slices": args.slices,
        "scroll_size": args.batch_size,
        "refresh": str(bool(args.refresh)).lower(),
    }
    if args.requests_per_second >= 0:
        params["requests_per_second"] = args.requests_per_second
    return request_json(
        args,
        "POST",
        f"{args.index}/_update_by_query",
        build_update_body(),
        params=params,
        label="NPN/NEON verbatimTrait update_by_query",
    )


def get_task(args, task_id):
    return request_json(args, "GET", f"_tasks/{task_id}", label="task status")


def wait_for_task(args, task_id):
    while True:
        try:
            response = get_task(args, task_id)
        except RuntimeError as err:
            detail = str(err)
            if "isn't running and hasn't stored its results" in detail:
                return {"completed": True, "resultUnavailable": True, "message": detail}
            raise
        task = response.get("task") or {}
        status = task.get("status") or {}
        updated = int(status.get("updated") or 0)
        noops = int(status.get("noops") or 0)
        total = int(status.get("total") or 0)
        version_conflicts = int(status.get("version_conflicts") or 0)
        print(
            f"task={task_id} updated={updated:,} noops={noops:,} total={total:,} "
            f"version_conflicts={version_conflicts:,}",
            flush=True,
        )
        if response.get("completed"):
            return response
        time.sleep(max(args.poll_interval, 1))


def write_report(path, report):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=True)
        fh.write("\n")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--scheme", default=DEFAULT_SCHEME)
    parser.add_argument(
        "--base-url",
        default="",
        help=f"Phenobase query proxy URL. When set, use the proxy instead of host/port (example: {DEFAULT_BASE_URL}).",
    )
    parser.add_argument("--index", default=DEFAULT_INDEX)
    parser.add_argument("--apply", action="store_true", help="Write updates to Elasticsearch. Default is dry-run.")
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--slices", default="auto")
    parser.add_argument("--requests-per-second", type=float, default=-1)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--wait", action="store_true", help="Wait for update_by_query to finish.")
    parser.add_argument("--task-id", default="", help="Poll an existing update_by_query task instead of starting one.")
    parser.add_argument("--poll-interval", type=float, default=30)
    parser.add_argument("--request-timeout", type=float, default=120)
    parser.add_argument("--retries", type=int, default=8)
    parser.add_argument("--retry-sleep", type=float, default=10)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    return parser.parse_args()


def main():
    args = parse_args()
    started = time.time()
    docs_to_inspect = None if args.task_id else count_source_docs(args)
    report = OrderedDict(
        [
            ("generatedAtUtc", datetime.now(timezone.utc).replace(microsecond=0).isoformat()),
            ("apply", args.apply),
            ("index", args.index),
            ("dataSources", NPN_DATA_SOURCES),
            ("docsToInspect", docs_to_inspect),
        ]
    )

    if args.task_id:
        report["taskId"] = args.task_id
        report["taskResponse"] = wait_for_task(args, args.task_id) if args.wait else get_task(args, args.task_id)
    elif args.apply:
        update_response = run_update_by_query(args)
        report["updateByQueryResponse"] = update_response
        task_id = update_response.get("task")
        if task_id and args.wait:
            report["taskResponse"] = wait_for_task(args, task_id)

    report["elapsedSeconds"] = round(time.time() - started, 3)
    write_report(args.report, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"Wrote report to {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
