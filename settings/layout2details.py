import sys
from pathlib import Path

_current_dir = Path(__file__).resolve().parent
_project_root = _current_dir.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from copy import deepcopy
from dotenv import load_dotenv
from typing import List, Dict, Any, Union, Optional

from llm_utils import create_fast_llm
from agent_config import (
    DETAILS_MODEL,
    DETAILS_REASONING_EFFORT,
    DETAILS_REQUEST_TIMEOUT,
    DETAILS_ROOM_RETRY_COUNT,
    SETTINGS_DEFAULT_TEMPERATURE,
    MAX_WORKERS_DEFAULT,
)
from prompt import (
    LAYOUT2DETAILS_ROOM_PROMPT_TEMPLATE,
    LAYOUT2DETAILS_SINGLE_ITEM_PROMPT_TEMPLATE,
    DETAILS_VALIDATION_PROMPT_TEMPLATE,
    DETAILS_CORRECTION_PROMPT_TEMPLATE,
    EVENT_UNIVERSAL_ACTIONS,
)
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

# --- 环境配置 ---
load_dotenv()
dotenv_path = _project_root / ".env"
load_dotenv(dotenv_path=dotenv_path)

# ==========================================
# 1. 定义数据结构 (Pydantic Schema)
# ==========================================

# --- 环境调节能力（扁平，供物理引擎使用；working_condition 可选以避免 API schema 报 required 错误）---
class EnvironmentalRegulationItem(BaseModel):
    target_attribute: str = Field(description="影响的环境属性: temperature, humidity, hygiene, air_freshness 之一")
    delta_per_minute: float = Field(description="每分钟变化量；temperature 时若填了 target_value 则优先按目标值指数趋近，delta 可填 0")
    working_condition: Optional[Dict[str, str]] = Field(default=None, description="生效条件，如 power=on 且 mode=cool 时填为键值对，无则省略")
    target_value: Optional[float] = Field(default=None, description="仅 temperature 时使用：目标温度 °C，房间温度将指数趋近该值，避免线性 delta 导致 runaway")

# --- 物理法则模板：语义标签 -> 物理引擎参数（由 Python 接管，LLM 只打标 physics_capabilities）---
PHYSICS_TEMPLATES = {
    "cooling": {
        "target_attribute": "temperature", "delta_per_minute": -0.2,
        "working_condition": {"power": "on", "mode": "cool"}, "target_value": 22.0,
    },
    "heating": {
        "target_attribute": "temperature", "delta_per_minute": 0.15,
        "working_condition": {"power": "on", "mode": "heat"}, "target_value": 26.0,
    },
    "slight_heating": {
        "target_attribute": "temperature", "delta_per_minute": 0.02,
        "working_condition": {"power": "on"},
    },
    "ventilation": {
        "target_attribute": "air_freshness", "delta_per_minute": 0.1,
        "working_condition": {"power": "on"}, "target_value": 0.8,
    },
    "cooking_smoke": {
        "target_attribute": "air_freshness", "delta_per_minute": -0.05,
        "working_condition": {"power": "on"},
    },
    "humidify": {
        "target_attribute": "humidity", "delta_per_minute": 0.02,
        "working_condition": {"power": "on"}, "target_value": 0.6,
    },
    "dehumidify": {
        "target_attribute": "humidity", "delta_per_minute": -0.02,
        "working_condition": {"power": "on"}, "target_value": 0.4,
    },
    "cleaning": {
        "target_attribute": "hygiene", "delta_per_minute": 0.05,
        "working_condition": {"power": "on"}, "target_value": 0.9,
    },
}

# --- A. 家具相关结构 ---
# current_state 唯一功能：记录设备开否 + 若开则当前设置值。家具不记录温度、不记录表面放置物，仿真不依赖。
class FurnitureState(BaseModel):
    # 可选；仿真不依赖。仅当需占用状态时用 occupied（如床）
    occupied: Optional[bool] = Field(default=None, description="是否被占用，如床")
    open: Optional[str] = Field(default=None, description="可开合家具的开合状态，如 open/closed")

class FurnitureItem(BaseModel):
    furniture_id: str = Field(description="必须与输入列表中的ID完全一致")
    name: str = Field(description="家具中文名称")
    room: str = Field(description="所属房间")
    support_actions: List[str] = Field(description="支持的动作，如 ['sit', 'sleep']")
    comfort_level: float = Field(description="舒适度 0.0-1.0")
    current_state: FurnitureState
    physics_capabilities: List[str] = Field(default_factory=list, description="从物理能力词典中选择该物品具备的能力标签，如无则为空 []。")
    environmental_regulation: List[EnvironmentalRegulationItem] = Field(default_factory=list, description="由系统物理引擎内部接管，LLM无需填写")

