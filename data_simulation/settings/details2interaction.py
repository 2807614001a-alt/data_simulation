import json
import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Any, Union, Optional
from dotenv import load_dotenv

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
# 1. Pydantic 数据结构 (保持不变)
# ==========================================

class Precondition(BaseModel):
    type: str = Field(description="条件类型，如 location, object_state, time_constraint")
    value: Union[str, bool, int, float] = Field(description="条件所需的值")
    object: Optional[str] = Field(description="如果特定于某个对象，在此指定", default=None)
    max_meters: Optional[float] = Field(description="距离限制", default=None)

class Effect(BaseModel):
    type: str = Field(description="影响类型，如 user_state, object_state")
    attribute: str = Field(description="受影响的属性，如 energy_level, hygiene, state")
    value: Optional[Union[str, bool, int, float]] = Field(description="变成的具体值", default=None)
    delta: Optional[float] = Field(description="数值变化量", default=None)
    per_minute: Optional[bool] = Field(description="是否随时间持续变化", default=False)

class Duration(BaseModel):
    min: float = Field(description="最小持续分钟数")
    max: float = Field(description="最大持续分钟数")

class InteractionRule(BaseModel):
    action: str = Field(description="动作名称，如 sleep, turn_on")
    applicable_objects: List[str] = Field(description="支持该动作的所有物品中文名称列表")
    preconditions: List[Precondition] = Field(description="执行动作的前置条件")
    effects: List[Effect] = Field(description="执行动作后的状态改变")
    duration_minutes: Duration = Field(description="动作持续时间范围")

# ==========================================
# 2. 辅助函数
# ==========================================

def load_json_file(filename):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"错误: 找不到文件 {path}")
        sys.exit(1)

def save_json_file(data, filename="interaction_rules.json"):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    with open(path, 'w', encoding='utf-8') as f:
        # 包装成 interaction_rules 列表格式，符合你的需求
        final_output = {"interaction_rules": data}
        json.dump(final_output, f, ensure_ascii=False, indent=2)
    print(f"[完成] 交互规则库已保存至: {path}")

def aggregate_actions(items_data: List[Dict]) -> Dict[str, List[str]]:
    """
    聚合动作：将所有物品按动作归类
    """
    action_map = {}
    print(">>> [1/3] 正在聚合动作与物品...")
    for item in items_data:
        name = item.get("name", "未知物品")
        actions = item.get("support_actions", [])
        
        for action in actions:
            if action not in action_map:
                action_map[action] = set()
            action_map[action].add(name)
    
    return {k: list(v) for k, v in action_map.items()}

# ==========================================
# 3. 核心 Agent：处理单个动作
# ==========================================

def process_single_action_rule(llm, action_name: str, objects: List[str]):
    """
    针对单个动作调用 LLM 生成规则
    """
    # 这里我们只需要解析出一个 Rule 对象，而不是列表
    parser = JsonOutputParser(pydantic_object=InteractionRule)
    
    template = """
    你是一个仿真系统逻辑引擎。请为**单个动作**定义详细的交互规则。

    **目标动作**: {action_name}
    **适用物品**: {object_list}

    **逻辑定义要求**:
    1. **Preconditions (前置条件)**: 
       - 用户必须在哪里？(例如 location: same_room)
       - 物品必须处于什么状态？(例如 'open' 动作通常要求物品当前是 'closed')
    2. **Effects (影响)**: 
       - 对用户属性的影响 (energy_level, hygiene, hunger, stress等)。
       - 对物品状态的影响 (state变成occupied/open/on等)。
       - 尽量使用数值 delta (变化量) 而不是绝对值，除非是状态切换。
    3. **Duration (耗时)**:
       - 给出符合现实逻辑的最小和最大分钟数。

    **输出格式**:
    必须严格按照 JSON 格式输出单个对象。
    {format_instructions}
    """

    prompt = ChatPromptTemplate.from_template(template)
    prompt = prompt.partial(format_instructions=parser.get_format_instructions())
    
    chain = prompt | llm | parser

    print(f"  -> 正在生成规则: [{action_name}] (涉及 {len(objects)} 个物品)...")
    
    try:
        result = chain.invoke({
            "action_name": action_name,
            "object_list": json.dumps(objects, ensure_ascii=False)
        })
        
        # 容错处理：如果 LLM 返回了列表 [Rule]，取第一个；如果是字典 Rule，直接返回
        if isinstance(result, list) and len(result) > 0:
            return result[0]
        return result
        
    except Exception as e:
        print(f"  [警告] 动作 '{action_name}' 生成失败: {e}")
        # 返回一个基本的兜底数据，防止程序崩溃
        return {
            "action": action_name,
            "applicable_objects": objects,
            "preconditions": [],
            "effects": [],
            "duration_minutes": {"min": 1, "max": 5}
        }

# ==========================================
# 4. 主程序
# ==========================================

def main():
    # 1. 读取数据
    items_data = load_json_file("house_details.json")
    
    # 2. 聚合
    aggregated_map = aggregate_actions(items_data)
    total_actions = len(aggregated_map)
    print(f"    共识别出 {total_actions} 个唯一动作。")
    
    # 3. 初始化 LLM
    llm = ChatOpenAI(model="gpt-4", temperature=0.7)
    
    # 4. 逐个生成 (Loop)
    all_rules = []
    print("\n>>> [2/3] 开始逐个生成规则逻辑...")
    
    for i, (action, objects) in enumerate(aggregated_map.items(), 1):
        # 调用处理函数
        rule_data = process_single_action_rule(llm, action, objects)
        
        if rule_data:
            all_rules.append(rule_data)
            
        # 可选：防止触发 API 速率限制，每处理几个动作停顿一下
        # if i % 5 == 0: time.sleep(1)

    # 5. 保存
    print(f"\n>>> [3/3] 全部完成。共生成 {len(all_rules)} 条规则。")
    save_json_file(all_rules)

if __name__ == "__main__":
    main()