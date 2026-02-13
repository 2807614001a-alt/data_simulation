import sys
from pathlib import Path

_current_dir = Path(__file__).resolve().parent
_project_root = _current_dir.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from typing import List, Dict, Any, Union, Optional

from llm_utils import create_fast_llm
from agent_config import DEFAULT_MODEL, SETTINGS_DEFAULT_TEMPERATURE, SETTINGS_USE_RESPONSES_API, MAX_WORKERS_DEFAULT
from prompt import LAYOUT2DETAILS_ROOM_PROMPT_TEMPLATE
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

# --- A. 家具相关结构 ---
class FurnitureState(BaseModel):
    temperature: float = Field(description="表面温度")
    items_on: List[str] = Field(description="上面放置的物品列表，如 ['book', 'cup']", default_factory=list)

class FurnitureItem(BaseModel):
    furniture_id: str = Field(description="必须与输入列表中的ID完全一致")
    name: str = Field(description="家具中文名称")
    room: str = Field(description="所属房间")
    support_actions: List[str] = Field(description="支持的动作，如 ['sit', 'sleep']")
    comfort_level: float = Field(description="舒适度 0.0-1.0")
    current_state: FurnitureState
    environmental_regulation: List[EnvironmentalRegulationItem] = Field(default_factory=list, description="对室内环境的影响，如扫地机器人提升清洁度")

# --- B. 设备相关结构 ---
class DeviceState(BaseModel):
    power: str = Field(description="'on' 或 'off'")
    temperature_set: Optional[float] = Field(description="设定温度，非温控设备可为null")
    mode: Optional[str] = Field(description="工作模式")
    fan_speed: Optional[str] = Field(description="风速")

class DeviceItem(BaseModel):
    device_id: str = Field(description="必须与输入列表中的ID完全一致")
    name: str = Field(description="设备中文名称")
    room: str = Field(description="所属房间")
    support_actions: List[str] = Field(description="支持的操作，如 ['turn_on', 'set_temp']")
    current_state: DeviceState
    environmental_regulation: List[EnvironmentalRegulationItem] = Field(default_factory=list, description="对室内环境的影响，如空调制冷时每分钟降温")

# --- C. 容器结构 (用于解析器的联合类型输出) ---
class RoomItemsDetail(BaseModel):
    # 我们希望得到一个混合列表
    items: List[Union[FurnitureItem, DeviceItem]] = Field(description="家具和设备的详细属性列表")

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
            model=DEFAULT_MODEL,
            temperature=SETTINGS_DEFAULT_TEMPERATURE,
            use_responses_api=SETTINGS_USE_RESPONSES_API,
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


def _clean_current_state(items: List[dict]) -> None:
    """仅做 schema 级规范化：power 若存在则限定为 on/off；不按设备名硬编码。具体哪些物品应有 power 由提示词约束、由 LLM 生成时自行判断。"""
    for it in items:
        state = it.get("current_state")
        if not isinstance(state, dict):
            continue
        state = dict(state)
        if "power" in state and state.get("power") not in ("on", "off"):
            state["power"] = "off"
        it["current_state"] = state


def _fill_empty_support_actions(items: List[dict]) -> None:
    """若某物品的 support_actions 为空，仅做边界兜底：填入通用动作，不按设备类型写死。具体应由 prompt 约束 LLM 生成非空。"""
    for it in items:
        acts = it.get("support_actions")
        if acts is not None and len(acts) > 0:
            continue
        it["support_actions"] = ["use", "interact"]
        rid = _item_id(it) or (it.get("name") or "unknown")
        print(f"  [补全] {rid} support_actions 为空，已填入通用兜底: ['use', 'interact']")


def _validate_and_clean_env_regulation(items: List[dict]) -> None:
    """仅按 schema/物理因果做边界清理：去掉 delta=0、同条件同 target 合并、按属性语义修正符号（heat→temperature 为正、开窗→air_freshness 为正）。不按设备名写死；「谁该有 regulation」由 prompt 约束。"""
    for it in items:
        regs = it.get("environmental_regulation") or []
        if not regs:
            continue

        new_regs = []
        for r in regs:
            if not isinstance(r, dict):
                continue
            r = dict(r)
            cond = r.get("working_condition") or {}
            attr = (r.get("target_attribute") or "").lower()
            delta = r.get("delta_per_minute", 0)
            # temperature 且有 target_value 时保留（物理引擎按目标值指数趋近），否则 delta=0 的条目丢弃
            if delta == 0 and not (attr == "temperature" and isinstance(r.get("target_value"), (int, float))):
                continue
            # 按属性语义修正符号（边界规则，非设备名单）
            if attr == "temperature":
                cond_mode = (cond.get("mode") or "").lower() if isinstance(cond, dict) else ""
                if "heat" in cond_mode or cond_mode == "heat":
                    if delta < 0:
                        r["delta_per_minute"] = abs(delta)
            if attr == "air_freshness":
                state_val = (cond.get("state") or cond.get("open") or "").lower() if isinstance(cond, dict) else ""
                if "open" in state_val or state_val == "open":
                    if delta < 0:
                        r["delta_per_minute"] = abs(delta)
            new_regs.append(r)

        # 同一 working_condition 下同一 target_attribute 只保留一条（合并 delta）
        key_to_reg: Dict[tuple, dict] = {}
        for r in new_regs:
            if not isinstance(r, dict):
                continue
            cond = r.get("working_condition") or {}
            try:
                cond_key = json.dumps(cond, sort_keys=True) if isinstance(cond, dict) else str(cond)
            except (TypeError, ValueError):
                cond_key = str(cond)
            k = (cond_key, (r.get("target_attribute") or "").strip())
            if k not in key_to_reg:
                key_to_reg[k] = dict(r)
            else:
                existing = key_to_reg[k]
                # temperature 的 target_value 保留先出现的
                if attr == "temperature" and isinstance(existing.get("target_value"), (int, float)):
                    pass
                elif attr == "temperature" and isinstance(r.get("target_value"), (int, float)):
                    existing["target_value"] = r["target_value"]
                else:
                    existing["delta_per_minute"] = existing.get("delta_per_minute", 0) + r.get("delta_per_minute", 0)
        it["environmental_regulation"] = list(key_to_reg.values())


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
# 3. 核心 Agent：生成单个房间的物品详情
# ==========================================

