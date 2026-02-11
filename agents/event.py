import os
import json
import logging
import sys
import time
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
)
from physics_engine import calculate_room_state

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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


def _default_room_state(ts: Any = None) -> Dict[str, Any]:
    return {
        "temperature": 24.0,
        "humidity": 0.5,
        "hygiene": 0.7,
        "last_update_ts": ts,
    }


def _update_room_environments_and_format(
    target_rooms: List[str],
    activity_start_time: str,
    environment_snapshot: Dict,
    outdoor_weather: Dict,
    details_map: Dict,
) -> tuple:
    """
    懒更新：对 target_rooms 从上次时间推到 activity_start_time，返回 (updated_snapshot, current_room_environment_str)。
    当前未注入设备状态，仅自然衰减 + 室外趋近。
    """
    snapshot = dict(environment_snapshot)
    outdoor = outdoor_weather or {}
    lines = []
    for room_id in target_rooms:
        if room_id == "Outside":
            continue
        last_state = snapshot.get(room_id) or _default_room_state(activity_start_time)
        last_ts = last_state.get("last_update_ts") or activity_start_time
        new_state = calculate_room_state(
            current_state=last_state,
            last_update_time=last_ts,
            current_time=activity_start_time,
            active_devices=[],
            details_map=details_map,
            outdoor_weather=outdoor,
        )
        snapshot[room_id] = new_state
        lines.append(
            f"- **{room_id}**: 温度 {new_state['temperature']}°C, 湿度 {new_state['humidity']*100:.0f}%, 清洁度 {new_state['hygiene']:.2f}"
        )
    if not lines:
        text = "（当前活动无室内房间或为外出；无房间环境数据。）"
    else:
        text = "\n".join(lines)
    return snapshot, text


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
    
    # 2. 懒更新房间环境并生成「当前房间环境」描述
    activity_start = state["current_activity"].get("start_time", "")
    snapshot = state.get("environment_snapshot") or {}
    outdoor = state.get("outdoor_weather") or {}
    details_map = state.get("details_map") or {}
    updated_snapshot, room_env_text = _update_room_environments_and_format(
        target_rooms, activity_start, snapshot, outdoor, details_map
    )
    try:
        profile = json.loads(state["resident_profile"])
        pref = profile.get("preferences", {})
        comfort_temp = pref.get("home_temperature", 24)
        room_env_text += f"\n**居民舒适温度偏好**: {comfort_temp}°C。若 |室温 - 舒适温度| > 2 建议插入调节事件（如开/关空调、开窗）。"
    except Exception:
        room_env_text += "\n**居民舒适温度偏好**: 24°C（默认）。"
    
    # 3. 调用 LLM
    prompt = ChatPromptTemplate.from_template(EVENT_GENERATION_PROMPT_TEMPLATE)
    structured_llm = llm.with_structured_output(EventSequence, method="json_schema", strict=True)
    chain = prompt | structured_llm
    
    activity_str = json.dumps(state["current_activity"], ensure_ascii=False)
    prev_events_str = json.dumps(state["previous_events"][-2:], ensure_ascii=False) if state["previous_events"] else "[]"
    
    print("  [LLM] Generating events (may take 10-60s)...", flush=True)
    result = chain.invoke({
        "event_requirements": EVENT_REQUIREMENTS,
        "resident_profile_json": state["resident_profile"],
        "agent_state_json": state.get("agent_state_json", "{}"),
        "room_list_json": context_data["room_list_json"],
        "furniture_details_json": context_data["furniture_details_json"],
        "current_room_environment": room_env_text,
        "current_activity_json": activity_str,
        "context_size": 5,
        "previous_events_context": prev_events_str
    })
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
        }
        chars = _estimate_prompt_chars(EVENT_GENERATION_PROMPT_TEMPLATE, vars_for_count)
        logger.info(f"LLM input size (event generate): ~{chars} chars (~{chars//4} tokens)")
    except Exception:
        pass

    _sanitize_events(result.events, state["full_layout"])

    return {
        "current_events": result,
        "room_context_data": context_data,
        "revision_count": 0,
        "environment_snapshot": updated_snapshot,
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
    result = chain.invoke({
        "event_requirements": EVENT_REQUIREMENTS,
        "house_layout_summary": layout_summary,
        "current_activity_json": activity_str,
        "agent_state_json": state.get("agent_state_json", "{}"),
        "events_json": events_json
    })
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

    
    if result.is_valid:
        logger.info("[OK] Validation Passed!")
    else:
        logger.warning(f"[FAIL] Validation Failed: {result.correction_content[:100]}...")
        
    return {"validation_result": result}

def correct_events_node(state: EventState):
    logger.info(f"[Step 3] Correcting Events (Attempt {state['revision_count'] + 1})...")
    prompt = ChatPromptTemplate.from_template(EVENT_CORRECTION_PROMPT_TEMPLATE)
    structured_llm = llm.with_structured_output(EventSequence, method="json_schema", strict=True)
    chain = prompt | structured_llm
    
    events_json = state["current_events"].model_dump_json()
    activity_str = json.dumps(state["current_activity"], ensure_ascii=False)
    layout_summary = state["room_context_data"]["furniture_details_json"]
    
    print("  [LLM] Correcting events (may take 10-40s)...", flush=True)
    result = chain.invoke({
        "event_requirements": EVENT_REQUIREMENTS,
        "resident_profile_json": state["resident_profile"],
        "furniture_details_json": layout_summary,
        "current_activity_json": activity_str,
        "agent_state_json": state.get("agent_state_json", "{}"),
        "original_events_json": events_json,
        "correction_content": state["validation_result"].correction_content
    })
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

    
    return {
        "current_events": result,
        "revision_count": state["revision_count"] + 1
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
    environment_snapshot = {}
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
        outdoor_weather = {"temperature": 28.0, "humidity": 0.6}

    def _process_one(index: int, activity: Dict, prev_events: List[Dict], env_snapshot: Dict):
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
        }

        if SKIP_EVENT_VALIDATION:
            gen_result = generate_events_node(state)
            if gen_result.get("current_events"):
                new_events = gen_result["current_events"].model_dump()["events"]
                return index, activity, new_events, None, gen_result.get("environment_snapshot") or env_snapshot
            return index, activity, None, "no_events", env_snapshot

        final_state = app.invoke(state)
        if final_state.get("current_events"):
            new_events = final_state["current_events"].model_dump()["events"]
            upd = final_state.get("environment_snapshot") or env_snapshot
            return index, activity, new_events, None, upd
        return index, activity, None, "no_events", env_snapshot

    for index, activity in enumerate(activities_list):
        print(f"--- Processing [{index+1}/{len(activities_list)}]: {activity['activity_name']} ---", flush=True)
        last_exc = None
        for attempt in range(LLM_RETRY_COUNT + 1):
            try:
                idx, act, new_events, err, updated_snapshot = _process_one(
                    index, activity, context_events_buffer, environment_snapshot
                )
                if err or not new_events:
                    logger.error(f"[ERROR] Failed to generate events for {activity['activity_name']}")
                    break
                aid = act.get("activity_id", "")
                if aid and updated_snapshot:
                    snapshot_at_activity_start[aid] = {k: dict(v) for k, v in (updated_snapshot or {}).items()}
                environment_snapshot.update(updated_snapshot or {})
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
                    else:
                        logger.error(f"[ERROR] Error processing activity {activity['activity_id']}: {e}")
                    import traceback
                    traceback.print_exc()
                    break

    # 3. 为每个 event 附加「推理时该房间的环境」(温度/湿度/清洁度)，便于查看环境是否参与推理
    for ev in all_generated_events:
        aid = ev.get("activity_id")
        rid = ev.get("room_id")
        if aid and rid and rid != "Outside":
            snap = snapshot_at_activity_start.get(aid, {}).get(rid)
            if snap:
                ev["room_environment"] = {
                    "temperature": snap.get("temperature"),
                    "humidity": snap.get("humidity"),
                    "hygiene": snap.get("hygiene"),
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
    
    print(f"\n All done! Total {len(all_generated_events)} events generated.")
    print(f" Result saved to: {output_file}")

if __name__ == "__main__":
    run_batch_processing()
