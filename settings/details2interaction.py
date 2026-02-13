import sys
from pathlib import Path

_current_dir = Path(__file__).resolve().parent
_project_root = _current_dir.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import json
import os
import re
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
from prompt import (
    DETAILS2INTERACTION_ACTION_PROMPT_TEMPLATE,
    DETAILS2INTERACTION_EFFECT_REVIEW_SYSTEM,
    DETAILS2INTERACTION_EFFECT_REVIEW_USER,
)
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
        name = (item.get("name") or "未知物品").strip()
        actions = item.get("support_actions", [])
        
        for action in actions:
            if action not in action_map:
                action_map[action] = set()
            action_map[action].add(name)
    
    return {k: list(v) for k, v in action_map.items()}


def _postprocess_rules(all_rules: List[Dict], items_data: List[Dict]) -> List[Dict]:
    """
    后处理规则：正向过滤（只保留 support_actions 包含该 action 的物品）、去空格、effect type 仅做格式约束（只允许 user_state/object_state）。
    """
    details_by_name: Dict[str, Dict] = {}
    for item in items_data:
        name = (item.get("name") or "").strip()
        if name:
            details_by_name[name] = item

    out = []
    for rule in all_rules:
        if not isinstance(rule, dict):
            out.append(rule)
            continue
        rule = dict(rule)
        action = (rule.get("action") or "").strip()
        action_lower = action.lower()
        objs = rule.get("applicable_objects") or []
        new_objs = []
        for o in objs:
            s = str(o).strip()
            if s not in details_by_name:
                continue
            support = details_by_name[s].get("support_actions") or []
            if action in support or action_lower in [a.lower() for a in support]:
                new_objs.append(s)
        if not new_objs:
            continue
        rule["applicable_objects"] = new_objs
        # effect type 仅做格式约束：只允许 "user_state" 或 "object_state"，否则统一为 "object_state"（不做语义猜测）
        effects = rule.get("effects") or []
        for e in effects:
            if not isinstance(e, dict):
                continue
            t = (e.get("type") or "").strip()
            if t not in ("user_state", "object_state"):
                e["type"] = "object_state"
        rule["effects"] = effects
        out.append(rule)
    return out


def _llm_review_rule_effects(rules: List[Dict]) -> List[Dict]:
    """由智能体根据动作语义审查并修正 effects，不依赖硬编码动作列表。失败或解析异常时返回原列表。遇 429 会重试。"""
    if not rules:
        return rules
    llm = get_thread_llm()
    rules_json = json.dumps(rules, ensure_ascii=False, indent=2)
    from langchain_core.messages import SystemMessage, HumanMessage
    messages = [
        SystemMessage(content=DETAILS2INTERACTION_EFFECT_REVIEW_SYSTEM),
        HumanMessage(content=DETAILS2INTERACTION_EFFECT_REVIEW_USER.format(rules_json=rules_json)),
    ]
    for attempt in range(6):
        try:
            response = llm.invoke(messages)
            text = response.content if hasattr(response, "content") else str(response)
            text = text.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            corrected = json.loads(text)
            if isinstance(corrected, list) and len(corrected) == len(rules):
                print("  [智能体审查] 已按语义修正 effects。")
                return corrected
            break
        except Exception as e:
            err_str = str(e).lower()
            if ("429" in err_str or "ratelimitreached" in err_str) and attempt < 5:
                wait = _parse_retry_after_seconds(e)
                print(f"  [429 限流] effect 审查等待 {wait:.0f}s 后重试 ({attempt + 1}/5)...")
                time.sleep(wait)
                continue
            print(f"  [提示] effect 审查未应用（{e}），保留原规则。")
            break
    return rules


# ==========================================
# 3. 核心 Agent：处理单个动作
# ==========================================

def _is_rate_limit_error(exc: Exception) -> bool:
    s = str(exc).lower()
    return "429" in s or "ratelimitreached" in s or "rate limit" in s


def _parse_retry_after_seconds(exc: Exception) -> float:
    """从 429 错误信息中解析 'retry after X second(s)'，默认 2.0。"""
    m = re.search(r"retry\s+after\s+(\d+)\s*second", str(exc), re.I)
    if m:
        return max(1.0, float(m.group(1)))
    return 2.0


def process_single_action_rule(llm, action_name: str, objects: List[str]):
    """
    针对单个动作调用 LLM 生成规则（极速 JSON 模式：with_structured_output）。
    遇 429 限流时自动重试并等待 API 提示的秒数。
    """
    structured_chain = llm.with_structured_output(
        InteractionRule, method="json_schema", strict=False
    )
    prompt = ChatPromptTemplate.from_template(DETAILS2INTERACTION_ACTION_PROMPT_TEMPLATE)
    chain = prompt | structured_chain

    print(f"  -> 正在生成规则: [{action_name}] (涉及 {len(objects)} 个物品)...")

    max_retries = 5
    payload = {"action_name": action_name, "object_list": json.dumps(objects, ensure_ascii=False)}

    for attempt in range(max_retries + 1):
        try:
            result = chain.invoke(payload)
            if isinstance(result, InteractionRule):
                return result.model_dump()
            if isinstance(result, list) and len(result) > 0:
                return result[0].model_dump() if isinstance(result[0], InteractionRule) else result[0]
            return result
        except Exception as e:
            if _is_rate_limit_error(e) and attempt < max_retries:
                wait = _parse_retry_after_seconds(e)
                print(f"  [429 限流] 等待 {wait:.0f}s 后重试 ({attempt + 1}/{max_retries})...")
                time.sleep(wait)
                continue
            print(f"  [警告] 动作 '{action_name}' 生成失败: {e}")
            return {
                "action": action_name,
                "applicable_objects": objects,
                "preconditions": [],
                "effects": [],
                "duration_minutes": {"min": 1, "max": 5},
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
        # 单线程时在每次请求后加短延迟，降低 429 限流概率
        delay_between_calls = float(os.getenv("DETAILS2INTERACTION_DELAY_SECONDS", "1.5"))
        for i, (action, objects) in enumerate(action_items, 1):
            rule_data = process_single_action_rule(llm, action, objects)
            if rule_data:
                all_rules.append(rule_data)
            if i < len(action_items) and delay_between_calls > 0:
                time.sleep(delay_between_calls)

    # 5. 后处理：过滤 applicable_objects、去空格、统一 effect type
    all_rules = _postprocess_rules(all_rules, items_data)
    # 6. 由智能体根据动作语义审查 effects（无硬编码动作列表）
    print("\n>>> [3/3] 智能体审查 effects 合理性...")
    all_rules = _llm_review_rule_effects(all_rules)
    print(f"    共生成 {len(all_rules)} 条规则（已后处理并由智能体审查 effects）。")
    save_json_file(all_rules)

if __name__ == "__main__":
    main()
