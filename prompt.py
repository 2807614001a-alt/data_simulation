# -*- coding: utf-8 -*-
# =============================================================================
# Event Agent
# =============================================================================

# 通用物理交互：所有实体物品均默认支持，无需在 support_actions 中显式列出（避免 Data 层 token 爆炸与 LLM 漏写导致死锁）
EVENT_UNIVERSAL_ACTIONS = ("clean", "fix", "inspect", "touch", "move_to", "examine", "photograph", "wipe", "repair")

EVENT_REQUIREMENTS = """
## 事件定义 (Event Definition)
事件是连接宏观"活动"与微观"动作"的中间层。它是用户在特定房间内，利用特定设施完成的一个具体子目标。
核心特征：
1. **物体依赖性**：绝大多数居家事件都必须与至少一个家具或设备交互。
2. **物理可行性**：选用的物品必须支持该动作。支持判定见下方「物品功能校验」。
3. **性格时间观**：事件的耗时应反映居民性格。

## 物品功能校验 (Affordance) — 显式 + 隐式
- **显式**：若动作在物品的 `support_actions` 中，则支持。
- **隐式（通用物理类）**：以下动作为通用物理交互，**默认该房间内所有实体物品均支持**，无需在 `support_actions` 中列出：clean, fix, inspect, touch, move_to, examine, photograph, wipe, repair。
- 仅当动作**既不在** support_actions **也不属于**上述通用列表时，才判定为不支持。

## 分解原则
1. **宏观拆解**：将一个 Activity 拆解为逻辑连贯的 Event 序列。
2. **空间一致性**：切换房间必须生成独立的"移动(Move)"事件。
3. **外出闭环**：外出活动（Work, Shopping等）**不进行分解**，保持为一个单独的事件，`room` 设为 "Outside"，`target_objects` 为空。

## 输出格式要求 (JSON List)
每个事件对象包含：
- `activity_id`: 所属父活动的ID
- `start_time`: ISO格式 (YYYY-MM-DDTHH:MM:SS)
- `end_time`: ISO格式 (YYYY-MM-DDTHH:MM:SS)
- `room_id`: 发生的房间ID (必须存在于 layout 中，外出则为 "Outside")
- `target_object_ids`: 关键字段。涉及的家具/设备ID列表。
- `action_type`: ["interact", "move", "idle", "outside"]
- `description`: 详细描述。

## 约束条件
1. **物品功能校验**：target_object_ids 必须在当前 room_id 内；动作要么在 support_actions 中，要么属于通用物理交互（见上）。
2. **时间严丝合缝**：子事件时间加总必须严格等于父 Activity 的时间段。
3. **随机性注入**：基于 Profile 插入合理的微小随机事件。
"""

EVENT_GENERATION_PROMPT_TEMPLATE = """
你是一个具备物理常识和心理学洞察的行为仿真引擎。
请根据【居民档案】的性格特征，将【当前活动】递归拆解为一系列具体的【事件】。

{event_requirements}

## 输入数据 context

### 1. 居民档案 (Agent Profile)
{resident_profile_json}

### 1.1 Agent State (Real-time)
{agent_state_json}

### 2. 物理环境 (Physical Environment)
**房间列表:**
{room_list_json}
**家具与设备详情 (已过滤为当前相关区域):**
{furniture_details_json}

### 2.1 当前房间环境 (Current Room Environment) — 请根据此调节行为
{current_room_environment}

### 3. 待拆解的父活动 (Parent Activity)
{current_activity_json}

### 4. 上下文 (Context)
**前序事件 (最近{context_size}条):**
{previous_events_context}

## 任务指令
1. **分析意图**：理解父活动 `{current_activity_json}` 的目标。
2. **资源匹配**：在 `room_id` 中寻找最适合完成该目标的 `furniture` 或 `device`。
3. **环境响应**：根据「当前房间环境」与居民偏好（舒适温度、清洁度）决定是否插入调节性事件：
   - 若 |当前室温 - 舒适温度| > 2°C，可插入开/关空调、开窗等事件（如 turn_on_ac, open_window, set_temp）。
   - 若清洁度 < 0.5 且居民尽责性较高，可插入打扫类事件（如 clean_room, turn_on_vacuum）。
4. **性格渲染**：根据 Big Five 调整粒度。
5. **生成序列**：输出符合 JSON 格式的事件列表，确保时间连续且填满父活动时段。
"""

