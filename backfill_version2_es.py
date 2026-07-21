#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Backfill version2 Phenobase fields on an existing Elasticsearch index."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, OrderedDict

from gbif_family import DEFAULT_GBIF_CACHE, GbifFamilyResolver
from trait_lookup import load_traits_catalog, resolve_trait

try:
    from elasticsearch import Elasticsearch, helpers
except ModuleNotFoundError:
    Elasticsearch = None
    helpers = None


DEFAULT_HOST = "149.165.170.158"
DEFAULT_PORT = 8081
DEFAULT_SCHEME = "http"
DEFAULT_INDEX = "phenobase2"
DEFAULT_TRAITS_PATH = "data/traits.csv"
DEFAULT_REPORT_PATH = "downloads/version2_backfill_report.json"
DEFAULT_BASE_URL = "https://biscicol.org/phenobase/api/v1/query"
DOI_RESOLVER_PREFIX = "https://doi.org/"
ANNOTATION_METHOD_BY_BASIS = {
    "humanobservation": "in_situ",
    "machineobservation": "machine",
}
TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504}

SOURCE_FIELDS = [
    "annotationID",
    "dataSource",
    "scientificName",
    "taxonRank",
    "family",
    "verbatimFamily",
    "standardizedFamily",
    "gbifFamily",
    "genus",
    "species",
    "specificEpithet",
    "taxonSearch",
    "trait",
    "trait_urn",
    "traitUrn",
    "mappedTraits",
    "mappedTraitsUrns",
    "mappedTraitIDs",
    "date",
    "year",
    "observedMetadataUrl",
    "observed_metadata_url",
    "sourceRecordUrl",
    "annotation_method",
    "annotationMethod",
    "collectionMethod",
    "basisOfRecord",
    "modelUri",
    "ModelUri",
    "model_uri",
    "accuracyFamily",
    "accuracyIncludingUncertainFamily",
    "accuracyExcludingUncertainFamily",
]

MAPPING_PROPERTIES = {
    "verbatimFamily": {"type": "keyword"},
    "standardizedFamily": {"type": "keyword"},
    "traitUrn": {"type": "keyword"},
    "mappedTraitsUrns": {"type": "keyword"},
    "sourceRecordUrl": {"type": "text"},
    "annotationMethod": {"type": "text"},
    "collectionMethod": {"type": "keyword"},
    "taxonSearch": {"type": "keyword"},
    "modelUri": {"type": "text"},
    "accuracyIncludingUncertainFamily": {"type": "text"},
    "accuracyFamily": {"type": "text"},
}


class ScrollInterrupted(RuntimeError):
    """Raised when a long-running scroll should be restarted from a fresh query."""


def is_restartable_direct_scroll_error(exc):
    status_code = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status_code in {404, 429, 500, 502, 503, 504}:
        return True

    text = str(exc)
    if "No search context found" in text:
        return True

    class_name = exc.__class__.__name__
    return class_name in {
        "ConnectionError",
        "ConnectionTimeout",
        "ReadTimeoutError",
        "TimeoutError",
        "TransportError",
    }


def is_blank(value):
    return value is None or value == "" or value == []


def first_value(source, *fields):
    for field in fields:
        value = source.get(field)
        if not is_blank(value):
            return value
    return None


