import sys
from pathlib import Path

# 先加入 data_simulation 到 path，否则下面 import llm_utils / agent_config 会报错
_current_dir = Path(__file__).resolve().parent
_project_root = _current_dir.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import json
import os
import random
from dotenv import load_dotenv
from typing import List, Dict, Optional

from llm_utils import create_fast_llm
from agent_config import DEFAULT_MODEL, SETTINGS_DEFAULT_TEMPERATURE
from prompt import PROFILE_GENERATOR_PROMPT_TEMPLATE, ROLE_DICE_BRAINSTORM_PROMPT
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

# --- 环境配置 ---
load_dotenv()
dotenv_path = _project_root / ".env"
load_dotenv(dotenv_path=dotenv_path)

# ==========================================
# 1. 定义数据结构 (严格对齐你的JSON)
# ==========================================

class BigFive(BaseModel):
    openness: float = Field(description="开放性 (0.0-1.0)")
    conscientiousness: float = Field(description="尽责性 (0.0-1.0)")
    extraversion: float = Field(description="外向性 (0.0-1.0)")
    agreeableness: float = Field(description="宜人性 (0.0-1.0)")
    neuroticism: float = Field(description="神经质/情绪稳定性 (0.0-1.0)")

class Personality(BaseModel):
    big_five: BigFive
    traits: List[str] = Field(description="基于五大性格特质的具体性格标签列表，如['理性', '内向']")

class Values(BaseModel):
    work_life_balance: float = Field(description="工作生活平衡重视度 (0.0-1.0)")
    health_consciousness: float = Field(description="健康意识 (0.0-1.0)")
    social_interaction: float = Field(description="社交需求 (0.0-1.0)")
    personal_growth: float = Field(description="个人成长重视度 (0.0-1.0)")
    financial_security: float = Field(description="财务安全重视度 (0.0-1.0)")

class SleepSchedule(BaseModel):
    weekday_bedtime: str = Field(description="工作日入睡时间, e.g. '23:30'")
    weekday_wakeup: str = Field(description="工作日起床时间")
    weekend_bedtime: str = Field(description="周末入睡时间")
    weekend_wakeup: str = Field(description="周末起床时间")

class MealHabits(BaseModel):
    breakfast_time: str
    lunch_time: str
    dinner_time: str
    cooking_frequency: float = Field(description="烹饪频率 (0.0-1.0), 1.0为每天做饭")
    diet_preference: List[str] = Field(description="饮食偏好，如['低糖', '素食']")

class Exercise(BaseModel):
    frequency: str = Field(description="频率描述，如 '3次/周'")
    preferred_time: str = Field(description="偏好时间段")
    type: List[str] = Field(description="运动类型列表")

class EventCountConfig(BaseModel):
    mean: float = Field(description="平均事件数量 (建议 0.5-2.0)")
    std: float = Field(description="事件数量标准差 (建议 0.1-1.0)")
    max: int = Field(description="每日事件数量上限 (建议 1-5)")

class RandomEventConfig(BaseModel):
    perturbed: EventCountConfig = Field(description="扰动态事件数量分布参数")
    crisis: EventCountConfig = Field(description="危机态事件数量分布参数")

class WeeklyActivity(BaseModel):
    activity: str = Field(description="活动名称")
    time: str = Field(description="时间段")
    location: str = Field(description="地点")
    frequency: str = Field(description="频率描述")


class WeeklyScheduleEntry(BaseModel):
    """单日条目，用于满足 OpenAI strict schema（不接受 Dict 动态 key）"""
    day: str = Field(description="星期几，英文小写，如 monday, tuesday")
    activity: WeeklyActivity = Field(description="该日的典型活动")


class Routines(BaseModel):
    sleep_schedule: SleepSchedule
    meal_habits: MealHabits
    exercise: Exercise
    weekly_schedule: List[WeeklyScheduleEntry] = Field(
        default_factory=list,
        description="典型的一周日程安排，3-5个典型活动即可，每项含 day 与 activity",
    )

class UserPreferences(BaseModel):
    entertainment: List[str] = Field(description="娱乐偏好列表")
    music_genre: List[str] = Field(description="音乐风格列表")
    home_temperature: int = Field(description="舒适室温(整数)")

class UserProfile(BaseModel):
    user_id: str = Field(description="用户ID, 格式: user_XXX")
    name: str = Field(description="用户姓名(中文)")
    age: int = Field(description="年龄")
    gender: str = Field(description="性别 (male/female)")
    occupation: str = Field(description="职业")
    personality: Personality
    values: Values
    routines: Routines
    preferences: UserPreferences
    random_event_config: RandomEventConfig = Field(description="随机事件数量分布参数配置")

# ==========================================
# 2. 角色骰子 (Role Dice)：头脑风暴 5 个反差角色，固定取第 4 个
# ==========================================