EVENT_VALIDATION_PROMPT_TEMPLATE = """
请作为"物理与逻辑审核员"，对以下生成的事件序列进行严格审查。

{event_requirements}

## 待审核数据
**环境数据:**
{house_layout_summary}

**父活动:**
{current_activity_json}

**Agent State (Real-time):**
{agent_state_json}

**生成的事件序列:**
{events_json}

## 验证维度
1. **房间合法性 (强校验)**:
   - `room_id` 必须出现在环境数据的房间列表中，否则判定不通过。
   - `room_id = "Outside"` 时，`target_object_ids` 必须为空，`action_type` 必须为 "outside"。
2. **物品归属 (强校验)**:
   - `target_object_ids` 必须全部属于对应 `room_id` 的家具/设备清单。
   - 任一物品不在该房间，判定为不通过，并指出具体物品与房间。
3. **物理可供性**:
   - 若动作在物品的 support_actions 中，或动作属于**通用物理交互**（clean, fix, inspect, touch, move_to, examine, photograph, wipe, repair），则视为支持，**不得**以「未在 support_actions 中列出」为由判不通过。
   - 仅当动作既不在 support_actions 也不属于上述通用列表时，才判定为不通过。
4. **时间完整性 (强校验)**:
   - 子事件时间必须无缝衔接、无重叠、无空洞。
   - 子事件总时长必须严格覆盖父 Activity 时间段。
5. **行为逻辑**: 顺序是否合理？房间切换是否有 Move？
6. **性格一致性**: 是否违背性格设定？

## 返回结果
- Pass: is_valid: true
- Fail: is_valid: false, 并在 correction_content 中列出"必须修正"的具体点（房间/物品/时间/动作）。注意：通用物理交互（clean/fix/inspect/touch/move_to 等）不得以「未在 support_actions 中」为由判 Fail。
"""

EVENT_CORRECTION_PROMPT_TEMPLATE = """
你是一个专业的行为修正模块。上一次生成的事件序列存在逻辑或物理错误。
请根据验证反馈，重新生成修正后的事件序列。

{event_requirements}

## 参考数据
**居民档案:** {resident_profile_json}
**可用环境物品:** {furniture_details_json}
**父活动:** {current_activity_json}
**Agent State (Real-time):** {agent_state_json}

## 错误现场
**原始错误规划:**
{original_events_json}

**验证反馈 (必须解决的问题):**
{correction_content}

## 修正指令
1. 定位错误。
2. **房间/物品修正 (强制)**:
   - 如果 `room_id` 不在环境数据中，必须改为合法房间或 "Outside"。
   - 如果改为 "Outside"，`target_object_ids` 必须清空，`action_type` 设为 "outside"。
   - 如果 `target_object_ids` 含有不在该房间的物品，必须替换为该房间内的合法物品；若无合适物品，改为 `target_object_ids = []` 并调整描述为非物品交互事件。
   - **通用物理交互**：若验证反馈仅称「某物品的 support_actions 中无 clean/fix/inspect/touch/move_to 等」，此类动作默认所有实体物品均支持，**不要**为此删改事件或替换物品，应保留原事件。
3. **时间修正 (强制)**：确保子事件无重叠、无空洞，且严格覆盖父活动时段。
4. **行为逻辑**：房间切换补充 Move 事件，保持时序合理。
5. **保持风格**：尽量保持原有叙事风格与性格一致性。
"""

# =============================================================================
# Device Operate Agent
# =============================================================================

DEVICE_STATE_GEN_PROMPT = """
You are a smart-home behavior analyzer.
Given a user event, infer what device state changes should happen at the start and end.

## Inputs
- **Event**: {description}
- **Time**: {start_time} to {end_time}
- **Devices**: {target_devices}
- **Reference**: {device_details}

## Requirements
1. **Patch on Start**: device changes at event start (e.g., power on, set mode).
2. **Patch on End**: device changes at event end (e.g., power off). If no change needed, return empty.
3. **Timestamps**:
   - Start Patch timestamp must equal {start_time}
   - End Patch timestamp must equal {end_time}
4. **Format**:
   - Provide changes as a list of key/value items (patch_items).
   - Example: {{"key": "power", "value": "on"}}

## Example Output
See requirements above; ensure output matches the EventDeviceState schema.
"""

# =============================================================================
# Planning Agent
# =============================================================================

