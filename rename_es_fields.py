#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Rename legacy Phenobase Elasticsearch fields in place."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from datetime import datetime, timezone


DEFAULT_HOST = "149.165.170.158"
DEFAULT_PORT = 8081
DEFAULT_SCHEME = "http"
DEFAULT_INDEX = "phenobase2"
DEFAULT_BASE_URL = "https://biscicol.org/phenobase/api/v1/query"
DEFAULT_REPORT = "downloads/field_rename_report.json"
TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504}

MAPPING_PROPERTIES = {
    "standardizedFamily": {"type": "keyword"},
    "collectionMethod": {"type": "keyword"},
    "accuracyFamily": {"type": "text"},
    "accuracyIncludingUncertainFamily": {"type": "text"},
}


def normalize_base_url(base_url):
    return str(base_url or "").rstrip("/")


def build_es_url(args, path, params=None):
    params = params or {}
    query_string = urllib.parse.urlencode(params)
    suffix = f"?{query_string}" if query_string else ""
    if args.base_url:
        return f"{normalize_base_url(args.base_url)}//{path.lstrip('/')}{suffix}"
    return f"{args.scheme}://{args.host}:{args.port}/{path.lstrip('/')}{suffix}"


def request_json(args, method, path, payload=None, params=None, content_type="application/json", label="ES request"):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        if isinstance(payload, bytes):
            data = payload
        else:
            data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = content_type

    attempts = max(args.retries, 0) + 1
    last_exc = None
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            build_es_url(args, path, params=params),
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=args.request_timeout) as response:
                text = response.read().decode("utf-8")
                return json.loads(text) if text else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last_exc = exc
            retryable = exc.code in TRANSIENT_HTTP_CODES
            detail = f"HTTP {exc.code}: {body[:500]}"
        except (urllib.error.URLError, TimeoutError) as exc:
            last_exc = exc
            retryable = True
            detail = str(exc)

        if not retryable or attempt >= attempts:
            raise RuntimeError(f"{label} failed: {detail}") from last_exc

        sleep_seconds = min(args.retry_sleep * attempt, 120)
        print(
            f"{label} failed ({detail}); retry {attempt}/{attempts - 1} after {sleep_seconds:g}s.",
            file=sys.stderr,
            flush=True,
        )
        time.sleep(sleep_seconds)

    raise RuntimeError(f"{label} failed") from last_exc


def exists_query(field):
    return {"exists": {"field": field}}


def legacy_accuracy_family_query():
    return {
        "bool": {
            "must": [exists_query("accuracyFamily")],
            "must_not": [exists_query("accuracyIncludingUncertainFamily")],
        }
    }


def build_rename_query(include_legacy_accuracy_family=False):
    should = [
        exists_query("gbifFamily"),
        exists_query("basisOfRecord"),
        exists_query("accuracyExcludingUncertainFamily"),
    ]
    if include_legacy_accuracy_family:
        should.append(legacy_accuracy_family_query())
    return {"bool": {"should": should, "minimum_should_match": 1}}


def count_queries(include_legacy_accuracy_family=False):
    queries = OrderedDict(
        [
            ("gbifFamily", exists_query("gbifFamily")),
            ("basisOfRecord", exists_query("basisOfRecord")),
            ("accuracyExcludingUncertainFamily", exists_query("accuracyExcludingUncertainFamily")),
            ("standardizedFamily", exists_query("standardizedFamily")),
            ("collectionMethod", exists_query("collectionMethod")),
            ("accuracyFamily", exists_query("accuracyFamily")),
            ("accuracyIncludingUncertainFamily", exists_query("accuracyIncludingUncertainFamily")),
        ]
    )
    if include_legacy_accuracy_family:
        queries["legacyAccuracyFamilyWithoutIncluding"] = legacy_accuracy_family_query()
    return queries


