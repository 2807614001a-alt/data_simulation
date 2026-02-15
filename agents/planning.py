import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional
from typing_extensions import TypedDict

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field
from urllib.parse import urlparse
import socket

load_dotenv()

current_dir = Path(__file__).resolve().parent
dotenv_path = current_dir.parent / ".env"
load_dotenv(dotenv_path=dotenv_path)

project_root = current_dir.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from llm_utils import create_fast_llm
from prompt import (
    ACTIVITY_PLANNING_REQUIREMENTS,
    PLANNING_PROMPT_TEMPLATE,
    PLANNING_VALIDATION_PROMPT_TEMPLATE,
    PLANNING_CORRECTION_PROMPT_TEMPLATE,
    SUMMARIZATION_PROMPT_TEMPLATE,
    VALUES_INTERPRETATION_GUIDE,
)
from agent_config import (
    DEFAULT_MODEL,
    PLANNING_TEMPERATURE,
    PLANNING_USE_RESPONSES_API,
    SKIP_PLANNING_VALIDATION,
    MAX_PLANNING_REVISIONS,
)

# ==========================================
# 1. Prompt constants (from prompt.py)
# ==========================================

# ==========================================
# 2. Data models
# ==========================================

class ActivityItem(BaseModel):
    activity_id: str = Field(description="唯一标识, e.g., 'act_001'")
    activity_name: str = Field(description="活动简述")
    start_time: str = Field(description="ISO格式时间")
    end_time: str = Field(description="ISO格式时间")
    description: str = Field(description="详细描述，包含动作、家具设备交互、社交对象等")
    main_rooms: List[str] = Field(description="涉及的房间ID列表")

class ActivityPlan(BaseModel):
    activities: List[ActivityItem]

class ValidationResult(BaseModel):
    is_valid: bool = Field(description="规划是否通过验证")
    correction_content: Optional[str] = Field(description="如未通过，详细修改建议；通过则为空")

class PreviousDaySummary(BaseModel):
    previous_day_summary: str = Field(description="昨日行为总结")

# ==========================================
# 3. Settings loaders
# ==========================================

def load_settings_data(settings_dir_name: str = "settings") -> Dict[str, str]:
    """
    Load settings JSON and format into prompt variables.
    """
    settings_path = project_root / settings_dir_name

    print(f"[INFO] Loading settings from: {settings_path}")

    context_data = {
        "profile_demographics": "N/A",
        "profile_psychology": "N/A",
        "profile_routines_and_relations": "N/A",
        "house_layout_json": "N/A",
        "simulation_context": "N/A",
    }

    # Profile
    profile_path = settings_path / "profile.json"
    if profile_path.exists():
        try:
            with open(profile_path, "r", encoding="utf-8") as f:
                profile = json.load(f)

            name = profile.get("name", "未知")
            age = profile.get("age", "未知")
            gender = profile.get("gender", "未知")
            occupation = profile.get("occupation", "未知")

            context_data["profile_demographics"] = (
                f"- 姓名: {name}\n"
                f"- 年龄: {age}\n"
                f"- 性别: {gender}\n"
                f"- 职业: {occupation}\n"
            )

            personality = profile.get("personality", {})
            values = profile.get("values", {})
            preferences = profile.get("preferences", {})

            context_data["profile_psychology"] = (
                "【性格特征 (Personality)】\n"
                f"{json.dumps(personality, ensure_ascii=False, indent=2)}\n"
                "【核心价值观 (Values)】\n"
                f"{json.dumps(values, ensure_ascii=False, indent=2)}\n"
                "【兴趣与偏好 (Preferences)】\n"
                f"{json.dumps(preferences, ensure_ascii=False, indent=2)}\n"
            )

            routines = profile.get("routines", {})
            relationships = profile.get("relationships", [])

            context_data["profile_routines_and_relations"] = (
                "【详细作息配置 (Routines)】\n"
                f"{json.dumps(routines, ensure_ascii=False, indent=2)}\n"
                "【社交关系网 (Relationships)】\n"
                f"{json.dumps(relationships, ensure_ascii=False, indent=2)}\n"
            )

            print("[OK] Profile loaded successfully.")
        except Exception as exc:
            print(f"[ERROR] Error loading profile: {exc}")

    # House layout
    layout_path = settings_path / "house_layout.json"
    if layout_path.exists():
        try:
            with open(layout_path, "r", encoding="utf-8") as f:
                layout_data = json.load(f)
            context_data["house_layout_json"] = json.dumps(layout_data, ensure_ascii=False, indent=2)
            print("[OK] House layout loaded successfully.")
        except Exception as exc:
            print(f"[ERROR] Error loading layout: {exc}")
    else:
        print(f"[WARN] house_layout.json not found at {layout_path}")

    return context_data