# ==========================================
# 3. 核心 Agent：生成单个房间的物品详情
# ==========================================

def process_single_room(llm, profile_str, room_id, room_data):
    """
    处理单个房间：接收房间的 ID 列表，生成详细属性（极速 JSON 模式：with_structured_output）
    """
    # strict=False：items 为 List[Union[FurnitureItem, DeviceItem]]，避免 Union/动态结构触犯 API 校验
    structured_chain = llm.with_structured_output(
        RoomItemsDetail, method="json_schema", strict=False
    )

    # 提取上一步生成的 ID 列表
    furniture_ids = room_data.get("furniture", [])
    device_ids = room_data.get("devices", [])
    
    # 如果房间是空的，直接返回空列表
    if not furniture_ids and not device_ids:
        return []

    prompt = ChatPromptTemplate.from_template(LAYOUT2DETAILS_ROOM_PROMPT_TEMPLATE)
    chain = prompt | structured_chain

    print(f" -> 正在生成详情: {room_id} (包含 {len(furniture_ids)} 家具, {len(device_ids)} 设备)...")

    try:
        result = chain.invoke({
            "profile_context": profile_str,
            "room_id": room_id,
            "room_type": room_data.get("room_type", "未知房间"),
            "furniture_list": json.dumps(furniture_ids),
            "device_list": json.dumps(device_ids)
        })
        def _to_dict(x):
            if hasattr(x, "model_dump"):
                return x.model_dump()
            return x if isinstance(x, dict) else {}

        if isinstance(result, RoomItemsDetail):
            items = result.items or []
            return [_to_dict(it) for it in items]
        if isinstance(result, list):
            return [_to_dict(it) for it in result]
        if isinstance(result, dict):
            items = result.get("items", [])
            return [_to_dict(it) for it in items]
        print(f"  [警告] {room_id} 返回了无法识别的格式: {type(result)}")
        return []
        
    except Exception as e:
        # 打印完整的错误栈以便调试（可选）
        # import traceback
        # traceback.print_exc()
        print(f"  [警告] 房间 {room_id} 生成出错: {e}")
        return []
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
    
    # 2. 初始化模型
    llm = get_thread_llm()

    # 3. 数据标准化 (解决 List vs Dict 报错的核心逻辑)
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

    # 4. 循环处理并累积结果
    all_items_flat_list = []
    
    print("\n>>> 2. 开始逐个房间细化物品状态...")
    
    room_items_list = list(rooms_to_process)
    max_workers = get_max_workers(len(room_items_list))

    def _worker(item):
        room_id, room_data = item
        if isinstance(room_data, dict):
            room_items = process_single_room(get_thread_llm(), profile_str, room_id, room_data)
            return room_id, room_items
        return room_id, []

    if max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for _, room_items in executor.map(_worker, room_items_list):
                all_items_flat_list.extend(room_items)
    else:
        for room_id, room_data in room_items_list:
            # ?????? data ?????
            if isinstance(room_data, dict):
                # ?????????
                room_items = process_single_room(llm, profile_str, room_id, room_data)
                all_items_flat_list.extend(room_items)

    # 5. 校验：移除无效条目（delta=0 且非 temperature+target_value）
    for item in all_items_flat_list:
        regs = item.get("environmental_regulation") or []
        if not regs:
            continue
        kept = [
            r for r in regs
            if isinstance(r, dict)
            and (r.get("delta_per_minute", 0) != 0 or (r.get("target_attribute") == "temperature" and isinstance(r.get("target_value"), (int, float))))
        ]
        item["environmental_regulation"] = kept

    # 6. 程序化后处理：与 layout 对齐、去重、补全缺失 ID、name 后缀、校验/清理 regulation、current_state
    layout_item_set = _build_layout_item_set(house_data)
    all_items_flat_list = _normalize_and_dedupe_details(all_items_flat_list, layout_item_set)
    _ensure_layout_details_completeness(all_items_flat_list, layout_item_set, house_data)
    _sync_name_room_suffix(all_items_flat_list, house_data)
    _validate_and_clean_env_regulation(all_items_flat_list)
    _clean_current_state(all_items_flat_list)
    _fill_empty_support_actions(all_items_flat_list)

    # 7. 保存结果
    print(f"\n>>> 3. 处理完成，共生成 {len(all_items_flat_list)} 个物品详情（与 layout 一一对应，已校验 regulation 方向与重复）。")
    save_json_file(all_items_flat_list)

if __name__ == "__main__":
    main()
