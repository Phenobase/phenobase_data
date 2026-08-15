import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "downloads" / "npn" / "fetchAndTransformNPNData.py"
SPEC = importlib.util.spec_from_file_location("fetchAndTransformNPNData", MODULE_PATH)
fetch_npn = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch_npn)


class FetchNpnDataTests(unittest.TestCase):
    def test_verbatim_trait_uses_equals_status_for_npn_and_neon(self):
        mapping_index = {
            fetch_npn.normalize_key("Leaves"): {
                1: {
                    "trait_urn": "PPO:0002315",
                    "trait": "unfolded true leaf present",
                }
            }
        }
        species_catalog = {
            "by_id": {
                1: {
                    "family": "Salicaceae",
                    "genus": "Populus",
                    "species": "deltoides",
                }
            },
            "by_gs": {},
        }
        counters = {
            "keptCount": 0,
            "droppedByMinusOne": 0,
            "droppedEmptyTrait": 0,
            "droppedNoMap": 0,
            "droppedBadObsStatus": 0,
        }
        base_observation = {
            "phenophase_description": "Leaves",
            "phenophase_status": "1",
            "genus": "Populus",
            "species": "deltoides",
            "species_id": 1,
            "observation_date": "2026-05-16",
            "observation_id": "56449935",
            "site_id": "29887",
            "individual_id": "189856",
        }

        rows = fetch_npn.transform_rows(
            [
                {**base_observation, "dataset_id": ""},
                {**base_observation, "dataset_id": "16", "observation_id": "56449936"},
            ],
            mapping_index,
            species_catalog,
            counters,
        )

        self.assertEqual(
            [row["dataSource"] for row in rows],
            [
                "USA National Phenology Network",
                "National Ecological Observatory Network (USA)",
            ],
        )
        self.assertEqual([row["verbatimTrait"] for row in rows], ["Leaves=1", "Leaves=1"])


if __name__ == "__main__":
    unittest.main()
