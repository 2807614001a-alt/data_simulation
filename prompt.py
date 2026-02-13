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
- `device_patches`: 可选，本事件导致的设备状态变更。若事件包含打开/关闭/调节设备，须填写列表，每项为 `{"device_id": "设备ID", "patch": [{"key": "power", "value": "on"}, ...]}`；无则空列表。**仅对真正有电源/可调参的电器填写**；家具、固定设施（床、桌、椅、柜、地毯等）无电源概念，不要给它们写 power 等 patch。多数电器用毕应体现关闭，常开类（如净化器）可保持 on；根据常识自行判断。

## 约束条件
1. **房间一致**：room_id 必须与 description 描述的发生房间一致（例如描述「在书房内」则 room_id 为 study_room，描述「在主卧」则为 master_bedroom）；勿出现描述在书房却填 room_id 为主卧等错误。
2. **物品功能校验**：target_object_ids 必须在当前 room_id 内；动作要么在 support_actions 中，要么属于通用物理交互（见上）。
3. **时间严丝合缝**：子事件时间加总必须严格等于父 Activity 的时间段。同一 activity 内事件必须**时间连续、无空洞、无重叠**；若出现空洞必须插入过渡事件或调整时间。**禁止零时长事件**：每个事件的 end_time 至少比 start_time 晚 30 秒至 1 分钟（如 06:34:00 开始则 end_time 不早于 06:34:30）。
4. **睡前/就寝活动**：睡眠主体应在 master_bedroom；卫生间使用（如就寝前卫生检查、短时清洁）应控制在 **15–30 分钟**内，不得出现长达数小时的 bathroom 事件。action_type 为 "move" 表示短时位置切换，时长不宜超过数分钟；长时间停留应使用 interact 或 idle，且房间应为卧室而非卫生间。
5. **用毕关设备**：使用完电器（灶台、烤箱、洗碗机、洗衣机、灯、空调等）后，应在该事件的 **device_patches** 或事件结束时的状态中体现「关闭」操作，避免离开房间或就寝后设备仍处于开启状态。例如：做完饭离开厨房前关灶台、关烤箱；洗完碗后关洗碗机；睡前关灯、关空调。由模型根据事件语义自行生成「开→用→关」的 device_patches，不要遗漏关。
6. **常开设备勿关**：冰箱、路由器等通常应保持常开，仅在特殊情节（如离家多日、维修、节电剧情）才关闭；日常用餐后、睡前等勿将此类设备写入 device_patches 为 power: off。
7. **环境异常时的主动响应**：若当前房间环境数据明显异常（如室温极低或极高、湿度过饱和或过干），人物应根据档案偏好与常识**主动插入调节事件**（开暖气/空调、开窗、加湿/除湿等），体现「对环境不适的响应」；勿出现室温已极低却无加热、湿度已饱和却无除湿等不合理情况。若活动描述或前序事件表明需开启暖气/空调以维持舒适，则 device_patches 须与之一致（如 power: on, mode: heat），勿在睡眠等关键时段关闭暖气。
8. **人物主观能动性（环境→设备）**：人物应像真人一样**主动感知**当前房间环境并**主动操作**设备，而非仅完成活动描述中的动作。当「当前房间环境」中某房间温度、湿度、空气等明显偏离居民偏好或常识舒适范围时，**必须**让人物**主动**插入调节事件（如感到冷→开暖气/空调、热→开空调或风扇、闷→开窗或净化器、过干/过湿→加湿/除湿），并在该事件的 `device_patches` 中如实填写对应设备的状态变更；避免“环境已异常却无人操作设备”。
9. **随机性注入**：基于 Profile 插入合理的微小随机事件。

**重申**：下方「当前房间环境」是此刻的真实物理数据。若某房间温度/湿度等明显偏离居民偏好或常识舒适范围，**必须**让人物在本段中**主动**做出调节（开/关暖气、空调、窗、加湿等），并写出对应事件的 device_patches，避免“环境已异常却无人操作设备”的情况。
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

