import unittest

from backfill_npn_recorded_by import action_from_observation, month_chunks


class NpnRecordedByBackfillTests(unittest.TestCase):
    def test_action_updates_recorded_by_and_occurrence_id(self):
        action, skip_reason = action_from_observation(
            {
                "observation_id": 27252552,
                "observedby_person_id": 12345,
            },
            "phenobase2",
        )

        self.assertEqual(skip_reason, "")
        self.assertEqual(action["_index"], "phenobase2")
        self.assertEqual(action["_id"], "npn:27252552")
        self.assertEqual(action["doc"], {"occurrenceID": "27252552", "recordedBy": "12345"})

    def test_action_falls_back_to_submitted_by_person_id(self):
        action, skip_reason = action_from_observation(
            {
                "observation_id": 27252553,
                "submittedby_person_id": 67890,
            },
            "phenobase2",
        )

        self.assertEqual(skip_reason, "")
        self.assertEqual(action["doc"], {"occurrenceID": "27252553", "recordedBy": "67890"})

    def test_action_updates_occurrence_id_without_recorded_by(self):
        action, skip_reason = action_from_observation(
            {
                "observation_id": "50205748",
                "observer_id": "",
            },
            "phenobase2",
        )

        self.assertEqual(skip_reason, "")
        self.assertEqual(action["_id"], "npn:50205748")
        self.assertEqual(action["doc"], {"occurrenceID": "50205748"})

    def test_action_skips_missing_observation_id(self):
        action, skip_reason = action_from_observation({"observer_id": 12345}, "phenobase2")

        self.assertIsNone(action)
        self.assertEqual(skip_reason, "missing_observation_id")

    def test_month_chunks_are_inclusive(self):
        self.assertEqual(
            list(month_chunks("2026-01-15", "2026-03-02", months=1)),
            [
                ("2026-01-15", "2026-02-14"),
                ("2026-02-15", "2026-03-02"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
