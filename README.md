# 居家人工智能仿真 (Data Simulation)

基于 LLM 与物理引擎的多日居家生活仿真：从居民档案与户型配置出发，生成每日活动计划、细粒度事件与设备操作链，并保证室内环境（温湿度、清洁度、空气清新度等）随设备与时间连续变化。

---

## 功能概览

- **多日循环**：按配置天数逐日运行，每日承接前一日结束时的房间环境与设备状态，保证跨日数据一致。
- **Planning 层**：根据居民档案、星期与日期生成当日活动计划（活动名、房间、起止时间），支持校验与 LLM 修正。
- **Event 层**：为每个活动生成时间线事件（含 `device_patches`），支持按段迭代生成与物理推进，环境评估与「必须响应」驱动人物调节设备。
- **物理引擎**：根据设备开关、开窗、空调/暖气/净化器等计算各房间温度、湿度、清洁度、空气清新度，用于下一段/下一活动的环境输入。
- **Device Chain 层**：将事件转为 `action_event_chain`（含 `layer5_device_state` 的 patch_on_start/patch_on_end），供下游控制或学习使用。
- **室外天气**：可选接入 OpenWeather 获取室外温湿度，用于室内自然衰减与日变化。

---

## 环境要求

- **Python**：3.10+
- **主要依赖**：`python-dotenv`、`langchain-core`、`langchain-openai`、`langgraph`、`pydantic`、`openai`、`httpx` 等（见各模块 `import`，可按需从 `pip install langchain-openai langgraph python-dotenv pydantic` 起装）。

---

## 目录结构

```
data_simulation/
├── agents/                    # 仿真核心
│   ├── n_day_simulation.py    # 多日仿真入口（推荐由此运行）
│   ├── planning.py            # 活动计划生成与校验
│   ├── event.py               # 事件生成、校验、修正与物理闭环
│   ├── device_operate.py      # 事件 → action_event_chain
│   ├── physics_engine.py      # 房间环境物理计算
│   └── weather.py             # 室外天气
├── settings/                  # 配置与 Settings 流水线
│   ├── profile.json           # 居民档案（可由 profile_generator 生成）
│   ├── house_layout.json      # 户型与房间（可由 profile2layout 生成）
│   ├── house_details.json     # 家具与设备详情（可由 layout2details 等生成）
│   ├── autosetting.py         # 一键跑完 profile→layout→details→interaction→validation
│   ├── profile_generator.py   # 生成 profile.json
│   ├── profile2layout.py      # profile → house_layout
│   ├── layout2details.py     # layout → house_details
│   ├── details2interaction.py
│   └── final_validation.py
├── data/                      # 运行输出（自动生成）
│   ├── simulation_context_dayN.json
│   ├── activity_dayN.json
│   ├── events_dayN.json
│   ├── action_event_chain_dayN.json
│   └── ...
├── agent_config.py            # 统一配置（模型、天数、重试、开关等）
├── prompt.py                  # LLM 提示模板
├── llm_utils.py               # LLM 创建与封装
└── docs/                      # 流程图与说明
    ├── FLOW_OVERVIEW.mmd
    └── FLOW_ALL.mmd
```

---

## 快速开始

### 1. 配置环境变量

在项目根目录创建 `.env`，至少配置 LLM 相关项（若使用 OpenAI 兼容 API）：

```bash
# 必选：API Key
OPENAI_API_KEY=sk-xxx

# 可选：自定义 base_url（默认 https://api.openai.com/v1）
OPENAI_BASE_URL=https://your-api/v1

# 可选：模型与推理强度
OPENAI_MODEL=gpt-5-nano
OPENAI_REASONING_EFFORT=minimal
```

其他常用项见 `agent_config.py`（如 `SIM_DAYS`、`SIM_RUN_EVENTS`、`OPENWEATHER_CITY` 等）。

### 2. 准备 Settings（若尚未有 profile/layout/details）

在 `settings` 目录下执行一键流水线，生成 `profile.json`、`house_layout.json`、`house_details.json` 等：

