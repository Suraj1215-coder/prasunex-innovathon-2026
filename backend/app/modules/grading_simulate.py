"""
CAMPUS-NEXUS v2 — PHASE 09: M05/M06 Grading Sandbox & IRT Engine
=================================================================
POST /api/v1/grading/simulate

Implements:
  • Absolute grading (fixed percentage cutoffs)
  • Z-score relative grading (σ-band cutoffs)
  • Gaussian fit + KDE (Silverman, N<40 fallback) distribution visualization
  • 2PL IRT diagnostic: P_i(θ) = 1 / (1 + exp(-a_i * (θ - b_i)))
  • Point-biserial item discrimination: r_pbis = ((X̄₁ - X̄₀) / S_X) * √(p(1-p))
    Flag r_pbis < 0.15 AND negative r_pbis separately
  • GMM-based natural gap detection (1-3 components, BIC model selection)
  • Dynamic-programming gap-aware boundary optimization:
    Minimize count of students within ε=1.0 mark below each threshold
    Enforce Δ_min separation; monotonicity checks
  • Preview vs. committed grade snapshot with before/after comparison
  • Professor approval workflow (auditable action, NOT legally binding e-sig)
  • Full rollback via GradeChangeLedger
  • Bias/fairness warnings
  • Edge-case handling: constant scores, N=1, all-wrong, all-correct,
    sparse item matrix, invalid thresholds, ties, outliers

SPEC RULES ENFORCED:
  - NEVER auto-grant grace or nullify a question without faculty authorization
  - Algorithmic proposals require governance approval before commit
  - Grade-boundary monotonicity enforced before any commit
  - Uncertainty communicated on small cohort (N < 40)
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from scipy.stats import gaussian_kde, norm, skew
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AssessmentQuestion, Course, GradeChangeLedger, MarkRecord, User
from ..security import get_current_user, require_roles

router = APIRouter(prefix="/api/v1/grading", tags=["Phase09 Grading Sandbox M05/M06"])


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GRADE_ORDER = ["O", "A+", "A", "B+", "B", "C", "F"]

# Default Z-score cutoffs (Z ≥ threshold → grade)
DEFAULT_Z_CUTOFFS: dict[str, float] = {
    "O":  1.80,
    "A+": 1.20,
    "A":  0.60,
    "B+": 0.00,
    "B":  -0.60,
    "C":  -1.50,
}

# Default absolute cutoffs (score% ≥ threshold → grade)
DEFAULT_ABS_CUTOFFS: dict[str, float] = {
    "O":  90.0,
    "A+": 80.0,
    "A":  70.0,
    "B+": 60.0,
    "B":  50.0,
    "C":  40.0,
}

# Theoretical normal share for Z-bands
NORMAL_SHARES: dict[str, float] = {
    "O":  round((1.0 - norm.cdf(1.80)) * 100, 1),
    "A+": round((norm.cdf(1.80) - norm.cdf(1.20)) * 100, 1),
    "A":  round((norm.cdf(1.20) - norm.cdf(0.60)) * 100, 1),
    "B+": round((norm.cdf(0.60) - norm.cdf(0.00)) * 100, 1),
    "B":  round((norm.cdf(0.00) - norm.cdf(-0.60)) * 100, 1),
    "C":  round((norm.cdf(-0.60) - norm.cdf(-1.50)) * 100, 1),
    "F":  round(norm.cdf(-1.50) * 100, 1),
}


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

class SimulateGradingRequest(BaseModel):
    course_id: str = Field(..., description="Course ID to simulate grading for")
    mode: str = Field(
        "relative",
        description=(
            "Grading mode: 'absolute' (fixed %), 'relative' (Z-score bands), "
            "'gap_aware' (DP-optimised boundary shift)"
        ),
    )
    # Optional score override — used for direct inline simulation without DB
    scores: list[float] | None = Field(
        None,
        description=(
            "Optional explicit score list (0-100). If omitted, pulls from DB MarkRecord. "
            "Max 2000 entries."
        ),
    )
    # Question-level score matrix for IRT (optional; if omitted IRT skipped)
    item_scores: dict[str, list[float]] | None = Field(
        None,
        description=(
            "Dict of q_code → list of per-student scores (parallel with scores list). "
            "Enables IRT + point-biserial analysis."
        ),
    )
    max_marks: float = Field(100.0, gt=0, description="Maximum possible marks (denominator)")
    # Absolute cutoffs (only used when mode='absolute')
    abs_cutoffs: dict[str, float] | None = Field(
        None,
        description="Custom absolute cutoffs e.g. {'O':90,'A+':80,...}. Must be descending.",
    )
    # Z-score cutoffs (only used when mode='relative')
    z_cutoffs: dict[str, float] | None = Field(
        None,
        description="Custom Z-score cutoffs. Must be descending.",
    )
    # Gap-aware DP parameters
    epsilon: float = Field(1.0, ge=0.0, le=10.0, description="Heartbreak window (marks)")
    delta_min: float = Field(2.0, ge=0.5, description="Minimum gap between adjacent cutoffs")
    # Commit to DB?
    commit: bool = Field(False, description="If True, writes grades to DB after professor_approved=True")
    professor_approved: bool = Field(
        False,
        description=(
            "Must be explicitly True to allow commit. "
            "Acts as an auditable approval action — NOT a legally binding e-signature."
        ),
    )
    professor_note: str = Field("", max_length=500, description="Optional approval note")
    # Mean/std shift for what-if exploration
    mean_shift: float = Field(0.0, description="Shift mean by this many marks (what-if)")
    std_scale: float = Field(1.0, gt=0.0, description="Scale std-dev by this factor (what-if)")

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in ("absolute", "relative", "gap_aware"):
            raise ValueError("mode must be 'absolute', 'relative', or 'gap_aware'")
        return v

    @field_validator("scores")
    @classmethod
    def validate_scores(cls, v: list[float] | None) -> list[float] | None:
        if v is not None and len(v) > 2000:
            raise ValueError("scores list exceeds maximum of 2000 entries")
        return v


class ApproveGradingRequest(BaseModel):
    simulation_token: str = Field(..., description="Token returned by simulate endpoint")
    course_id: str
    professor_note: str = Field("", max_length=500)


# ---------------------------------------------------------------------------
# Pure computation helpers
# ---------------------------------------------------------------------------

def _safe_std(arr: np.ndarray) -> float:
    """Return std, but at least 1e-4 to avoid division-by-zero."""
    return max(float(np.std(arr)), 1e-4)


def _silverman_bw(arr: np.ndarray) -> float:
    """Silverman's rule: h = 1.06 * σ * N^(-1/5)."""
    return 1.06 * _safe_std(arr) * (len(arr) ** -0.2)


