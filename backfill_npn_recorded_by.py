#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Backfill recordedBy and occurrenceID on existing NPN/NEON ES docs."""

from __future__ import annotations

import argparse
import calendar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, OrderedDict
from datetime import date, datetime, timedelta, timezone


NPN_OBSERVATIONS_URL = "https://services.usanpn.org/npn_portal/observations/getObservations.json"
DEFAULT_HOST = "149.165.170.158"
DEFAULT_PORT = 8081
DEFAULT_SCHEME = "http"
DEFAULT_INDEX = "phenobase2"
DEFAULT_BASE_URL = "https://biscicol.org/phenobase/api/v1/query"
DEFAULT_REPORT = "downloads/npn/npn_recorded_by_backfill_report.json"
RECORDED_BY_FIELDS = (
    "recordedBy",
    "recorded_by",
    "observer_id",
    "observerID",
    "observer_name",
    "observer",
    "user_id",
    "username",
    "person_id",
    "participant_id",
)
TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504}


def norm(value):
    return "" if value is None else str(value).strip()


def parse_date(value):
    return date.fromisoformat(norm(value))


def add_months(value, months):
    year = value.year + (value.month - 1 + months) // 12
    month = (value.month - 1 + months) % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def month_chunks(start_date, end_date, months=1):
    current = parse_date(start_date)
    end = parse_date(end_date)
    if current > end:
        raise RuntimeError("--start-date must be on or before --end-date")
    while current <= end:
        chunk_end = min(add_months(current, months) - timedelta(days=1), end)
        yield current.isoformat(), chunk_end.isoformat()
        current = chunk_end + timedelta(days=1)


def build_query_url(url, params=None):
    query = urllib.parse.urlencode(params or {}, doseq=True)
    return f"{url}?{query}" if query else url


def request_json(method, url, payload=None, timeout=120, content_type="application/json", retries=3, retry_sleep=5):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        if isinstance(payload, bytes):
            data = payload
        else:
            data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = content_type

    attempts = max(retries, 0) + 1
    last_exc = None
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8")
                return json.loads(text) if text else {}
        except urllib.error.HTTPError as exc:
            last_exc = exc
            retryable = exc.code in TRANSIENT_HTTP_CODES
            detail = f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError) as exc:
            last_exc = exc
            retryable = True
            detail = str(exc)

        if not retryable or attempt >= attempts:
            raise last_exc

        sleep_seconds = min(retry_sleep * attempt, 120)
        print(f"Request failed ({detail}); retry {attempt}/{attempts - 1} after {sleep_seconds:g}s.", file=sys.stderr, flush=True)
        time.sleep(sleep_seconds)

    raise last_exc


def fetch_npn_observations(start_date, end_date, args):
    params = {
        "start_date": start_date,
        "end_date": end_date,
        "request_src": "phenobase_recorded_by_backfill",
        "additional_field": ["observer_id"],
    }
    url = build_query_url(NPN_OBSERVATIONS_URL, params)
    data = request_json(
        "GET",
        url,
        timeout=args.request_timeout,
        retries=args.retries,
        retry_sleep=args.retry_sleep,
    )
    return data if isinstance(data, list) else []


def recorded_by(observation):
    for field in RECORDED_BY_FIELDS:
        value = norm(observation.get(field))
        if value:
            return value
    return ""


def action_from_observation(observation, index_name):
    observation_id = norm(observation.get("observation_id"))
    if not observation_id:
        return None, "missing_observation_id"

    doc = {"occurrenceID": observation_id}
    value = recorded_by(observation)
    if value:
        doc["recordedBy"] = value

    return {
        "_index": index_name,
        "_id": f"npn:{observation_id}",
        "doc": doc,
    }, ""


def normalize_base_url(base_url):
    return str(base_url or "").rstrip("/")


