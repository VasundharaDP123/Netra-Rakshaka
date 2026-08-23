"""
Health report generation from REAL recorded telemetry.

Everything in a generated report is read from the SQLite database - the rows
that stream_data() wrote while real hardware was connected. Nothing here reads
the simulator, and nothing is invented: if a metric has no data behind it, the
report says so rather than printing a plausible-looking number.

Two things are produced from the same collected data:
  collect_report_data()  -> the facts, also handed to the AI advisor
  build_pdf()            -> a downloadable PDF of those facts
"""

import io
import time
import sqlite3
from datetime import datetime

from database import get_connection

# Clinical reference values, the same ones the classifier uses. Kept here too so
# the report can say how far the user sits from the guidance, not just report raw
# numbers a reader would have to interpret themselves.
REF = {
    "blink_rate_min": 12,      # relaxed humans blink ~15/min; below 12 is suppressed
    "blink_critical": 8,
    "distance_min_cm": 40,     # optometry guidance is 40-50 cm
    "distance_critical_cm": 35,
    "tilt_max_deg": 25,
    "tilt_critical_deg": 30,
    "lux_min": 80,
}


def _window_bounds(days):
    """Return (cutoff_string, label) for the requested window."""
    seconds = days * 86400
    cutoff = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() - seconds))
    label = "Last 24 hours" if days == 1 else f"Last {days} days"
    return cutoff, label


