import sys
from pathlib import Path

_current_dir = Path(__file__).resolve().parent
_project_root = _current_dir.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import json
import os
from dotenv import load_dotenv
from typing import List, Dict, Any, Optional, Tuple

from llm_utils import create_fast_llm
from agent_config import DEFAULT_MODEL, SETTINGS_DEFAULT_TEMPERATURE, SETTINGS_USE_RESPONSES_API
from prompt import (
    LAYOUT_CHECK_PROMPT_TEMPLATE,
    LAYOUT_VALIDATION_PROMPT_TEMPLATE,
    LAYOUT_CORRECTION_PROMPT_TEMPLATE,
)
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

# --- 环境配置 ---
load_dotenv()
dotenv_path = _project_root / ".env"
load_dotenv(dotenv_path=dotenv_path)

# ==========================================
# 1. 复用数据结构 (保持 Schema 绝对一致)
# ==========================================

class EnvironmentState(BaseModel):
    temperature: float
    humidity: float
    light_level: float
    noise_level: float
    hygiene: float = 0.5  # 清洁度 0–1，与物理引擎对齐
    air_freshness: float = 0.5  # 空气清新度 0–1，与物理引擎对齐

class RoomInfo(BaseModel):
    room_type: str
    area_sqm: float
    furniture: List[str]
    devices: List[str]
    environment_state: EnvironmentState

# 用列表避免 Dict 动态 key，与 profile2layout 一致
class RoomEntry(BaseModel):
    room_id: str = Field(description="房间英文ID")
    room_info: RoomInfo = Field(description="该房间的详情")


class HouseSnapshot(BaseModel):
    rooms: List[RoomEntry] = Field(default_factory=list, description="房间列表，每项含 room_id 与 room_info")


class LayoutValidationResult(BaseModel):
    is_valid: bool = Field(description="户型是否通过校验")
    correction_content: str = Field(default="", description="未通过时的修正说明")

# ==========================================
# 2. 辅助函数
# ==========================================

def load_json_file(filename):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"错误: 找不到文件 {path}。")
        sys.exit(1)

def save_json_file(data, filename):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[成功] 逻辑修正后的户型图已保存至: {path}")

# ==========================================
# 3. 核心 Agent: 逻辑审查与修正
# ==========================================

def run_logic_fixer_agent():
    # 1. 读取上下文
    print(">>> 1. 读取 Profile 和 原始 Layout...")
    profile = load_json_file("profile.json")
    layout = load_json_file("house_layout.json")

    profile_str = json.dumps(profile, ensure_ascii=False)
    layout_str = json.dumps(layout, ensure_ascii=False)

    # strict=True：rooms 为 List[RoomEntry]，无动态 key
    llm = create_fast_llm(
        model=DEFAULT_MODEL,
        temperature=SETTINGS_DEFAULT_TEMPERATURE,
        use_responses_api=SETTINGS_USE_RESPONSES_API,
    )
    structured_chain = llm.with_structured_output(
        HouseSnapshot, method="json_schema", strict=True
    )

    prompt = ChatPromptTemplate.from_template(LAYOUT_CHECK_PROMPT_TEMPLATE)
    chain = prompt | structured_chain

    print(">>> 2. 正在进行逻辑审查与修正 (Logic Inspection)...")
    print("    正在检查：职业工具、宠物用品、生存设施...")
    
    try:
        fixed = chain.invoke({
            "profile_context": profile_str,
            "layout_context": layout_str
        })
        rooms_val = fixed.rooms if hasattr(fixed, "rooms") else getattr(fixed, "rooms", layout)
        if isinstance(rooms_val, dict):
            return rooms_val
        if isinstance(rooms_val, list):
            return {e.room_id: e.room_info.model_dump() if hasattr(e.room_info, "model_dump") else e.room_info for e in rooms_val}
        return layout

    except Exception as e:
        print(f"修正过程出错: {e}")
        return layout # 如果出错，返回原件防止中断


def _layout_to_dict(fixed) -> Dict[str, Any]:
    """将 HouseSnapshot 或 rooms 列表转为 layout 字典（room_id -> room_info）。"""
    if isinstance(fixed, dict):
        return fixed
    rooms_val = fixed.rooms if hasattr(fixed, "rooms") else getattr(fixed, "rooms", [])
    if isinstance(rooms_val, dict):
        return rooms_val
    if isinstance(rooms_val, list):
        return {
            e.room_id: e.room_info.model_dump() if hasattr(e.room_info, "model_dump") else e.room_info
            for e in rooms_val
        }
    return {}


def validate_layout_agent(layout_dict: Dict[str, Any], profile_str: str) -> LayoutValidationResult:
    """多级校验：检查 ID 唯一、窗户、与 profile 一致性。"""
    llm = create_fast_llm(
        model=DEFAULT_MODEL,
        temperature=SETTINGS_DEFAULT_TEMPERATURE,
        use_responses_api=SETTINGS_USE_RESPONSES_API,
    )
    structured_llm = llm.with_structured_output(LayoutValidationResult, method="json_schema", strict=True)
    prompt = ChatPromptTemplate.from_template(LAYOUT_VALIDATION_PROMPT_TEMPLATE)
    chain = prompt | structured_llm
    layout_str = json.dumps(layout_dict, ensure_ascii=False, indent=2)
    return chain.invoke({"profile_context": profile_str, "layout_context": layout_str})


