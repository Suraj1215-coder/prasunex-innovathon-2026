import { NextRequest, NextResponse } from "next/server";

// In-memory data store for live demo interaction
const MOCK_TICKETS = [
  {
    id: "TCK-101",
    ticket_number: 101,
    title: "Projector lamp burnt out in Seminar Hall 3",
    description: "During morning Operating Systems lecture, the ceiling projector displayed high temp warning and shut down.",
    location: "Academic Block 2, Floor 3, Room 304",
    category: "Electrical",
    status: "In Progress",
    priority: "High",
    assigned_to: "Sunil Verma (Lab Warden)",
    created_at: new Date(Date.now() - 3600000 * 2).toISOString(),
    updated_at: new Date().toISOString(),
    sla_countdown: "3h 45m",
    sla_hours_remaining: 3.75,
    duplicate_count: 2,
    upvotes: 8,
  },
  {
    id: "TCK-102",
    ticket_number: 102,
    title: "Wi-Fi Access Point 5GHz band dropping connections",
    description: "AP-CS-B2 repeatedly reboots when student concurrency exceeds 40 devices.",
    location: "Hostel Block B, 2nd Floor Common Room",
    category: "Networking",
    status: "Open",
    priority: "Urgent",
    assigned_to: "Ramesh Kumar (Network Admin)",
    created_at: new Date(Date.now() - 3600000 * 5).toISOString(),
    updated_at: new Date().toISOString(),
    sla_countdown: "1h 15m",
    sla_hours_remaining: 1.25,
    duplicate_count: 5,
    upvotes: 14,
  },
  {
    id: "TCK-103",
    ticket_number: 103,
    title: "Air conditioning leak near server rack #2",
    description: "Condensation water dripping close to edge router UPS battery tray.",
    location: "Main Data Center, Basement Floor",
    category: "HVAC",
    status: "Resolved",
    priority: "Critical",
    assigned_to: "Facilities Team",
    created_at: new Date(Date.now() - 3600000 * 24).toISOString(),
    updated_at: new Date().toISOString(),
    sla_countdown: "Resolved",
    sla_hours_remaining: 0,
    duplicate_count: 1,
    upvotes: 4,
  },
];

const MOCK_COURSES = [
  { id: "cs201", code: "CS201", title: "Data Structures & Algorithms", department: "CSE", semester: 3, enrolled: 68, avgScore: 78.4, passRate: 92 },
  { id: "cs301", code: "CS301", title: "Operating Systems", department: "CSE", semester: 5, enrolled: 62, avgScore: 72.1, passRate: 88 },
  { id: "cs401", code: "CS401", title: "Machine Learning & AI", department: "CSE", semester: 7, enrolled: 54, avgScore: 81.6, passRate: 96 },
];

const MOCK_STUDENTS = [
  { id: "usr-012", name: "Rohan Patel", roll_number: "21CS044", department: "CSE", semester: 5, attendance: 68.2, cgpa: 7.4, at_risk: true, risk_reason: "Attendance below 75%" },
  { id: "usr-045", name: "Simran Kaur", roll_number: "21CS089", department: "CSE", semester: 5, attendance: 71.5, cgpa: 7.8, at_risk: true, risk_reason: "Low quiz score in Semaphores" },
  { id: "usr-078", name: "Aditya Verma", roll_number: "21CS112", department: "CSE", semester: 5, attendance: 64.0, cgpa: 6.9, at_risk: true, risk_reason: "Consecutive absences in Lab" },
  { id: "usr-092", name: "Neha Joshi", roll_number: "21CS135", department: "CSE", semester: 5, attendance: 73.0, cgpa: 8.1, at_risk: true, risk_reason: "Virtual Memory diagnostic falling" },
  { id: "usr-001", name: "Aarav Sharma", roll_number: "21CS001", department: "CSE", semester: 5, attendance: 91.4, cgpa: 9.4, at_risk: false, risk_reason: "None" },
];

