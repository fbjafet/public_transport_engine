import json
from typing import Any

import requests

from app.config import Config


def _call_llm(system_prompt: str, user_prompt: str) -> dict[str, Any] | None:
    if not Config.LLM_API_KEY:
        return None

    try:
        response = requests.post(
            Config.LLM_API_URL,
            headers={
                "Authorization": f"Bearer {Config.LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": Config.LLM_MODEL,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
            },
            timeout=15,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception:
        return None


def analyze_disruption(
    route_code: str,
    route_name: str,
    mode: str,
    stop_name: str | None,
    raw_note: str,
) -> dict[str, str]:
    system_prompt = (
        "You are a public transit dispatch assistant. Return strict JSON with keys: "
        "'severity' (minor|major|critical), 'route_status' (delayed|diverted|suspended), "
        "and 'diversion_summary' (concise plain-language operational diversion notice)."
    )
    user_prompt = (
        f"Route: {route_code} ({route_name}, mode: {mode})\n"
        f"Affected stop: {stop_name or 'Unspecified'}\n"
        f"Driver/operational note: {raw_note}"
    )

    llm_result = _call_llm(system_prompt, user_prompt)
    if llm_result and llm_result.get("severity") in {"minor", "major", "critical"}:
        return {
            "severity": llm_result["severity"],
            "route_status": llm_result.get("route_status", "delayed"),
            "diversion_summary": llm_result.get(
                "diversion_summary", "Monitor route for ongoing delays."
            ),
        }

    note_lower = raw_note.lower()
    if any(
        w in note_lower
        for w in ("suspend", "closed", "power", "derail", "collision", "blocked")
    ):
        severity = "critical"
        route_status = "suspended"
        diversion = (
            f"Service on {route_code} is temporarily suspended"
            f"{f' near {stop_name}' if stop_name else ''}. "
            "Passengers should transfer to parallel routes or await shuttle replacement."
        )
    elif any(
        w in note_lower for w in ("divert", "roadworks", "detour", "flood", "protest")
    ):
        severity = "major"
        route_status = "diverted"
        diversion = (
            f"Route {route_code} is operating on a local diversion"
            f"{f', bypassing {stop_name}' if stop_name else ''}. "
            "Board at the nearest adjacent stop and allow extra travel time."
        )
    else:
        severity = "minor"
        route_status = "delayed"
        diversion = (
            f"Route {route_code} is experiencing delays"
            f"{f' around {stop_name}' if stop_name else ''}. "
            "Services remain running on the normal alignment."
        )

    return {
        "severity": severity,
        "route_status": route_status,
        "diversion_summary": diversion,
    }


def draft_channel_alerts(
    route_code: str,
    route_name: str,
    stop_name: str | None,
    severity: str,
    diversion_summary: str,
    tone: str,
) -> dict[str, str]:
    system_prompt = (
        "You are a transit passenger communications specialist. "
        "Return strict JSON with keys 'web', 'sms', and 'signage'. "
        "Keep 'sms' under 160 characters, 'signage' under 120 characters, and 'web' under 450 characters. "
        "Use plain language matching the requested tone."
    )
    user_prompt = (
        f"Tone: {tone}\n"
        f"Severity: {severity}\n"
        f"Route: {route_code} - {route_name}\n"
        f"Affected stop: {stop_name or 'Multiple stops'}\n"
        f"Diversion summary: {diversion_summary}"
    )

    llm_result = _call_llm(system_prompt, user_prompt)
    if llm_result and all(k in llm_result for k in ("web", "sms", "signage")):
        return {
            "web": str(llm_result["web"])[:500],
            "sms": str(llm_result["sms"])[:500],
            "signage": str(llm_result["signage"])[:500],
        }

    location = f" at {stop_name}" if stop_name else ""
    if tone == "urgent":
        return {
            "web": (
                f"URGENT SERVICE ALERT — Route {route_code} ({route_name}): "
                f"Active {severity} disruption{location}. {diversion_summary} "
                "Please plan an alternative route immediately."
            )[:500],
            "sms": (
                f"URGENT {route_code}: {severity.upper()} disruption{location}. "
                f"{diversion_summary}"
            )[:160],
            "signage": (
                f"ALERT {route_code}: {severity.upper()} DISRUPTION{location.upper()} — CHECK ALTERNATIVE ROUTES"
            )[:120],
        }

    return {
        "web": (
            f"Service Update — Route {route_code} ({route_name}): "
            f"We are currently managing a {severity} disruption{location}. "
            f"{diversion_summary} Thank you for your patience."
        )[:500],
        "sms": (
            f"Update {route_code}: {severity} disruption{location}. "
            f"{diversion_summary}"
        )[:160],
        "signage": (
            f"{route_code} UPDATE: DELAYS/DIVERSION{location.upper()} — ALLOW EXTRA TIME"
        )[:120],
    }