# --- B. 设备相关结构 ---
# current_state 唯一功能：记录设备开否 + 若开则当前设置值（仅直接调节温度的写 temperature_set，仅直接调节湿度的写 humidity_set）
class DeviceState(BaseModel):
    power: str = Field(description="'on' 或 'off'")
    temperature_set: Optional[float] = Field(default=None, description="设定温度°C，仅直接调节温度的设备填写")
    humidity_set: Optional[float] = Field(default=None, description="设定湿度0-1，仅直接调节湿度的设备填写")
    mode: Optional[str] = Field(default=None, description="工作模式")
    fan_speed: Optional[str] = Field(default=None, description="风速")

class DeviceItem(BaseModel):
    device_id: str = Field(description="必须与输入列表中的ID完全一致")
    name: str = Field(description="设备中文名称")
    room: str = Field(description="所属房间")
    support_actions: List[str] = Field(description="支持的操作，如 ['turn_on', 'set_temp']")
    current_state: DeviceState
    physics_capabilities: List[str] = Field(default_factory=list, description="从物理能力词典中选择该物品具备的能力标签，如无则为空 []。")
    environmental_regulation: List[EnvironmentalRegulationItem] = Field(default_factory=list, description="由系统物理引擎内部接管，LLM无需填写")

# --- C. 容器结构 (用于解析器的联合类型输出) ---
class RoomItemsDetail(BaseModel):
    # 我们希望得到一个混合列表
    items: List[Union[FurnitureItem, DeviceItem]] = Field(description="家具和设备的详细属性列表")


# --- D. details 校验结果（与 layout 的 LayoutValidationResult 同构）---
class DetailsValidationResult(BaseModel):
    is_valid: bool = Field(description="是否通过校验")
    correction_content: str = Field(default="", description="未通过时的修正说明，逐条列出设备 ID 与问题")

# ==========================================
# 2. 辅助函数
# ==========================================

_thread_local = threading.local()

def get_max_workers(total: int, env_name: str = "MAX_WORKERS", default: int = None) -> int:
    """
    根据数据量与环境变量决定并行度；未设 MAX_WORKERS 时使用 agent_config.MAX_WORKERS_DEFAULT。
    """
    if default is None:
        default = MAX_WORKERS_DEFAULT
    if total <= 1:
        return 1
    env_value = os.getenv(env_name)
    if env_value:
        try:
            value = int(env_value)
            if value > 0:
                return min(value, total)
        except ValueError:
            pass
    cpu_count = os.cpu_count() or default
    return min(total, max(1, min(8, cpu_count)))

def get_thread_llm():
    llm = getattr(_thread_local, "llm", None)
    if llm is None:
        llm = create_fast_llm(
            model=DETAILS_MODEL,
            temperature=SETTINGS_DEFAULT_TEMPERATURE,
            use_responses_api=False,  # 与 with_structured_output 同用须 False，否则报 Cannot mix and match text.format with text_format
            reasoning_effort=DETAILS_REASONING_EFFORT,
            request_timeout=DETAILS_REQUEST_TIMEOUT,
        )
        _thread_local.llm = llm
    return llm

def load_json_file(filename):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"错误: 找不到文件 {path}。请确保上一步(house_layout.json)已生成。")
        sys.exit(1)

def save_json_file(data, filename="house_details.json"):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[完成] 详细物品配置已保存至: {path}")


def _build_layout_item_set(house_data: Union[list, dict]) -> set:
    """从 house_layout 构建 (room_id, item_id) 集合，仅这些条目应出现在 details 中。"""
    out = set()
    if isinstance(house_data, list):
        for room in house_data:
            r_id = room.get("room_id") or room.get("id") or room.get("room_type", "")
            r_info = room.get("room_info", room)
            for fid in (r_info.get("furniture") or []) + (r_info.get("devices") or []):
                out.add((r_id, fid))
    elif isinstance(house_data, dict):
        if "rooms" in house_data and isinstance(house_data["rooms"], list):
            for room in house_data["rooms"]:
                r_id = room.get("room_id") or room.get("id") or ""
                r_info = room.get("room_info", room)
                for fid in (r_info.get("furniture") or []) + (r_info.get("devices") or []):
                    out.add((r_id, fid))
        else:
            for r_id, r_data in house_data.items():
                if isinstance(r_data, dict):
                    for fid in (r_data.get("furniture") or []) + (r_data.get("devices") or []):
                        out.add((r_id, fid))
    return out


def _item_id(item: dict) -> str:
    return (item.get("device_id") or item.get("furniture_id") or "").strip()