def collect_report_data(days=1):
    """
    Read the real recorded session data for the window.

    Returns a dict of plain facts. `has_data` is False when nothing was recorded
    in the window - callers must handle that rather than printing zeros as if
    they were measurements.
    """
    cutoff, label = _window_bounds(days)
    out = {
        "window_label": label,
        "window_days": days,
        "generated_at": datetime.now().strftime('%d %B %Y, %I:%M %p'),
        "has_data": False,
    }

    # app.py records a row of zeros whenever no device is connected, so the
    # dashboard shows "disconnected" instead of stale values. Those rows are not
    # measurements and must never reach a clinical report - averaged in, they
    # drag every figure toward zero and would produce nonsense like a "0 cm
    # closest distance". A row counts as a real reading only if at least one
    # sensor actually returned something.
    CONNECTED = ("(screen_distance_cm > 0 OR blink_rate > 0 OR "
                 " ambient_lux > 0 OR eye_temp_celsius > 0)")

    try:
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("""
            SELECT COUNT(*)                                            AS samples,
                   MIN(timestamp)                                      AS first_seen,
                   MAX(timestamp)                                      AS last_seen,
                   AVG(blink_rate)                                     AS avg_bpm,
                   MIN(blink_rate)                                     AS min_bpm,
                   AVG(screen_distance_cm)                             AS avg_dist,
                   MIN(NULLIF(screen_distance_cm,0))                   AS min_dist,
                   AVG(head_tilt_degrees)                              AS avg_tilt,
                   MAX(head_tilt_degrees)                              AS max_tilt,
                   AVG(ambient_lux)                                    AS avg_lux,
                   AVG(eye_temp_celsius)                               AS avg_temp,
                   SUM(CASE WHEN strain_level='Safe'     THEN 1 ELSE 0 END) AS safe_n,
                   SUM(CASE WHEN strain_level='Moderate' THEN 1 ELSE 0 END) AS mod_n,
                   SUM(CASE WHEN strain_level='Critical' THEN 1 ELSE 0 END) AS crit_n,
                   SUM(CASE WHEN blink_rate       < ? THEN 1 ELSE 0 END)    AS low_blink_n,
                   SUM(CASE WHEN screen_distance_cm > 0
                             AND screen_distance_cm < ? THEN 1 ELSE 0 END)  AS close_n,
                   SUM(CASE WHEN head_tilt_degrees  > ? THEN 1 ELSE 0 END)  AS tilt_n,
                   SUM(CASE WHEN ambient_lux > 0
                             AND ambient_lux < ? THEN 1 ELSE 0 END)         AS dark_n
            FROM telemetry_history
            WHERE timestamp >= ? AND """ + CONNECTED + """
        """, (REF["blink_rate_min"], REF["distance_min_cm"],
              REF["tilt_max_deg"], REF["lux_min"], cutoff))
        t = cur.fetchone()

        samples = (t["samples"] or 0) if t else 0
        if samples == 0:
            conn.close()
            return out

        cur.execute("SELECT COUNT(*) AS n FROM break_events WHERE timestamp >= ?", (cutoff,))
        breaks = cur.fetchone()["n"] or 0

        # Compliance: did the user actually complete the rests they were given?
        cur.execute("""
            SELECT action, COUNT(*) AS n
            FROM compliance_log WHERE timestamp >= ? GROUP BY action
        """, (cutoff,))
        comp = {r["action"]: r["n"] for r in cur.fetchall()}

        cur.execute("""
            SELECT COUNT(*) AS n, COALESCE(SUM(duration_min),0) AS mins
            FROM deep_work_sessions WHERE timestamp >= ?
        """, (cutoff,))
        dw = cur.fetchone()

        # Busiest hours - where the strain actually concentrated.
        cur.execute("""
            SELECT strftime('%H', timestamp) AS hr,
                   COUNT(*) AS n,
                   SUM(CASE WHEN strain_level='Critical' THEN 1 ELSE 0 END) AS crit
            FROM telemetry_history
            WHERE timestamp >= ? AND """ + CONNECTED + """
            GROUP BY hr HAVING n > 30 ORDER BY crit DESC LIMIT 3
        """, (cutoff,))
        peak = [{"hour": f"{int(r['hr']):02d}:00",
                 "critical_pct": round((r["crit"] / r["n"]) * 100, 1)}
                for r in cur.fetchall() if r["n"]]

        conn.close()

        def pct(n):
            return round(((n or 0) / samples) * 100, 1)

        complied = comp.get("complied", 0)
        skipped = comp.get("skipped", 0)
        ignored = comp.get("ignored", 0)
        prompts = complied + skipped + ignored

        out.update({
            "has_data": True,
            "samples": samples,
            "first_seen": t["first_seen"],
            "last_seen": t["last_seen"],
            # stream_data() emits every 200 ms, so 5 samples is one second.
            "screen_time_min": round(samples * 0.2 / 60, 1),
            "avg_bpm": round(t["avg_bpm"] or 0, 1),
            "min_bpm": int(t["min_bpm"] or 0),
            "avg_distance_cm": round(t["avg_dist"] or 0, 1),
            "min_distance_cm": int(t["min_dist"] or 0),
            "avg_tilt_deg": round(t["avg_tilt"] or 0, 1),
            "max_tilt_deg": int(t["max_tilt"] or 0),
            "avg_lux": int(t["avg_lux"] or 0),
            "avg_temp_c": round(t["avg_temp"] or 0, 1),
            "safe_pct": pct(t["safe_n"]),
            "moderate_pct": pct(t["mod_n"]),
            "critical_pct": pct(t["crit_n"]),
            "time_low_blink_pct": pct(t["low_blink_n"]),
            "time_too_close_pct": pct(t["close_n"]),
            "time_bad_posture_pct": pct(t["tilt_n"]),
            "time_dim_room_pct": pct(t["dark_n"]),
            "breaks_enforced": breaks,
            "rests_prompted": prompts,
            "rests_complied": complied,
            "rests_skipped": skipped,
            "rests_ignored": ignored,
            "compliance_pct": round((complied / prompts) * 100, 1) if prompts else None,
            "deep_work_sessions": dw["n"] or 0,
            "deep_work_minutes": dw["mins"] or 0,
            "peak_strain_hours": peak,
        })
        return out

    except Exception as e:
        print(f"[REPORT] Could not collect report data: {e}")
        out["error"] = str(e)
        return out


