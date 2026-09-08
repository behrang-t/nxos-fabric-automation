# Phase 1 collection contract

The collector runs preflight, then preserves snapshot/baseline → traffic probes
→ post-probe order.
Role groups own `fabric_role` in inventory. Each host must belong to exactly one
NX-OS role group. The group name `leaves` is intentional.

The baseline includes the pre-traffic observation of every
`pre_post_comparison_command_sets` group. These same groups run again in
`post_probe`; there is no separate pre_probe directory.

## Outcome rules

Every planned operational command has a record. `execution_status` is
`completed` only when execution succeeded, returned nonblank text, and no
recognized percent-prefixed CLI error line was detected. Otherwise it is
`error`; `message` explains why and `stdout` preserves returned text unchanged.
There is no PASS/FAIL assessment in this phase.

All CLI result policy lives in `collection_results.py`. The filter receives the
Ansible result and prior device stop state, returning `execution_status`, `msg`
and `stop_device` alongside preserved output. Only preflight establishes the stop
decision. YAML executes commands, stores that decision and appends records.
Raw/JSON rendering consumes the
classified status without reinterpreting Ansible failure flags. `stop_device`
is internal execution state and is not added to the public JSON schema.

The CLI detector matches percent-prefixed Invalid, Error, Incomplete, or
Ambiguous messages at the beginning of a line, ignoring case. It is a bounded
collection validator, not a complete catalogue of all NX-OS errors. Add further
patterns only with real output fixtures.

Preflight executes `show hostname` using the existing transport and
`ansible_connect_timeout: 30`. Any unsuccessful preflight is classified as
unreachable by Collector policy and prevents snapshot, baseline and post-probe
sends to that device for this run. Preserve the cause in every unsent error
record. This operational label does not identify the underlying failure cause.
Preflight evidence is written to `baseline/<device>/preflight.txt` in the run's
raw directory. The existing snapshot/command messages carry its failure cause
into JSON and the human report; schema version 1 is unchanged.

After successful preflight, record every later command error and continue.
There is no later stop transition, including for an Ansible `unreachable` flag.

Probe handling is unchanged from the base revision: a registered result with an
exit code is recorded as completed, retaining stdout, stderr and the exit code
without interpreting them. A missing measurement result/exit code is a collection
error. This status does not assert successful ping execution or reachability.

## Machine-readable output

`schemas/collection_results.schema.json` describes schema version 1. Existing
field names and types are retained. An operational command record contains:

| Field | Meaning |
| --- | --- |
| category | Collection group; does not constrain future checks |
| command | Actual planned CLI command |
| execution_status | completed or error |
| stdout | Complete command output as one string; JSON escapes line breaks |
| message | Collection error reason, with module context if supplied |
| raw_file | Evidence-root-relative file path, or null if no file was written |

Consumers decode JSON before handing `stdout` to a parser. Never split the
serialized JSON into lines to retrieve command output. The report reads the same
payload and lists collection errors explicitly. Summary error counts include
commands that could not be sent; they are not claims about attempted execution.

Configuration snapshots are written only after successful CLI validation.
A failed snapshot has status error and a null raw_file. The snapshot message
contains the failure reason. Operational command raw files retain invalid CLI
output for investigation.

## Acceptance checks on the lab

1. Normal collection: complete multiline stdout and valid artifact references.
2. Invalid CLI: error record; the next command is still sent.
3. Empty stdout: error record; the next command is still sent.
4. Failed preflight: no snapshot, baseline or post-probe sends to that device;
   all planned records and the original cause remain in the report. Other devices
   and reporting continue. Successful preflight: subsequent errors do not stop sends.
5. Packet-loss measurement (rc 1): completed collection; no health verdict.
6. A registered probe with rc 2: retain its output and rc unchanged, without
   introducing an additional exit-code classification.

The repository still contains two unrecorded warmup packets before each measured
probe. This hardening change preserves that existing behavior; deciding whether
to remove warmup is a separate collection-plan decision.

Future optional diagnostic profiles: CPU, memory, environmental state,
hardware/interface counters, syslog, vPC and STP.

Live acceptance of the preflight change is pending the next lab startup.
Parser-phase follow-up: submit sanitized reproduction evidence referencing
https://github.com/ansible-collections/ansible.netcommon/issues/340.
