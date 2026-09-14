"""Offline runtime tests: real Genie parsers and explicit failure boundaries."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import jsonschema
from scripts import parse_evidence as engine

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = json.loads((ROOT / "tests/fixtures/parsing/operational_samples.json").read_text())


def record(command="show ip ospf neighbors", output=None, status="completed"):
    return {"category": "test", "command": command, "execution_status": status,
            "stdout": SAMPLES.get(command, "") if output is None else output,
            "message": "preflight failed" if status == "error" else "",
            "raw_file": "raw/test/baseline/leaf1/test.txt"}


def payload(commands):
    device = {"role": "leaf", "management_ip": "192.0.2.1", "collection_status": "partial",
              "planned_commands": len(commands), "completed_commands": 0, "commands": commands}
    return {"schema_version": 1,
            "run": {"run_id": "test", "started_at": "test", "completed_at": "test",
                    "collection_status": "partial", "collection_plan": {"file": "collection_plan.yml", "schema_version": 1}},
            "fabric": {"name": "test", "site": "test"},
            "summary": {"baseline": {"planned": len(commands), "completed": 0, "errors": len(commands)},
                        "post_probe": {"planned": 0, "completed": 0, "errors": 0},
                        "configuration_snapshots": {"planned": 0, "collected": 0, "errors": 0},
                        "traffic_probes": {"planned": 0, "executed": 0, "execution_errors": 0}},
            "baseline": {"devices": {"leaf1": device}}, "post_probe": {"devices": {}},
            "configuration_snapshots": {"devices": {}}, "traffic_probes": [],
            "artifacts": {"raw_root": "raw/test", "structured_results": "collection_results.json", "report": "evidence_report.md"}}


class ParsingTests(unittest.TestCase):
    def test_real_command_lookup_and_aliases(self):
        for command in SAMPLES:
            with self.subTest(command=command):
                result = engine.parse_record(record(command), "baseline", "leaf1")
                self.assertEqual(result["parsing_status"], "parsed", result)
                self.assertTrue(result["data"])
                self.assertEqual(result["command"], command)

    def test_collection_error_never_calls_parser(self):
        with patch.object(engine, "get_parser", side_effect=AssertionError("called")) as lookup:
            result = engine.parse_record(record(output="partial text", status="error"), "baseline", "leaf1")
        lookup.assert_not_called()
        self.assertEqual(result["parsing_status"], "skipped_collection_error")
        self.assertEqual(result["collection_message"], "preflight failed")

    def test_blank_success_is_invalid_input(self):
        self.assertEqual(engine.parse_record(record(output=" \n"), "baseline", "leaf1")["parsing_status"], "invalid_input")

    def test_missing_parser_is_distinct(self):
        result = engine.parse_record(record("show nonexistent evidence command", "some output"), "baseline", "leaf1")
        self.assertEqual(result["parsing_status"], "parser_not_found")

    def test_unknown_and_truncated_are_not_empty(self):
        for output in ("Unrecognized non-empty output", "BGP routing table information"):
            result = engine.parse_record(record("show bgp l2vpn evpn route-type 5", output), "baseline", "leaf1")
            self.assertEqual(result["parsing_status"], "parse_error")

    def test_recognized_empty_and_unparsed_extra_row(self):
        pairs = [("show bgp l2vpn evpn route-type 5", "BGP routing table information for VRF default, address family L2VPN EVPN"),
                 ("show l2route evpn mac-ip all", (ROOT / "parsing/l2route_empty.txt").read_text())]
        for command, output in pairs:
            with self.subTest(command=command):
                self.assertEqual(engine.parse_record(record(command, output), "baseline", "leaf1")["parsing_status"], "empty")
                self.assertEqual(engine.parse_record(record(command, output + "\nUNKNOWN ROW"), "baseline", "leaf1")["parsing_status"], "parse_error")

    def test_ospf_zero_keeps_structured_data(self):
        raw = ' OSPF Process ID 1 VRF default\n Total number of neighbors: 0\n'
        result = engine.parse_record(record(output=raw), "baseline", "leaf1")
        self.assertEqual(result["parsing_status"], "empty", result)
        self.assertTrue(result["data"])

    def test_populated_l2route_unhandled_by_pinned_parser_is_error(self):
        raw = (ROOT / "parsing/l2route_empty.txt").read_text()
        raw += "\n250         0050.0000.0e00 10.10.10.11                             BGP    --                 0         10.10.0.1 (Label: 30001)\n"
        result = engine.parse_record(record("show l2route evpn mac-ip all", raw), "baseline", "bgw1")
        self.assertEqual(result["parsing_status"], "parse_error")
        self.assertIsNone(result["data"])

    def test_ping_loss_is_data_execution_error_is_not(self):
        for rc, output, expected in [
            (0, "5 packets transmitted, 5 received, 0% packet loss, time 4ms", "parsed"),
            (1, "5 packets transmitted, 0 received, 100% packet loss, time 4ms", "parsed"),
            (2, "ping: bad address", "parse_error"),
            (0, "unexpected output", "parse_error"),
        ]:
            with self.subTest(rc=rc, output=output):
                value = dict(record("ping", output), return_code=rc)
                result = engine.parse_record(value, "traffic_probes", "server1")
                self.assertEqual(result["parsing_status"], expected)
                self.assertEqual(result["measurement"]["return_code"], rc)

    def test_config_scope_and_failed_snapshot(self):
        result = engine.parse_record(record("show running-config", "hostname leaf1"), "baseline", "leaf1")
        self.assertEqual(result["parsing_status"], "not_applicable")
        result = engine.parse_record({"collection_status": "error", "message": "preflight failed"}, "configuration_snapshots", "leaf1")
        self.assertEqual(result["parsing_status"], "skipped_collection_error")

    def test_record_error_continues_and_phases_stay_separate(self):
        source = payload([record(output="bad"), record()])
        source["post_probe"]["devices"]["leaf1"] = copy.deepcopy(source["baseline"]["devices"]["leaf1"])
        result = engine.parse_collection(source)
        self.assertEqual([r["parsing_status"] for r in result["records"]], ["parse_error", "parsed", "parse_error", "parsed"])
        self.assertEqual(len({r["record_id"] for r in result["records"]}), 4)

    def test_schema_failure_continues(self):
        class BadParser:
            def __init__(self, device):
                pass

            def parse(self, **kwargs):
                from genie.metaparser.util.exceptions import SchemaMissingKeyError
                raise SchemaMissingKeyError("missing field")

        with patch.object(engine, "get_parser", return_value=(BadParser, {})):
            result = engine.parse_collection(payload([record(), record()]))
        self.assertEqual(len(result["records"]), 2)
        self.assertTrue(all(r["parsing_status"] == "parse_error" for r in result["records"]))

    def test_cli_output_schema_and_no_input_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "collection_results.json"
            path.write_text(json.dumps(payload([record(), record(status="error")])) )
            before = path.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(engine.main(["--input", str(path)]), 2)
            result = json.loads(path.with_name("parsed_evidence.json").read_text())
            jsonschema.validate(result, json.loads((ROOT / "schemas/parsed_evidence.schema.json").read_text()))
            self.assertEqual(result["summary"]["records"], 2)
            self.assertEqual(path.read_bytes(), before)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                engine.main(["--input", str(path), "--output", str(path)])

    def test_bad_input_preserves_previous_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "collection_results.json"
            output = path.with_name("parsed_evidence.json")
            output.write_text("previous artifact")
            for contents in ("broken JSON", '{"schema_version": 99}'):
                path.write_text(contents)
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(engine.main(["--input", str(path)]), 1)
                self.assertEqual(output.read_text(), "previous artifact")


if __name__ == "__main__":
    unittest.main()
