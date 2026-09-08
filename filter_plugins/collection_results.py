"""Collection outcome validation; no network health assessment or parsing."""

import re


CLI_ERROR = re.compile(
    r"^\s*%\s*(?:Invalid\b|Error\b|Incomplete\b|Ambiguous\b)",
    re.IGNORECASE | re.MULTILINE,
)


def collection_cli_result(result, device_stopped=False, preflight=False, preflight_message=""):
    """Keep the complete output and expose one consistent collection outcome."""
    result = dict(result or {})
    stdout = result.get("stdout")
    output = stdout[0] if isinstance(stdout, list) and stdout else ""
    match = CLI_ERROR.search(output) if isinstance(output, str) else None
    reason = ""
    stop_device = bool(device_stopped)
    if result.get("unreachable"):
        reason = "Device connection unavailable."
    elif result.get("skipped"):
        reason = ("Command not sent: preflight classified this device as unreachable. " + preflight_message
                  if device_stopped else "Command was skipped.")
    elif result.get("failed"):
        reason = "Command execution failed."
    elif not isinstance(output, str) or not output.strip():
        reason = "No usable command output was returned."
    elif match:
        reason = "CLI error: " + output[match.start():].lstrip().splitlines()[0]
    result["failed"] = bool(reason)
    if preflight:
        stop_device = bool(reason)
        if reason:
            reason = "Preflight failed (collector classification: unreachable). " + reason
    result["execution_status"] = "error" if reason else "completed"
    result["stop_device"] = stop_device
    message = result.get("msg") or ""
    result["msg"] = f"{reason} {message}".strip() if reason else message
    return result


class FilterModule:
    def filters(self):
        return {
            "collection_cli_result": collection_cli_result,
        }
