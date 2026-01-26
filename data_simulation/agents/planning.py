import os
import json
from pathlib import Path
from typing import List, Optional, Dict
from typing_extensions import TypedDict

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END
from dotenv import load_dotenv

load_dotenv()
current_dir = Path(__file__).resolve().parent
dotenv_path = current_dir.parent / '.env'
load_dotenv(dotenv_path=dotenv_path)
# ==========================================
# 1. 提示词常量定义
# ==========================================

ACTIVITY_PLANNING_REQUIREMENTS = """
## 活动规划核心要求
请生成一个详细的、符合居民特征的一天（24小时）活动规划。

### 1. 数据驱动的行为逻辑
你需要严格基于提供的【居民档案】的三个层级进行推导：
* **生理节律（Layer 3 Routines）**：严格遵守 `sleep_schedule`（入睡/起床时间）和 `meal_habits`（用餐时间段）。
* **性格表现（Layer 2 Personality）**：
    * 根据 `big_five`（大五人格）调整活动内容（例如：高开放性->安排阅读/创作；高外向性->安排外出/通话；高尽责性->工作/家务一丝不苟）。
    * 根据 `values`（价值观）调整活动重心（例如：高健康意识->必须安排 `exercise` 中定义的运动）。
* **社交互动（Relationships）**：如果安排社交活动，请优先从 `relationships` 列表中选取对象，并符合设定的亲密度和联系频率。

### 2. 环境与物品交互
活动必须与【房屋布局】中的具体设施相匹配：
* **物品依赖**：除了“睡觉”、“发呆”等，大多数活动应隐含对 `furniture`（家具）或 `devices`（设备）的使用。
* **可行性检查**：不要安排房屋内不存在的设备进行的活动（例如：如果客厅没有游戏机，就不能安排“在客厅打主机游戏”）。

### 3. 外出活动规范（闭环原则）
* **重要**：外出活动（工作、购物、运动、社交）必须是一个**独立且完整**的闭环，不要拆分。
* 格式：从家中出发 → 具体活动内容（含地点/对象） → 返回家中。
* 涉及房间：外出活动的 `main_rooms` 应为空列表 `[]`。

### 4. 格式与完整性
* **时间连续性**：24小时无缝衔接，上一活动 `end_time` 必须等于下一活动 `start_time`。
* **覆盖面**：包含睡眠、生理卫生、饮食、工作/学习、家务、娱乐、运动、社交。
"""

PLANNING_PROMPT_TEMPLATE = """
你是一个基于大模型的高保真人类行为模拟器。请根据以下多维度的居民档案和物理环境，通过逻辑推演，规划出这位居民一天（24小时）的活动流。

{activity_planning_requirements}

## 输入数据

### 1. 居民档案 (Profile)
**基础信息 (Layer 1):**
{profile_demographics}

**性格与价值观 (Layer 2):**
*请重点参考 Big Five 分数和 Values 偏好来决定活动的具体内容和风格。*
{profile_psychology}

**生活习惯与社交 (Layer 3 & Relations):**
*请严格遵守以下作息时刻表和社交关系网。*
{profile_routines_and_relations}

### 2. 物理环境 (Environment)
**房屋布局与物品清单:**
*请注意检查每个房间内的 `furniture` 和 `devices` 列表，确保活动有物可依。*
{house_layout_json}
"""

PLANNING_VALIDATION_PROMPT_TEMPLATE = """
请作为“行为逻辑审核员”，审核以下AI生成的居民活动规划。
你的任务是确保规划不仅在时间上连续，而且在**性格逻辑**和**物理环境**上是真实的。

{activity_planning_requirements}

## 待审核数据
**居民性格与习惯:**
{profile_psychology}
{profile_routines_and_relations}

**房屋物品清单:**
{house_layout_json}

**当前活动规划:**
{activity_plans_json}

## 验证维度
请严格从以下四个维度进行检查：
1. **时间连续性**: 检查是否存在时间重叠或空隙。
2. **生理节律一致性**: 睡眠、用餐、运动是否符合 Profile 设定。
3. **环境交互合理性**: 房间是否有对应的 furniture/devices。
4. **性格逻辑性**: 活动是否违背 Big Five 性格。

## 返回结果
- 如果验证通过，设置 `is_valid` 为 `true`，`correction_content` 为空。
- 如果验证不通过，设置 `is_valid` 为 `false`，并在 `correction_content` 中**引用具体的Profile字段或Layout物品**，详细说明冲突点。
"""