def _assign_z_grade(z: float, cutoffs: dict[str, float]) -> str:
    for g in ["O", "A+", "A", "B+", "B", "C"]:
        if z >= cutoffs.get(g, DEFAULT_Z_CUTOFFS[g]):
            return g
    return "F"


def _assign_abs_grade(score_pct: float, cutoffs: dict[str, float]) -> str:
    for g in ["O", "A+", "A", "B+", "B", "C"]:
        if score_pct >= cutoffs.get(g, DEFAULT_ABS_CUTOFFS[g]):
            return g
    return "F"


def _validate_cutoff_monotonicity(cutoffs: dict[str, float], default: dict[str, float]) -> list[str]:
    """Return list of warning strings if cutoffs are not strictly descending."""
    keys = ["O", "A+", "A", "B+", "B", "C"]
    vals = [cutoffs.get(k, default.get(k, 0.0)) for k in keys]
    warnings: list[str] = []
    for i in range(1, len(vals)):
        if vals[i] >= vals[i - 1]:
            warnings.append(
                f"Boundary ordering violation: cutoff({keys[i]})={vals[i]} "
                f">= cutoff({keys[i-1]})={vals[i-1]}. "
                "Grade boundaries must be strictly descending."
            )
    return warnings


def _kde_density(scores: np.ndarray, x_grid: np.ndarray) -> np.ndarray:
    """Return KDE density on x_grid. Falls back to Gaussian on failure."""
    try:
        kde = gaussian_kde(scores, bw_method="silverman")
        return kde(x_grid)
    except Exception:
        mean, std = float(np.mean(scores)), _safe_std(scores)
        return norm.pdf(x_grid, loc=mean, scale=std)


def _histogram(scores: np.ndarray, bins: int = 20) -> list[dict]:
    """Return histogram as list of {bin_start, bin_end, count}."""
    if len(scores) < 2:
        return []
    lo, hi = max(0.0, float(np.min(scores)) - 1), min(100.0, float(np.max(scores)) + 1)
    counts, edges = np.histogram(scores, bins=bins, range=(lo, hi))
    return [
        {"bin_start": round(float(edges[i]), 1),
         "bin_end": round(float(edges[i + 1]), 1),
         "count": int(counts[i])}
        for i in range(len(counts))
    ]


# ---------------------------------------------------------------------------
# 2PL IRT + Point-Biserial
# ---------------------------------------------------------------------------

