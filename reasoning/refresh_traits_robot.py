#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from collections import defaultdict


DEFAULT_REASONING_DIR = "reasoning"
DEFAULT_QUERY = os.path.join("reasoning", "robot", "traits_pairs.sparql")
DEFAULT_PAIRS_OUTPUT = os.path.join("reasoning", "robot", "traits_pairs.csv")
DEFAULT_OUTPUT = os.path.join("reasoning", "robot", "traits.csv")
DEFAULT_COMPARE_TO = os.path.join("data", "traits.csv")
DEFAULT_REPORT = os.path.join("reasoning", "robot", "comparison.json")
DEFAULT_ROBOT = os.path.join("..", "robot", "robot")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate a separate traits.csv via ROBOT + SPARQL and compare it to data/traits.csv."
    )
    parser.add_argument(
        "--input-owl",
        help="Path to the PPO OWL file. Defaults to the latest local reasoning snapshot.",
    )
    parser.add_argument(
        "--robot-path",
        default=os.environ.get("ROBOT") or DEFAULT_ROBOT,
        help=f"Path to the ROBOT executable (default: {DEFAULT_ROBOT} or $ROBOT).",
    )
    parser.add_argument(
        "--query",
        default=DEFAULT_QUERY,
        help=f"SPARQL query path (default: {DEFAULT_QUERY})",
    )
    parser.add_argument(
        "--pairs-output",
        default=DEFAULT_PAIRS_OUTPUT,
        help=f"Intermediate ROBOT CSV output (default: {DEFAULT_PAIRS_OUTPUT})",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Output traits CSV path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--compare-to",
        default=DEFAULT_COMPARE_TO,
        help=f"Reference traits CSV for comparison (default: {DEFAULT_COMPARE_TO})",
    )
    parser.add_argument(
        "--report",
        default=DEFAULT_REPORT,
        help=f"Comparison report path (default: {DEFAULT_REPORT})",
    )
    parser.add_argument(
        "--present-order",
        choices=["query-order", "reverse-query"],
        default="reverse-query",
        help="How to order present-term mapped chains after SPARQL expansion (default: reverse-query).",
    )
    parser.add_argument(
        "--skip-query",
        action="store_true",
        help="Skip the ROBOT query step and rebuild traits.csv from an existing pairs CSV.",
    )
    return parser.parse_args()


def ensure_parent_dir(path):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def extract_numeric_id(ppo_id):
    try:
        return int(ppo_id.split(":", 1)[1])
    except Exception:
        return 0


def resolve_input_owl(args_input, reasoning_dir):
    if args_input:
        return os.path.abspath(args_input)

    metadata_path = os.path.join(reasoning_dir, "traits_build_metadata.json")
    if os.path.exists(metadata_path):
        with open(metadata_path, encoding="utf-8") as fh:
            metadata = json.load(fh)
        snapshot_path = metadata.get("snapshot_path")
        if snapshot_path and os.path.exists(snapshot_path):
            return os.path.abspath(snapshot_path)

    dated_snapshots = []
    for name in os.listdir(reasoning_dir):
        path = os.path.join(reasoning_dir, name, "ppo.owl")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name) and os.path.exists(path):
            dated_snapshots.append((name, path))
    if dated_snapshots:
        dated_snapshots.sort()
        return os.path.abspath(dated_snapshots[-1][1])

    raise FileNotFoundError(
        "Could not resolve an input OWL file. Pass --input-owl or run reasoning/refresh_traits.py first."
    )


def run_robot_query(robot_path, input_owl, query_path, output_path):
    if not os.path.exists(robot_path):
        raise FileNotFoundError(f"ROBOT executable not found: {robot_path}")
    if not os.path.exists(input_owl):
        raise FileNotFoundError(f"Input OWL not found: {input_owl}")
    if not os.path.exists(query_path):
        raise FileNotFoundError(f"SPARQL query not found: {query_path}")

    ensure_parent_dir(output_path)
    cmd = [
        robot_path,
        "query",
        "--input",
        input_owl,
        "--query",
        query_path,
        output_path,
    ]
    subprocess.run(cmd, check=True)


