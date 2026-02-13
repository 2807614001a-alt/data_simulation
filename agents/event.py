import os
import json
import logging
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from typing_extensions import TypedDict

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END
from dotenv import load_dotenv

load_dotenv()
current_dir = Path(__file__).resolve().parent
dotenv_path = current_dir.parent / '.env'
load_dotenv(dotenv_path=dotenv_path)
project_root = current_dir.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
from llm_utils import create_fast_llm
from prompt import (
    EVENT_REQUIREMENTS,
    EVENT_GENERATION_PROMPT_TEMPLATE,
    EVENT_VALIDATION_PROMPT_TEMPLATE,
    EVENT_CORRECTION_PROMPT_TEMPLATE,
)
from agent_config import (
    DEFAULT_MODEL,
    EVENT_TEMPERATURE,
    EVENT_USE_RESPONSES_API,
    SKIP_EVENT_VALIDATION,
    MAX_EVENT_REVISIONS,
    LLM_RETRY_COUNT,
    LLM_RETRY_DELAY,
    INNER_LLM_RETRY_COUNT,
    INNER_LLM_RETRY_DELAY,
    USE_ITERATIVE_EVENT_GENERATION,
)
from physics_engine import calculate_room_state

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==========================================
# 2. 数据结构定义 (Pydantic Models)
# ==========================================

class DevicePatchEntry(BaseModel):
    """设备状态单条键值变更，满足 API 严格 schema（无 additionalProperties）。"""
    key: str = Field(description="状态属性名，如 power, mode, state, brightness")
    value: str = Field(description="状态值，如 on, off, cool, open, 80")


class DevicePatchItem(BaseModel):
    """单个事件导致的设备状态变更，用于物理闭环（如打开空调后温度下降）。"""
    device_id: str = Field(description="设备ID，须在 target_object_ids 或当前房间设备中")
    patch: List[DevicePatchEntry] = Field(
        default_factory=list,
        description="状态键值变更列表，如 [{\"key\": \"power\", \"value\": \"on\"}, {\"key\": \"mode\", \"value\": \"cool\"}]；无则空列表",
    )


class EventItem(BaseModel):
    activity_id: str = Field(description="所属父活动ID")
    start_time: str = Field(description="ISO格式开始时间")
    end_time: str = Field(description="ISO格式结束时间")
    room_id: str = Field(description="发生的房间ID，外出为'Outside'")
    target_object_ids: List[str] = Field(description="交互的物品ID列表")
    action_type: str = Field(description="动作类型: interact, move, idle, outside")
    description: str = Field(description="详细描述，包含动作、物品、性格细节")
    device_patches: List[DevicePatchItem] = Field(
        default_factory=list,
        description="本事件导致的设备状态变更，如打开/关闭设备；无则空列表。用于物理引擎计算下一时刻温度/湿度/清洁度。",
    )


class EventSequence(BaseModel):
    events: List[EventItem]

class ValidationResult(BaseModel):
    is_valid: bool = Field(description="是否通过验证")
    correction_content: Optional[str] = Field(description="错误详情与修改建议")

# ==========================================
# 3. 数据加载与环境上下文工具
# ==========================================

def load_settings_data(project_root: Path) -> Dict[str, Any]:
    """
    加载 settings 文件夹下的配置
    """
    settings_path = project_root / "settings"
    print(f" Loading settings from: {settings_path}")

    data = {
        "profile_json": "{}",
        "house_layout": {},
        "house_details_map": {},
        "interaction_rules": []
    }

    # Profile
    if (settings_path / "profile.json").exists():
        with open(settings_path / "profile.json", 'r', encoding='utf-8') as f:
            data["profile_json"] = json.dumps(json.load(f), ensure_ascii=False, indent=2)

    # House Layout
    if (settings_path / "house_layout.json").exists():
        with open(settings_path / "house_layout.json", 'r', encoding='utf-8') as f:
            data["house_layout"] = json.load(f)

    # House Details (List -> Dict)
    if (settings_path / "house_details.json").exists():
        with open(settings_path / "house_details.json", 'r', encoding='utf-8') as f:
            details_list = json.load(f)
            for item in details_list:
                item_id = item.get("furniture_id") or item.get("device_id")
                if item_id:
                    data["house_details_map"][item_id] = item
    
    return data

def get_room_specific_context(full_layout: Dict, details_map: Dict, target_rooms: List[str]) -> Dict[str, Any]:
    """
    上下文裁剪：只提取相关房间的物品
    """
    room_list = list(full_layout.keys())
    filtered_details = {}
    
    # 过滤出存在于 layout 中的房间
    rooms_to_scan = [r for r in target_rooms if r in full_layout]
    
    # 如果没有匹配的房间（如 Outside 或数据错误），默认不提供物品详情，或可视情况提供 Living Room
    if not rooms_to_scan and "living_room" in full_layout:
        # 策略：如果完全匹配不到房间，不注入任何物品，避免干扰
        pass 

    for room_key in rooms_to_scan:
        room_struct = full_layout[room_key]
        furniture_ids = room_struct.get("furniture", [])
        device_ids = room_struct.get("devices", [])
        all_ids = furniture_ids + device_ids
        
        room_items = []
        for item_id in all_ids:
            if item_id in details_map:
                item_info = details_map[item_id]
                support_actions = item_info.get("support_actions", [])
                if not support_actions:
                    continue
                room_items.append({
                    "id": item_id,
                    "name": item_info.get("name", "Unknown"),
                    "support_actions": support_actions
                })
            else:
                continue
        
        filtered_details[room_key] = room_items

    return {
        "room_list_json": json.dumps(room_list, ensure_ascii=False),
        "furniture_details_json": json.dumps(filtered_details, ensure_ascii=False, indent=2)
    }

# ==========================================
# 4. LangGraph 状态与节点
# ==========================================

class EventState(TypedDict):
    resident_profile: str
    full_layout: Dict
    details_map: Dict
    current_activity: Dict
    previous_events: List[Dict]
    agent_state_json: str
    room_context_data: Dict
    current_events: Optional[EventSequence]
    validation_result: Optional[ValidationResult]
    revision_count: int
    environment_snapshot: Dict  # room_id -> {temperature, humidity, hygiene, last_update_ts}
    outdoor_weather: Dict       # {temperature, humidity} 室外
    device_states: Dict        # device_id -> {power, mode, ...} 全屋设备当前状态，用于物理闭环

# 极速 LLM，use_responses_api=False 以兼容 with_structured_output（与 settings/details2interaction 一致）
llm = create_fast_llm(
    model=DEFAULT_MODEL,
    temperature=EVENT_TEMPERATURE,
    use_responses_api=EVENT_USE_RESPONSES_API,
)

def _estimate_prompt_chars(template: str, variables: Dict[str, Any]) -> int:
    total = len(template or "")
    for val in variables.values():
        total += len(str(val))
    return total


def _default_room_state(ts: Any = None, light_level: float = 0.5) -> Dict[str, Any]:
    return {
        "temperature": 24.0,
        "humidity": 0.5,
        "hygiene": 0.7,
        "air_freshness": 0.7,
        "light_level": light_level,
        "last_update_ts": ts,
    }


