#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Build or extend the scientificName -> GBIF family cache from Elasticsearch."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, OrderedDict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone

from gbif_family import CACHE_FIELDS, DEFAULT_GBIF_CACHE, GBIF_MATCH_URL, cache_key, normalize_name


DEFAULT_HOST = "149.165.170.158"
DEFAULT_PORT = 8081
DEFAULT_SCHEME = "http"
DEFAULT_INDEX = "phenobase2"
DEFAULT_BASE_URL = "https://biscicol.org/phenobase/api/v1/query"
DEFAULT_NAMES_OUTPUT = "downloads/taxonomy_family_compare/es_scientific_names_missing_gbif.csv"
DEFAULT_REPORT = "downloads/taxonomy_family_compare/gbif_family_cache_report.json"
TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504}


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

    attempts = max(args.es_retries, 0) + 1
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
            last_exc = exc
            retryable = exc.code in TRANSIENT_HTTP_CODES
            detail = f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:500]}"
        except (urllib.error.URLError, TimeoutError) as exc:
            last_exc = exc
            retryable = True
            detail = str(exc)

        if not retryable or attempt >= attempts:
            raise RuntimeError(f"{label} failed: {detail}") from last_exc

        sleep_seconds = min(args.es_retry_sleep * attempt, 120)
        print(
            f"{label} failed ({detail}); retry {attempt}/{attempts - 1} after {sleep_seconds:g}s.",
            file=sys.stderr,
            flush=True,
        )
        time.sleep(sleep_seconds)

    raise RuntimeError(f"{label} failed") from last_exc


def build_query(args):
    must = [{"exists": {"field": "scientificName"}}]
    if args.query.strip() not in {"", "*"}:
        must.append({"query_string": {"query": args.query, "analyze_wildcard": True}})

    query = {"bool": {"must": must}}
    if not args.include_existing_gbif_family:
        query["bool"]["must_not"] = [{"exists": {"field": "gbifFamily"}}]
    return query


def get_total_hits(response):
    total = ((response or {}).get("hits") or {}).get("total", 0)
    if isinstance(total, dict):
        return int(total.get("value") or 0)
    return int(total or 0)


def add_name(counts, scientific_name, count=1):
    name = normalize_name(scientific_name)
    if name:
        counts[name] += int(count or 0)


def collect_names_composite(args):
    counts = Counter()
    after = None
    pages = 0

    while True:
        composite = {
            "size": args.composite_size,
            "sources": [
                {
                    "scientificName": {
                        "terms": {
                            "field": args.name_field,
                        }
                    }
                }
            ],
        }
        if after:
            composite["after"] = after

        body = {
            "size": 0,
            "track_total_hits": 1,
            "query": build_query(args),
            "aggs": {
                "names": {
                    "composite": composite,
                }
            },
        }
        response = request_json(
            args,
            "POST",
            f"{args.index}/_search",
            body,
            label="composite scientificName aggregation",
        )
        if response.get("error"):
            raise RuntimeError(json.dumps(response["error"], ensure_ascii=True))

        agg = (response.get("aggregations") or {}).get("names") or {}
        buckets = agg.get("buckets") or []
        pages += 1
        total_hits = get_total_hits(response)

        if pages == 1 and not buckets and total_hits > 0:
            raise RuntimeError(
                f"{args.name_field} returned no aggregation buckets for a query with matching records"
            )

        for bucket in buckets:
            add_name(counts, (bucket.get("key") or {}).get("scientificName"), bucket.get("doc_count", 0))
            if args.limit_names and len(counts) >= args.limit_names:
                break

        print(
            f"ES composite page {pages}: unique names={len(counts):,}",
            flush=True,
        )

        if args.limit_names and len(counts) >= args.limit_names:
            break
        after = agg.get("after_key")
        if not buckets or not after:
            break

    if args.limit_names:
        return Counter(dict(counts.most_common(args.limit_names)))
    return counts


