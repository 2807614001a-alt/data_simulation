import os
import json
import logging
import sys
from pathlib import Path
from datetime import datetime
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
from settings.llm_utils import create_chat_llm
# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==========================================
# 1. 提示词常量 (保持不变)
# ==========================================

EVENT_REQUIREMENTS = """
## 事件定义 (Event Definition)
事件是连接宏观“活动”与微观“动作”的中间层。它是用户在特定房间内，利用特定设施完成的一个具体子目标。
核心特征：
1. **物体依赖性**：绝大多数居家事件都必须与至少一个家具或设备交互。
2. **物理可行性**：选用的物品必须支持该动作（参考 `support_actions`）。
3. **性格时间观**：事件的耗时应反映居民性格。

## 分解原则
1. **宏观拆解**：将一个 Activity 拆解为逻辑连贯的 Event 序列。
2. **空间一致性**：切换房间必须生成独立的“移动(Move)”事件。
3. **外出闭环**：外出活动（Work, Shopping等）**不进行分解**，保持为一个单独的事件，`room` 设为 "Outside"，`target_objects` 为空。

## 输出格式要求 (JSON List)
每个事件对象包含：
- `activity_id`: 所属父活动的ID
- `start_time`: ISO格式 (YYYY-MM-DDTHH:MM:SS)
- `end_time`: ISO格式 (YYYY-MM-DDTHH:MM:SS)
- `room_id`: 发生的房间ID (必须存在于 layout 中，外出则为 "Outside")
- `target_object_ids`: 关键字段。涉及的家具/设备ID列表。
- `action_type`: ["interact", "move", "idle", "outside"]
- `description`: 详细描述。

## 约束条件
1. **物品功能校验 (Affordance)**：target_object_ids 必须在当前 room_id 内且支持该动作。
2. **时间严丝合缝**：子事件时间加总必须严格等于父 Activity 的时间段。
3. **随机性注入**：基于 Profile 插入合理的微小随机事件。
"""

EVENT_GENERATION_PROMPT_TEMPLATE = """
你是一个具备物理常识和心理学洞察的行为仿真引擎。
请根据【居民档案】的性格特征，将【当前活动】递归拆解为一系列具体的【事件】。

{event_requirements}

## 输入数据 context

### 1. 居民档案 (Agent Profile)
{resident_profile_json}

### 2. 物理环境 (Physical Environment)
**房间列表:**
{room_list_json}
**家具与设备详情 (已过滤为当前相关区域):**
{furniture_details_json}

### 3. 待拆解的父活动 (Parent Activity)
{current_activity_json}

### 4. 上下文 (Context)
**前序事件 (最近{context_size}条):**
{previous_events_context}

## 任务指令
1. **分析意图**：理解父活动 `{current_activity_json}` 的目标。
2. **资源匹配**：在 `room_id` 中寻找最适合完成该目标的 `furniture` 或 `device`。
3. **性格渲染**：根据 Big Five 调整粒度。
4. **生成序列**：输出符合 JSON 格式的事件列表，确保时间连续且填满父活动时段。
"""

EVENT_VALIDATION_PROMPT_TEMPLATE = """
请作为“物理与逻辑审核员”，对以下生成的事件序列进行严格审查。

{event_requirements}

## 待审核数据
**环境数据:**
{house_layout_summary}

**父活动:**
{current_activity_json}

**生成的事件序列:**
{events_json}

## 验证维度
1. **物理可供性**: 物品是否存在？功能是否支持？未在环境数据中标记的房间id，是否成功将除室外一律标记为outside外？
2. **时间完整性**: 总时间是否匹配？无缝衔接？
3. **行为逻辑**: 顺序是否合理？房间切换是否有 Move？
4. **性格一致性**: 是否违背性格设定？

## 返回结果
- Pass: is_valid: true
- Fail: is_valid: false, 并说明 correction_content。
"""

