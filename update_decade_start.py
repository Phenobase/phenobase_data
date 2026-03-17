#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import sys

from elasticsearch import Elasticsearch


DEFAULT_HOST = "149.165.170.158"
DEFAULT_PORT = 8081
DEFAULT_SCHEME = "http"
DEFAULT_INDEX = "phenobase2"


def build_client(host, port, scheme):
    return Elasticsearch([{"host": host, "port": port, "scheme": scheme}])


def ensure_mapping(es, index_name):
    body = {
        "properties": {
            "decadeStart": {"type": "integer"}
        }
    }
    return es.indices.put_mapping(index=index_name, body=body)


def build_update_body(overwrite):
    must = [{"exists": {"field": "year"}}]
    must_not = [] if overwrite else [{"exists": {"field": "decadeStart"}}]

    return {
        "script": {
            "lang": "painless",
            "source": """
                if (ctx._source.year == null) {
                    ctx.op = 'noop';
                    return;
                }

                int y;
                if (ctx._source.year instanceof Number) {
                    y = ((Number) ctx._source.year).intValue();
                } else if (ctx._source.year instanceof String) {
                    y = (int) Double.parseDouble(ctx._source.year);
                } else {
                    ctx.op = 'noop';
                    return;
                }

                ctx._source.decadeStart = (y / 10) * 10;
            """,
        },
        "query": {
            "bool": {
                "must": must,
                "must_not": must_not,
            }
        }
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Backfill decadeStart on an existing Elasticsearch phenobase index."
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Elasticsearch host (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Elasticsearch port (default: {DEFAULT_PORT})")
    parser.add_argument("--scheme", default=DEFAULT_SCHEME, help=f"Connection scheme (default: {DEFAULT_SCHEME})")
    parser.add_argument("--index", default=DEFAULT_INDEX, help=f"Index name (default: {DEFAULT_INDEX})")
    parser.add_argument("--overwrite", action="store_true", help="Recompute decadeStart even if it already exists")
    parser.add_argument("--wait", action="store_true", help="Wait for completion instead of returning a task id")
    parser.add_argument("--slices", default="auto", help='Parallel slices for update_by_query (default: "auto")')
    parser.add_argument(
        "--requests-per-second",
        type=float,
        default=-1.0,
        help="Throttle update_by_query. Use -1 for unlimited (default: -1)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh the index after the update completes",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    es = build_client(args.host, args.port, args.scheme)
    if not es.ping():
        print(
            f"Could not connect to Elasticsearch at {args.scheme}://{args.host}:{args.port}",
            file=sys.stderr,
        )
        return 1

    if not es.indices.exists(index=args.index):
        print(f"Index does not exist: {args.index}", file=sys.stderr)
        return 1

    print(f"Connected to {args.scheme}://{args.host}:{args.port}")
    print(f"Ensuring mapping for {args.index}...")
    mapping_response = ensure_mapping(es, args.index)
    print(json.dumps(mapping_response, indent=2, sort_keys=True))

    body = build_update_body(args.overwrite)
    request_kwargs = {
        "index": args.index,
        "body": body,
        "conflicts": "proceed",
        "wait_for_completion": args.wait,
        "refresh": args.refresh,
        "slices": args.slices,
    }
    if args.requests_per_second >= 0:
        request_kwargs["requests_per_second"] = args.requests_per_second

    print(f"Starting update_by_query on {args.index}...")
    response = es.update_by_query(**request_kwargs)
    print(json.dumps(response, indent=2, sort_keys=True))

    if not args.wait and response.get("task"):
        print()
        print("Task created.")
        print(f"Task id: {response['task']}")
        print("Check status with:")
        print(
            f"curl -s {args.scheme}://{args.host}:{args.port}/_tasks/{response['task']} | python3 -m json.tool"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
