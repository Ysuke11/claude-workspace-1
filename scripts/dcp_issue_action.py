#!/usr/bin/env python3
"""Issue one Stage 1 DLP count preflight Action (PR #31 surface) on the Mac.

Run from the Mac that holds the C2 v2 controls, inside a clean clone of
Ysuke11/ys-decision-os at main ada835edad1c33dd0400ee1b266a98f3c960f1cb:

    python3 -I -B dcp_issue_action.py \
        --repo /absolute/path/to/clean/ys-decision-os \
        --chain-from /Users/Shared/ys-chatgpt-export-stage1-control-v2/observed-shape-preflight/<OSP run id>/action.json

What it does, in order, and nothing else:

1. Refuses root, sudo and unsupported CPython (3.9 / 3.14 only).
2. Verifies that the clone's preflight, Stage 1 runner, controls, adapter,
   engine, frozen guard, synthetic UAT report and source-binding schema set
   match the digests fixed below (computed from the GitHub main above).
3. Reads the three C2 control files (anchor, manifest, receipt) through the
   sealed runner's descriptor helpers: no payload file is opened. Takes
   file_count and total_bytes from the manifest.
4. Copies the twelve-field source_chain from a previous preflight Action and
   recomputes every chain SHA from the file it names; any mismatch aborts.
5. Mints a new run id (DCP-YYYYMMDD-HHMMSS-xxxxxxxx), issued_at = now (UTC
   second), expires_at = issued_at + 24h, and composes the Action.
6. Validates the composed Action with the preflight's own _validate_action,
   the runner's C2 transitive-chain check and C2 source-control check, exactly
   as the preflight will at run time.
7. Creates <control root>/<run id>/ (0700) and writes action.json (0600,
   canonical bytes, O_EXCL), re-reads it through the runner helper, and prints
   the approval tuple.

It does NOT run the preflight. Execution is a separate command that the human
issues only after approving the printed tuple verbatim. The script prints no
path from the export, no file name, no title, no identifier and no value;
only digests, counts and the tuple.

--verify-repo-only stops after step 2 (usable on any CPython, e.g. in CI).
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
import sys
import types

sys.dont_write_bytecode = True

EXPECTED_MAIN = "ada835edad1c33dd0400ee1b266a98f3c960f1cb"
EXPECTED = {
    "scripts/chatgpt_export_stage1_dlp_count_preflight.py": "7d4c34efa7fe45b1a85c4d8f5e415c21a9393721863f9a4af9bec59fab7c8cda",
    "scripts/chatgpt_export_real_normalization_runner.py": "4bd2ab3d08f3c8241091c10b119ffd78fa330acda93e6086f8e7b434e08ae6a5",
    "scripts/chatgpt_export_batch_adapter.py": "99d10115bde8a46c079a76d85bd78ff1a9d2e80de0e37452b94315e3bb64c0e0",
    "scripts/chatgpt_export_real_normalization.py": "466054b1e2be5d2240b53703a25b9b4f736c5aa9176aa464c6eee1079ce98596",
    "scripts/payment_card_guard_frozen_v1.py": "ec8bcbc83356b322eea0b9a3c62bbceafc5be8a2fd6026f7ba7f55a079659174",
    "scripts/chatgpt_export_real_normalization_controls.py": "41009847c24c8face4c2a2aa67f66e9c625840536e09c620efdd91df6c79bd10",
    "deliverables/chatgpt_export_batch_adapter/STAGE1_DLP_COUNT_PREFLIGHT_SYNTHETIC_UAT_V1.json": "2a0ea2c51eaa4fbc22bc686a0d33940630663dddafcc40aafcab1b4717d88c60",
}
EXPECTED_SOURCE_BINDING_SCHEMA_SET_SHA256 = "e5c3c669f7ca99fe901dc214e55c2cfec31ab0ff237b4cc81ca9a5b72f9d3c30"
ALLOWED_RUNTIME_VERSIONS = {(3, 9), (3, 14)}
TTL = timedelta(hours=24)


class IssueError(RuntimeError):
    pass


def fail(code: str) -> None:
    raise IssueError(f"ISSUE_{code}")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_nofollow(path: str, maximum: int = 64 * 1024 * 1024) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_size > maximum:
            fail("CHAIN_FILE_INVALID")
        chunks = []
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def verify_repo(repo: Path) -> None:
    for rel, digest in EXPECTED.items():
        target = repo / rel
        if target.is_symlink() or not target.is_file() or sha256(target.read_bytes()) != digest:
            fail("REPO_DIGEST_MISMATCH")


def load_modules(repo: Path):
    sys.path.insert(0, str(repo))
    from scripts import chatgpt_export_stage1_dlp_count_preflight as preflight  # noqa: E402
    from scripts import chatgpt_export_real_normalization_runner as runner  # noqa: E402

    if Path(preflight.PREFLIGHT_PATH).resolve() != (repo / "scripts/chatgpt_export_stage1_dlp_count_preflight.py").resolve():
        fail("REPO_MODULE_MISMATCH")
    if runner._source_binding_schema_set_sha256() != EXPECTED_SOURCE_BINDING_SCHEMA_SET_SHA256:
        fail("SOURCE_BINDING_SCHEMA_SET_MISMATCH")
    uat_raw = (repo / "deliverables/chatgpt_export_batch_adapter/STAGE1_DLP_COUNT_PREFLIGHT_SYNTHETIC_UAT_V1.json").read_bytes()
    preflight._validate_uat_report(uat_raw, runner_sha=EXPECTED["scripts/chatgpt_export_stage1_dlp_count_preflight.py"])
    return preflight, runner


def main(argv) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--repo", required=True, help="absolute path of the clean clone at main " + EXPECTED_MAIN[:12])
    parser.add_argument("--chain-from", help="absolute path of a previous preflight action.json whose source_chain names the same C2 run")
    parser.add_argument("--verify-repo-only", action="store_true")
    args = parser.parse_args(argv)

    repo = Path(args.repo)
    if not repo.is_absolute() or not repo.is_dir():
        fail("REPO_PATH_INVALID")
    verify_repo(repo)
    preflight, runner = load_modules(repo)
    if args.verify_repo_only:
        print("ISSUE_REPO_VERIFIED", EXPECTED_MAIN)
        return 0

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        fail("ROOT_EXECUTION_FORBIDDEN")
    if any(name in os.environ for name in ("SUDO_UID", "SUDO_GID", "SUDO_USER", "SUDO_COMMAND")):
        fail("SUDO_FORBIDDEN")
    if sys.version_info[:2] not in ALLOWED_RUNTIME_VERSIONS:
        fail("RUNTIME_UNSUPPORTED")
    if not args.chain_from:
        fail("CHAIN_SOURCE_REQUIRED")

    control_root = preflight.PRODUCTION_CONTROL_ROOT
    if control_root != runner.PRODUCTION_STAGE1_CONTROL_ROOT / "dlp-count-preflight":
        fail("CONTROL_ROOT_UNEXPECTED")

    # C2 controls, read the way the preflight reads them (0600, descriptor-relative, no payload).
    source_control_root = runner.PRODUCTION_SOURCE_CONTROL_ROOT
    scr_fd = runner._open_absolute_directory(source_control_root, private=True)
    try:
        raw = {}
        for basename in (runner.SOURCE_ANCHOR_BASENAME, runner.SOURCE_MANIFEST_BASENAME, runner.SOURCE_EXTRACTION_RECEIPT_BASENAME):
            raw[basename] = runner._read_regular_at(
                scr_fd, basename, maximum=runner.MAX_MANIFEST_BYTES, code="ISSUE_C2_CONTROL_READ_FAILED", expected_mode=0o600,
            )
    finally:
        os.close(scr_fd)
    manifest = runner._parse_json(raw[runner.SOURCE_MANIFEST_BASENAME], "ISSUE_C2_MANIFEST_INVALID", canonical=True)
    file_count = manifest["file_count"]
    total_bytes = manifest["total_bytes"]
    if not isinstance(file_count, int) or not isinstance(total_bytes, int) or isinstance(file_count, bool) or isinstance(total_bytes, bool):
        fail("C2_MANIFEST_INVALID")

    # source_chain: copied from a previous Action, every SHA recomputed from the named file.
    chain_action = json.loads(read_nofollow(args.chain_from, maximum=preflight.MAX_ACTION_BYTES).decode("utf-8"))
    controls = runner._load_controls()
    chain = chain_action.get("source_chain")
    if not isinstance(chain, dict) or frozenset(chain) != controls.SOURCE_CHAIN_FIELDS:
        fail("CHAIN_FIELDS_INVALID")
    for name, value in chain.items():
        if name.endswith("_path"):
            digest = chain[name[: -len("_path")] + "_sha256"]
            if not isinstance(value, str) or not value.startswith("/"):
                fail("CHAIN_PATH_INVALID")
            if sha256(read_nofollow(value)) != digest:
                fail("CHAIN_SHA_MISMATCH")

    issued = datetime.now(timezone.utc).replace(microsecond=0)
    expires = issued + TTL
    run_id = f"DCP-{issued:%Y%m%d-%H%M%S}-{secrets.token_hex(4)}"
    if preflight.RUN_ID_RE.fullmatch(run_id) is None:
        fail("RUN_ID_INVALID")
    run_dir = control_root / run_id
    action_path = run_dir / preflight.ACTION_BASENAME

    action = {
        "schema": preflight.ACTION_SCHEMA,
        "stage": preflight.STAGE,
        "source_route": preflight.PRODUCTION_SOURCE_ROUTE,
        "run_id": run_id,
        "issued_at": issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "single_use": True,
        "source_manifest_sha256": sha256(raw[runner.SOURCE_MANIFEST_BASENAME]),
        "control_root_anchor_sha256": sha256(raw[runner.SOURCE_ANCHOR_BASENAME]),
        "source_extraction_receipt_sha256": sha256(raw[runner.SOURCE_EXTRACTION_RECEIPT_BASENAME]),
        "source_binding_schema_set_sha256": EXPECTED_SOURCE_BINDING_SCHEMA_SET_SHA256,
        "source_chain": {name: chain[name] for name in sorted(chain)},
        "preflight_runner_sha256": EXPECTED["scripts/chatgpt_export_stage1_dlp_count_preflight.py"],
        "stage1_runner_sha256": EXPECTED["scripts/chatgpt_export_real_normalization_runner.py"],
        "adapter_sha256": EXPECTED["scripts/chatgpt_export_batch_adapter.py"],
        "normalization_engine_sha256": EXPECTED["scripts/chatgpt_export_real_normalization.py"],
        "payment_card_guard_sha256": EXPECTED["scripts/payment_card_guard_frozen_v1.py"],
        "controls_sha256": EXPECTED["scripts/chatgpt_export_real_normalization_controls.py"],
        "uat_report_sha256": EXPECTED["deliverables/chatgpt_export_batch_adapter/STAGE1_DLP_COUNT_PREFLIGHT_SYNTHETIC_UAT_V1.json"],
        "input": {"file_count": file_count, "total_bytes": total_bytes},
        "roots": {
            "input_root": str(runner.PRODUCTION_INPUT_ROOT),
            "source_control_root": str(source_control_root),
            "control_root": str(control_root),
            "output_root": str(run_dir),
        },
        "output_ceilings": {
            "max_consumed_marker_bytes": preflight.MAX_MARKER_BYTES,
            "max_result_bytes": preflight.MAX_RESULT_BYTES,
        },
        "effects": {name: False for name in preflight.EFFECT_FIELDS},
    }
    if frozenset(action) != preflight.ACTION_FIELDS:
        fail("ACTION_FIELDS_INVALID")
    action_raw = preflight._canonical(action)
    action_sha = sha256(action_raw)

    # Validate exactly as the preflight will.
    preflight._validate_action(
        action, action_path=PurePosixPath(str(action_path)), run_id=run_id,
        expected_action_sha=action_sha,
        expected_runner_sha=action["preflight_runner_sha256"],
        expected_manifest_sha=action["source_manifest_sha256"],
        expected_uat_sha=action["uat_report_sha256"],
        expected_expires_at=action["expires_at"], now=issued, synthetic=False,
    )
    gate = types.SimpleNamespace(
        source_chain=types.MappingProxyType(dict(action["source_chain"])),
        source_route=action["source_route"],
        input_root=action["roots"]["input_root"],
        source_control_root=action["roots"]["source_control_root"],
        file_count=file_count, total_bytes=total_bytes,
        control_root_anchor_sha256=action["control_root_anchor_sha256"],
        source_manifest_sha256=action["source_manifest_sha256"],
        source_extraction_receipt_sha256=action["source_extraction_receipt_sha256"],
    )
    runner._validate_c2_transitive_chain(gate, action)
    runner._validate_c2_source_controls(
        raw[runner.SOURCE_ANCHOR_BASENAME], raw[runner.SOURCE_MANIFEST_BASENAME],
        raw[runner.SOURCE_EXTRACTION_RECEIPT_BASENAME], gate, action, synthetic=False,
    )

    # Write the Action: control root 0700, run dir 0700 (new), action.json 0600 exclusive.
    control_dir = Path(str(control_root))
    if not control_dir.exists():
        os.mkdir(control_dir, 0o700)
    control_fd = runner._open_absolute_directory(control_root, private=True)
    os.close(control_fd)
    os.mkdir(run_dir, 0o700)
    try:
        fd = os.open(str(action_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.write(fd, action_raw)
            os.fsync(fd)
        finally:
            os.close(fd)
        run_fd = runner._open_absolute_directory(run_dir, private=True)
        try:
            runner._read_regular_at(
                run_fd, preflight.ACTION_BASENAME, maximum=preflight.MAX_ACTION_BYTES,
                code="ISSUE_ACTION_REREAD_FAILED", expected_mode=0o600, expected_sha256=action_sha,
            )
        finally:
            os.close(run_fd)
    except BaseException:
        try:
            os.unlink(action_path)
        except OSError:
            pass
        try:
            os.rmdir(run_dir)
        except OSError:
            pass
        raise

    uat_abs = repo / "deliverables/chatgpt_export_batch_adapter/STAGE1_DLP_COUNT_PREFLIGHT_SYNTHETIC_UAT_V1.json"
    print("ISSUED  (nothing has been executed; present this tuple to the human and stop)")
    print(f"repo_main                        {EXPECTED_MAIN}")
    print(f"input.file_count                 {file_count}")
    print(f"input.total_bytes                {total_bytes}")
    print(f"issued_at                        {action['issued_at']}")
    print("")
    print("APPROVAL TUPLE")
    print(f"  --expected-run-id                 {run_id}")
    print(f"  --expected-action-sha256          {action_sha}")
    print(f"  --expected-runner-sha256          {action['preflight_runner_sha256']}")
    print(f"  --expected-source-manifest-sha256 {action['source_manifest_sha256']}")
    print(f"  --expected-uat-report-sha256      {action['uat_report_sha256']}")
    print(f"  --expected-expires-at             {action['expires_at']}")
    print("")
    print("AFTER EXPLICIT APPROVAL, run once from the clone root:")
    print(
        "python3 -I -B scripts/chatgpt_export_stage1_dlp_count_preflight.py"
        f" --expected-run-id {run_id}"
        f" --expected-action-sha256 {action_sha}"
        f" --expected-runner-sha256 {action['preflight_runner_sha256']}"
        f" --expected-source-manifest-sha256 {action['source_manifest_sha256']}"
        f" --expected-uat-report-sha256 {action['uat_report_sha256']}"
        f" --expected-expires-at {action['expires_at']}"
        f" --action {action_path}"
        f" --uat-report {uat_abs}"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except IssueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(3)
    except Exception as exc:  # closed-code failures from the sealed helpers
        code = getattr(exc, "code", None) or type(exc).__name__
        print(f"ISSUE_FAILED {code}", file=sys.stderr)
        sys.exit(3)