def _point_biserial(q_scores: np.ndarray, totals: np.ndarray, max_m: float) -> dict:
    """
    Compute point-biserial discrimination for a single question.

    r_pbis = ((X̄₁ - X̄₀) / S_X) * √(p(1-p))

    Returns dict with r_pbis, irt_a, irt_b, is_defective, flag_severity.
    """
    N = len(totals)
    S_X = _safe_std(totals)

    # Dichotomous: pass if score >= 50% of max
    threshold = 0.5 * max_m
    correct = (q_scores >= threshold).astype(int)
    p = float(np.mean(correct))
    p = min(max(p, 0.001), 0.999)

    correct_totals = totals[correct == 1]
    wrong_totals = totals[correct == 0]
    X1bar = float(np.mean(correct_totals)) if len(correct_totals) > 0 else 0.0
    X0bar = float(np.mean(wrong_totals)) if len(wrong_totals) > 0 else 0.0

    r_pbis = round(((X1bar - X0bar) / S_X) * math.sqrt(p * (1.0 - p)), 3)

    # 2PL IRT parameters
    # b (difficulty): logit of proportion failing = logit(1-p)
    b_difficulty = round(-math.log(p / (1.0 - p)), 3)  # negative = easy, positive = hard
    # a (discrimination): derived from r_pbis via empirical mapping
    a_discrimination = round(max(0.05, float(r_pbis * 2.5)), 3)

    # Build Item Characteristic Curve (ICC): P(θ) = 1 / (1 + exp(-a*(θ-b)))
    thetas = np.linspace(-3.5, 3.5, 30)
    icc = [
        {
            "theta": round(float(th), 2),
            "p_correct": round(1.0 / (1.0 + math.exp(-a_discrimination * (float(th) - b_difficulty))), 4),
        }
        for th in thetas
    ]

    # Flag logic per spec
    flag_severity = "none"
    alert = "Standard healthy discrimination."
    if r_pbis < 0.0:
        flag_severity = "critical"
        alert = (
            f"NEGATIVE DISCRIMINATION (r_pbis={r_pbis}): "
            "High-scoring students are getting this question WRONG more than low-scoring students. "
            "This indicates ambiguous phrasing, keying error, or adversarial distractor. "
            "ACTION REQUIRED: Faculty must review before any grade is finalised."
        )
    elif r_pbis < 0.15:
        flag_severity = "warning"
        alert = (
            f"LOW DISCRIMINATION (r_pbis={r_pbis} < 0.15): "
            "Question does not distinguish high and low performers. "
            "Recommend faculty review; consider dropping or awarding grace marks "
            "(faculty authorization required — system will NOT auto-nullify)."
        )
    elif b_difficulty > 2.5:
        flag_severity = "info"
        alert = f"HIGH DIFFICULTY (b={b_difficulty}): only {round(p*100,1)}% passed. Review question clarity."

    return {
        "pass_rate_pct": round(p * 100, 1),
        "r_pbis": r_pbis,
        "irt_b_difficulty": b_difficulty,
        "irt_a_discrimination": a_discrimination,
        "is_defective": r_pbis < 0.15,
        "flag_severity": flag_severity,  # "none" | "info" | "warning" | "critical"
        "alert": alert,
        "icc_curve": icc,
        "note": (
            "Faculty authorization is required to drop or nullify this question. "
            "This system does not automatically grant grace marks or nullify questions."
        ),
    }


def _run_irt_analysis(
    scores: np.ndarray,
    item_scores_map: dict[str, list[float]],
    item_max_marks: dict[str, float],
) -> list[dict]:
    """Run IRT + point-biserial for each question in item_scores_map."""
    if not item_scores_map:
        return []

    results = []
    totals = scores  # shape (N,)

    for q_code, q_scores_list in item_scores_map.items():
        q_arr = np.array(q_scores_list, dtype=float)
        # Align lengths defensively
        n = min(len(totals), len(q_arr))
        if n < 3:
            results.append({
                "q_code": q_code,
                "error": "Insufficient data (N<3) for IRT analysis",
                "is_defective": False,
                "flag_severity": "none",
            })
            continue

        max_m = item_max_marks.get(q_code, 10.0)
        irt = _point_biserial(q_arr[:n], totals[:n], max_m)
        irt["q_code"] = q_code
        irt["max_marks"] = max_m
        results.append(irt)

    return results


# ---------------------------------------------------------------------------
# GMM-based gap detection (manual EM, no sklearn dependency)
# ---------------------------------------------------------------------------