def _normalize_and_dedupe_details(
    items: List[dict],
    layout_item_set: set,
) -> List[dict]:
    """
    1. 统一为 device_id 字段（furniture_id 合并为 device_id）
    2. 按 (device_id, room) 去重，保留第一条
    3. 仅保留 (room, device_id) 在 layout_item_set 中的条目
    """
    seen: set = set()
    out = []
    for it in items:
        rid = _item_id(it)
        room = (it.get("room") or "").strip()
        if not rid:
            continue
        if (room, rid) not in layout_item_set:
            continue
        key = (rid, room)
        if key in seen:
            continue
        seen.add(key)
        it = dict(it)
        if "furniture_id" in it and not it.get("device_id"):
            it["device_id"] = it.pop("furniture_id", None)
        elif "furniture_id" in it and it.get("device_id"):
            del it["furniture_id"]
        if not it.get("device_id"):
            it["device_id"] = rid
        out.append(it)
    return out


def _get_room_type_from_layout(layout: Union[list, dict], room_id: str) -> str:
    """从 layout 中取房间的 room_type（LLM 生成，自适应中英文），缺省用 room_id。"""
    if isinstance(layout, dict) and not layout.get("rooms"):
        room_data = layout.get(room_id) if isinstance(layout.get(room_id), dict) else None
        if room_data:
            return (room_data.get("room_type") or room_id).strip()
    if isinstance(layout, list):
        for room in layout:
            r_id = room.get("room_id") or room.get("id") or ""
            if r_id == room_id:
                r_info = room.get("room_info", room)
                return (r_info.get("room_type") or room_id).strip()
    if isinstance(layout, dict) and isinstance(layout.get("rooms"), list):
        for room in layout["rooms"]:
            r_id = room.get("room_id") or room.get("id") or ""
            if r_id == room_id:
                r_info = room.get("room_info", room)
                return (r_info.get("room_type") or room_id).strip()
    return room_id


def _sync_name_room_suffix(items: List[dict], layout: Union[list, dict]) -> None:
    """为 name 追加房间后缀；房间标签取自 layout 的 room_type（自适应语言），零硬编码。"""
    for it in items:
        room = (it.get("room") or "").strip()
        name = (it.get("name") or "").strip()
        if not name:
            continue
        room_label = _get_room_type_from_layout(layout, room)
        if room_label and not name.endswith(f"({room_label})"):
            it["name"] = f"{name}({room_label})"


# 设备 current_state 仅保留：power、temperature_set（仅温控）、humidity_set（仅加湿）、mode、fan_speed、open。家具仅保留 open、occupied。
_CURRENT_STATE_DEVICE_KEYS = frozenset({"power", "temperature_set", "humidity_set", "mode", "fan_speed", "open"})
_CURRENT_STATE_FURNITURE_KEYS = frozenset({"open", "occupied"})


# 家具统一使用的「支持动作」：通用物理交互 + 常用家具动作；由程序填写，不依赖 LLM，便于 event 校验与上下文一致。
FURNITURE_DEFAULT_SUPPORT_ACTIONS: List[str] = [
    "use", "interact", "sit", "sleep", "open", "close",
    *list(EVENT_UNIVERSAL_ACTIONS),
]


def _apply_furniture_support_actions_default(items: List[dict]) -> None:
    """家具的 support_actions 统一设为 FURNITURE_DEFAULT_SUPPORT_ACTIONS，不依赖 LLM 生成；仅设备保留 LLM 生成的设备相关动作。"""
    for it in items:
        if it.get("device_id"):
            continue
        it["support_actions"] = list(FURNITURE_DEFAULT_SUPPORT_ACTIONS)


def _is_window_item(it: dict) -> bool:
    """窗户作为 devices 特例：按 id 或 name 判定是否为窗户。"""
    rid = (_item_id(it) or "").lower()
    name = (it.get("name") or "").lower()
    return "window" in rid or "窗" in name or "窗户" in name


def _normalize_window_devices(items: List[dict]) -> None:
    """窗户设备特例：仅 open/close；current_state 只保留 open。environmental_regulation 由 physics_capabilities -> window_ventilation 在 _apply_physics_templates 中生成。"""
    for it in items:
        if not it.get("device_id") or not _is_window_item(it):
            continue
        it["support_actions"] = ["open", "close"]
        state = it.get("current_state") or {}
        open_val = state.get("open") if isinstance(state, dict) else None
        if open_val not in ("open", "closed"):
            open_val = "closed"
        it["current_state"] = {"open": open_val}