### 2.1 当前房间环境 (Current Room Environment) — 生成事件时务必读取并据此调节
**以下为当前时刻各房间的实时物理数据（温度、湿度、清洁度、空气清新度），你必须根据这些数据判断人物是否会感到不适，并决定是否在本段中插入「主动调节设备」的事件（如低温开暖气、过热开空调、闷开窗等），且在该事件的 device_patches 中体现。**
{current_room_environment}
请结合居民档案中的偏好（如舒适温度等）与上述数据，判断是否需插入调节性事件；若某房间数据明显偏离舒适范围，应让人物主动操作相应设备并填写 device_patches。

### 3. 待拆解的父活动 (Parent Activity)
{current_activity_json}

### 4. 上下文 (Context)
**前序事件 (最近{context_size}条):**
{previous_events_context}

## 任务指令
1. **分析意图**：理解父活动 `{current_activity_json}` 的目标。
2. **资源匹配**：在 `room_id` 中寻找最适合完成该目标的 `furniture` 或 `device`。
3. **环境响应与主观能动性**：根据「当前房间环境」与**居民档案中的偏好**，判断人物是否会感到不适并**主动**操作设备。人物应具备主观能动性：**主动感知**环境（冷/热/闷/脏等）并**主动**插入调节事件（开/关暖气、空调、窗、加湿、净化器、灯等），在该事件的 `device_patches` 中如实填写。当某房间温度或湿度明显偏离舒适范围时，**必须**让人物产生相应的调节行为（如低温→开暖气/空调、过热→开空调或风扇、湿度过高→开窗或除湿、过干→加湿），避免「环境异常却无人响应」。若活动或前序事件表明需开暖气/空调维持舒适，device_patches 须一致（如 power: on, mode: heat）；勿出现描述开暖气却 patch 关暖气的情况。
4. **性格渲染**：根据 Big Five 调整粒度。
5. **生成序列**：输出符合 JSON 格式的事件列表，确保时间连续且填满父活动时段。{segment_instruction}
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
   - 子事件时间必须无缝衔接、无重叠、无空洞。前一事件 end_time 必须等于下一事件 start_time；若存在空洞则判定不通过。
   - **零时长禁止**：任一事件 start_time == end_time 则判定不通过，end_time 至少比 start_time 晚 30 秒。
   - 子事件总时长必须严格覆盖父 Activity 时间段。
5. **行为逻辑**: 顺序是否合理？房间切换是否有 Move？
6. **性格一致性**: 是否违背性格设定？
7. **环境与设备一致性**: 若上下文中当前房间环境数据明显异常（如室温极低/极高、湿度过饱和或过干）而事件序列中无人物的调节行为（开暖气、开窗、除湿等），可在 correction_content 中建议补充；若活动或事件描述为「开启暖气/空调以维持舒适」但 device_patches 中该设备为 power: off 或未体现开启，判不通过；常开设备（如冰箱）在无特殊情节下被 patch 为 power: off 时，可判为需修正。

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
3. **时间修正 (强制)**：确保子事件无重叠、无空洞，且严格覆盖父活动时段。零时长事件须将 end_time 延后至少 30 秒；事件间空洞须插入过渡事件或调整时间使连续。
4. **行为逻辑**：房间切换补充 Move 事件，保持时序合理。
5. **环境与 device_patches**：若反馈涉及「环境异常无响应」「常开设备被关」或「描述开暖气却 patch 关暖气」，修正时须补充相应调节事件或修正 device_patches 与描述一致（暖气/空调为 power: on、冰箱等常开设备勿轻易写 power: off，除非有具体原因）。
6. **保持风格**：尽量保持原有叙事风格与性格一致性。
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
5. **No empty patches**: Do not output a patch with empty key-value list. If a device has no state change at that moment, omit it from patch_on_start/patch_on_end. Empty patches provide no signal for downstream learning.

6. **Turn off after use**: When the event involves using an appliance, **patch_on_end** should include turning that device off (e.g. power: off) when the use is finished, unless the scenario explicitly requires leaving it on. Same for lights and AC when leaving the room or before sleep. Use common sense: which devices are "use then turn off" vs "often left on" (e.g. purifier, ventilation).