def rule_based_findings(d):
    """
    Deterministic findings straight from the recorded numbers.

    This is the fallback when the AI advisor is unavailable, and it is also what
    keeps the report honest: every line here is tied to a measured value, so the
    report still says something true even with no network.
    """
    if not d.get("has_data"):
        return []

    f = []
    if d["avg_bpm"] and d["avg_bpm"] < REF["blink_critical"]:
        f.append(("Severely suppressed blinking",
                  f"Average blink rate was {d['avg_bpm']}/min against a healthy 15/min. "
                  f"Blinking was below normal for {d['time_low_blink_pct']}% of the session. "
                  "This is the strongest driver of tear-film breakup and dry eye."))
    elif d["avg_bpm"] and d["avg_bpm"] < REF["blink_rate_min"]:
        f.append(("Reduced blinking",
                  f"Average blink rate was {d['avg_bpm']}/min, below the healthy 15/min. "
                  f"Blinking was suppressed for {d['time_low_blink_pct']}% of the session."))

    if d["avg_distance_cm"] and d["avg_distance_cm"] < REF["distance_critical_cm"]:
        f.append(("Viewing distance too close",
                  f"Average distance was {d['avg_distance_cm']} cm, below the 40-50 cm "
                  f"guidance, and closest recorded was {d['min_distance_cm']} cm. Sustained "
                  "near work keeps the ciliary muscle contracted and is a myopia risk factor."))
    elif d["time_too_close_pct"] > 25:
        f.append(("Frequently sitting too close",
                  f"{d['time_too_close_pct']}% of the session was spent closer than "
                  f"{REF['distance_min_cm']} cm."))

    if d["time_bad_posture_pct"] > 25:
        f.append(("Forward head posture",
                  f"Head tilt exceeded {REF['tilt_max_deg']} degrees for "
                  f"{d['time_bad_posture_pct']}% of the session, peaking at "
                  f"{d['max_tilt_deg']} degrees. This loads the cervical spine."))

    if d["time_dim_room_pct"] > 20:
        f.append(("Working in low light",
                  f"Ambient light was below {REF['lux_min']} lux for "
                  f"{d['time_dim_room_pct']}% of the session. A bright screen in a dim "
                  "room forces repeated pupil adjustment."))

    if d["compliance_pct"] is not None and d["compliance_pct"] < 60 and d["rests_prompted"] >= 3:
        f.append(("Low rest compliance",
                  f"Only {d['compliance_pct']}% of prompted rests were completed "
                  f"({d['rests_complied']} of {d['rests_prompted']}). The intervention is "
                  "firing correctly but is not being followed."))

    if d["critical_pct"] > 20:
        f.append(("High proportion of critical strain",
                  f"{d['critical_pct']}% of all recorded samples were classified Critical."))

    if not f:
        f.append(("No significant issues detected",
                  "All measured values stayed within the clinical reference ranges for "
                  "this session."))
    return f


# Language models write typographic Unicode - non-breaking hyphens, smart
# quotes, maths symbols. The PDF base fonts have no glyphs for these and
# reportlab draws a black box for each one, which looks broken in a report a
# user may show a doctor. Fold them to plain ASCII before they reach the page.
_ASCII_FOLD = {
    '‐': '-', '‑': '-', '‒': '-', '–': '-', '—': '-',
    '―': '-', '−': '-', '­': '-',
    '‘': "'", '’': "'", '‚': "'", '‛': "'",
    '“': '"', '”': '"', '„': '"',
    '…': '...', '•': '-', '·': '-',
    ' ': ' ', ' ': ' ', ' ': ' ', ' ': ' ', '​': '',
    '≤': '<=', '≥': '>=', '≠': '!=', '×': 'x',
    '→': '->', '′': "'", '″': '"', '≈': '~',
}