def split_pipe(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").split("|") if item.strip()]


def normalize_key(value):
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def source_family_value(source):
    for field in ("verbatimFamily", "family"):
        family = str(source.get(field) or "").strip()
        if family:
            return family
    return ""


def derive_standardized_family(source):
    scientific_name = str(source.get("scientificName") or "").strip()
    if not scientific_name:
        return ""

    if normalize_key(source.get("taxonRank")) == "family":
        return scientific_name

    family = source_family_value(source)
    if family and family.casefold() == scientific_name.casefold():
        return scientific_name

    return ""


def resolve_standardized_family(source, gbif_resolver, resolve_gbif=True):
    family = first_value(source, "standardizedFamily", "gbifFamily")
    if not is_blank(family):
        return family

    family = derive_standardized_family(source)
    if not is_blank(family):
        return family

    if resolve_gbif:
        family = gbif_resolver.family_for(source.get("scientificName"))
        if not is_blank(family):
            return family

        family = gbif_resolver.family_for(source.get("genus"))
        if not is_blank(family):
            return family

    return source_family_value(source)


def normalize_model_uri(value):
    if value is None:
        return value
    text = str(value).strip()
    if not text:
        return value
    lower = text.lower()
    if lower.startswith(("http://", "https://")):
        return text
    if lower.startswith("doi:"):
        return DOI_RESOLVER_PREFIX + text.split(":", 1)[1].strip()
    if lower.startswith("doi.org/"):
        return DOI_RESOLVER_PREFIX + text.split("/", 1)[1].strip()
    if text.startswith("10."):
        return DOI_RESOLVER_PREFIX + text
    return text


def derive_annotation_method(source):
    return ANNOTATION_METHOD_BY_BASIS.get(normalize_key(source.get("collectionMethod") or source.get("basisOfRecord")))


def build_taxon_search(row):
    values = []
    seen = set()

    def add(value):
        if value is None:
            return
        text = str(value).strip()
        if not text:
            return
        collapsed = " ".join(text.split())
        key = collapsed.lower()
        if key not in seen:
            seen.add(key)
            values.append(collapsed)

    add(row.get("standardizedFamily") or row.get("gbifFamily"))
    add(row.get("verbatimFamily") or row.get("family"))
    add(row.get("genus"))
    add(row.get("species") or row.get("specificEpithet"))
    add(row.get("scientificName"))

    scientific_name = str(row.get("scientificName") or "").strip()
    if scientific_name:
        parts = scientific_name.split()
        if len(parts) >= 2:
            add(" ".join(parts[:2]))

    return values


def set_update(updates, source, field, value, overwrite=False):
    if is_blank(value):
        return
    if overwrite or source.get(field) != value:
        if overwrite or is_blank(source.get(field)):
            updates[field] = value


def normalize_data_source(value):
    if value == "SeasonWatch India":
        return "SeasonWatch (India)"
    return value


def merged_source(source, updates):
    merged = dict(source)
    merged.update(updates)
    return merged


def filter_updates(updates, only_standardized_family=False):
    if not only_standardized_family:
        return updates
    return {field: value for field, value in updates.items() if field == "standardizedFamily"}


def derive_trait_updates(source, updates, traits_catalog, overwrite=False):
    current = merged_source(source, updates)
    record = resolve_trait(
        traits_catalog,
        trait_urn=first_value(current, "traitUrn", "trait_urn"),
        trait=current.get("trait"),
    )
    if not record:
        return False

    set_update(updates, source, "traitUrn", record.get("trait_urn"), overwrite=overwrite)
    set_update(updates, source, "trait", record.get("trait"), overwrite=overwrite)
    mapped_traits = split_pipe(record.get("mappedTraits"))
    mapped_ids = split_pipe(record.get("mappedTraitIDs"))
    set_update(updates, source, "mappedTraits", mapped_traits, overwrite=overwrite)
    set_update(updates, source, "mappedTraitsUrns", mapped_ids, overwrite=overwrite)
    return True


def derive_updates(source, traits_catalog, gbif_resolver, overwrite=False, resolve_gbif=True):
    updates = {}

    data_source = source.get("dataSource")
    normalized_data_source = normalize_data_source(data_source)
    if data_source and normalized_data_source != data_source:
        updates["dataSource"] = normalized_data_source

    set_update(
        updates,
        source,
        "verbatimFamily",
        first_value(source, "verbatimFamily", "family"),
        overwrite=overwrite,
    )
    set_update(
        updates,
        source,
        "traitUrn",
        first_value(source, "traitUrn", "trait_urn"),
        overwrite=overwrite,
    )
    set_update(
        updates,
        source,
        "sourceRecordUrl",
        first_value(source, "sourceRecordUrl", "observedMetadataUrl", "observed_metadata_url"),
        overwrite=overwrite,
    )
    set_update(
        updates,
        source,
        "collectionMethod",
        first_value(source, "collectionMethod", "basisOfRecord"),
        overwrite=overwrite,
    )

    annotation_method = first_value(source, "annotationMethod", "annotation_method")
    if not annotation_method:
        annotation_method = derive_annotation_method(source)
    set_update(updates, source, "annotationMethod", annotation_method, overwrite=overwrite)

    model_uri = first_value(source, "modelUri", "ModelUri", "model_uri")
    normalized_model_uri = normalize_model_uri(model_uri)
    set_update(updates, source, "modelUri", normalized_model_uri, overwrite=overwrite)

    derive_trait_updates(source, updates, traits_catalog, overwrite=overwrite)

    set_update(
        updates,
        source,
        "accuracyFamily",
        first_value(source, "accuracyExcludingUncertainFamily"),
        overwrite=overwrite,
    )
    set_update(
        updates,
        source,
        "accuracyIncludingUncertainFamily",
        first_value(source, "accuracyIncludingUncertainFamily", "accuracyFamily"),
        overwrite=overwrite,
    )

    standardized_family = resolve_standardized_family(source, gbif_resolver, resolve_gbif=resolve_gbif)
    set_update(updates, source, "standardizedFamily", standardized_family, overwrite=overwrite)

    if overwrite or is_blank(source.get("taxonSearch")):
        taxon_search = build_taxon_search(merged_source(source, updates))
        set_update(updates, source, "taxonSearch", taxon_search, overwrite=overwrite)

    return updates


def build_client(args):
    if Elasticsearch is None:
        raise RuntimeError(
            "The elasticsearch Python package is required only for direct host/port mode. "
            "Install it or run with --base-url."
        )
    return Elasticsearch([{"host": args.host, "port": args.port, "scheme": args.scheme}])


def ensure_mapping(es, index_name):
    return es.indices.put_mapping(index=index_name, body={"properties": MAPPING_PROPERTIES})


def normalize_base_url(base_url):
    return base_url.rstrip("/")


def build_proxy_url(base_url, path, params=None):
    params = params or {}
    query_string = urllib.parse.urlencode(params)
    suffix = f"?{query_string}" if query_string else ""
    return f"{normalize_base_url(base_url)}//{path.lstrip('/')}{suffix}"


def proxy_json(base_url, method, path, payload=None, timeout=120, params=None, content_type="application/json"):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        if isinstance(payload, bytes):
            data = payload
        else:
            data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = content_type
    request = urllib.request.Request(
        build_proxy_url(base_url, path, params=params),
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        text = response.read().decode("utf-8")
        return json.loads(text) if text else {}


def proxy_json_with_retries(
    args,
    method,
    path,
    payload=None,
    params=None,
    content_type="application/json",
    label="proxy request",
    transient_codes=None,
):
    transient_codes = transient_codes or TRANSIENT_HTTP_CODES
    attempts = max(args.proxy_retries, 0) + 1
    last_exc = None

    for attempt in range(1, attempts + 1):
        try:
            return proxy_json(
                args.base_url,
                method,
                path,
                payload,
                timeout=args.request_timeout,
                params=params,
                content_type=content_type,
            )
        except urllib.error.HTTPError as exc:
            last_exc = exc
            retryable = exc.code in transient_codes
            detail = f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError) as exc:
            last_exc = exc
            retryable = True
            detail = str(exc)

        if not retryable or attempt >= attempts:
            raise last_exc

        sleep_seconds = min(args.proxy_retry_sleep * attempt, 120)
        print(
            f"{label} failed ({detail}); retry {attempt}/{attempts - 1} "
            f"after {sleep_seconds:g}s.",
            file=sys.stderr,
            flush=True,
        )
        time.sleep(sleep_seconds)

    raise last_exc


def proxy_index_exists(args):
    try:
        response = proxy_json_with_retries(args, "GET", f"{args.index}/_mapping", label="mapping check")
        return args.index in response
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise


def ensure_mapping_proxy(args):
    response = proxy_json_with_retries(
        args,
        "POST",
        f"{args.index}/_mapping",
        {"properties": MAPPING_PROPERTIES},
        label="mapping update",
    )
    if "error" in response:
        raise RuntimeError(f"Proxy mapping update failed: {response['error']}")
    return response


def build_query(args):
    base_query = {"match_all": {}} if args.query.strip() in {"", "*"} else {
        "query_string": {"query": args.query, "analyze_wildcard": True}
    }
    if args.overwrite:
        return base_query

    should = [
        {"term": {"dataSource": "SeasonWatch India"}},
        {"bool": {"must": [{"exists": {"field": "family"}}], "must_not": [{"exists": {"field": "verbatimFamily"}}]}},
        {"bool": {"must": [{"exists": {"field": "gbifFamily"}}], "must_not": [{"exists": {"field": "standardizedFamily"}}]}},
        {"bool": {"must": [{"exists": {"field": "scientificName"}}], "must_not": [{"exists": {"field": "standardizedFamily"}}]}},
        {"bool": {"must": [{"exists": {"field": "trait_urn"}}], "must_not": [{"exists": {"field": "traitUrn"}}]}},
        {"bool": {"must": [{"exists": {"field": "traitUrn"}}], "must_not": [{"exists": {"field": "mappedTraitsUrns"}}]}},
        {"bool": {"must": [{"exists": {"field": "trait_urn"}}], "must_not": [{"exists": {"field": "mappedTraitsUrns"}}]}},
        {"bool": {"must": [{"exists": {"field": "observedMetadataUrl"}}], "must_not": [{"exists": {"field": "sourceRecordUrl"}}]}},
        {"bool": {"must": [{"exists": {"field": "basisOfRecord"}}], "must_not": [{"exists": {"field": "collectionMethod"}}]}},
        {"bool": {"must": [{"exists": {"field": "annotation_method"}}], "must_not": [{"exists": {"field": "annotationMethod"}}]}},
        {"bool": {"must": [{"exists": {"field": "accuracyExcludingUncertainFamily"}}], "must_not": [{"exists": {"field": "accuracyFamily"}}]}},
    ]
    if "match_all" in base_query:
        return {"bool": {"should": should, "minimum_should_match": 1}}
    return {"bool": {"must": [base_query], "should": should, "minimum_should_match": 1}}


def scroll_hits_direct(es, args):
    body = {
        "size": args.batch_size,
        "_source": SOURCE_FIELDS,
        "sort": ["_doc"],
        "query": build_query(args),
    }
    response = es.search(
        index=args.index,
        body=body,
        scroll=args.scroll,
        request_timeout=args.request_timeout,
    )
    scroll_id = response.get("_scroll_id")
    try:
        while True:
            hits = ((response or {}).get("hits") or {}).get("hits") or []
            if not hits:
                break
            for hit in hits:
                yield hit
            try:
                response = es.scroll(
                    scroll_id=scroll_id,
                    scroll=args.scroll,
                    request_timeout=args.request_timeout,
                )
            except Exception as exc:
                if is_restartable_direct_scroll_error(exc):
                    raise ScrollInterrupted(f"direct scroll request failed: {exc}") from exc
                raise
            scroll_id = response.get("_scroll_id", scroll_id)
    finally:
        if scroll_id:
            try:
                es.clear_scroll(scroll_id=scroll_id)
            except Exception:
                pass


def scroll_hits_proxy(args):
    body = {
        "size": args.batch_size,
        "_source": SOURCE_FIELDS,
        "sort": ["_doc"],
        "query": build_query(args),
    }
    response = proxy_json_with_retries(
        args,
        "POST",
        f"{args.index}/_search",
        body,
        params={"scroll": args.scroll},
        label="initial search",
    )
    scroll_id = response.get("_scroll_id")
    try:
        while True:
            hits = ((response or {}).get("hits") or {}).get("hits") or []
            if not hits:
                break
            for hit in hits:
                yield hit
            try:
                response = proxy_json_with_retries(
                    args,
                    "POST",
                    "_search/scroll",
                    {"scroll": args.scroll, "scroll_id": scroll_id},
                    label="scroll request",
                )
            except urllib.error.HTTPError as exc:
                if exc.code in {404, 429, 500, 502, 503, 504}:
                    raise ScrollInterrupted(f"scroll request failed with HTTP {exc.code}") from exc
                raise
            except (urllib.error.URLError, TimeoutError) as exc:
                raise ScrollInterrupted(f"scroll request failed: {exc}") from exc
            scroll_id = response.get("_scroll_id", scroll_id)
    finally:
        if scroll_id:
            try:
                proxy_json(args.base_url, "DELETE", "_search/scroll", {"scroll_id": [scroll_id]}, timeout=args.request_timeout)
            except Exception:
                pass


def scroll_hits(es, args):
    if args.base_url:
        yield from scroll_hits_proxy(args)
    else:
        yield from scroll_hits_direct(es, args)


def proxy_bulk(args, actions):
    lines = []
    for action in actions:
        lines.append(json.dumps({"update": {"_index": action["_index"], "_id": action["_id"]}}, separators=(",", ":")))
        lines.append(json.dumps({"doc": action["doc"]}, separators=(",", ":")))
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    response = proxy_json_with_retries(
        args,
        "POST",
        "_bulk",
        payload,
        content_type="application/x-ndjson",
        label="bulk update",
    )
    return response.get("items") or []


def parse_args():
    parser = argparse.ArgumentParser(description="Backfill version2 fields in a Phenobase ES index.")
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
    parser.add_argument("--traits-path", default=DEFAULT_TRAITS_PATH)
    parser.add_argument("--gbif-cache", default=DEFAULT_GBIF_CACHE)
    parser.add_argument("--gbif-timeout", type=float, default=30)
    parser.add_argument("--no-gbif", action="store_true", help="Skip GBIF resolution.")
    parser.add_argument(
        "--gbif-cache-only",
        action="store_true",
        help="Populate standardizedFamily only from --gbif-cache; do not make live GBIF requests.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Recompute fields even when already present.")
    parser.add_argument("--apply", action="store_true", help="Write updates to Elasticsearch. Default is dry-run.")
    parser.add_argument(
        "--only-standardized-family",
        action="store_true",
        help="Only write standardizedFamily updates, even if other version2 field updates are derivable.",
    )
    parser.add_argument("--ensure-mapping", action="store_true", help="Ensure version2 ES field mappings during dry-run.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum docs to inspect. Use 0 for no limit.")
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--scroll", default="5m")
    parser.add_argument(
        "--max-scroll-restarts",
        type=int,
        default=100,
        help="Maximum fresh-scroll restarts after proxy scroll failures (default: 100).",
    )
    parser.add_argument(
        "--restart-sleep",
        type=float,
        default=10,
        help="Seconds to wait before restarting a failed proxy scroll (default: 10).",
    )
    parser.add_argument(
        "--proxy-retries",
        type=int,
        default=8,
        help="Retries for transient proxy/search/bulk HTTP failures such as 502 (default: 8).",
    )
    parser.add_argument(
        "--proxy-retry-sleep",
        type=float,
        default=10,
        help="Base seconds between transient proxy retries; multiplied by attempt up to 120s (default: 10).",
    )
    parser.add_argument("--request-timeout", type=float, default=120)
    parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    args = parser.parse_args()
    if args.no_gbif and args.gbif_cache_only:
        parser.error("--no-gbif and --gbif-cache-only cannot be used together")
    return args


def main():
    args = parse_args()
    started = time.time()
    es = None
    if args.base_url:
        if not proxy_index_exists(args):
            print(f"Index does not exist through proxy: {args.index}", file=sys.stderr)
            return 1
    else:
        es = build_client(args)
        if not es.ping():
            print(f"Could not connect to {args.scheme}://{args.host}:{args.port}", file=sys.stderr)
            return 1
        if not es.indices.exists(index=args.index):
            print(f"Index does not exist: {args.index}", file=sys.stderr)
            return 1

    if args.apply or args.ensure_mapping:
        mapping_response = ensure_mapping_proxy(args) if args.base_url else ensure_mapping(es, args.index)
        print(json.dumps(mapping_response, indent=2, sort_keys=True))

    traits_catalog = load_traits_catalog(args.traits_path)
    gbif_resolver = GbifFamilyResolver(
        cache_path=args.gbif_cache,
        enabled=not args.no_gbif and not args.gbif_cache_only,
        timeout=args.gbif_timeout,
    )

    stats = Counter()
    field_updates = Counter()
    actions = []

    def flush():
        nonlocal actions
        if not actions:
            return
        if args.apply:
            if args.base_url:
                for item in proxy_bulk(args, actions):
                    meta = item.get("update") or {}
                    if 200 <= int(meta.get("status", 0)) < 300:
                        stats["written"] += 1
                    else:
                        stats["write_errors"] += 1
                        print(json.dumps(item, ensure_ascii=True), file=sys.stderr)
            else:
                for ok, item in helpers.streaming_bulk(
                    es,
                    actions=actions,
                    chunk_size=len(actions),
                    request_timeout=args.request_timeout,
                    max_retries=2,
                    raise_on_error=False,
                    refresh=False,
                ):
                    if ok:
                        stats["written"] += 1
                    else:
                        stats["write_errors"] += 1
                        print(json.dumps(item, ensure_ascii=True), file=sys.stderr)
        actions = []

    scroll_restarts = 0
    while True:
        inspected_before_round = stats["inspected"]
        try:
            for hit in scroll_hits(es, args):
                if args.limit and stats["inspected"] >= args.limit:
                    break
                stats["inspected"] += 1
                source = hit.get("_source") or {}
                updates = derive_updates(
                    source,
                    traits_catalog,
                    gbif_resolver,
                    overwrite=args.overwrite,
                    resolve_gbif=not args.no_gbif,
                )
                updates = filter_updates(updates, only_standardized_family=args.only_standardized_family)
                if not updates:
                    stats["noops"] += 1
                    continue

                stats["would_update"] += 1
                for field in updates:
                    field_updates[field] += 1
                actions.append(
                    {
                        "_op_type": "update",
                        "_index": args.index,
                        "_id": hit["_id"],
                        "doc": updates,
                    }
                )
                if len(actions) >= args.batch_size:
                    flush()

                if stats["inspected"] % max(args.batch_size, 1) == 0:
                    print(
                        f"inspected={stats['inspected']:,} would_update={stats['would_update']:,} "
                        f"written={stats['written']:,}",
                        flush=True,
                    )
            break
        except ScrollInterrupted as exc:
            flush()
            scroll_restarts += 1
            stats["scroll_restarts"] = scroll_restarts
            print(
                f"Scroll interrupted after inspecting {stats['inspected']:,} docs: {exc}. "
                f"Restarting fresh scroll {scroll_restarts}/{args.max_scroll_restarts}...",
                file=sys.stderr,
                flush=True,
            )
            if scroll_restarts > args.max_scroll_restarts:
                raise RuntimeError(f"Exceeded --max-scroll-restarts={args.max_scroll_restarts}") from exc
            if stats["inspected"] == inspected_before_round:
                raise RuntimeError("Scroll restart made no progress; aborting to avoid an infinite loop.") from exc
            if args.restart_sleep > 0:
                time.sleep(args.restart_sleep)
            if args.limit and stats["inspected"] >= args.limit:
                break

    flush()

    report = OrderedDict(
        [
            ("apply", args.apply),
            ("index", args.index),
            ("query", args.query),
            ("limit", args.limit),
            ("elapsedSeconds", round(time.time() - started, 3)),
            ("stats", OrderedDict(sorted(stats.items()))),
            ("fieldUpdates", OrderedDict(sorted(field_updates.items()))),
            ("gbif", OrderedDict(
                [
                    ("cachePath", args.gbif_cache),
                    ("cacheOnly", args.gbif_cache_only),
                    ("cacheSize", len(gbif_resolver.cache)),
                    ("cacheHits", gbif_resolver.cache_hits),
                    ("cacheMisses", gbif_resolver.cache_misses),
                    ("disabledMisses", gbif_resolver.disabled_misses),
                    ("requests", gbif_resolver.requests),
                    ("errors", gbif_resolver.errors),
                ]
            )),
        ]
    )
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=True)
        fh.write("\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"Wrote report to {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