def _room_state_from_layout_or_default(full_layout: Dict, room_id: str, ts: Any) -> Dict[str, Any]:
    """优先使用 layout 中该房间的 environment_state 作为初始物理状态，避免全屋从 24°C 等单一默认值起步。"""
    room_data = (full_layout or {}).get(room_id) or {}
    env = room_data.get("environment_state") or {}
    if isinstance(env, dict) and any(k in env for k in ("temperature", "humidity", "hygiene", "air_freshness")):
        state = dict(_default_room_state(ts))
        if "temperature" in env:
            state["temperature"] = float(env.get("temperature", 24.0))
        if "humidity" in env:
            h = env["humidity"]
            state["humidity"] = float(h) if 0 <= float(h) <= 1 else float(h) / 100.0
        if "hygiene" in env:
            state["hygiene"] = float(env.get("hygiene", 0.7))
        if "air_freshness" in env:
            state["air_freshness"] = float(env.get("air_freshness", 0.7))
        if "light_level" in env:
            state["light_level"] = float(env.get("light_level", 0.5))
        state["last_update_ts"] = ts
        return state
    return _default_room_state(ts)


def _build_active_devices_for_room(
    full_layout: Dict,
    device_states: Dict,
    room_id: str,
) -> List[Dict[str, Any]]:
    """根据 layout 和 device_states 构建该房间的 active_devices 列表，供物理引擎使用。"""
    room_data = full_layout.get(room_id) or {}
    device_ids = room_data.get("devices", [])
    return [
        {"device_id": did, "state": device_states.get(did, {})}
        for did in device_ids
    ]


def _format_snapshot_to_room_env_text(snapshot: Dict, target_rooms: List[str]) -> str:
    """将已有 snapshot 格式化为「当前房间环境」文本，不跑物理。用于迭代生成时本段起点环境。"""
    lines = []
    for room_id in target_rooms:
        if room_id == "Outside":
            continue
        state = snapshot.get(room_id) or {}
        t = state.get("temperature", 24.0)
        h = state.get("humidity", 0.5)
        hy = state.get("hygiene", 0.7)
        af = state.get("air_freshness", 0.7)
        lines.append(f"- **{room_id}**: 温度 {t}°C, 湿度 {h*100:.0f}%, 清洁度 {hy:.2f}, 空气清新度 {af:.2f}")
    return "\n".join(lines) if lines else "（当前活动无室内房间或为外出；无房间环境数据。）"


# 舒适范围默认值（当 profile 未提供时）
COMFORT_TEMP_LOW = 20.0
COMFORT_TEMP_HIGH = 26.0
COMFORT_HUMIDITY_LOW = 0.3
COMFORT_HUMIDITY_HIGH = 0.7
COMFORT_AIR_FRESHNESS_MIN = 0.5


def _evaluate_comfort_and_build_mandate(
    snapshot: Dict,
    target_rooms: List[str],
    resident_profile: Any,
) -> str:
    """
    在「先跑物理得到当前环境」之后调用：评估各房间是否超出舒适范围，
    若超出则生成「必须响应」的明确指令，供注入 prompt，让人物在本段中主动调节。
    resident_profile 可为 JSON 字符串或 dict，从中读取 preferences.home_temperature 等。
    """
    try:
        profile = resident_profile if isinstance(resident_profile, dict) else json.loads(resident_profile or "{}")
    except Exception:
        profile = {}
    prefs = profile.get("preferences") or {}
    preferred_temp = float(prefs.get("home_temperature", 22.0))
    temp_low = preferred_temp - 2.0
    temp_high = preferred_temp + 2.0
    h_low = COMFORT_HUMIDITY_LOW
    h_high = COMFORT_HUMIDITY_HIGH
    af_min = COMFORT_AIR_FRESHNESS_MIN
    hy_min = 0.5  # 清洁度低于此建议打扫

    mandates = []
    for room_id in target_rooms:
        if room_id == "Outside":
            continue
        state = snapshot.get(room_id) or {}
        t = state.get("temperature", 24.0)
        h = state.get("humidity", 0.5)
        af = state.get("air_freshness", 0.7)
        hy = state.get("hygiene", 0.7)

        need_act = []
        if t < temp_low:
            need_act.append(f"温度 {t}°C 偏低（舒适约 {preferred_temp}°C），请让人物主动开暖气或空调加热")
        elif t > temp_high:
            need_act.append(f"温度 {t}°C 偏高（舒适约 {preferred_temp}°C），请让人物主动开空调/风扇或开窗散热")
        if h < h_low:
            need_act.append(f"湿度过低（{h*100:.0f}%），可酌情加湿或开窗")
        elif h > h_high:
            need_act.append(f"湿度过高（{h*100:.0f}%），请让人物主动开窗或除湿")
        if af < af_min:
            need_act.append(f"空气清新度偏低（{af:.2f}），请让人物主动开窗或开空气净化器/抽油烟机")
        if hy < hy_min:
            need_act.append(f"清洁度偏低（{hy:.2f}），请考虑插入打扫/清洁事件以提升该房间清洁度")

        if need_act:
            mandates.append(f"- **{room_id}**：{'；'.join(need_act)}。**请在本段中首先生成人物主动调节设备的事件，并填写 device_patches**。")
    if not mandates:
        return "当前各房间环境在舒适范围内，无需强制调节；若有偏好可酌情微调。"
    return "**以下房间当前环境超出舒适范围，请在本段中首先生成人物主动调节设备的事件（开暖气/空调/窗/净化器等），并填写对应 device_patches：**\n" + "\n".join(mandates)


def _is_snapshot_still_out_of_comfort(
    snapshot: Dict,
    target_rooms: List[str],
    resident_profile: Any,
) -> tuple:
    """活动结束后的 snapshot 是否仍有房间超出舒适范围。返回 (是否仍不达标, 修正说明文案)。"""
    mandate = _evaluate_comfort_and_build_mandate(snapshot, target_rooms, resident_profile)
    still_bad = "以下房间当前环境超出舒适范围" in mandate or "清洁度偏低" in mandate
    return still_bad, mandate


def _update_room_environments_and_format(
    target_rooms: List[str],
    activity_start_time: str,
    environment_snapshot: Dict,
    outdoor_weather: Dict,
    details_map: Dict,
    full_layout: Dict,
    device_states: Dict,
) -> tuple:
    """
    懒更新：对 target_rooms 从上次时间推到 activity_start_time，返回 (updated_snapshot, current_room_environment_str)。
    将 device_states 转为 active_devices 传入物理引擎，实现设备状态闭环（如空调开着则温度下降）。
    """
    snapshot = dict(environment_snapshot)
    outdoor = outdoor_weather or {}
    lines = []
    for room_id in target_rooms:
        if room_id == "Outside":
            continue
        last_state = snapshot.get(room_id) or _room_state_from_layout_or_default(full_layout, room_id, activity_start_time)
        last_ts = last_state.get("last_update_ts") or activity_start_time
        active_devices = _build_active_devices_for_room(full_layout, device_states or {}, room_id)
        new_state = calculate_room_state(
            current_state=last_state,
            last_update_time=last_ts,
            current_time=activity_start_time,
            active_devices=active_devices,
            details_map=details_map,
            outdoor_weather=outdoor,
        )
        snapshot[room_id] = new_state
        lines.append(
            f"- **{room_id}**: 温度 {new_state['temperature']}°C, 湿度 {new_state['humidity']*100:.0f}%, 清洁度 {new_state['hygiene']:.2f}, 空气清新度 {new_state.get('air_freshness', 0.7):.2f}"
        )
    if not lines:
        text = "（当前活动无室内房间或为外出；无房间环境数据。）"
    else:
        text = "\n".join(lines)
    return snapshot, text