def build_settings_data_from_cache(profile_dict: Dict, layout_dict: Dict) -> Dict[str, str]:
    """
    从已加载的 profile/layout 构建与 load_settings_data 相同结构的 context_data，
    供 14 天循环内复用，避免每日重复读盘。
    """
    context_data = {
        "profile_demographics": "N/A",
        "profile_psychology": "N/A",
        "profile_routines_and_relations": "N/A",
        "house_layout_json": "N/A",
        "simulation_context": "N/A",
    }
    if profile_dict:
        name = profile_dict.get("name", "未知")
        age = profile_dict.get("age", "未知")
        gender = profile_dict.get("gender", "未知")
        occupation = profile_dict.get("occupation", "未知")
        context_data["profile_demographics"] = (
            f"- 姓名: {name}\n"
            f"- 年龄: {age}\n"
            f"- 性别: {gender}\n"
            f"- 职业: {occupation}\n"
        )
        personality = profile_dict.get("personality", {})
        values = profile_dict.get("values", {})
        preferences = profile_dict.get("preferences", {})
        context_data["profile_psychology"] = (
            "【性格特征 (Personality)】\n"
            f"{json.dumps(personality, ensure_ascii=False, indent=2)}\n"
            "【核心价值观 (Values)】\n"
            f"{json.dumps(values, ensure_ascii=False, indent=2)}\n"
            "【兴趣与偏好 (Preferences)】\n"
            f"{json.dumps(preferences, ensure_ascii=False, indent=2)}\n"
        )
        routines = profile_dict.get("routines", {})
        relationships = profile_dict.get("relationships", [])
        context_data["profile_routines_and_relations"] = (
            "【详细作息配置 (Routines)】\n"
            f"{json.dumps(routines, ensure_ascii=False, indent=2)}\n"
            "【社交关系网 (Relationships)】\n"
            f"{json.dumps(relationships, ensure_ascii=False, indent=2)}\n"
        )
    if layout_dict:
        context_data["house_layout_json"] = json.dumps(layout_dict, ensure_ascii=False, indent=2)
    return context_data


def load_profile_json() -> str:
    profile_path = project_root / "settings" / "profile.json"
    if not profile_path.exists():
        return "{}"
    with open(profile_path, "r", encoding="utf-8") as f:
        return json.dumps(json.load(f), ensure_ascii=False, indent=2)

# ==========================================
# 4. Graph state
# ==========================================

class AgentState(TypedDict):
    inputs: Dict[str, str]
    current_plan: Optional[ActivityPlan]
    validation_result: Optional[ValidationResult]
    revision_count: int

# ==========================================
# 5. Nodes
# ==========================================

# 极速 LLM，use_responses_api=False 以兼容 with_structured_output
llm = create_fast_llm(
    model=DEFAULT_MODEL,
    temperature=PLANNING_TEMPERATURE,
    use_responses_api=PLANNING_USE_RESPONSES_API,
)

def _estimate_prompt_chars(template: str, variables: Dict[str, str]) -> int:
    total = len(template or "")
    for val in variables.values():
        total += len(str(val))
    return total

