#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import json
import os
import shutil
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from collections import OrderedDict
from datetime import datetime, timezone


DEFAULT_SOURCE_URL = "https://raw.githubusercontent.com/PlantPhenoOntology/ppo/refs/heads/main/ppo.owl"
DEFAULT_OUTPUT = "data/traits.csv"
DEFAULT_REASONING_DIR = "reasoning"
DEFAULT_DOCS_DATA = "docs/traits-data.json"
DEFAULT_DOCS_CSV = "docs/traits.csv"

NS = {
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Refresh data/traits.csv from the GitHub PPO ontology source."
    )
    parser.add_argument(
        "--source-url",
        default=DEFAULT_SOURCE_URL,
        help=f"Ontology source URL (default: {DEFAULT_SOURCE_URL})",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Destination CSV path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--reasoning-dir",
        default=DEFAULT_REASONING_DIR,
        help=f"Directory for ontology snapshots and build metadata (default: {DEFAULT_REASONING_DIR})",
    )
    parser.add_argument(
        "--docs-data",
        default=DEFAULT_DOCS_DATA,
        help=f"Destination JSON path for the static trait viewer (default: {DEFAULT_DOCS_DATA})",
    )
    parser.add_argument(
        "--docs-csv",
        default=DEFAULT_DOCS_CSV,
        help=f"Destination CSV path for GitHub Pages publishing (default: {DEFAULT_DOCS_CSV})",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=120.0,
        help="Network timeout in seconds when downloading the ontology (default: 120).",
    )
    return parser.parse_args()


def ensure_parent_dir(path):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def download_ontology(source_url, timeout_seconds, destination):
    with urllib.request.urlopen(source_url, timeout=timeout_seconds) as response, open(destination, "wb") as fh:
        shutil.copyfileobj(response, fh)


def extract_ppo_id(iri):
    if not iri or "PPO_" not in iri:
        return None
    return iri.rsplit("/", 1)[-1].replace("_", ":")


def extract_numeric_id(ppo_id):
    if not ppo_id or ":" not in ppo_id:
        return 0
    try:
        return int(ppo_id.split(":", 1)[1])
    except ValueError:
        return 0


def local_name(tag):
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def parse_ontology(owl_path):
    tree = ET.parse(owl_path)
    root = tree.getroot()

    ontology = root.find("owl:Ontology", NS)
    version_info = None
    version_iri = None
    if ontology is not None:
        version_node = ontology.find("owl:versionInfo", NS)
        if version_node is not None and version_node.text:
            version_info = version_node.text.strip()
        version_iri_node = ontology.find("owl:versionIRI", NS)
        if version_iri_node is not None:
            version_iri = version_iri_node.attrib.get(f"{{{NS['rdf']}}}resource")

    classes = OrderedDict()

    for elem in root.iter():
        if local_name(elem.tag) != "Class":
            continue
        iri = elem.attrib.get(f"{{{NS['rdf']}}}about")
        ppo_id = extract_ppo_id(iri)
        if not ppo_id:
            continue

        label = None
        direct_supers = []

        for child in list(elem):
            child_name = local_name(child.tag)
            if child_name == "label":
                text = (child.text or "").strip()
                if text and label is None:
                    label = text
            elif child_name == "subClassOf":
                resource = child.attrib.get(f"{{{NS['rdf']}}}resource")
                if resource:
                    super_id = extract_ppo_id(resource)
                    if super_id and super_id not in direct_supers:
                        direct_supers.append(super_id)

        classes[ppo_id] = {
            "id": ppo_id,
            "iri": iri,
            "label": label,
            "direct_supers": direct_supers,
        }

    return {
        "version_info": version_info,
        "version_iri": version_iri,
        "classes": classes,
    }


def is_trait_label(label):
    if not label:
        return False
    return label.endswith(" present") or label.endswith(" absent")


def build_traits_rows(classes):
    trait_classes = OrderedDict(
        (ppo_id, meta)
        for ppo_id, meta in classes.items()
        if is_trait_label(meta.get("label"))
    )

    label_by_id = {ppo_id: meta["label"] for ppo_id, meta in trait_classes.items()}
    direct_supers_by_id = {ppo_id: meta["direct_supers"] for ppo_id, meta in classes.items()}

    def collect_present_chain(start_id):
        ordered = []
        seen = set()

        def visit(node_id):
            if node_id in seen:
                return
            seen.add(node_id)
            label = label_by_id.get(node_id)
            if label and label.endswith(" present"):
                ordered.append(node_id)
            for parent_id in direct_supers_by_id.get(node_id, []):
                visit(parent_id)

        visit(start_id)
        return ordered

    rows = []
    for ppo_id, meta in trait_classes.items():
        label = meta["label"]
        if label.endswith(" present"):
            mapped_ids = collect_present_chain(ppo_id)
        else:
            mapped_ids = [ppo_id]

        rows.append(
            {
                "trait_urn": ppo_id,
                "trait": label,
                "mappedTraitIDs": "|".join(mapped_ids),
                "mappedTraits": "|".join(label_by_id[x] for x in mapped_ids),
            }
        )

    rows.sort(key=lambda row: extract_numeric_id(row["trait_urn"]))
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