def es_url(args, path):
    if args.base_url:
        return f"{normalize_base_url(args.base_url)}//{path.lstrip('/')}"
    return f"{args.scheme}://{args.host}:{args.port}/{path.lstrip('/')}"


def bulk_payload(actions):
    lines = []
    for action in actions:
        lines.append(json.dumps({"update": {"_index": action["_index"], "_id": action["_id"]}}, separators=(",", ":")))
        lines.append(json.dumps({"doc": action["doc"]}, separators=(",", ":")))
    return ("\n".join(lines) + "\n").encode("utf-8")


def flush_bulk(actions, args, stats):
    if not actions:
        return

    stats["would_update"] += len(actions)
    if not args.apply:
        return

    response = request_json(
        "POST",
        es_url(args, "_bulk"),
        payload=bulk_payload(actions),
        timeout=args.request_timeout,
        content_type="application/x-ndjson",
        retries=args.retries,
        retry_sleep=args.retry_sleep,
    )
    for item in response.get("items") or []:
        meta = item.get("update") or {}
        status = int(meta.get("status") or 0)
        if 200 <= status < 300:
            stats["written"] += 1
        elif status == 404:
            stats["not_found"] += 1
        else:
            stats["write_errors"] += 1
            print(json.dumps(item, ensure_ascii=True), file=sys.stderr)


def write_report(path, args, stats):
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    report = OrderedDict(
        [
            ("generatedAtUtc", datetime.now(timezone.utc).replace(microsecond=0).isoformat()),
            ("apply", args.apply),
            ("index", args.index),
            ("startDate", args.start_date),
            ("endDate", args.end_date),
            ("stats", OrderedDict(sorted(stats.items()))),
        ]
    )
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=True)
        fh.write("\n")
    print(f"Wrote report to {path}")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("start_date", help="Start date in YYYY-MM-DD format")
    parser.add_argument("end_date", help="End date in YYYY-MM-DD format")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--scheme", default=DEFAULT_SCHEME)
    parser.add_argument(
        "--base-url",
        default="",
        help=f"Phenobase query proxy URL. When set, use the proxy instead of host/port (example: {DEFAULT_BASE_URL}).",
    )
    parser.add_argument("--index", default=DEFAULT_INDEX)
    parser.add_argument("--apply", action="store_true", help="Write ES updates. Default is dry-run.")
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--chunk-months", type=int, default=1)
    parser.add_argument("--request-timeout", type=float, default=120)
    parser.add_argument("--retries", type=int, default=6)
    parser.add_argument("--retry-sleep", type=float, default=10)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.batch_size <= 0:
        raise RuntimeError("--batch-size must be greater than 0")
    if args.chunk_months <= 0:
        raise RuntimeError("--chunk-months must be greater than 0")

    stats = Counter()
    actions = []
    started = time.time()

    for chunk_start, chunk_end in month_chunks(args.start_date, args.end_date, args.chunk_months):
        observations = fetch_npn_observations(chunk_start, chunk_end, args)
        stats["chunks"] += 1
        stats["observations"] += len(observations)

        for observation in observations:
            action, skip_reason = action_from_observation(observation, args.index)
            if skip_reason:
                stats[skip_reason] += 1
                continue
            if "recordedBy" not in action["doc"]:
                stats["missing_recordedBy"] += 1
            actions.append(action)
            if len(actions) >= args.batch_size:
                flush_bulk(actions, args, stats)
                actions = []

        flush_bulk(actions, args, stats)
        actions = []
        elapsed = max(time.time() - started, 1e-6)
        print(
            f"{chunk_start} to {chunk_end}: observations={len(observations):,}; "
            f"would_update={stats['would_update']:,}; written={stats['written']:,}; "
            f"not_found={stats['not_found']:,}; {stats['observations'] / elapsed:,.0f} obs/s",
            flush=True,
        )

    write_report(args.report, args, stats)
    print(json.dumps(OrderedDict(sorted(stats.items())), indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
