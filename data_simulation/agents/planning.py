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

from settings.llm_utils import create_chat_llm

# ==========================================
# 1. Prompt constants
# ==========================================

ACTIVITY_PLANNING_REQUIREMENTS = """
## 活动规划核心要求
请生成一个详细的、符合居民特征的一天（24小时）活动规划。

### 1. 仿真逻辑与状态机 (核心)
你需要首先读取【仿真上下文】中的 `simulation_state` 和 `current_date`，并按照以下逻辑执行：

* **日期类型判断 (Workday vs Weekend)**:
    * 检查 `day_of_week`。如果是工作日，严格执行 Profile 中的工作日作息；如果是周末，切换至休闲/晚起模式。
    * **周期性检查**: 检查 Profile 中是否有特定日期的活动（如“每周三健身”、“每周五聚餐”），必须将这些固定事项排入日程。

* **记忆机制 (Context Memory)**:
    * 读取 `previous_day_summary`。如果前一天有“熬夜”、“醉酒”或“高强度运动”，请在今天的 `start_time`（起床时间）或活动强度上体现**滞后效应**（如：推迟起床30分钟，或减少今日运动量）。

* **状态机响应 (State Machine)**:
    * **正常态 (Normal)**: 遵循 80% 的基准线，无意外发生，严格按计划行事。
    * **扰动态 (Perturbed)**: 上下文中会指定一个 `random_event`（如：朋友临时拜访、轻微感冒、必须在家加班）。你必须将此事件自然地插入日程中，并展示它如何**挤占**了原本的活动时间（如：因为加班取消了晚上的阅读）。
    * **危机态 (Crisis)**: 上下文中会指定一个 `emergency_event`（如：跌倒、突发疾病、设备故障）。
        * 该事件必须在日程中发生。
        * 事件发生后，后续活动应中断或转变为“应对危机”（如：等待救援、联系家人、停止移动）。

### 2. 数据驱动的行为推导
* **生理节律**：严格遵守 `sleep_schedule` 和 `meal_habits`，除非受【记忆机制】或【扰动/危机】影响。
* **性格表现**：
    * 高开放性 -> 即使在扰动日，也会尝试用新颖方式解决问题。
    * 高尽责性 -> 即使生病（扰动态），也可能会尝试完成最低限度的工作。
* **环境交互**：所有活动必须绑定 `furniture` 或 `devices`。
    * *危机态特别说明*：如果发生“跌倒”，请注明跌倒发生的具体位置（Room/Furniture）。

### 3. 外出活动规范（闭环原则）
* 外出活动（工作、购物、运动）必须是 `Home -> Activity -> Home` 的闭环。
* 外出期间 `main_rooms` 为空列表 `[]`。
* **注意**: 如果状态为“居家办公（扰动态）”，则原本的外出工作应改为在“书房/客厅”使用“电脑”进行。

### 4. 格式与完整性
* **时间连续性**：24小时无缝衔接 (00:00 - 23:59)。
* **输出内容**：必须包含 Activity ID, Name, Start/End Time, Description, Main Rooms。

## 输出数据格式要求 (JSON List)
每个活动对象包含：
- `activity_id`: 唯一标识 (e.g., "act_001")
- `activity_name`: 活动简述 (e.g., "居家办公", "突发跌倒")
- `start_time`: ISO格式 (e.g., "2025-01-01T09:00:00")
- `end_time`: ISO格式 (e.g., "2025-01-01T12:00:00")
- `description`: **详细描述**。必须包含：
    1.  动作细节与性格体现。
    2.  使用的家具/设备。
    3.  **状态体现**：如果是扰动或危机，明确描述异常情况（如“感到头晕，倒在了沙发旁”）。
    4.  如果是社交，提到互动对象。
- `main_rooms`: 涉及的房间ID列表。
"""

