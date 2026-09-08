# NX-OS Fabric Automation Lab

This project is a multi-phase automation lab built around an NX-OS VXLAN EVPN Multisite fabric in EVE-NG.

Phase 1 collects operational evidence from the fabric, captures the running configuration of each NX-OS device and runs controlled reachability probes from Linux endpoints. It does not decide whether the network is healthy. Its job is to capture a complete, reviewable record that can be assessed later without reconnecting to the lab.

No device configuration is changed during this phase.

## Lab scope

The topology includes:

- Site 1 in AS 65001
- Site 2 in AS 65002
- a DCI node in AS 65003
- two border gateways
- three leaf switches and two spines
- OSPF and PIM in the site underlays
- BGP EVPN across the overlay and DCI
- L2VNI and L3VNI tenant services
- three Alpine Linux endpoints connected to the leaf switches

The endpoint addressing used by the probes is:

| Endpoint | Attached leaf | Data-plane address | Default gateway |
|---|---|---|---|
| `server1` | `leaf1` | `10.10.10.10/24` | `10.10.10.1` |
| `server2` | `leaf2` | `10.10.10.11/24` | `10.10.10.1` |
| `server3` | `leaf3` | `10.10.11.11/24` | `10.10.11.1` |

Each endpoint has a separate management interface for Ansible and uses `eth0` for test traffic.

## Phase 1 workflow

The collection flow is:

```text
running-configuration snapshot and baseline collection
        ↓
endpoint traffic probes
        ↓
post-probe learning collection
        ↓
structured results and Markdown report
```

### Baseline collection

NX-OS commands are selected by device role and executed independently. The baseline covers:

- platform and interface state
- OSPF/PIM underlay state
- BGP EVPN control plane
- BGP configuration
- NVE peers and VNIs
- tenant VLAN and routing state
- Multisite and DCI state
- MAC, ARP and EVPN Type-2 learning before the probes

If an individual command fails, the remaining commands still run. The failure is preserved in the raw output and the run is marked `partial` instead of being silently discarded.

### Running-configuration snapshots

The playbook runs `show running-config` once on every in-scope NX-OS device and stores the returned configuration separately from the operational command output:

```text
raw/<RUN_ID>/configuration_snapshots/<device>/running_config.cfg
```

These snapshots provide the configuration input for the section-aware compliance checks planned for Phase 2. They are observed device state, not golden configurations.

Snapshot files are written with mode `0600`. Because a running configuration can still contain sensitive values, the files should be reviewed and sanitized before they are added to a public repository.

### Endpoint traffic probes

Nine reachability measurements are run from the Alpine endpoints:

- three endpoint-to-gateway probes
- two same-subnet probes, one in each direction
- four inter-subnet probes, covering both directions between the sites

Before each measurement, two preliminary packets are sent to populate ARP and MAC state. Only the following five-packet measurement is retained in the evidence pack.

Each measured probe uses a fixed packet count:

```bash
ping -I <interface> -c 5 -i 1 -W 2 <destination>
```

The command, standard output, standard error and return code are stored for every probe. A return code of `1` is still a successfully collected measurement; it means the ping did not meet its success condition. A missing execution result is a collection error.

### Post-probe collection

After the traffic tests, the playbook collects the learning-related command groups again. Baseline and post-probe files use the same names, so changes in MAC, ARP and EVPN Type-2 state can be compared directly.

For example:

```bash
diff -u \
  evidence_pack/raw/<RUN_ID>/baseline/leaf1/tenant_learning.txt \
  evidence_pack/raw/<RUN_ID>/post_probe/leaf1/tenant_learning.txt
```

## Collection plan

`collection_plan.yml` is the declarative input for Phase 1. It defines:

- baseline-only command groups
- command groups collected both before and after the probes
- probe defaults
- probe source, destination and category

The playbook combines this data with the inventory at runtime. The rendered command used for a probe is also the command written to the raw and structured artifacts.

## Evidence layout

Every execution receives a UTC run ID and writes all artifacts under that ID:

```text
evidence_pack/
├── raw/
│   └── <RUN_ID>/
│       ├── baseline/
│       │   └── <device>/
│       │       └── <command_group>.txt
│       ├── configuration_snapshots/
│       │   └── <device>/
│       │       └── running_config.cfg
│       ├── traffic_probes/
│       │   └── <probe_id>.txt
│       └── post_probe/
│           └── <device>/
│               └── <command_group>.txt
├── structured/
│   └── <RUN_ID>/
│       └── collection_results.json
└── reports/
    └── <RUN_ID>/
        └── evidence_report.md
```