def _patch_entries_to_dict(patch: Any) -> Dict[str, str]:
    """将 patch 转为普通 dict。支持 List[DevicePatchEntry] 或 [{\"key\",\"value\"}] 或旧版 {\"power\":\"on\"}。"""
    if isinstance(patch, dict):
        return {k: str(v) for k, v in patch.items()}
    if isinstance(patch, list):
        out = {}
        for e in patch:
            if isinstance(e, dict) and "key" in e and "value" in e:
                out[str(e["key"])] = str(e["value"])
            elif hasattr(e, "key") and hasattr(e, "value"):
                out[str(e.key)] = str(e.value)
        return out
    return {}


def _normalize_device_patch(patch_dict: Dict[str, str]) -> Dict[str, str]:
    """统一 LLM 常用键与物理引擎/ house_details 的 working_condition 一致，使设备生效。"""
    if not patch_dict:
        return patch_dict
    out = dict(patch_dict)
    v = (out.get("turn_on") or out.get("power") or "").lower()
    if v == "on":
        out["power"] = "on"
    if (out.get("open") or "").lower() == "open":
        out["open"] = "open"
    if (out.get("state") or "").lower() == "open":
        out["open"] = "open"
    return out


def _apply_device_patches(device_states: Dict, events: List[Any]) -> None:
    """按事件顺序将 device_patches 合并到 device_states（原地修改）。"""
    for ev in events:
        if isinstance(ev, dict):
            patches = ev.get("device_patches", []) or []
        else:
            patches = getattr(ev, "device_patches", None) or []
        for p in patches:
            if isinstance(p, dict):
                did = p.get("device_id")
                patch = p.get("patch") or []
            else:
                did = getattr(p, "device_id", None)
                patch = getattr(p, "patch", None) or []
            if not did:
                continue
            patch_dict = _normalize_device_patch(_patch_entries_to_dict(patch))
            if patch_dict:
                device_states[did] = {**device_states.get(did, {}), **patch_dict}


# 活动对房间环境的影响（每分钟增量）。理想做法是由 house_details 的 environmental_regulation 等生成数据驱动，此处仅最小兜底保证物理推进可用。
def _get_activity_deltas_for_rooms(
    target_rooms: List[str],
    device_states: Dict,
    full_layout: Dict,
) -> Dict[str, Dict[str, float]]:
    """根据当前 device_states 推断各房间是否有烹饪/淋浴等，返回每房间的 activity_deltas_per_minute。"""
    out: Dict[str, Dict[str, float]] = {}
    for room_id in target_rooms:
        if room_id == "Outside":
            continue
        room_data = (full_layout or {}).get(room_id) or {}
        device_ids = room_data.get("devices", []) + room_data.get("furniture", [])
        cooking_on = False
        shower_on = False
        for did in device_ids:
            state = (device_states or {}).get(did) or {}
            if str(state.get("power")).lower() != "on":
                continue
            did_lower = (did or "").lower()
            if "oven" in did_lower or "induction" in did_lower or "cooktop" in did_lower or "stove" in did_lower:
                cooking_on = True
            if "shower" in did_lower or ("heater" in did_lower and room_id == "bathroom"):
                shower_on = True
        if room_id == "kitchen" and cooking_on:
            out[room_id] = {"temperature": 0.35, "humidity": 0.1, "air_freshness": -0.08}
        elif room_id == "bathroom" and shower_on:
            out[room_id] = {"humidity": 0.15, "air_freshness": -0.05}
    return out


def _advance_snapshot_to_activity_end(
    snapshot: Dict,
    activity_start_time: str,
    activity_end_time: str,
    target_rooms: List[str],
    device_states: Dict,
    full_layout: Dict,
    details_map: Dict,
    outdoor_weather: Dict,
    activity_deltas_per_room: Optional[Dict[str, Dict[str, float]]] = None,
) -> Dict:
    """从活动开始时间推进到活动结束时间，使用当前 device_states 与活动类型影响参与物理计算。"""
    result = dict(snapshot)
    outdoor = outdoor_weather or {}
    activity_deltas_per_room = activity_deltas_per_room or {}
    for room_id in target_rooms:
        if room_id == "Outside":
            continue
        last_state = result.get(room_id) or _room_state_from_layout_or_default(full_layout, room_id, activity_start_time)
        active_devices = _build_active_devices_for_room(full_layout, device_states or {}, room_id)
        activity_deltas = activity_deltas_per_room.get(room_id)
        new_state = calculate_room_state(
            current_state=last_state,
            last_update_time=activity_start_time,
            current_time=activity_end_time,
            active_devices=active_devices,
            details_map=details_map,
            outdoor_weather=outdoor,
            activity_deltas_per_minute=activity_deltas,
        )
        result[room_id] = new_state
    return result


# 超过此时长（小时）的活动按事件粒度更新 room_environment，避免长活动内所有事件共用同一快照
ROOM_ENV_PER_EVENT_THRESHOLD_HOURS = 1.0


def _advance_snapshot_through_events(
    snapshot: Dict[str, Dict],
    events: List[Dict],
    device_states: Dict,
    full_layout: Dict,
    details_map: Dict,
    outdoor_weather: Dict,
    target_rooms: List[str],
) -> Dict[str, Dict]:
    """
    按事件顺序推进物理：每事件先应用其 device_patches，再将该事件涉及房间从 last_ts 推进到 event.end_time。
    返回推进后的 snapshot（处于最后一条事件的 end_time），device_states 原地更新。
    用于「分段生成」时得到「本段事件结束后的环境」，作为下一段生成的 current_room_environment。
    """
    import copy
    result = {k: copy.deepcopy(v) for k, v in (snapshot or {}).items()}
    dev_states = device_states  # 原地更新
    outdoor = outdoor_weather or {}
    ordered = sorted(
        [e for e in events if (e.get("start_time") or e.get("end_time"))],
        key=lambda x: (x.get("start_time") or x.get("end_time") or ""),
    )
    for ev in ordered:
        st = ev.get("start_time") or ""
        et = ev.get("end_time") or st
        rid = ev.get("room_id")
        # 先应用本事件的 device_patches（设备在事件发生时改变）
        for p in ev.get("device_patches") or []:
            did = p.get("device_id")
            patch = p.get("patch") or []
            if not did:
                continue
            patch_dict = _normalize_device_patch(_patch_entries_to_dict(patch))
            if patch_dict:
                dev_states[did] = {**dev_states.get(did, {}), **patch_dict}
        # 再将该段结束时间 et 作为当前时刻，推进所有 target_rooms 的物理状态
        for room_id in target_rooms:
            if room_id == "Outside":
                continue
            last_state = result.get(room_id) or _room_state_from_layout_or_default(full_layout, room_id, st)
            last_ts = last_state.get("last_update_ts") or st
            active_devices = _build_active_devices_for_room(full_layout, dev_states, room_id)
            activity_deltas = _get_activity_deltas_for_rooms([room_id], dev_states, full_layout).get(room_id)
            new_state = calculate_room_state(
                current_state=last_state,
                last_update_time=last_ts,
                current_time=et,
                active_devices=active_devices,
                details_map=details_map,
                outdoor_weather=outdoor,
                activity_deltas_per_minute=activity_deltas,
            )
            result[room_id] = new_state
    return result


