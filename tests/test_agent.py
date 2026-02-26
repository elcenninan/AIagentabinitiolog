import unittest

from agent import JobRecord, parse_log, retrieve_best_job


class AgentTests(unittest.TestCase):
    def test_parse_log_extracts_job_and_error(self):
        log = """
        INFO starting /abinitio/graphs/gl_orders_fact_load.mp
        ERROR row rejected
        ERROR ABORT due to bad record
        """
        parsed = parse_log(log)
        self.assertEqual(parsed["detected_job"], "gl_orders_fact_load")
        self.assertIn("ABORT", parsed["failure_reason"])

    def test_retrieve_best_job(self):
        parsed = {
            "detected_job": "gl_payments_fact_load",
            "tokens": {"payments", "stg_payments", "error"},
        }
        jobs = [
            JobRecord("gl_orders_fact_load", "stg_orders", "dw_orders_fact", ["orders"]),
            JobRecord("gl_payments_fact_load", "stg_payments", "dw_payments_fact", ["payments"]),
        ]
        best = retrieve_best_job(parsed, jobs)
        self.assertEqual(best.job_name, "gl_payments_fact_load")


if __name__ == "__main__":
    unittest.main()