const MOCK_EARLY_WARNINGS = [
  { id: "w-1", student_id: "usr-012", student_name: "Rohan Patel", roll_number: "21CS044", course_code: "CS201", attendance: 68.2, predicted_attendance: 71.4, status: "Critical", recommended_action: "Issue Dean Notice & Remedial Sessions" },
  { id: "w-2", student_id: "usr-045", student_name: "Simran Kaur", roll_number: "21CS089", course_code: "CS301", attendance: 71.5, predicted_attendance: 73.8, status: "Warning", recommended_action: "Pair with Peer Tutor for Semaphores" },
  { id: "w-3", student_id: "usr-078", student_name: "Aditya Verma", roll_number: "21CS112", course_code: "CS201", attendance: 64.0, predicted_attendance: 68.0, status: "Critical", recommended_action: "Counseling & HoD Review" },
  { id: "w-4", student_id: "usr-092", student_name: "Neha Joshi", roll_number: "21CS135", course_code: "CS401", attendance: 73.0, predicted_attendance: 74.5, status: "Warning", recommended_action: "Assign Micro-Drill Modules" },
];

async function tryProxyToFastAPI(req: NextRequest, path: string, method: string) {
  try {
    const urlsToTry = [
      `http://127.0.0.1:8000/api/${path}`,
      `http://127.0.0.1:8000/api/v1/${path}`,
      `http://127.0.0.1:8000/${path}`,
    ];
    if (path.startsWith("v1/")) {
      urlsToTry.unshift(`http://127.0.0.1:8000/api/${path}`);
    }

    let body: string | undefined = undefined;
    if (method === "POST" || method === "PUT" || method === "PATCH") {
      try {
        body = await req.clone().text();
      } catch {}
    }
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const auth = req.headers.get("authorization");
    if (auth) headers["authorization"] = auth;

    for (const backendUrl of urlsToTry) {
      try {
        const res = await fetch(backendUrl, {
          method,
          headers,
          body,
          signal: AbortSignal.timeout(5000),
        });
        if (res.ok) {
          const data = await res.json();
          return NextResponse.json(data);
        }
      } catch {}
    }
  } catch {}
  return null;
}