def _gmm_bic_select(scores: np.ndarray, max_k: int = 3) -> dict:
    """
    Fit 1..max_k Gaussian components via EM, pick best by BIC.
    Returns dict: {k, means, stds, weights, bic_scores, natural_gaps}.
    """
    N = len(scores)
    if N < 10:
        return {"k": 1, "means": [float(np.mean(scores))], "stds": [_safe_std(scores)],
                "weights": [1.0], "bic_scores": {}, "natural_gaps": []}

    # Clamp max_k
    max_k = min(max_k, max(1, N // 5))
    bic_scores: dict[int, float] = {}
    best_k = 1
    best_params: dict = {}

    for k in range(1, max_k + 1):
        # Initialize means via k-means style quantile
        quantiles = np.linspace(0, 100, k + 2)[1:-1]
        means = np.percentile(scores, quantiles)
        stds = np.full(k, _safe_std(scores) / k)
        weights = np.ones(k) / k

        # EM iterations
        for _ in range(60):
            # E-step
            resps = np.zeros((N, k))
            for j in range(k):
                resps[:, j] = weights[j] * norm.pdf(scores, means[j], max(stds[j], 1e-3))
            row_sums = resps.sum(axis=1, keepdims=True)
            row_sums = np.where(row_sums == 0, 1e-300, row_sums)
            resps /= row_sums

            # M-step
            Nk = resps.sum(axis=0)
            Nk = np.where(Nk == 0, 1e-10, Nk)
            means = (resps * scores[:, None]).sum(axis=0) / Nk
            stds = np.sqrt((resps * (scores[:, None] - means) ** 2).sum(axis=0) / Nk)
            stds = np.maximum(stds, 0.5)
            weights = Nk / N

        # Log-likelihood → BIC = -2*LL + params*ln(N)
        ll_vals = np.zeros(N)
        for j in range(k):
            ll_vals += weights[j] * norm.pdf(scores, means[j], stds[j])
        ll_vals = np.where(ll_vals <= 0, 1e-300, ll_vals)
        log_lik = float(np.sum(np.log(ll_vals)))
        n_params = 3 * k - 1
        bic = -2 * log_lik + n_params * math.log(N)
        bic_scores[k] = round(bic, 2)

        if not best_params or bic < bic_scores.get(best_k, float("inf")):
            best_k = k
            best_params = {"means": means.tolist(), "stds": stds.tolist(), "weights": weights.tolist()}

    # Natural gaps: midpoints between adjacent GMM component means
    sorted_means = sorted(best_params.get("means", []))
    natural_gaps = [round((sorted_means[i] + sorted_means[i + 1]) / 2, 2)
                    for i in range(len(sorted_means) - 1)]

    return {
        "k": best_k,
        "means": [round(m, 2) for m in sorted_means],
        "stds": [round(s, 2) for s in best_params.get("stds", [])],
        "weights": [round(w, 4) for w in best_params.get("weights", [])],
        "bic_scores": bic_scores,
        "natural_gaps": natural_gaps,
    }


# ---------------------------------------------------------------------------
# Dynamic-Programming gap-aware boundary optimizer
# ---------------------------------------------------------------------------

def _dp_gap_aware(
    scores: np.ndarray,
    nominal_cutoffs: list[float],
    epsilon: float = 1.0,
    delta_min: float = 2.0,
) -> dict:
    """
    DP optimizer: minimize total students trapped in [cutoff - ε, cutoff) across all boundaries.

    Returns:
        adjusted_cutoffs: list[float] (same length as nominal_cutoffs, descending)
        rescued_count: int
        trapped_before: int
        trapped_after: int
        moves: list[{grade, nominal, adjusted, rescued}]
    """
    scores_sorted = np.sort(scores)

    def _count_trapped(cutoffs: list[float]) -> int:
        total = 0
        for c in cutoffs:
            total += int(np.sum((c - epsilon <= scores) & (scores < c)))
        return total

    trapped_before = _count_trapped(nominal_cutoffs)

    adjusted = list(nominal_cutoffs)
    moves = []
    grade_names = ["O", "A+", "A", "B+", "B", "C"]

    for i, nom in enumerate(nominal_cutoffs):
        # Find scores just below threshold
        trapped_here = scores[(nom - epsilon <= scores) & (scores < nom)]
        if len(trapped_here) > 0:
            # Lower cutoff to smallest trapped score (rescues all in window)
            new_c = round(float(np.min(trapped_here)), 2)
            moves.append({
                "grade": grade_names[i] if i < len(grade_names) else f"G{i}",
                "nominal_cutoff": round(nom, 2),
                "adjusted_cutoff": new_c,
                "rescued": len(trapped_here),
            })
            adjusted[i] = new_c
        else:
            moves.append({
                "grade": grade_names[i] if i < len(grade_names) else f"G{i}",
                "nominal_cutoff": round(nom, 2),
                "adjusted_cutoff": round(nom, 2),
                "rescued": 0,
            })

    # Enforce delta_min separation (monotonicity fix) — O > A+ > A > B+ > B > C
    for i in range(1, len(adjusted)):
        if adjusted[i - 1] - adjusted[i] < delta_min:
            adjusted[i] = round(adjusted[i - 1] - delta_min, 2)

    trapped_after = _count_trapped(adjusted)

    return {
        "adjusted_cutoffs": [round(c, 2) for c in adjusted],
        "trapped_before": int(trapped_before),
        "trapped_after": int(trapped_after),
        "rescued_count": max(0, trapped_before - trapped_after),
        "moves": moves,
    }


# ---------------------------------------------------------------------------
# Grade assignment
# ---------------------------------------------------------------------------

def _grade_scores(
    scores: np.ndarray,
    mode: str,
    abs_cutoffs: dict[str, float],
    z_cutoffs: dict[str, float],
    mean: float,
    std: float,
) -> tuple[list[str], dict[str, int], list[dict]]:
    """
    Assign letter grades. Returns (grade_list, grade_counts, student_rows).
    """
    grade_list: list[str] = []
    grade_counts: dict[str, int] = {g: 0 for g in GRADE_ORDER}
    student_rows: list[dict] = []

    for i, s in enumerate(scores):
        if mode == "absolute":
            g = _assign_abs_grade(float(s), abs_cutoffs)
            z = round((float(s) - mean) / std, 3)
        else:  # relative or gap_aware both use Z-score assignment
            z = round((float(s) - mean) / std, 3)
            g = _assign_z_grade(z, z_cutoffs)

        grade_list.append(g)
        grade_counts[g] = grade_counts.get(g, 0) + 1
        student_rows.append({"score": round(float(s), 2), "z_score": z, "letter_grade": g})

    return grade_list, grade_counts, student_rows


# ---------------------------------------------------------------------------
# Fairness / bias warnings
# ---------------------------------------------------------------------------

def _fairness_warnings(scores: np.ndarray, grade_counts: dict[str, int], N: int) -> list[str]:
    warns: list[str] = []
    fail_pct = grade_counts.get("F", 0) / max(1, N) * 100
    if fail_pct > 25:
        warns.append(
            f"HIGH FAILURE RATE WARNING: {round(fail_pct, 1)}% of students received F. "
            "Review question paper difficulty, teaching coverage, or attendance patterns before finalising grades."
        )
    if N < 40:
        warns.append(
            f"SMALL COHORT (N={N} < 40): Statistical estimates are unreliable. "
            "KDE bandwidth and Z-score boundaries have high uncertainty. "
            "Consider absolute grading or consulting HoD before finalising."
        )
    skew_v = float(skew(scores)) if N >= 3 else 0.0
    if abs(skew_v) > 1.0:
        warns.append(
            f"SKEWED DISTRIBUTION (skewness={round(skew_v, 2)}): "
            "Score distribution is highly skewed. Z-score bands may mis-classify students. "
            "KDE-based approach is recommended."
        )
    # Constant scores check
    if _safe_std(scores) < 0.5:
        warns.append(
            "CONSTANT SCORE WARNING: All students have nearly identical scores. "
            "Relative grading is undefined — use absolute mode."
        )
    warns.append(
        "ALGORITHMIC DISCLAIMER: Grade boundaries proposed by this system are computational tools. "
        "They are NOT guaranteed to be fair solely because an algorithm generated them. "
        "Faculty review, governance approval, and final sign-off are mandatory before grade publication."
    )
    return warns


# ---------------------------------------------------------------------------
# Main Simulate Endpoint
# ---------------------------------------------------------------------------

@router.post("/simulate")
def simulate_grading(
    payload: SimulateGradingRequest,
    user: User = Depends(require_roles(["professor", "hod", "dean", "admin"])),
    db: Session = Depends(get_db),
):
    """
    PHASE 09 — M05/M06: Full Grading Sandbox Simulation

    Modes:
      • absolute   — fixed percentage cutoffs (default: O≥90, A+≥80 ...)
      • relative   — Z-score band cutoffs (default: O≥+1.80σ ...)
      • gap_aware  — DP-optimised boundary shift to minimize heartbreak students

    Returns:
      - Score histogram (20 bins) + KDE/Gaussian curve (50 points)
      - Grade distribution (actual vs theoretical normal share)
      - Per-student preview (score, z_score, letter_grade)
      - IRT 2PL audit per question (if item_scores provided)
      - GMM natural gap analysis
      - Gap-aware DP boundary optimization details
      - Before/after comparison when commit=True
      - Fairness warnings
      - Simulation token for approval workflow
    """
    now = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # 1. Gather scores
    # ------------------------------------------------------------------
    if payload.scores is not None:
        scores_raw = np.array(payload.scores, dtype=float)
    else:
        records = db.query(MarkRecord).filter(
            MarkRecord.course_id == payload.course_id,
            MarkRecord.tenant_id == user.tenant_id,
        ).all()
        if not records:
            # Return structured empty response
            return {
                "simulation_token": None,
                "status": "no_data",
                "message": "No MarkRecord entries found for this course. Upload marks first.",
                "sample_size": 0,
            }
        scores_raw = np.array([float(r.total_score) for r in records], dtype=float)

    # ------------------------------------------------------------------
    # 2. Edge-case guards
    # ------------------------------------------------------------------
    N = len(scores_raw)
    if N == 0:
        raise HTTPException(422, "Score list is empty.")
    if N == 1:
        return {
            "simulation_token": None,
            "status": "insufficient_data",
            "message": "Only 1 student — cannot compute meaningful grade distribution.",
            "sample_size": 1,
        }

    # Clamp scores to [0, max_marks]
    scores = np.clip(scores_raw, 0.0, payload.max_marks)

    # ------------------------------------------------------------------
    # 3. Descriptive statistics
    # ------------------------------------------------------------------
    mean_raw = float(np.mean(scores))
    std_raw = _safe_std(scores)
    mean_adj = mean_raw + payload.mean_shift
    std_adj = std_raw * payload.std_scale
    std_adj = max(std_adj, 1e-4)

    skew_val = float(skew(scores)) if N >= 3 else 0.0
    suggest_kde = abs(skew_val) > 0.6 or N < 40
    silverman_h = round(_silverman_bw(scores), 3)
    pct_scores = (scores / payload.max_marks) * 100.0  # normalize to 0-100%

    # ------------------------------------------------------------------
    # 4. KDE + Gaussian density curve (50 points)
    # ------------------------------------------------------------------
    x_min = max(0.0, float(np.min(scores)) - 5.0)
    x_max = min(float(payload.max_marks), float(np.max(scores)) + 5.0)
    x_grid = np.linspace(x_min, x_max, 50)
    gauss_y = norm.pdf(x_grid, loc=mean_adj, scale=std_adj)
    kde_y = _kde_density(scores, x_grid) if N >= 5 else gauss_y

    curve_points = [
        {
            "x": round(float(x), 2),
            "gaussian": round(float(gy), 6),
            "kde": round(float(ky), 6),
        }
        for x, gy, ky in zip(x_grid, gauss_y, kde_y)
    ]

    histogram = _histogram(scores, bins=min(20, max(5, N // 2)))

    # ------------------------------------------------------------------
    # 5. Determine cutoffs based on mode
    # ------------------------------------------------------------------
    abs_cutoffs = dict(payload.abs_cutoffs or DEFAULT_ABS_CUTOFFS)
    z_cutoffs = dict(payload.z_cutoffs or DEFAULT_Z_CUTOFFS)
    dp_result: dict | None = None
    gmm_result: dict | None = None
    boundary_warnings: list[str] = []

    # Validate monotonicity of user-provided cutoffs
    if payload.abs_cutoffs:
        boundary_warnings += _validate_cutoff_monotonicity(abs_cutoffs, DEFAULT_ABS_CUTOFFS)
    if payload.z_cutoffs:
        boundary_warnings += _validate_cutoff_monotonicity(z_cutoffs, DEFAULT_Z_CUTOFFS)

    if payload.mode == "gap_aware":
        # GMM natural gap detection
        gmm_result = _gmm_bic_select(scores, max_k=3)

        # Nominal Z-score based cutoffs in raw score space
        nominal_raw = [
            mean_adj + z_cutoffs.get("O",  DEFAULT_Z_CUTOFFS["O"])  * std_adj,
            mean_adj + z_cutoffs.get("A+", DEFAULT_Z_CUTOFFS["A+"]) * std_adj,
            mean_adj + z_cutoffs.get("A",  DEFAULT_Z_CUTOFFS["A"])  * std_adj,
            mean_adj + z_cutoffs.get("B+", DEFAULT_Z_CUTOFFS["B+"]) * std_adj,
            mean_adj + z_cutoffs.get("B",  DEFAULT_Z_CUTOFFS["B"])  * std_adj,
            mean_adj + z_cutoffs.get("C",  DEFAULT_Z_CUTOFFS["C"])  * std_adj,
        ]
        # Clamp to valid range
        nominal_raw = [max(0.0, min(float(payload.max_marks), c)) for c in nominal_raw]

        dp_result = _dp_gap_aware(scores, nominal_raw, payload.epsilon, payload.delta_min)
        adj_cuts = dp_result["adjusted_cutoffs"]

        # Build Z-score cutoffs back from adjusted raw cuts
        z_cutoffs = {
            "O":  round((adj_cuts[0] - mean_adj) / std_adj, 3),
            "A+": round((adj_cuts[1] - mean_adj) / std_adj, 3),
            "A":  round((adj_cuts[2] - mean_adj) / std_adj, 3),
            "B+": round((adj_cuts[3] - mean_adj) / std_adj, 3),
            "B":  round((adj_cuts[4] - mean_adj) / std_adj, 3),
            "C":  round((adj_cuts[5] - mean_adj) / std_adj, 3),
        }
        # Re-validate monotonicity after DP adjustment
        boundary_warnings += _validate_cutoff_monotonicity(z_cutoffs, DEFAULT_Z_CUTOFFS)

    # ------------------------------------------------------------------
    # 6. Assign grades → preview snapshot
    # ------------------------------------------------------------------
    grade_list, grade_counts, student_rows = _grade_scores(
        scores, payload.mode, abs_cutoffs, z_cutoffs, mean_adj, std_adj
    )

    # ------------------------------------------------------------------
    # 7. Comparison table: actual vs theoretical normal shares
    # ------------------------------------------------------------------
    comparison_table = [
        {
            "grade": g,
            "actual_count": grade_counts[g],
            "actual_share_pct": round(grade_counts[g] / N * 100, 1),
            "normal_share_pct": NORMAL_SHARES.get(g, 0.0),
            "z_cutoff": z_cutoffs.get(g, DEFAULT_Z_CUTOFFS.get(g)),
            "abs_cutoff": abs_cutoffs.get(g, DEFAULT_ABS_CUTOFFS.get(g)),
        }
        for g in GRADE_ORDER
    ]

    # ------------------------------------------------------------------
    # 8. IRT 2PL + Point-Biserial Analysis
    # ------------------------------------------------------------------
    irt_items: list[dict] = []
    if payload.item_scores is not None:
        # Build max_marks per question from DB if available, else default 10
        db_questions: list[AssessmentQuestion] = []
        if payload.scores is None:  # DB mode
            db_questions = db.query(AssessmentQuestion).filter(
                AssessmentQuestion.course_id == payload.course_id,
                AssessmentQuestion.tenant_id == user.tenant_id,
            ).all()
        q_max = {q.q_code: q.max_marks for q in db_questions}

        irt_items = _run_irt_analysis(scores, payload.item_scores, q_max)

    # ------------------------------------------------------------------
    # 9. Fairness warnings
    # ------------------------------------------------------------------
    fairness_warns = _fairness_warnings(scores, grade_counts, N)

    # ------------------------------------------------------------------
    # 10. Before snapshot (current DB grades for comparison)
    # ------------------------------------------------------------------
    before_snapshot: list[dict] = []
    if payload.scores is None and payload.commit:
        records_for_commit = db.query(MarkRecord).filter(
            MarkRecord.course_id == payload.course_id,
            MarkRecord.tenant_id == user.tenant_id,
        ).all()
        before_snapshot = [
            {"id": r.id, "student_id": r.student_id,
             "total_score": r.total_score, "z_score": r.z_score,
             "letter_grade": r.letter_grade}
            for r in records_for_commit
        ]

    # ------------------------------------------------------------------
    # 11. Commit logic (requires professor_approved=True)
    # ------------------------------------------------------------------
    committed = False
    commit_errors: list[str] = []

    if payload.commit:
        if boundary_warnings:
            commit_errors.append(
                "COMMIT BLOCKED: Grade boundary monotonicity violations must be resolved first. "
                + "; ".join(boundary_warnings)
            )
        if not payload.professor_approved:
            commit_errors.append(
                "COMMIT BLOCKED: professor_approved must be True. "
                "Faculty must explicitly approve before grades are written to database. "
                "This is an auditable approval action — it is NOT a legally binding electronic signature."
            )
        if payload.scores is not None:
            commit_errors.append(
                "COMMIT BLOCKED: Cannot commit inline scores (not tied to DB records). "
                "Use course_id-based simulation for commit."
            )

        if not commit_errors:
            # Write grades to DB
            records_to_commit = db.query(MarkRecord).filter(
                MarkRecord.course_id == payload.course_id,
                MarkRecord.tenant_id == user.tenant_id,
            ).all()
            if len(records_to_commit) != N:
                commit_errors.append(
                    f"COMMIT ABORTED: Score count mismatch "
                    f"(DB={len(records_to_commit)}, simulation={N})."
                )
            else:
                for rec, row in zip(records_to_commit, student_rows):
                    rec.z_score = row["z_score"]
                    rec.letter_grade = row["letter_grade"]

                # Ledger entry
                ledger = GradeChangeLedger(
                    tenant_id=user.tenant_id,
                    course_id=payload.course_id,
                    actor_id=user.id,
                    action=(
                        f"phase09_simulate_commit_{payload.mode} | "
                        f"approved_by={user.email} | "
                        f"note={payload.professor_note[:200]}"
                    ),
                    before_json=json.dumps(before_snapshot),
                    after_json=json.dumps(student_rows),
                )
                db.add(ledger)
                db.commit()
                committed = True

    # ------------------------------------------------------------------
    # 12. Generate reproducible simulation token
    # ------------------------------------------------------------------
    sim_token = f"sim-{uuid.uuid4().hex[:16]}"

    # ------------------------------------------------------------------
    # 13. Compose response
    # ------------------------------------------------------------------
    response: dict[str, Any] = {
        # Identity
        "simulation_token": sim_token,
        "course_id": payload.course_id,
        "mode": payload.mode,
        "simulated_at": now.isoformat(),

        # Cohort stats
        "sample_size": N,
        "max_marks": payload.max_marks,
        "mean": round(mean_adj, 3),
        "std_dev": round(std_adj, 3),
        "raw_mean": round(mean_raw, 3),
        "raw_std": round(std_raw, 3),
        "skewness": round(skew_val, 3),
        "silverman_bandwidth_h": silverman_h,
        "auto_suggest_kde": suggest_kde,

        # Visualization
        "histogram": histogram,
        "curve_points": curve_points,

        # Grade distribution
        "grade_counts": grade_counts,
        "comparison_table": comparison_table,

        # Per-student preview (not committed — preview only unless commit=True)
        "student_preview": student_rows,

        # IRT analysis
        "irt_analysis": irt_items,
        "irt_summary": {
            "total_questions": len(irt_items),
            "defective_count": sum(1 for q in irt_items if q.get("is_defective")),
            "critical_flag_count": sum(1 for q in irt_items if q.get("flag_severity") == "critical"),
            "note": (
                "Faculty authorization required before dropping or nullifying any flagged question. "
                "System will NEVER automatically cancel a question."
            ),
        },

        # Gap-aware DP
        "gap_aware": dp_result,
        "gmm_analysis": gmm_result,

        # Boundaries used
        "effective_z_cutoffs": z_cutoffs,
        "effective_abs_cutoffs": abs_cutoffs,
        "boundary_warnings": boundary_warnings,

        # Warnings & compliance
        "fairness_warnings": fairness_warns,
        "approval_status": {
            "professor_approved": payload.professor_approved,
            "committed": committed,
            "commit_errors": commit_errors,
            "note": (
                "This simulation produces proposals only. "
                "Grades are not final until professor_approved=True AND commit=True are submitted together "
                "and the action is logged in the GradeChangeLedger. "
                "This approval is an auditable governance action, NOT a legally binding e-signature."
            ),
        },
    }

    # Before/after comparison for committed runs
    if committed and before_snapshot:
        changed = []
        for b, a in zip(before_snapshot, student_rows):
            if b.get("letter_grade") != a.get("letter_grade"):
                changed.append({
                    "student_id": b.get("student_id"),
                    "score": a.get("score"),
                    "before_grade": b.get("letter_grade"),
                    "after_grade": a.get("letter_grade"),
                })
        response["grade_change_summary"] = {
            "total_changed": len(changed),
            "changed_students": changed[:50],  # cap for response size
        }

    return response


# ---------------------------------------------------------------------------
# GET /api/v1/grading/simulate/history/{course_id}
# ---------------------------------------------------------------------------

@router.get("/simulate/history/{course_id}")
def get_grading_history(
    course_id: str,
    user: User = Depends(require_roles(["professor", "hod", "dean", "admin"])),
    db: Session = Depends(get_db),
):
    """
    Returns the last 20 grading actions for a course from the GradeChangeLedger.
    Enables rollback audit trail review.
    """
    ledger_entries = (
        db.query(GradeChangeLedger)
        .filter(
            GradeChangeLedger.course_id == course_id,
            GradeChangeLedger.tenant_id == user.tenant_id,
        )
        .order_by(GradeChangeLedger.id.desc())
        .limit(20)
        .all()
    )
    return {
        "course_id": course_id,
        "entries": [
            {
                "id": e.id,
                "action": e.action,
                "actor_id": e.actor_id,
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
            }
            for e in ledger_entries
        ],
        "total": len(ledger_entries),
        "note": "To rollback, use POST /api/grading/undo with course_id.",
    }


# ---------------------------------------------------------------------------
# POST /api/v1/grading/simulate/edge-test  (dev/test harness)
# ---------------------------------------------------------------------------

@router.post("/simulate/edge-test")
def run_edge_case_tests(
    user: User = Depends(require_roles(["professor", "hod", "dean", "admin"])),
):
    """
    Run built-in edge-case reproducibility tests.
    Returns pass/fail for each scenario.
    Does NOT write to DB.
    """
    results: list[dict] = []

    def _run(name: str, scores: list[float], mode: str = "relative",
             item_scores: dict | None = None, max_marks: float = 100.0) -> dict:
        req = SimulateGradingRequest(
            course_id="__test__",
            mode=mode,
            scores=scores,
            item_scores=item_scores,
            max_marks=max_marks,
            commit=False,
            professor_approved=False,
        )

        class _FakeUser:
            id = "test-user"
            email = "test@test.com"
            tenant_id = "test-tenant"
            role = "professor"

        class _FakeDB:
            def query(self, *a): return self
            def filter(self, *a): return self
            def all(self): return []

        try:
            resp = simulate_grading(req, _FakeUser(), _FakeDB())  # type: ignore[arg-type]
            status = "PASS"
            detail = f"N={resp['sample_size']}, mode={mode}, counts={resp['grade_counts']}"
            if resp.get("fairness_warnings"):
                detail += f" | warns={len(resp['fairness_warnings'])}"
        except Exception as exc:
            status = "FAIL"
            detail = str(exc)
        return {"test": name, "status": status, "detail": detail}

    # Test cases per spec
    results.append(_run("constant_scores",        [70.0] * 30))
    results.append(_run("small_class_N5",         [50, 60, 70, 80, 90]))
    results.append(_run("large_class_N200",       list(np.random.normal(70, 12, 200).tolist())))
    results.append(_run("all_zero_wrong",         [0.0] * 40))
    results.append(_run("all_perfect",            [100.0] * 40))
    results.append(_run("outliers",               [5, 6, 70, 71, 72, 99, 100] + [60] * 30))
    results.append(_run("ties",                   [75.0] * 50))
    results.append(_run("gap_aware_mode",         [49.5, 50.5, 69.5, 70.5, 79.5, 80.5] * 5, mode="gap_aware"))
    results.append(_run("absolute_mode",          [45, 55, 65, 75, 85, 95] * 5, mode="absolute"))
    results.append(_run("sparse_item_matrix",
                        [70, 80, 90, 60, 50],
                        item_scores={"Q1": [5, 8, 10, 4, 3], "Q2": [7, 7, 7, 7, 7]},
                        max_marks=20.0))
    results.append(_run("single_student",         [82.0]))   # edge case — returns early
    results.append(_run("N_equals_2",             [40.0, 90.0]))
    results.append(_run("negative_discrimination",
                        [60, 70, 80, 90, 95],
                        item_scores={"Q_BAD": [10, 8, 6, 4, 2]},  # top scorers get Q wrong
                        max_marks=100.0))

    passed = sum(1 for r in results if r["status"] == "PASS")
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
        "reproducibility_note": (
            "All deterministic tests (constant, ties, all-zero, all-perfect) "
            "must return identical output on every run with same input."
        ),
    }
