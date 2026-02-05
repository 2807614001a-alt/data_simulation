import json
import os
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple, List

from dotenv import load_dotenv

current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import planning
import event
import device_operate

load_dotenv()
dotenv_path = project_root / ".env"
load_dotenv(dotenv_path=dotenv_path)

DATA_DIR = project_root / "data"

DAYS = int(os.getenv("SIM_DAYS", "14"))
START_DATE = os.getenv("SIM_START_DATE")
RUN_EVENTS = os.getenv("SIM_RUN_EVENTS", "1") != "0"

NORMAL_WEIGHT = float(os.getenv("SIM_NORMAL_WEIGHT", "0.7"))
PERTURBED_WEIGHT = float(os.getenv("SIM_PERTURBED_WEIGHT", "0.2"))
CRISIS_WEIGHT = float(os.getenv("SIM_CRISIS_WEIGHT", "0.1"))
RANDOM_EVENT_MEAN = float(os.getenv("SIM_RANDOM_EVENT_MEAN", "1.0"))
RANDOM_EVENT_STD = float(os.getenv("SIM_RANDOM_EVENT_STD", "0.5"))
RANDOM_EVENT_MAX = int(os.getenv("SIM_RANDOM_EVENT_MAX", "3"))


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, payload: Dict) -> None:
    _ensure_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

def _write_simulation_context(day_index: int, payload: Dict) -> None:
    _write_json(DATA_DIR / "simulation_context.json", payload)
    _write_json(DATA_DIR / f"simulation_context_day{day_index}.json", payload)


def _pick_state() -> str:
    states = ["Normal", "Perturbed", "Crisis"]
    weights = [NORMAL_WEIGHT, PERTURBED_WEIGHT, CRISIS_WEIGHT]
    return random.choices(states, weights=weights, k=1)[0]


def _build_simulation_context(
    current_date: date,
    previous_day_summary: Optional[str],
    previous_day_snapshot: Optional[Dict],
    day_start_time: str,
    day_end_time: str,
    event_config: Optional[Dict],
    agent_state: Optional[Dict],
    agent_state_stage: str,
) -> Dict[str, Optional[str]]:
    day_of_week = current_date.strftime("%A")
    day_type = "weekend" if day_of_week in {"Saturday", "Sunday"} else "workday"

    simulation_state = _pick_state()
    force_day1_state = os.getenv("SIM_FORCE_DAY1_STATE", "").strip()
    if force_day1_state:
        simulation_state = force_day1_state
    random_event = ""
    emergency_event = ""
    random_event_count = 0
    emergency_event_count = 0

    def _sample_event_count(kind: str) -> int:
        cfg = (event_config or {}).get(kind) or {}
        mean = float(cfg.get("mean", RANDOM_EVENT_MEAN))
        std = float(cfg.get("std", RANDOM_EVENT_STD))
        max_count = int(cfg.get("max", RANDOM_EVENT_MAX))
        count = int(round(random.gauss(mean, std)))
        if count < 0:
            return 0
        return min(count, max_count)

    if simulation_state == "Perturbed":
        random_event_count = _sample_event_count("perturbed")
    elif simulation_state == "Crisis":
        emergency_event_count = _sample_event_count("crisis")

    return {
        "current_date": current_date.isoformat(),
        "day_of_week": day_of_week,
        "day_type": day_type,
        "simulation_state": simulation_state,
        "previous_day_summary": previous_day_summary or "N/A",
        "previous_day_snapshot": previous_day_snapshot or {},
        "agent_state": agent_state or {},
        "agent_state_stage": agent_state_stage,
        "day_start_time": day_start_time,
        "day_end_time": day_end_time,
        "random_event": random_event,
        "emergency_event": emergency_event,
        "random_event_count": random_event_count,
        "emergency_event_count": emergency_event_count,
    }


def _copy_json(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)
    _write_json(dst, data)


def _parse_iso(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str)


def _format_iso(dt_obj: datetime) -> str:
    return dt_obj.isoformat()


def _get_sleep_cutoff(activities: List[Dict]) -> Optional[datetime]:
    for activity in activities:
        name = activity.get("activity_name", "")
        if "睡眠" in name or "Sleep" in name:
            try:
                return _parse_iso(activity["start_time"])
            except Exception:
                return None
    return None


def _get_wake_time(profile: Dict, target_date: date) -> datetime:
    routines = profile.get("routines", {})
    sleep_schedule = routines.get("sleep_schedule", {})
    day_of_week = target_date.strftime("%A")
    day_type = "weekend" if day_of_week in {"Saturday", "Sunday"} else "workday"
    if day_type == "workday":
        wake_str = sleep_schedule.get("weekday_wakeup", "07:00")
    else:
        wake_str = sleep_schedule.get("weekend_wakeup", "08:30")
    return datetime.combine(target_date, datetime.strptime(wake_str, "%H:%M").time())


