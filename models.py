from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime, Float, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Decision(Base):
    __tablename__ = "decisions"

    decision_id = Column(String, primary_key=True, default=lambda: f"dec_{uuid4().hex[:12]}")
    created_at = Column(DateTime, default=datetime.utcnow)

    gpu_id = Column(String, nullable=False)
    workload_id = Column(String, nullable=False)
    owner_email = Column(String, nullable=False)
    team_name = Column(String, nullable=False)
    environment = Column(String, nullable=False)
    workload_type = Column(String, nullable=False)
    pod_status = Column(String, nullable=False)
    checkpoint_state = Column(String, nullable=False)
    sm_utilisation = Column(Float, nullable=False)
    memory_used_mb = Column(Float, nullable=False)
    hourly_rate = Column(Float, nullable=False)
    last_activity_at = Column(DateTime, nullable=False)

    workload_state = Column(String, nullable=False)
    decision = Column(String, nullable=False)
    reason_codes = Column(JSON, nullable=False)
    avoidable_cost = Column(Float, nullable=False)
    disruption_cost = Column(Float, nullable=False)
    net_benefit = Column(Float, nullable=False)
    policy_version = Column(String, nullable=False)

    status = Column(String, default="AWAITING_APPROVAL")
    operation_id = Column(String, nullable=True)
    verification = Column(String, nullable=True)
    audit_trail = Column(JSON, default=list)
