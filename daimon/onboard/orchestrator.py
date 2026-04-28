"""``daimon onboard`` orchestrator.

Top-level :func:`run_onboard` that runs the steps in order, with a
result envelope rich enough for both the interactive CLI ('print
each step as we go') and the MCP tool ('return everything as a
single JSON dict for the agent to surface').

Steps:

  1. Identity gen (with optional confirmation gate).
  2. Recovery file at ``~/.config/daimon/recovery.txt`` (mode 0600).
  3. Manifest fetch + starter-card prefetch (small, blocking).
  4. Background prefetcher spawn (the rest of the cards, off the
     critical path).
  5. Optional Claude Code wiring (atomic MCP entry + hook write).
  6. Doctor-style summary.

Identity gen / recovery / manifest fetch run unconditionally. The
Claude Code wiring is gated on ``wire_claude_code`` (default True)
because the MCP variant of onboard wants to skip it (the MCP server
itself is what's running the tool — wiring it back into Claude Code
from inside Claude Code is a useful operation, but agents may have
already wired it manually).
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


# Re-exported lazily inside run_onboard to avoid heavy imports at
# module load time (this file is imported by the MCP server too,
# which we don't want to slow down).


@dataclass
class OnboardResult:
    """Structured result of an onboarding run.

    Attributes:
        identity_action: ``"generated"`` (fresh) | ``"already_present"``
            (existing keypair preserved).
        pubkey_hex: 64-char hex pubkey of the identity.
        mnemonic: the 24-word BIP39 phrase. Only populated when a fresh
            identity was generated. Empty string otherwise.
        recovery_path: where the recovery file was written, or ``None``
            if not written (e.g. existing identity, no mnemonic to save).
        manifest_action: ``"installed"`` | ``"already_present"`` |
            ``"failed"`` (then ``manifest_error`` is populated).
        manifest_version: pack version of the installed manifest, or
            ``None`` if the fetch failed.
        manifest_error: error message when ``manifest_action == "failed"``.
        starter_fetched: list of card_ids the synchronous starter
            prefetch successfully landed.
        starter_failed: list of ``(card_id, error)`` pairs for cards
            that failed during starter prefetch.
        prefetch_pid: PID of the detached background prefetcher, or
            ``None`` if not spawned (opt-out, no manifest, etc).
        claude_code_action: ``"wired"`` (both hook + MCP entry written)
            | ``"refreshed"`` (existing entries refreshed) |
            ``"already_present"`` | ``"skipped"`` (caller passed
            ``wire_claude_code=False``) | ``"failed"``.
        claude_code_settings: path to the settings file that was edited
            (or would be edited).
        claude_code_backup: path to the timestamped backup created
            before the edit (None if nothing was changed).
        claude_code_error: error message when
            ``claude_code_action == "failed"``.
    """
    identity_action: str
    pubkey_hex: str
    mnemonic: str = ""
    recovery_path: Optional[str] = None
    bundle_action: str = "skipped"
    bundle_version: Optional[str] = None
    bundle_error: Optional[str] = None
    manifest_action: str = "skipped"
    manifest_version: Optional[str] = None
    manifest_error: Optional[str] = None
    starter_fetched: List[str] = field(default_factory=list)
    starter_failed: List[List[str]] = field(default_factory=list)
    prefetch_pid: Optional[int] = None
    claude_code_action: str = "skipped"
    claude_code_settings: Optional[str] = None
    claude_code_backup: Optional[str] = None
    claude_code_error: Optional[str] = None
    claude_code_mcp_command: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "identity_action": self.identity_action,
            "pubkey_hex": self.pubkey_hex,
            "mnemonic": self.mnemonic,
            "recovery_path": self.recovery_path,
            "bundle_action": self.bundle_action,
            "bundle_version": self.bundle_version,
            "bundle_error": self.bundle_error,
            "manifest_action": self.manifest_action,
            "manifest_version": self.manifest_version,
            "manifest_error": self.manifest_error,
            "starter_fetched": list(self.starter_fetched),
            "starter_failed": [list(p) for p in self.starter_failed],
            "prefetch_pid": self.prefetch_pid,
            "claude_code_action": self.claude_code_action,
            "claude_code_settings": self.claude_code_settings,
            "claude_code_backup": self.claude_code_backup,
            "claude_code_error": self.claude_code_error,
            "claude_code_mcp_command": self.claude_code_mcp_command,
        }


# ---------------------------------------------------------------------------
# Recovery file
# ---------------------------------------------------------------------------

def write_recovery_file(mnemonic: str, *, dest: Optional[Path] = None) -> Path:
    """Persist the BIP39 mnemonic at ``~/.config/daimon/recovery.txt`` mode 0600.

    The mnemonic is also returned by ``daimon init`` exactly once on the
    terminal; this file is the durable "did I copy it down" backstop.
    Permissions are tightened to 0600 so a multi-user box can't trivially
    snoop the recovery phrase.

    On Windows, mode bits are advisory — Python's ``chmod`` only sets the
    read-only bit. We still call it for symmetry; protecting the file
    properly on Windows requires NTFS ACLs which are outside this scope.

    Returns the path written. Idempotent — overwrites an existing file
    so a re-onboard on the same identity refreshes the recovery (the
    mnemonic itself is deterministic from the identity key, so the
    bytes don't change anyway).
    """
    if not mnemonic:
        raise ValueError("write_recovery_file: mnemonic must not be empty")
    if dest is None:
        from daimon.identity.keys import CONFIG_DIR
        dest = CONFIG_DIR / "recovery.txt"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(mnemonic + "\n", encoding="utf-8")
    try:
        dest.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        # Best-effort. Some filesystems (FAT32, network mounts) don't
        # support chmod. The file is still written; we just couldn't
        # tighten the perms.
        pass
    return dest


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

LogFn = Callable[[str], None]


def _noop_log(_msg: str) -> None:
    pass


def run_onboard(
    *,
    force_identity: bool = False,
    confirm_mnemonic: Optional[Callable[[str], bool]] = None,
    wire_claude_code: bool = True,
    settings_path: Optional[Path] = None,
    spawn_prefetch: bool = True,
    install_bundle: bool = True,
    log: LogFn = _noop_log,
) -> OnboardResult:
    """Run the onboarding flow end-to-end.

    Args:
        force_identity: when True, overwrite an existing identity (the
            old collection + ledger position are unrecoverable unless
            the old mnemonic was saved). Mirrors ``daimon init --force``.
        confirm_mnemonic: optional callback invoked with the mnemonic
            string. Should return True iff the user confirmed they
            saved it. When False, the recovery file is still written
            (safety net) but the call returns early WITHOUT wiring
            Claude Code or fetching the manifest. Pass ``None`` to skip
            the gate entirely (CI / agent flows that handle confirmation
            elsewhere).
        wire_claude_code: when False, skip the Claude Code wiring step
            (the MCP variant of onboard sets this to False since the
            agent is already running inside Claude Code).
        settings_path: override the default
            ``~/.claude/settings.json`` path. Mostly for tests.
        spawn_prefetch: when False, skip the detached prefetcher spawn.
            Tests use this to avoid leaving zombies around; the explicit
            ``daimon prefetch`` command is also unaffected.
        log: callable invoked with each step's progress message. The
            CLI passes ``click.echo``; the MCP tool passes a no-op
            (it surfaces progress through the returned dict).

    Returns:
        :class:`OnboardResult` describing every step's outcome. The
        caller decides how to render it.
    """
    # Local imports — avoid pulling heavy dependencies at module import
    # time (this module is imported by the MCP server which we want to
    # keep fast).
    from daimon.identity import generate_identity, load_identity
    from daimon.identity.keys import PRIVATE_KEY_PATH
    from daimon.onboard.claude_code import (
        DAIMON_MCP_SERVER_NAME,
        install_claude_code_integration,
        resolve_mcp_command,
    )
    from daimon.update.fetcher import ArtUpdateError
    from daimon.update.lazy import fetch_card
    from daimon.update.manifest import fetch_manifest

    result_kwargs: Dict[str, Any] = {}

    from daimon.identity.keys import CONFIG_DIR

    # -----------------------------------------------------------------------
    # Step 1: Identity + recovery (transactional — recovery is written
    # BEFORE any log call so a downstream crash never loses the mnemonic).
    # -----------------------------------------------------------------------
    if PRIVATE_KEY_PATH.exists() and not force_identity:
        existing = load_identity()
        recovery_existing = CONFIG_DIR / "recovery.txt"
        recovery_path_str = (
            str(recovery_existing) if recovery_existing.exists() else None
        )
        result_kwargs.update(
            identity_action="already_present",
            pubkey_hex=existing.pubkey_hex,
            mnemonic="",
            recovery_path=recovery_path_str,
        )
        log(f"identity: already present at {PRIVATE_KEY_PATH}")
        if recovery_path_str is None:
            log(
                "recovery: WARN — identity exists but recovery.txt is missing. "
                "Mnemonic is no longer recoverable from this machine. "
                "Back up identity.key now, or re-run with --force to "
                "regenerate (DESTRUCTIVE)."
            )
    else:
        identity = generate_identity(force=force_identity)
        # Persist recovery FIRST — before any log/print so a stdout encoding
        # crash, broken pipe, or SIGINT cannot lose the mnemonic.
        recovery_path_str: Optional[str] = None
        if identity.mnemonic:
            try:
                recovery = write_recovery_file(identity.mnemonic)
                recovery_path_str = str(recovery)
            except OSError:
                recovery_path_str = None
        result_kwargs.update(
            identity_action="generated",
            pubkey_hex=identity.pubkey_hex,
            mnemonic=identity.mnemonic or "",
            recovery_path=recovery_path_str,
        )
        log(f"identity: generated -> pubkey {identity.pubkey_hex[:16]}")
        if recovery_path_str:
            log(f"recovery: written to {recovery_path_str} (mode 0600)")
        elif identity.mnemonic:
            log("recovery: WARN — failed to write recovery file")

        # -------------------------------------------------------------------
        # Mnemonic confirmation gate (after the file is on disk so users
        # who Ctrl-C still have the recovery.txt backstop).
        # -------------------------------------------------------------------
        if confirm_mnemonic is not None and identity.mnemonic:
            confirmed = bool(confirm_mnemonic(identity.mnemonic))
            if not confirmed:
                log("onboard: aborted by user before continuing.")
                return OnboardResult(**result_kwargs)

    # -----------------------------------------------------------------------
    # Step 2: WezTerm bundle install (cross-platform — linux/macos/windows
    # × x86_64/aarch64). Mandatory: card art rendering uses the Kitty
    # Graphics Protocol which only works on the bundled WezTerm at our
    # locked DPI/cell-size. Skip with install_bundle=False (CI / smoke).
    # -----------------------------------------------------------------------
    if install_bundle:
        try:
            from daimon.install import (
                BundleInstallError,
                install_bundle as _install_bundle,
            )
            report = _install_bundle(verify_smoke_test=False)
            action = "already_installed" if report.skipped_download else "installed"
            result_kwargs.update(
                bundle_action=action,
                bundle_version=report.tag,
            )
            log(f"bundle: {action} ({report.tag})")
        except BundleInstallError as e:
            result_kwargs.update(
                bundle_action="failed",
                bundle_error=str(e),
            )
            log(f"bundle: FAILED -- {e}")
        except OSError as e:
            result_kwargs.update(
                bundle_action="failed",
                bundle_error=str(e),
            )
            log(f"bundle: FAILED -- {e}")

    # -----------------------------------------------------------------------
    # Step 3: Manifest + starter prefetch
    # -----------------------------------------------------------------------
    try:
        manifest = fetch_manifest(show_progress=False)
        result_kwargs.update(
            manifest_action="installed",
            manifest_version=manifest.pack_version,
        )
        log(
            f"manifest: installed for {manifest.pack_version} "
            f"({manifest.card_count} cards)"
        )
    except ArtUpdateError as e:
        result_kwargs.update(
            manifest_action="failed",
            manifest_error=str(e),
        )
        manifest = None
        log(f"manifest: FAILED — {e}")

    starter_fetched: List[str] = []
    starter_failed: List[List[str]] = []
    if manifest is not None:
        for cid in manifest.starter_card_ids:
            try:
                fetch_card(cid, manifest=manifest, show_progress=False)
                starter_fetched.append(cid)
                log(f"starter: ok   {cid}")
            except ArtUpdateError as e:
                starter_failed.append([cid, str(e)])
                log(f"starter: FAIL {cid}: {e}")
    result_kwargs["starter_fetched"] = starter_fetched
    result_kwargs["starter_failed"] = starter_failed

    # -----------------------------------------------------------------------
    # Step 4: Background prefetch (the rest of the cards)
    # -----------------------------------------------------------------------
    if spawn_prefetch and manifest is not None:
        from daimon.update.prefetch import spawn_prefetch_subprocess
        pid = spawn_prefetch_subprocess()
        result_kwargs["prefetch_pid"] = pid
        if pid is not None:
            log(f"prefetch: spawned background fetcher (pid {pid})")
        else:
            log("prefetch: opted out or could not spawn — cards will be "
                "fetched on demand.")

    # -----------------------------------------------------------------------
    # Step 5: Claude Code wiring
    # -----------------------------------------------------------------------
    if wire_claude_code:
        try:
            from daimon.mining.installer import DEFAULT_SETTINGS_PATH
            target = settings_path or DEFAULT_SETTINGS_PATH
            mcp_command = resolve_mcp_command()
            cc = install_claude_code_integration(
                settings_path=target,
                mcp_command=mcp_command,
                server_name=DAIMON_MCP_SERVER_NAME,
            )
            # Compose a single action label from the two halves.
            hook_a = cc["hook_action"]
            mcp_a = cc["mcp_action"]
            if hook_a in ("installed",) or mcp_a in ("installed", "refreshed"):
                action = "wired" if hook_a == "installed" else "refreshed"
            elif hook_a == "already_present" and mcp_a == "already_present":
                action = "already_present"
            else:
                action = f"hook={hook_a};mcp={mcp_a}"
            result_kwargs.update(
                claude_code_action=action,
                claude_code_settings=cc["settings_path"],
                claude_code_backup=cc.get("backup_path"),
                claude_code_mcp_command=cc.get("mcp_command"),
            )
            log(f"claude-code: {action} ({cc['settings_path']})")
            if cc.get("backup_path"):
                log(f"claude-code: backup → {cc['backup_path']}")
        except (RuntimeError, OSError) as e:
            result_kwargs.update(
                claude_code_action="failed",
                claude_code_error=str(e),
            )
            log(f"claude-code: FAILED — {e}")

    return OnboardResult(**result_kwargs)