The three output forms serve different purposes:

- `raw/` preserves command-level evidence, configuration snapshots and probe output for review and troubleshooting.
- `collection_results.json` provides a stable input for automated assessment.
- `evidence_report.md` is a concise index of what ran, what was captured and where the files were written.

The report describes collection completeness only. It does not translate operational state or probe results into `PASS` or `FAIL`.

## Project structure

```text
.
├── README.md
├── ansible.cfg
├── collection_plan.yml
├── requirements.txt
├── requirements.yml
├── inventory/
│   ├── inventory.yml
│   └── group_vars/
│       ├── all/
│       │   └── vault.yml
│       ├── nxos.yml
│       └── traffic_endpoints.yml
├── playbooks/
│   └── collect_vxlan_evpn_evidence.yml
├── templates/
│   └── evidence_report.md.j2
├── evidence_pack/
│   ├── raw/
│   ├── structured/
│   └── reports/
└── docs/
    ├── topology_notes.md
    └── topology_multisite_lab.png
```

The external WAN router remains in the inventory for topology context but is outside the current collection scope.

## Running Phase 1

From the project directory, activate the existing virtual environment and check the playbook syntax:

```bash
source .venv-nxos-fabric/bin/activate

ansible-playbook --syntax-check \
  playbooks/collect_vxlan_evpn_evidence.yml
```

Run the collection:

```bash
ansible-playbook \
  playbooks/collect_vxlan_evpn_evidence.yml
```

The project `ansible.cfg` supplies the inventory and Vault password file when the command is run from this directory.

To verify that the structured artifact contains valid JSON:

```bash
python -m json.tool \
  evidence_pack/structured/<RUN_ID>/collection_results.json
```

`json.tool` checks JSON syntax and prints the document in a readable form. It does not validate the network state or the meaning of the collected values.

## Phase 2

Phase 2 will consume the Phase 1 artifacts through two separate check families.

Operational command output will be parsed with Genie or a small custom parser where Genie coverage is not available. The normalized observed state will be evaluated against `expected_state.yml`. The first operational assessment checks are planned for:

- OSPF neighbors
- BGP EVPN sessions
- NVE state
- VNI state
- EVPN route types
- endpoint learning
- endpoint reachability

Running-configuration snapshots will follow a separate path. `ciscoconfparse2` will extract the relevant configuration sections, normalize them and compare them with the corresponding golden configuration.

Both operational assessment checks and configuration compliance checks will feed the same deterministic Check Engine. It will assign statuses such as `PASS`, `FAIL`, `WARNING`, `NOT TESTED` and `NOT APPLICABLE`.

Keeping assessment separate from collection means a fabric can produce a complete evidence run even when some of its control-plane, data-plane or configuration checks fail.

## Later work

Phase 3 will introduce human-approved remediation. An AI-assisted layer may propose a structured remediation plan, but a policy check and explicit operator approval will be required before an automation executor applies it.

Each approved change cycle will reuse the existing workflow:

```text
remediation
    ↓
Phase 1 recollection
    ↓
Phase 2 reassessment
    ↓
healthy or another approved plan
```

Remediation backups, rollback and write-command policies are outside the current
collection workflow. Collection connection-stop behavior is described below.

## Phase 1 collection hardening

See [the collection contract](docs/phase1-collection-contract.md) for error rules,
connection handling, role ownership, and lab acceptance cases. The machine output
contract is [collection_results.schema.json](schemas/collection_results.schema.json).
Run offline checks with `python3 -m unittest discover -s tests -v` after installing
the project requirements. These checks do not connect to the lab.

### Responsibilities and data flow

The goal is complete, traceable evidence collection with explicit errors, not a
network health verdict. Command groups select evidence by role and stage; they
do not define future parser or assessment boundaries.