def load_pairs(pairs_output):
    grouped = defaultdict(list)
    with open(pairs_output, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            grouped[row["trait_id"]].append(row)
    return grouped


def build_rows(grouped_pairs, present_order):
    rows = []
    for trait_id in sorted(grouped_pairs, key=extract_numeric_id):
        pair_rows = grouped_pairs[trait_id]
        trait_label = pair_rows[0]["trait_label"]
        is_present = trait_label.endswith(" present")

        ordered_pairs = list(pair_rows)
        if is_present and present_order == "reverse-query":
            ordered_pairs.reverse()

        mapped_ids = []
        mapped_labels = []
        seen = set()
        for pair in ordered_pairs:
            mapped_id = pair["mapped_id"]
            if mapped_id in seen:
                continue
            seen.add(mapped_id)
            mapped_ids.append(mapped_id)
            mapped_labels.append(pair["mapped_label"])

        rows.append(
            {
                "trait_urn": trait_id,
                "trait": trait_label,
                "mappedTraitIDs": "|".join(mapped_ids),
                "mappedTraits": "|".join(mapped_labels),
            }
        )
    return rows


def write_traits_csv(rows, output_path):
    ensure_parent_dir(output_path)
    with open(output_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["trait_urn", "trait", "mappedTraitIDs", "mappedTraits"],
        )
        writer.writeheader()
        writer.writerows(rows)


def load_traits_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return {row["trait_urn"]: row for row in csv.DictReader(fh)}


def split_pipe(value):
    return [part for part in (value or "").split("|") if part]


def compare_outputs(reference_rows, robot_rows):
    reference_ids = set(reference_rows)
    robot_ids = set(robot_rows)
    shared_ids = sorted(reference_ids & robot_ids, key=extract_numeric_id)

    exact_row_matches = 0
    mapped_id_set_matches = 0
    exact_diff_examples = []
    set_diff_examples = []

    for trait_id in shared_ids:
        ref = reference_rows[trait_id]
        rob = robot_rows[trait_id]

        ref_set = set(split_pipe(ref["mappedTraitIDs"]))
        rob_set = set(split_pipe(rob["mappedTraitIDs"]))

        if ref_set == rob_set:
            mapped_id_set_matches += 1
            if (
                ref["mappedTraitIDs"] == rob["mappedTraitIDs"]
                and ref["mappedTraits"] == rob["mappedTraits"]
            ):
                exact_row_matches += 1
            elif len(exact_diff_examples) < 10:
                exact_diff_examples.append(
                    {
                        "trait_urn": trait_id,
                        "trait": ref["trait"],
                        "reference_mappedTraitIDs": ref["mappedTraitIDs"],
                        "robot_mappedTraitIDs": rob["mappedTraitIDs"],
                    }
                )
        elif len(set_diff_examples) < 10:
            set_diff_examples.append(
                {
                    "trait_urn": trait_id,
                    "trait": ref["trait"],
                    "reference_mappedTraitIDs": ref["mappedTraitIDs"],
                    "robot_mappedTraitIDs": rob["mappedTraitIDs"],
                }
            )

    return {
        "reference_row_count": len(reference_ids),
        "robot_row_count": len(robot_ids),
        "shared_trait_count": len(shared_ids),
        "reference_only_traits": sorted(reference_ids - robot_ids, key=extract_numeric_id),
        "robot_only_traits": sorted(robot_ids - reference_ids, key=extract_numeric_id),
        "mapped_id_set_match_count": mapped_id_set_matches,
        "exact_row_match_count": exact_row_matches,
        "mapped_id_sets_equal_for_all_shared_traits": mapped_id_set_matches == len(shared_ids),
        "exact_rows_equal_for_all_shared_traits": exact_row_matches == len(shared_ids),
        "exact_diff_examples": exact_diff_examples,
        "set_diff_examples": set_diff_examples,
    }


def write_report(report, output_path):
    ensure_parent_dir(output_path)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)


def main():
    args = parse_args()
    reasoning_dir = os.path.abspath(DEFAULT_REASONING_DIR)
    input_owl = resolve_input_owl(args.input_owl, reasoning_dir)
    pairs_output = os.path.abspath(args.pairs_output)
    output_path = os.path.abspath(args.output)
    report_path = os.path.abspath(args.report)
    query_path = os.path.abspath(args.query)
    robot_path = os.path.abspath(args.robot_path)

    if not args.skip_query:
        run_robot_query(robot_path, input_owl, query_path, pairs_output)
    elif not os.path.exists(pairs_output):
        raise FileNotFoundError(
            f"--skip-query was set but pairs CSV was not found: {pairs_output}"
        )

    grouped_pairs = load_pairs(pairs_output)
    rows = build_rows(grouped_pairs, args.present_order)
    write_traits_csv(rows, output_path)

    print(f"Input OWL: {input_owl}")
    print(f"ROBOT query output: {pairs_output}")
    print(f"ROBOT traits CSV: {output_path}")
    print(f"Traits written: {len(rows):,}")

    if os.path.exists(args.compare_to):
        reference_rows = load_traits_csv(args.compare_to)
        robot_rows = load_traits_csv(output_path)
        report = compare_outputs(reference_rows, robot_rows)
        report.update(
            {
                "input_owl": input_owl,
                "robot_path": robot_path,
                "query_path": query_path,
                "present_order": args.present_order,
                "compare_to": os.path.abspath(args.compare_to),
                "output_path": output_path,
                "pairs_output": pairs_output,
            }
        )
        write_report(report, report_path)
        print(f"Comparison report: {report_path}")
        print(
            "Comparison summary: "
            f"{report['exact_row_match_count']}/{report['shared_trait_count']} exact rows; "
            f"{report['mapped_id_set_match_count']}/{report['shared_trait_count']} mapped-ID sets match."
        )
    else:
        print(f"Reference CSV not found, skipped comparison: {args.compare_to}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
