import os
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

# 加载环境变量
current_dir = Path(__file__).resolve().parent
dotenv_path = current_dir.parent / '.env'
load_dotenv(dotenv_path=dotenv_path)

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==========================================
# 1. 定义输出数据结构 (Pydantic Models)
# ==========================================

class PatchItem(BaseModel):
    key: str = Field(description="状态属性名, e.g. 'power', 'temperature'")
    value: str = Field(description="状态属性值, e.g. 'on', '23'. 统一转为字符串")

class DevicePatch(BaseModel):
    timestamp: str = Field(description="该操作发生的时间点 (ISO格式)")
    device_id: str = Field(description="设备ID")
    # 【修复】使用 List[PatchItem] 替代 Dict[str, Any] 避免 OpenAI 400 错误
    patch_items: List[PatchItem] = Field(description="状态变更内容列表")

class EventDeviceState(BaseModel):
    patch_on_start: List[DevicePatch] = Field(description="事件开始时刻发生的设备变更")
    patch_on_end: List[DevicePatch] = Field(description="事件结束时刻发生的设备变更")

# ==========================================
# 2. 核心提示词 (Prompt)
# ==========================================

DEVICE_STATE_GEN_PROMPT = """
你是一个智能家居行为分析器。
请根据用户的【行为事件】，推断设备应该在【开始】和【结束】时发生什么状态变化。

## 输入数据
- **Event**: {description}
- **Time**: {start_time} 至 {end_time}
- **Devices**: {target_devices}
- **Reference**: {device_details}

## 任务要求
1. **Patch on Start**: 事件开始时，设备状态如何改变？(例如：打开电源、设置模式、打开门)
2. **Patch on End**: 事件结束时，设备状态如何改变？(例如：关闭电源、关闭门)。如果行为不需要关闭(如持续运行)，则列表为空。
3. **Timestamp**: 
   - Start Patch 的时间戳必须是 {start_time}。
   - End Patch 的时间戳必须是 {end_time}。
4. **格式规则**: 
   - 由于输出限制，请将状态变化拆解为 key-value 列表 (patch_items)。
   - 例如：`{{"key": "power", "value": "on"}}`

## 输出示例
Event: 做饭 (Stove)
Time: 08:00 - 08:30
Result:
{{
  "patch_on_start": [
    {{
      "timestamp": "08:00", 
      "device_id": "stove", 
      "patch_items": [
        {{"key": "power", "value": "on"}}, 
        {{"key": "mode", "value": "cook"}}
      ]
    }}
  ],
  "patch_on_end": [
    {{
      "timestamp": "08:30", 
      "device_id": "stove", 
      "patch_items": [
        {{"key": "power", "value": "off"}}
      ]
    }}
  ]
}}
"""

# ==========================================
# 3. 辅助函数
# ==========================================

def load_settings_data(project_root: Path) -> Dict[str, Any]:
    """加载配置数据"""
    settings_path = project_root / "settings"
    data = {"house_details_map": {}}

    if (settings_path / "house_details.json").exists():
        with open(settings_path / "house_details.json", 'r', encoding='utf-8') as f:
            details_list = json.load(f)
            for item in details_list:
                item_id = item.get("furniture_id") or item.get("device_id")
                if item_id:
                    data["house_details_map"][item_id] = item
    return data

def get_device_context(target_ids: List[str], details_map: Dict) -> str:
    """获取涉及设备的简要信息"""
    context = []
    for tid in target_ids:
        if tid in details_map:
            info = details_map[tid]
            context.append(f"{tid} ({info.get('name', 'Unknown')}) - Supports: {info.get('support_actions', [])}")
    return "; ".join(context)

def convert_patch_to_dict(patch_obj: DevicePatch) -> Dict:
    """【后处理】将 PatchItem 列表转回 Dict 格式，符合最终 JSON 输出要求"""
    kv_dict = {item.key: item.value for item in patch_obj.patch_items}
    return {
        "timestamp": patch_obj.timestamp,
        "device_id": patch_obj.device_id,
        "patch": kv_dict  # 转换回 {"power": "on"}
    }

# ==========================================
# 4. 主逻辑
# ==========================================

def run_event_chain_generation():
    project_root = Path(__file__).resolve().parent.parent
    
    settings = load_settings_data(project_root)
    events_file = project_root / "data" / "final_events_full_day.json"
    
    if not events_file.exists():
        events_file = project_root / "data" / "events.json"
        
    if not events_file.exists():
        logger.error("❌ No events file found. Please run Layer 3 first.")
        return

    with open(events_file, 'r', encoding='utf-8') as f:
        events_list = json.load(f)

    logger.info(f"🚀 Generating Action Event Chain for {len(events_list)} events...")

    # 初始化 LLM
    llm = ChatOpenAI(model="gpt-4o", temperature=0.3)
    structured_llm = llm.with_structured_output(EventDeviceState)
    
    final_chain = []

    for index, event in enumerate(events_list):
        target_ids = event.get("target_object_ids", [])
        is_outside = event.get("room_id") == "Outside"
        # 简单过滤：只有明确涉及设备且非外出事件才处理
        use_devices = len(target_ids) > 0 and not is_outside
        
        event_output = {
            "event_id": event.get("activity_id", f"evt_{index:03d}"),
            "room_id": event.get("room_id"),
            "start_time": event.get("start_time"),
            "end_time": event.get("end_time"),
            "description": event.get("description"),
            "use_devices": use_devices,
            "devices": target_ids,
            "layer5_device_state": {
                "patch_on_start": [],
                "patch_on_end": []
            }
        }

        if use_devices:
            desc_short = event.get('description', '')[:20]
            logger.info(f"⚡ Analyzing Devices for Event [{index+1}]: {desc_short}...")
            
            device_context = get_device_context(target_ids, settings["house_details_map"])
            
            prompt = ChatPromptTemplate.from_template(DEVICE_STATE_GEN_PROMPT)
            chain = prompt | structured_llm
            
            try:
                result = chain.invoke({
                    "description": event.get("description"),
                    "start_time": event.get("start_time"),
                    "end_time": event.get("end_time"),
                    "target_devices": ", ".join(target_ids),
                    "device_details": device_context
                })
                
                # 【关键修复】: 手动将 LLM 输出的 List[Item] 结构转回 Dict 结构
                start_patches = [convert_patch_to_dict(p) for p in result.patch_on_start]
                end_patches = [convert_patch_to_dict(p) for p in result.patch_on_end]
                
                event_output["layer5_device_state"] = {
                    "patch_on_start": start_patches,
                    "patch_on_end": end_patches
                }
                
            except Exception as e:
                logger.error(f"❌ LLM Error on event {index}: {e}")
        
        final_chain.append(event_output)

    # 输出结果
    output_data = {"action_event_chain": final_chain}
    output_path = project_root / "data" / "action_event_chain.json"
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    logger.info(f"✅ Generated {len(final_chain)} event chains.")
    logger.info(f"📂 Result saved to: {output_path}")

if __name__ == "__main__":
    run_event_chain_generation()