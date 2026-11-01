from datetime import datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class WorkloadEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    gpu_id: str = Field(min_length=1, max_length=256)
    workload_id: str = Field(min_length=1, max_length=256)
    owner_email: EmailStr
    team_name: str = Field(min_length=1, max_length=256)
    environment: Literal["dev", "staging", "prod"]
    workload_type: Literal["notebook", "training", "inference", "batch"]
    pod_status: Literal["Running", "Pending", "Failed", "Completed"]
    checkpoint_state: Literal["NONE", "IN_PROGRESS", "COMPLETE", "FAILED"]
    sm_utilisation: float = Field(ge=0, le=100, allow_inf_nan=False)
    memory_used_mb: float = Field(ge=0, le=1_000_000_000, allow_inf_nan=False)
    hourly_rate: float = Field(gt=0, le=1_000_000, allow_inf_nan=False)
    last_activity_at: datetime
    savings_horizon_hours: float = Field(default=1, gt=0, le=24, allow_inf_nan=False)
    estimated_disruption_cost: Optional[float] = Field(default=None, ge=0, le=1_000_000_000, allow_inf_nan=False)

    @field_validator("last_activity_at")
    @classmethod
    def validate_activity_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("last_activity_at must include a timezone")
        value = value.astimezone(timezone.utc)
        if value > datetime.now(timezone.utc):
            raise ValueError("last_activity_at cannot be in the future")
        return value


class EvaluationResponse(BaseModel):
    decision_id: str
    gpu_id: str
    workload_id: str
    owner_email: str
    workload_state: str
    decision: str
    reason_codes: List[str]
    avoidable_cost: float
    disruption_cost: Optional[float]
    net_benefit: Optional[float]
    policy_version: str

    @field_validator("disruption_cost", "net_benefit", mode="before")
    @classmethod
    def unknown_cost(cls, value):
        return None if value == -1 else value


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool = Field(strict=True)


class ApprovalResponse(BaseModel):
    decision_id: str
    status: str
    operation_id: Optional[str] = None
    verification: Optional[str] = None


class DecisionDetail(EvaluationResponse):
    status: str
    operation_id: Optional[str] = None
    verification: Optional[str] = None
    audit_trail: List[dict]
