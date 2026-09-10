#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Citation and acknowledgement text for Phenobase exports."""

from __future__ import annotations

from collections import OrderedDict
from datetime import date
import re


MONTH_ABBR = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

PHENOBASE_URL = "https://phenobase.org/"

SOURCE_ORDER = [
    "National Ecological Observatory Network (USA)",
    "USA National Phenology Network",
    "Budburst",
    "iNaturalist",
    "SeasonWatch (India)",
    "PhenoObs",
    "Herbarium",
]

SOURCE_ALIASES = {
    "budburst": "Budburst",
    "herbarium": "Herbarium",
    "inat": "iNaturalist",
    "inaturalist": "iNaturalist",
    "nationalecologicalobservatorynetwork": "National Ecological Observatory Network (USA)",
    "nationalecologicalobservatorynetworkusa": "National Ecological Observatory Network (USA)",
    "neon": "National Ecological Observatory Network (USA)",
    "phenobs": "PhenoObs",
    "phenoobs": "PhenoObs",
    "seasonwatch": "SeasonWatch (India)",
    "seasonwatchindia": "SeasonWatch (India)",
    "usanationalphenologynetwork": "USA National Phenology Network",
    "usanpn": "USA National Phenology Network",
}

SOURCE_CITATION_TEMPLATES = {
    "National Ecological Observatory Network (USA)": (
        "NEON (National Ecological Observatory Network). {year}. Plant phenology observations, "
        "DP1.10055.001, RELEASE-2026 for data through Dec 31 2024 "
        "(https://doi.org/10.48443/p75s-7p48) and provisional data since Jan 1 2025. "
        "[Date range of data used]. Dataset accessed {access_date} via Phenobase "
        "(https://phenobase.org/)."
    ),
    "USA National Phenology Network": (
        "USA National Phenology Network. {year}. Plant Phenology Status Data "
        "(http://doi.org/10.5066/F78S4N1V). [Date range of data used]. "
        "Data set accessed {access_date} via Phenobase (https://phenobase.org/)."
    ),
    "Budburst": (
        "Budburst. {year}. Budburst: An online database of plant observations, "
        "a citizen-science project of the Chicago Botanic Garden. Glencoe, Illinois "
        "(http://www.budburst.org). Data set accessed {access_date} via Phenobase "
        "(https://phenobase.org/)."
    ),
    "iNaturalist": (
        "iNaturalist community. Observations of [species list] from [geographic area], "
        "[country name] observed on/between [date range]. Exported from Phenobase "
        "(https://phenobase.org/) on {access_date}."
    ),
    "SeasonWatch (India)": (
        "SeasonWatch Citizen Scientist Network. {year}. Data on tree phenology "
        "(https://doi.org/10.15468/kdtw96). Data type – [date][region][species], "
        "SeasonWatch, India. Dataset accessed {access_date} via Phenobase "
        "(https://phenobase.org/)."
    ),
    "PhenoObs": (
        "Nordt, B., Hensen, I., Bucher, S. F., Freiberg, M., Primack, R. B., "
        "Stevens, A.-D., Bonn, A., Wirth, C., Jakubka, D., Plos, C., Sporbert, M., "
        "and Römermann, C. 2021. The PhenObs initiative – A standardised protocol "
        "for monitoring phenological responses to climate change using herbaceous plant species "
        "in botanical gardens. Functional Ecology, 35, 821-834. "
        "https://doi.org/10.1111/1365-2435.13747"
    ),
    "Herbarium": (
        "Phenobase. {year}. Phenobase: a global resource for plant phenology data "
        "(https://phenobase.org). [Date range of data used], Dataset accessed {access_date}."
    ),
}

FALLBACK_CITATION_TEMPLATE = (
    "Phenobase. {year}. Phenobase: a global resource for plant phenology data "
    "(https://phenobase.org). [Date range of data used], Dataset accessed {access_date}."
)

ACKNOWLEDGEMENT_NAMES = OrderedDict(
    [
        ("Budburst", "Budburst"),
        ("iNaturalist", "iNaturalist"),
        ("PhenoObs", "PhenObs"),
        ("SeasonWatch (India)", "SeasonWatch (India)"),
        ("USA National Phenology Network", "USA National Phenology Network"),
    ]
)