def correct_layout_agent(layout_dict: Dict[str, Any], profile_str: str, correction_content: str) -> Dict[str, Any]:
    """根据校验反馈修正户型。"""
    llm = create_fast_llm(
        model=DEFAULT_MODEL,
        temperature=SETTINGS_DEFAULT_TEMPERATURE,
        use_responses_api=SETTINGS_USE_RESPONSES_API,
    )
    structured_llm = llm.with_structured_output(HouseSnapshot, method="json_schema", strict=True)
    prompt = ChatPromptTemplate.from_template(LAYOUT_CORRECTION_PROMPT_TEMPLATE)
    chain = prompt | structured_llm
    layout_str = json.dumps(layout_dict, ensure_ascii=False, indent=2)
    fixed = chain.invoke({
        "profile_context": profile_str,
        "layout_context": layout_str,
        "correction_content": correction_content,
    })
    return _layout_to_dict(fixed)


# 房间 ID 到前缀的映射（用于跨房间重复 ID 重命名）。属「约定配置」，可后续迁至 layout_convention.json 等，程序只读不写死。
_ROOM_PREFIX = {
    "living_room": "lr",
    "master_bedroom": "mb",
    "study_room": "sr",
    "kitchen": "kt",
    "bathroom": "bc",
    "entry_hall": "eh",
}

