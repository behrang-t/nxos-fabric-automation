# Phase 2: parsed evidence

The engine consumes a completed `collection_results.json` and produces
`parsed_evidence.json` beside it. It performs extraction and validates structure;
it does not compare a golden state or make a network health assessment.

## Execution and ownership

`scripts/parse_evidence.py` owns selection, result classification, and JSON output.
Genie selects the parser class using the NX-OS command. The class's `parse()`
method receives the saved stdout and validates the result against its Schema.
The fixture test runner `folder_parsing_job.py` is not used at runtime.
No SSH connection is opened; the device object's execute method rejects calls.

The final localhost play writes collection JSON, renders the collection report,
and runs this engine once using `ansible_playbook_python`. Install requirements
in the same environment that runs `ansible-playbook`. For saved evidence:

```bash
python scripts/parse_evidence.py --input evidence_pack/structured/RUN_ID/collection_results.json
```

Optional `--output PATH` changes the destination. Re-running replaces that parsed
artifact atomically; raw evidence and collection JSON are preserved. An output
path equal to the input is rejected. Malformed JSON or an invalid collection
contract stops the run before replacing an existing artifact. An old artifact
may therefore remain after failure: always check the exit code and source hash.

Exit codes: `0` complete parsing, `2` partial parsing with an artifact written,
`1` fatal run failure. Ansible accepts 0 and 2 and displays counts including
errors. A partial run is not a successful network assessment.

## Coverage

All 25 distinct operational commands in the collection plan are eligible for
Genie parsing. The two Multisite link commands run on border gateways.
Configuration snapshots and all `show running-config` commands are retained as
`not_applicable` on collection success. Failed collection takes precedence.

Two explicit lookup aliases reuse existing parsers without changing raw text:

| Collected command | Genie lookup command |
| --- | --- |
| `show vlan brief` | `show vlan` |
| `show bgp ipv4 unicast summary` | `show bgp vrf all ipv4 unicast summary` |

The alias changes only lookup; no aliased command is sent to the network.
Fixtures exercise both aliases with project output. Parser output fields are
those provided by the pinned version; successful parsing does not prove that
every raw field was extracted. In particular, extra EVPN Summary route-type
counters remain raw evidence until a concrete consumer requires them.

The iputils probe parser extracts transmitted/received counts, packet loss and
RTT when present. It requires return code 0 or 1 plus complete statistics.
Return code 1 with 100% loss remains parsed measurement data. Return code 2 or
missing statistics is a parsing error; original rc/stderr and evidence references
are retained. This does not change the Phase 1 probe collection policy.

## Per-record outcomes

| Status | Meaning |
| --- | --- |
| `parsed` | Extraction and parser Schema validation succeeded |
| `empty` | A recognized empty form, or explicit OSPF zero counts |
| `skipped_collection_error` | Collector reported failure; parser not called |
| `invalid_input` | Completed operational record has blank stdout |
| `not_applicable` | Successful configuration evidence reserved for later work |
| `parser_not_found` | Genie cannot select a parser for this command |
| `parse_error` | Extraction/Schema failure or unrecognized empty result |

Every command, snapshot and probe present in the input receives one output
record. A failed preflight leaves error records supplied by the Collector;
the engine preserves them and their causes without retrying device access.
Baseline and post-probe are separate records, including duplicate commands.
`record_id` combines phase, device and input position and is stable for the same
input. The collection schema checks structure, not the semantic accuracy of the
Collector's planned/completed counters.

Each record preserves phase, device, role, original command, category, raw file,
collection status/message, parser selection, parsed data and error details.
Probe records additionally preserve ID and measurement context. `raw_file`
remains relative to the evidence root, as in the collection contract. Full stdout
is not duplicated: the source collection artifact and its SHA-256 identify it.
Genie numeric dictionary keys become strings in JSON; consumers must use the
on-disk contract rather than Python-only key types.

## Empty-result boundary

`SchemaEmptyParserError` alone does not prove a valid empty table. The engine
recognizes only these forms, with offline fixtures:

- OSPF Summary: successfully parsed instances all explicitly report zero
  neighbors. Preserve the structured process/VRF data.
- EVPN route-type 2/3/5: the entire nonblank output is the observed routing-table
  information header. Additional lines invalidate this empty-form match.
- L2route MAC-IP: the entire normalized output matches the saved legend/header
  in `parsing/l2route_empty.txt`. Unknown extra rows invalidate the match.

The EVPN one-line form is an observed project convention, not proof that transport
could never truncate output at that exact point. Those bytes alone cannot
distinguish such truncation. Other unknown empty forms remain `parse_error` until
supported by a concrete fixture. We do not classify generic empty dictionaries
inside arbitrary successfully parsed results as empty tables.

## Dependency and provenance

Genie/pyATS are based on 26.8. `genie.libs.parser` is installed from fork commit
`9957e9e3665333277f94a30390d6029d3989b90e`, containing OSPF and EVPN/MVPN changes
(upstream PRs #1003 and #1004). This integration does not contain the separate
NVE-detail contribution (#1002). Ordinary `show interface nve1` parsing still
uses the existing parser; no promise is made to extract the additional fields
covered by #1002.

Another known gap remains in the pinned `show l2route evpn mac-ip all` parser:
remote BGP rows with `--` flags and a next-hop `(Label: ...)` suffix in the older
project capture raise `SchemaEmptyParserError`. The engine correctly records
these populated outputs as `parse_error`. Fixing that parser is separate work;
successful local/HMM rows are still parsed. Do not turn this error into `empty`.

Output includes package version, installed VCS commit when present, editable
status, engine version and source SHA-256. A local editable install without VCS
installation metadata honestly reports a null commit. Project deployment should
use the pinned VCS dependency, not the development checkout.

## Verification

Run `python -m unittest discover -s tests -v` and the collector syntax check.
New tests use real saved output for OSPF, EVPN, lookup aliases and Multisite,
and simulated failure cases for continuation, missing parser, schema errors,
unknown/truncated output, ping errors, and atomic output behavior.
They do not require a running lab. Live collection followed by automatic parsing
remains pending the next lab startup.