def _get_day_time_window(profile: Dict, current_date: date) -> Tuple[datetime, datetime]:
    routines = profile.get("routines", {})
    sleep_schedule = routines.get("sleep_schedule", {})
    day_of_week = current_date.strftime("%A")
    day_type = "weekend" if day_of_week in {"Saturday", "Sunday"} else "workday"

    if day_type == "workday":
        bed_str = sleep_schedule.get("weekday_bedtime", "23:30")
    else:
        bed_str = sleep_schedule.get("weekend_bedtime", "00:30")

    wake_time = _get_wake_time(profile, current_date)
    bed_time = datetime.combine(current_date, datetime.strptime(bed_str, "%H:%M").time())
    if bed_time <= wake_time:
        bed_time = bed_time + timedelta(days=1)
    # day_end_time set to next day's wake time to include sleep at day end
    next_day_wake = _get_wake_time(profile, current_date + timedelta(days=1))
    return wake_time, next_day_wake


def _align_and_slice_activities(
    activities: List[Dict],
    day_start: datetime,
    day_end_limit: datetime,
) -> Tuple[List[Dict], datetime]:
    if not activities:
        return activities, day_start

    try:
        first_start = _parse_iso(activities[0]["start_time"])
    except Exception:
        return activities, day_start

    delta = day_start - first_start
    for activity in activities:
        try:
            start = _parse_iso(activity["start_time"]) + delta
            end = _parse_iso(activity["end_time"]) + delta
            activity["start_time"] = _format_iso(start)
            activity["end_time"] = _format_iso(end)
        except Exception:
            continue

    cutoff = day_end_limit

    sliced: List[Dict] = []
    for activity in activities:
        start = _parse_iso(activity["start_time"])
        if start >= cutoff:
            break
        end = _parse_iso(activity["end_time"])
        if end > cutoff:
            activity["end_time"] = _format_iso(cutoff)
            sliced.append(activity)
            break
        sliced.append(activity)

    if not sliced:
        return sliced, cutoff

    sliced[-1]["end_time"] = _format_iso(cutoff)
    return sliced, cutoff