export async function GET(req: NextRequest, { params }: { params: { slug: string[] } }) {
  const path = (params.slug || []).join("/");

  // Try real FastAPI backend on port 8000 first
  const proxied = await tryProxyToFastAPI(req, path, "GET");
  if (proxied) return proxied;

  if (path === "dashboard/overview") {
    return NextResponse.json({
      enrolled_students: 200,
      active_courses: 14,
      open_tickets: 3,
      at_risk_count: 4,
      average_attendance: 87.8,
      nba_attainment_pct: 88.9,
      verified_audit_events: 1089,
    });
  }

  if (path === "facilities/tickets") {
    return NextResponse.json(MOCK_TICKETS);
  }

  if (path === "grading/courses") {
    return NextResponse.json(MOCK_COURSES);
  }

  if (path === "students") {
    return NextResponse.json(MOCK_STUDENTS);
  }

  if (path === "attendance/early-warning") {
    return NextResponse.json(MOCK_EARLY_WARNINGS);
  }

  if (path === "accreditation/outcomes") {
    return NextResponse.json([
      { co_code: "CO1", description: "Analyze complexity of graph & tree algorithms", target: 70, attained: 84.5, status: "Attained" },
      { co_code: "CO2", description: "Design multi-threaded synchronisation primitives", target: 70, attained: 76.2, status: "Attained" },
      { co_code: "CO3", description: "Evaluate deadlock avoidance & detection", target: 70, attained: 68.4, status: "Near Target" },
      { co_code: "CO4", description: "Implement virtual memory paging mechanisms", target: 70, attained: 79.1, status: "Attained" },
    ]);
  }

  if (path === "admin/billing") {
    return NextResponse.json({
      active_tier: "autonomous_enterprise",
      tier_name: "Autonomous Enterprise",
      students_count: 200,
      price_per_student: 28,
      arr_usd: 5600,
      status: "Active",
      next_renewal: "2027-11-15",
    });
  }

  if (path === "attendance/me") {
    return NextResponse.json({
      overall_pct: 86.4,
      status: "Good Standing (>75%)",
      courses: [
        { code: "CS201", title: "Data Structures", attended: 26, total: 30, pct: 86.7 },
        { code: "CS301", title: "Operating Systems", attended: 23, total: 28, pct: 82.1 },
        { code: "CS401", title: "Machine Learning", attended: 21, total: 24, pct: 87.5 },
      ],
      safe_bunks_remaining: 3,
    });
  }

  if (path === "knowledge/me" || path.startsWith("v1/analytics/dkt/")) {
    return NextResponse.json({
      student_id: "usr-student-aarav",
      student_name: "Aarav Sharma",
      overall_mastery_pct: 72.0,
      risk_level: "Low",
      dropout_probability: 0.08,
      concept_mastery: {
        "Database Systems (DBMS)": 92.0,
        "Operating Systems (OS)": 78.5,
        "Computer Networks (CN)": 64.0,
        "Data Structures & Algorithms": 41.2,
      },
      prerequisite_blockers: [
        {
          concept_id: "DSA-04",
          concept_name: "Data Structures & Algorithms",
          mastery_pct: 41.2,
          blocker_severity: "Critical",
          root_cause: "Prerequisite gap: Pointer arithmetic and dynamic memory allocation prevents Dynamic Programming consolidation.",
        },
      ],
      intervention_recommended: "Assign 15-minute recursive micro-drill and pair with High-Mastery peer in DSA.",
    });
  }

  if (path === "admin/equipment/forecast" || path.startsWith("v1/admin/equipment/forecast")) {
    return NextResponse.json({
      tenant_id: "nitx",
      assets_monitored: 5,
      critical_count: 1,
      warning_count: 1,
      ranked_forecast: [
        {
          asset_tag: "HST-WTR-04",
          name: "Hostel 4 Water Chiller Pump",
          location: "Hostel Block 4",
          age_days: 710,
          failure_prob_14d_pct: 38.4,
          risk_tier: "CRITICAL",
          recommendation: "Immediate preventative maintenance: Replace worn impeller before midterms.",
        },
        {
          asset_tag: "EE-DSO-014",
          name: "Digital Storage Oscilloscope Lab 3",
          location: "ECE Lab 3",
          age_days: 620,
          failure_prob_14d_pct: 22.1,
          risk_tier: "WARNING",
          recommendation: "Schedule routine diagnostic check within 7 days.",
        },
        {
          asset_tag: "IT-AHU-01",
          name: "Air Handling Unit Server Room",
          location: "Data Center",
          age_days: 450,
          failure_prob_14d_pct: 8.6,
          risk_tier: "NOMINAL",
          recommendation: "Operating within nominal MTBF parameters.",
        },
      ],
    });
  }

  if (path === "peer-matcher/me" || path === "peer-matcher/my-pair") {
    return NextResponse.json([
      { id: "peer-1", name: "Ananya Iyer", roll: "21CS015", strength: "Semaphores & Concurrency", wants_help_with: "Virtual Memory", match_score: 94 },
      { id: "peer-2", name: "Devansh Mehta", roll: "21CS032", strength: "Deadlock Detection", wants_help_with: "Graph Algorithms", match_score: 89 },
    ]);
  }

  if (path === "opportunities" || path === "opportunities/events") {
    return NextResponse.json([
      { id: "opp-1", title: "Google Summer of Code 2026 Mentorship", company: "Google Open Source", stipend: "$3,000 - $6,000", deadline: "Dec 15, 2026", type: "Fellowship" },
      { id: "opp-2", title: "Systems Engineer Intern (Linux Kernel)", company: "Red Hat", stipend: "₹65,000 / month", deadline: "Nov 30, 2026", type: "Internship" },
      { id: "opp-3", title: "Prasunex Innovathon National Finals", company: "Prasunex AI", stipend: "₹2,50,000 Prize Pool", deadline: "Jan 10, 2027", type: "Hackathon" },
    ]);
  }

  if (path === "study-plan" || path === "v1/student/study-plan") {
    return NextResponse.json({
      student_id: "usr-student-aarav",
      total_hours_allocated: 14.0,
      hours_by_topic: {
        "Data Structures - Dynamic Programming": 5.5,
        "Operating Systems - Virtual Memory": 4.0,
        "Computer Networks - TCP Congestion": 2.5,
        "Database Systems - Transaction ACID": 2.0,
      },
      weekly_schedule: [
        { day: "Monday", topic: "Data Structures - Dynamic Programming", allocated_hours: 3.0, activity: "Greedy Knapsack & DP Memoization", target_mastery_gain: "+14.2%" },
        { day: "Tuesday", topic: "Operating Systems - Virtual Memory", allocated_hours: 2.5, activity: "Inverted Page Tables & TLB", target_mastery_gain: "+10.5%" },
        { day: "Wednesday", topic: "Computer Networks - TCP Congestion", allocated_hours: 2.5, activity: "Slow Start & Reno vs Vegas", target_mastery_gain: "+8.9%" },
        { day: "Thursday", topic: "Database Systems - Transaction ACID", allocated_hours: 2.0, activity: "Two-Phase Locking (2PL)", target_mastery_gain: "+6.5%" },
        { day: "Friday", topic: "FSRS Diagnostic Spaced Repetition", allocated_hours: 4.0, activity: "3 Review Flashcards + Quiz", target_mastery_gain: "+11.4%" },
      ],
      projected_weekly_mastery_boost: "+11.4% overall mastery",
    });
  }

  if (path === "micro-drills") {
    return NextResponse.json([
      {
        id: "d-1",
        question: "In Banker's Algorithm, which condition must hold for a state to be declared safe?",
        options: [
          "All resources are currently unallocated",
          "A safe sequence exists where every process can complete with available + released resources",
          "Deadlock detection is triggered only once",
          "Mutual exclusion constraint is globally disabled",
        ],
        correct_index: 1,
        explanation: "A state is defined as safe if there exists a sequence <P1, P2, ... Pn> such that for each Pi, the resources that Pi can still request can be satisfied by currently available resources plus resources held by all Pj (j < i).",
      },
      {
        id: "d-2",
        question: "Why does a B+ Tree offer superior range query performance compared to a standard B-Tree?",
        options: [
          "It uses binary search inside each leaf node",
          "Leaf nodes are linked sequentially with doubly-linked pointers, eliminating tree re-traversals",
          "It stores data records inside internal root nodes",
          "It prohibits key insertions once full",
        ],
        correct_index: 1,
        explanation: "All leaf nodes in a B+ Tree form a continuous linked list. For range queries (e.g., age BETWEEN 20 AND 30), once the lower bound is located in O(log N), subsequent records are read sequentially in O(k) without backtracking up to root.",
      },
    ]);
  }

  if (path === "privacy/reports") {
    return NextResponse.json([
      { id: "REP-9901", tracking_code: "ZK-7741-X9", category: "Academic Rigor", summary: "Biased grading distribution in Section B lab viva", status: "Received by Dean", created_at: "2 days ago" },
    ]);
  }

  return NextResponse.json({ path, success: true, message: "OK" });
}

