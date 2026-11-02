"""ORM models.

Shape borrowed from proven incident tools: per-state timestamps and a resolution reason (Netflix
Dispatch), AI and human values stored side by side so an override never erases what the AI said
(Keep), approval bound to a plan hash (supplier-disruption).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.clock import now
from app.db import Base, UTCDateTime


def _id_col() -> Mapped[int]:
    return mapped_column(Integer, primary_key=True)


# --------------------------------------------------------------------------- people & master data


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = _id_col()
    email: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(30))  # analyst|ops_manager|regional_head|director|auditor|admin
    region: Mapped[str] = mapped_column(String(10))  # WEST|NORTH|SOUTH|ALL
    approval_limit_inr: Mapped[float] = mapped_column(Float, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Store(Base):
    __tablename__ = "stores"
    id: Mapped[int] = _id_col()
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    city: Mapped[str] = mapped_column(String(60))
    region: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(10), default="store")  # store|dc
    serving_dc: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|launching|postponed
    launch_date: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    expected_daily_revenue_inr: Mapped[float] = mapped_column(Float, default=0)
    manager_name: Mapped[str] = mapped_column(String(120), default="")
    manager_email: Mapped[str] = mapped_column(String(120), default="")


class Sku(Base):
    __tablename__ = "skus"
    id: Mapped[int] = _id_col()
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    unit_cost_inr: Mapped[float] = mapped_column(Float, default=0)


class InventoryLevel(Base):
    __tablename__ = "inventory"
    __table_args__ = (UniqueConstraint("sku_code", "location_code"),)
    id: Mapped[int] = _id_col()
    sku_code: Mapped[str] = mapped_column(String(20), index=True)
    location_code: Mapped[str] = mapped_column(String(20), index=True)
    on_hand: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    transferable: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = _id_col()
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    contact_email: Mapped[str] = mapped_column(String(120), default="")
    reliability: Mapped[float] = mapped_column(Float, default=0.9)
    is_contracted: Mapped[bool] = mapped_column(Boolean, default=True)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[int] = _id_col()
    po_number: Mapped[str] = mapped_column(String(30), unique=True)
    supplier_code: Mapped[str] = mapped_column(String(20))
    sku_code: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[int] = mapped_column(Integer)
    dest_location: Mapped[str] = mapped_column(String(20))
    promised_date: Mapped[datetime] = mapped_column(UTCDateTime)
    revised_date: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|delayed|delivered|cancelled


class SupplierQuote(Base):
    """Offers the planner can discover through tools: alternate supply, expedite, transfer lanes."""

    __tablename__ = "supplier_quotes"
    id: Mapped[int] = _id_col()
    kind: Mapped[str] = mapped_column(String(20))  # alternate_supply|expedite|transfer
    supplier_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sku_code: Mapped[str] = mapped_column(String(20))
    from_location: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_location: Mapped[str | None] = mapped_column(String(20), nullable=True)
    max_quantity: Mapped[int] = mapped_column(Integer)
    lead_days: Mapped[float] = mapped_column(Float)
    unit_premium_inr: Mapped[float] = mapped_column(Float, default=0)
    fixed_cost_inr: Mapped[float] = mapped_column(Float, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")


# --------------------------------------------------------------------------- the case


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[int] = _id_col()
    ref: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200), default="New request")
    summary: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(30), default="other")
    status: Mapped[str] = mapped_column(String(30), default="NEW", index=True)
    priority: Mapped[str | None] = mapped_column(String(4), nullable=True)  # P1..P4
    risk_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    region: Mapped[str | None] = mapped_column(String(10), nullable=True)
    store_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sku_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    intent_clarity: Mapped[str] = mapped_column(String(20), default="clear")
    overall_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    flags: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # intake/risk snapshots
    decision_deadline_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    sla_paused_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ledger_version: Mapped[int] = mapped_column(Integer, default=1)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True)
    parent_case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolution_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resolution_note: Mapped[str] = mapped_column(Text, default="")
    processing: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now, onupdate=now)
    triaged_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    messages: Mapped[list["CaseMessage"]] = relationship(back_populates="case", order_by="CaseMessage.id")
    facts: Mapped[list["Fact"]] = relationship(back_populates="case", order_by="Fact.id")
    issues: Mapped[list["Issue"]] = relationship(back_populates="case", order_by="Issue.id")


class CaseMessage(Base):
    __tablename__ = "case_messages"
    id: Mapped[int] = _id_col()
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="email")  # email|manual|webhook|eml
    from_addr: Mapped[str] = mapped_column(String(200), default="")
    from_name: Mapped[str] = mapped_column(String(200), default="")
    subject: Mapped[str] = mapped_column(String(300), default="")
    body: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    relation: Mapped[str] = mapped_column(String(20), default="original")  # original|duplicate|update|related
    guard_label: Mapped[str | None] = mapped_column(String(20), nullable=True)
    guard_signals: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    case: Mapped[Case] = relationship(back_populates="messages")


class Fact(Base):
    """Entry in the case's fact ledger: the shared blackboard every agent reads from and cites."""

    __tablename__ = "facts"
    __table_args__ = (UniqueConstraint("case_id", "key"),)
    id: Mapped[int] = _id_col()
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    ref: Mapped[str] = mapped_column(String(8))  # F1, F2 ... stable within a case
    key: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(20))  # email|inventory|po|calendar|derived|human|update
    ai_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    human_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="stated")  # stated|inferred|missing|verified|unverified
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    grounded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    edited_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    edit_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now, onupdate=now)
    case: Mapped[Case] = relationship(back_populates="facts")

    @property
    def value(self) -> Any:
        return self.human_value if self.human_value is not None else self.ai_value


