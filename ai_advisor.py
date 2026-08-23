"""
AI clinical recommendations from the user's own recorded data.

The advisor is given only measured values - the aggregates report_generator
collected from SQLite. It is never given simulator output, and it is never asked
to invent a number. Because the prompt carries this user's actual figures, two
different users get two different answers; the same user gets a different answer
as their habits change.

Configured for Groq (api.groq.com). The endpoint is OpenAI-compatible, so
pointing AI_BASE_URL at another provider - xAI's Grok, OpenAI, a local server -
works without touching this code.

The API key is read from the environment and is never written to a file in the
repository:

    setx GROQ_API_KEY "gsk_your-key-here"        (Windows, then reopen terminal)
    export GROQ_API_KEY="gsk_your-key-here"      (Linux / Mac)

Optional overrides:
    AI_MODEL      model name   (default below)
    AI_BASE_URL   API base     (default https://api.groq.com/openai/v1)

With no key configured the advisor does not pretend: it reports that AI is not
configured, and the caller falls back to findings computed from the data.
"""

import os
import json
import urllib.request
import urllib.error

# gpt-oss-120b is the strongest reasoning model on this account, which matters
# for advice that has to stay tied to the numbers instead of drifting into
# generic screen-hygiene tips. Override with AI_MODEL for a faster, smaller one.
DEFAULT_MODEL = os.environ.get("AI_MODEL") or os.environ.get("XAI_MODEL") or "openai/gpt-oss-120b"
BASE_URL = (os.environ.get("AI_BASE_URL") or os.environ.get("XAI_BASE_URL")
            or "https://api.groq.com/openai/v1").rstrip("/")
TIMEOUT_SEC = 45


def api_key():
    """Accept any of the common names - people copy the key under all of them."""
    for name in ("GROQ_API_KEY", "XAI_API_KEY", "GROK_API_KEY", "AI_API_KEY"):
        v = (os.environ.get(name) or "").strip()
        if v:
            return v
    return ""


def is_configured():
    return bool(api_key())


SYSTEM_PROMPT = (
    "You are an optometry-informed assistant for Netra-Rakshaka, a wearable that "
    "measures digital eye strain. You will be given ONE user's real measured "
    "session data.\n\n"
    "Rules you must follow:\n"
    "1. Base every statement on the numbers provided. Quote the actual figures.\n"
    "2. Never invent a measurement that is not in the data.\n"
    "3. Do not diagnose. This is a preventive wellness tool, not a medical device. "
    "If the data looks concerning, advise seeing a qualified optometrist.\n"
    "4. Be specific to THIS user. Do not give generic screen-hygiene advice that "
    "would apply to anyone.\n"
    "5. If a metric is already healthy, say so briefly rather than inventing a problem.\n\n"
    "6. Always explain the REASON behind each reading - the physiological or "
    "behavioural cause - and the reason each recommendation will help. The user "
    "must understand why, not just what.\n\n"
    "Reply in plain text, no markdown symbols, in exactly this structure:\n"
    "WHAT YOUR DATA SHOWS\n"
    "- two to four short lines, each citing a real number from the data\n\n"
    "WHY THIS IS HAPPENING\n"
    "- for each finding above, the likely cause and the physiological mechanism, "
    "in one short line each\n\n"
    "WHAT TO CHANGE\n"
    "- two to four specific actions for this user, and after each one a short "
    "'because ...' explaining what it will improve\n\n"
    "WATCH FOR\n"
    "- one or two short lines on what to monitor next and why it matters\n\n"
    "Keep the whole reply under 300 words. Write simply."
)


