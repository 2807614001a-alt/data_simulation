import os
import json
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any, Union
from typing_extensions import TypedDict
from datetime import datetime, timedelta

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()
current_dir = Path(__file__).resolve().parent
dotenv_path = current_dir.parent / '.env'
load_dotenv(dotenv_path=dotenv_path)

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==========================================
# 1. 核心提示词 (Prompts)
# ==========================================

DEVICE_OPERATE_SYS_PROMPT = """
你是一个智能家居的底层控制系统（IoT Controller）。
你的任务是根据用户的行为事件（Event），生成具体的设备状态变更指令（Signals）。

## 输入信息
1. **当前事件**: 用户正在做什么，持续多久。
2. **设备当前状态**: 涉及设备的当前快照。
3. **交互规则**: 物理世界的基本法则（如：只有打开盖子才能取东西）。

## 输出要求
生成一个 JSON 列表，包含在该事件时间段内发生的所有设备状态变更。
- **Start Signals**: 事件开始时发生的改变（如：按下开关）。
- **End Signals** (可选): 事件结束时发生的改变（如：随手关灯、关火）。
- **Intermediate**: 如果事件很长，中间的状态变化。

## 规则
1. **时间戳**: 必须严格在 Event 的 start_time 和 end_time 之间。
2. **状态一致性**: 如果设备已经是 "on"，不要重复发送 "turn_on" 指令，除非模式改变。
3. **补全逻辑**: 如果事件是 "做饭"，隐含了 "开火 -> 烹饪 -> 关火" 的完整闭环。如果事件只是 "打开电视"，则不需要生成关闭指令。
4. **Patch 格式**: 使用 Key-Value 列表来描述变化。

## 示例
事件: 12:00-12:30 在厨房做饭 (Stove)。
输出:
[
  {{
    "timestamp": "12:00:00", 
    "device_id": "stove", 
    "patch_items": [{{"key": "power", "value": "on"}}, {{"key": "mode", "value": "cook"}}], 
    "reason": "开始做饭"
  }},
  {{
    "timestamp": "12:30:00", 
    "device_id": "stove", 
    "patch_items": [{{"key": "power", "value": "off"}}], 
    "reason": "做饭结束"
  }}
]
"""

# ==========================================
# 2. 数据结构 (Pydantic Models)
# ==========================================

class PatchItem(BaseModel):
    key: str = Field(description="状态属性名, e.g. 'power', 'temperature'")
    value: str = Field(description="状态属性值, e.g. 'on', '23'. 统一使用字符串表示")

class DeviceSignal(BaseModel):
    timestamp: str = Field(description="ISO格式时间，必须在事件范围内")
    device_id: str = Field(description="设备ID")
    patch_items: List[PatchItem] = Field(description="状态变更列表")
    reason: str = Field(description="变更原因简述")

    def get_patch_dict(self) -> Dict[str, Any]:
        return {item.key: item.value for item in self.patch_items}

class SignalResponse(BaseModel):
    signals: List[DeviceSignal]

# ==========================================
# 3. 状态管理器 (Pure Device State Machine)
# ==========================================

class StateManager:
    def __init__(self, house_details_map: Dict):
        self.device_states = {}  # Only World State
        
        # 初始化设备状态
        for device_id, details in house_details_map.items():
            self.device_states[device_id] = details.get("current_state", {}).copy()

    def get_involved_states(self, object_ids: List[str]) -> Dict:
        """只获取当前事件涉及的设备状态"""
        snapshot = {}
        for oid in object_ids:
            snapshot[oid] = self.device_states.get(oid, {})
        return snapshot

    def apply_patch(self, device_id: str, patch: Dict):
        """应用状态补丁"""
        if device_id not in self.device_states:
            self.device_states[device_id] = {}
        
        # 字典更新
        self.device_states[device_id].update(patch)

    def get_full_snapshot(self):
        """获取当前所有设备的完整状态快照"""
        # 返回深拷贝，防止日志被后续修改污染
        return self.device_states.copy()

# ==========================================
# 4. 辅助函数
# ==========================================

def load_settings_data(project_root: Path) -> Dict[str, Any]:
    """加载配置数据"""
    settings_path = project_root / "settings"
    data = {"house_details_map": {}, "interaction_rules": []}

    # Load Details
    if (settings_path / "house_details.json").exists():
        with open(settings_path / "house_details.json", 'r', encoding='utf-8') as f:
            details_list = json.load(f)
            for item in details_list:
                item_id = item.get("furniture_id") or item.get("device_id")
                if item_id:
                    data["house_details_map"][item_id] = item

    # Load Rules
    if (settings_path / "interaction_rules.json").exists():
        with open(settings_path / "interaction_rules.json", 'r', encoding='utf-8') as f:
            content = json.load(f)
            data["interaction_rules"] = content.get("interaction_rules", [])
            
    return data