def _ascii_safe(text):
    if not text:
        return text
    for bad, good in _ASCII_FOLD.items():
        text = text.replace(bad, good)
    # Anything still outside Latin-1 has no glyph either - drop it rather than
    # printing a box. Degree signs and accents survive; exotic symbols do not.
    return ''.join(c if ord(c) < 256 else '' for c in text)


# ── PDF ────────────────────────────────────────────────────────────────────
def build_pdf(data, findings=None, ai_text=None, ai_source=None):
    """
    Render the collected data to a PDF and return it as bytes.

    findings  - list of (title, detail) from rule_based_findings()
    ai_text   - optional AI-written recommendations
    ai_source - label describing where ai_text came from, printed for honesty
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle, HRFlowable)

    INK = colors.HexColor('#0f191f')
    MUTED = colors.HexColor('#5d6e78')
    ACCENT = colors.HexColor('#1b4fa0')
    LINE = colors.HexColor('#d9e1e6')
    CRIT = colors.HexColor('#a93227')

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title="Netra-Rakshaka Eye Health Report")

    ss = getSampleStyleSheet()
    h1 = ParagraphStyle('h1', parent=ss['Title'], fontName='Helvetica-Bold',
                        fontSize=20, textColor=INK, alignment=TA_LEFT, spaceAfter=2)
    sub = ParagraphStyle('sub', parent=ss['Normal'], fontSize=9.5, textColor=MUTED, spaceAfter=10)
    h2 = ParagraphStyle('h2', parent=ss['Heading2'], fontName='Helvetica-Bold',
                        fontSize=12.5, textColor=INK, spaceBefore=14, spaceAfter=6)
    body = ParagraphStyle('body', parent=ss['Normal'], fontSize=9.5, leading=13.5, textColor=INK)
    small = ParagraphStyle('small', parent=ss['Normal'], fontSize=8.2, leading=11, textColor=MUTED)
    ftitle = ParagraphStyle('ftitle', parent=body, fontName='Helvetica-Bold', textColor=CRIT)

    el = []
    el.append(Paragraph("Eye Health &amp; Behaviour Report", h1))
    el.append(Paragraph(
        f"Netra-Rakshaka &nbsp;·&nbsp; {data['window_label']} &nbsp;·&nbsp; "
        f"generated {data['generated_at']}", sub))
    el.append(HRFlowable(width="100%", thickness=1.2, color=INK, spaceAfter=10))

    if not data.get("has_data"):
        el.append(Paragraph("No recorded data in this period", h2))
        el.append(Paragraph(
            "No sensor readings were stored for this window, so no report can be produced. "
            "Connect the spectacles and record a session, then generate the report again. "
            "No values are shown here because none were measured.", body))
        doc.build(el)
        return buf.getvalue()

    # ── measured session ──
    el.append(Paragraph("Session measured", h2))
    el.append(Paragraph(
        f"{data['samples']:,} readings recorded from "
        f"{data['first_seen']} to {data['last_seen']}, "
        f"about {data['screen_time_min']:.0f} minutes of monitored screen time. "
        "All figures below are measured values from these readings.", body))

    def row(metric, value, reference, flag=False):
        v = Paragraph(f"<b>{value}</b>" if flag else value, body)
        return [Paragraph(metric, body), v, Paragraph(reference, small)]

    tbl = [[Paragraph("<b>Metric</b>", body),
            Paragraph("<b>Measured</b>", body),
            Paragraph("<b>Clinical reference</b>", body)],
           row("Average blink rate", f"{data['avg_bpm']} / min", "15 / min healthy",
               data['avg_bpm'] < REF['blink_rate_min']),
           row("Lowest blink rate", f"{data['min_bpm']} / min", "below 8 is critical",
               data['min_bpm'] < REF['blink_critical']),
           row("Average screen distance", f"{data['avg_distance_cm']} cm", "40-50 cm",
               data['avg_distance_cm'] < REF['distance_min_cm']),
           row("Closest recorded", f"{data['min_distance_cm']} cm", "below 35 cm is critical",
               data['min_distance_cm'] < REF['distance_critical_cm']),
           row("Average head tilt", f"{data['avg_tilt_deg']}°", "under 25°",
               data['avg_tilt_deg'] > REF['tilt_max_deg']),
           row("Maximum head tilt", f"{data['max_tilt_deg']}°", "over 30° loads the spine",
               data['max_tilt_deg'] > REF['tilt_critical_deg']),
           row("Average room light", f"{data['avg_lux']} lux", "above 80 lux",
               data['avg_lux'] < REF['lux_min']),
           row("Average temperature", f"{data['avg_temp_c']} °C", "ambient"),
           ]
    t = Table(tbl, colWidths=[62 * mm, 45 * mm, 67 * mm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#edf2f4')),
        ('LINEBELOW', (0, 0), (-1, -1), 0.4, LINE),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    el.append(Spacer(1, 6))
    el.append(t)

    # ── strain + behaviour ──
    el.append(Paragraph("Strain distribution", h2))
    el.append(Paragraph(
        f"Safe {data['safe_pct']}% &nbsp;·&nbsp; Moderate {data['moderate_pct']}% "
        f"&nbsp;·&nbsp; Critical {data['critical_pct']}%", body))
    el.append(Spacer(1, 4))
    el.append(Paragraph(
        f"Time spent below healthy blink rate: {data['time_low_blink_pct']}% &nbsp;·&nbsp; "
        f"too close to the screen: {data['time_too_close_pct']}% &nbsp;·&nbsp; "
        f"poor head posture: {data['time_bad_posture_pct']}% &nbsp;·&nbsp; "
        f"low room light: {data['time_dim_room_pct']}%", body))

    if data.get("peak_strain_hours"):
        hrs = ", ".join(f"{h['hour']} ({h['critical_pct']}% critical)"
                        for h in data["peak_strain_hours"])
        el.append(Spacer(1, 4))
        el.append(Paragraph(f"Highest strain occurred around: {hrs}", body))

    el.append(Paragraph("Breaks and compliance", h2))
    if data["rests_prompted"]:
        el.append(Paragraph(
            f"{data['breaks_enforced']} enforced breaks were triggered. Of "
            f"{data['rests_prompted']} rest prompts, {data['rests_complied']} were completed, "
            f"{data['rests_skipped']} skipped and {data['rests_ignored']} ignored "
            f"- a compliance rate of {data['compliance_pct']}%.", body))
    else:
        el.append(Paragraph(
            f"{data['breaks_enforced']} enforced breaks were triggered. No rest prompts were "
            "responded to in this period, so no compliance rate can be calculated.", body))
    if data["deep_work_sessions"]:
        el.append(Spacer(1, 4))
        el.append(Paragraph(
            f"{data['deep_work_sessions']} Deep Work sessions totalling "
            f"{data['deep_work_minutes']} minutes, during which breaks were held back.", body))

    # ── findings ──
    el.append(Paragraph("Findings from this session", h2))
    for title, detail in (findings or []):
        el.append(Paragraph(_ascii_safe(title), ftitle))
        el.append(Paragraph(_ascii_safe(detail), body))
        el.append(Spacer(1, 6))

    # ── AI recommendations ──
    if ai_text:
        el.append(Paragraph("Personalised recommendations", h2))
        el.append(Paragraph(_ascii_safe(ai_text).replace('\n', '<br/>'), body))
        el.append(Spacer(1, 5))
        el.append(Paragraph(f"Generated by {ai_source} from the measured values above.", small))

    el.append(Spacer(1, 14))
    el.append(HRFlowable(width="100%", thickness=0.7, color=LINE, spaceAfter=6))
    el.append(Paragraph(
        "Netra-Rakshaka is a preventive wellness tool, not a diagnostic medical device. "
        "It does not diagnose any condition. Consult a qualified optometrist for medical advice. "
        "All values in this report are measured from recorded sensor data.", small))

    doc.build(el)
    return buf.getvalue()