def _build_user_prompt(d):
    """Turn the measured aggregates into a compact factual brief."""
    if not d.get("has_data"):
        return None

    lines = [
        f"Window: {d['window_label']}",
        f"Monitored screen time: {d['screen_time_min']} minutes "
        f"({d['samples']} sensor readings, disconnected periods already excluded)",
        "",
        "MEASURED VALUES (reference in brackets):",
        f"- Average blink rate: {d['avg_bpm']} per minute [healthy 15, below 8 critical]",
        f"- Lowest blink rate seen: {d['min_bpm']} per minute",
        f"- Average screen distance: {d['avg_distance_cm']} cm [guidance 40-50 cm]",
        f"- Closest distance recorded: {d['min_distance_cm']} cm [below 35 cm critical]",
        f"- Average head tilt: {d['avg_tilt_deg']} degrees [keep under 25]",
        f"- Maximum head tilt: {d['max_tilt_deg']} degrees [over 30 loads the spine]",
        f"- Average room light: {d['avg_lux']} lux [keep above 80]",
        f"- Average ambient temperature: {d['avg_temp_c']} C",
        "",
        "PROPORTION OF SESSION SPENT IN EACH UNHEALTHY STATE:",
        f"- Blinking below healthy rate: {d['time_low_blink_pct']}% of the time",
        f"- Closer than 40 cm: {d['time_too_close_pct']}% of the time",
        f"- Head tilt over 25 degrees: {d['time_bad_posture_pct']}% of the time",
        f"- Room light below 80 lux: {d['time_dim_room_pct']}% of the time",
        "",
        "STRAIN CLASSIFICATION:",
        f"- Safe {d['safe_pct']}%, Moderate {d['moderate_pct']}%, Critical {d['critical_pct']}%",
        "",
        "BREAK BEHAVIOUR:",
        f"- Enforced breaks triggered: {d['breaks_enforced']}",
    ]

    if d["rests_prompted"]:
        lines.append(
            f"- Rest prompts: {d['rests_prompted']} "
            f"({d['rests_complied']} completed, {d['rests_skipped']} skipped, "
            f"{d['rests_ignored']} ignored) = {d['compliance_pct']}% compliance")
    else:
        lines.append("- No rest prompts were responded to in this window")

    if d.get("deep_work_sessions"):
        lines.append(f"- Deep Work sessions: {d['deep_work_sessions']} "
                     f"totalling {d['deep_work_minutes']} minutes")

    if d.get("peak_strain_hours"):
        hrs = ", ".join(f"{h['hour']} ({h['critical_pct']}% critical)"
                        for h in d["peak_strain_hours"])
        lines.append(f"- Strain concentrated around: {hrs}")

    lines.append("")
    lines.append("Give this user their personalised recommendations.")
    return "\n".join(lines)


def get_recommendations(data):
    """
    Ask the model for recommendations built from this user's measured data.

    Returns (text, source, ok). On any failure `ok` is False and `text` explains
    why - the caller then uses the rule-based findings rather than showing
    nothing or, worse, showing something invented.
    """
    if not data.get("has_data"):
        return ("No recorded data in this period, so no recommendations can be made. "
                "Connect the spectacles and record a session first.",
                "no data", False)

    key = api_key()
    if not key:
        return ("AI recommendations are not configured. Set the GROQ_API_KEY "
                "environment variable and restart the server to enable them.",
                "not configured", False)

    prompt = _build_user_prompt(data)
    payload = {
        "model": DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        # Some variation so the wording feels human, but low enough that the
        # advice stays anchored to the numbers it was given.
        "temperature": 0.4,
        # gpt-oss is a reasoning model and spends tokens thinking before it
        # writes, so a tight cap truncates the answer mid-sentence. Generous
        # here; the prompt's own word limit keeps the reply short.
        "max_tokens": 2000,
    }

    req = urllib.request.Request(
        BASE_URL + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            # Without an explicit agent urllib sends "Python-urllib/3.x", which
            # Cloudflare in front of the API blocks outright (HTTP 403, code
            # 1010) before the request ever reaches the model. The key is fine;
            # the default user agent is what gets refused.
            "User-Agent": "Netra-Rakshaka/1.0",
            "Accept": "application/json",
        },
        method="POST")

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
            body = json.loads(r.read().decode("utf-8"))
        text = (body.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        if not text:
            return ("The AI service returned an empty response.", "empty response", False)
        return (text, f"Groq · {DEFAULT_MODEL}", True)

    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:400]
        except Exception:
            pass
        if e.code in (401, 403):
            msg = "The API key was rejected. Check GROQ_API_KEY is correct and active."
        elif e.code == 404:
            msg = (f"The model '{DEFAULT_MODEL}' was not found. Set AI_MODEL to a model "
                   "your account can use.")
        elif e.code == 429:
            msg = "Rate limit or quota reached on the AI service. Try again shortly."
        else:
            msg = f"The AI service returned HTTP {e.code}."
        print(f"[AI ADVISOR] HTTP {e.code}: {detail}")
        return (msg, f"error {e.code}", False)

    except Exception as e:
        print(f"[AI ADVISOR] {type(e).__name__}: {e}")
        return (f"Could not reach the AI service ({type(e).__name__}). "
                "Check the internet connection.", "unreachable", False)