def _fill_empty_support_actions(items: List[dict]) -> None:
    """仅对设备：若 support_actions 为空则填入设备兜底 ['turn_on','turn_off','use']。家具已由 _apply_furniture_support_actions_default 统一填写；窗户已由 _normalize_window_devices 填写。"""
    for it in items:
        if not it.get("device_id"):
            continue
        if _is_window_item(it):
            continue
        acts = it.get("support_actions")
        if acts is not None and len(acts) > 0:
            continue
        it["support_actions"] = ["turn_on", "turn_off", "use"]
        rid = _item_id(it) or (it.get("name") or "unknown")
        print(f"  [补全] 设备 {rid} support_actions 为空，已填入兜底: ['turn_on', 'turn_off', 'use']")


# 室内温度合理范围（与 physics_engine 一致），用于钳位 target_value，避免 -5°C、50°C 等荒谬值
_TEMPERATURE_MIN = 18.0
_TEMPERATURE_MAX = 30.0


# 允许的 working_condition 键（机器可读状态名），禁止 Schema/自然语言键
_WORKING_CONDITION_ALLOWED_KEYS = frozenset({"power", "open", "mode"})


def _normalize_working_condition(cond: dict) -> dict:
    """将 working_condition 统一为机器可读键值对：键小写，并剔除 Schema/自然语言键，避免解析崩溃。"""
    if not cond or not isinstance(cond, dict):
        return cond if isinstance(cond, dict) else {}
    out = {}
    for k, v in cond.items():
        key = str(k).strip().lower()
        if key not in _WORKING_CONDITION_ALLOWED_KEYS:
            continue
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        val = v if isinstance(v, str) else str(v).strip()
        if val:
            out[key] = val
    return out


def _apply_physics_templates(items: List[dict]) -> None:
    """将 LLM 生成的语义标签翻译为标准的物理引擎参数，提前执行防丢失"""
    success_count = 0
    for it in items:
        capabilities = it.get("physics_capabilities") or []
        if not isinstance(capabilities, list):
            capabilities = []
        regulations = []
        for cap in capabilities:
            if cap in PHYSICS_TEMPLATES:
                regulations.append(dict(PHYSICS_TEMPLATES[cap]))
                success_count += 1
        it["environmental_regulation"] = regulations
        it.pop("physics_capabilities", None)
    if success_count > 0:
        print(f"\n[物理引擎] 成功将 {success_count} 个语义标签转换为了绝对物理常量！\n")


# 非温控/非湿度调节设备：current_state 不得保留 temperature_set、humidity_set（避免幽灵空调面板）
_ID_NAME_NEVER_THERMAL_STATE = (
    "television", "monitor", "tv", "显示器", "电视", "notebook", "laptop", "tablet", "笔记本", "平板",
    "bookcase", "bookshelf", "书架", "cabinet", "柜", "flower_storage", "植物收纳", "storage",
    "workbench", "工作台", "coffee_table", "coffee_station", "stool", "island", "desk", "书桌",
    "night_stand", "床头柜", "dresser", "vanity", "梳妆台", "洗手台", "curtain", "窗帘", "rug", "地毯",
)


def _clean_current_state(items: List[dict]) -> None:
    """规范化 current_state：设备只保留开否与设定值；家具不保留 temperature/items_on，只保留 open/occupied。非温控设备删除 temperature_set/humidity_set。power 限定为 on/off。"""
    for it in items:
        state = it.get("current_state")
        if not isinstance(state, dict):
            continue
        state = dict(state)
        # 判断是设备还是家具：有 device_id 视为设备
        is_device = "device_id" in it and it.get("device_id")
        if is_device:
            if state.get("power") not in ("on", "off"):
                state["power"] = "off"
            rid = (_item_id(it) or "").lower()
            name = (it.get("name") or "").lower()
            text = f"{rid} {name}"
            # 非温控/非湿度调节设备不得保留 temperature_set、humidity_set
            if any(s in text for s in _ID_NAME_NEVER_THERMAL_STATE):
                state.pop("temperature_set", None)
                state.pop("humidity_set", None)
            state = {k: v for k, v in state.items() if k in _CURRENT_STATE_DEVICE_KEYS}
            if "power" not in state:
                state["power"] = "off"
        else:
            # 家具：删除 temperature、items_on 等，只保留 open、occupied
            state = {k: v for k, v in state.items() if k in _CURRENT_STATE_FURNITURE_KEYS}
        it["current_state"] = state


def _ensure_layout_details_completeness(
    items: List[dict],
    layout_item_set: set,
    house_data: Union[list, dict],
) -> None:
    """确保 layout 中每个 (room, id) 在 details 中都有对应条目，缺失则追加默认条目。"""
    current_set = set()
    for it in items:
        rid = _item_id(it)
        room = (it.get("room") or "").strip()
        if rid and room:
            current_set.add((room, rid))
    missing = layout_item_set - current_set
    if not missing:
        return
    for room, did in missing:
        label = _get_room_type_from_layout(house_data, room)
        items.append({
            "device_id": did,
            "name": f"{did}({label})" if label else did,
            "room": room,
            "support_actions": [],
            "current_state": {"power": "off"},
            "environmental_regulation": [],
        })
    print(f"  [补全] 为 layout 中缺失的 {len(missing)} 个 ID 生成了默认 details 条目。")