def generate_node(state: AgentState):
    print("\n[Step 1] Generating Initial Plan...")
    prompt = ChatPromptTemplate.from_template(PLANNING_PROMPT_TEMPLATE)
    structured_llm = llm.with_structured_output(ActivityPlan, method="json_schema", strict=True)
    chain = prompt | structured_llm

    result = chain.invoke({
        "activity_planning_requirements": ACTIVITY_PLANNING_REQUIREMENTS,
        "values_interpretation_guide": VALUES_INTERPRETATION_GUIDE,
        **state["inputs"],
    })
    try:
        vars_for_count = {"activity_planning_requirements": ACTIVITY_PLANNING_REQUIREMENTS, "values_interpretation_guide": VALUES_INTERPRETATION_GUIDE, **state["inputs"]}
        chars = _estimate_prompt_chars(PLANNING_PROMPT_TEMPLATE, vars_for_count)
        print(f"[INFO] LLM input size (planning generate): ~{chars} chars (~{chars//4} tokens)")
    except Exception:
        pass
    return {"current_plan": result, "revision_count": 0}


def validate_node(state: AgentState):
    if SKIP_PLANNING_VALIDATION:
        print("\n[FAST] Skipping planning validation (SKIP_PLANNING_VALIDATION=1).")
        return {"validation_result": ValidationResult(is_valid=True, correction_content=None)}
    print("\n[Step 2] Validating Plan...")
    prompt = ChatPromptTemplate.from_template(PLANNING_VALIDATION_PROMPT_TEMPLATE)
    structured_llm = llm.with_structured_output(ValidationResult, method="json_schema", strict=True)
    chain = prompt | structured_llm

    inputs = state["inputs"]
    plan_json = state["current_plan"].model_dump_json()

    result = chain.invoke({
        "activity_planning_requirements": ACTIVITY_PLANNING_REQUIREMENTS,
        "profile_psychology": inputs["profile_psychology"],
        "profile_routines_and_relations": inputs["profile_routines_and_relations"],
        "house_layout_json": inputs["house_layout_json"],
        "activity_plans_json": plan_json,
        "simulation_context": inputs.get("simulation_context", "N/A"),
    })
    try:
        vars_for_count = {
            "activity_planning_requirements": ACTIVITY_PLANNING_REQUIREMENTS,
            "profile_psychology": inputs["profile_psychology"],
            "profile_routines_and_relations": inputs["profile_routines_and_relations"],
            "house_layout_json": inputs["house_layout_json"],
            "activity_plans_json": plan_json,
            "simulation_context": inputs.get("simulation_context", "N/A"),
        }
        chars = _estimate_prompt_chars(PLANNING_VALIDATION_PROMPT_TEMPLATE, vars_for_count)
        print(f"[INFO] LLM input size (planning validate): ~{chars} chars (~{chars//4} tokens)")
    except Exception:
        pass

    if result.is_valid:
        print("[OK] Validation Passed!")
    else:
        print(f"[ERROR] Validation Failed. Reason: {result.correction_content[:150]}...")

    # Hard check: time continuity (highest priority).
    try:
        activities = state["current_plan"].activities if state.get("current_plan") else []
        sim_ctx = json.loads(inputs.get("simulation_context", "{}"))
        day_start_time = sim_ctx.get("day_start_time")
        day_end_time = sim_ctx.get("day_end_time")
        hard_errors = []
        if not activities:
            hard_errors.append("日程为空，无法覆盖时间窗口。")
        else:
            # ensure sorted by start_time
            def _parse_iso(ts: str):
                from datetime import datetime
                return datetime.fromisoformat(ts)
            ordered = sorted(activities, key=lambda a: a.start_time)
            if day_start_time and ordered[0].start_time > day_start_time:
                hard_errors.append("首个活动开始时间晚于 day_start_time。")
            for prev, cur in zip(ordered, ordered[1:]):
                if cur.start_time > prev.end_time:
                    hard_errors.append("存在时间空档。")
                if cur.start_time < prev.end_time:
                    hard_errors.append("存在时间重叠。")
            if day_end_time and ordered[-1].end_time < day_end_time:
                hard_errors.append("最后活动未覆盖到 day_end_time。")
        if hard_errors:
            result.is_valid = False
            hard_msg = "硬校验失败（时间连续性）： " + " ".join(hard_errors)
            if result.correction_content:
                result.correction_content = f"{hard_msg} {result.correction_content}"
            else:
                result.correction_content = hard_msg
    except Exception:
        pass

    # Hard check: main_rooms must exist in house_layout (no hallucinated rooms).
    try:
        layout = json.loads(inputs.get("house_layout_json", "{}"))
        valid_rooms = set(layout.keys())
        activities = state["current_plan"].activities if state.get("current_plan") else []
        bad_rooms = []
        for act in activities:
            for room in (act.main_rooms or []):
                if room not in valid_rooms:
                    bad_rooms.append(room)
        if bad_rooms:
            result.is_valid = False
            rooms = ", ".join(sorted(set(bad_rooms)))
            hard_msg = f"硬校验失败：出现不在 house_layout 中的房间：{rooms}。"
            if result.correction_content:
                result.correction_content = f"{hard_msg} {result.correction_content}"
            else:
                result.correction_content = hard_msg
    except Exception:
        pass

    return {"validation_result": result}