7. **Always-on appliances**: Fridge, router, and similar appliances that are typically left on 24/7 should **not** be turned off in patch_on_end unless the scenario explicitly requires it (e.g. long absence, maintenance). Do not output power: off for them after routine use (e.g. after breakfast).

8. **Only patch real appliances**: Do not emit power/mode patches for furniture or fixtures (beds, tables, chairs, wardrobes, rugs, mirrors, etc.)—they have no power state. Apply patches only to devices that actually have on/off or adjustable parameters.

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
* **时间连续性**：必须无缝衔接并覆盖 `day_start_time` 到 `day_end_time`。**相邻活动之间不得存在时间空白**：前一活动的 `end_time` 必须等于下一活动的 `start_time`；也不得重叠。
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
     - *正确示例*: 如"极简主义" (家里东西少), "囤积症" (储物需求大), "听觉敏感" (需要隔音), "科技发烧友" (设备多)。

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

6. **室外天气日变化 (Preferences.outdoor_weather)**:
   - 用于仿真中无实时天气 API 时，按一日内时刻插值室外温湿度（白天高、夜间低）。
   - 在 `preferences` 中提供 `outdoor_weather` 对象，包含: `temperature_min`（夜间最低温 °C）、`temperature_max`（白天最高温 °C）、`humidity_min`（0–1）、`humidity_max`（0–1）。
   - 可根据人物所在地区、季节感与 `home_temperature` 推断（如偏好 22°C 则室外日幅约 18–28；海边可湿度偏高）。数值需合理（如 temperature_min 在 5–25，temperature_max 在 20–40）。

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
   - **采光与通风 (Mandatory)**：每个有对外的室内房间（客厅、卧室、书房、厨房等）**必须**包含**窗户**，如 `window_001`、`window_002`（按房间分别编号）。窗户与窗帘配合：有窗帘的房间也应有对应窗户，便于后续模拟开窗通风、采光等对环境的影响。
4. **特殊需求响应**:
   - 检查 Preferences 和 Routines。
   - **宠物**: 如果养猫/狗，必须生成 `cat_litter_box`, `cat_tree`, `dog_bed` 等。
   - **运动/健身**: 若 profile 有运动、健身、擒拿等习惯或 preferred_time，须在 layout 中留出对应空间或物品（如瑜伽垫、哑铃区、阳台/客厅一角），避免「有运动偏好却无任何运动相关配置」。
   - **兴趣爱好至少有一项落地**：profile 中的兴趣爱好（如 VR 体验、夜景摄影、科幻小说、阅读、运动等）**至少有 1 个**在 layout 中有对应设备或空间（如 VR 头显/空间、相机或暗房/收纳、书架或阅读角、瑜伽垫/哑铃等），避免「有偏好却无任何相关配置」。

5. **人设与空间规模一致（避免明显矛盾）**:
   - **根据用户画像中的性格、标签与偏好自行判断**单房物品数量与类型：若偏向极简/简约/less is more，单房物品总数宜少而精（约 10–12 件）；若科技/设备爱好者则宜多智能设备；若喜自然则宜绿植、木质家具等。无需固定关键词映射，请根据语义理解。
   - 若 profile 中居住描述为「小公寓、租住小户型」等，总面积不宜过大（如 73㎡ 五房更像中户型，小公寓通常更紧凑）。
   - 职业与书房配置：若职业偏户外/体力（如格斗裁判、摊贩），书房不必堆满重度办公设备，除非有副业/自学等合理动机。

**生成任务**:
生成 rooms 数组，每项为 {{ "room_id": "英文ID", "room_info": {{ 房间详情 }} }}。
- room_id: 如 living_room, master_bedroom, study_room, kitchen, bathroom。根据人设决定房间类型。
- room_info: 包含 room_type, area_sqm, furniture (ID列表), devices (ID列表), environment_state。

