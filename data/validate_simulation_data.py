# -*- coding: utf-8 -*-
"""
检查 data 目录下仿真数据是否合理：房间/物品与 layout 一致、时间顺序、跨文件一致性。
运行: 在 data_simulation 项目根目录下执行
  python data/validate_simulation_data.py
  或在 data 目录下: python validate_simulation_data.py
"""
import json
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# 兼容直接运行或从项目根运行
DATA_DIR = Path(__file__).resolve().parent
if DATA_DIR.name != "data":
    DATA_DIR = Path(__file__).resolve().parent / "data"
SETTINGS_DIR = DATA_DIR.parent / "settings"
VALID_ROOMS = set()  # 从 house_layout 加载
ROOM_ITEMS = {}      # room_id -> set of furniture + devices
ALL_ITEM_IDS = set()


def load_layout():
    layout_path = SETTINGS_DIR / "house_layout.json"
    if not layout_path.exists():
        return False
    with open(layout_path, "r", encoding="utf-8") as f:
        layout = json.load(f)
    global VALID_ROOMS, ROOM_ITEMS, ALL_ITEM_IDS
    VALID_ROOMS = set(layout.keys())
    for rid, data in layout.items():
        furniture = set(data.get("furniture", []))
        devices = set(data.get("devices", []))
        ROOM_ITEMS[rid] = furniture | devices
        ALL_ITEM_IDS |= ROOM_ITEMS[rid]
    VALID_ROOMS.add("Outside")
    return True


def parse_iso(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def check_activity_file(path, day_label, issues):
    if not path.exists():
        issues["missing"].append(f"activity: {day_label}")
        return
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    activities = data.get("activities", [])
    if not activities:
        issues["empty"].append(f"activity {day_label}: no activities")
        return
    for i, act in enumerate(activities):
        aid = act.get("activity_id", "")
        main_rooms = act.get("main_rooms", [])
        start = act.get("start_time", "")
        end = act.get("end_time", "")
        for r in main_rooms:
            if r != "Outside" and r not in VALID_ROOMS:
                issues["invalid_room"].append(f"{day_label} activity {aid} main_rooms: '{r}' not in layout")
        if start and end:
            t0, t1 = parse_iso(start), parse_iso(end)
            if t0 and t1:
                ts0 = t0.timestamp() if hasattr(t0, "timestamp") else 0
                ts1 = t1.timestamp() if hasattr(t1, "timestamp") else 0
                if ts1 <= ts0:
                    issues["time"].append(f"{day_label} activity {aid}: end_time <= start_time")
    return


def get_events_list(data):
    if isinstance(data, list):
        return data
    return data.get("events", [])


def check_events_file(path, day_label, issues):
    if not path.exists():
        issues["missing"].append(f"events: {day_label}")
        return
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    events = get_events_list(data)
    if not events:
        issues["empty"].append(f"events {day_label}: no events")
        return
    prev_end = None  # float timestamp for comparison
    for i, ev in enumerate(events):
        rid = ev.get("room_id", "")
        objs = ev.get("target_object_ids", [])
        start = ev.get("start_time", "")
        end = ev.get("end_time", "")
        if rid != "Outside" and rid not in VALID_ROOMS:
            issues["invalid_room"].append(f"{day_label} events[{i}] room_id: '{rid}' not in layout")
        if rid != "Outside" and rid in ROOM_ITEMS:
            for oid in objs:
                if oid not in ROOM_ITEMS[rid]:
                    issues["item_mismatch"].append(f"{day_label} events[{i}] room={rid} target_object_ids contains '{oid}' not in room")
        if start and end:
            t0, t1 = parse_iso(start), parse_iso(end)
            if t0 and t1:
                ts0, ts1 = t0.timestamp() if hasattr(t0, "timestamp") else 0, t1.timestamp() if hasattr(t1, "timestamp") else 0
                if ts1 <= ts0:
                    issues["time"].append(f"{day_label} events[{i}] end_time <= start_time")
                if prev_end is not None and ts0 < prev_end:
                    issues["time"].append(f"{day_label} events[{i}] start_time before previous end")
                prev_end = ts1
    return


def check_chain_file(path, day_label, events_path, issues):
    if not path.exists():
        issues["missing"].append(f"action_event_chain: {day_label}")
        return
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    chain = data.get("action_event_chain", [])
    if not chain:
        issues["empty"].append(f"action_event_chain {day_label}: empty")
        return
    # 与 events 条数一致（若 events 存在）
    if events_path.exists():
        with open(events_path, "r", encoding="utf-8") as f:
            ed = json.load(f)
        ev_list = get_events_list(ed)
        if len(ev_list) != len(chain):
            issues["mismatch"].append(f"{day_label}: events count {len(ev_list)} != chain count {len(chain)}")
    for i, link in enumerate(chain):
        rid = link.get("room_id", "")
        if rid != "Outside" and rid not in VALID_ROOMS:
            issues["invalid_room"].append(f"{day_label} chain[{i}] room_id: '{rid}' not in layout")
    return


def check_simulation_context(path, day_label, issues):
    if not path.exists():
        issues["missing"].append(f"simulation_context: {day_label}")
        return
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    d = data.get("current_date", "")
    if not d:
        issues["meta"].append(f"{day_label} simulation_context: no current_date")
    return


def main():
    if not load_layout():
        print("ERROR: house_layout.json not found under settings/. Skip room/item checks.")
        global VALID_ROOMS, ROOM_ITEMS
        VALID_ROOMS = {"living_room", "study_room", "master_bedroom", "kitchen", "bathroom", "Outside"}
        ROOM_ITEMS = {}

    issues = defaultdict(list)
    days = list(range(1, 15))
    for d in days:
        label = f"day{d}"
        check_activity_file(DATA_DIR / f"activity_day{d}.json", label, issues)
        check_events_file(DATA_DIR / f"events_day{d}.json", label, issues)
        check_chain_file(
            DATA_DIR / f"action_event_chain_day{d}.json",
            label,
            DATA_DIR / f"events_day{d}.json",
            issues,
        )
        check_simulation_context(DATA_DIR / f"simulation_context_day{d}.json", label, issues)

    # 汇总
    print("=" * 60)
    print("Simulation data validation report")
    print("=" * 60)
    print(f"Layout: valid rooms = {sorted(VALID_ROOMS)}")
    print()

    total = 0
    for key in ["missing", "empty", "invalid_room", "item_mismatch", "time", "mismatch", "meta"]:
        L = issues[key]
        total += len(L)
        if L:
            print(f"[{key}] ({len(L)}):")
            for line in L[:15]:
                print("  ", line)
            if len(L) > 15:
                print("  ... and", len(L) - 15, "more")
            print()

    if total == 0:
        print("No issues found. Data looks consistent.")
    else:
        print(f"Total issues: {total}")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