PLANNING_PROMPT_TEMPLATE = """
你是一个基于大模型的高保真人类行为模拟器。请根据以下多维度的居民档案和物理环境，通过逻辑推演，规划出这位居民一天（24小时）的活动流。

{activity_planning_requirements}

## 状态机事件要求（高优先级）
当 `simulation_state` 为 **Perturbed** 或 **Crisis** 且 `random_event` / `emergency_event` 为空时，你必须**自行生成**一个合理事件，并满足：
1. **必须**明确标注事件（使用“事件：<内容>”格式）。
2. 该事件必须对当天日程产生实际影响（例如取消/推迟/缩短某活动）。
3. 如果是 Crisis，后续活动应转为应对处理（就医、维修、联系家人等）。

## 输入数据

### 1. 居民档案 (Profile)
**基础信息 (Layer 1):**
{profile_demographics}

**性格与价值观 (Layer 2):**
*请重点参考 Big Five 分数和 Values 偏好来决定活动的具体内容和风格。*
{profile_psychology}

**生活习惯与社交 (Layer 3 & Relations):**
*请严格遵守以下作息时间表和社交关系网。*
{profile_routines_and_relations}

### 2. 物理环境 (Environment)
**房屋布局与物品清单**
*请注意检查每个房间内的 `furniture` 和 `devices` 列表，确保活动有物可依。*
{house_layout_json}

### 3. 仿真上下文 (Simulation Context)
{simulation_context}
"""

PLANNING_VALIDATION_PROMPT_TEMPLATE = """
请作为“行为逻辑审核员”，审核以下AI生成的居民活动规划。
你的任务是确保规划不仅在时间上连续，而且在**性格逻辑**和**物理环境**上是真实的。

{activity_planning_requirements}

## 待审核数据
**居民性格与习惯**
{profile_psychology}
{profile_routines_and_relations}

**房屋物品清单:**
{house_layout_json}

**当前活动规划:**
{activity_plans_json}

## 验证维度
1. **时间连续性 (强校验)**: 不允许时间重叠或空档，必须覆盖 24 小时。
2. **作息/餐点 (强校验)**: 睡眠/三餐时间必须贴合 Profile（允许轻微偏差，但需说明原因）。
3. **固定事项 (强校验)**: Profile 中明确的固定安排必须出现（如周会/固定运动）。
4. **环境交互合理性 (强校验)**: main_rooms 必须来自房屋布局，且活动描述能对应家具/设备。
5. **扰动/危机体现 (强校验)**：
   - 若 `simulation_state` 为 Perturbed/Crisis，必须在当日活动描述中体现“异常事件的发生与影响”。
   - 表达需自然，但必须能明确看出“事件导致日程变化”（如取消/推迟/缩短/改地点/应对处理）。
6. **性格逻辑性**: 活动是否违背 Big Five 性格与价值观。

## 返回结果
- 如通过: is_valid = true, correction_content 为空。
- 如不通过: is_valid = false，并在 correction_content 中详细说明“必须修正”的冲突点（含异常事件是否体现）。
"""