def correct_node(state: AgentState):
    print(f"\n[Step 3] Refining Plan (Attempt {state['revision_count'] + 1})...")
    prompt = ChatPromptTemplate.from_template(PLANNING_CORRECTION_PROMPT_TEMPLATE)
    structured_llm = llm.with_structured_output(ActivityPlan, method="json_schema", strict=True)
    chain = prompt | structured_llm

    inputs = state["inputs"]
    plan_json = state["current_plan"].model_dump_json()

    result = chain.invoke({
        "activity_planning_requirements": ACTIVITY_PLANNING_REQUIREMENTS,
        "profile_psychology": inputs["profile_psychology"],
        "profile_routines_and_relations": inputs["profile_routines_and_relations"],
        "house_layout_json": inputs["house_layout_json"],
        "simulation_context": inputs.get("simulation_context", "N/A"),
        "original_activity_plans_json": plan_json,
        "correction_content": state["validation_result"].correction_content,
    })
    try:
        vars_for_count = {
            "activity_planning_requirements": ACTIVITY_PLANNING_REQUIREMENTS,
            "profile_psychology": inputs["profile_psychology"],
            "profile_routines_and_relations": inputs["profile_routines_and_relations"],
            "house_layout_json": inputs["house_layout_json"],
            "simulation_context": inputs.get("simulation_context", "N/A"),
            "original_activity_plans_json": plan_json,
            "correction_content": state["validation_result"].correction_content,
        }
        chars = _estimate_prompt_chars(PLANNING_CORRECTION_PROMPT_TEMPLATE, vars_for_count)
        print(f"[INFO] LLM input size (planning correct): ~{chars} chars (~{chars//4} tokens)")
    except Exception:
        pass

    return {
        "current_plan": result,
        "revision_count": state["revision_count"] + 1,
    }

# ==========================================
# 6. Graph construction
# ==========================================

def router(state: AgentState):
    if state["validation_result"].is_valid:
        return "end"
    if state["revision_count"] >= MAX_PLANNING_REVISIONS:
        print("\n[WARN] Max planning revisions reached. Stopping.")
        return "end"
    return "correct"

workflow = StateGraph(AgentState)
workflow.add_node("generate", generate_node)
workflow.add_node("validate", validate_node)
workflow.add_node("correct", correct_node)

workflow.set_entry_point("generate")
workflow.add_edge("generate", "validate")
workflow.add_conditional_edges("validate", router, {"end": END, "correct": "correct"})
workflow.add_edge("correct", "validate")

app = workflow.compile()

# ==========================================
# 7. Helpers
# ==========================================

