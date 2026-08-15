import unittest

import normalize_npn_verbatim_traits_es


class NormalizeNpnVerbatimTraitsEsTests(unittest.TestCase):
    def test_update_body_targets_only_npn_sources_and_verbatim_trait(self):
        body = normalize_npn_verbatim_traits_es.build_update_body()
        query = body["query"]["bool"]["filter"]

        self.assertIn(
            {
                "terms": {
                    "dataSource": [
                        "USA National Phenology Network",
                        "National Ecological Observatory Network (USA)",
                    ]
                }
            },
            query,
        )
        self.assertIn({"exists": {"field": "verbatimTrait"}}, query)

    def test_script_rewrites_only_legacy_status_suffixes(self):
        script = normalize_npn_verbatim_traits_es.build_normalize_script()

        self.assertIn("statusSuffix.equals(' (0)') || statusSuffix.equals(' (1)')", script)
        self.assertIn("value.substring(0, value.length() - 4) + '=' + status", script)
        self.assertIn("ctx.op = 'noop'", script)

    def test_update_by_query_launches_asynchronously(self):
        class Args:
            index = "phenobase2"
            wait = True
            slices = "auto"
            batch_size = 5000
            refresh = False
            requests_per_second = -1

        captured = {}
        original_request_json = normalize_npn_verbatim_traits_es.request_json
        try:
            normalize_npn_verbatim_traits_es.request_json = (
                lambda args, method, path, payload=None, params=None, label=None: captured.setdefault("params", params)
                or {"task": "node:1"}
            )
            normalize_npn_verbatim_traits_es.run_update_by_query(Args())
        finally:
            normalize_npn_verbatim_traits_es.request_json = original_request_json

        self.assertEqual(captured["params"]["wait_for_completion"], "false")

    def test_wait_for_task_treats_missing_completed_task_as_done(self):
        original_get_task = normalize_npn_verbatim_traits_es.get_task
        try:
            normalize_npn_verbatim_traits_es.get_task = (
                lambda _args, _task_id: (_ for _ in ()).throw(
                    RuntimeError("task status failed: task [node:1] isn't running and hasn't stored its results")
                )
            )
            response = normalize_npn_verbatim_traits_es.wait_for_task(
                type("Args", (), {"poll_interval": 1})(),
                "node:1",
            )
        finally:
            normalize_npn_verbatim_traits_es.get_task = original_get_task

        self.assertTrue(response["completed"])
        self.assertTrue(response["resultUnavailable"])


if __name__ == "__main__":
    unittest.main()
