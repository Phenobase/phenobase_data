# npn_cleanup.py
import argparse
from elasticsearch import Elasticsearch

DEFAULT_INDEX = "phenobase2"
DEFAULT_HOST = "149.165.170.158"
DEFAULT_PORT = 8081
DEFAULT_SCHEME = "http"

def make_client(host, port, scheme):
    return Elasticsearch([{"host": host, "port": port, "scheme": scheme}])

def build_query(value):
    """
    Works whether dataSource is mapped as keyword or only text.
    Prefers exact keyword match, falls back to match_phrase.
    """
    return {
        "bool": {
            "should": [
                {"term": {"dataSource.keyword": value}},   # exact (if keyword subfield exists)
                {"term": {"dataSource": value}},           # exact on root (if keyword mapping used)
                {"match_phrase": {"dataSource": value}},   # fallback for text-only
            ],
            "minimum_should_match": 1
        }
    }

def count_npn(es, index, value):
    q = build_query(value)
    resp = es.count(index=index, body={"query": q})
    return resp.get("count", 0)

def delete_npn(es, index, value, dry_run=False):
    q = build_query(value)
    if dry_run:
        # Show up to 10 example IDs to verify before deleting
        resp = es.search(
            index=index,
            body={"query": q, "_source": False, "size": 10},
            request_timeout=60,
        )
        ids = [h["_id"] for h in resp.get("hits", {}).get("hits", [])]
        return {"dry_run": True, "sample_ids": ids, "total_hint": resp.get("hits", {}).get("total", {})}

    resp = es.delete_by_query(
        index=index,
        body={"query": q},
        conflicts="proceed",
        refresh=True,
        slices="auto",
        request_timeout=600,
        wait_for_completion=True,
    )
    return resp

def main():
    ap = argparse.ArgumentParser(description="Count (and optionally delete) docs by dataSource.")
    ap.add_argument("--index", default=DEFAULT_INDEX)
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--scheme", default=DEFAULT_SCHEME)
    ap.add_argument("--value", default="National Phenology Network",
                    help='Value to match in "dataSource" (default: "National Phenology Network")')
    ap.add_argument("--delete", action="store_true", help="Perform deletion (requires --yes)")
    ap.add_argument("--yes", action="store_true", help="Confirm deletion without prompt")
    ap.add_argument("--dry-run", action="store_true", help="Show sample IDs only (no deletion)")
    args = ap.parse_args()

    es = make_client(args.host, args.port, args.scheme)

    cnt = count_npn(es, args.index, args.value)
    print(f'Count where dataSource == "{args.value}": {cnt:,}')

    if args.delete:
        if not args.yes and not args.dry_run:
            print("Refusing to delete without --yes (or use --dry-run first).")
            return
        resp = delete_npn(es, args.index, args.value, dry_run=args.dry_run)
        if args.dry_run:
            total_hint = resp.get("total_hint")
            hint_str = (f'{total_hint.get("value")} ({total_hint.get("relation")})'
                        if isinstance(total_hint, dict) else str(total_hint))
            print(f"[DRY-RUN] Example IDs: {resp['sample_ids']}")
            print(f"[DRY-RUN] Total (hint): {hint_str}")
        else:
            deleted = resp.get("deleted")
            version_conflicts = resp.get("version_conflicts")
            print(f"Deleted: {deleted:,} | version_conflicts: {version_conflicts}")

if __name__ == "__main__":
    main()