def _load_simulation_context_from_file() -> Optional[Dict[str, str]]:
    context_path = project_root / "data" / "simulation_context.json"
    if not context_path.exists():
        return None
    try:
        with open(context_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def run_planning(
    simulation_context: Optional[Dict[str, str]] = None,
    cached_settings_data: Optional[Dict[str, str]] = None,
) -> Optional[Dict]:
    if cached_settings_data is not None:
        settings_data = dict(cached_settings_data)
    else:
        settings_data = load_settings_data("settings")

    if settings_data.get("house_layout_json") == "N/A":
        print("[WARN] 房屋布局数据未加载，可能导致生成失败。请检查 settings/house_layout.json")

    # Preflight: show LLM endpoint and attempt a quick TCP connect if base_url is set
    use_custom_base = os.getenv("OPENAI_USE_BASE_URL", "").strip().lower() in {"1", "true", "yes", "y", "on"}
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE")
    model_name = os.getenv("OPENAI_MODEL") or "gpt-4o"
    if use_custom_base and base_url:
        parsed = urlparse(base_url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        print(f"[INFO] LLM base_url: {base_url} | model: {model_name}")
        if host:
            try:
                with socket.create_connection((host, port), timeout=2):
                    print(f"[OK] LLM endpoint reachable: {host}:{port}")
            except Exception as exc:
                print(f"[ERROR] LLM endpoint unreachable: {host}:{port} ({exc})")
    else:
        print(f"[INFO] LLM using default OpenAI endpoint. model: {model_name}")

    if simulation_context is None:
        simulation_context = _load_simulation_context_from_file()

    if simulation_context:
        settings_data["simulation_context"] = json.dumps(simulation_context, ensure_ascii=False, indent=2)

    if SKIP_PLANNING_VALIDATION:
        print("[FAST] Planning: 1 LLM call (generate only), no validate/correct.\n")
    initial_state = {
        "inputs": settings_data,
        "current_plan": None,
        "validation_result": None,
        "revision_count": 0,
    }

    try:
        final_state = app.invoke(initial_state)

        if final_state.get("current_plan"):
            data_dict = final_state["current_plan"].model_dump()
            final_json = json.dumps(data_dict, indent=2, ensure_ascii=False)

            print("\n\n[RESULT] Final Activity Plan Generated:")
            print(final_json)

            output_file = project_root / "data" / "activity.json"
            output_file.parent.mkdir(parents=True, exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(final_json)
            print(f"[OK] Result saved to {output_file}")
            return data_dict
    except Exception as exc:
        print(f"\n[ERROR] Execution Error: {exc}")
        import traceback
        traceback.print_exc()
    return None


def generate_previous_day_summary(profile_json: str, activity_logs: List[Dict], execution_log: str = "") -> str:
    prompt = ChatPromptTemplate.from_template(SUMMARIZATION_PROMPT_TEMPLATE)
    structured_llm = llm.with_structured_output(PreviousDaySummary, method="json_schema", strict=True)
    chain = prompt | structured_llm

    activity_payload = {
        "activity_logs": activity_logs,
        "actual_execution_records": execution_log,
    }

    result = chain.invoke({
        "profile_json": profile_json,
        "activity_logs_json": json.dumps(activity_payload, ensure_ascii=False, indent=2),
        "values_interpretation_guide": VALUES_INTERPRETATION_GUIDE,
    })
    try:
        vars_for_count = {
            "profile_json": profile_json,
            "activity_logs_json": json.dumps(activity_payload, ensure_ascii=False, indent=2),
            "values_interpretation_guide": VALUES_INTERPRETATION_GUIDE,
        }
        chars = _estimate_prompt_chars(SUMMARIZATION_PROMPT_TEMPLATE, vars_for_count)
        print(f"[INFO] LLM input size (summary): ~{chars} chars (~{chars//4} tokens)")
    except Exception:
        pass
    return result.previous_day_summary


if __name__ == "__main__":
    run_planning()
