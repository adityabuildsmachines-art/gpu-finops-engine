from datetime import datetime, timedelta, timezone
import os

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import and_, create_engine, exists, update
from sqlalchemy.orm import Session, aliased, sessionmaker

from engine import POLICY_VERSION, evaluate_event, simulate_operation
from models import Base, Decision
from schemas import ApprovalRequest, ApprovalResponse, DecisionDetail, EvaluationResponse, WorkloadEvent
from security import Principal, authenticate

app = FastAPI(title="GPU FinOps Engine", description="Simulates approval-gated GPU reclamation decisions.", version="0.2.0")
DATABASE_URL = os.getenv("GPU_FINOPS_DATABASE_URL", "sqlite:///./gpu_finops.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite:") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    # Do not echo input values: they can contain non-finite numbers or sensitive data.
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]})


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def require_scope(principal: Principal, role: str, team: str, workload: str, *, conceal: bool = False) -> None:
    if not principal.permits(role, team, workload):
        if conceal:
            raise HTTPException(status_code=404, detail="Decision not found")
        raise HTTPException(status_code=403, detail="Insufficient permissions")


@app.get("/health")
def health():
    return {"status": "ok", "service": "GPU FinOps Engine"}


@app.post("/evaluate", response_model=EvaluationResponse)
def evaluate(event: WorkloadEvent, principal: Principal = Depends(authenticate), db: Session = Depends(get_db)):
    require_scope(principal, "evaluate", event.team_name, event.workload_id)
    result = evaluate_event(event)
    decision = Decision(
        gpu_id=event.gpu_id, workload_id=event.workload_id, owner_email=event.owner_email,
        team_name=event.team_name, environment=event.environment, workload_type=event.workload_type,
        pod_status=event.pod_status, checkpoint_state=event.checkpoint_state,
        sm_utilisation=event.sm_utilisation, memory_used_mb=event.memory_used_mb,
        hourly_rate=event.hourly_rate, last_activity_at=event.last_activity_at,
        workload_state=result["workload_state"], decision=result["decision"],
        reason_codes=result["reason_codes"], avoidable_cost=result["avoidable_cost"],
        # -1 is an internal sentinel compatible with existing non-null SQLite columns.
        disruption_cost=result["disruption_cost"] if result["disruption_cost"] is not None else -1,
        net_benefit=result["net_benefit"] if result["net_benefit"] is not None else -1,
        policy_version=result["policy_version"],
        status="AWAITING_APPROVAL" if result["decision"] == "REQUIRE_APPROVAL" else "NO_ACTION",
        audit_trail=[{"timestamp": datetime.now(timezone.utc).isoformat(), "event": "DECISION_CREATED",
                      "decision": result["decision"], "actor": principal.subject,
                      "savings_horizon_hours": event.savings_horizon_hours,
                      "estimated_disruption_cost": event.estimated_disruption_cost}],
    )
    db.add(decision)
    db.commit()
    db.refresh(decision)
    return decision


@app.post("/decisions/{decision_id}/approve", response_model=ApprovalResponse)
def approve_decision(decision_id: str, request: ApprovalRequest,
                     principal: Principal = Depends(authenticate), db: Session = Depends(get_db)):
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    require_scope(principal, "approve", decision.team_name, decision.workload_id, conceal=True)
    if decision.status != "AWAITING_APPROVAL" or decision.decision != "REQUIRE_APPROVAL" or decision.policy_version != POLICY_VERSION:
        raise HTTPException(status_code=409, detail="Decision is not eligible for approval")

    now = datetime.now(timezone.utc)
    status = "SIMULATED" if request.approved else "REJECTED"
    operation_id = simulate_operation() if request.approved else None
    audit = list(decision.audit_trail or []) + [{"timestamp": now.isoformat(),
        "event": "ACTION_SIMULATED" if request.approved else "ACTION_REJECTED",
        "actor": principal.subject, "operation_id": operation_id}]
    conditions = [Decision.decision_id == decision_id, Decision.status == "AWAITING_APPROVAL",
                  Decision.decision == "REQUIRE_APPROVAL", Decision.policy_version == POLICY_VERSION]
    if request.approved:
        newer = aliased(Decision)
        conditions.extend([
            Decision.checkpoint_state.notin_(["IN_PROGRESS", "FAILED"]),
            Decision.workload_state.notin_(["CHECKPOINTING", "ACTIVE_COMPUTE"]),
            Decision.created_at >= (now - timedelta(minutes=5)).replace(tzinfo=None),
            ~exists().where(and_(newer.team_name == Decision.team_name,
                newer.workload_id == Decision.workload_id,
                newer.decision_id != Decision.decision_id, newer.created_at >= Decision.created_at))
        ])
    result = db.execute(update(Decision).where(*conditions).values(
        status=status, operation_id=operation_id, verification=None, audit_trail=audit
    ).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="Decision is stale or no longer eligible")
    db.commit()
    return ApprovalResponse(decision_id=decision_id, status=status, operation_id=operation_id)


@app.get("/decisions/{decision_id}", response_model=DecisionDetail)
def get_decision(decision_id: str, principal: Principal = Depends(authenticate), db: Session = Depends(get_db)):
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    require_scope(principal, "read", decision.team_name, decision.workload_id, conceal=True)
    return decision
