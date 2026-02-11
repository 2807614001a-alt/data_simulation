# -*- coding: utf-8 -*-
"""
统一配置：LLM（create_fast_llm）、仿真与数据操作参数。
改这一处即可调模型、温度、推理强度、天数、权重、并发等，便于慢慢调。
环境变量可覆盖（见各变量下方注释）；未写 env 的也可自行加 os.getenv。
"""
import os
from pathlib import Path

_here = Path(__file__).resolve().parent
_dotenv = _here / ".env"
if _dotenv.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_dotenv)
    except Exception:
        pass

def _env(key: str, default: str) -> str:
    v = os.getenv(key)
    return v.strip() if v else default

def _env_bool(key: str, default: bool) -> bool:
    v = os.getenv(key, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "y", "on")

def _env_float(key: str, default: float) -> float:
    v = os.getenv(key)
    if v is None or v.strip() == "":
        return default
    try:
        return float(v.strip())
    except ValueError:
        return default

def _env_int(key: str, default: int) -> int:
    v = os.getenv(key)
    if v is None or v.strip() == "":
        return default
    try:
        return int(v.strip())
    except ValueError:
        return default

# =============================================================================
# create_fast_llm 相关（模型、温度、推理、API 方式、调试）
# =============================================================================

# 模型，环境变量 OPENAI_MODEL 覆盖
DEFAULT_MODEL = _env("OPENAI_MODEL", "gpt-5-nano") or "gpt-5-nano"

# 推理强度：minimal | low | medium | high，部分 API 支持 none。OPENAI_REASONING_EFFORT 覆盖（minimal 最快）
REASONING_EFFORT = _env("OPENAI_REASONING_EFFORT", "minimal").lower() or "minimal"

# 输出冗长度：low | medium | high，部分 API 支持。OPENAI_VERBOSITY 覆盖（low 最快）
VERBOSITY = _env("OPENAI_VERBOSITY", "low").lower() or "low"

# 是否打印每次 LLM 请求耗时（判断是否耗在第三方 API），SIM_LOG_LLM_TIMING=1 开启
LOG_LLM_TIMING = _env_bool("SIM_LOG_LLM_TIMING", False)

# 是否打印 create_fast_llm 的 model/reasoning_effort/base_url，OPENAI_LLM_DEBUG=1 开启
LLM_DEBUG = _env_bool("OPENAI_LLM_DEBUG", False)

# --- Planning ---
PLANNING_TEMPERATURE = _env_float("SIM_PLANNING_TEMPERATURE", 0.7)
PLANNING_USE_RESPONSES_API = False

# --- Event ---
EVENT_TEMPERATURE = _env_float("SIM_EVENT_TEMPERATURE", 0.7)
EVENT_USE_RESPONSES_API = False

# --- Device Operate ---
DEVICE_OPERATE_TEMPERATURE = _env_float("SIM_DEVICE_OPERATE_TEMPERATURE", 0.3)
DEVICE_OPERATE_USE_RESPONSES_API = False

# --- Settings 脚本 ---
SETTINGS_DEFAULT_TEMPERATURE = _env_float("SIM_SETTINGS_TEMPERATURE", 0.0)
SETTINGS_USE_RESPONSES_API = True
SETTINGS_DETAILS2INTERACTION_USE_RESPONSES_API = False
SETTINGS_DETAILS2INTERACTION_TEMPERATURE = _env_float("SIM_SETTINGS_DETAILS2INTERACTION_TEMPERATURE", 0.0)

# =============================================================================
# 仿真 / 14 天与单日（固定数据操作参数）
# =============================================================================

# 模拟天数，SIM_DAYS 覆盖
DAYS = max(1, _env_int("SIM_DAYS", 14))

# 起始日期，如 "2025-01-01"；空则从今天起算，SIM_START_DATE 覆盖
START_DATE = _env("SIM_START_DATE", "").strip() or None

# 是否跑 event + device 层（0 则只跑 planning），SIM_RUN_EVENTS 覆盖
RUN_EVENTS = _env("SIM_RUN_EVENTS", "1").strip() != "0"

# 状态权重：Normal / Perturbed / Crisis，SIM_NORMAL_WEIGHT 等覆盖
NORMAL_WEIGHT = _env_float("SIM_NORMAL_WEIGHT", 0.7)
PERTURBED_WEIGHT = _env_float("SIM_PERTURBED_WEIGHT", 0.2)
CRISIS_WEIGHT = _env_float("SIM_CRISIS_WEIGHT", 0.1)

# 随机事件次数：高斯均值、标准差、上限，SIM_RANDOM_EVENT_MEAN 等覆盖
RANDOM_EVENT_MEAN = _env_float("SIM_RANDOM_EVENT_MEAN", 1.0)
RANDOM_EVENT_STD = _env_float("SIM_RANDOM_EVENT_STD", 0.5)
RANDOM_EVENT_MAX = max(0, _env_int("SIM_RANDOM_EVENT_MAX", 3))

# 随机种子，设则复现；SIM_RANDOM_SEED 覆盖
RANDOM_SEED = _env("SIM_RANDOM_SEED", "").strip() or None

# 首日强制状态，如 "Normal" / "Perturbed" / "Crisis"；SIM_FORCE_DAY1_STATE 覆盖
FORCE_DAY1_STATE = _env("SIM_FORCE_DAY1_STATE", "").strip() or None

# 写 JSON 是否紧凑（无 indent）以省 I/O，SIM_COMPACT_JSON=1 开启
COMPACT_JSON = _env_bool("SIM_COMPACT_JSON", False)

# =============================================================================
# Planning 层：校验与修正（影响 token/耗时）
# =============================================================================

# 是否跳过 planning 校验/修正（默认不跳过），SIM_SKIP_PLANNING_VALIDATION=1 开启
SKIP_PLANNING_VALIDATION = _env_bool("SIM_SKIP_PLANNING_VALIDATION", False)

# 校验未过时最多修正几轮，SIM_MAX_PLANNING_REVISIONS 覆盖
MAX_PLANNING_REVISIONS = max(0, _env_int("SIM_MAX_PLANNING_REVISIONS", 1))

# =============================================================================
# Event 层：校验与修正轮数
# =============================================================================

# 是否跳过 event 校验/修正（默认不跳过），SIM_SKIP_EVENT_VALIDATION=1 开启
SKIP_EVENT_VALIDATION = _env_bool("SIM_SKIP_EVENT_VALIDATION", False)

# 校验未过时最多修正几轮，SIM_MAX_EVENT_REVISIONS 覆盖
MAX_EVENT_REVISIONS = max(0, _env_int("SIM_MAX_EVENT_REVISIONS", 1))

# =============================================================================
# 并发：Settings / Device 等脚本里线程池默认 worker 数
# =============================================================================

# MAX_WORKERS 环境变量未设时用的默认值；device_operate / details2interaction / layout2details 会参考
MAX_WORKERS_DEFAULT = max(1, _env_int("SIM_MAX_WORKERS_DEFAULT", 8))