def _refine_room_environment_for_long_activity(
    snapshot_at_start: Dict[str, Dict],
    events: List[Dict],
    device_states_at_start: Dict,
    full_layout: Dict,
    details_map: Dict,
    outdoor_weather: Dict,
    activity_start_time: str,
    activity_end_time: str,
    target_rooms: List[str],
) -> None:
    """对长活动内的事件逐事件推进物理并写入 room_environment（原地修改 events）。"""
    import copy
    snapshot = {k: copy.deepcopy(v) for k, v in (snapshot_at_start or {}).items()}
    device_states = copy.deepcopy(device_states_at_start or {})
    outdoor = outdoor_weather or {}
    ordered = sorted([e for e in events if e.get("room_id") and e.get("room_id") != "Outside"], key=lambda x: (x.get("start_time") or ""))
    for ev in ordered:
        rid = ev.get("room_id")
        start_time = ev.get("start_time") or activity_start_time
        last_state = snapshot.get(rid) or _room_state_from_layout_or_default(full_layout, rid, start_time)
        last_ts = last_state.get("last_update_ts") or activity_start_time
        active_devices = _build_active_devices_for_room(full_layout, device_states, rid)
        activity_deltas = _get_activity_deltas_for_rooms([rid], device_states, full_layout).get(rid)
        new_state = calculate_room_state(
            current_state=last_state,
            last_update_time=last_ts,
            current_time=start_time,
            active_devices=active_devices,
            details_map=details_map,
            outdoor_weather=outdoor,
            activity_deltas_per_minute=activity_deltas,
        )
        snapshot[rid] = new_state
        ev["room_environment"] = {
            "temperature": new_state.get("temperature"),
            "humidity": new_state.get("humidity"),
            "hygiene": new_state.get("hygiene"),
            "air_freshness": new_state.get("air_freshness", 0.7),
            "light_level": new_state.get("light_level", 0.5),
        }
        for p in ev.get("device_patches") or []:
            did = p.get("device_id")
            patch = p.get("patch") or []
            if not did:
                continue
            patch_dict = _normalize_device_patch(_patch_entries_to_dict(patch))
            if patch_dict:
                device_states[did] = {**device_states.get(did, {}), **patch_dict}


def _sanitize_events(events: List[EventItem], full_layout: Dict) -> None:
    room_item_map = {}
    for room_id, room_data in full_layout.items():
        furniture_ids = room_data.get("furniture", [])
        device_ids = room_data.get("devices", [])
        room_item_map[room_id] = set(furniture_ids + device_ids)

    for evt in events:
        room_id = evt.room_id
        if room_id == "Outside":
            evt.target_object_ids = []
            evt.action_type = "outside"
            continue
        if room_id not in room_item_map:
            evt.room_id = "Outside"
            evt.target_object_ids = []
            evt.action_type = "outside"
            continue
        valid_ids = room_item_map[room_id]
        evt.target_object_ids = [obj_id for obj_id in evt.target_object_ids if obj_id in valid_ids]


def _is_retryable_llm_error(e: Exception) -> bool:
    """判断是否为可重试的 LLM 调用错误（连接、SSL、超时、限流、5xx）。"""
    def msg_and_cause(exc: Exception) -> str:
        out = str(exc).lower()
        c = getattr(exc, "__cause__", None)
        if c:
            out += " " + str(c).lower()
        return out

    err_msg = msg_and_cause(e)
    if any(k in err_msg for k in (
        "connection", "timeout", "timed out", "reset",
        "ssl", "eof", "unexpected_eof", "protocol", "tls"
    )):
        return True
    if "503" in err_msg or "502" in err_msg or "504" in err_msg or "429" in err_msg:
        return True
    try:
        import openai
        if isinstance(e, openai.APIConnectionError):
            return True
        if hasattr(openai, "APIStatusError") and isinstance(e, openai.APIStatusError):
            if getattr(e, "status_code", None) in (429, 502, 503, 504):
                return True
    except Exception:
        pass
    try:
        import httpx
        if isinstance(e, httpx.ConnectError):
            return True
        c = getattr(e, "__cause__", None)
        if c is not None and isinstance(c, httpx.ConnectError):
            return True
    except Exception:
        pass
    try:
        import httpcore
        c = e
        for _ in range(5):
            if c is None:
                break
            if type(c).__name__ == "ConnectError" or (
                getattr(httpcore, "ConnectError", None) is not None and isinstance(c, httpcore.ConnectError)
            ):
                return True
            c = getattr(c, "__cause__", None)
    except Exception:
        pass
    return False


def _invoke_chain_with_retry(chain, inputs: Dict[str, Any], label: str = "LLM"):
    """对单次 chain.invoke 做内层重试，吸收瞬时连接/5xx 错误。"""
    last_exc = None
    for attempt in range(INNER_LLM_RETRY_COUNT + 1):
        try:
            return chain.invoke(inputs)
        except Exception as e:
            last_exc = e
            if attempt < INNER_LLM_RETRY_COUNT and _is_retryable_llm_error(e):
                delay = INNER_LLM_RETRY_DELAY * (attempt + 1)
                logger.warning(
                    "[%s] 第 %d/%d 次调用失败（可重试）: %s，%ds 后重试...",
                    label, attempt + 1, INNER_LLM_RETRY_COUNT + 1, e, delay
                )
                time.sleep(delay)
            else:
                raise
    raise last_exc