SUMMARIZATION_PROMPT_TEMPLATE = """
你是一个基于大模型的高保真人类行为模拟器。请根据以下【居民档案】和【昨日的活动流】数据，生成一份简明扼要、重点突出的“昨日行为总结 (Previous Day Summary)”。

## 总结核心要求

1.  **睡眠质量与时间**：对比居民档案中的 `sleep_schedule`，评估睡眠是否充足（时长是否低于基线 6.5 小时），以及入睡/起床时间是否异常。
    * **关键词**：如果异常，使用“**熬夜**”、“**睡眠不足**”、“**晚起**”等关键词。
2.  **身心状态与生理异常**：
    * 关注是否有高强度体力消耗或超出日常的活动，可能导致今日疲劳。
    * 关注是否有情绪/社交压力或异常情况，可能影响今日心情与精力。
    * 如有非常规身心负担，请用自然语言描述其影响。
3.  **突发事件**：如果昨日日程中包含“扰动态”或“危机态”事件，必须明确指出该事件及其对居民造成的**即时影响**（例如：跌倒导致行动不便、设备故障导致工作中断）。
5.  **跨日影响提示**：如果昨日存在扰动/危机或睡眠异常，请明确写出对今日的可能影响（如晚起、降低强度、增加休息等）。
4.  **数据格式**：输出必须是一个简洁的、单句或两句的文本描述。

## 输入数据

### 1. 居民档案 (Profile)
// 包含居民的 Big Five 性格、健康意识、工作日作息时间等。
{...}

### 2. 昨日的活动流 (Activity Logs)
// 这是昨日（已发生的）详细活动列表。
{
  "activity_logs": [
    // 假设这是你昨天生成的日程，用于今日总结
    {
      "activity_id": "act_016",
      "activity_name": "睡眠",
      "start_time": "2025-01-01T23:15:00",  // 晚于平时 23:30
      "end_time": "2025-01-02T07:30:00",    // 晚于平时 07:00
      "description": "进入睡眠周期，但因前夜加班，推迟入睡并晚起30分钟，睡眠不足。",
      "main_rooms": ["Bedroom"]
    },
    {
      "activity_id": "act_009",
      "activity_name": "户外跑步 (高强度)",
      "start_time": "2025-01-01T19:00:00",
      "end_time": "2025-01-01T20:30:00",
      "description": "进行了一次高强度的户外跑步，身体感到明显的肌肉酸痛。",
      "main_rooms": []
    },
    {
      "activity_id": "act_012",
      "activity_name": "突发跌倒",
      "start_time": "2025-01-01T10:15:00",
      "end_time": "2025-01-01T10:25:00",
      "description": "在厨房意外滑倒，膝盖轻微擦伤。",
      "main_rooms": ["Kitchen"]
    }
    // ... 其他活动
  ]
}

## 输出格式要求

**输出必须仅包含一个 JSON 字段：**

```json
{
  "previous_day_summary": "文本描述"
}
"""

PLANNING_CORRECTION_PROMPT_TEMPLATE = """
你是一个专业的生活规划师。上一轮生成的规划未能通过逻辑验证。
请根据验证反馈，重新生成修正后的活动规划。

{activity_planning_requirements}

## 参考数据
**居民性格与习惯**
{profile_psychology}
{profile_routines_and_relations}

**房屋物品清单:**
{house_layout_json}

## 原始规划与问题
**原始活动规划:**
{original_activity_plans_json}

**验证未通过原因 (必读):**
{correction_content}

## 修正指令
1. 优先解决反馈中指出的逻辑冲突。
2. **时间修正 (强制)**：补齐空档并消除重叠，保证 24 小时完整覆盖。
3. **作息/固定事项 (强制)**：严格贴合 Profile 的作息与固定安排；若有偏差必须在描述中解释原因。
4. **房间合法性 (强制)**：main_rooms 必须来自 house_layout；外出活动 main_rooms 为空。
5. **物品可用性 (强制)**：活动描述需明确使用该房间内的家具/设备。
6. **扰动/危机体现 (强制)**：当 `simulation_state` 为 Perturbed/Crisis 时，必须在当天活动描述中自然体现异常事件及其对日程的影响。
"""

SUMMARIZATION_PROMPT_TEMPLATE = """
你是一个基于大模型的高保真人类行为模拟器。请根据以下【居民档案】和【昨日的活动流】数据，生成一份简明扼要、重点突出的“昨日行为总结 (Previous Day Summary)”。

## 总结核心要求

1.  **睡眠质量与时间**：对比居民档案中的 `sleep_schedule`，评估睡眠是否充足（时长是否低于基线 6.5 小时），以及入睡/起床时间是否异常。
    * **关键词**：如果异常，使用“**熬夜**”、“**睡眠不足**”、“**晚起**”等关键词。
2.  **身心状态与生理异常**：
    * 检查是否有**高强度运动**（如：马拉松、力量训练），这可能导致今日疲劳。
    * 检查是否有**社交/情绪异常**（如：朋友聚餐到深夜、争吵、孤独、压力），这可能影响今日的心情和精力。
    * 检查是否有**饮酒**或**非日常药物**的使用。
3.  **突发事件**：如果昨日日程中包含“扰动态”或“危机态”事件，必须明确指出该事件及其对居民造成的**即时影响**（例如：跌倒导致行动不便、设备故障导致工作中断）。
4.  **数据格式**：输出必须是一个简洁的、单句或两句的文本描述。

## 输入数据

### 1. 居民档案 (Profile)
{profile_json}

### 2. 昨日的活动流 (Activity Logs)
{activity_logs_json}

## 输出格式要求
输出必须仅包含一个 JSON 字段:
{{"previous_day_summary": "文本描述"}}
"""

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