export async function POST(req: NextRequest, { params }: { params: { slug: string[] } }) {
  const path = (params.slug || []).join("/");

  // Try real FastAPI backend on port 8000 first
  const proxied = await tryProxyToFastAPI(req, path, "POST");
  if (proxied) return proxied;

  if (path === "v1/student/what-if") {
    try {
      const body = await req.json();
      const hours = body.study_hours || {};
      const totalH = Object.values(hours).reduce((a: any, b: any) => a + Number(b), 0);
      const deltaGpa = Math.min(2.0, (Number(totalH) * 0.08)).toFixed(2);
      const currentGpa = 7.42;
      const projGpa = (currentGpa + Number(deltaGpa)).toFixed(2);
      return NextResponse.json({
        student_id: body.student_id || "usr-student-aarav",
        current_score: 74.2,
        projected_score: Math.min(96.0, 74.2 + Number(deltaGpa) * 10),
        current_gpa: currentGpa,
        projected_gpa: Number(projGpa),
        delta_gpa: Number(deltaGpa),
        current_letter_grade: "B",
        projected_letter_grade: Number(projGpa) >= 8.5 ? "A+" : (Number(projGpa) >= 8.0 ? "A" : "B+"),
        insight: `Investing ${totalH} hours raises projected semester GPA by +${deltaGpa} points, shifting grade to ${Number(projGpa) >= 8.5 ? "A+" : (Number(projGpa) >= 8.0 ? "A" : "B+")}.`,
      });
    } catch {}
  }

  if (path === "v1/copilot/ask") {
    try {
      const body = await req.json();
      const q = (body.query || "").toLowerCase();
      if (q.includes("b-tree") || q.includes("b+ tree") || q.includes("index")) {
        return NextResponse.json({
          grounded: true,
          answer: "In a B-Tree, keys and data records reside in all nodes. In a B+ Tree, internal nodes store navigation keys exclusively while all actual records are in leaf nodes linked sequentially, delivering optimal O(log N + k) range query performance.",
          citations: [
            "Silberschatz, Korth, Sudarshan. Database System Concepts (7th Ed.), Chapter 14: Indexing, pp. 645-652.",
            "NITX CS-301 Lecture 12: Tree Indexing, Unit 3.",
          ],
          concept_code: "DBMS-03-INDEXING",
        });
      }
      return NextResponse.json({
        grounded: true,
        answer: "According to the official NITX accredited syllabus, this topic covers algorithmic complexity, ACID transaction isolation, and cache coherence tradeoffs.",
        citations: ["NITX Course Pack, Section 3.2."],
        concept_code: "CORE-CURRICULUM",
      });
    } catch {}
  }

  if (path === "facilities/tickets" || path === "v1/grievance/submit") {
    try {
      const body = await req.json();
      if (body.category === "sensitive_zk") {
        return NextResponse.json({
          ticket_type: "sensitive_zk",
          tracking_code: "ZK-" + Math.random().toString(36).substring(2, 8).toUpperCase(),
          status: "Received by Dean",
          anonymous: true,
          message: "Report delivered anonymously to Dean Inbox. Zero user linkability guaranteed by Semaphore ZK protocol.",
        });
      }
      const newTicket = {
        id: `tkt-${Date.now()}`,
        ticket_number: MOCK_TICKETS.length + 101,
        title: body.title || "Facility Issue",
        description: body.description || "",
        location: body.location || "Campus Premises",
        category: body.category || "General",
        status: "Ticket verified",
        priority: "High",
        priority_weight: 2.4,
        deduplicated: false,
        created_at: new Date().toISOString(),
        sla_hours: 48,
        sla_deadline: new Date(Date.now() + 48 * 3600000).toISOString(),
        message: "New work order created and dispatched to maintenance team.",
      };
      return NextResponse.json({ success: true, ticket_id: newTicket.id, deduplicated: false, status: "Ticket verified", message: "Work order created." });
    } catch {
      return NextResponse.json({ success: true, message: "Ticket logged." });
    }
  }

  if (path === "v1/grading/simulate" || path === "grading/simulate") {
    try {
      const body = await req.json().catch(() => ({}));
      const mode = body.mode || "gap_aware";
      const N = 62;
      const mean = 74.2 + (body.mean_shift || 0);
      const std = 11.4 * (body.std_scale || 1);
      // Simulate grade distribution
      const gradeCounts: Record<string,number> = { O: 6, "A+": 14, A: 20, "B+": 12, B: 7, C: 2, F: 1 };
      const compTable = [
        { grade: "O",  actual_count: 6,  actual_share_pct: 9.7,  normal_share_pct: 3.6,  z_cutoff: 1.8 },
        { grade: "A+", actual_count: 14, actual_share_pct: 22.6, normal_share_pct: 7.9,  z_cutoff: 1.2 },
        { grade: "A",  actual_count: 20, actual_share_pct: 32.3, normal_share_pct: 15.9, z_cutoff: 0.6 },
        { grade: "B+", actual_count: 12, actual_share_pct: 19.4, normal_share_pct: 22.6, z_cutoff: 0.0 },
        { grade: "B",  actual_count: 7,  actual_share_pct: 11.3, normal_share_pct: 22.6, z_cutoff: -0.6 },
        { grade: "C",  actual_count: 2,  actual_share_pct: 3.2,  normal_share_pct: 20.7, z_cutoff: -1.5 },
        { grade: "F",  actual_count: 1,  actual_share_pct: 1.6,  normal_share_pct: 6.7,  z_cutoff: null },
      ];
      const irtItems = [
        { q_code: "Q1", max_marks: 10, r_pbis: 0.52, irt_b_difficulty: -0.84, irt_a_discrimination: 1.3, is_defective: false, flag_severity: "none", alert: "Standard healthy discrimination.", pass_rate_pct: 78.2 },
        { q_code: "Q2", max_marks: 10, r_pbis: 0.38, irt_b_difficulty: 0.21, irt_a_discrimination: 0.95, is_defective: false, flag_severity: "none", alert: "Standard healthy discrimination.", pass_rate_pct: 61.4 },
        { q_code: "Q3", max_marks: 10, r_pbis: 0.11, irt_b_difficulty: 1.44, irt_a_discrimination: 0.28, is_defective: true, flag_severity: "warning", alert: "LOW DISCRIMINATION (r_pbis=0.11 < 0.15): Consider dropping or grace marks — faculty authorization required.", pass_rate_pct: 44.7 },
        { q_code: "Q4", max_marks: 10, r_pbis: -0.18, irt_b_difficulty: 0.87, irt_a_discrimination: 0.05, is_defective: true, flag_severity: "critical", alert: "NEGATIVE DISCRIMINATION (r_pbis=-0.18): High-performing students answered WRONG. Ambiguous phrasing or keying error. Faculty MUST review.", pass_rate_pct: 55.1 },
        { q_code: "Q5", max_marks: 10, r_pbis: 0.44, irt_b_difficulty: -0.31, irt_a_discrimination: 1.1, is_defective: false, flag_severity: "none", alert: "Standard healthy discrimination.", pass_rate_pct: 71.9 },
      ];
      const dpResult = mode === "gap_aware" ? {
        adjusted_cutoffs: [91.2, 82.0, 72.5, 63.1, 52.3, 41.0],
        trapped_before: 7, trapped_after: 0, rescued_count: 7,
        moves: [
          { grade: "O",  nominal_cutoff: 91.6, adjusted_cutoff: 91.2, rescued: 2 },
          { grade: "A+", nominal_cutoff: 82.4, adjusted_cutoff: 82.0, rescued: 1 },
          { grade: "A",  nominal_cutoff: 72.9, adjusted_cutoff: 72.5, rescued: 3 },
          { grade: "B+", nominal_cutoff: 63.1, adjusted_cutoff: 63.1, rescued: 0 },
          { grade: "B",  nominal_cutoff: 52.3, adjusted_cutoff: 52.3, rescued: 1 },
          { grade: "C",  nominal_cutoff: 41.0, adjusted_cutoff: 41.0, rescued: 0 },
        ],
      } : null;
      return NextResponse.json({
        simulation_token: `sim-mock-${Date.now().toString(16)}`,
        course_id: body.course_id || "cs301",
        mode,
        simulated_at: new Date().toISOString(),
        sample_size: N,
        max_marks: body.max_marks || 100,
        mean: Math.round(mean * 100) / 100,
        std_dev: Math.round(std * 100) / 100,
        raw_mean: 74.2,
        raw_std: 11.4,
        skewness: -0.34,
        silverman_bandwidth_h: 4.812,
        auto_suggest_kde: false,
        histogram: [
          { bin_start: 40, bin_end: 45, count: 1 }, { bin_start: 45, bin_end: 50, count: 2 },
          { bin_start: 50, bin_end: 55, count: 2 }, { bin_start: 55, bin_end: 60, count: 3 },
          { bin_start: 60, bin_end: 65, count: 5 }, { bin_start: 65, bin_end: 70, count: 8 },
          { bin_start: 70, bin_end: 75, count: 11 },{ bin_start: 75, bin_end: 80, count: 10 },
          { bin_start: 80, bin_end: 85, count: 9 }, { bin_start: 85, bin_end: 90, count: 7 },
          { bin_start: 90, bin_end: 95, count: 3 }, { bin_start: 95, bin_end: 100, count: 1 },
        ],
        grade_counts: gradeCounts,
        comparison_table: compTable,
        student_preview: Array.from({ length: 10 }, (_, i) => ({
          score: Math.round((mean - 15 + i * 3.5) * 10) / 10,
          z_score: Math.round((-1.3 + i * 0.3) * 100) / 100,
          letter_grade: compTable[Math.min(i, compTable.length - 1)].grade,
        })),
        irt_analysis: irtItems,
        irt_summary: {
          total_questions: irtItems.length,
          defective_count: 2,
          critical_flag_count: 1,
          note: "Faculty authorization required before dropping or nullifying any flagged question.",
        },
        gap_aware: dpResult,
        gmm_analysis: {
          k: 2,
          means: [61.4, 82.1],
          stds: [8.2, 7.6],
          weights: [0.42, 0.58],
          bic_scores: { 1: 412.3, 2: 398.7, 3: 405.1 },
          natural_gaps: [71.75],
        },
        effective_z_cutoffs: { O: 1.8, "A+": 1.2, A: 0.6, "B+": 0.0, B: -0.6, C: -1.5 },
        effective_abs_cutoffs: { O: 90, "A+": 80, A: 70, "B+": 60, B: 50, C: 40 },
        boundary_warnings: [],
        fairness_warnings: [
          "ALGORITHMIC DISCLAIMER: Grade boundaries proposed by this system are computational tools. They are NOT guaranteed to be fair solely because an algorithm generated them. Faculty review and governance approval are mandatory.",
        ],
        approval_status: {
          professor_approved: body.professor_approved || false,
          committed: false,
          commit_errors: body.commit && !body.professor_approved
            ? ["COMMIT BLOCKED: professor_approved must be True."]
            : [],
          note: "This simulation produces proposals only. Grades are not final until approved and committed.",
        },
      });
    } catch {
      return NextResponse.json({ error: "Simulation failed" }, { status: 500 });
    }
  }

  if (path === "v1/marks/ingest-photo") {
    return NextResponse.json({
      status: "extracted_with_discrepancy",
      course_id: "northstar-cs301",
      perspective_corrected: true,
      ocr_confidence_mean: 0.964,
      total_rows_detected: 10,
      discrepancies_count: 1,
      audit_notice: "Row 3 arithmetic discrepancy flagged: Q1+Q2+Q3+Q4 != Written Total. Please review before committing.",
    });
  }

  if (path === "v1/paper/audit") {
    return NextResponse.json({
      course_id: "northstar-cs301",
      exam_title: "Mid-Semester Exam 2026",
      total_marks: 50.0,
      higher_order_thinking_pct: 80.0,
      nba_criterion_compliant: true,
      syllabus_coverage_pct: 92.5,
      flagged_questions_count: 1,
      flagged_questions: [
        {
          q_num: 6,
          text: "Identify serialization anomaly under read-committed isolation.",
          r_pbis: -0.24,
          severity: "CRITICAL_DEFECT",
          diagnosis: "Negative point-biserial correlation detected. High-performing students scored LOWER than low-performing students due to ambiguous phrasing.",
        },
      ],
      recommendation: "Drop Q6 (-0.24 r_pbis) and redistribute marks across Q3 and Q4.",
    });
  }

  if (path === "v1/schedule/solve" || path === "schedule/solve") {
    return NextResponse.json({
      status: "Optimal (CP-SAT Solved)",
      total_slots: 4,
      student_clashes: 0,
      solve_time_ms: 38,
      cohort: "CSE & ECE Core Cohort",
      hard_constraints_satisfied: true,
      schedule: [
        { course_code: "CS201", course_name: "Data Structures & Algorithms", slot_id: 1, date: "2026-12-01", time: "09:30 - 12:30", room_id: "LH-101", enrolled: 68 },
        { course_code: "EC201", course_name: "Digital Logic & Design", slot_id: 1, date: "2026-12-01", time: "09:30 - 12:30", room_id: "LH-102", enrolled: 45 },
        { course_code: "CS301", course_name: "Operating Systems", slot_id: 2, date: "2026-12-03", time: "09:30 - 12:30", room_id: "LH-101", enrolled: 62 },
        { course_code: "EC301", course_name: "Signals & Systems", slot_id: 2, date: "2026-12-03", time: "09:30 - 12:30", room_id: "LH-102", enrolled: 40 },
        { course_code: "CS401", course_name: "Machine Learning & AI", slot_id: 3, date: "2026-12-05", time: "09:30 - 12:30", room_id: "LH-101", enrolled: 54 },
        { course_code: "MA201", course_name: "Discrete Mathematics", slot_id: 4, date: "2026-12-08", time: "09:30 - 12:30", room_id: "AUD-1", enrolled: 95 },
      ],
    });
  }

  // Phase 09 — M05/M06 Grading Sandbox simulate (GET history)
  if (path.startsWith("v1/grading/simulate/history/") || path.startsWith("grading/simulate/history/")) {
    const courseId = path.split("/").pop() || "cs301";
    return NextResponse.json({
      course_id: courseId,
      entries: [
        { id: 3, action: "phase09_simulate_commit_gap_aware | approved_by=prof@nitx.demo | note=Reviewed and approved after department meeting", actor_id: "usr-prof-01", timestamp: new Date(Date.now() - 86400000).toISOString() },
        { id: 2, action: "curve_saved_relative", actor_id: "usr-prof-01", timestamp: new Date(Date.now() - 172800000).toISOString() },
        { id: 1, action: "grace_marks_all_2.0", actor_id: "usr-prof-01", timestamp: new Date(Date.now() - 259200000).toISOString() },
      ],
      total: 3,
      note: "To rollback, use POST /api/grading/undo with course_id.",
    });
  }

  return NextResponse.json({ path, success: true, message: "Received" });
}
