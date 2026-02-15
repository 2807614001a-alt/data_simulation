import sys
from pathlib import Path

_current_dir = Path(__file__).resolve().parent
_project_root = _current_dir.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import json
import os
from dotenv import load_dotenv
from typing import List, Dict

from llm_utils import create_fast_llm
from agent_config import DEFAULT_MODEL, SETTINGS_DEFAULT_TEMPERATURE, SETTINGS_USE_RESPONSES_API
from prompt import PROFILE2LAYOUT_PROMPT_TEMPLATE
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

# --- 环境配置 ---
load_dotenv()
dotenv_path = _project_root / ".env"
load_dotenv(dotenv_path=dotenv_path)

# --- 1. 定义数据结构 (Schema) ---

# 1.1 最内层的环境状态
class EnvironmentState(BaseModel):
    temperature: float = Field(description="温度 (摄氏度)")
    humidity: float = Field(description="湿度 (0.0 - 1.0)")
    light_level: float = Field(description="光照强度 (0.0 - 1.0)")
    noise_level: float = Field(description="噪音水平 (0.0 - 1.0)")
    hygiene: float = Field(description="清洁度 (0.0 - 1.0)")
    air_freshness: float = Field(description="空气清新度 (0.0 - 1.0)")

# 1.2 房间详细信息
class RoomInfo(BaseModel):
    room_type: str = Field(description="房间中文类型，例如：客厅、主卧")
    area_sqm: float = Field(description="房间面积 (平方米)")
    # 注意：这里根据你的新要求，改成了字符串列表 (List[str])
    furniture: List[str] = Field(description="家具ID列表，如 ['sofa_001', 'table_001']")
    devices: List[str] = Field(description="设备ID列表，如 ['ac_001', 'light_001']")
    environment_state: EnvironmentState = Field(description="环境传感器状态")

# 1.3 根结构：用列表避免 Dict 动态 key，Responses API 对 strict 校验不接受 rooms: Dict[str, RoomInfo]
class RoomEntry(BaseModel):
    room_id: str = Field(description="房间英文ID，如 living_room, kitchen")
    room_info: RoomInfo = Field(description="该房间的详情")


class HouseSnapshot(BaseModel):
    rooms: List[RoomEntry] = Field(
        default_factory=list,
        description="所有房间的列表，每项含 room_id 与 room_info",
    )

# --- 2. 读取 Profile 与 动态约束 ---
def get_profile_data():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    profile_path = os.path.join(current_dir, "profile.json")
    if os.path.isfile(profile_path):
        with open(profile_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"name": "示例家庭", "needs": ["喜欢看电影", "需要安静的睡眠环境", "智能家居控"]}


# --- 3. 运行设计 Agent ---
def run_architect_agent_json():
    user_profile = get_profile_data()
    profile_str = json.dumps(user_profile, ensure_ascii=False)

    llm = create_fast_llm(
        model=DEFAULT_MODEL,
        temperature=SETTINGS_DEFAULT_TEMPERATURE,
        use_responses_api=SETTINGS_USE_RESPONSES_API,
    )
    structured_chain = llm.with_structured_output(
        HouseSnapshot, method="json_schema", strict=True
    )

    prompt = ChatPromptTemplate.from_template(PROFILE2LAYOUT_PROMPT_TEMPLATE)
    chain = prompt | structured_chain

    print(">>> 正在生成环境状态快照...")
    result = chain.invoke({"profile_context": profile_str})

    # 将 List[RoomEntry] 转为 { room_id: room_info } 以兼容下游
    if hasattr(result, "rooms") and isinstance(result.rooms, list):
        return {e.room_id: e.room_info.model_dump() if hasattr(e.room_info, "model_dump") else e.room_info for e in result.rooms}
    if isinstance(result, dict) and "rooms" in result:
        r = result["rooms"]
        if isinstance(r, list):
            return {e["room_id"]: e.get("room_info", e) for e in r}
        return r
    return result if isinstance(result, dict) else {}

# --- 4. 保存 ---
def save_layout_to_file(data, filename="house_layout.json"):
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n[成功] 文件已保存至: {output_path}")

if __name__ == "__main__":
    snapshot_json = run_architect_agent_json()
    
    print("\n=== 生成的 JSON 结构 ===")
    print(json.dumps(snapshot_json, ensure_ascii=False, indent=2))
    
    save_layout_to_file(snapshot_json)