class RoleDiceOutput(BaseModel):
    """角色骰子输出：5 个候选职业/身份，调用方取第 4 个 (role_4)。"""
    role_1: str = Field(description="第1个候选角色，一句简短中文描述")
    role_2: str = Field(description="第2个候选角色，一句简短中文描述")
    role_3: str = Field(description="第3个候选角色，一句简短中文描述")
    role_4: str = Field(description="第4个候选角色，一句简短中文描述")
    role_5: str = Field(description="第5个候选角色，一句简短中文描述")


def roll_role_dice(llm) -> str:
    """
    后台头脑风暴 5 个反差大的职业/身份，只返回第 4 个，用于增加随机性。
    """
    chain = llm.with_structured_output(RoleDiceOutput, method="json_schema", strict=True)
    prompt = ChatPromptTemplate.from_template(ROLE_DICE_BRAINSTORM_PROMPT)
    out = (prompt | chain).invoke({})
    if isinstance(out, RoleDiceOutput):
        return out.role_4
    if isinstance(out, dict):
        return out.get("role_4", "生活在中国的随机成年人，职业与性格不限")
    return "生活在中国的随机成年人，职业与性格不限"


# ==========================================
# 3. 生成器核心逻辑
# ==========================================

def generate_profile(seed_instruction: str = None, use_role_dice: bool = True):
    """
    根据种子指令生成用户画像。
    如果 seed_instruction 为空或为通用随机指令，会先执行「角色骰子」：头脑风暴 5 个反差角色，取第 4 个，再生成画像以增加随机性。
    use_role_dice=False 可关闭角色骰子。
    """
    # 极速 LLM + 原生结构化输出：必须 use_responses_api=False，否则与 with_structured_output 冲突 (text_format vs text.format)
    llm = create_fast_llm(
        model=DEFAULT_MODEL,
        temperature=SETTINGS_DEFAULT_TEMPERATURE,
        use_responses_api=False,
    )
    # strict=True：weekly_schedule 已改为 List[WeeklyScheduleEntry]，无动态 key，API 接受
    structured_chain = llm.with_structured_output(
        UserProfile, method="json_schema", strict=True
    )

    # 默认指令 或 通用随机指令 时，先掷「角色骰子」再生成
    _generic_random_instructions = (
        "生成一个生活在中国的随机成年人，职业和性格不限。",
        "生成一个随机的中国人",
    )
    if not seed_instruction:
        seed_instruction = _generic_random_instructions[0]
    seed_stripped = seed_instruction.strip()
    if use_role_dice and (seed_stripped in _generic_random_instructions or not seed_stripped):
        role = roll_role_dice(llm)
        seed_instruction = f"生成一个生活在中国的成年人，职业/身份为：{role}。其余性格、作息与生活细节请自由发挥，保持高保真仿真所需的具体度。"
        print(f"[角色骰子] 已选定第 4 个角色: {role}")

    # 提示词见 prompt.PROFILE_GENERATOR_PROMPT_TEMPLATE
    prompt = ChatPromptTemplate.from_template(PROFILE_GENERATOR_PROMPT_TEMPLATE)
    chain = prompt | structured_chain

    print("[INFO] Generating profile...")
    try:
        profile = chain.invoke({"seed_instruction": seed_instruction})
        if isinstance(profile, UserProfile):
            out = profile.model_dump()
            # 将 weekly_schedule 从 [{"day": "monday", "activity": {...}}] 转为 {"monday": {...}} 以兼容下游
            routines = out.get("routines", {})
            ws = routines.get("weekly_schedule", [])
            if isinstance(ws, list):
                routines["weekly_schedule"] = {e["day"]: e["activity"] for e in ws}
            return out
        if isinstance(profile, dict):
            return UserProfile.model_validate(profile).model_dump()
        raise ValueError(f"Unexpected profile type: {type(profile).__name__}")
    except Exception as e:
        print(f"[ERROR] Profile generation failed: {e}")
        return None

# ==========================================
# 4. 主程序
# ==========================================

if __name__ == "__main__":
    # --- 你可以在这里修改生成要求 ---
    # 场景 A: 随机生成
    prompt_text = "生成一个随机的中国人"
    
    # 场景 B: 指定特定类型 (你可以修改这里来测试不同的人设)
    #prompt_text = "生成一个35岁的女性自由插画师，喜欢猫，有点社恐，经常熬夜，住在成都。"
    
    # 1. 生成数据
    new_profile = generate_profile(prompt_text)

    if new_profile:
        # 2. 保存到 profile.json (覆盖旧文件，供后续Agent使用)
        current_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(current_dir, 'profile.json')
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(new_profile, f, ensure_ascii=False, indent=2)
            
        print(f"\n[成功] 新的用户画像已生成并保存至: {file_path}")
        print(f"姓名: {new_profile['name']}")
        print(f"职业: {new_profile['occupation']}")
        print(f"性格标签: {new_profile['personality']['traits']}")
        print("-" * 30)
    else:
        print("[ERROR] profile.json generation failed. Exiting.")
        sys.exit(1)