def copy_docs_csv(source_path, docs_csv_path):
    ensure_parent_dir(docs_csv_path)
    shutil.copyfile(source_path, docs_csv_path)


def build_docs_payload(rows, ontology_meta, source_url):
    trait_ids = {row["trait_urn"] for row in rows}
    label_by_id = {row["trait_urn"]: row["trait"] for row in rows}
    nodes = []

    for row in rows:
        ppo_id = row["trait_urn"]
        direct_supers = [
            parent_id
            for parent_id in ontology_meta["classes"][ppo_id]["direct_supers"]
            if parent_id in trait_ids
        ]
        mapped_ids = row["mappedTraitIDs"].split("|") if row["mappedTraitIDs"] else []
        mapped_labels = row["mappedTraits"].split("|") if row["mappedTraits"] else []
        nodes.append(
            {
                "id": ppo_id,
                "label": row["trait"],
                "type": "present" if row["trait"].endswith(" present") else "absent",
                "direct_supers": direct_supers,
                "direct_super_labels": [label_by_id[parent_id] for parent_id in direct_supers],
                "mapped_ids": mapped_ids,
                "mapped_labels": mapped_labels,
                "iri": ontology_meta["classes"][ppo_id]["iri"],
            }
        )

    children_by_id = {node["id"]: [] for node in nodes}
    for node in nodes:
        for parent_id in node["direct_supers"]:
            if parent_id in children_by_id:
                children_by_id[parent_id].append(node["id"])

    for node in nodes:
        children = sorted(children_by_id[node["id"]], key=extract_numeric_id)
        node["direct_children"] = children
        node["direct_child_labels"] = [label_by_id[child_id] for child_id in children]

    nodes.sort(key=lambda node: extract_numeric_id(node["id"]))

    return {
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_url": source_url,
        "version_info": ontology_meta["version_info"],
        "version_iri": ontology_meta["version_iri"],
        "row_count": len(rows),
        "present_count": sum(node["type"] == "present" for node in nodes),
        "absent_count": sum(node["type"] == "absent" for node in nodes),
        "nodes": nodes,
    }


def write_docs_payload(payload, output_path):
    ensure_parent_dir(output_path)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)


def write_build_metadata(reasoning_dir, source_url, snapshot_path, ontology_meta, rows, output_path):
    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_url": source_url,
        "snapshot_path": snapshot_path,
        "version_info": ontology_meta["version_info"],
        "version_iri": ontology_meta["version_iri"],
        "row_count": len(rows),
        "present_count": sum(row["trait"].endswith(" present") for row in rows),
        "absent_count": sum(row["trait"].endswith(" absent") for row in rows),
        "output_path": output_path,
    }
    metadata_path = os.path.join(reasoning_dir, "traits_build_metadata.json")
    with open(metadata_path, "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2, sort_keys=True)
    return metadata_path


def main():
    args = parse_args()
    reasoning_dir = os.path.abspath(args.reasoning_dir)
    output_path = os.path.abspath(args.output)
    docs_data_path = os.path.abspath(args.docs_data)
    docs_csv_path = os.path.abspath(args.docs_csv)
    ensure_parent_dir(output_path)
    ensure_parent_dir(docs_data_path)
    ensure_parent_dir(docs_csv_path)
    os.makedirs(reasoning_dir, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="ppo_refresh_") as temp_dir:
        downloaded_owl = os.path.join(temp_dir, "ppo.owl")
        download_ontology(args.source_url, args.request_timeout, downloaded_owl)
        ontology_meta = parse_ontology(downloaded_owl)
        version_info = ontology_meta["version_info"] or "unknown-version"

        snapshot_dir = os.path.join(reasoning_dir, version_info)
        os.makedirs(snapshot_dir, exist_ok=True)
        snapshot_path = os.path.join(snapshot_dir, "ppo.owl")
        shutil.copyfile(downloaded_owl, snapshot_path)

    rows = build_traits_rows(ontology_meta["classes"])
    write_traits_csv(rows, output_path)
    copy_docs_csv(output_path, docs_csv_path)
    docs_payload = build_docs_payload(rows, ontology_meta, args.source_url)
    write_docs_payload(docs_payload, docs_data_path)
    metadata_path = write_build_metadata(
        reasoning_dir,
        args.source_url,
        snapshot_path,
        ontology_meta,
        rows,
        output_path,
    )

    print(f"Ontology version: {ontology_meta['version_info'] or 'UNKNOWN'}")
    if ontology_meta["version_iri"]:
        print(f"Ontology version IRI: {ontology_meta['version_iri']}")
    print(f"Ontology snapshot: {snapshot_path}")
    print(f"Traits written: {len(rows):,}")
    print(f"Output CSV: {output_path}")
    print(f"Published CSV: {docs_csv_path}")
    print(f"Viewer data: {docs_data_path}")
    print(f"Build metadata: {metadata_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