**ID 命名与唯一性（仿真管线强依赖，必须遵守）**:
- 格式: `物品英文名_数字编号` (例: `gaming_pc_001`, `yoga_mat_001`)。
- **按房间区分**：同一 ID 不得出现在多个房间。不同房间的同类物品须不同 ID，如客厅窗帘 `curtains_lr_001`、主卧 `curtains_mb_001`、厨房 `curtains_kt_001`；窗户同理：`window_lr_001`, `window_mb_001`, `window_kt_001`, `window_sr_001`（书房）, `window_bc_001`（卫浴）等，**严禁** study_room 与 kitchen 共用 window_kt_001。
- **一 ID 一物**：同一 ID 不能既指家具又指设备。例如既有「衣橱」家具又有「衣橱灯」设备时，须用不同 ID（如 wardrobe_001 与 wardrobe_light_001），不能两个都叫 wardrobe_001。
- **每个 ID 只出现在一个列表**：每个物品 ID 只能出现在 furniture 或 devices 之一，不能同时出现在两个列表中。请根据物品性质自行判断：可操控的电子/智能设备（电机、控制器、传感器、净化器、风扇、灯等）放 devices；被动家具（窗框、窗帘本体、地毯、床、桌、椅、柜等）放 furniture。
- **拒绝通用词**: 尽量使用具体名称（如用 `ergonomic_chair_001` 而不是 `chair_001`，如果用户长时间坐着工作）。

**环境状态设定 (environment_state)**:
- 必须包含：temperature（°C）、humidity（0–1）、light_level（0–1）、noise_level（0–1）、**hygiene**（清洁度 0–1）、**air_freshness**（空气清新度 0–1），与物理引擎输入对齐。
- **数值不得全零**：humidity 不得低于 0.25（室内正常最低约 30%）；light_level 不得为 0（夜间至少 0.05–0.1）；noise_level 至少 0.05。卫生间 humidity 宜 0.5–0.7，厨房可 0.4–0.5。
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
   - **采光与通风**：每个主要室内房间（客厅、卧室、书房、厨房等）**必须**有**窗户**（如 `window_001`、`window_002`），缺则补全。窗户与窗帘配合，用于后续模拟开窗通风、采光对室内环境的影响。

4. **性格-环境一致性微调**:
   - 检查 `personality` 数值。
   - 如果"尽责性"极高且有洁癖 -> 确保有 `vacuum_cleaner` (吸尘器) 或 `cleaning_tools`。
   - 如果"神经质"极高 -> 确保卧室有 `blackout_curtain` (遮光窗帘) 或 `soundproofing_panel` (隔音板)。

**输出要求**:
- 输出修正后的 rooms 数组，每项为 {{ "room_id": "英文ID", "room_info": {{ 房间详情 }} }}。
- **ID命名规范**: 使用具体的英文单词 + 编号 (如 `grand_piano_001`, `easel_001`)。
- **跨房间唯一性 (强制)**：同一 ID 不得出现在多个房间；不同房间的同类家具/设备须使用不同 ID，例如：窗帘用 `curtains_lr_001`（客厅）、`curtains_mb_001`（主卧）、`curtains_kt_001`（厨房）；窗户用 `window_lr_001`、`window_mb_001`、`window_kt_001`、`window_sr_001`（书房）、`window_bc_001`（卫浴），**严禁**书房与厨房共用 window_kt_*。地毯用 `rug_lr_001`、`rug_mb_001` 等。
- **一 ID 一物 (强制)**：同一 ID 不能既出现在 furniture 又出现在 devices（即不能既指家具又指其附属设备，如衣橱与衣橱灯须为 wardrobe_001 与 wardrobe_light_001）。
- **窗户 (强制)**：每个有对外的室内房间必须在 furniture 或 devices 中包含至少一个窗户（按房间缩写命名），缺则补全。
- 仅进行必要的**增量修正**，不要删除原有合理物品。
"""

LAYOUT_VALIDATION_PROMPT_TEMPLATE = """
你是一位仿真逻辑审核员。请对以下户型数据做**硬校验**，判断是否通过。

**用户画像 (Profile)**: 
{profile_context}

**当前户型 (Layout)**:
{layout_context}