def _normalize_layout_ids(layout_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    程序化修复「跨房间 ID 重复」：同一 ID 在多个房间出现时按房间前缀重命名。
    同房间双列（同一 ID 同时在 furniture 和 devices）不做猜测分类，留给校验报错 + LLM 修正。
    """
    # 收集每个 ID 出现在哪些房间
    id_to_rooms: Dict[str, List[str]] = {}
    for room_id, room_data in layout_dict.items():
        for fid in room_data.get("furniture", []) + room_data.get("devices", []):
            if fid not in id_to_rooms:
                id_to_rooms[fid] = []
            if room_id not in id_to_rooms[fid]:
                id_to_rooms[fid].append(room_id)

    # 跨房间重复：为每个 (id, room) 生成新 ID，并替换
    rename_map: Dict[Tuple[str, str], str] = {}  # (old_id, room_id) -> new_id
    for fid, rooms in id_to_rooms.items():
        if len(rooms) <= 1:
            continue
        prefix_map = _ROOM_PREFIX
        for room_id in rooms:
            prefix = prefix_map.get(room_id, room_id[:2].lower() if len(room_id) >= 2 else "x")
            new_id = f"{prefix}_{fid}"
            rename_map[(fid, room_id)] = new_id

    def replace_in_room(room_id: str, ids_list: List[str], is_furniture: bool) -> List[str]:
        out = []
        seen = set()
        for fid in ids_list:
            new_id = rename_map.get((fid, room_id), fid)
            if new_id in seen:
                continue
            seen.add(new_id)
            out.append(new_id)
        return out

    for room_id, room_data in layout_dict.items():
        fur = room_data.get("furniture", [])
        dev = room_data.get("devices", [])
        # 仅应用跨房间重命名；同房间双列不在此处理，由 _hard_check_same_room_dual_list 报错后由 LLM 修正
        room_data["furniture"] = replace_in_room(room_id, fur, True)
        room_data["devices"] = replace_in_room(room_id, dev, False)

    return layout_dict


def _normalize_environment_state_defaults(layout_dict: Dict[str, Any]) -> None:
    """仅做边界兜底：禁止全零、钳位到合理区间。具体房间差异（如卫生间湿度更高）由 prompt 在生成时体现，程序不按房间名分支。"""
    for room_id, room_data in layout_dict.items():
        es = room_data.get("environment_state") or {}
        if not isinstance(es, dict):
            continue
        if es.get("hygiene") == 0 or es.get("hygiene") is None:
            es["hygiene"] = 0.65
        if es.get("air_freshness") == 0 or es.get("air_freshness") is None:
            es["air_freshness"] = 0.65
        h = es.get("humidity")
        if h is None or h == 0:
            es["humidity"] = 0.5
        else:
            es["humidity"] = max(0.25, min(0.8, float(h)))
        light = es.get("light_level")
        if light is None or light == 0:
            es["light_level"] = 0.5
        else:
            es["light_level"] = max(0.05, min(1.0, float(light)))
        noise = es.get("noise_level")
        if noise is None or noise == 0:
            es["noise_level"] = 0.15
        else:
            es["noise_level"] = max(0.05, min(1.0, float(noise)))
        room_data["environment_state"] = es


# 硬校验：同一 ID 是否出现在多房间
def _hard_check_duplicate_ids(layout_dict: Dict[str, Any]) -> Optional[str]:
    id_to_rooms: Dict[str, List[str]] = {}
    for room_id, room_data in layout_dict.items():
        for fid in room_data.get("furniture", []) + room_data.get("devices", []):
            id_to_rooms.setdefault(fid, []).append(room_id)
    duplicates = {fid: rooms for fid, rooms in id_to_rooms.items() if len(rooms) > 1}
    if not duplicates:
        return None
    return "硬校验失败：以下 ID 出现在多个房间，须按房间区分（如 curtains_lr_001, curtains_mb_001）：" + json.dumps(duplicates, ensure_ascii=False)


# 硬校验：同一房间内同一 ID 不能同时在 furniture 和 devices
def _hard_check_same_room_dual_list(layout_dict: Dict[str, Any]) -> Optional[str]:
    dual = []
    for room_id, room_data in layout_dict.items():
        fur = set(room_data.get("furniture", []))
        dev = set(room_data.get("devices", []))
        both = fur & dev
        if both:
            dual.append((room_id, list(both)))
    if not dual:
        return None
    return "硬校验失败：以下房间中存在同时出现在 furniture 与 devices 的 ID（每个 ID 只能属于其一）：" + json.dumps(dual, ensure_ascii=False)


# 硬校验：有对外的室内房间是否含 window。indoor_rooms 为约定名单，可后续迁至配置。
def _hard_check_windows(layout_dict: Dict[str, Any]) -> Optional[str]:
    indoor_rooms = ["living_room", "master_bedroom", "study_room", "kitchen", "bathroom", "entry_hall"]
    missing = []
    for room_id in indoor_rooms:
        if room_id not in layout_dict:
            continue
        room_data = layout_dict[room_id]
        all_ids = room_data.get("furniture", []) + room_data.get("devices", [])
        if not any("window" in str(x).lower() for x in all_ids):
            missing.append(room_id)
    if not missing:
        return None
    return "硬校验失败：以下房间缺少窗户（须含 window_*）：" + ", ".join(missing)


MAX_LAYOUT_REVISIONS = 3

def run_layout_with_validation():
    """先执行逻辑修正，再多级校验；未通过则修正后重新校验，最多 MAX_LAYOUT_REVISIONS 轮。"""
    profile = load_json_file("profile.json")
    layout = load_json_file("house_layout.json")
    profile_str = json.dumps(profile, ensure_ascii=False)
    layout_str = json.dumps(layout, ensure_ascii=False)

    print(">>> 1. 逻辑审查与修正 (Logic Inspection)...")
    fixed = run_logic_fixer_agent()
    layout_dict = _layout_to_dict(fixed)
    if not layout_dict:
        layout_dict = layout
    # 程序化修复 ID：跨房间重复重命名、同房间双列只保留在 devices
    layout_dict = _normalize_layout_ids(layout_dict)

    for rev in range(MAX_LAYOUT_REVISIONS):
        # 硬校验
        err_dup = _hard_check_duplicate_ids(layout_dict)
        err_dual = _hard_check_same_room_dual_list(layout_dict)
        err_win = _hard_check_windows(layout_dict)
        if err_dup or err_dual or err_win:
            validation_result = LayoutValidationResult(is_valid=False, correction_content=(err_dup or "") + " " + (err_dual or "") + " " + (err_win or ""))
        else:
            validation_result = validate_layout_agent(layout_dict, profile_str)

        if validation_result.is_valid:
            print(f">>> 校验通过 (第 {rev + 1} 轮)。")
            return layout_dict
        print(f">>> 校验未通过 (第 {rev + 1} 轮): {validation_result.correction_content[:120]}...")
        if rev + 1 >= MAX_LAYOUT_REVISIONS:
            print("[WARN] 已达最大修正轮数，保存当前结果。")
            return layout_dict
        print(">>> 执行修正...")
        layout_dict = correct_layout_agent(layout_dict, profile_str, validation_result.correction_content or "")
        layout_dict = _normalize_layout_ids(layout_dict)

    return layout_dict

# ==========================================
# 4. 主程序入口
# ==========================================

if __name__ == "__main__":
    # 运行逻辑修正 + 多级校验与修正（ID 唯一、窗户、与 profile 一致）
    final_layout = run_layout_with_validation()
    # 确保 environment_state 中 hygiene/air_freshness 有合理默认值（非 0）
    _normalize_environment_state_defaults(final_layout)

    # 打印差异（可选，简单对比一下家具数量）
    print("\n>>> 修正摘要:")
    try:
        old_layout = load_json_file("house_layout.json")
        for room_id, room_data in final_layout.items():
            old_count = len(old_layout.get(room_id, {}).get("furniture", [])) if room_id in old_layout else 0
            new_count = len(room_data.get("furniture", []))
            if new_count > old_count:
                print(f"  - {room_id}: 家具增加了 {new_count - old_count} 件")
            elif old_count == 0 and new_count > 0:
                print(f"  - {room_id}: [新增房间]")
    except:
        pass

    # 保存覆盖原文件
    save_json_file(final_layout, "house_layout.json")
