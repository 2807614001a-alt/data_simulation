
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from typing import List, Dict, Any

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field

# --- 环境配置 ---
load_dotenv()
current_dir = Path(__file__).resolve().parent
dotenv_path = current_dir.parent / '.env'
load_dotenv(dotenv_path=dotenv_path)

# ==========================================
# 1. 复用数据结构 (保持 Schema 绝对一致)
# ==========================================

class EnvironmentState(BaseModel):
    temperature: float
    humidity: float
    light_level: float
    noise_level: float

class RoomInfo(BaseModel):
    room_type: str
    area_sqm: float
    furniture: List[str]
    devices: List[str]
    environment_state: EnvironmentState

# 包装类，用于提示 Parser 输出结构，但最后我们会剥离 root key
class HouseSnapshot(BaseModel):
    rooms: Dict[str, RoomInfo]

# ==========================================
# 2. 辅助函数
# ==========================================

def load_json_file(filename):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"错误: 找不到文件 {path}。")
        sys.exit(1)

def save_json_file(data, filename):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[成功] 逻辑修正后的户型图已保存至: {path}")

# ==========================================
# 3. 核心 Agent: 逻辑审查与修正
# ==========================================

def run_logic_fixer_agent():
    # 1. 读取上下文
    print(">>> 1. 读取 Profile 和 原始 Layout...")
    profile = load_json_file("profile.json")
    layout = load_json_file("house_layout.json")

    profile_str = json.dumps(profile, ensure_ascii=False)
    layout_str = json.dumps(layout, ensure_ascii=False)

    # 2. 初始化 LLM (使用 GPT-4 保证逻辑推理能力)
    llm = ChatOpenAI(model="gpt-4", temperature=0.3) # 低温，由发散转为严谨
    parser = JsonOutputParser(pydantic_object=HouseSnapshot)

    # 3. 定义“找茬” Prompt
    template = template = """
    你是一位**具有极强常识推理能力的仿真逻辑审查官 (Simulation Logic Auditor)**。
    你的核心任务是进行**逻辑闭环检查**：确保用户画像中的每一个特征、动作和需求，在物理空间中都有对应的物体作为支撑。

    **输入上下文**:
    1. **用户画像 (Profile)**: 
    {profile_context}
    
    2. **当前生成的户型数据 (Draft Layout)**: 
    {layout_context}

    **审查思维链 (Chain of Thought) - 请遵循以下原则进行广义修正**:

    1. **“无对象，不行为”原则 (职业与爱好检查)**:
       - 遍历用户的 `occupation` (职业)、`routines` (日程) 和 `preferences` (爱好)。
       - **核心逻辑**: 如果用户需要执行某个动作，房间里必须有对应的工具。
       - *推理示例*: 
         - 是“音乐家”？-> 必须有乐器（钢琴/吉他/小提琴）。
         - 是“健身教练”？-> 必须有哑铃、深蹲架或跑步机。
         - 爱“喝茶”？-> 必须有茶具套装。
         - 爱“打游戏”？-> 必须有游戏主机或高配PC。
       - **执行**: 发现缺失的工具，立即添加到最合适的房间（如书房、客厅或卧室）。

    2. **生命体依存原则 (宠物与特殊住户)**:
       - 检查 Profile 中提及的任何**非人类生命体**（猫、狗、鸟、爬宠等）。
       - **核心逻辑**: 任何生命体都需要“吃、喝、拉、睡”的物理容器。
       - **执行**: 
         - 有猫 -> 补全猫砂盆、猫碗、猫抓板。
         - 有狗 -> 补全狗窝、喂食器。
         - 有鱼 -> 补全鱼缸。
         - (如果没有提及宠物，忽略此项)。

    3. **人类生存底线原则 (通用基础设施)**:
       - 无论用户人设多么特殊（哪怕是极简主义者），现代人类生活必须包含以下设施，**缺一不可，强制补全**：
         - **卫生/衣物**: `washing_machine_001` (洗衣机)、`laundry_rack_001` (晾衣架)。
         - **废弃物处理**: `trash_can_001` (必须在厨房和主要活动区域出现)。
         - **入口收纳**: `shoe_cabinet_001` (鞋柜)。

    4. **性格-环境一致性微调**:
       - 检查 `personality` 数值。
       - 如果“尽责性”极高且有洁癖 -> 确保有 `vacuum_cleaner` (吸尘器) 或 `cleaning_tools`。
       - 如果“神经质”极高 -> 确保卧室有 `blackout_curtain` (遮光窗帘) 或 `soundproofing_panel` (隔音板)。

    **输出要求**:
    - 输出修正后的**完整 JSON 对象**。
    - **严格保持原有的 Schema 结构** (Key为房间ID，Value为房间详情)。
    - **ID命名规范**: 使用具体的英文单词 + 编号 (如 `grand_piano_001`, `easel_001`)。
    - 仅进行必要的**增量修正**，不要删除原有合理物品。

    {format_instructions}
    """

    prompt = ChatPromptTemplate.from_template(template)
    prompt = prompt.partial(format_instructions=parser.get_format_instructions())

    chain = prompt | llm | parser

    print(">>> 2. 正在进行逻辑审查与修正 (Logic Inspection)...")
    print("    正在检查：职业工具、宠物用品、生存设施...")
    
    try:
        fixed_layout = chain.invoke({
            "profile_context": profile_str,
            "layout_context": layout_str
        })
        
        # 数据清洗：处理可能存在的根节点包裹
        if "rooms" in fixed_layout and isinstance(fixed_layout["rooms"], dict):
            return fixed_layout["rooms"]
        return fixed_layout

    except Exception as e:
        print(f"修正过程出错: {e}")
        return layout # 如果出错，返回原件防止中断

# ==========================================
# 4. 主程序入口
# ==========================================

if __name__ == "__main__":
    # 运行逻辑修正
    final_layout = run_logic_fixer_agent()
    
    # 打印差异（可选，简单对比一下家具数量）
    print("\n>>> 修正摘要:")
    try:
        old_layout = load_json_file("house_layout.json")
        for room_id, room_data in final_layout.items():
            old_count = len(old_layout.get(room_id, {}).get("furniture", [])) if room_id in old_layout else 0
            new_count = len(room_data.get("furniture", []))
            if new_count > old_count:
                print(f"  - {room_id}: 家具增加了 {new_count - old_count} 件")
            elif old_count == 0 and new_count > 0:
                print(f"  - {room_id}: [新增房间]")
    except:
        pass

    # 保存覆盖原文件
    save_json_file(final_layout, "house_layout.json")