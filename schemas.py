from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field


class WorkloadEvent(BaseModel):
    gpu_id: str = Field(min_length=1)
    workload_id: str = Field(min_length=1)
    owner_email: EmailStr
    team_name: str = Field(min_length=1)
    environment: Literal["dev", "staging", "prod"]
    workload_type: Literal["notebook", "training", "inference", "batch"]
    pod_status: Literal["Running", "Pending", "Failed", "Completed"]
    checkpoint_state: Literal["NONE", "IN_PROGRESS", "COMPLETE", "FAILED"]
    sm_utilisation: float = Field(ge=0, le=100)
    memory_used_mb: float = Field(ge=0)
    hourly_rate: float = Field(gt=0)
    last_activity_at: datetime


class EvaluationResponse(BaseModel):
    decision_id: str
    gpu_id: str
    workload_id: str
    owner_email: str
    workload_state: str
    decision: str
    reason_codes: List[str]
    avoidable_cost: float
    disruption_cost: float
    net_benefit: float
    policy_version: str


class ApprovalRequest(BaseModel):
    approver_email: EmailStr
    approved: bool


class ApprovalResponse(BaseModel):
    decision_id: str
    status: str
    operation_id: Optional[str] = None
    verification: Optional[str] = None


class DecisionDetail(BaseModel):
    decision_id: str
    gpu_id: str
    workload_id: str
    owner_email: str
    workload_state: str
    decision: str
    reason_codes: List[str]
    avoidable_cost: float
    disruption_cost: float
    net_benefit: float
    status: str
    operation_id: Optional[str] = None
    verification: Optional[str] = None
    audit_trail: List[dict]