ACTIVITY_PLANNING_REQUIREMENTS = """
## 活动规划核心要求
请生成一个详细的、符合居民特征的"日程窗口"活动规划（以起床为开始、入睡为结束）。

### 1. 仿真逻辑与状态机 (核心)
你需要首先读取【仿真上下文】中的 `simulation_state` 和 `current_date`，并按照以下逻辑执行：

* **日期类型判断 (Workday vs Weekend)**:
    * 检查 `day_of_week`。如果是工作日，严格执行 Profile 中的工作日作息；如果是周末，切换至休闲/晚起模式。
    * **周期性检查**: 检查 Profile 中是否有特定日期的活动（如"每周三健身"、"每周五聚餐"），必须将这些固定事项排入日程。

* **记忆机制 (Context Memory)**:
    * 读取 `previous_day_summary`。如果前一天有"熬夜"、"醉酒"或"高强度运动"，请在今天的 `start_time`（起床时间）或活动强度上体现**滞后效应**（如：推迟起床30分钟，或减少今日运动量）。
* **实时状态 (Agent State)**:
    * 读取 `agent_state`（包含 mood/energy/stress/health 等），并将其作为今日安排的"硬约束/软偏好"。
    * 例如：energy 低 -> 增加休息或降低强度；health=unwell -> 避免高强度运动，安排恢复与就医/休息。

* **状态机响应 (State Machine)**:
    * **正常态 (Normal)**: 遵循 80% 的基准线，无意外发生，严格按计划行事。
    * **扰动态 (Perturbed)**: 上下文中会指定一个 `random_event`（如：朋友临时拜访、轻微感冒、必须在家加班）。你必须将此事件自然地插入日程中，并展示它如何**挤占**了原本的活动时间（如：因为加班取消了晚上的阅读）。
    * **危机态 (Crisis)**: 上下文中会指定一个 `emergency_event`（如：跌倒、突发疾病、设备故障）。
        * 该事件必须在日程中发生。
        * 事件发生后，后续活动应中断或转变为"应对危机"（如：等待救援、联系家人、停止移动）。

### 2. 数据驱动的行为推导
* **生理节律**：尽可能贴合 `sleep_schedule` 和 `meal_habits`。允许**小幅误差**，但必须解释原因。
  - 餐点时间：允许 ±30 分钟内浮动；超过则必须给出明确原因（扰动/危机/跨日影响）。
  - 入睡/起床：允许 ±30 分钟内浮动；超过则必须给出明确原因（熬夜/身体不适/突发事件）。
* **日程边界**：活动必须从"起床/醒来"开始，以"入睡/睡眠"结束。
* **性格表现**：
    * 高开放性 -> 即使在扰动日，也会尝试用新颖方式解决问题。
    * 高尽责性 -> 即使生病（扰动态），也可能会尝试完成最低限度的工作。
* **环境交互**：所有活动必须绑定 `furniture` 或 `devices`。
    * *危机态特别说明*：如果发生"跌倒"，请注明跌倒发生的具体位置（Room/Furniture）。
* **资产清单强约束**：活动只能与 Asset List（house_layout/house_details）中存在的物品交互，不允许臆造物品。
* **房间映射强约束**：禁止输出未在 house_layout 中存在的房间。若需要居家办公，请映射到 house_layout 中的有效房间。
* **描述约束**：若未在 Asset List 中出现某物品，请改用泛化描述（如"准备食物/处理食材/整理餐具"），不要写出不存在的具体物品名。

### 3. 外出活动规范（闭环原则）
* 外出活动（工作、购物、运动）必须是 `Home -> Activity -> Home` 的闭环。
* 外出期间 `main_rooms` 为空列表 `[]`。
* **注意**: 如果状态为"居家办公（扰动态）"，则原本的外出工作应改为在"书房/客厅"使用"电脑"进行。

### 4. 格式与完整性
* **时间连续性**：必须无缝衔接并覆盖 `day_start_time` 到 `day_end_time`。
* **起始时间**：当 `day_start_time` 不是 00:00 时，日程必须从 `day_start_time` 开始，禁止从 00:00 开始。
* **结束时间**：日程必须覆盖至 `day_end_time`，且最后一项为"入睡/睡眠"类活动（允许跨日，直到次日醒来）。
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
    3.  **状态体现**：如果是扰动或危机，明确描述异常情况（如"感到头晕，倒在了沙发旁"）。
    4.  如果是社交，提到互动对象。
- `main_rooms`: 涉及的房间ID列表。
"""

