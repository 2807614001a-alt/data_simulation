import json
import os
import sys
import random
from pathlib import Path
from dotenv import load_dotenv
from typing import List, Dict, Optional

from llm_utils import create_chat_llm
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field

# --- 环境配置 ---
load_dotenv()
current_dir = Path(__file__).resolve().parent
dotenv_path = current_dir.parent / '.env'
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

class Routines(BaseModel):
    sleep_schedule: SleepSchedule
    meal_habits: MealHabits
    exercise: Exercise
    weekly_schedule: Dict[str, WeeklyActivity] = Field(
        description="典型的一周日程安排，Key为星期几(英文小写，如 'tuesday')，挑选3-5个典型活动即可"
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
# 2. 生成器核心逻辑
# ==========================================

def generate_profile(seed_instruction: str = None):
    """
    根据种子指令生成用户画像。
    如果 seed_instruction 为空，则完全随机生成。
    """
    
    # 初始化模型
    llm = create_chat_llm(model="gpt-4", temperature=0.8) # 稍微调高温度以增加多样性
    parser = JsonOutputParser(pydantic_object=UserProfile)

    # 默认指令
    if not seed_instruction:
        seed_instruction = "生成一个生活在中国的随机成年人，职业和性格不限。"

    template = """
    你是一位兼具社会学洞察力与小说家想象力的**人物侧写专家**。请根据指令生成一个用于高保真家庭生活仿真的用户画像（User Profile）。

    **输入指令:**
    "{seed_instruction}"

    **生成核心原则 (拒绝平庸)**:
    1. **身份颗粒度**: 
       - 拒绝模糊的标签（如“职员”）。
       - **必须具体**: 例如“在家远程办公的金融分析师，经常需要视频会议”或“刚退休的植物学教授，痴迷于兰花”。
       - **职业影响**: 职业必须体现在 `routines`（作息）和 `values`（价值观）中。

    2. **性格的物理投射 (重要)**:
       - **五大性格 (Big Five)**: 请给出精确数值。
       - **特质 (Traits)**: 基于数值生成 3-5 个具体的、**能影响居住环境**的特质。
         - *错误示例*: "善良" (对房子没影响)。
         - *正确示例*: "极简主义" (家里东西少), "囤积症" (储物需求大), "听觉敏感" (需要隔音), "科技发烧友" (设备多)。

    3. **生活方式与怪癖 (Routines & Preferences)**:
       - **饮食**: 不要只写“随便”。要具体，如“生酮饮食（需要大量肉类储存）”或“手冲咖啡爱好者（需要特定台面）”。
       - **爱好**: **必须**包含 1-2 个需要**特定物理空间或设备**的爱好。
         - *例子*: 瑜伽（需瑜伽垫）、电竞（需双屏电脑）、烘焙（需烤箱）、撸猫（需猫爬架）。

    4. **真实感校验**:
       - 避免完美人格。可以适当加入一些缺点（如“不爱做家务”、“经常熬夜打游戏”），这会让仿真更有趣。

    5. **随机事件参数 (Random Event Config)**:
       - 需要提供 `random_event_config`，用于控制扰动态/危机态每日事件数量分布。
       - 参数应基于人物画像与现实生活方式推断（如工作压力、健康状况、社交频率、作息稳定性）。
       - 每个分布包含: mean, std, max；请给出合理、可解释的数值。
       - 参考范围: mean 0.5-2.0, std 0.1-1.0, max 1-5（可在合理范围内微调）。

    **语言要求**:
    - 所有文本内容请使用**中文**。

    **输出格式**:
    {format_instructions}
    
    请严格按照 JSON 格式输出，不要包含任何 Markdown 格式标记（如 ```json）。
    """

    prompt = ChatPromptTemplate.from_template(template)
    prompt = prompt.partial(format_instructions=parser.get_format_instructions())

    chain = prompt | llm | parser

    print("[INFO] Generating profile...")
    try:
        profile = chain.invoke({"seed_instruction": seed_instruction})
        if isinstance(profile, dict):
            return UserProfile.model_validate(profile).model_dump()
        if isinstance(profile, UserProfile):
            return profile.model_dump()
        raise ValueError(f"Unexpected profile type: {type(profile).__name__}")
    except Exception as e:
        print(f"[ERROR] Profile generation failed: {e}")
        return None

# ==========================================
# 3. 主程序
# ==========================================

if __name__ == "__main__":
    # --- 你可以在这里修改生成要求 ---
    # 场景 A: 随机生成
    prompt_text = "生成一个生活在上海的随机白领"
    
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