def _load_action_event_chain_snapshot() -> Dict:
    chain_path = DATA_DIR / "action_event_chain.json"
    if not chain_path.exists():
        return {"agent_location": "Unknown", "device_states": {}}
    with open(chain_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    events = data.get("action_event_chain", [])
    agent_location = events[-1].get("room_id", "Unknown") if events else "Unknown"

    device_states: Dict[str, Dict[str, str]] = {}
    for evt in events:
        layer = evt.get("layer5_device_state", {})
        for patch in layer.get("patch_on_start", []) + layer.get("patch_on_end", []):
            device_id = patch.get("device_id")
            patch_data = patch.get("patch", {})
            if not device_id:
                continue
            state = device_states.setdefault(device_id, {})
            for key, value in patch_data.items():
                state[key] = value

    device_power = {
        dev_id: {"power": state.get("power", "unknown")}
        for dev_id, state in device_states.items()
    }
    return {"agent_location": agent_location, "device_states": device_power}


def _update_physiology_state(previous_state: Dict[str, float], previous_day_summary: str) -> Dict[str, float]:
    fatigue = previous_state.get("fatigue", 0.3)
    hunger = previous_state.get("hunger", 0.3)

    summary = previous_day_summary or ""
    if "熬夜" in summary or "睡眠不足" in summary:
        fatigue += 0.2
    if "高强度" in summary:
        fatigue += 0.2
    if "晚起" in summary:
        fatigue += 0.1
    if "饮酒" in summary:
        fatigue += 0.1

    hunger += 0.2
    fatigue = max(0.0, min(1.0, fatigue - 0.05))
    hunger = max(0.0, min(1.0, hunger))
    return {"fatigue": fatigue, "hunger": hunger}

def _init_agent_state(previous_day_summary: str, previous_day_snapshot: Dict) -> Dict[str, object]:
    physiology = (previous_day_snapshot or {}).get("physiology", {})
    fatigue = float(physiology.get("fatigue", 0.3))
    hunger = float(physiology.get("hunger", 0.3))

    energy = max(0.0, min(1.0, 1.0 - fatigue))
    stress = 0.3
    mood = "neutral"
    health = "ok"

    summary = previous_day_summary or ""
    if "熬夜" in summary or "睡眠不足" in summary:
        energy = max(0.0, energy - 0.2)
        mood = "tired"
    if "生病" in summary or "感冒" in summary or "头晕" in summary:
        health = "unwell"
        mood = "low"
        stress += 0.2
    if "跌倒" in summary or "危机" in summary:
        health = "unwell"
        stress += 0.3

    return {
        "mood": mood,
        "energy": round(energy, 2),
        "stress": round(min(1.0, stress), 2),
        "hunger": round(min(1.0, hunger), 2),
        "health": health,
        "notes": ""
    }

def _update_agent_state_from_activities(agent_state: Dict[str, object], activities: List[Dict]) -> Dict[str, object]:
    state = dict(agent_state or {})
    energy = float(state.get("energy", 0.5))
    stress = float(state.get("stress", 0.3))
    mood = state.get("mood", "neutral")
    health = state.get("health", "ok")

    for act in activities:
        name = act.get("activity_name", "")
        desc = act.get("description", "")
        text = f"{name} {desc}"
        if "睡眠" in text:
            energy = min(1.0, energy + 0.2)
        if "高强度" in text or "加班" in text:
            stress = min(1.0, stress + 0.2)
        if "休息" in text or "放松" in text:
            stress = max(0.0, stress - 0.1)
        if "事件：" in text or "突发" in text or "危机" in text:
            stress = min(1.0, stress + 0.2)
        if "感冒" in text or "头晕" in text or "不适" in text:
            health = "unwell"
            mood = "low"

    state["energy"] = round(max(0.0, min(1.0, energy)), 2)
    state["stress"] = round(max(0.0, min(1.0, stress)), 2)
    state["mood"] = mood
    state["health"] = health
    return state


def run_multi_day_simulation() -> None:
    seed = os.getenv("SIM_RANDOM_SEED")
    if seed is not None:
        random.seed(int(seed))

    if START_DATE:
        base_date = date.fromisoformat(START_DATE)
    else:
        base_date = date.today()

    profile_json = planning.load_profile_json()
    try:
        profile_data = json.loads(profile_json)
    except Exception:
        profile_data = {}
    event_config = profile_data.get("random_event_config") or {}
    previous_day_summary = "N/A"
    previous_day_snapshot: Dict = {}
    physiology_state = {"fatigue": 0.3, "hunger": 0.3}

    for day_index in range(1, DAYS + 1):
        current_date = base_date + timedelta(days=day_index - 1)
        day_start_time, day_end_limit = _get_day_time_window(profile_data, current_date)

        agent_state = _init_agent_state(previous_day_summary, previous_day_snapshot)
        simulation_context = _build_simulation_context(
            current_date,
            previous_day_summary,
            previous_day_snapshot,
            _format_iso(day_start_time),
            _format_iso(day_end_limit),
            event_config,
            agent_state,
            "start",
        )

        _write_simulation_context(day_index, simulation_context)

        activity_plan = planning.run_planning(simulation_context=simulation_context)
        if not activity_plan:
            print(f"[ERROR] Day {day_index}: activity plan generation failed.")
            break

        activities = activity_plan.get("activities", [])
        aligned_activities, day_end_time = _align_and_slice_activities(
            activities,
            day_start_time,
            day_end_limit,
        )
        activity_plan["activities"] = aligned_activities

        _write_json(DATA_DIR / "activity.json", activity_plan)
        _write_json(DATA_DIR / f"activity_day{day_index}.json", activity_plan)

        simulation_context["agent_state"] = _update_agent_state_from_activities(
            simulation_context.get("agent_state", {}),
            aligned_activities,
        )
        simulation_context["agent_state_stage"] = "after_planning"
        _write_simulation_context(day_index, simulation_context)

        if RUN_EVENTS:
            event.run_batch_processing(activity_plan.get("activities", []))
            _copy_json(DATA_DIR / "events.json", DATA_DIR / f"events_day{day_index}.json")

            device_operate.run_event_chain_generation()
            _copy_json(
                DATA_DIR / "action_event_chain.json",
                DATA_DIR / f"action_event_chain_day{day_index}.json",
            )
            simulation_context["agent_state_stage"] = "after_events"
            _write_simulation_context(day_index, simulation_context)

        activities = activity_plan.get("activities", [])
        if activities:
            previous_day_summary = planning.generate_previous_day_summary(profile_json, activities)
            _write_json(
                DATA_DIR / f"previous_day_summary_day{day_index}.json",
                {"previous_day_summary": previous_day_summary},
            )
        else:
            previous_day_summary = "N/A"

        if RUN_EVENTS:
            previous_day_snapshot = _load_action_event_chain_snapshot()
        else:
            previous_day_snapshot = {"agent_location": "Unknown", "device_states": {}}
        physiology_state = _update_physiology_state(physiology_state, previous_day_summary)
        previous_day_snapshot["physiology"] = physiology_state

        print(f"[OK] Day {day_index} completed.")


if __name__ == "__main__":
    run_multi_day_simulation()