def generate_events_node(state: EventState):
    activity_name = state['current_activity'].get('activity_name', 'Unknown')
    logger.info(f" [Step 1] Decomposing Activity: {activity_name} ...")
    
    # 1. 裁剪上下文
    target_rooms = state["current_activity"].get("main_rooms", [])
    context_data = get_room_specific_context(
        state["full_layout"], 
        state["details_map"], 
        target_rooms
    )
    
    # 2. 懒更新房间环境并生成「当前房间环境」描述（带入 device_states 做物理闭环）
    activity_start = state["current_activity"].get("start_time", "")
    activity_end = state["current_activity"].get("end_time", activity_start)
    snapshot = state.get("environment_snapshot") or {}
    outdoor = state.get("outdoor_weather") or {}
    details_map = state.get("details_map") or {}
    full_layout = state.get("full_layout") or {}
    device_states = dict(state.get("device_states") or {})
    updated_snapshot, room_env_text = _update_room_environments_and_format(
        target_rooms, activity_start, snapshot, outdoor, details_map, full_layout, device_states
    )
    env_note = "\n**说明**：居民档案（含 preferences 等）已在上方提供。是否插入调节事件、插入何种事件，请根据档案中的偏好与当前房间环境综合判断，由你根据常识与性格推断。"

    # 3. 调用 LLM（迭代：每段生成后物理推进，下一段基于新环境；非迭代：一次性生成）
    prompt = ChatPromptTemplate.from_template(EVENT_GENERATION_PROMPT_TEMPLATE)
    structured_llm = llm.with_structured_output(EventSequence, method="json_schema", strict=True)
    chain = prompt | structured_llm

    activity_str = json.dumps(state["current_activity"], ensure_ascii=False)
    prev_events_str = json.dumps(state["previous_events"][-2:], ensure_ascii=False) if state["previous_events"] else "[]"

    if USE_ITERATIVE_EVENT_GENERATION:
        import copy
        current_time = activity_start
        seg_snapshot = copy.deepcopy(updated_snapshot)
        seg_device_states = copy.deepcopy(device_states)
        all_events: List[EventItem] = []
        segment_index = 0
        while current_time < activity_end:
            segment_index += 1
            # 先物理：本段起点环境由物理引擎推进后的 seg_snapshot 得到；再评估是否超出舒适并生成「必须调节」指令
            room_env_text = _format_snapshot_to_room_env_text(seg_snapshot, target_rooms) + env_note
            comfort_mandate = _evaluate_comfort_and_build_mandate(seg_snapshot, target_rooms, state.get("resident_profile") or "{}")
            room_env_text += "\n\n**环境评估与必须响应**：\n" + comfort_mandate
            logger.info("Event segment env (passed to LLM): %s", (room_env_text[:200] + "..." if len(room_env_text) > 200 else room_env_text))
            events_so_far = [e.model_dump() for e in all_events]
            segment_instruction = (
                " **本段生成**：当前时刻为 " + current_time + "。请从该时刻起生成事件，首条事件 start_time 必须等于当前时刻；"
                "连续生成直至活动结束或本段约 20–30 分钟。上方「当前房间环境」为该时刻**先跑物理引擎**得到的真实数据；"
                "若「环境评估与必须响应」中列出某房间超出舒适范围，请在本段中**首先生成**人物主动调节设备的事件，并填写 device_patches。"
                "人物在本段的设备操作（开暖气/开窗/净化器等）会在**同一活动内**即时参与物理计算，下一段将看到调节后的环境。"
                "已生成事件（供衔接）：" + json.dumps(events_so_far, ensure_ascii=False)
            )
            print(f"  [LLM] Generating events segment {segment_index} from {current_time}...", flush=True)
            result = _invoke_chain_with_retry(chain, {
                "event_requirements": EVENT_REQUIREMENTS,
                "resident_profile_json": state["resident_profile"],
                "agent_state_json": state.get("agent_state_json", "{}"),
                "room_list_json": context_data["room_list_json"],
                "furniture_details_json": context_data["furniture_details_json"],
                "current_room_environment": room_env_text,
                "current_activity_json": activity_str,
                "context_size": 5,
                "previous_events_context": prev_events_str,
                "segment_instruction": segment_instruction,
            }, label="event_generate_segment")
            if not result.events:
                logger.warning("Segment %d: LLM 返回空事件列表，退出迭代。", segment_index)
                break
            last_ev = result.events[-1]
            prev_time = current_time
            current_time = last_ev.end_time
            # 打印本段时间推进情况，便于排查「一直重复」死循环
            logger.info(
                "Segment %d 时间推进: 本段起点=%s, 本段最后事件 end_time=%s, activity_end=%s -> 下一段起点=%s, 是否结束=%s",
                segment_index, prev_time, last_ev.end_time, activity_end, current_time, current_time >= activity_end
            )
            if current_time <= prev_time:
                logger.warning(
                    "本段未推进时间：last event end_time=%s <= 本段起点=%s，会导致死循环。强制前进 20 分钟。",
                    last_ev.end_time, prev_time
                )
                try:
                    t = datetime.fromisoformat(prev_time.replace("Z", "+00:00")) + timedelta(minutes=20)
                    current_time = t.strftime("%Y-%m-%dT%H:%M:%S")
                    if current_time >= activity_end:
                        current_time = activity_end
                except Exception as e:
                    logger.warning("强制前进时间解析失败: %s，直接设为 activity_end。", e)
                    current_time = activity_end
            all_events.extend(result.events)
            # 本段人物的设备调节（device_patches）立即生效：先写入 device_states，再参与物理推进；
            # 下一段起点环境 = 本段结束时刻的物理状态，故人物主观能动性会在同一 activity 内后续时间真实影响环境。
            _apply_device_patches(seg_device_states, [e.model_dump() for e in result.events])
            seg_snapshot = _advance_snapshot_through_events(
                seg_snapshot,
                [e.model_dump() for e in result.events],
                seg_device_states,
                full_layout,
                details_map,
                outdoor,
                target_rooms,
            )
            if current_time >= activity_end:
                break
            # 硬上限：单活动最多迭代段数，防止异常时无限循环
            if segment_index >= 20:
                logger.warning("已达到单活动最多 20 段，强制结束迭代，避免无限循环。")
                break
        result = EventSequence(events=all_events)
        device_states = seg_device_states
        snapshot_at_end = seg_snapshot
        last_ts = all_events[-1].end_time if all_events else activity_start
        if last_ts < activity_end:
            activity_deltas_per_room = _get_activity_deltas_for_rooms(target_rooms, device_states, full_layout)
            snapshot_at_end = _advance_snapshot_to_activity_end(
                seg_snapshot,
                last_ts,
                activity_end,
                target_rooms,
                device_states,
                full_layout,
                details_map,
                outdoor,
                activity_deltas_per_room=activity_deltas_per_room,
            )
    else:
        room_env_text += env_note
        # 先物理得到环境后，评估是否超出舒适并注入必须调节指令
        comfort_mandate = _evaluate_comfort_and_build_mandate(updated_snapshot, target_rooms, state.get("resident_profile") or "{}")
        room_env_text += "\n\n**环境评估与必须响应**：\n" + comfort_mandate
        segment_instruction = ""
        # 一次性生成时 current_room_environment 为活动开始时刻先跑物理得到的环境，再叠加「必须响应」指令
        logger.info("Event one-shot env (passed to LLM): %s", (room_env_text[:200] + "..." if len(room_env_text) > 200 else room_env_text))
        print("  [LLM] Generating events (may take 10-60s)...", flush=True)
        result = _invoke_chain_with_retry(chain, {
            "event_requirements": EVENT_REQUIREMENTS,
            "resident_profile_json": state["resident_profile"],
            "agent_state_json": state.get("agent_state_json", "{}"),
            "room_list_json": context_data["room_list_json"],
            "furniture_details_json": context_data["furniture_details_json"],
            "current_room_environment": room_env_text,
            "current_activity_json": activity_str,
            "context_size": 5,
            "previous_events_context": prev_events_str,
            "segment_instruction": segment_instruction,
        }, label="event_generate")
        try:
            vars_for_count = {
                "event_requirements": EVENT_REQUIREMENTS,
                "resident_profile_json": state["resident_profile"],
                "agent_state_json": state.get("agent_state_json", "{}"),
                "room_list_json": context_data["room_list_json"],
                "furniture_details_json": context_data["furniture_details_json"],
                "current_room_environment": room_env_text,
                "current_activity_json": activity_str,
                "context_size": 5,
                "previous_events_context": prev_events_str,
                "segment_instruction": segment_instruction,
            }
            chars = _estimate_prompt_chars(EVENT_GENERATION_PROMPT_TEMPLATE, vars_for_count)
            logger.info(f"LLM input size (event generate): ~{chars} chars (~{chars//4} tokens)")
        except Exception:
            pass
        _sanitize_events(result.events, state["full_layout"])
        activity_deltas_per_room = _get_activity_deltas_for_rooms(target_rooms, device_states, full_layout)
        _apply_device_patches(device_states, result.events)
        snapshot_at_end = _advance_snapshot_to_activity_end(
            updated_snapshot,
            activity_start,
            activity_end,
            target_rooms,
            device_states,
            full_layout,
            details_map,
            outdoor,
            activity_deltas_per_room=activity_deltas_per_room,
        )

    _sanitize_events(result.events, state["full_layout"])

    return {
        "current_events": result,
        "room_context_data": context_data,
        "revision_count": 0,
        "environment_snapshot": snapshot_at_end,
        "environment_snapshot_at_activity_start": updated_snapshot,
        "device_states": device_states,
    }