PLANNING_CORRECTION_PROMPT_TEMPLATE = """
你是一个专业的生活规划师。上一次生成的规划未能通过逻辑验证。
请根据验证反馈，重新生成修正后的活动规划。

{activity_planning_requirements}

## 参考数据
**居民性格与习惯:**
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
1. **针对性修复**：优先解决“验证未通过原因”中指出的所有逻辑冲突。
2. **保持完整性**：确保依然覆盖24小时。
3. **深度描述**：明确体现使用的家具/设备名称。
"""

# ==========================================
# 2. 数据结构定义 (Pydantic Models)
# ==========================================

class ActivityItem(BaseModel):
    activity_id: str = Field(description="唯一标识, e.g., 'act_001'")
    activity_name: str = Field(description="活动简述")
    start_time: str = Field(description="ISO格式时间")
    end_time: str = Field(description="ISO格式时间")
    description: str = Field(description="详细描述，包含动作、家具设备交互、社交对象等")
    main_rooms: List[str] = Field(description="涉及的房间ID列表")

class ActivityPlan(BaseModel):
    """生成的完整活动列表"""
    activities: List[ActivityItem]

class ValidationResult(BaseModel):
    """验证结果"""
    is_valid: bool = Field(description="规划是否完全通过验证")
    correction_content: Optional[str] = Field(description="如果不通过，详细的修改建议；如果通过，留空")

# ==========================================
# 3. 上下文加载工具
# ==========================================

def load_settings_data(settings_dir_name: str = "settings") -> Dict[str, str]:
    """
    从上一级目录读取 JSON 并格式化为 Prompt 变量
    """
    current_dir = Path(__file__).resolve().parent
    settings_path = current_dir.parent / settings_dir_name
    
    print(f"📂 Loading settings from: {settings_path}")

    context_data = {
        "profile_demographics": "N/A",
        "profile_psychology": "N/A",
        "profile_routines_and_relations": "N/A",
        "house_layout_json": "N/A"
    }

    # --- 1. 读取 Profile (profile.json) ---
    profile_path = settings_path / "profile.json"
    if profile_path.exists():
        try:
            with open(profile_path, 'r', encoding='utf-8') as f:
                profile = json.load(f)
                
                # Layer 1
                name = profile.get("name", "未知")
                age = profile.get("age", "未知")
                gender = profile.get("gender", "未知")
                occupation = profile.get("occupation", "未知")
                
                context_data["profile_demographics"] = f"""
- 姓名: {name}
- 年龄: {age}
- 性别: {gender}
- 职业: {occupation}
"""
                # Layer 2
                personality = profile.get("personality", {})
                values = profile.get("values", {})
                preferences = profile.get("preferences", {})
                
                context_data["profile_psychology"] = f"""
【性格特征 (Personality)】
{json.dumps(personality, ensure_ascii=False, indent=2)}
【核心价值观 (Values)】
{json.dumps(values, ensure_ascii=False, indent=2)}
【兴趣与偏好 (Preferences)】
{json.dumps(preferences, ensure_ascii=False, indent=2)}
"""
                # Layer 3
                routines = profile.get("routines", {})
                relationships = profile.get("relationships", [])
                
                context_data["profile_routines_and_relations"] = f"""
【详细作息配置 (Routines)】
{json.dumps(routines, ensure_ascii=False, indent=2)}
【社交关系网 (Relationships)】
{json.dumps(relationships, ensure_ascii=False, indent=2)}
"""
                print("✅ Profile loaded successfully.")
        except Exception as e:
            print(f"❌ Error loading profile: {e}")

    # --- 2. 读取 Layout (house_layout.json) ---
    layout_path = settings_path / "house_layout.json" 
    if layout_path.exists():
        try:
            with open(layout_path, 'r', encoding='utf-8') as f:
                layout_data = json.load(f)
                context_data["house_layout_json"] = json.dumps(layout_data, ensure_ascii=False, indent=2)
                print("✅ House layout loaded successfully.")
        except Exception as e:
            print(f"❌ Error loading layout: {e}")
    else:
        print(f"⚠️ Warning: house_layout.json not found at {layout_path}")

    return context_data

# ==========================================
# 4. 定义 Graph 状态 (State)
# ==========================================

