import sys
from pathlib import Path

_current_dir = Path(__file__).resolve().parent
_project_root = _current_dir.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import json
import os
from dotenv import load_dotenv
from typing import List, Dict, Any

from llm_utils import create_fast_llm
from agent_config import DEFAULT_MODEL, SETTINGS_DEFAULT_TEMPERATURE, SETTINGS_USE_RESPONSES_API
from prompt import LAYOUT_CHECK_PROMPT_TEMPLATE
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

# ==========================================
# 4. 主程序入口
# ==========================================

if __name__ == "__main__":
    # 运行逻辑修正
    final_layout = run_logic_fixer_agent()
    
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