def validate_events_node(state: EventState):
    logger.info(" [Step 2] Validating Events...")
    prompt = ChatPromptTemplate.from_template(EVENT_VALIDATION_PROMPT_TEMPLATE)
    structured_llm = llm.with_structured_output(ValidationResult, method="json_schema", strict=True)
    chain = prompt | structured_llm
    
    events_json = state["current_events"].model_dump_json()
    activity_str = json.dumps(state["current_activity"], ensure_ascii=False)
    layout_summary = state["room_context_data"]["furniture_details_json"]
    
    print("  [LLM] Validating events (may take 5-30s)...", flush=True)
    result = _invoke_chain_with_retry(chain, {
        "event_requirements": EVENT_REQUIREMENTS,
        "house_layout_summary": layout_summary,
        "current_activity_json": activity_str,
        "agent_state_json": state.get("agent_state_json", "{}"),
        "events_json": events_json
    }, label="event_validate")
    try:
        vars_for_count = {
            "event_requirements": EVENT_REQUIREMENTS,
            "house_layout_summary": layout_summary,
            "current_activity_json": activity_str,
            "agent_state_json": state.get("agent_state_json", "{}"),
            "events_json": events_json,
        }
        chars = _estimate_prompt_chars(EVENT_VALIDATION_PROMPT_TEMPLATE, vars_for_count)
        logger.info(f"LLM input size (event validate): ~{chars} chars (~{chars//4} tokens)")
    except Exception:
        pass

    
    # 硬校验：零时长事件（start_time == end_time）
    try:
        for i, ev in enumerate(state["current_events"].events):
            if ev.start_time == ev.end_time:
                result.is_valid = False
                msg = f"硬校验失败：事件[{i}] 零时长 (start_time == end_time == {ev.start_time})。end_time 至少延后 30 秒。"
                result.correction_content = (msg + " " + (result.correction_content or "")) if result.correction_content else msg
                break
    except Exception:
        pass

    # 硬校验：同一 activity 内连续事件时间空洞（prev.end_time != next.start_time）
    if result.is_valid:
        try:
            events = state["current_events"].events
            for i in range(len(events) - 1):
                if events[i].activity_id == events[i + 1].activity_id and events[i].end_time != events[i + 1].start_time:
                    result.is_valid = False
                    result.correction_content = (
                        f"硬校验失败：同一活动内事件[{i}].end_time ({events[i].end_time}) 与 事件[{i+1}].start_time ({events[i+1].start_time}) 存在空洞，必须连续或插入过渡事件。"
                        + (" " + result.correction_content) if result.correction_content else ""
                    )
                    break
        except Exception:
            pass

    # 环境校验：按物理引擎推进后的 snapshot 检查是否仍超出舒适范围，若仍不达标则要求修正（最多与逻辑修正共用 MAX_EVENT_REVISIONS 次）
    if result.is_valid:
        target_rooms = state["current_activity"].get("main_rooms") or []
        snap = state.get("environment_snapshot") or {}
        still_bad, env_mandate = _is_snapshot_still_out_of_comfort(
            snap, target_rooms, state.get("resident_profile") or "{}"
        )
        if still_bad:
            result.is_valid = False
            result.correction_content = (
                "环境仍不达标（经物理引擎推进后）：" + env_mandate
                + " 请增加或修改调节设备的事件（开窗/开净化器/抽油烟机/暖气等），并填写 device_patches。"
            )
            logger.warning("[FAIL] Environment check: snapshot still out of comfort, requesting correction.")
    if result.is_valid:
        logger.info("[OK] Validation Passed!")
    else:
        logger.warning(f"[FAIL] Validation Failed: {result.correction_content[:100] if result.correction_content else ''}...")
    return {"validation_result": result}

def correct_events_node(state: EventState):
    import copy
    logger.info(f"[Step 3] Correcting Events (Attempt {state['revision_count'] + 1})...")
    prompt = ChatPromptTemplate.from_template(EVENT_CORRECTION_PROMPT_TEMPLATE)
    structured_llm = llm.with_structured_output(EventSequence, method="json_schema", strict=True)
    chain = prompt | structured_llm

    events_json = state["current_events"].model_dump_json()
    activity_str = json.dumps(state["current_activity"], ensure_ascii=False)
    layout_summary = state["room_context_data"]["furniture_details_json"]

    print("  [LLM] Correcting events (may take 10-40s)...", flush=True)
    result = _invoke_chain_with_retry(chain, {
        "event_requirements": EVENT_REQUIREMENTS,
        "resident_profile_json": state["resident_profile"],
        "furniture_details_json": layout_summary,
        "current_activity_json": activity_str,
        "agent_state_json": state.get("agent_state_json", "{}"),
        "original_events_json": events_json,
        "correction_content": state["validation_result"].correction_content
    }, label="event_correct")
    try:
        vars_for_count = {
            "event_requirements": EVENT_REQUIREMENTS,
            "resident_profile_json": state["resident_profile"],
            "furniture_details_json": layout_summary,
            "current_activity_json": activity_str,
            "agent_state_json": state.get("agent_state_json", "{}"),
            "original_events_json": events_json,
            "correction_content": state["validation_result"].correction_content,
        }
        chars = _estimate_prompt_chars(EVENT_CORRECTION_PROMPT_TEMPLATE, vars_for_count)
        logger.info(f"LLM input size (event correct): ~{chars} chars (~{chars//4} tokens)")
    except Exception:
        pass

    _sanitize_events(result.events, state["full_layout"])
    # 用修正后的事件重新跑物理，更新 snapshot 与 device_states，供下一轮 validate 做环境校验
    target_rooms = state["current_activity"].get("main_rooms") or []
    activity_start = state["current_activity"].get("start_time", "")
    activity_end = state["current_activity"].get("end_time", activity_start)
    snap_start = copy.deepcopy(state.get("environment_snapshot_at_activity_start") or {})
    dev_states = copy.deepcopy(state.get("device_states") or {})
    outdoor = state.get("outdoor_weather") or {}
    full_layout = state.get("full_layout") or {}
    details_map = state.get("details_map") or {}
    snap_end = _advance_snapshot_through_events(
        snap_start,
        [e.model_dump() for e in result.events],
        dev_states,
        full_layout,
        details_map,
        outdoor,
        target_rooms,
    )
    last_ts = result.events[-1].end_time if result.events else activity_start
    if last_ts < activity_end:
        activity_deltas_per_room = _get_activity_deltas_for_rooms(target_rooms, dev_states, full_layout)
        snap_end = _advance_snapshot_to_activity_end(
            snap_end, last_ts, activity_end, target_rooms, dev_states, full_layout, details_map, outdoor,
            activity_deltas_per_room=activity_deltas_per_room,
        )
    return {
        "current_events": result,
        "revision_count": state["revision_count"] + 1,
        "environment_snapshot": snap_end,
        "device_states": dev_states,
    }

