#!/usr/bin/env python3
"""Parse one saved collection run without connecting to any device."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import sys
import tempfile

import jsonschema
from genie.conf.base import Device
from genie.libs.parser.utils import get_parser
from genie.libs.parser.utils.common import ParserNotFound
from genie.metaparser.util.exceptions import SchemaEmptyParserError


ROOT = Path(__file__).resolve().parents[1]
ENGINE_VERSION = "1.0.0"
COMMAND_ALIASES = {
    "show vlan brief": "show vlan",
    "show bgp ipv4 unicast summary": "show bgp vrf all ipv4 unicast summary",
}


def verified_empty(command, output):
    """Recognize only observed empty forms; unknown forms remain parse errors."""
    lines = [" ".join(line.split()) for line in output.splitlines() if line.strip()]
    if re.fullmatch(r"show bgp l2vpn evpn route-type [235]", command):
        return len(lines) == 1 and bool(re.fullmatch(
            r"BGP routing table information for VRF \S+, address family L2VPN EVPN",
            lines[0],
        ))
    if command == "show l2route evpn mac-ip all":
        # Exact observed legend/header; do not ignore arbitrary unparsed lines.
        expected = (ROOT / "parsing/l2route_empty.txt").read_text()
        return lines == [" ".join(s.split()) for s in expected.splitlines() if s.strip()]
    return False


def ospf_zero(data):
    counts = [instance["total_neighbors"]
              for vrf in data.get("vrf", {}).values()
              for instance in vrf.get("address_family", {}).get("ipv4", {}).get("instance", {}).values()]
    return bool(counts) and all(count == 0 for count in counts)


def parse_ping(record):
    """Parse iputils measurement statistics, retaining loss as data."""
    if record.get("return_code") not in (0, 1):
        raise ValueError("Ping did not finish a usable measurement (return_code is not 0 or 1).")
    match = re.search(
        r"(?m)^\s*(?P<transmitted>\d+) packets transmitted,\s*"
        r"(?P<received>\d+) (?:packets )?received(?:,\s*\+\d+ errors)?,\s*"
        r"(?P<packet_loss>[\d.]+)% packet loss(?:,.*)?$", record["stdout"])
    if not match:
        raise ValueError("No complete iputils packet statistics found.")
    data = {"transmitted": int(match["transmitted"]),
            "received": int(match["received"]), "packet_loss": float(match["packet_loss"])}
    if not 0 <= data["received"] <= data["transmitted"] or not 0 <= data["packet_loss"] <= 100:
        raise ValueError("Invalid iputils measurement counters.")
    rtt = re.search(r"(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", record["stdout"])
    if rtt:
        data["rtt_ms"] = dict(zip(("min", "avg", "max", "mdev"), map(float, rtt.groups())))
    return data


def parse_record(record, phase, device, role=None, index=0):
    """One result for every input record; classification policy lives here."""
    snapshot = phase == "configuration_snapshots"
    status = record.get("collection_status") if snapshot else record.get("execution_status")
    command = record.get("command")
    result = {
        "record_id": f"{phase}/{device}/{index}", "phase": phase,
        "device": device, "role": role, "command": command,
        "category": record.get("category"), "probe_id": record.get("probe_id"),
        "raw_file": record.get("raw_file"), "collection_status": status,
        "collection_message": record.get("message", ""),
        "parsing_status": None, "parser": None, "data": None, "error": None,
    }
    if phase == "traffic_probes":
        result["measurement"] = {k: record.get(k) for k in
                                 ("destination", "source_interface", "return_code", "stderr")}
    if status not in ("completed", "collected"):
        result["parsing_status"] = "skipped_collection_error"
        return result
    if snapshot or (command or "").startswith("show running-config"):
        result["parsing_status"] = "not_applicable"
        return result
    if not isinstance(record.get("stdout"), str) or not record["stdout"].strip():
        result.update(parsing_status="invalid_input", error={"type": "EmptyInput", "message": "Collection was marked completed but stdout is blank."})
        return result
    try:
        if phase == "traffic_probes":
            result["parser"] = {"class": "parse_ping", "module": "scripts.parse_evidence", "command": command}
            result["data"] = parse_ping(record)
        else:
            lookup = COMMAND_ALIASES.get(command, command)
            dev = Device(name=device, os="nxos")
            # This object must never initiate collection, even if a parser is faulty.
            def offline_only(*args, **kwargs):
                raise RuntimeError("Network execution is disabled in the evidence parser.")
            dev.execute = offline_only
            try:
                cls, kwargs = get_parser(lookup, dev)
            except ParserNotFound as exc:
                result.update(parsing_status="parser_not_found", error={"type": type(exc).__name__, "message": str(exc)})
                return result
            result["parser"] = {"class": cls.__name__, "module": cls.__module__, "command": lookup}
            result["data"] = cls(device=dev).parse(output=record["stdout"], **kwargs)
            if not result["data"]:
                raise SchemaEmptyParserError("Parser returned no data")
        result["parsing_status"] = "empty" if command == "show ip ospf neighbors" and ospf_zero(result["data"]) else "parsed"
    except SchemaEmptyParserError as exc:
        if verified_empty(command, record["stdout"]):
            result.update(parsing_status="empty", data={})
        else:
            result.update(parsing_status="parse_error", data=None,
                          error={"type": type(exc).__name__, "message": "No data extracted; output is not a recognized empty form."})
    except Exception as exc:
        result.update(parsing_status="parse_error", data=None,
                      error={"type": type(exc).__name__, "message": str(exc)})
    return result


def package_provenance():
    dist = metadata.distribution("genie.libs.parser")
    direct = dist.read_text("direct_url.json")
    info = json.loads(direct) if direct else {}
    return {"version": dist.version, "commit": info.get("vcs_info", {}).get("commit_id"),
            "editable": info.get("dir_info", {}).get("editable", False)}


def parse_collection(payload):
    schema = json.loads((ROOT / "schemas/collection_results.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(payload)
    records = []
    for phase in ("baseline", "post_probe"):
        for host, value in payload[phase]["devices"].items():
            for index, record in enumerate(value["commands"]):
                records.append(parse_record(record, phase, host, value.get("role"), index))
    for host, record in payload["configuration_snapshots"]["devices"].items():
        records.append(parse_record(record, "configuration_snapshots", host, record.get("role")))
    for index, record in enumerate(payload["traffic_probes"]):
        records.append(parse_record(record, "traffic_probes", record["source"], "end_user", index))
    counts = dict(Counter(record["parsing_status"] for record in records))
    failures = sum(counts.get(status, 0) for status in
                   ("skipped_collection_error", "invalid_input", "parser_not_found", "parse_error"))
    return {"schema_version": 1, "engine_version": ENGINE_VERSION,
            "run_id": payload["run"]["run_id"], "generated_at": datetime.now(timezone.utc).isoformat(),
            "parser_package": package_provenance(), "status": "partial" if failures else "complete",
            "summary": {"records": len(records), "counts": counts}, "records": records}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    output = args.output or args.input.with_name("parsed_evidence.json")
    if output.resolve() == args.input.resolve():
        parser.error("output must not overwrite collection evidence")
    try:
        raw = args.input.read_bytes()
        result = parse_collection(json.loads(raw))
        result["source"] = {"file": args.input.name, "sha256": hashlib.sha256(raw).hexdigest()}
        # JSON object keys are strings on disk, including numeric keys from Genie.
        result = json.loads(json.dumps(result, allow_nan=False))
        schema = json.loads((ROOT / "schemas/parsed_evidence.schema.json").read_text())
        jsonschema.Draft202012Validator(schema).validate(result)
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                             prefix=".parsed-", delete=False) as stream:
                temp = Path(stream.name)
                json.dump(result, stream, indent=2, ensure_ascii=False, allow_nan=False)
                stream.write("\n")
            os.replace(temp, output)
        finally:
            if temp and temp.exists():
                temp.unlink()
        print(json.dumps({"output": str(output), "status": result["status"], **result["summary"]}))
        return 2 if result["status"] == "partial" else 0
    except (OSError, ValueError, jsonschema.ValidationError, metadata.PackageNotFoundError) as exc:
        print(f"Parsing run failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