EVENT_CORRECTION_PROMPT_TEMPLATE = """
你是一个专业的行为修正模块。上一次生成的事件序列存在逻辑或物理错误。
请根据验证反馈，重新生成修正后的事件序列。

{event_requirements}

## 参考数据
**居民档案:** {resident_profile_json}
**可用环境物品:** {furniture_details_json}
**父活动:** {current_activity_json}

## 错误现场
**原始错误规划:**
{original_events_json}

**验证反馈 (必须解决的问题):**
{correction_content}

## 修正指令
1. 定位错误。
2. 查找资源 (替代物品)。
3. 调整时间。
4. 保持风格。
"""

# ==========================================
# 2. 数据结构定义 (Pydantic Models)
# ==========================================

class EventItem(BaseModel):
    activity_id: str = Field(description="所属父活动ID")
    start_time: str = Field(description="ISO格式开始时间")
    end_time: str = Field(description="ISO格式结束时间")
    room_id: str = Field(description="发生的房间ID，外出为'Outside'")
    target_object_ids: List[str] = Field(description="交互的物品ID列表")
    action_type: str = Field(description="动作类型: interact, move, idle, outside")
    description: str = Field(description="详细描述，包含动作、物品、性格细节")

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
    print(f"📂 Loading settings from: {settings_path}")

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
                room_items.append({
                    "id": item_id,
                    "name": item_info.get("name", "Unknown"),
                    "support_actions": item_info.get("support_actions", []),
                    "current_state": item_info.get("current_state", {})
                })
            else:
                room_items.append({"id": item_id, "name": "Unknown", "support_actions": []})
        
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
    
    room_context_data: Dict
    current_events: Optional[EventSequence]
    validation_result: Optional[ValidationResult]
    revision_count: int

llm = create_chat_llm(model="gpt-4o", temperature=0.7)

def generate_events_node(state: EventState):
    activity_name = state['current_activity'].get('activity_name', 'Unknown')
    logger.info(f"🎬 [Step 1] Decomposing Activity: {activity_name} ...")
    
    # 1. 裁剪上下文
    target_rooms = state["current_activity"].get("main_rooms", [])
    context_data = get_room_specific_context(
        state["full_layout"], 
        state["details_map"], 
        target_rooms
    )
    
    # 2. 调用 LLM
    prompt = ChatPromptTemplate.from_template(EVENT_GENERATION_PROMPT_TEMPLATE)
    structured_llm = llm.with_structured_output(EventSequence)
    chain = prompt | structured_llm
    
    activity_str = json.dumps(state["current_activity"], ensure_ascii=False)
    # 关键：只取最近 5 个事件作为 Context
    prev_events_str = json.dumps(state["previous_events"][-5:], ensure_ascii=False) if state["previous_events"] else "[]"
    
    result = chain.invoke({
        "event_requirements": EVENT_REQUIREMENTS,
        "resident_profile_json": state["resident_profile"],
        "room_list_json": context_data["room_list_json"],
        "furniture_details_json": context_data["furniture_details_json"],
        "current_activity_json": activity_str,
        "context_size": 5,
        "previous_events_context": prev_events_str
    })
    
    return {
        "current_events": result,
        "room_context_data": context_data,
        "revision_count": 0
    }

def validate_events_node(state: EventState):
    logger.info("🔍 [Step 2] Validating Events...")
    prompt = ChatPromptTemplate.from_template(EVENT_VALIDATION_PROMPT_TEMPLATE)
    structured_llm = llm.with_structured_output(ValidationResult)
    chain = prompt | structured_llm
    
    events_json = state["current_events"].model_dump_json()
    activity_str = json.dumps(state["current_activity"], ensure_ascii=False)
    layout_summary = state["room_context_data"]["furniture_details_json"]
    
    result = chain.invoke({
        "event_requirements": EVENT_REQUIREMENTS,
        "house_layout_summary": layout_summary,
        "current_activity_json": activity_str,
        "events_json": events_json
    })
    
    if result.is_valid:
        logger.info("✅ Validation Passed!")
    else:
        logger.warning(f"❌ Validation Failed: {result.correction_content[:100]}...")
        
    return {"validation_result": result}