def router(state: EventState):
    if state["validation_result"].is_valid:
        return "end"
    if state["revision_count"] >= MAX_EVENT_REVISIONS:
        logger.error("[WARN] Max revisions reached. Skipping this activity.")
        return "end"
    return "correct"

# 构建 Graph
workflow = StateGraph(EventState)
workflow.add_node("generate", generate_events_node)
workflow.add_node("validate", validate_events_node)
workflow.add_node("correct", correct_events_node)
workflow.set_entry_point("generate")
workflow.add_edge("generate", "validate")
workflow.add_conditional_edges("validate", router, {"end": END, "correct": "correct"})
workflow.add_edge("correct", "validate")
app = workflow.compile()

# ==========================================
# 5. 主程序运行 (批量处理 Loop)
# ==========================================

def run_batch_processing(
    activities_list: Optional[List[Dict]] = None,
    cached_settings: Optional[Dict[str, Any]] = None,
    initial_environment_snapshot: Optional[Dict[str, Any]] = None,
    initial_device_states: Optional[Dict[str, Dict[str, str]]] = None,
):
    project_root = Path(__file__).resolve().parent.parent

    # 1. 加载 Settings（优先用缓存，避免 14 天循环内重复读盘）
    if cached_settings is not None:
        settings = cached_settings
    else:
        settings = load_settings_data(project_root)
    if not settings.get("house_details_map"):
            logger.warning("[WARN] House Details is empty!")
    agent_state_json = "{}"
    sim_context_path = project_root / "data" / "simulation_context.json"
    if sim_context_path.exists():
        try:
            with open(sim_context_path, "r", encoding="utf-8") as f:
                sim_ctx = json.load(f)
            agent_state = sim_ctx.get("agent_state", {})
            agent_state_json = json.dumps(agent_state, ensure_ascii=False, indent=2)
        except Exception:
            agent_state_json = "{}"

    # 2. 加载 Activity Data
    if activities_list is None:
        activity_file = project_root / "data" / "activity.json"
        if not activity_file.exists():
            logger.error(f"[ERROR] Activity file not found: {activity_file}")
            return
    
        with open(activity_file, 'r', encoding='utf-8') as f:
            activity_data = json.load(f)
            activities_list = activity_data.get("activities", [])

    print(f"\n Starting Batch Processing for {len(activities_list)} activities...\n")
    if SKIP_EVENT_VALIDATION:
        print("[FAST] SIM_SKIP_EVENT_VALIDATION=1: 跳过校验/修正，每活动仅 1 次生成，提速明显。\n")

    all_generated_events = []
    context_events_buffer = []
    # 用上一日结束时的房间环境与设备状态做初值（多日一致）；无则用 house_layout 的 environment_state
    full_layout = settings.get("house_layout") or {}
    layout_room_default = {}
    for room_id, room_data in full_layout.items():
        es = room_data.get("environment_state") or {}
        layout_room_default[room_id] = {
            "temperature": es.get("temperature", 24.0),
            "humidity": es.get("humidity", 0.5),
            "hygiene": es.get("hygiene", 0.7),
            "air_freshness": es.get("air_freshness", 0.7),
            "light_level": es.get("light_level", 0.5),
            "last_update_ts": None,
        }
    if initial_environment_snapshot:
        environment_snapshot = {k: dict(v) for k, v in initial_environment_snapshot.items()}
        for rid, default in layout_room_default.items():
            if rid not in environment_snapshot:
                environment_snapshot[rid] = dict(default)
        logger.info("[INIT] Day 使用上一日结束时的 environment_snapshot 作为初值（共 %d 房间）。", len(environment_snapshot))
    else:
        environment_snapshot = {k: dict(v) for k, v in layout_room_default.items()}
    snapshot_at_activity_start = {}  # activity_id -> { room_id -> {temperature, humidity, hygiene, ...} } 用于写入输出，体现「推理时用的环境」
    outdoor_weather = {}
    if sim_context_path.exists():
        try:
            with open(sim_context_path, "r", encoding="utf-8") as f:
                sim_ctx = json.load(f)
            outdoor_weather = sim_ctx.get("outdoor_weather") or {}
        except Exception:
            pass
    if not outdoor_weather:
        try:
            from weather import fetch_openweather
            outdoor_weather = fetch_openweather()
        except Exception:
            pass
    if not outdoor_weather:
        outdoor_weather = {"temperature": 24.0, "humidity": 0.5}

    # 设备状态跟踪：有上一日结束时状态则沿用，否则从 house_details 初始化
    details_map = settings.get("house_details_map") or {}
    if initial_device_states:
        device_states = {did: dict(state) for did, state in initial_device_states.items()}
        for room_data in full_layout.values():
            for did in room_data.get("devices", []):
                if did not in device_states and did in details_map:
                    device_states[did] = dict(details_map[did].get("current_state") or {})
        logger.info("[INIT] Day 使用上一日结束时的 device_states 作为初值（共 %d 设备）。", len(initial_device_states))
    else:
        device_states = {}
        for room_data in full_layout.values():
            for did in room_data.get("devices", []):
                if did not in device_states and did in details_map:
                    device_states[did] = dict(details_map[did].get("current_state") or {})

    def _process_one(index: int, activity: Dict, prev_events: List[Dict], env_snapshot: Dict, dev_states: Dict):
        if len(activity["start_time"]) == 5:
            activity["start_time"] = f"{activity['start_time']}:00"
        if len(activity["end_time"]) == 5:
            activity["end_time"] = f"{activity['end_time']}:00"

        state = {
            "resident_profile": settings["profile_json"],
            "full_layout": settings["house_layout"],
            "details_map": settings["house_details_map"],
            "current_activity": activity,
            "previous_events": prev_events,
            "agent_state_json": agent_state_json,
            "revision_count": 0,
            "environment_snapshot": env_snapshot,
            "outdoor_weather": outdoor_weather,
            "device_states": dev_states,
        }

        if SKIP_EVENT_VALIDATION:
            gen_result = generate_events_node(state)
            if gen_result.get("current_events"):
                new_events = gen_result["current_events"].model_dump()["events"]
                snap_start = gen_result.get("environment_snapshot_at_activity_start") or gen_result.get("environment_snapshot") or env_snapshot
                return index, activity, new_events, None, gen_result.get("environment_snapshot") or env_snapshot, gen_result.get("device_states") or dev_states, snap_start
            return index, activity, None, "no_events", env_snapshot, dev_states, env_snapshot

        final_state = app.invoke(state)
        if final_state.get("current_events"):
            new_events = final_state["current_events"].model_dump()["events"]
            upd = final_state.get("environment_snapshot") or env_snapshot
            snap_start = final_state.get("environment_snapshot_at_activity_start") or upd
            return index, activity, new_events, None, upd, final_state.get("device_states") or dev_states, snap_start
        return index, activity, None, "no_events", env_snapshot, dev_states, env_snapshot

    for index, activity in enumerate(activities_list):
        print(f"--- Processing [{index+1}/{len(activities_list)}]: {activity['activity_name']} ---", flush=True)
        last_exc = None
        for attempt in range(LLM_RETRY_COUNT + 1):
            try:
                idx, act, new_events, err, updated_snapshot, updated_device_states, snap_at_start = _process_one(
                    index, activity, context_events_buffer, environment_snapshot, device_states
                )
                if err or not new_events:
                    logger.error(f"[ERROR] Failed to generate events for {activity['activity_name']}")
                    break
                aid = act.get("activity_id", "")
                if aid and snap_at_start:
                    snapshot_at_activity_start[aid] = {k: dict(v) for k, v in (snap_at_start or {}).items()}
                # 长活动（>1h）按事件粒度更新 room_environment，使「环境逐渐变化→触发调节」可学习
                try:
                    start_t = act.get("start_time") or ""
                    end_t = act.get("end_time") or ""
                    if start_t and end_t:
                        from datetime import datetime
                        t0 = datetime.fromisoformat(start_t.replace("Z", "+00:00"))
                        t1 = datetime.fromisoformat(end_t.replace("Z", "+00:00"))
                        dur_h = (t1 - t0).total_seconds() / 3600.0
                        if dur_h >= ROOM_ENV_PER_EVENT_THRESHOLD_HOURS:
                            _refine_room_environment_for_long_activity(
                                snap_at_start, new_events, device_states,
                                settings.get("house_layout") or {}, settings.get("house_details_map") or {},
                                outdoor_weather, start_t, end_t, act.get("main_rooms") or [],
                            )
                except Exception as _e:
                    pass
                environment_snapshot.update(updated_snapshot or {})
                if updated_device_states:
                    device_states.update(updated_device_states)
                # 仅更新本活动涉及房间的 last_update_ts；未访问房间保持上次的 last_update_ts，下次进入时物理引擎会按完整 dt 推进（室外+设备持续影响）
                for room_id in act.get("main_rooms") or []:
                    if room_id == "Outside":
                        continue
                    if room_id not in environment_snapshot:
                        environment_snapshot[room_id] = _default_room_state(act["end_time"])
                    environment_snapshot[room_id]["last_update_ts"] = act["end_time"]
                all_generated_events.extend(new_events)
                context_events_buffer = new_events[-5:]
                print(f"[OK] Generated {len(new_events)} events for {activity['activity_name']}.", flush=True)
                last_exc = None
                break
            except Exception as e:
                last_exc = e
                err_msg = str(e).lower()
                is_retryable = (
                    "timeout" in err_msg or "timed out" in err_msg
                    or "connection" in err_msg or "reset" in err_msg
                    or "503" in err_msg or "502" in err_msg or "504" in err_msg
                )
                if attempt < LLM_RETRY_COUNT and is_retryable:
                    logger.warning(
                        f"[RETRY] Attempt {attempt + 1}/{LLM_RETRY_COUNT + 1} failed for {activity['activity_name']}: {e}. "
                        f"Waiting {LLM_RETRY_DELAY}s then retry..."
                    )
                    time.sleep(LLM_RETRY_DELAY)
                else:
                    if attempt >= LLM_RETRY_COUNT and is_retryable:
                        logger.error(
                            f"[ERROR] All {LLM_RETRY_COUNT + 1} attempts failed (timeout/network) for {activity['activity_id']}. Skipping this activity."
                        )
                        err_lower = str(e).lower()
                        if "ssl" in err_lower or "eof" in err_lower or "proxy" in err_lower:
                            logger.info(
                                "[HINT] 若使用代理，可尝试临时取消 HTTP_PROXY/HTTPS_PROXY 或更换网络后再运行。"
                            )
                    else:
                        logger.error(f"[ERROR] Error processing activity {activity['activity_id']}: {e}")
                    import traceback
                    traceback.print_exc()
                    break

    # 校验：每个 activity 至少有一条 event（严重遗漏会导致约 2 小时等工作时段无事件数据）
    activity_ids_with_events = {ev.get("activity_id") for ev in all_generated_events if ev.get("activity_id")}
    for act in activities_list:
        aid = act.get("activity_id")
        if aid and aid not in activity_ids_with_events:
            logger.error(
                f"[ERROR] activity_id '{aid}' ({act.get('activity_name', '')}) 没有任何对应 events，"
                "请检查事件生成是否失败或跳过，并重新运行或修正。"
            )

    # 3. 为每个 event 附加「推理时该房间的环境」；长活动已在上面按事件粒度写入，此处仅补全未设置的
    for ev in all_generated_events:
        if ev.get("room_environment") is not None:
            continue
        aid = ev.get("activity_id")
        rid = ev.get("room_id")
        if aid and rid and rid != "Outside":
            snap = snapshot_at_activity_start.get(aid, {}).get(rid)
            if snap:
                ev["room_environment"] = {
                    "temperature": snap.get("temperature"),
                    "humidity": snap.get("humidity"),
                    "hygiene": snap.get("hygiene"),
                    "air_freshness": snap.get("air_freshness", 0.7),
                    "light_level": snap.get("light_level", 0.5),
                }

    # 4. 保存事件 + 按活动的环境快照（方便核对「生成该活动时用的环境」）
    output_file = project_root / "data" / "events.json"
    payload = {
        "events": all_generated_events,
        "meta": {
            "environment_by_activity": snapshot_at_activity_start,
            "note": "environment_by_activity: 每个活动开始时各房间的温度/湿度/清洁度，用于 event 生成推理；每个 event 的 room_environment 为该事件所在房间的该时刻环境。",
        },
    }
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    # 返回当日结束时的房间环境与设备状态，供多日仿真中下一日作为初值使用（保证 Day2+ 初始/最终环境一致）
    result = {
        "final_environment_snapshot": {k: dict(v) for k, v in environment_snapshot.items()},
        "final_device_states": {did: dict(state) for did, state in device_states.items()},
    }
    print(f"\n All done! Total {len(all_generated_events)} events generated.")
    print(f" Result saved to: {output_file}")
    return result

if __name__ == "__main__":
    run_batch_processing()