# ==========================================
# 5. 主逻辑
# ==========================================

def run_device_simulation():
    project_root = Path(__file__).resolve().parent.parent
    
    # 1. 加载配置
    settings = load_settings_data(project_root)
    if not settings["house_details_map"]:
        logger.warning("⚠️ House Details is empty or not loaded correctly.")

    state_manager = StateManager(settings["house_details_map"])
    
    # 2. 加载上一层生成的 Events
    events_file = project_root / "data" / "final_events_full_day.json"
    
    if not events_file.exists():
        events_file = project_root / "data" / "events.json"
        
    if not events_file.exists():
        logger.error("❌ No events file found. Please run Layer 3 (event_decomposition.py) first.")
        return

    with open(events_file, 'r', encoding='utf-8') as f:
        events_list = json.load(f)

    logger.info(f"🚀 Starting Device Simulation Layer for {len(events_list)} events...")

    # 初始化 LLM
    llm = ChatOpenAI(model="gpt-4o", temperature=0.3)
    structured_llm = llm.with_structured_output(SignalResponse)
    
    all_signals = []
    full_state_log = []

    # 3. 逐事件推进仿真
    for index, event in enumerate(events_list):
        desc_short = event.get('description', '')[:20]
        logger.info(f"--- Processing Event [{index+1}/{len(events_list)}]: {desc_short}... ---")
        
        target_ids = event.get("target_object_ids", [])
        
        # --- 过滤逻辑 ---
        # 如果没有交互对象，或是在外面，不调用 LLM，也不产生状态变更日志
        # (因为现在只关心 device update 触发的日志)
        if not target_ids or event.get("room_id") == "Outside":
            continue

        # --- Step A: 准备 LLM 上下文 ---
        current_device_snapshot = state_manager.get_involved_states(target_ids)
        
        # 截断规则防止 Token 溢出
        rules_str = json.dumps(settings["interaction_rules"], ensure_ascii=False)
        if len(rules_str) > 3000:
            rules_str = rules_str[:3000] + "... (truncated)"

        prompt = ChatPromptTemplate.from_messages([
            ("system", DEVICE_OPERATE_SYS_PROMPT),
            ("human", """
            Current Event: {event_json}
            Involved Devices Current State: {device_states_json}
            Interaction Rules (Reference): {rules_json}
            """)
        ])
        
        chain = prompt | structured_llm
        
        try:
            result = chain.invoke({
                "event_json": json.dumps(event, ensure_ascii=False),
                "device_states_json": json.dumps(current_device_snapshot, ensure_ascii=False),
                "rules_json": rules_str
            })
            
            # --- Step B: 逐个 Signal 应用并记录快照 ---
            if result.signals:
                for signal in result.signals:
                    patch_dict = signal.get_patch_dict()
                    
                    # 1. 应用变更到内存状态机
                    state_manager.apply_patch(signal.device_id, patch_dict)
                    
                    # 2. 收集增量信号 (Signals)
                    signal_record = {
                        "timestamp": signal.timestamp,
                        "device_id": signal.device_id,
                        "patch": patch_dict,  
                        "reason": signal.reason,
                        "event_id": event.get("activity_id", "unknown")
                    }
                    all_signals.append(signal_record)
                    
                    # 3. 【关键修改】每次变更后，立即记录当前全量状态 (Snapshots)
                    # 这样日志就是由"设备更新"驱动的，而非时间驱动
                    log_entry = {
                        "timestamp": signal.timestamp,  # 使用信号发生的时间
                        "trigger_device": signal.device_id, # 标记是谁触发了这次快照
                        "change_reason": signal.reason,
                        "devices_state": state_manager.get_full_snapshot()
                    }
                    full_state_log.append(log_entry)
                    
                    logger.info(f"📡 Signal & Snapshot: {signal.device_id} -> {patch_dict}")

        except Exception as e:
            logger.error(f"❌ Error generating signals: {e}")

    # 4. 保存结果
    output_dir = project_root / "data"
    
    # 保存信号流
    with open(output_dir / "device_signals.json", "w", encoding="utf-8") as f:
        json.dump(all_signals, f, indent=2, ensure_ascii=False)
        
    # 保存状态机日志 (事件驱动版)
    with open(output_dir / "simulation_state_log.json", "w", encoding="utf-8") as f:
        json.dump(full_state_log, f, indent=2, ensure_ascii=False)

    logger.info(f"✅ Simulation Complete. Generated {len(all_signals)} signals.")
    logger.info(f"📂 Check 'data/device_signals.json' and 'data/simulation_state_log.json'")

if __name__ == "__main__":
    run_device_simulation()