# ==========================================
# 3. 核心 Agent：按物件并行生成房间物品详情
# ==========================================

def _make_placeholder_item(room_id: str, item_id: str, item_type: str) -> dict:
    """生成失败时占位条目，保证顺序；后续由 _ensure_layout_details_completeness / _fill_empty_support_actions 补全。"""
    key = "furniture_id" if item_type == "家具" else "device_id"
    return {
        key: item_id,
        "name": item_id,
        "room": room_id,
        "support_actions": [],
        "current_state": {"power": "off"} if item_type == "设备" else {},
        "environmental_regulation": [],
    }


def _generate_one_item(args) -> Optional[dict]:
    """
    单物件生成（供并行调用）。args = (profile_str, room_id, room_data, item_id, item_type)。
    返回一个 item dict，失败返回 None（调用方用 placeholder 占位）。
    """
    profile_str, room_id, room_data, item_id, item_type = args
    furniture_ids = room_data.get("furniture", [])
    device_ids = room_data.get("devices", [])
    llm = get_thread_llm()
    structured_chain = llm.with_structured_output(RoomItemsDetail, method="json_schema", strict=False)
    prompt = ChatPromptTemplate.from_template(LAYOUT2DETAILS_SINGLE_ITEM_PROMPT_TEMPLATE)
    chain = prompt | structured_chain

    def _to_dict(x):
        if hasattr(x, "model_dump"):
            return x.model_dump()
        return x if isinstance(x, dict) else {}

    for attempt in range(DETAILS_ROOM_RETRY_COUNT + 1):
        try:
            result = chain.invoke({
                "profile_context": profile_str,
                "room_id": room_id,
                "room_type": room_data.get("room_type", "未知房间"),
                "target_item_id": item_id,
                "target_item_type": item_type,
                "furniture_list": json.dumps(furniture_ids),
                "device_list": json.dumps(device_ids),
            })
            if isinstance(result, RoomItemsDetail) and result.items:
                return _to_dict(result.items[0])
            if isinstance(result, list) and result:
                return _to_dict(result[0])
            if isinstance(result, dict):
                items = result.get("items", [])
                if items:
                    return _to_dict(items[0])
            return None
        except Exception as e:
            if attempt < DETAILS_ROOM_RETRY_COUNT:
                time.sleep(5 * (attempt + 1))
            else:
                print(f"  [警告] 物品 {item_id} ({room_id}) 生成出错: {e}")
            return None
    return None


def process_single_room(profile_str, room_id, room_data):
    """
    处理单个房间：按物件并行生成每个物品的详情（单物件单次调用），保持 furniture 先、device 后的顺序。
    """
    furniture_ids = room_data.get("furniture", [])
    device_ids = room_data.get("devices", [])
    if not furniture_ids and not device_ids:
        return []

    # 保持顺序：先家具后设备
    args_list = (
        [(profile_str, room_id, room_data, fid, "家具") for fid in furniture_ids]
        + [(profile_str, room_id, room_data, did, "设备") for did in device_ids]
    )
    n_total = len(args_list)
    print(f" -> 正在生成详情: {room_id} (按物件并行，共 {n_total} 个)...")

    workers = get_max_workers(n_total)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(_generate_one_item, args_list))

    room_items = []
    for i, one in enumerate(results):
        if one is not None:
            room_items.append(one)
        else:
            item_id = args_list[i][3]
            item_type = args_list[i][4]
            room_items.append(_make_placeholder_item(room_id, item_id, item_type))
    return room_items


# ==========================================
# 3.5 details 校验与修正 Agent（与 layout 的 validate/correct 同构，用提示词驱动）
# ==========================================

# 校验/修正 Agent 调用超时（秒），按房间校验时单次输入小，可略短
DETAILS_VALIDATE_TIMEOUT = 45
DETAILS_CORRECT_TIMEOUT = 40
# 单房间内校验未通过时，最多修正的轮数（每轮：校验 → 对未通过项修正 → 再校验）
DETAILS_MAX_CORRECTION_ROUNDS = 3