PLANNING_PROMPT_TEMPLATE = """
你是一个基于大模型的高保真人类行为模拟器。请根据以下多维度的居民档案和物理环境，通过逻辑推演，规划出这位居民一天的"起床到入睡"活动流。

{activity_planning_requirements}

## 状态机事件要求（高优先级）
当 `simulation_state` 为 **Perturbed** 或 **Crisis** 时，按下述规则生成事件：
1. 读取 `random_event_count` 或 `emergency_event_count`，并**生成对应数量**的异常事件。
2. **必须**在活动描述中明确标注事件（使用"事件：<内容>"格式）。
3. 每个事件必须对当天日程产生实际影响（取消/推迟/缩短/改地点等）。
4. 如果是 Crisis，后续活动应转为应对处理（就医、维修、联系家人等）。

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
请作为"行为逻辑审核员"，审核以下AI生成的居民活动规划。
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

**仿真上下文 (含 agent_state):**
{simulation_context}

## 验证维度
1. **时间连续性 (强校验)**: 不允许时间重叠或空档，必须覆盖 `day_start_time` 至 `day_end_time`。
2. **起始时间 (强校验)**:
   - 若 `day_start_time` 不是 00:00，活动必须从 `day_start_time` 开始，不得出现 00:00 起床等跳变。
3. **起床/入睡边界 (强校验)**:
   - 第一个活动必须**语义上**表达"起床/醒来/晨间开始"，无需死板依赖某个词。
   - 最后一个活动必须**语义上**表达"入睡/睡眠/就寝"，并覆盖到 `day_end_time`（可跨日）。
1. **作息/餐点 (强校验)**:
   - 睡眠与起床时间应贴合 Profile（工作日/周末区分），允许 ±30 分钟误差；超过必须说明原因。
   - 早餐/午餐/晚餐应贴合 Profile 时间，允许 ±30 分钟误差；超过必须说明原因。
4. **固定事项 (强校验)**:
   - Profile 中明确的固定安排必须出现在正确的日期/时间段。
   - 如被扰动/危机影响取消，必须在描述中明确说明取消原因与替代安排。
5. **环境交互合理性 (强校验)**: main_rooms 必须来自房屋布局，且活动描述能对应家具/设备。
6. **资产清单强校验**：活动描述只能使用 Asset List 中的物品；出现不存在物品必须判为不通过。
7. **房间映射强校验**：禁止使用不在 house_layout 中的房间；若出现必须判为不通过并要求改为有效房间。
8. **扰动/危机体现 (强校验)**：
   - 若 `simulation_state` 为 Perturbed/Crisis，必须在当日活动描述中体现"异常事件的发生与影响"。
   - 判定应基于**语义**，无需固定关键词；但必须能明确看出"事件导致日程变化"（如取消/推迟/缩短/改地点/应对处理）。
9. **实时状态一致性 (强校验)**:
   - `agent_state` 显示疲劳/不适/情绪低落时，不应安排高强度或高压力活动；如确需安排，必须在描述中给出合理解释。
10. **性格逻辑性**: 活动是否违背 Big Five 性格与价值观。

## 返回结果
- 如通过: is_valid = true, correction_content 为空。
- 如不通过: is_valid = false，并在 correction_content 中详细说明"必须修正"的冲突点（含异常事件是否体现）。
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

**仿真上下文 (含 agent_state):**
{simulation_context}

## 原始规划与问题
**原始活动规划:**
{original_activity_plans_json}

**验证未通过原因 (必读):**
{correction_content}

## 修正指令
1. 优先解决反馈中指出的逻辑冲突。
2. **时间修正 (强制)**：补齐空档并消除重叠，保证覆盖 `day_start_time` 至 `day_end_time`。
3. **起始时间 (强制)**：若 `day_start_time` 不是 00:00，活动必须从 `day_start_time` 开始。
4. **起床/入睡边界 (强制)**：第一个活动必须是"起床/醒来"，最后一个活动必须是"入睡/睡眠"，并覆盖到 `day_end_time`。
5. **作息/餐点 (强制)**：严格贴合 Profile 的作息与三餐时间窗口；若有偏差必须在描述中解释原因。
6. **固定事项 (强制)**：Profile 中明确的固定安排必须出现在正确日期/时间段；如被扰动/危机取消，需明确说明原因与替代安排。
7. **房间合法性 (强制)**：main_rooms 必须来自 house_layout；外出活动 main_rooms 为空。
8. **物品可用性 (强制)**：活动描述需明确使用该房间内的家具/设备。
9. **资产清单强制**：只能与 Asset List 中的物品交互，禁止臆造物品。
10. **房间映射强制**：禁止使用不在 house_layout 中的房间；如出现，必须改为有效房间。
11. **扰动/危机体现 (强制)**：当 `simulation_state` 为 Perturbed/Crisis 时，必须在当天活动描述中体现异常事件及其对日程的影响。
12. **实时状态一致性 (强制)**：`agent_state` 若显示疲劳/不适/情绪低落，应调整强度与节奏，并在描述中体现恢复/缓解措施。
"""