class AgentState(TypedDict):
    inputs: Dict[str, str]
    current_plan: Optional[ActivityPlan]
    validation_result: Optional[ValidationResult]
    revision_count: int

# ==========================================
# 5. 定义节点逻辑 (Nodes)
# ==========================================

# 初始化 LLM
llm = ChatOpenAI(model="gpt-4o", temperature=0.7)

def generate_node(state: AgentState):
    print("\n🚀 [Step 1] Generating Initial Plan...")
    prompt = ChatPromptTemplate.from_template(PLANNING_PROMPT_TEMPLATE)
    structured_llm = llm.with_structured_output(ActivityPlan)
    chain = prompt | structured_llm
    
    result = chain.invoke({
        "activity_planning_requirements": ACTIVITY_PLANNING_REQUIREMENTS,
        **state["inputs"]
    })
    return {"current_plan": result, "revision_count": 0}

def validate_node(state: AgentState):
    print("\n🔍 [Step 2] Validating Plan...")
    prompt = ChatPromptTemplate.from_template(PLANNING_VALIDATION_PROMPT_TEMPLATE)
    structured_llm = llm.with_structured_output(ValidationResult)
    chain = prompt | structured_llm
    
    inputs = state["inputs"]
    # 【修改点】: 使用 model_dump_json() 替代 json()
    plan_json = state["current_plan"].model_dump_json() 
    
    result = chain.invoke({
        "activity_planning_requirements": ACTIVITY_PLANNING_REQUIREMENTS,
        "profile_psychology": inputs["profile_psychology"],
        "profile_routines_and_relations": inputs["profile_routines_and_relations"],
        "house_layout_json": inputs["house_layout_json"],
        "activity_plans_json": plan_json
    })
    
    if result.is_valid:
        print("✅ Validation Passed!")
    else:
        # 只打印前100个字符避免刷屏
        print(f"❌ Validation Failed. Reason: {result.correction_content[:150]}...")
        
    return {"validation_result": result}

def correct_node(state: AgentState):
    print(f"\n🛠️ [Step 3] Refining Plan (Attempt {state['revision_count'] + 1})...")
    prompt = ChatPromptTemplate.from_template(PLANNING_CORRECTION_PROMPT_TEMPLATE)
    structured_llm = llm.with_structured_output(ActivityPlan)
    chain = prompt | structured_llm
    
    inputs = state["inputs"]
    # 【修改点】: 使用 model_dump_json() 替代 json()
    plan_json = state["current_plan"].model_dump_json()
    
    result = chain.invoke({
        "activity_planning_requirements": ACTIVITY_PLANNING_REQUIREMENTS,
        "profile_psychology": inputs["profile_psychology"],
        "profile_routines_and_relations": inputs["profile_routines_and_relations"],
        "house_layout_json": inputs["house_layout_json"],
        "original_activity_plans_json": plan_json,
        "correction_content": state["validation_result"].correction_content
    })
    
    return {
        "current_plan": result, 
        "revision_count": state["revision_count"] + 1
    }

# ==========================================
# 6. 构建图 (Graph Construction)
# ==========================================

def router(state: AgentState):
    if state["validation_result"].is_valid:
        return "end"
    if state["revision_count"] >= 3:
        print("\n⚠️ Max revisions reached. Stopping.")
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
# 7. 运行脚本
# ==========================================

if __name__ == "__main__":
    settings_data = load_settings_data("settings")
    
    if settings_data["house_layout_json"] == "N/A":
        print("⚠️ 警告: 房屋布局数据未加载，可能导致生成失败。请检查 settings/house_layout.json")

    initial_state = {
        "inputs": settings_data,
        "current_plan": None,
        "validation_result": None,
        "revision_count": 0
    }
    
    try:
        final_state = app.invoke(initial_state)
        
        if final_state["current_plan"]:
            # 【核心修改点】: Pydantic V2 正确的 JSON 序列化方式
            # 1. 先转成 Python 字典 (model_dump)
            # 2. 再用 json.dumps 处理中文 (ensure_ascii=False)
            data_dict = final_state["current_plan"].model_dump()
            final_json = json.dumps(data_dict, indent=2, ensure_ascii=False)
            
            print("\n\n🎉 Final Activity Plan Generated:")
            print(final_json)
            
            output_file = "data/plan.json"
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(final_json)
            print(f"✅ Result saved to {output_file}")
    except Exception as e:
        print(f"\n❌ Execution Error: {e}")
        import traceback
        traceback.print_exc()