def build_rename_script(include_legacy_accuracy_family=False):
    statements = [
        """
if (ctx._source.containsKey('gbifFamily')) {
  def oldValue = ctx._source.remove('gbifFamily');
  if (oldValue != null && oldValue != '' &&
      (!ctx._source.containsKey('standardizedFamily') ||
       ctx._source.standardizedFamily == null ||
       ctx._source.standardizedFamily == '')) {
    ctx._source.standardizedFamily = oldValue;
  }
}
""".strip(),
        """
if (ctx._source.containsKey('basisOfRecord')) {
  def oldValue = ctx._source.remove('basisOfRecord');
  if (oldValue != null && oldValue != '' &&
      (!ctx._source.containsKey('collectionMethod') ||
       ctx._source.collectionMethod == null ||
       ctx._source.collectionMethod == '')) {
    ctx._source.collectionMethod = oldValue;
  }
}
""".strip(),
    ]

    if include_legacy_accuracy_family:
        statements.append(
            """
if (ctx._source.containsKey('accuracyFamily') &&
    !ctx._source.containsKey('accuracyIncludingUncertainFamily')) {
  def oldValue = ctx._source.remove('accuracyFamily');
  if (oldValue != null && oldValue != '') {
    ctx._source.accuracyIncludingUncertainFamily = oldValue;
  }
}
""".strip()
        )

    statements.append(
        """
if (ctx._source.containsKey('accuracyExcludingUncertainFamily')) {
  def oldValue = ctx._source.remove('accuracyExcludingUncertainFamily');
  if (oldValue != null && oldValue != '' &&
      (!ctx._source.containsKey('accuracyFamily') ||
       ctx._source.accuracyFamily == null ||
       ctx._source.accuracyFamily == '')) {
    ctx._source.accuracyFamily = oldValue;
  }
}
""".strip()
    )
    return "\n\n".join(statements)


def build_update_body(include_legacy_accuracy_family=False):
    return {
        "script": {
            "lang": "painless",
            "source": build_rename_script(include_legacy_accuracy_family),
        },
        "query": build_rename_query(include_legacy_accuracy_family),
    }


def count_matching_docs(args, query):
    response = request_json(args, "POST", f"{args.index}/_count", {"query": query}, label="count")
    return int(response.get("count") or 0)


def collect_counts(args):
    counts = OrderedDict()
    for label, query in count_queries(args.migrate_legacy_accuracy_family).items():
        counts[label] = count_matching_docs(args, query)
    counts["renameQueryTotal"] = count_matching_docs(
        args,
        build_rename_query(args.migrate_legacy_accuracy_family),
    )
    return counts


def ensure_mapping(args):
    return request_json(
        args,
        "POST",
        f"{args.index}/_mapping",
        {"properties": MAPPING_PROPERTIES},
        label="mapping update",
    )


def run_update_by_query(args):
    params = {
        "conflicts": "proceed",
        "wait_for_completion": str(bool(args.wait)).lower(),
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
        build_update_body(args.migrate_legacy_accuracy_family),
        params=params,
        label="field rename update_by_query",
    )


def get_task(args, task_id):
    return request_json(args, "GET", f"_tasks/{task_id}", label="task status")


def wait_for_task(args, task_id):
    while True:
        response = get_task(args, task_id)
        task = response.get("task") or {}
        status = task.get("status") or {}
        updated = int(status.get("updated") or 0)
        total = int(status.get("total") or 0)
        version_conflicts = int(status.get("version_conflicts") or 0)
        print(
            f"task={task_id} updated={updated:,}/{total:,} version_conflicts={version_conflicts:,}",
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
    parser.add_argument(
        "--migrate-legacy-accuracy-family",
        action="store_true",
        help=(
            "Also move the legacy accuracyFamily field to accuracyIncludingUncertainFamily. "
            "Use for the one-time migration of the current legacy index."
        ),
    )
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--slices", default="auto")
    parser.add_argument("--requests-per-second", type=float, default=-1)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--wait", action="store_true", help="Wait for update_by_query to finish.")
    parser.add_argument("--poll-interval", type=float, default=30)
    parser.add_argument("--request-timeout", type=float, default=120)
    parser.add_argument("--retries", type=int, default=8)
    parser.add_argument("--retry-sleep", type=float, default=10)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    return parser.parse_args()


def main():
    args = parse_args()
    started = time.time()

    counts_before = collect_counts(args)
    report = OrderedDict(
        [
            ("generatedAtUtc", datetime.now(timezone.utc).replace(microsecond=0).isoformat()),
            ("apply", args.apply),
            ("index", args.index),
            ("migrateLegacyAccuracyFamily", args.migrate_legacy_accuracy_family),
            ("countsBefore", counts_before),
        ]
    )

    if args.apply:
        report["mappingResponse"] = ensure_mapping(args)
        update_response = run_update_by_query(args)
        report["updateByQueryResponse"] = update_response
        task_id = update_response.get("task")
        if task_id and args.wait:
            report["taskResponse"] = wait_for_task(args, task_id)
        if args.wait:
            report["countsAfter"] = collect_counts(args)

    report["elapsedSeconds"] = round(time.time() - started, 3)
    write_report(args.report, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"Wrote report to {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