SUMMARIZATION_PROMPT_TEMPLATE = """
你是一个基于大模型的高保真人类行为模拟器。请根据以下【居民档案】和【昨日的活动流】数据，生成一份简明扼要、重点突出的"昨日行为总结 (Previous Day Summary)"。

## 总结核心要求

1.  **睡眠质量与时间**：对比居民档案中的 `sleep_schedule`，评估睡眠是否充足（时长是否低于基线 6.5 小时），以及入睡/起床时间是否异常。
    * **关键词**：如果异常，使用"**熬夜**"、"**睡眠不足**"、"**晚起**"等关键词。
2.  **身心状态与生理异常**：
    * 检查是否有**高强度运动**（如：马拉松、力量训练），这可能导致今日疲劳。
    * 检查是否有**社交/情绪异常**（如：朋友聚餐到深夜、争吵、孤独、压力），这可能影响今日的心情和精力。
    * 检查是否有**饮酒**或**非日常药物**的使用。
3.  **突发事件**：如果昨日日程中包含"扰动态"或"危机态"事件，必须明确指出该事件及其对居民造成的**即时影响**（例如：跌倒导致行动不便、设备故障导致工作中断）。
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

# =============================================================================
# Settings 脚本：Profile 生成、户型、审查、详情、交互规则
# =============================================================================

# 角色骰子：头脑风暴 5 个反差大的职业/身份，调用方固定取第 4 个以增加随机性
ROLE_DICE_BRAINSTORM_PROMPT = """
你正在为「高保真家庭生活仿真」生成候选角色。请进行一次头脑风暴，列出 **5 个** 职业/身份。

**要求**:
1. 5 个角色之间**反差极大**（行业、年龄感、生活方式、社交属性等尽量不同）。
2. 尽量避开常见标签（如"程序员"、"教师"），优先**具体、有画面感**的描述。
3. 每个用一句简短中文描述即可，例如："退休的潜艇厨师，现独居海边"、"地下乐队贝斯手，白天在宠物店打工"。
4. 仅输出这 5 条，按顺序编号为 role_1 到 role_5，不要解释。
"""

PROFILE_GENERATOR_PROMPT_TEMPLATE = """
你是一位兼具社会学洞察力与小说家想象力的**人物侧写专家**。请根据指令生成一个用于高保真家庭生活仿真的用户画像（User Profile）。

**输入指令:**
"{seed_instruction}"

**生成核心原则 (拒绝平庸)**:
1. **身份颗粒度**: 
   - 拒绝模糊的标签（如"职员"）。
   - **必须具体**: 例如"在家远程办公的金融分析师，经常需要视频会议"或"刚退休的植物学教授，痴迷于兰花"。
   - **职业影响**: 职业必须体现在 `routines`（作息）和 `values`（价值观）中。

2. **性格的物理投射 (重要)**:
   - **五大性格 (Big Five)**: 请给出精确数值。
   - **特质 (Traits)**: 基于数值生成 3-5 个具体的、**能影响居住环境**的特质。
     - *错误示例*: "善良" (对房子没影响)。
     - *正确示例*: "极简主义" (家里东西少), "囤积症" (储物需求大), "听觉敏感" (需要隔音), "科技发烧友" (设备多)。

3. **生活方式与怪癖 (Routines & Preferences)**:
   - **饮食**: 不要只写"随便"。要具体，如"生酮饮食（需要大量肉类储存）"或"手冲咖啡爱好者（需要特定台面）"。
   - **爱好**: **必须**包含 1-2 个需要**特定物理空间或设备**的爱好。
     - *例子*: 瑜伽（需瑜伽垫）、电竞（需双屏电脑）、烘焙（需烤箱）、撸猫（需猫爬架）。

