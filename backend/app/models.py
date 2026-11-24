    category = Column(String(64), nullable=False, default="general")
    location = Column(String(200), nullable=False, default="Unknown")
    manufacturer = Column(String(120), nullable=True)
    model_number = Column(String(120), nullable=True)
    installed_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    maintenance_logs = Column(Text, nullable=False, default="[]")  # JSON array of service events
    weibull_k = Column(Float, nullable=True)      # shape parameter
    weibull_lambda = Column(Float, nullable=True)  # scale parameter (days)
    last_service_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(24), nullable=False, default="operational")  # operational, degraded, failed, retired
    photo_ref = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (
        CheckConstraint("weibull_k IS NULL OR weibull_k > 0", name="ck_equipment_weibull_k_positive"),
        CheckConstraint("weibull_lambda IS NULL OR weibull_lambda > 0", name="ck_equipment_weibull_lambda_positive"),
        Index("ix_equipment_tenant_location", "tenant_id", "location"),
    )


class MaintenanceEvent(Base):
    """Service / repair event for equipment."""
    __tablename__ = "maintenance_events"

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(64), ForeignKey("colleges.id"), nullable=False, index=True)
    equipment_id = Column(String(64), ForeignKey("equipment.id"), nullable=False, index=True)
    event_type = Column(String(32), nullable=False, default="repair")  # repair, inspection, replacement, calibration
    description = Column(Text, nullable=True)
    performed_by = Column(String(64), ForeignKey("users.id"), nullable=True)
    cost = Column(Numeric(10, 2), nullable=True)
    is_failure = Column(Boolean, nullable=False, default=False)
    event_date = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    created_at = Column(DateTime(timezone=True), default=utcnow)


# ============================================================================
# 13. EVENTS — OCR content, embeddings, faculty_approval_flag
# ============================================================================

class Event(Base):
    """
    v2 required: OCR content, embeddings, faculty_approval_flag
    Campus events / notices parsed from posters/text.
    """
    __tablename__ = "events"

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(64), ForeignKey("colleges.id"), nullable=False, index=True)
    title = Column(String(300), nullable=False)
    category = Column(String(64), nullable=False, default="general")  # hackathon, scholarship, fest, seminar, notice
    organizer = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    ocr_content = Column(Text, nullable=True)   # raw OCR-extracted text
    ocr_confidence = Column(Float, nullable=True)
    embedding = Column(Vector384(), nullable=True)
    faculty_approval_flag = Column(Boolean, nullable=False, default=False)
    approved_by = Column(String(64), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    source_poster_ref = Column(String(500), nullable=True)  # encrypted storage ref
    start_date = Column(DateTime(timezone=True), nullable=True)
    end_date = Column(DateTime(timezone=True), nullable=True)
    registration_deadline = Column(DateTime(timezone=True), nullable=True)
    location = Column(String(200), nullable=True)
    eligibility = Column(Text, nullable=True)
    external_url = Column(String(500), nullable=True)
    is_published = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_event_tenant_category", "tenant_id", "category"),
    )


# ============================================================================
# 14. KNOWLEDGE STATE (DKT)
# ============================================================================

class KnowledgeState(Base):
    __tablename__ = "knowledge_states"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(64), ForeignKey("colleges.id"), nullable=False, index=True)
    student_id = Column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    course_id = Column(String(64), ForeignKey("courses.id"), nullable=False, index=True)
    concept_code = Column(String(40), nullable=False)
    bkt_mastery = Column(Float, nullable=False, default=0.5)
    dkt_mastery = Column(Float, nullable=False, default=0.5)
    attempts = Column(Integer, nullable=False, default=0)
    correct_count = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("student_id", "course_id", "concept_code", name="uq_knowledge_state"),
    )


# ============================================================================
# 15. STUDY PLANS
# ============================================================================

class StudyPlan(Base):
    """Container for a student's what-if / study optimization session."""
    __tablename__ = "study_plans"

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(64), ForeignKey("colleges.id"), nullable=False, index=True)
    student_id = Column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    total_weekly_hours = Column(Float, nullable=False, default=20.0)
    allocation_json = Column(Text, nullable=False, default="{}")  # {course_id: hours}
    projected_gpa = Column(Float, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class StudyPlanItem(Base):
    __tablename__ = "study_plan_items"

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(64), ForeignKey("colleges.id"), nullable=False, index=True)
    student_id = Column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    course_id = Column(String(64), ForeignKey("courses.id"), nullable=False, index=True)
    plan_id = Column(String(64), ForeignKey("study_plans.id"), nullable=True)
    concept_code = Column(String(40), nullable=False)
    scheduled_date = Column(String(10), nullable=False, index=True)
    duration_minutes = Column(Integer, nullable=False, default=25)
    activity = Column(String(240), nullable=False)
    completed = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)


# ============================================================================
# 16. MAINTENANCE / FACILITIES TICKETS & DEDUP GROUPS
# ============================================================================

class MaintenanceTicket(Base):
    __tablename__ = "maintenance_tickets"

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(64), ForeignKey("colleges.id"), nullable=False, index=True)
    reporter_id = Column(String(64), ForeignKey("users.id"), nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    location = Column(String(160), nullable=False, default="Lab 2 - Bench 14")
    asset_id = Column(String(64), ForeignKey("equipment.id"), nullable=True, index=True)
    photo_path = Column(String(300), nullable=True)
    vision_category = Column(String(64), nullable=False, default="general")
    vision_confidence = Column(Float, nullable=False, default=0.0)
    embedding = Column(Vector384(), nullable=True)
    embedding_json = Column(Text, nullable=False, default="[]")
    dedup_group_id = Column(String(64), ForeignKey("dedup_groups.id"), nullable=True, index=True)
    merged_into_id = Column(String(64), nullable=True)
    merged_count = Column(Integer, nullable=False, default=1)
    assigned_to_id = Column(String(64), ForeignKey("users.id"), nullable=True)
    priority = Column(String(24), nullable=False, default="Medium")
    priority_weight = Column(Float, nullable=False, default=1.0)
    status = Column(String(32), nullable=False, default="Ticket verified")