from uuid import uuid4

from schemas import WorkloadEvent

POLICY_VERSION = "gpu-finops-mvp-v2"


def classify_workload(event: WorkloadEvent) -> tuple[str, list[str]]:
    if event.checkpoint_state in ("IN_PROGRESS", "FAILED"):
        return "CHECKPOINTING", ["CHECKPOINT_WRITE_ACTIVE" if event.checkpoint_state == "IN_PROGRESS" else "CHECKPOINT_FAILED"]
    if event.pod_status in ("Pending", "Failed", "Completed"):
        return "IDLE_OR_FAILED", ["POD_NOT_RUNNING"]
    if event.sm_utilisation >= 70:
        return "ACTIVE_COMPUTE", ["HIGH_COMPUTE_ACTIVITY"]
    if event.memory_used_mb >= 16000 and event.sm_utilisation < 20:
        return "VRAM_HOARDING", ["LOW_COMPUTE_ACTIVITY", "VRAM_RETAINED"]
    if event.sm_utilisation < 20:
        return "LOW_UTILISATION", ["LOW_COMPUTE_ACTIVITY"]
    return "ACTIVE_COMPUTE", []


def evaluate_event(event: WorkloadEvent) -> dict:
    state, reasons = classify_workload(event)
    # Savings are prospective; past spending is not recoverable.
    savings = round(event.hourly_rate * event.savings_horizon_hours, 2) if state not in ("CHECKPOINTING", "ACTIVE_COMPUTE") else 0.0
    disruption = event.estimated_disruption_cost
    benefit = None if disruption is None else max(0.0, round(savings - disruption, 2))
    if state == "CHECKPOINTING":
        decision = "DO_NOT_TOUCH"
    elif state == "ACTIVE_COMPUTE":
        decision = "NO_ACTION"
    elif disruption is None:
        decision = "NO_ACTION"
        reasons.append("DISRUPTION_COST_UNKNOWN")
    elif benefit <= 0:
        decision = "NO_ACTION"
        reasons.append("NET_BENEFIT_NOT_POSITIVE")
    else:
        # Every recommendation requires a human decision, including development.
        decision = "REQUIRE_APPROVAL"
    return {
        "workload_state": state,
        "decision": decision,
        "reason_codes": reasons,
        "avoidable_cost": savings,
        "disruption_cost": disruption,
        "net_benefit": benefit,
        "policy_version": POLICY_VERSION,
    }


def simulate_operation() -> str:
    return f"sim_{uuid4().hex}"