4. **真实感校验**:
   - 避免完美人格。可以适当加入一些缺点（如"不爱做家务"、"经常熬夜打游戏"），这会让仿真更有趣。

5. **随机事件参数 (Random Event Config)**:
   - 需要提供 `random_event_config`，用于控制扰动态/危机态每日事件数量分布。
   - 参数应基于人物画像与现实生活方式推断（如工作压力、健康状况、社交频率、作息稳定性）。
   - 每个分布包含: mean, std, max；请给出合理、可解释的数值。
   - 参考范围: mean 0.5-2.0, std 0.1-1.0, max 1-5（可在合理范围内微调）。

**语言要求**:
- 所有文本内容请使用**中文**。
- 严格按照 JSON Schema 输出，不要包含任何 Markdown 标记。
"""

PROFILE2LAYOUT_PROMPT_TEMPLATE = """
你是一位精通环境心理学和居住空间设计的资深建筑师。请根据用户画像，设计一个**完全定制化且具备真实生活逻辑**的居住空间快照。

**输入用户画像**: 
{profile_context}

**设计思维链 (Chain of Thought) - 请按此逻辑思考**:
1. **职业场景推导**: 
   - 分析用户的 Occupation。
   - 如果是远程办公/自由职业，**必须**设计独立工作区（书房或专用角落），并配置职业设备（如程序员需 `dual_monitor`, 画师需 `easel` 或 `drawing_tablet`）。
   - 如果是外勤为主，可能只需要简单的笔记本支架。
2. **性格投射 (Big Five)**: 
   - **尽责性(Conscientiousness)**: 高分者家里会有收纳箱 (`organizer_box`)、日程板；低分者桌面可能杂乱。
   - **开放性(Openness)**: 高分者家里可能有乐器 (`guitar`, `piano`)、书墙、奇怪的装饰画。
   - **神经质(Neuroticism)**: 高分者倾向于舒适、私密的空间，如遮光窗帘 (`blackout_curtain`)、加重毯、香薰机。
3. **生活逻辑补全 (Mandatory)**:
   - 严禁生成"样板房"。**必须**包含维持人类生存的基础设施，无论人设如何：
     - 清洁: `washing_machine_001` (洗衣机), `laundry_rack_001` (晾衣架/烘干机), `trash_can_001` (垃圾桶), `broom_001` (扫把)。
     - 收纳: `shoe_cabinet_001` (鞋柜), `wardrobe_001` (衣柜)。
     - 舒适: `curtain_001` (窗帘), `rug_001` (地毯)。
4. **特殊需求响应**:
   - 检查 Preferences 和 Routines。
   - **宠物**: 如果养猫/狗，必须生成 `cat_litter_box`, `cat_tree`, `dog_bed` 等。
   - **运动**: 如果有瑜伽/健身习惯，必须在客厅或阳台生成 `yoga_mat` 或 `treadmill`。

**生成任务**:
生成 rooms 数组，每项为 {{ "room_id": "英文ID", "room_info": {{ 房间详情 }} }}。
- room_id: 如 living_room, master_bedroom, study_room, kitchen, bathroom。根据人设决定房间类型。
- room_info: 包含 room_type, area_sqm, furniture (ID列表), devices (ID列表), environment_state。

**ID命名严格规范**:
- 格式: `物品英文名_数字编号` (例: `gaming_pc_001`, `yoga_mat_001`)。
- **拒绝通用词**: 尽量使用具体名称（如用 `ergonomic_chair_001` 而不是 `chair_001`，如果用户长时间坐着工作）。

**环境状态设定**:
- 根据用户的 `routines` 设定初始状态。例如：如果用户习惯熬夜，卧室的遮光窗帘可能是 `closed`。
- 严格按照 JSON Schema 输出。
"""

LAYOUT_CHECK_PROMPT_TEMPLATE = """
你是一位**具有极强常识推理能力的仿真逻辑审查官 (Simulation Logic Auditor)**。
你的核心任务是进行**逻辑闭环检查**：确保用户画像中的每一个特征、动作和需求，在物理空间中都有对应的物体作为支撑。

**输入上下文**:
1. **用户画像 (Profile)**: 
{profile_context}

2. **当前生成的户型数据 (Draft Layout)**: 
{layout_context}

