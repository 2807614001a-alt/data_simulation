import sys
from pathlib import Path

_current_dir = Path(__file__).resolve().parent
_project_root = _current_dir.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import json
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Union, Optional

from llm_utils import create_fast_llm
from agent_config import (
    DEFAULT_MODEL,
    SETTINGS_DETAILS2INTERACTION_TEMPERATURE,
    SETTINGS_DETAILS2INTERACTION_USE_RESPONSES_API,
    MAX_WORKERS_DEFAULT,
)
from prompt import DETAILS2INTERACTION_ACTION_PROMPT_TEMPLATE
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()
dotenv_path = _project_root / ".env"
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

_thread_local = threading.local()

def get_max_workers(total: int, env_name: str = "MAX_WORKERS", default: int = None) -> int:
    """
    根据数据量与环境变量决定并行度；未设 MAX_WORKERS 时使用 agent_config.MAX_WORKERS_DEFAULT。
    """
    if default is None:
        default = MAX_WORKERS_DEFAULT
    if total <= 1:
        return 1
    env_value = os.getenv(env_name)
    if env_value:
        try:
            value = int(env_value)
            if value > 0:
                return min(value, total)
        except ValueError:
            pass
    cpu_count = os.cpu_count() or default
    return min(total, max(1, min(8, cpu_count)))

def get_thread_llm():
    llm = getattr(_thread_local, "llm", None)
    if llm is None:
        # 极速配置（minimal reasoning + low verbosity），但关闭 use_responses_api 以兼容 with_structured_output，避免 text.format vs text_format 冲突
        llm = create_fast_llm(
            model=DEFAULT_MODEL,
            temperature=SETTINGS_DETAILS2INTERACTION_TEMPERATURE,
            use_responses_api=SETTINGS_DETAILS2INTERACTION_USE_RESPONSES_API,
        )
        _thread_local.llm = llm
    return llm

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
    针对单个动作调用 LLM 生成规则（极速 JSON 模式：with_structured_output）
    """
    # strict=False：避免嵌套 List/Dict 触犯 API schema 校验
    structured_chain = llm.with_structured_output(
        InteractionRule, method="json_schema", strict=False
    )
    prompt = ChatPromptTemplate.from_template(DETAILS2INTERACTION_ACTION_PROMPT_TEMPLATE)
    chain = prompt | structured_chain

    print(f"  -> 正在生成规则: [{action_name}] (涉及 {len(objects)} 个物品)...")
    
    try:
        result = chain.invoke({
            "action_name": action_name,
            "object_list": json.dumps(objects, ensure_ascii=False)
        })
        if isinstance(result, InteractionRule):
            return result.model_dump()
        if isinstance(result, list) and len(result) > 0:
            return result[0] if not isinstance(result[0], InteractionRule) else result[0].model_dump()
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
    llm = get_thread_llm()
    
    # 4. 逐个生成 (Loop)
    all_rules = []
    print("\n>>> [2/3] 开始逐个生成规则逻辑...")
    
    action_items = list(aggregated_map.items())
    max_workers = get_max_workers(len(action_items))

    def _worker(item):
        action, objects = item
        return process_single_action_rule(get_thread_llm(), action, objects)

    if max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for rule_data in executor.map(_worker, action_items):
                if rule_data:
                    all_rules.append(rule_data)
    else:
        for i, (action, objects) in enumerate(action_items, 1):
            # ?????????
            rule_data = process_single_action_rule(llm, action, objects)
            
            if rule_data:
                all_rules.append(rule_data)
                
            # ??????????? API ?????????????????????????
            # if i % 5 == 0: time.sleep(1)

    print(f"\n>>> [3/3] 全部完成。共生成 {len(all_rules)} 条规则。")
    save_json_file(all_rules)

if __name__ == "__main__":
    main()