NEON_ACKNOWLEDGEMENT = (
    "This material is based in part upon work supported by the National Ecological "
    "Observatory Network (NEON), a program sponsored by the U.S. National Science "
    "Foundation (NSF) and operated under cooperative agreement by Battelle."
)


def normalize_source_key(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def canonical_data_source(data_source):
    source = str(data_source or "").strip()
    return SOURCE_ALIASES.get(normalize_source_key(source), source)


def format_access_date(access_date=None):
    access_date = access_date or date.today()
    if isinstance(access_date, str):
        return access_date
    return f"{MONTH_ABBR[access_date.month - 1]} {access_date.day}, {access_date.year}"


def access_year(access_date=None):
    access_date = access_date or date.today()
    if isinstance(access_date, str):
        match = re.search(r"\b(20\d{2}|19\d{2})\b", access_date)
        return match.group(1) if match else access_date
    return str(access_date.year)


def source_count_items(data_sources):
    if hasattr(data_sources, "items"):
        items = data_sources.items()
    else:
        items = ((data_source, None) for data_source in data_sources)
    return [(str(data_source), count) for data_source, count in items if data_source and (count is None or int(count) > 0)]


def ordered_source_count_items(data_sources):
    items = source_count_items(data_sources)
    order = {source: index for index, source in enumerate(SOURCE_ORDER)}

    def sort_key(item):
        data_source, _count = item
        canonical = canonical_data_source(data_source)
        return (order.get(canonical, len(order)), data_source.lower())

    return sorted(items, key=sort_key)


def citation_for_data_source(data_source, access_date=None):
    canonical = canonical_data_source(data_source)
    template = SOURCE_CITATION_TEMPLATES.get(canonical, FALLBACK_CITATION_TEMPLATE)
    return template.format(year=access_year(access_date), access_date=format_access_date(access_date))


def citations_for_data_sources(data_sources, access_date=None):
    return OrderedDict(
        (
            data_source,
            citation_for_data_source(data_source, access_date),
        )
        for data_source, _count in ordered_source_count_items(data_sources)
    )


def source_citation_rows(data_sources, access_date=None):
    return [
        OrderedDict(
            [
                ("dataSource", data_source),
                ("recordCount", count if count is not None else ""),
                ("citationText", citation_for_data_source(data_source, access_date)),
            ]
        )
        for data_source, count in ordered_source_count_items(data_sources)
    ]


def acknowledgements_for_data_sources(data_sources):
    canonical_sources = {
        canonical_data_source(data_source)
        for data_source, _count in source_count_items(data_sources)
    }
    acknowledgements = []
    names = [
        display_name
        for canonical, display_name in ACKNOWLEDGEMENT_NAMES.items()
        if canonical in canonical_sources
    ]
    if names:
        acknowledgements.append(
            "We thank the following organizations whose many professional and volunteer "
            f"participants contributed data: {', '.join(names)}."
        )
    if "National Ecological Observatory Network (USA)" in canonical_sources:
        acknowledgements.append(NEON_ACKNOWLEDGEMENT)
    return acknowledgements


def citation_markdown(data_sources, access_date=None):
    lines = [
        "# Citations",
        "",
        "Use the citation below for each data source included in this download. "
        "Bracketed placeholders such as [Date range of data used] are intentionally retained for users to complete.",
        "",
        "## Source Citations",
        "",
    ]

    citations = citations_for_data_sources(data_sources, access_date)
    if citations:
        for data_source, citation in citations.items():
            lines.extend([f"### {data_source}", "", citation, ""])
    else:
        lines.extend(["No source records were exported.", ""])

    acknowledgements = acknowledgements_for_data_sources(data_sources)
    if acknowledgements:
        lines.extend(["## Acknowledgements", ""])
        for acknowledgement in acknowledgements:
            lines.extend([acknowledgement, ""])

    return "\n".join(lines).rstrip() + "\n"
