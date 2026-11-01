from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from engine import evaluate_event, execute_mock_operation
from models import Base, Decision
from schemas import ApprovalRequest, ApprovalResponse, DecisionDetail, EvaluationResponse, WorkloadEvent

app = FastAPI(
    title="GPU FinOps Engine",
    description="Detects idle or wasteful GPU workloads, estimates avoidable cost, and manages safe reclamation decisions.",
    version="0.1.0",
)

DATABASE_URL = "sqlite:///./gpu_finops.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "GPU FinOps Engine",
    }


@app.post("/evaluate", response_model=EvaluationResponse)
def evaluate(event: WorkloadEvent, db: Session = Depends(get_db)):
    result = evaluate_event(event)

    decision = Decision(
        gpu_id=event.gpu_id,
        workload_id=event.workload_id,
        owner_email=event.owner_email,
        team_name=event.team_name,
        environment=event.environment,
        workload_type=event.workload_type,
        pod_status=event.pod_status,
        checkpoint_state=event.checkpoint_state,
        sm_utilisation=event.sm_utilisation,
        memory_used_mb=event.memory_used_mb,
        hourly_rate=event.hourly_rate,
        last_activity_at=event.last_activity_at,
        workload_state=result["workload_state"],
        decision=result["decision"],
        reason_codes=result["reason_codes"],
        avoidable_cost=result["avoidable_cost"],
        disruption_cost=result["disruption_cost"],
        net_benefit=result["net_benefit"],
        policy_version=result["policy_version"],
        audit_trail=[
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "DECISION_CREATED",
                "decision": result["decision"],
            }
        ],
    )

    db.add(decision)
    db.commit()
    db.refresh(decision)

    return decision


@app.post("/decisions/{decision_id}/approve", response_model=ApprovalResponse)
def approve_decision(
    decision_id: str,
    request: ApprovalRequest,
    db: Session = Depends(get_db),
):
    decision = db.get(Decision, decision_id)

    if decision is None:
        raise HTTPException(status_code=404, detail="Decision not found")

    if decision.status != "AWAITING_APPROVAL":
        raise HTTPException(status_code=400, detail="Decision is not awaiting approval")

    if not request.approved:
        decision.status = "REJECTED"
        decision.audit_trail.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "ACTION_REJECTED",
                "approver": request.approver_email,
            }
        )
        db.commit()
        db.refresh(decision)

        return ApprovalResponse(
            decision_id=decision.decision_id,
            status=decision.status,
        )

    operation_id, verification = execute_mock_operation(decision)

    decision.status = "EXECUTED_AND_VERIFIED"
    decision.operation_id = operation_id
    decision.verification = verification

    decision.audit_trail.append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "ACTION_APPROVED_AND_VERIFIED",
            "approver": request.approver_email,
            "operation_id": operation_id,
            "verification": verification,
        }
    )

    db.commit()
    db.refresh(decision)

    return ApprovalResponse(
        decision_id=decision.decision_id,
        status=decision.status,
        operation_id=decision.operation_id,
        verification=decision.verification,
    )


@app.get("/decisions/{decision_id}", response_model=DecisionDetail)
def get_decision(decision_id: str, db: Session = Depends(get_db)):
    decision = db.get(Decision, decision_id)

    if decision is None:
        raise HTTPException(status_code=404, detail="Decision not found")

    return decision
