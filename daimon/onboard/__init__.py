"""DAIMON first-run onboarding.

The :func:`run_onboard` orchestrator collapses the four legacy bootstrap
steps (``daimon install`` + ``daimon init`` + ``daimon mine install-hook``
+ MCP server wiring) into a single interactive flow:

  1. Identity gen with mnemonic confirmation gate
  2. Recovery file (``~/.config/daimon/recovery.txt`` mode 0600)
  3. Claude Code detection + atomic settings.json write
     (PostToolUse hook + ``mcpServers.daimon`` MCP entry, in one
     backup-and-write transaction)
  4. Manifest fetch + starter-card prefetch
  5. Background prefetch spawn (the rest of the cards, off the
     critical path)
  6. Doctor-style summary

Both the CLI command (``daimon onboard``) and the MCP tool
(``dm_onboard``) call into this same orchestrator. The CLI is
interactive (terminal prompts); the MCP variant accepts skip flags and
returns a structured envelope so an agent can choose to handle the
mnemonic display itself.
"""

from __future__ import annotations

from daimon.onboard.claude_code import (
    DAIMON_MCP_SERVER_NAME,
    install_claude_code_integration,
    is_daimon_mcp_present,
    resolve_mcp_command,
    uninstall_claude_code_integration,
)
from daimon.onboard.orchestrator import (
    OnboardResult,
    run_onboard,
    write_recovery_file,
)

__all__ = [
    "OnboardResult",
    "run_onboard",
    "write_recovery_file",
    "DAIMON_MCP_SERVER_NAME",
    "install_claude_code_integration",
    "is_daimon_mcp_present",
    "resolve_mcp_command",
    "uninstall_claude_code_integration",
]