**审查思维链 (Chain of Thought) - 请遵循以下原则进行广义修正**:

1. **"无对象，不行为"原则 (职业与爱好检查)**:
   - 遍历用户的 `occupation` (职业)、`routines` (日程) 和 `preferences` (爱好)。
   - **核心逻辑**: 如果用户需要执行某个动作，房间里必须有对应的工具。
   - *推理示例*: 
     - 是"音乐家"？-> 必须有乐器（钢琴/吉他/小提琴）。
     - 是"健身教练"？-> 必须有哑铃、深蹲架或跑步机。
     - 爱"喝茶"？-> 必须有茶具套装。
     - 爱"打游戏"？-> 必须有游戏主机或高配PC。
   - **执行**: 发现缺失的工具，立即添加到最合适的房间（如书房、客厅或卧室）。

2. **生命体依存原则 (宠物与特殊住户)**:
   - 检查 Profile 中提及的任何**非人类生命体**（猫、狗、鸟、爬宠等）。
   - **核心逻辑**: 任何生命体都需要"吃、喝、拉、睡"的物理容器。
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
   - 如果"尽责性"极高且有洁癖 -> 确保有 `vacuum_cleaner` (吸尘器) 或 `cleaning_tools`。
   - 如果"神经质"极高 -> 确保卧室有 `blackout_curtain` (遮光窗帘) 或 `soundproofing_panel` (隔音板)。

**输出要求**:
- 输出修正后的 rooms 数组，每项为 {{ "room_id": "英文ID", "room_info": {{ 房间详情 }} }}。
- **ID命名规范**: 使用具体的英文单词 + 编号 (如 `grand_piano_001`, `easel_001`)。
- 仅进行必要的**增量修正**，不要删除原有合理物品。
"""

LAYOUT2DETAILS_ROOM_PROMPT_TEMPLATE = """
你是一位高保真的物联网与交互逻辑设计师。请为房间内的物品生成详细的属性定义。

**输入上下文**:
1. **用户画像**: {profile_context}
2. **当前房间**: {room_id} ({room_type})
3. **待处理物品**: 家具 {furniture_list}, 设备 {device_list}

**生成核心原则 (必须严格遵守)**:
1. **动作闭环 (Action Symmetry)**: 防止仿真逻辑死锁。
   - 任何"进入/占用"类动作，必须配对"退出/释放"类动作。
     - `sit` (坐) -> 必须有 `stand_up` (站起)。
     - `lie_down` (躺) -> 必须有 `get_up` (起床)。
     - `turn_on` (开) -> 必须有 `turn_off` (关)。
     - `open` (开门/盖) -> 必须有 `close` (关门/盖)。
2. **移动能力 (Navigation)**:
   - 如果房间内有地毯、地板或空地，请务必添加 `walk_to` 或 `move_to` 动作，作为移动的锚点。
3. **人设匹配的状态**:
   - 查看用户的 `routines`。如果用户现在应该在睡觉，那么床的 `current_state` 应该是 `occupied: true`。
   - 如果用户很懒（低尽责性），桌子上 (`items_on`) 应该堆满了杂物 (`trash`, `snacks`, `tissues`)。
   - 如果用户是极简主义者，桌子应该是空的。

**环境调节 (environmental_regulation)**:
- 若物品会**改变室内环境**，必须填写 `environmental_regulation` 列表（否则可为空列表）。
- 每项包含: `target_attribute`（temperature / humidity / hygiene）、`delta_per_minute`（每分钟变化量）、`working_condition`（生效条件，如 JSON 对象：power 为 on 且 mode 为 cool 时写作 key-value 即可）。
- 示例: 空调制冷时 target_attribute=temperature, delta_per_minute=-1.0, working_condition 为 power=on 且 mode=cool；加湿器 power=on 时 humidity +0.3/min；扫地机器人 power=on 时 hygiene +0.05/min。
- 家具若有影响（如开窗通风）也可填写；无影响则留空列表。

**输出要求**:
- 为列表中的**每一个** ID 生成配置。
- `support_actions`: 尽可能丰富。例如电视不仅能 `turn_on`，还能 `watch_movie`, `play_game` (如果有游戏机)。
- 严格按照 JSON Schema 输出 items 列表。
"""

DETAILS2INTERACTION_ACTION_PROMPT_TEMPLATE = """
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

必须严格按照 JSON Schema 输出单个对象。
"""