def validate_details_agent(items: List[dict], profile_str: str) -> DetailsValidationResult:
    """用 Agent + 提示词校验 house_details，返回 is_valid 与 correction_content。"""
    llm = create_fast_llm(
        model=DETAILS_MODEL,
        temperature=SETTINGS_DEFAULT_TEMPERATURE,
        use_responses_api=False,
        reasoning_effort=DETAILS_REASONING_EFFORT,
        request_timeout=DETAILS_REQUEST_TIMEOUT,
    )
    structured_llm = llm.with_structured_output(DetailsValidationResult, method="json_schema", strict=True)
    prompt = ChatPromptTemplate.from_template(DETAILS_VALIDATION_PROMPT_TEMPLATE)
    chain = prompt | structured_llm
    details_str = json.dumps(items, ensure_ascii=False, indent=2)
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(
                chain.invoke,
                {"profile_context": profile_str, "details_context": details_str},
            )
            return future.result(timeout=DETAILS_VALIDATE_TIMEOUT)
    except FuturesTimeoutError:
        print(f"  [警告] details 校验超时（{DETAILS_VALIDATE_TIMEOUT}s），视为通过以继续。")
        return DetailsValidationResult(is_valid=True, correction_content="")


def _apply_patch(item: dict, patch: dict) -> None:
    """将 patch 的字段深合并到 item（patch 中有的 key 覆盖 item）。"""
    for k, v in patch.items():
        if k not in item:
            item[k] = deepcopy(v)
        elif isinstance(v, dict) and isinstance(item.get(k), dict):
            for kk, vv in v.items():
                item[k][kk] = deepcopy(vv)
        else:
            item[k] = deepcopy(v)


def correct_details_agent(items: List[dict], profile_str: str, correction_content: str) -> List[dict]:
    """用 Agent 输出补丁列表（仅需改的 id + patch），合并回原列表；带超时避免卡死。"""
    llm = create_fast_llm(
        model=DETAILS_MODEL,
        temperature=SETTINGS_DEFAULT_TEMPERATURE,
        use_responses_api=False,
        reasoning_effort=DETAILS_REASONING_EFFORT,
        request_timeout=DETAILS_REQUEST_TIMEOUT,
    )
    prompt = ChatPromptTemplate.from_template(DETAILS_CORRECTION_PROMPT_TEMPLATE)
    chain = prompt | llm
    id_to_item = {}
    for it in items:
        rid = _item_id(it)
        if rid:
            id_to_item[rid] = it
    details_str = json.dumps(items, ensure_ascii=False, indent=2)
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(
                chain.invoke,
                {
                    "profile_context": profile_str,
                    "details_context": details_str,
                    "correction_content": correction_content,
                },
            )
            msg = future.result(timeout=DETAILS_CORRECT_TIMEOUT)
    except FuturesTimeoutError:
        print(f"  [警告] details 修正超时（{DETAILS_CORRECT_TIMEOUT}s），保留原结果。")
        return items
    try:
        content = getattr(msg, "content", str(msg))
        # 部分 API 返回 content 为 list（多段内容），需先转为字符串
        if isinstance(content, list):
            content = "".join(
                (x.get("text", str(x)) if isinstance(x, dict) else str(x) for x in content)
            )
        content = str(content).strip()
        raw_response = content  # 保留完整原始回复，补丁数为 0 时打印排查
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        # 从回复中抽取最外层 [...] 再解析，避免前后说明文字或单引号导致 JSON 报错
        start = content.find("[")
        if start >= 0:
            depth = 0
            for i in range(start, len(content)):
                if content[i] == "[":
                    depth += 1
                elif content[i] == "]":
                    depth -= 1
                    if depth == 0:
                        content = content[start : i + 1]
                        break
        if start < 0:
            patches = []
        else:
            try:
                patches = json.loads(content)
            except json.JSONDecodeError:
                content_fix = re.sub(r"'([^']*)'(\s*):", r'"\1"\2:', content)
                try:
                    patches = json.loads(content_fix)
                except json.JSONDecodeError:
                    raise
        if not isinstance(patches, list):
            print("  [details 修正] 解析结果非列表，未应用任何补丁。")
            return items
        print(f"  [details 修正] 收到补丁数: {len(patches)}")
        if len(patches) == 0:
            print("  [details 修正] 补丁数为 0，未修改任何条目（数据流异常：有校验反馈却无补丁）。")
            print("  ---------- [details 修正 原始回复（供排查）] ----------")
            preview = (raw_response[:2500] + "\n…") if len(raw_response) > 2500 else raw_response
            print(preview)
            print("  ---------------------------------------------------------")
        applied_ids = []
        for one in patches:
            if not isinstance(one, dict):
                continue
            rid = one.get("id") or one.get("device_id") or one.get("furniture_id")
            patch = one.get("patch")
            if not rid or not isinstance(patch, dict):
                continue
            print(f"  [details 修正] 补丁 id={rid!r} patch={json.dumps(patch, ensure_ascii=False)}")
            target = id_to_item.get(rid)
            if target is not None:
                _apply_patch(target, patch)
                applied_ids.append(rid)
            else:
                print(f"  [details 修正] 未匹配到条目，跳过 id={rid!r}（当前列表仅有: {list(id_to_item.keys())[:15]}{'...' if len(id_to_item) > 15 else ''}）")
        if applied_ids:
            print(f"  [details 修正] 已应用补丁的 id: {applied_ids}")
        return items
    except Exception as e:
        try:
            raw_preview = (content[:1000] + "…") if len(content) > 1000 else content
        except NameError:
            raw_preview = "(无内容)"
        print(f"  [警告] details 修正 Agent 解析失败: {e}，保留原结果。")
        print("  ---------- [details 修正 原始回复（前 1000 字）] ----------")
        print(raw_preview)
        print("  ---------------------------------------------------------")
        return items