def collect_names_scroll(args):
    counts = Counter()
    scroll_id = None
    pages = 0
    inspected = 0
    body = {
        "size": args.batch_size,
        "sort": ["_doc"],
        "_source": ["scientificName"],
        "query": build_query(args),
    }
    response = request_json(
        args,
        "POST",
        f"{args.index}/_search",
        body,
        params={"scroll": args.scroll},
        label="initial scientificName scroll",
    )
    scroll_id = response.get("_scroll_id")

    try:
        while True:
            hits = ((response or {}).get("hits") or {}).get("hits") or []
            if not hits:
                break

            pages += 1
            for hit in hits:
                inspected += 1
                add_name(counts, (hit.get("_source") or {}).get("scientificName"))
                if args.limit_names and len(counts) >= args.limit_names:
                    break

            print(
                f"ES scroll page {pages}: inspected={inspected:,} unique names={len(counts):,}",
                flush=True,
            )

            if args.limit_names and len(counts) >= args.limit_names:
                break
            if not scroll_id:
                break
            response = request_json(
                args,
                "POST",
                "_search/scroll",
                {"scroll": args.scroll, "scroll_id": scroll_id},
                label="scientificName scroll",
            )
            scroll_id = response.get("_scroll_id", scroll_id)
    finally:
        if scroll_id:
            try:
                request_json(
                    args,
                    "DELETE",
                    "_search/scroll",
                    {"scroll_id": [scroll_id]},
                    label="clear scientificName scroll",
                )
            except Exception:
                pass

    if args.limit_names:
        return Counter(dict(counts.most_common(args.limit_names)))
    return counts


def collect_names(args):
    if args.name_source == "scroll":
        return collect_names_scroll(args)
    if args.name_source == "composite":
        return collect_names_composite(args)

    try:
        return collect_names_composite(args)
    except Exception as exc:
        print(
            f"Composite aggregation failed on {args.name_field}: {exc}. Falling back to scroll.",
            file=sys.stderr,
            flush=True,
        )
        return collect_names_scroll(args)


def iter_csv_paths(paths):
    for path in paths:
        if os.path.isdir(path):
            for root, _, files in os.walk(path):
                for filename in sorted(files):
                    if filename.lower().endswith(".csv"):
                        yield os.path.join(root, filename)
        elif os.path.isfile(path):
            yield path
        else:
            print(f"Skipping missing CSV path: {path}", file=sys.stderr, flush=True)


def collect_names_csv(args):
    counts = Counter()
    rows = 0
    for path in iter_csv_paths(args.names_from_csv):
        file_rows = 0
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames or args.csv_name_field not in reader.fieldnames:
                print(
                    f"Skipping {path}: missing {args.csv_name_field} column",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            for row in reader:
                rows += 1
                file_rows += 1
                add_name(counts, row.get(args.csv_name_field))
                if args.limit_names and len(counts) >= args.limit_names:
                    break
        print(
            f"CSV {path}: rows={file_rows:,}; unique names={len(counts):,}",
            flush=True,
        )
        if args.limit_names and len(counts) >= args.limit_names:
            break

    print(f"CSV input rows={rows:,}; unique names={len(counts):,}", flush=True)
    if args.limit_names:
        return Counter(dict(counts.most_common(args.limit_names)))
    return counts


def write_names(path, counts):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["scientificName", "recordCount"])
        writer.writeheader()
        for scientific_name, count in sorted(counts.items(), key=lambda item: item[0].casefold()):
            writer.writerow({"scientificName": scientific_name, "recordCount": count})


def read_names(path, limit=0):
    counts = Counter()
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            add_name(counts, row.get("scientificName") or row.get("name"), row.get("recordCount") or 1)
            if limit and len(counts) >= limit:
                break
    return counts