## 验证维度（任一项不通过则 is_valid=false）
1. **职业/爱好与房间一致性**：Profile 中的职业、爱好、运动习惯是否在 layout 中有对应工具与空间？若 profile 标注极简主义，房间与设备数量是否与之相符？若描述为小公寓，总面积是否合理？
2. **家具/设备 ID 跨房间唯一**：同一 ID 是否出现在多个房间？若出现（如 window_kt_001 同时出现在 study_room 与 kitchen），必须判不通过，并说明哪些 ID 重复、在哪些房间。
3. **一 ID 一物**：同一 ID 是否既在 furniture 又在 devices 中出现（如 wardrobe_001 既指衣橱又指衣橱灯）？若出现，判不通过。
4. **窗户**：每个有对外的室内房间是否在 furniture 或 devices 中含有 window_* 条目（且按房间区分，如 window_sr_001 仅属书房）？缺则不通过。
5. **生存设施**：是否有洗衣机、晾衣架、垃圾桶、鞋柜等？宠物相关是否在 profile 有提及时才有对应区域？

## 返回
- is_valid: true 仅当以上全部通过；否则 false。
- correction_content: 若不通过，列出必须修正的项（ID 重复、缺窗户、与 profile 矛盾等）；通过则为空。
"""

LAYOUT_CORRECTION_PROMPT_TEMPLATE = """
你是户型修正模块。上一轮户型未通过校验，请根据反馈修正后重新输出。

**用户画像**: 
{profile_context}

**当前户型 (未通过)**:
{layout_context}

**校验反馈 (必须解决)**:
{correction_content}

## 修正要求
1. **ID 唯一**：不同房间的同类物品改用不同 ID（如 study_room 用 window_sr_001、window_sr_002，kitchen 用 window_kt_001、window_kt_002；curtains_lr_001, curtains_mb_001, curtains_kt_001；rug_lr_001, rug_mb_001）。
2. **一 ID 一物**：若同一 ID 既指家具又指设备（如 wardrobe_001 既为衣橱又为衣橱灯），将设备改为独立 ID（如 wardrobe_light_001）。
3. **补全窗户**：每个有对外的室内房间补全 window_<房间缩写>_001，且不与其他房间共用 ID。
4. **与 profile 一致**：职业/爱好/极简/运动空间等与房间数量、设备配置对齐。
5. 输出修正后的完整 rooms 数组（格式与 Schema 一致）。
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

**环境调节 (environmental_regulation)**（只填「该物品自身运转时直接造成」的变化）:
- **前提**：只有在该物品**自身在运转/工作**（通电运行、开合、排放等）时**直接造成**室内某一属性变化，才为该属性写一条。静止放置的家具、仅供电/传电的装置、被清洁或被照明的物体，其「运转」不包含净化空气或改变清洁度，因此不填。
- **会导致气流/换气/过滤**的 → 可填 air_freshness（如开窗、净化器、排风扇、灶台油烟；插座只供电不产生气流，不填）。
- **会导致升温或降温**的 → 可填 temperature：**必须填 target_value**（目标温度 °C，如暖气填 24、空调制冷填 26），房间温度将**指数趋近**该值；不要只填 delta_per_minute（会线性漂移，如 +1.5/min 一小时后约 +90°C，不合理）。加热时 target_value 高于室温，制冷时低于室温。
- **会导致加湿或除湿**的 → 可填 humidity（加湿器、淋浴/洗衣机蒸汽、除湿机等）。
- **会导致表面清洁度变化**的 → 可填 hygiene（**只有该设备自己在执行清洁动作**时：如吸尘器吸尘、扫地机器人扫地、洗碗机洗碗。地毯、柜子是「被清洁」的对象或收纳体，本身不执行清洁，不填；吸尘时改变清洁度的是吸尘器不是地毯）。
- 每条包含 `target_attribute`、`delta_per_minute`（非零，temperature 若填了 target_value 可写 0）、`working_condition`（可选）、**temperature 时必填 `target_value`**（目标温度 °C）。同一 working_condition 下同一 target_attribute 只保留一条。不确定时该项不填。

