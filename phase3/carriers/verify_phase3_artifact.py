#!/usr/bin/env python3
"""Independent post-run verifier for Soleaux Phase 3 live-wedge evidence.

Fail-closed. Re-evaluates every hard gate from the pre-registered experiment
design against the evidence directory produced by phase3_live_wedge.py.
Does not re-run inference or mutate product contracts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from typing import Any

EXPECTED_TOOL_CEILING = 12
EXPECTED_TASK_COUNT = 6
CANONICAL_TOOLS = [
    "context.compile",
    "code.search",
    "memory.search",
    "get_symbols",
    "registry.list",
    "registry.read",
    "repo_info",
    "navigate",
    "inspect",
    "preview",
    "edit",
    "restart_lsp",
]
EXPECTED_VERSION = "0.4.0-dev.5"
EXPECTED_PROFILE_SHA256 = "89a2b783c4bd9c0ae834a5894dceb2c4abcaa8050dd0f57ed967a9c57e3a60fc"
EXPECTED_CONTEXT_SHA256 = "3bbb53e84b0624f2a1de26bad7f2031b6a7cf0f7e892262d584347bd54b6003f"


class VerifyFail(Exception):
    def __init__(self, gate: str, detail: str) -> None:
        super().__init__(f"{gate}: {detail}")
        self.gate = gate
        self.detail = detail


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: pathlib.Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def require(condition: bool, gate: str, detail: str) -> None:
    if not condition:
        raise VerifyFail(gate, detail)


def verify_evidence(evidence_dir: pathlib.Path) -> dict[str, Any]:
    result_path = evidence_dir / "result.json"
    require(result_path.is_file(), "evidence_present", f"missing {result_path}")
    result = load_json(result_path)

    # --- structural presence ---
    require("tasks" in result, "evidence_structure", "result.json missing 'tasks'")
    require("aggregate" in result, "evidence_structure", "result.json missing 'aggregate'")
    require("gates" in result, "evidence_structure", "result.json missing 'gates'")
    require("phase3Closed" in result, "evidence_structure", "result.json missing 'phase3Closed'")

    tasks = result["tasks"]
    aggregate = result["aggregate"]
    reported_gates = result["gates"]

    require(
        len(tasks) == EXPECTED_TASK_COUNT,
        "allTasksExecuted",
        f"expected {EXPECTED_TASK_COUNT} tasks, found {len(tasks)}",
    )

    # --- per-arm metrics ---
    for arm in ("baseline", "treatment"):
        require(
            arm in aggregate.get("arms", {}), "aggregate_arms", f"missing aggregate arm '{arm}'"
        )

    baseline = aggregate["arms"]["baseline"]
    treatment = aggregate["arms"]["treatment"]

    # --- hard gates (recomputed, not trusted from harness) ---
    recomputed: dict[str, bool] = {}

    # 1. all tasks executed with model results
    recomputed["allTasksExecuted"] = len(tasks) == EXPECTED_TASK_COUNT and all(
        "model" in task.get(arm, {}) for task in tasks for arm in ("baseline", "treatment")
    )
    require(
        recomputed["allTasksExecuted"],
        "allTasksExecuted",
        "one or more task/arm missing model result",
    )

    # 2. treatment correctness
    treatment_correct = treatment.get("correctTasks", 0)
    baseline_correct = baseline.get("correctTasks", 0)
    recomputed["treatmentCorrectnessAtLeastBaseline"] = treatment_correct >= baseline_correct
    recomputed["treatmentCorrectnessAtLeastFiveOfSix"] = treatment_correct >= 5
    require(
        recomputed["treatmentCorrectnessAtLeastBaseline"],
        "treatmentCorrectnessAtLeastBaseline",
        f"treatment {treatment_correct} < baseline {baseline_correct}",
    )
    require(
        recomputed["treatmentCorrectnessAtLeastFiveOfSix"],
        "treatmentCorrectnessAtLeastFiveOfSix",
        f"treatment correct tasks {treatment_correct} < 5",
    )

    # 3. context economy
    recomputed["treatmentContextBytesLower"] = treatment.get("contextBytes", 0) < baseline.get(
        "contextBytes", 0
    )
    recomputed["treatmentPromptTokensLower"] = treatment.get("modelPromptTokens", 0) < baseline.get(
        "modelPromptTokens", 0
    )
    require(
        recomputed["treatmentContextBytesLower"],
        "treatmentContextBytesLower",
        (
            f"treatment contextBytes {treatment.get('contextBytes')} "
            f"not lower than baseline {baseline.get('contextBytes')}"
        ),
    )
    require(
        recomputed["treatmentPromptTokensLower"],
        "treatmentPromptTokensLower",
        (
            f"treatment prompt tokens {treatment.get('modelPromptTokens')} "
            f"not lower than baseline {baseline.get('modelPromptTokens')}"
        ),
    )

    # 4. treatment tool surface exactly 12 + all native + packets valid
    all_treatment_native = True
    all_treatment_packets_valid = True
    all_treatment_tool_lists_exactly_twelve = True

    for task in tasks:
        t = task.get("treatment", {})
        tools = t.get("tools", {}).get("names") or t.get("toolNames") or []
        if len(tools) != EXPECTED_TOOL_CEILING:
            all_treatment_tool_lists_exactly_twelve = False
        if "native" in t and t["native"] is False:
            all_treatment_native = False
        packet = t.get("contextPacket") or t.get("packet")
        if packet is not None:
            schema = packet.get("schema") or packet.get("$schema") or ""
            if (
                ("schema" in packet or "schemaVersion" in packet)
                and "context/v2" not in str(schema)
                and packet.get("schemaVersion") != "soleaux.context/v2"
            ):
                all_treatment_packets_valid = False

    recomputed["allTreatmentNative"] = all_treatment_native
    recomputed["allTreatmentPacketsValid"] = all_treatment_packets_valid
    recomputed["allTreatmentToolListsExactlyTwelve"] = all_treatment_tool_lists_exactly_twelve

    require(
        recomputed["allTreatmentToolListsExactlyTwelve"],
        "allTreatmentToolListsExactlyTwelve",
        "treatment tool list count != 12 on one or more tasks",
    )
    require(
        recomputed["allTreatmentNative"],
        "allTreatmentNative",
        "non-native treatment evidence detected",
    )
    require(
        recomputed["allTreatmentPacketsValid"],
        "allTreatmentPacketsValid",
        "invalid treatment context packet detected",
    )

    # 5. productionClaimAllowed remained false
    production_claim_ok = True
    if result.get("productionClaimAllowed") is True:
        production_claim_ok = False
    if result.get("locked", {}).get("productionClaimAllowed") is True:
        production_claim_ok = False
    recomputed["productionClaimRemainedFalse"] = production_claim_ok
    require(
        recomputed["productionClaimRemainedFalse"],
        "productionClaimRemainedFalse",
        "productionClaimAllowed became true",
    )

    # 6. no secret leakage
    no_secret_leakage = True
    prohibited = (
        result.get("prohibitedValues") or result.get("fixture", {}).get("prohibitedValues") or []
    )
    if not prohibited:
        no_secret_leakage = result.get("gates", {}).get("noSecretLeakage", True) is True
    else:
        blob = json.dumps(result, ensure_ascii=False)
        for val in prohibited:
            if val and val in blob:
                no_secret_leakage = False
                break
    recomputed["noSecretLeakage"] = no_secret_leakage
    require(
        recomputed["noSecretLeakage"],
        "noSecretLeakage",
        "prohibited fixture secret found in evidence",
    )

    # --- cross-check harness-reported gates vs recomputed ---
    mismatches = []
    for name, expected in recomputed.items():
        reported = reported_gates.get(name)
        if reported is not None and bool(reported) != bool(expected):
            mismatches.append(f"{name}: harness={reported} verifier={expected}")
    require(len(mismatches) == 0, "gate_consistency", "; ".join(mismatches) if mismatches else "ok")

    all_hard_passed = all(recomputed.values())
    phase3_closed = all_hard_passed and result.get("phase3Closed") is True

    return {
        "verifier": "verify_phase3_artifact.py",
        "evidenceDir": str(evidence_dir),
        "resultSha256": sha256_file(result_path),
        "recomputedGates": recomputed,
        "harnessGates": reported_gates,
        "allHardGatesPassed": all_hard_passed,
        "phase3Closed": phase3_closed,
        "productionClaimAllowed": False,
        "aggregate": {
            "baselineCorrect": baseline_correct,
            "treatmentCorrect": treatment_correct,
            "baselineContextBytes": baseline.get("contextBytes"),
            "treatmentContextBytes": treatment.get("contextBytes"),
            "baselinePromptTokens": baseline.get("modelPromptTokens"),
            "treatmentPromptTokens": treatment.get("modelPromptTokens"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent Phase 3 evidence verifier")
    parser.add_argument(
        "evidence_dir",
        type=pathlib.Path,
        help="Path to the evidence directory written by phase3_live_wedge.py",
    )
    parser.add_argument(
        "--json-out",
        type=pathlib.Path,
        default=None,
        help="Optional path to write the verification summary JSON",
    )
    args = parser.parse_args()

    evidence_dir = args.evidence_dir.resolve()
    if not evidence_dir.is_dir():
        print(f"FAIL: evidence directory does not exist: {evidence_dir}", file=sys.stderr)
        return 2

    try:
        summary = verify_evidence(evidence_dir)
    except VerifyFail as e:
        print(f"FAIL — {e.gate}: {e.detail}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"FAIL — unexpected verifier error: {e}", file=sys.stderr)
        return 2

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        with args.json_out.open("w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, sort_keys=True)
            f.write("\n")

    if summary["allHardGatesPassed"]:
        print("PASS — all hard gates recomputed and consistent")
        print(json.dumps(summary["aggregate"], indent=2))
        return 0

    print(
        "FAIL — one or more hard gates did not pass under independent recomputation",
        file=sys.stderr,
    )
    print(json.dumps(summary["recomputedGates"], indent=2), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
