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
    target_attribute: str = Field(description="影响的环境属性: temperature, humidity, hygiene 之一")
    delta_per_minute: float = Field(description="每分钟变化量，如 -0.5 表示降温0.5°C/分钟")
    working_condition: Optional[Dict[str, str]] = Field(default=None, description="生效条件，如 power=on 且 mode=cool 时填为键值对，无则省略")

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

    # 5. 保存结果
    print(f"\n>>> 3. 处理完成，共生成 {len(all_items_flat_list)} 个物品详情。")
    save_json_file(all_items_flat_list)

if __name__ == "__main__":
    main()