llm = create_chat_llm(model="gpt-4o", temperature=0.7)


def generate_node(state: AgentState):
    print("\n[Step 1] Generating Initial Plan...")
    prompt = ChatPromptTemplate.from_template(PLANNING_PROMPT_TEMPLATE)
    structured_llm = llm.with_structured_output(ActivityPlan)
    chain = prompt | structured_llm

    result = chain.invoke({
        "activity_planning_requirements": ACTIVITY_PLANNING_REQUIREMENTS,
        **state["inputs"],
    })
    return {"current_plan": result, "revision_count": 0}


def validate_node(state: AgentState):
    print("\n[Step 2] Validating Plan...")
    prompt = ChatPromptTemplate.from_template(PLANNING_VALIDATION_PROMPT_TEMPLATE)
    structured_llm = llm.with_structured_output(ValidationResult)
    chain = prompt | structured_llm

    inputs = state["inputs"]
    plan_json = state["current_plan"].model_dump_json()

    result = chain.invoke({
        "activity_planning_requirements": ACTIVITY_PLANNING_REQUIREMENTS,
        "profile_psychology": inputs["profile_psychology"],
        "profile_routines_and_relations": inputs["profile_routines_and_relations"],
        "house_layout_json": inputs["house_layout_json"],
        "activity_plans_json": plan_json,
    })

    if result.is_valid:
        print("[OK] Validation Passed!")
    else:
        print(f"[ERROR] Validation Failed. Reason: {result.correction_content[:150]}...")

    return {"validation_result": result}


def correct_node(state: AgentState):
    print(f"\n[Step 3] Refining Plan (Attempt {state['revision_count'] + 1})...")
    prompt = ChatPromptTemplate.from_template(PLANNING_CORRECTION_PROMPT_TEMPLATE)
    structured_llm = llm.with_structured_output(ActivityPlan)
    chain = prompt | structured_llm

    inputs = state["inputs"]
    plan_json = state["current_plan"].model_dump_json()

    result = chain.invoke({
        "activity_planning_requirements": ACTIVITY_PLANNING_REQUIREMENTS,
        "profile_psychology": inputs["profile_psychology"],
        "profile_routines_and_relations": inputs["profile_routines_and_relations"],
        "house_layout_json": inputs["house_layout_json"],
        "original_activity_plans_json": plan_json,
        "correction_content": state["validation_result"].correction_content,
    })

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
    if state["revision_count"] >= 3:
        print("\n[WARN] Max revisions reached. Stopping.")
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


def run_planning(simulation_context: Optional[Dict[str, str]] = None) -> Optional[Dict]:
    settings_data = load_settings_data("settings")

    if settings_data["house_layout_json"] == "N/A":
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


def generate_previous_day_summary(profile_json: str, activity_logs: List[Dict]) -> str:
    prompt = ChatPromptTemplate.from_template(SUMMARIZATION_PROMPT_TEMPLATE)
    structured_llm = llm.with_structured_output(PreviousDaySummary)
    chain = prompt | structured_llm

    activity_payload = {"activity_logs": activity_logs}

    result = chain.invoke({
        "profile_json": profile_json,
        "activity_logs_json": json.dumps(activity_payload, ensure_ascii=False, indent=2),
    })
    return result.previous_day_summary


if __name__ == "__main__":
    run_planning()
