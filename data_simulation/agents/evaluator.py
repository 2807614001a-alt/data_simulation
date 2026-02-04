import json
import os
import sys
from datetime import datetime, time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()
current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

DATA_DIR = project_root / "data"
SETTINGS_DIR = project_root / "settings"


def _read_json(path: Path) -> Optional[Dict]:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _parse_iso(dt_str: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(dt_str)
    except Exception:
        return None


def _collect_day_indexes() -> List[int]:
    days = set()
    for path in DATA_DIR.glob("simulation_context_day*.json"):
        name = path.stem
        try:
            day_idx = int(name.replace("simulation_context_day", ""))
            days.add(day_idx)
        except Exception:
            continue
    return sorted(days)


def _load_profile() -> Dict:
    profile = _read_json(SETTINGS_DIR / "profile.json")
    return profile or {}


def _load_layout() -> Dict:
    layout = _read_json(SETTINGS_DIR / "house_layout.json")
    return layout or {}


def _load_details() -> Dict:
    details_list = _read_json(SETTINGS_DIR / "house_details.json") or []
    details = {}
    for item in details_list:
        item_id = item.get("furniture_id") or item.get("device_id")
        if item_id:
            details[item_id] = item
    return details


def _score_structure(activities: List[Dict], day_start: str, day_end: str) -> Tuple[int, List[str]]:
    issues = []
    if not activities:
        return 0, ["no activities"]

    starts = []
    for act in activities:
        s = _parse_iso(act.get("start_time", ""))
        e = _parse_iso(act.get("end_time", ""))
        if not s or not e:
            issues.append("invalid time format")
            continue
        starts.append((s, e))
    starts.sort(key=lambda x: x[0])

    score = 100
    for i in range(1, len(starts)):
        prev_end = starts[i - 1][1]
        cur_start = starts[i][0]
        if cur_start < prev_end:
            score -= 10
            issues.append("overlap detected")
        if cur_start > prev_end:
            score -= 5
            issues.append("gap detected")

    ds = _parse_iso(day_start)
    de = _parse_iso(day_end)
    if ds and starts:
        if starts[0][0] > ds:
            score -= 10
            issues.append("starts after day_start_time")
    if de and starts:
        if starts[-1][1] < de:
            score -= 10
            issues.append("ends before day_end_time")

    return max(score, 0), sorted(set(issues))


def _score_persona(activities: List[Dict], profile: Dict, day_type: str) -> Tuple[int, List[str]]:
    issues = []
    routines = profile.get("routines", {})
    sleep_schedule = routines.get("sleep_schedule", {})
    meal_habits = routines.get("meal_habits", {})

    score = 100

    # Meals check
    meal_targets = {
        "breakfast": meal_habits.get("breakfast_time", meal_habits.get("breakfast", "08:00")),
        "lunch": meal_habits.get("lunch_time", meal_habits.get("lunch", "12:30")),
        "dinner": meal_habits.get("dinner_time", meal_habits.get("dinner", "19:30")),
    }
    meal_keywords = {
        "breakfast": ["早餐"],
        "lunch": ["午餐", "午饭"],
        "dinner": ["晚餐", "晚饭"],
    }
    for meal, target in meal_targets.items():
        target_t = _parse_iso(f"2000-01-01T{target}:00")
        found = False
        for act in activities:
            name = act.get("activity_name", "")
            if any(k in name for k in meal_keywords[meal]):
                s = _parse_iso(act.get("start_time", ""))
                if s and target_t:
                    actual = s.time()
                    delta = abs(
                        (datetime.combine(datetime.min, actual) - datetime.combine(datetime.min, target_t.time())).total_seconds()
                    )
                    if delta > 3600:
                        score -= 8
                        issues.append(f"{meal} time deviates >1h")
                found = True
                break
        if not found:
            score -= 8
            issues.append(f"{meal} missing")

    # Sleep check (simple)
    wake_time_key = "weekday_wakeup" if day_type == "workday" else "weekend_wakeup"
    sleep_time_key = "weekday_bedtime" if day_type == "workday" else "weekend_bedtime"
    wake_time = sleep_schedule.get(wake_time_key)
    sleep_time = sleep_schedule.get(sleep_time_key)

    if wake_time:
        first_act = activities[0] if activities else {}
        s = _parse_iso(first_act.get("start_time", ""))
        if s:
            actual = s.time()
            target_t = _parse_iso(f"2000-01-01T{wake_time}:00")
            if target_t:
                delta = abs(
                    (datetime.combine(datetime.min, actual) - datetime.combine(datetime.min, target_t.time())).total_seconds()
                )
                if delta > 5400:
                    score -= 10
                    issues.append("wake time deviates >1.5h")
    if sleep_time:
        last_act = activities[-1] if activities else {}
        e = _parse_iso(last_act.get("end_time", ""))
        if e:
            actual = e.time()
            target_t = _parse_iso(f"2000-01-01T{sleep_time}:00")
            if target_t:
                delta = abs(
                    (datetime.combine(datetime.min, actual) - datetime.combine(datetime.min, target_t.time())).total_seconds()
                )
                if delta > 7200:
                    score -= 10
                    issues.append("sleep time deviates >2h")

    return max(score, 0), sorted(set(issues))


def _score_environment(
    activities: List[Dict],
    events: List[Dict],
    layout: Dict,
    details: Dict
) -> Tuple[int, List[str]]:
    issues = []
    score = 100
    room_ids = set(layout.keys())
    detail_ids = set(details.keys())

    for act in activities:
        rooms = act.get("main_rooms", [])
        for room in rooms:
            if room not in room_ids:
                score -= 10
                issues.append(f"invalid room in activities: {room}")

    for evt in events:
        room_id = evt.get("room_id")
        target_ids = evt.get("target_object_ids", [])
        if room_id != "Outside" and room_id not in room_ids:
            score -= 10
            issues.append(f"invalid room in events: {room_id}")
        if room_id == "Outside" and target_ids:
            score -= 10
            issues.append("outside event has target_object_ids")
        for obj_id in target_ids:
            if obj_id not in detail_ids:
                score -= 6
                issues.append(f"unknown object id: {obj_id}")

    return max(score, 0), sorted(set(issues))


def _score_special_events(
    activities: List[Dict],
    simulation_context: Dict
) -> Tuple[int, List[str]]:
    issues = []
    score = 100
    state = simulation_context.get("simulation_state")
    required = 0
    if state == "Perturbed":
        required = int(simulation_context.get("random_event_count") or 0)
    elif state == "Crisis":
        required = int(simulation_context.get("emergency_event_count") or 0)
    if state in {"Perturbed", "Crisis"}:
        marks = 0
        for act in activities:
            desc = act.get("description", "")
            name = act.get("activity_name", "")
            text = f"{name} {desc}"
            if "事件：" in text or "突发" in text or "危机" in text:
                marks += 1
        if marks < required:
            score -= 20
            issues.append(f"special events < required ({marks}/{required})")
    return max(score, 0), sorted(set(issues))


def _score_cross_day(
    previous_summary: str,
    activities: List[Dict]
) -> Tuple[int, List[str]]:
    issues = []
    score = 100
    summary = previous_summary or ""
    if summary == "N/A":
        return score, issues

    needs_adjust = any(k in summary for k in ["熬夜", "睡眠不足", "晚起", "感冒", "头晕", "危机"])
    if not needs_adjust:
        return score, issues

    found_adjust = False
    for act in activities:
        text = f"{act.get('activity_name', '')} {act.get('description', '')}"
        if any(k in text for k in ["休息", "调整", "取消", "降低强度", "改为"]):
            found_adjust = True
            break

    if not found_adjust:
        score -= 20
        issues.append("no cross-day adjustment found")

    return max(score, 0), sorted(set(issues))


def evaluate() -> Dict:
    profile = _load_profile()
    layout = _load_layout()
    details = _load_details()
    day_indexes = _collect_day_indexes()

    report = {"days": [], "summary": {}}
    total = 0
    for day_idx in day_indexes:
        sim_ctx = _read_json(DATA_DIR / f"simulation_context_day{day_idx}.json") or {}
        activity = _read_json(DATA_DIR / f"activity_day{day_idx}.json") or {}
        events = _read_json(DATA_DIR / f"events_day{day_idx}.json") or []
        activities = activity.get("activities", [])

        s_score, s_issues = _score_structure(
            activities,
            sim_ctx.get("day_start_time", ""),
            sim_ctx.get("day_end_time", ""),
        )
        p_score, p_issues = _score_persona(
            activities,
            profile,
            sim_ctx.get("day_type", "workday"),
        )
        e_score, e_issues = _score_environment(activities, events, layout, details)
        sp_score, sp_issues = _score_special_events(activities, sim_ctx)
        c_score, c_issues = _score_cross_day(
            sim_ctx.get("previous_day_summary", ""),
            activities,
        )

        day_total = int(round((s_score + p_score + e_score + sp_score + c_score) / 5))
        total += day_total
        report["days"].append({
            "day": day_idx,
            "scores": {
                "structure": s_score,
                "persona": p_score,
                "environment": e_score,
                "special_events": sp_score,
                "cross_day": c_score,
                "overall": day_total,
            },
            "issues": {
                "structure": s_issues,
                "persona": p_issues,
                "environment": e_issues,
                "special_events": sp_issues,
                "cross_day": c_issues,
            },
        })

    report["summary"] = {
        "days": len(day_indexes),
        "overall_avg": int(round(total / len(day_indexes))) if day_indexes else 0,
    }
    return report


def main() -> None:
    report = evaluate()
    output_path = DATA_DIR / "evaluation_report.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"[OK] Evaluation saved to {output_path}")


if __name__ == "__main__":
    main()