| Component | Responsibility |
| --- | --- |
| `inventory/inventory.yml` | Defines devices and group-owned `fabric_role`; the playbook checks exclusive role-group membership. |
| `collection_plan.yml` | Defines role-aware command groups and traffic probes. Pre/post groups run in baseline and again in post-probe. |
| `playbooks/collect_vxlan_evpn_evidence.yml` | Orchestrates snapshot/baseline, probes, post-probe, raw files and final reports. |
| `playbooks/tasks/collect_cli_command.yml` | Sends each planned command if preflight passed and appends Python's classified result. |
| `filter_plugins/collection_results.py` | Owns all CLI result decisions: `execution_status`, `msg`, and `stop_device`, preserving stdout. It does not connect, keep host state, or append records. |
| `templates/evidence_report.md.j2` | Renders the final structured payload as a human-readable collection report. |
| `schemas/collection_results.schema.json` | Describes the version-1 JSON interface; full schema validation is not wired into runtime. |

The first NX-OS command is `show hostname` (preflight). Python validates its
result; YAML stores the returned `stop_device` decision for this run. Only
preflight decides whether later sends are allowed. The snapshot follows it.
For baseline and post-probe commands, the main playbook includes the task file
once per planned command. The task file checks the host flag before sending,
registers the result, and passes it plus the previous stop state to Python.
Python returns a classified result. YAML appends it to the stage's results list
without changing the preflight decision. YAML does not
interpret `unreachable`, `failed`, or CLI error text. An unavailable host produces
an unsent/error record for each remaining command instead of silently losing
evidence coverage. The include still runs to create those records.

The main playbook writes group raw files from these validated results. In the
final localhost play, it reads registered host results, builds
`collection_results.json`, then renders the Markdown report from the same
payload. Each operational record retains category, command, execution status,
complete stdout, message and raw-file reference. JSON-escaped newlines decode
back into the original multiline string for future parsers.

### Connection-stop policy

Preflight failure stops sends to that device for the run. After preflight
success, every subsequent command error is recorded and collection continues.

Preflight uses the existing `network_cli` transport, `show hostname`, and
`ansible_connect_timeout: 30`. Python applies the same CLI validation used for
collection. Failure is labelled unreachable by Collector policy; the original
error is retained. This is not a claim about the underlying failure cause.
The timeout setting is a connection setting, not a total preflight deadline.

Preflight evidence is saved as `raw/<RUN_ID>/baseline/<device>/preflight.txt`.
On rejection, snapshot and operational error messages retain the preflight cause;
every planned command still gets a record. The public JSON schema stays at v1.
Ansible's `unreachable` flag can classify an error but never triggers a new stop
after preflight. `ignore_errors` and `ignore_unreachable` allow bookkeeping and
continuation; the preflight flag alone controls whether a command is sent.

The task include still runs once per planned command to retain all records.
Probe execution and return-code recording retain their existing behavior.

### Offline verification

| Test | Coverage |
| --- | --- |
| `test_stop_decision_and_error_precedence` | All post-preflight errors retain the existing stop state; error flags take precedence over partial stdout. |
| `test_preflight_gate` | Failed/empty/CLI-error preflight stops sends; original causes survive in unsent records; successful preflight allows subsequent errors without a new stop. |
| `test_absent_empty_and_transport_results` | Missing/empty output, supplied `failed`, `unreachable` and `skipped` results yield an error and message; empty stdout does not invent an unreachable flag. |
| `test_cli_errors_and_context` | Invalid/Error/Incomplete/Ambiguous percent-prefixed lines are recognized, output is retained, and normal `Errors: 0` / `25%` text is not rejected. |
| `test_full_output_round_trip` | A synthetic 200-line output survives validation and JSON serialization/deserialization intact. |
| `test_inventory_roles_owned_by_groups` | Roles exist on groups and are not repeated on individual NX-OS hosts. |
| `test_render_actual_payload_with_mixed_results` | The actual payload/report templates handle supplied success/error/unsent records, snapshot failure, counts, output, paths, probe rc/stderr, and expected field names. |

These are regression tests with synthetic inputs, not real network tests. They
do not execute Ansible's task include, establish SSH sessions, prove that later
commands are not sent, or prove that ordinary failures allow the next command.
Failure-flag precedence with nonempty stdout is covered by the decision test. Full JSON Schema
validation is not exercised; field-name checks are only a partial contract check.
Live acceptance of this preflight change is pending the next lab startup.
Do not describe the offline checks as successful device acceptance.

At the start of the parser phase, report our observed `network_cli` failure
results upstream, referencing ansible.netcommon issue 340. Include installed
versions and sanitized evidence; no upstream report has been submitted yet.

A future, separately designed Phase 1 refactor will move collection to pyATS and
Unicon. Session lifecycle and error policy will be explicitly tested there too.