def _validate_one_item(args) -> tuple:
    """单物件校验（供并行调用）：(item, profile_str) -> (item, DetailsValidationResult)。"""
    item, profile_str = args
    result = validate_details_agent([item], profile_str)
    return (item, result)


def _correct_one_item(args) -> None:
    """单物件修正（供并行调用）：(item, profile_str, correction_content)，原地修改 item。"""
    item, profile_str, correction_content = args
    correct_details_agent([item], profile_str, correction_content)


def _validate_and_correct_room_items(room_id: str, room_items: List[dict], profile_str: str) -> tuple:
    """单房间生成完成后：按物件并行校验，对未通过项按物件并行修正，最多 DETAILS_MAX_CORRECTION_ROUNDS 轮。返回 (room_items, log_entries)。"""
    log_entries: List[Dict[str, Any]] = []
    if not room_items:
        return room_items, log_entries

    for round_no in range(1, DETAILS_MAX_CORRECTION_ROUNDS + 1):
        # 按物件并行校验
        n_items = len(room_items)
        workers = get_max_workers(n_items)
        validation_args = [(it, profile_str) for it in room_items]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            validation_results = list(ex.map(_validate_one_item, validation_args))

        failed = [
            (item, (r.correction_content or "").strip())
            for (item, r) in validation_results
            if not r.is_valid and (r.correction_content or "").strip()
        ]
        if not failed:
            if round_no > 1:
                print(f"  [{room_id}] 第 {round_no} 轮校验通过。")
            return room_items, log_entries

        print(f"  [{room_id}] 第 {round_no}/{DETAILS_MAX_CORRECTION_ROUNDS} 轮：校验未通过 {len(failed)} 个物品，按物件并行修正...")
        for (item, cc) in failed:
            item_id = _item_id(item) or "(无 id)"
            print(f"  ---------- [details 校验反馈] {item_id} ----------")
            print(cc[:500] + ("..." if len(cc) > 500 else ""))
            print("  ----------------------------------------")

        # 改错前快照（修正会原地修改 item）
        before_copies = [deepcopy(item) for (item, cc) in failed]
        # 按物件并行修正
        correction_args = [(item, profile_str, cc) for (item, cc) in failed]
        with ThreadPoolExecutor(max_workers=get_max_workers(len(failed))) as ex:
            list(ex.map(_correct_one_item, correction_args))
        # 记录改错前后与校验反馈
        for i, (item, cc) in enumerate(failed):
            log_entries.append({
                "room_id": room_id,
                "round": round_no,
                "item_id": _item_id(item) or "(无 id)",
                "name": item.get("name") or "",
                "correction_content": cc,
                "before": before_copies[i],
                "after": deepcopy(item),
            })

    print(f"  [{room_id}] 已达最大修正轮数 {DETAILS_MAX_CORRECTION_ROUNDS}，保留当前结果。")
    return room_items, log_entries


def _write_correction_log(all_entries: List[Dict[str, Any]], filepath: str) -> None:
    """将全部修正记录（校验反馈 + 改错前/后）写入 txt，便于排查。"""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write("layout2details 修正日志：校验反馈 + 改错前/后内容\n")
        f.write("=" * 80 + "\n\n")
        for idx, rec in enumerate(all_entries, 1):
            f.write("-" * 80 + "\n")
            f.write(f"【{idx}】房间: {rec.get('room_id', '')}  轮次: {rec.get('round', '')}  物品ID: {rec.get('item_id', '')}  名称: {rec.get('name', '')}\n")
            f.write("-" * 80 + "\n\n")
            f.write(">>> 校验反馈 (correction_content)\n\n")
            f.write(rec.get("correction_content", "") + "\n\n")
            f.write(">>> 改错前 (before)\n\n")
            f.write(json.dumps(rec.get("before", {}), ensure_ascii=False, indent=2) + "\n\n")
            f.write(">>> 改错后 (after)\n\n")
            f.write(json.dumps(rec.get("after", {}), ensure_ascii=False, indent=2) + "\n\n")
        f.write("=" * 80 + "\n")
        f.write(f"共 {len(all_entries)} 条修正记录\n")
    print(f"  [日志] 修正记录已写入: {filepath}")