class Issue(Base):
    __tablename__ = "issues"
    id: Mapped[int] = _id_col()
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    type: Mapped[str] = mapped_column(String(30))
    # missing|conflict|injection|low_confidence|contradiction|degraded|ambiguity|duplicate|
    # comms_failure|material_change|cancellation|sla_breach|out_of_scope|system_unavailable
    severity: Mapped[str] = mapped_column(String(10), default="medium")  # low|medium|high
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(10), default="open")  # open|resolved
    blocking: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    case: Mapped[Case] = relationship(back_populates="issues")


# --------------------------------------------------------------------------- agents


class AgentRun(Base):
    """One agent invocation: the message-bus record and the trace at the same time."""

    __tablename__ = "agent_runs"
    id: Mapped[int] = _id_col()
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), index=True, nullable=True)
    agent: Mapped[str] = mapped_column(String(30))
    step: Mapped[str] = mapped_column(String(40))
    prompt_version: Mapped[str] = mapped_column(String(20), default="")
    model: Mapped[str] = mapped_column(String(60), default="")
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    ledger_version: Mapped[int] = mapped_column(Integer, default=1)
    input_hash: Mapped[str] = mapped_column(String(64), default="")
    input_json: Mapped[Any] = mapped_column(JSON, nullable=True)
    output_json: Mapped[Any] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="ok")  # ok|failed|degraded|superseded|skipped
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_retries: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[int] = _id_col()
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    ledger_version: Mapped[int] = mapped_column(Integer, default=1)
    option_key: Mapped[str] = mapped_column(String(8))
    option: Mapped[dict[str, Any]] = mapped_column(JSON)
    review: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(10), default="ai")  # ai|human
    status: Mapped[str] = mapped_column(String(12), default="proposed")  # proposed|selected|rejected|superseded|approved
    plan_hash: Mapped[str] = mapped_column(String(64))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[int] = _id_col()
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    recommendation_id: Mapped[int] = mapped_column(ForeignKey("recommendations.id"))
    plan_hash: Mapped[str] = mapped_column(String(64))
    ledger_version: Mapped[int] = mapped_column(Integer)
    amount_inr: Mapped[float] = mapped_column(Float)
    required_role: Mapped[str] = mapped_column(String(30))
    region: Mapped[str] = mapped_column(String(10))
    assigned_to_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    escalation_level: Mapped[int] = mapped_column(Integer, default=0)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)  # why approval is mandatory
    due_at: Mapped[datetime] = mapped_column(UTCDateTime)
    reminded_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="pending")
    # pending|approved|rejected|escalated|superseded|cancelled|info_requested
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget_cap_inr: Mapped[float | None] = mapped_column(Float, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class ActionItem(Base):
    __tablename__ = "actions"
    id: Mapped[int] = _id_col()
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    owner: Mapped[str] = mapped_column(String(120), default="")
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="open")  # open|done|cancelled
    completed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Communication(Base):
    __tablename__ = "communications"
    id: Mapped[int] = _id_col()
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), index=True, nullable=True)
    audience: Mapped[str] = mapped_column(String(30))
    # requester_ack|clarification|supplier|store_manager|approver_brief|status_update|management|system
    channel: Mapped[str] = mapped_column(String(10), default="email")  # email|in_app
    to: Mapped[list[str]] = mapped_column(JSON, default=list)
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    fact_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    generated_by: Mapped[str] = mapped_column(String(10), default="ai")  # ai|human|system|template
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft|queued|sent|failed|dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class Notification(Base):
    """In-app channel. Also the fallback when email delivery fails."""

    __tablename__ = "notifications"
    id: Mapped[int] = _id_col()
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(30))
    text: Mapped[str] = mapped_column(Text)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class AuditEvent(Base):
    """Append-only, hash-chained. UPDATE/DELETE are blocked by DB triggers (see db.init_db)."""

    __tablename__ = "audit_events"
    id: Mapped[int] = _id_col()
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=now, index=True)
    case_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    actor_type: Mapped[str] = mapped_column(String(10))  # ai|human|system
    actor_id: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity: Mapped[str] = mapped_column(String(30), default="case")
    entity_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    before: Mapped[Any] = mapped_column(JSON, nullable=True)
    after: Mapped[Any] = mapped_column(JSON, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