def load_cache(path):
    cache = OrderedDict()
    if not path or not os.path.exists(path):
        return cache
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            name = row.get("scientificName") or row.get("name")
            key = cache_key(name)
            if key:
                normalized = normalize_name(name)
                clean = {field: row.get(field, "") for field in CACHE_FIELDS}
                clean["scientificName"] = normalized
                cache[key] = clean
    return cache


def write_cache(path, cache):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp_path = f"{path}.tmp"
    rows = sorted(cache.values(), key=lambda row: (row.get("scientificName") or "").casefold())
    with open(tmp_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CACHE_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in CACHE_FIELDS})
    os.replace(tmp_path, path)


def fetch_gbif_one(scientific_name, timeout, retries, user_agent):
    params = urllib.parse.urlencode({"name": scientific_name, "kingdom": "Plantae"})
    url = f"{GBIF_MATCH_URL}?{params}"
    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": user_agent, "Accept": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.load(response)
            return {
                "scientificName": scientific_name,
                "family": normalize_name(data.get("family")),
                "canonicalName": normalize_name(data.get("canonicalName")),
                "matchedScientificName": normalize_name(data.get("scientificName")),
                "status": normalize_name(data.get("status")),
                "matchType": normalize_name(data.get("matchType")),
                "confidence": data.get("confidence", ""),
                "usageKey": data.get("usageKey", ""),
                "error": "",
            }
        except Exception as exc:
            last_error = str(exc)
            if attempt < retries:
                time.sleep(min(2 ** attempt, 20))

    return {
        "scientificName": scientific_name,
        "family": "",
        "canonicalName": "",
        "matchedScientificName": "",
        "status": "",
        "matchType": "",
        "confidence": "",
        "usageKey": "",
        "error": last_error,
    }


def cache_needs_resolution(cache, name, args):
    row = cache.get(cache_key(name))
    if row is None:
        return True
    if args.retry_errors and row.get("error"):
        return True
    if args.retry_blank and not row.get("family"):
        return True
    return False