# ==========================================
# 4. 主程序 (已修复 List/Dict 兼容性问题)
# ==========================================

def main():
    # 1. 加载数据
    print(">>> 1. 读取上一步生成的快照数据...")
    profile = load_json_file("profile.json")
    
    # 假设这里读取的文件名是 house_layout.json 或 house_layout.json
    # 请根据你实际生成的文件名修改下面这行：
    input_file_name = "house_layout.json" # 或者 "house_layout.json"
    house_data = load_json_file(input_file_name)
    
    profile_str = json.dumps(profile, ensure_ascii=False)

    # 2. 数据标准化 (解决 List vs Dict 报错的核心逻辑)
    # 我们将数据统一转换为 [(room_id, room_data), ...] 的列表形式
    rooms_to_process = []

    if isinstance(house_data, list):
        # 情况 A: 输入是列表List [...]
        print(f"    检测到输入为 List 格式，包含 {len(house_data)} 个房间。")
        for index, room in enumerate(house_data):
            # 尝试查找 ID 字段，找不到则用 room_type，还找不到就用索引
            r_id = room.get('id') or room.get('room_id') or room.get('room_type') or f"room_{index}"
            rooms_to_process.append((r_id, room))
            
    elif isinstance(house_data, dict):
        # 情况 B: 输入是字典Dict {...}
        # 有一种情况是 {"rooms": [...]}
        if "rooms" in house_data and isinstance(house_data["rooms"], list):
             print(f"    检测到输入为包含 'rooms' 列表的 Dict 格式。")
             for index, room in enumerate(house_data["rooms"]):
                r_id = room.get('id') or room.get('room_id') or room.get('room_type') or f"room_{index}"
                rooms_to_process.append((r_id, room))
        else:
            # 标准情况: {"living_room": {...}, "kitchen": {...}}
            print(f"    检测到输入为标准 Dict 映射格式。")
            for r_id, r_data in house_data.items():
                rooms_to_process.append((r_id, r_data))
    else:
        print("错误: 无法识别的 JSON 结构。")
        return

    # 3. 循环处理并累积结果，同时收集修正日志
    all_items_flat_list = []
    all_correction_log: List[Dict[str, Any]] = []

    print("\n>>> 2. 开始逐个房间细化物品状态...")

    room_items_list = list(rooms_to_process)
    max_workers = get_max_workers(len(room_items_list))

    def _worker(item):
        room_id, room_data = item
        if isinstance(room_data, dict):
            room_items = process_single_room(profile_str, room_id, room_data)
            room_items, log_entries = _validate_and_correct_room_items(room_id, room_items, profile_str)
            return room_id, room_items, log_entries
        return room_id, [], []

    if max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for _, room_items, log_entries in executor.map(_worker, room_items_list):
                all_items_flat_list.extend(room_items)
                all_correction_log.extend(log_entries)
    else:
        for room_id, room_data in room_items_list:
            if isinstance(room_data, dict):
                room_items = process_single_room(profile_str, room_id, room_data)
                room_items, log_entries = _validate_and_correct_room_items(room_id, room_items, profile_str)
                all_items_flat_list.extend(room_items)
                all_correction_log.extend(log_entries)

    # 将修正内容与改错前后全部输出到 txt
    if all_correction_log:
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "details_correction_log.txt")
        _write_correction_log(all_correction_log, log_path)

    # 4. 程序化后处理：与 layout 对齐、去重、补全等
    layout_item_set = _build_layout_item_set(house_data)
    # [关键] 提前截胡：在任何清洗逻辑重构字典之前，先注入物理参数，防止 physics_capabilities 被后续清洗静默丢弃
    _apply_physics_templates(all_items_flat_list)
    all_items_flat_list = _normalize_and_dedupe_details(all_items_flat_list, layout_item_set)
    _ensure_layout_details_completeness(all_items_flat_list, layout_item_set, house_data)
    _sync_name_room_suffix(all_items_flat_list, house_data)
    _clean_current_state(all_items_flat_list)
    _apply_furniture_support_actions_default(all_items_flat_list)
    _normalize_window_devices(all_items_flat_list)
    _fill_empty_support_actions(all_items_flat_list)

    # 6. 保存结果（校验与修正已在每个房间生成后按房间执行，不再整表送入 Agent）
    print(f"\n>>> 3. 处理完成，共生成 {len(all_items_flat_list)} 个物品详情（与 layout 一一对应，已按房间校验/修正）。")
    save_json_file(all_items_flat_list)

if __name__ == "__main__":
    main()