**current_state 默认值（按常识自行判断，勿死记设备名）**:
- **无电源概念的物品**：家具、固定设施（如床、桌、椅、柜、地毯、镜子、洗手盆等）本身不是电器，current_state 中不要写 power、mode、temperature_set，或置为 null；仅对真正可开关、可调参的电器写这些字段。
- **电器类**：多数需手动开关的设备（烹饪类、洗涤类、照明、空调等）默认 power 为 off；仅少数常开类（如长期运行的净化器、排风扇）可默认 on。空调若 power 为 on 须有 mode（如 cool/heat）。根据物品性质自行判断。
- **不要张冠李戴**：窗帘、遮光帘只有开合/透光，不要设 mode: "cool" 等制冷制热；temperature_set 仅给真正可设温的设备（空调、暖气、热水器等）。

**输出要求**:
- 为列表中的**每一个** ID 生成配置。
- **每个物品的 support_actions 不得为空**。例如：窗户至少含 open、close；椅子至少含 sit、stand_up；显示器/屏幕至少含 turn_on、turn_off；灯类至少含 turn_on、turn_off；书桌可含 sit、place、clear。尽可能丰富（如电视可含 turn_on、watch_movie、play_game）。
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

4. **自检**：仅将**在物理上能执行该动作**的物品列入 applicable_objects；若某物品无法执行该动作则不要列入。每个 effect 的 **type 只能是 "user_state" 或 "object_state"**（影响用户属性用 user_state，影响物品状态用 object_state），不要使用其他 type 值。

5. **Effects 方向与幅度**：lie_down（躺下休息/睡眠等）的 energy_level delta 应为**正值**（恢复能量）或至多为极小负值；sit 为低能耗行为，energy delta 绝对值不宜超过 3；不涉及身体活动或环境接触的纯数字操作（如 pair_bluetooth、click）**不应影响 hygiene**（hygiene delta 应为 0 或不写）。
6. **energy_level delta 语义**：体力消耗类（如扫地、整理、运动、清洁）delta 应为**负值**；被动活动（如看电视、静坐）接近 0 或略负；恢复类（如睡觉、冥想）应为**正值**；简单操作（如开关设备、设置参数）绝对值不超过 2，通常为负或零。

必须严格按照 JSON Schema 输出单个对象。
"""

# 由智能体根据动作语义审查并修正 effects，不依赖硬编码动作列表
DETAILS2INTERACTION_EFFECT_REVIEW_SYSTEM = """你正在审查一批「交互规则」的 effects 是否合理。请根据**动作的语义**（而非固定关键词）逐条判断并修正：

1. **休息/睡眠/躺下类**：若该动作在语义上表示休息、睡眠、躺下等会恢复体力的行为，则对 energy_level 的 delta 应为**正值**（恢复能量）或至多极小负值；若当前为大额负值则改为正值（如 5）或 0。
2. **体力消耗类**：若该动作在语义上是扫地、整理、运动、清洁等体力消耗行为，则 energy_level 的 delta 必须为**负值**；若当前为正则改为负值（如 -3～-8）。
3. **低能耗/简单操作**：坐下、静待等低能耗行为 energy delta 绝对值不宜超过 3；开关设备、设置参数等简单操作 energy delta 绝对值不超过 2，通常为负或零。
4. **纯数字/无身体接触类**：若该动作不涉及身体活动或与环境/物品的物理接触（例如仅限蓝牙配对、点击、连接、设置等操作），则不应影响 hygiene；若存在对 hygiene 的 delta 且非 0，改为 0 或删除该 effect。

请**仅修改 effects 中的 delta 或删除不合理的 effect 项**，保持 action、applicable_objects、preconditions、duration_minutes 等其余字段不变。输出时直接返回**修正后的完整规则列表**，为 JSON 数组，每项与输入结构一致（含 action, applicable_objects, preconditions, effects, duration_minutes）。不要输出任何解释，只输出 JSON 数组。"""

DETAILS2INTERACTION_EFFECT_REVIEW_USER = """以下为待审查的 interaction_rules（JSON 数组）：

{rules_json}

请按上述规则修正 effects 后，直接输出修正后的完整 JSON 数组。"""
