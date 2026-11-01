from datetime import datetime, timezone
from uuid import uuid4

POLICY_VERSION = "gpu-finops-mvp-v1"


def classify_workload(event):
    if event.pod_status in ["Pending", "Failed", "Completed"]:
        return "IDLE_OR_FAILED", ["POD_NOT_RUNNING"]

    if event.checkpoint_state == "IN_PROGRESS":
        return "CHECKPOINTING", ["CHECKPOINT_WRITE_ACTIVE"]

    if event.sm_utilisation >= 70:
        return "ACTIVE_COMPUTE", ["HIGH_COMPUTE_ACTIVITY"]

    if event.memory_used_mb >= 16000 and event.sm_utilisation < 20:
        return "VRAM_HOARDING", ["LOW_COMPUTE_ACTIVITY", "VRAM_RETAINED"]

    if event.sm_utilisation < 20:
        return "LOW_UTILISATION", ["LOW_COMPUTE_ACTIVITY"]

    return "ACTIVE_COMPUTE", []


def calculate_disruption_cost(event, workload_state):
    if workload_state == "CHECKPOINTING":
        return event.hourly_rate * 4

    if workload_state == "VRAM_HOARDING":
        return event.hourly_rate * 0.5

    if workload_state == "LOW_UTILISATION":
        return event.hourly_rate * 0.25

    return event.hourly_rate * 2


def calculate_avoidable_cost(event, workload_state):
    if workload_state in ["ACTIVE_COMPUTE", "CHECKPOINTING"]:
        return 0.0

    now = datetime.now(timezone.utc)
    last_activity = event.last_activity_at

    if last_activity.tzinfo is None:
        last_activity = last_activity.replace(tzinfo=timezone.utc)

    inactive_hours = max(0.0, (now - last_activity).total_seconds() / 3600)
    billable_hours = min(inactive_hours, 24)

    return round(event.hourly_rate * billable_hours, 2)


def make_decision(event, workload_state):
    if workload_state == "CHECKPOINTING":
        return "DO_NOT_TOUCH"

    if event.environment == "prod":
        return "REQUIRE_APPROVAL"

    if workload_state == "VRAM_HOARDING":
        return "REQUIRE_APPROVAL"

    if workload_state == "LOW_UTILISATION":
        return "AUTO_RECOMMEND"

    if workload_state == "IDLE_OR_FAILED":
        return "AUTO_RECOMMEND"

    return "NO_ACTION"


def evaluate_event(event):
    workload_state, reason_codes = classify_workload(event)

    avoidable_cost = calculate_avoidable_cost(event, workload_state)
    disruption_cost = calculate_disruption_cost(event, workload_state)

    if avoidable_cost <= disruption_cost:
        decision = "NO_ACTION"
        net_benefit = 0.0
    else:
        decision = make_decision(event, workload_state)
        net_benefit = round(avoidable_cost - disruption_cost, 2)

    if decision == "NO_ACTION":
        reason_codes.append("NET_BENEFIT_NOT_POSITIVE")

    return {
        "workload_state": workload_state,
        "decision": decision,
        "reason_codes": reason_codes,
        "avoidable_cost": avoidable_cost,
        "disruption_cost": disruption_cost,
        "net_benefit": net_benefit,
        "policy_version": POLICY_VERSION,
    }


def execute_mock_operation(decision):
    operation_id = f"op_{uuid4().hex[:12]}"
    return operation_id, "GPU_MEMORY_RELEASED"