def correct_events_node(state: EventState):
    logger.info(f"🛠️ [Step 3] Correcting Events (Attempt {state['revision_count'] + 1})...")
    prompt = ChatPromptTemplate.from_template(EVENT_CORRECTION_PROMPT_TEMPLATE)
    structured_llm = llm.with_structured_output(EventSequence)
    chain = prompt | structured_llm
    
    events_json = state["current_events"].model_dump_json()
    activity_str = json.dumps(state["current_activity"], ensure_ascii=False)
    layout_summary = state["room_context_data"]["furniture_details_json"]
    
    result = chain.invoke({
        "event_requirements": EVENT_REQUIREMENTS,
        "resident_profile_json": state["resident_profile"],
        "furniture_details_json": layout_summary,
        "current_activity_json": activity_str,
        "original_events_json": events_json,
        "correction_content": state["validation_result"].correction_content
    })
    
    return {
        "current_events": result,
        "revision_count": state["revision_count"] + 1
    }

def router(state: EventState):
    if state["validation_result"].is_valid:
        return "end"
    if state["revision_count"] >= 3:
        logger.error("⚠️ Max revisions reached. Skipping this activity.")
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

def run_batch_processing():
    project_root = Path(__file__).resolve().parent.parent
    
    # 1. 加载 Settings
    settings = load_settings_data(project_root)
    if not settings["house_details_map"]:
        logger.warning("⚠️ House Details is empty!")

    # 2. 加载 Activity Data
    activity_file = project_root / "data" / "activity.json"
    if not activity_file.exists():
        logger.error(f"❌ Activity file not found: {activity_file}")
        return

    with open(activity_file, 'r', encoding='utf-8') as f:
        activity_data = json.load(f)
        activities_list = activity_data.get("activities", [])

    print(f"\n🚀 Starting Batch Processing for {len(activities_list)} activities...\n")

    all_generated_events = []
    # 使用 buffer 保持上下文连贯，但避免 token 爆炸
    context_events_buffer = [] 

    # 设定一个模拟日期，补全 JSON 中的 "HH:MM" 格式
    sim_date = "2026-01-26" 

    for index, activity in enumerate(activities_list):
        print(f"--- Processing [{index+1}/{len(activities_list)}]: {activity['activity_name']} ---")
        
        # 【数据预处理】：补全时间格式为 ISO
        # 假设 activity.json 里只有 "06:30"，我们将其补全为 "2026-01-26T06:30:00"
        # 这样 LLM 就会严格遵循这个日期生成 Event
        if len(activity["start_time"]) == 5: # "HH:MM"
             activity["start_time"] = f"{sim_date}T{activity['start_time']}:00"
        if len(activity["end_time"]) == 5:
             activity["end_time"] = f"{sim_date}T{activity['end_time']}:00"

        # 初始化 State
        state = {
            "resident_profile": settings["profile_json"],
            "full_layout": settings["house_layout"],
            "details_map": settings["house_details_map"],
            "current_activity": activity,
            "previous_events": context_events_buffer, # 传入最近的 buffer
            "revision_count": 0
        }

        try:
            # 调用 Graph (针对单个 Activity)
            final_state = app.invoke(state)
            
            if final_state.get("current_events"):
                new_events = final_state["current_events"].model_dump()["events"]
                
                # 1. 收集结果
                all_generated_events.extend(new_events)
                
                # 2. 更新 Buffer (只保留这轮生成的新事件，供下一轮做参考)
                # 如果 new_events 很多，只取最后 5 个
                context_events_buffer = new_events[-5:] 
                
                print(f"✅ Generated {len(new_events)} events for {activity['activity_name']}.")
            else:
                logger.error(f"❌ Failed to generate events for {activity['activity_name']}")

        except Exception as e:
            logger.error(f"❌ Error processing activity {activity['activity_id']}: {e}")
            import traceback
            traceback.print_exc()

    # 3. 保存最终的所有事件
    output_file = project_root / "data" / "events.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_generated_events, f, indent=2, ensure_ascii=False)
    
    print(f"\n🎉 All done! Total {len(all_generated_events)} events generated.")
    print(f"📁 Result saved to: {output_file}")

if __name__ == "__main__":
    run_batch_processing()