```bash
cd settings
python autosetting.py
```

也可单独运行 `profile_generator.py` → `profile2layout.py` → `layout_check.py` → `layout2details.py` → `details2interaction.py` → `final_validation.py`。

### 3. 运行多日仿真

在**项目根目录** `data_simulation` 下执行（保证可导入 `agents`、`agent_config` 等）：

```bash
cd data_simulation
python -m agents.n_day_simulation
```

或进入 `agents` 目录后执行：

```bash
cd agents
python n_day_simulation.py
```

运行后会按 `agent_config.DAYS` 逐日生成 `data/` 下的 `simulation_context_dayN.json`、`activity_dayN.json`、`events_dayN.json`、`action_event_chain_dayN.json` 等。

---

## 主要配置说明

| 配置项 | 环境变量 | 默认 | 说明 |
|--------|----------|------|------|
| 模拟天数 | `SIM_DAYS` | 7 | 多日仿真天数 |
| 是否跑 Event+Device 层 | `SIM_RUN_EVENTS` | 1 | 设为 0 则只跑 Planning |
| 起始日期 | `SIM_START_DATE` | 当天 | 如 `2025-01-01` |
| LLM 模型 | `OPENAI_MODEL` | gpt-5-nano | 与 base_url 对应 |
| 活动级重试次数 | `SIM_LLM_RETRY_COUNT` | 3 | 网络/超时失败时重试 |
| 单次调用内层重试 | `SIM_INNER_LLM_RETRY_COUNT` | 3 | 连接/5xx 时单次 invoke 重试 |
| 跳过 Event 校验 | `SIM_SKIP_EVENT_VALIDATION` | 0 | 设为 1 可提速 |
| 迭代按段生成事件 | `USE_ITERATIVE_EVENT_GENERATION` | True | 每段后跑物理推进环境 |
| 室外天气城市 | `OPENWEATHER_CITY` / `WEATHER_CITY` | Beijing | 用于室外温湿度 |

更多见 `agent_config.py` 内注释。

---

## 输出数据说明

- **simulation_context_dayN.json**：当日仿真的上下文（日期、时间窗、agent_state、室外天气等）。
- **activity_dayN.json**：当日活动列表（活动名、房间、起止时间等）。
- **events_dayN.json**：当日事件列表及每个事件附带的 `room_environment`、`device_patches`；`meta.environment_by_activity` 为各活动开始时各房间环境快照。
- **action_event_chain_dayN.json**：由事件生成的设备操作链，含 `layer5_device_state`（patch_on_start / patch_on_end），供下游控制或策略学习。

多日仿真时，Day2 及以后会使用 Day1 结束时的 `environment_snapshot` 与 `device_states` 作为当日初值，保证「初始环境 → 设备操作 → 最终环境」在跨日场景下仍一致。

---

## 流程图

- **总览**：`docs/FLOW_OVERVIEW.mmd`（从启动到多日、Planning、Event、Device Chain、物理引擎）。
- **全流程细分**：`docs/FLOW_ALL.mmd`（含各层内部节点与物理调用关系）。

可用 [Mermaid](https://mermaid.live) 或支持 Mermaid 的 Markdown 预览打开 `.mmd` 文件查看。

---

## 常见问题

- **连接错误 / SSL 错误**：若使用代理，可临时取消 `HTTP_PROXY`/`HTTPS_PROXY` 或增加 `SIM_LLM_RETRY_COUNT`、`SIM_INNER_LLM_RETRY_DELAY`。代码会将连接/SSL/5xx 视为可重试并自动重试。
- **Day2 环境与设备未延续**：请确保使用当前版本；已支持将上一日结束时的 `environment_snapshot` 与 `device_states` 作为下一日初值。
- **只跑 Planning**：设置 `SIM_RUN_EVENTS=0`，则每日只生成活动计划并写入 `activity_dayN.json`，不生成事件与 device chain。

---

## License

见项目根目录或仓库说明。