def resolve_cache(counts, args):
    cache = load_cache(args.gbif_cache)
    cache_entries_before = len(cache)
    names = [name for name in counts if cache_needs_resolution(cache, name, args)]
    total_missing = len(names)
    resolved = 0
    errors = 0
    with_family = 0

    print(
        f"GBIF cache entries={len(cache):,}; names needing GBIF={total_missing:,}",
        flush=True,
    )
    if args.skip_resolve or not names:
        return cache, {
            "cacheEntriesBefore": len(cache),
            "namesNeedingGbif": total_missing,
            "resolved": 0,
            "withFamily": sum(1 for row in cache.values() if row.get("family")),
            "errors": sum(1 for row in cache.values() if row.get("error")),
        }

    pending = {}
    names_iter = iter(names)

    def submit_next(executor):
        try:
            name = next(names_iter)
        except StopIteration:
            return False
        future = executor.submit(
            fetch_gbif_one,
            name,
            args.gbif_timeout,
            args.gbif_retries,
            args.user_agent,
        )
        pending[future] = name
        return True

    try:
        worker_count = max(args.gbif_workers, 1)
        write_every = max(args.cache_write_every, 1)
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for _ in range(worker_count * 2):
                if not submit_next(executor):
                    break

            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    name = pending.pop(future)
                    try:
                        row = future.result()
                    except Exception as exc:
                        row = {
                            "scientificName": name,
                            "family": "",
                            "canonicalName": "",
                            "matchedScientificName": "",
                            "status": "",
                            "matchType": "",
                            "confidence": "",
                            "usageKey": "",
                            "error": str(exc),
                        }
                    cache[cache_key(name)] = row
                    resolved += 1
                    if row.get("family"):
                        with_family += 1
                    if row.get("error"):
                        errors += 1

                    if resolved % write_every == 0 or resolved == total_missing:
                        write_cache(args.gbif_cache, cache)
                        print(
                            f"GBIF resolved {resolved:,}/{total_missing:,}; "
                            f"new families={with_family:,}; new errors={errors:,}",
                            flush=True,
                        )
                    submit_next(executor)
    finally:
        write_cache(args.gbif_cache, cache)

    return cache, {
        "cacheEntriesBefore": cache_entries_before,
        "namesNeedingGbif": total_missing,
        "resolved": resolved,
        "withFamily": sum(1 for row in cache.values() if row.get("family")),
        "errors": sum(1 for row in cache.values() if row.get("error")),
    }


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
    parser.add_argument("--query", default="*")
    parser.add_argument(
        "--include-existing-gbif-family",
        action="store_true",
        help="Collect names from all records with scientificName, not just records missing gbifFamily.",
    )
    parser.add_argument("--name-source", choices=["auto", "composite", "scroll"], default="auto")
    parser.add_argument("--name-field", default="scientificName.keyword")
    parser.add_argument("--composite-size", type=int, default=10000)
    parser.add_argument("--batch-size", type=int, default=10000)
    parser.add_argument("--scroll", default="5m")
    parser.add_argument("--request-timeout", type=float, default=120)
    parser.add_argument("--es-retries", type=int, default=8)
    parser.add_argument("--es-retry-sleep", type=float, default=10)
    parser.add_argument("--limit-names", type=int, default=0)
    parser.add_argument("--names-output", default=DEFAULT_NAMES_OUTPUT)
    parser.add_argument("--reuse-names", action="store_true")
    parser.add_argument(
        "--names-from-csv",
        action="append",
        default=[],
        help="Read scientific names from a CSV file or directory of CSV files instead of querying ES. May be repeated.",
    )
    parser.add_argument("--csv-name-field", default="scientificName")
    parser.add_argument("--gbif-cache", default=DEFAULT_GBIF_CACHE)
    parser.add_argument("--gbif-workers", type=int, default=8)
    parser.add_argument("--gbif-timeout", type=float, default=30)
    parser.add_argument("--gbif-retries", type=int, default=3)
    parser.add_argument("--cache-write-every", type=int, default=100)
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--retry-blank", action="store_true")
    parser.add_argument("--skip-resolve", action="store_true", help="Only collect/write scientific names; do not call GBIF.")
    parser.add_argument("--user-agent", default="Phenobase-GBIF-family-cache/1.0")
    parser.add_argument("--report", default=DEFAULT_REPORT)
    return parser.parse_args()


def main():
    args = parse_args()
    started = time.time()

    if args.reuse_names:
        counts = read_names(args.names_output, limit=args.limit_names)
        print(f"Reusing {len(counts):,} scientific names from {args.names_output}", flush=True)
    elif args.names_from_csv:
        counts = collect_names_csv(args)
        write_names(args.names_output, counts)
        print(f"Wrote {len(counts):,} scientific names to {args.names_output}", flush=True)
    else:
        counts = collect_names(args)
        write_names(args.names_output, counts)
        print(f"Wrote {len(counts):,} scientific names to {args.names_output}", flush=True)

    cache, cache_stats = resolve_cache(counts, args)
    report = OrderedDict(
        [
            ("generatedAtUtc", datetime.now(timezone.utc).replace(microsecond=0).isoformat()),
            ("elapsedSeconds", round(time.time() - started, 3)),
            ("index", args.index),
            ("query", args.query),
            ("missingGbifFamilyOnly", not args.include_existing_gbif_family),
            ("nameSource", "csv" if args.names_from_csv else args.name_source),
            ("nameField", args.name_field),
            ("namesFromCsv", args.names_from_csv),
            ("csvNameField", args.csv_name_field),
            ("namesOutput", args.names_output),
            ("uniqueScientificNames", len(counts)),
            ("recordCountRepresented", sum(counts.values())),
            ("gbifCache", args.gbif_cache),
            ("cacheEntriesAfter", len(cache)),
            ("cacheStats", cache_stats),
        ]
    )
    write_report(args.report, report)
    print(json.dumps(report, indent=2, sort_keys=True), flush=True)
    print(f"Wrote report to {args.report}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
