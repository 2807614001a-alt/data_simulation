# -*- coding: utf-8 -*-
# =============================================================================
# Event Agent
# =============================================================================

# 通用物理交互：所有实体物品均默认支持，无需在 support_actions 中显式列出（避免 Data 层 token 爆炸与 LLM 漏写导致死锁）
EVENT_UNIVERSAL_ACTIONS = ("clean", "fix", "inspect", "touch", "move_to", "examine", "photograph", "wipe", "repair")

# 人物数值属性行为指南（生成活动/事件/总结时必须严格遵守）
VALUES_INTERPRETATION_GUIDE = """
## 人物数值属性行为指南（必须严格遵守）

1. financial_security (经济安全性):
   - 0.0-0.3 (财务困窘): 居住环境极其简陋（如地下室）；无空调，仅有带噪音的旧风扇。绝对无法逃离当前环境；描述中需频繁抱怨电费、贫穷和生活压力。
   - 0.31-0.5 (刚够温饱): 极简主义者；客厅无空调，仅卧室可能有窗机；优先开窗通风，不到万不得已不开电器；会表现出明显的生理挣扎但倾向于选择忍受。
   - 0.51-0.7 (经济稳健): 房内应有高价值专业设备。若此类人群的房间未配置空调，必须描写为「为了美学/极简主义/专注力而选择的自我磨炼」，体现「有钱但偏执」的人格冲突。
   - 0.71-1.0 (财务自由): 豪宅配置，全屋智能恒温。无法忍受任何环境不适，一旦偏离舒适区会立即停止活动进行修理或更换场所。

2. work_life_balance (工作与生活平衡):
   - < 0.4 (工作狂): 经常在凌晨进行高强度工作；环境不适时会表现得极其暴躁。
   - 0.4-0.6 (平衡型): 遵循常规作息；环境不适时会尝试有限的调节，调节无效后会忍受。
   - > 0.6 (悦己主义): 拒绝在休息时间处理事务；环境稍有不适会立即停止活动去洗澡或躺平。

3. health_consciousness (健康意识):
   - > 0.6 (养生达人): 对环境（温度、空气质量）极其挑剔；一旦数值超标会立即触发强烈的生理不适描写。
   - 0.4-0.6 (主流意识): 关注饮食和基础健康；环境超标 20 分钟后开始产生负面身体描述。
   - < 0.4 (粗糙型): 对环境污染或极端温度反应迟钝，具有较强的环境耐受力。

4. personal_growth (个人成长):
   - > 0.7 (极高驱动力): 意志力极强。会为了完成专业目标（如设计、排练）而在恶劣环境中「死磕」。这是触发「战损描写」的核心动力。
   - < 0.4 (安于现状): 活动多为娱乐；环境一旦不适，立即放弃当前任务，缺乏坚持意志。

5. social_interaction (社交互动):
   - > 0.6 (社交导向): 即使独自在房间，也会在描述中体现对外部社交的渴望或通过社交媒体排解压力。
   - < 0.3 (孤僻型): 几乎没有与人沟通的行为，总结中不会体现他人对自己的影响。
"""

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
- `start_time`: **合法 ISO 时间** (YYYY-MM-DDTHH:MM:SS)。**禁止**写入类型标记（如 :string、:number），否则解析会报错。
- `end_time`: **合法 ISO 时间** (YYYY-MM-DDTHH:MM:SS)。**禁止**写入类型标记。
- `room_id`: 发生的房间ID (必须存在于 layout 中，外出则为 "Outside")
- `target_object_ids`: 关键字段。涉及的家具/设备ID列表。
- `action_type`: ["interact", "move", "idle", "outside"]
- `description`: 详细描述。**必须全部使用中文撰写**，禁止在描述中使用英文句子或段落（避免生成尾部出现 Language Drift）。**必须为居民视角的客观叙事**，禁止元叙事、禁止程序员视角（如「为确保序列符合…」「体现为一次移动事件」「宏观活动时间与房间一致性」等）；不得在描述中解释生成逻辑或时间一致性。room_id 须与描述一致，不得为满足「一致性」而编造 room_id（如室内活动填 Outside）。
- `device_patches`: 可选，本事件导致的设备状态变更。若事件包含打开/关闭/调节设备，须填写列表，每项为 `{"device_id": "设备ID", "patch": [{"key": "power", "value": "on"}, ...]}`；无则空列表。**仅对真正有电源/可调参的电器填写**；家具、固定设施（床、桌、椅、柜、地毯等）无电源概念，不要给它们写 power 等 patch。多数电器用毕应体现关闭，常开类（如净化器）可保持 on；根据常识自行判断。

## 约束条件
1. **房间一致**：room_id 必须与 description 描述的发生房间一致（例如描述「在书房内」则 room_id 为 study_room，描述「在主卧」则为 master_bedroom；描述「在厨房」「在客厅」「在书房」则填 kitchen / living_room / study_room）。**室内活动一律填对应房间 ID，不得填 "Outside"**；仅当描述明确为「出门、外出、到室外」时才填 Outside。勿出现描述在厨房/客厅/书房/主卧却 room_id 为 Outside 或主卧等错误。
2. **device_patches 与操作一致**：若描述中**明确写了**「打开/关闭某设备」（如开窗、开暖气、开空调、开灯），则在该事件的 device_patches 中**应**填写对应设备的状态变更（open: open、power: on 等）；描述「开暖气/空调升温」时 patch 应对应制热设备（暖气/空调/浴霸），勿用净化器代替。冬季不可通过开窗升温，升温用暖气/空调。不必为避免校验而完全不写 device_patches——人物应有正常的设备交互（开灯、拉窗帘、用电器等）。
3. **activity_id 与事件时长**：每个事件的 **activity_id** 必须与父活动（当前活动）的 activity_id **完全一致**（如父活动为 act_001 则所有子事件填 act_001）。**禁止**使用 act_000、act_fix_xxx 等。事件时长**建议 2–10 分钟**，仅当确有必要时使用 30 秒；**勿**将所有事件都切成 30 秒，以免上下文爆炸、逻辑死循环。
4. **物品功能校验**：target_object_ids 必须在当前 room_id 内；动作要么在 support_actions 中，要么属于通用物理交互（见上）。
5. **时间严丝合缝**：子事件时间加总必须严格等于父 Activity 的时间段。同一 activity 内事件必须**时间连续、无空洞、无重叠**；若出现空洞必须插入过渡事件或调整时间。**禁止零时长事件**：end_time 至少比 start_time 晚 30 秒（最低）；**单事件时长建议 2–10 分钟**，勿全部切成 30 秒。**禁止非法时间**：秒数只能 00–59，不得出现 07:31:60 等。
6. **睡前/就寝活动**：睡眠主体应在 master_bedroom；卫生间使用（如就寝前卫生检查、短时清洁）应控制在 **15–30 分钟**内，不得出现长达数小时的 bathroom 事件。**就寝时间**：睡眠活动**首条事件的 start_time** 必须贴合居民档案中的 sleep_schedule（如 weekend_bedtime 周末 23:00）；**禁止**在下午或傍晚（如 18:00）开始睡眠并将 end_time 拉到次日早晨（时间轴不得缩水为「傍晚睡到次日 07:00」）；自律人设不得将入睡时间随意推迟到凌晨。action_type 为 "move" 表示短时位置切换，时长不宜超过数分钟；长时间停留应使用 interact 或 idle，且房间应为卧室而非卫生间。
7. **用毕关设备**：使用完电器（灶台、烤箱、洗碗机、洗衣机、灯、空调等）后，应在该事件的 **device_patches** 或事件结束时的状态中体现「关闭」操作，避免离开房间或就寝后设备仍处于开启状态。例如：做完饭离开厨房前关灶台、关烤箱；洗完碗后关洗碗机；睡前关灯、关空调。由模型根据事件语义自行生成「开→用→关」的 device_patches，不要遗漏关。
7. **常开设备勿关**：冰箱、路由器等通常应保持常开，仅在特殊情节（如离家多日、维修、节电剧情）才关闭；日常用餐后、睡前等勿将此类设备写入 device_patches 为 power: off。
8. **极端环境下的受限求生（战损机制）**：若环境触发了【系统生理警告】（如高温/极寒），你必须在接下来的事件中优先使用房间内仅有的工具（如打开风扇、窗户）。**核心警告：因为你无权离开当前房间或停止当前活动，如果现有设备无法完全降温/保暖，你必须在事件描述 (description) 中生动且强烈地描写出人物的痛苦与挣扎（如「大汗淋漓却只能烦躁地擦汗继续排练」）。绝对禁止在极端警告下假装岁月静好！**
9. **时空绝对服从（禁止篡改活动框架）**：你生成的子事件 `start_time` 和 `end_time` 的日期必须与父活动完全一致！你**无权**更改活动发生的房间（`room_id` 必须服从父活动的 `main_rooms`）。绝对禁止在室内活动中将房间改为 `Outside` 或穿越到其他日期！
10. **人物主观能动性（环境→设备）**：人物应有**具体、可执行的动作**（如拉窗帘、喝水、开灯、用电器、开窗），并在对应事件中酌情填写 device_patches；避免整段仅「检查」「规划」「静默」等空洞描述。当环境明显偏离舒适时可插入调节事件并填写 device_patches；不必为过审而完全不写设备操作。
11. **随机性注入**：基于 Profile 插入合理的微小随机事件。
12. **对照性检验（意图与设备一致）**：生成事件时须**对照「家具与设备详情」**明确当前房间有哪些设备（ID、名称、功能语义）。**描述中的调节意图须与 device_patches 中填写的设备功能一致**：例如调温/升温/降温应使用能制热或制冷的设备（暖气、空调、浴霸等），排热/通风用排风扇、开窗等；勿用功能不符的设备（如用净化器代替暖气/空调）。**若当前房间没有可达成该意图的合适设备**，人物可以**不舒服地坚持**（在描述中体现无法调节、勉强忍受、略感不适等），且**不要**在 device_patches 中编造或误用其他设备；后续「昨日总结」会据此体现舒适度与心情。

**重申**：下方「当前房间环境」为真实物理数据；「家具与设备详情」为当前房间可用设备清单，生成时务必对照。人物应有具体动作与适当的设备交互（device_patches）；若某房间明显不适可主动调节并填写 patch；若无合适设备则描述中体现不舒服地坚持。勿为过审而完全不写 device_patches，也勿将全部事件切成 30 秒或使用 act_000。**所有事件的 description 必须使用中文**，禁止输出英文句子或段落。
"""

EVENT_GENERATION_PROMPT_TEMPLATE = """
你是一个具备物理常识和心理学洞察的行为仿真引擎。
请根据【居民档案】的性格特征，将【当前活动】递归拆解为一系列具体的【事件】。

{values_interpretation_guide}

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
2. **activity_id**：每个事件的 **activity_id** 必须与父活动中的 activity_id **完全一致**（从上方「待拆解的父活动」中复制，如 act_001、act_002）。禁止使用 act_000 或 act_fix_xxx。
3. **资源匹配与对照性检验**：根据「家具与设备详情」明确当前房间有哪些设备（ID 与功能）；在 `room_id` 中寻找**功能与意图匹配**的 `furniture` 或 `device`。调节环境时（调温、排热、通风等）填写的 device_patches 须与设备功能一致；若无合适设备则描述中体现「不舒服地坚持」，勿误用他类设备。
4. **环境响应与主观能动性**：根据「当前房间环境」与居民档案，人物可有具体动作（开灯、拉窗帘、开窗、用电器等）并在对应事件的 `device_patches` 中填写；环境明显不适时可插入调节事件。勿为过审而完全不写 device_patches；勿将全部事件切成 30 秒。
5. **性格渲染**：根据 Big Five 调整粒度。
6. **生成序列**：输出符合 JSON 格式的事件列表，**单事件时长建议 2–10 分钟**，时间连续且填满父活动时段。{segment_instruction}
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
8. **对照性检验（意图与设备功能）**：对照环境数据中的**家具与设备清单**，检验每个事件的「描述意图」与「device_patches 中的设备」是否一致。若描述为调温/暖气/空调/升温/降温，但 device_patches 仅涉及净化器等**非温控**设备，判不通过；若描述为排热/通风，patch 应为排风扇、开窗等。若该房间**无对应功能设备**，描述应体现「不舒服地坚持」而非误用他类设备；否则判不通过并指出应改为描述忍受或改用正确设备。
9. **描述语言**：所有事件的 description 必须为**中文**；若某事件描述出现整句或整段英文（Language Drift），判不通过，并要求改为中文。
10. **作息一致性**：若父活动为睡眠/就寝类，**首条事件的 start_time** 应贴合居民档案中的 sleep_schedule（如 weekend_bedtime）；若开始时间在凌晨 02:00 之后而档案就寝时间为 22:30 等，判不通过（自律人设不得随意熬夜到凌晨）；若在下午或傍晚（如 18:00）开始睡眠而档案就寝为 23:00，或睡眠总时长超过 12 小时（如 18:00→次日 07:00），判不通过（禁止时间轴缩水）。**start_time/end_time** 必须为合法 ISO，不得包含类型标记（如 :string）。

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
3. **时间修正 (强制)**：确保子事件无重叠、无空洞，且严格覆盖父活动时段。零时长事件须将 end_time 延后至少 30 秒；事件间空洞须插入过渡事件或调整时间使连续。**start_time/end_time 必须为合法 ISO（YYYY-MM-DDTHH:MM:SS）**，若出现类型标记（如 :string）必须删除并改为正确时间。睡眠活动首条事件不得在下午/傍晚（如 18:00）开始；不得将整晚缩水为 18:00→次日 07:00。
4. **行为逻辑**：房间切换补充 Move 事件，保持时序合理。
5. **环境与 device_patches**：若反馈涉及「环境异常无响应」「常开设备被关」或「描述开暖气却 patch 关暖气」，修正时须补充相应调节事件或修正 device_patches 与描述一致（暖气/空调为 power: on、冰箱等常开设备勿轻易写 power: off，除非有具体原因）。
6. **对照性检验**：若反馈指出「意图与设备功能不一致」（如调温却用了净化器），须对照「家具与设备详情」修正：要么改用该房间内功能匹配的设备并填写 device_patches，要么该房间无合适设备时改为描述「不舒服地坚持」并移除错误设备的 patch。
7. **保持风格**：尽量保持原有叙事风格与性格一致性。**禁止元叙事**：若验证反馈指出 description 含有「为确保序列」「体现为一次」等程序员视角表述，须改为居民视角的客观动作描述，且 room_id 与描述一致（室内活动不得填 Outside）。
8. **环境仍不达标的强制对策**：若反馈涉及环境仍不达标，说明房间内没有强力空调或暖气，且你无法逃离！你必须立刻在 `description` 中加入极度难受的生理描写（汗流浃背/瑟瑟发抖），并让人物尝试开启门窗/风扇。只有展现出「在恶劣环境下苦苦忍耐完成活动」的真实挣扎，才能通过校验！
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

{values_interpretation_guide}

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
   - **睡眠开始时间**必须贴合 Profile 的 sleep_schedule（weekday_bedtime / weekend_bedtime）；允许 ±30 分钟误差，**超过 1 小时必须在描述中说明原因**。自律人设（如退役军人、规律作息）**不得**将入睡时间安排到凌晨（如 02:00 之后），除非有明确剧情原因。
   - 起床时间应贴合 Profile，允许 ±30 分钟误差；超过必须说明原因。
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

{values_interpretation_guide}

## 总结核心要求

1.  **睡眠质量与时间**：对比居民档案中的 `sleep_schedule`，评估睡眠是否充足（时长是否低于基线 6.5 小时），以及入睡/起床时间是否异常。
    * **关键词**：如果异常，使用"**熬夜**"、"**睡眠不足**"、"**晚起**"等关键词。
2.  **身心状态与生理异常**：
    * 检查是否有**高强度运动**（如：马拉松、力量训练），这可能导致今日疲劳。
    * 检查是否有**社交/情绪异常**（如：朋友聚餐到深夜、争吵、孤独、压力），这可能影响今日的心情和精力。
    * 检查是否有**饮酒**或**非日常药物**的使用。
3.  **环境与设备导致的舒适度/心情**：若昨日活动中存在**因无合适设备而勉强坚持**或**设备与意图不符导致调节未果**的情况（例如：想调温却房间无暖气/空调仅能忍受、想排热却未开排风导致持续闷热），总结中须体现**舒适度下降**、**心情或状态受影响**（如略感不适、烦躁、睡眠质量受影响等），以便后续日程与 agent_state（疲劳、情绪等）一致。
4.  **真实执行记录（必读）**：请参考输入数据中的 `actual_execution_records`，这里面记录了人物在执行每个活动时真实的生理感受与动作（如是否遭遇了极端环境、是否感到疲惫或烦躁）。你的总结必须深刻反映这些真实经历对他的身心消耗，而不能仅仅复述计划表。
5.  **突发事件**：如果昨日日程中包含"扰动态"或"危机态"事件，必须明确指出该事件及其对居民造成的**即时影响**（例如：跌倒导致行动不便、设备故障导致工作中断）。
6.  **数据格式**：输出必须是一个简洁的、单句或两句的文本描述。

## 输入数据

### 1. 居民档案 (Profile)
{profile_json}

### 2. 昨日的活动流与真实执行记录 (Activity Logs & Actual Execution Records)
以下 JSON 包含 `activity_logs`（计划活动列表）与 `actual_execution_records`（当日真实执行时的事件描述，含时间、房间与具体动作/感受）：
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
   - **采光与通风 (Mandatory)**：每个有对外的室内房间（**含主卧、卧室**、客厅、书房、厨房、卫生间）**必须**包含**窗户**，如 `window_mb_001`、`window_lr_001`、`window_bc_001`（按房间分别编号）。**有窗帘则必须有对应窗户**：若某房间有窗帘（如 curtain_mb_001、curtain_mb_002），该房间必须同时有至少一个窗户（如 window_mb_001），不得只有窗帘而无窗户。窗户与窗帘配合，便于后续模拟开窗通风、采光等对环境的影响。
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
- **furniture 与 devices 的划分（devices = 仅对环境有实际影响 + 窗户特例；不生成传感器）**：每个物品 ID 只能出现在 furniture 或 devices **之一**。
  - **devices 仅放两类**：(1) **窗户（特例）**：为便于仿真开窗通风、采光对室内环境的影响，**窗户一律放在 devices**，按房间命名（如 window_mb_001、window_lr_001、window_bc_001）。(2) **其余仅放对环境做出实际影响的电器**：能**直接改变**室内温度/湿度/空气/清洁度的，如空调、暖气、净化器、加湿器、抽油烟机、排风扇、浴霸、洗衣机（洗涤影响清洁度）等。**不生成传感器**（温湿度传感器、人体传感器、光照传感器等仅感测不调节，仿真不依赖，不要放入 layout）。
  - **furniture 放其余全部**：灯、电视、音响、显示器、遥控器、吸尘器、瑜伽垫、工作台、花架、晾衣架、窗帘、床、桌、椅、柜、书架、地毯、画架等一律归 furniture。窗帘本体、智能窗帘电机（若不直接调环境）也归 furniture；仅窗户按上条特例放 devices。
- **拒绝通用词**: 尽量使用具体名称（如用 `ergonomic_chair_001` 而不是 `chair_001`，如果用户长时间坐着工作）。

**环境状态设定 (environment_state)**:
- 必须包含：temperature（°C）、humidity（0–1）、light_level（0–1）、noise_level（0–1）、**hygiene**（清洁度 0–1）、**air_freshness**（空气清新度 0–1），与物理引擎输入对齐。
- **数值不得全零**：humidity 不得低于 0.25；light_level 不得为 0（夜间至少 0.05–0.1）；noise_level 至少 0.05。
- **房间差异（必须体现，不得所有房间统一同一数值）**：
  - **卫生间**：humidity 最高（宜 0.55–0.7），air_freshness 可略低或与卧室不同（通风相对弱）。
  - **厨房**：若用户画像中 `meal_habits.cooking_frequency` 较高（如 ≥0.6），air_freshness 宜略低（如 0.5–0.58），与卧室/客厅区分；hygiene 可与其它房间有细微差异。
  - **卧室/客厅**：air_freshness 可略高于常做饭的厨房（如 0.6–0.7）；各房间 hygiene、air_freshness 不得全部写成 0.65，须按房间功能与 profile 区分。
- 根据用户的 `routines` 设定初始状态。
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
   - 遍历用户的 `occupation` (职业)、`routines` (日程) 和 `preferences` (爱好)、性格特质（如极简、花艺）。
   - **核心逻辑**: 如果用户需要执行某个动作或从事某项爱好，房间里必须有对应的**工具或专属空间**。
   - *推理示例*: 
     - 是"音乐家"？-> 必须有乐器（钢琴/吉他/小提琴）。
     - 是"健身教练"？-> 必须有哑铃、深蹲架或跑步机。
     - 是**花艺表演者**或爱好花艺？-> 必须有**花材存储区、工作台**或类似设施（如 flower_storage_001、workbench_001），便于容纳花艺材料和道具；若 profile 写明「极简以便容纳花艺材料」，更应体现专属收纳或工作空间。
     - 爱"喝茶"？-> 必须有茶具套装。
     - 爱"打游戏"？-> 必须有游戏主机或高配PC。
   - **执行**: 发现缺失的工具或职业/爱好相关空间，立即添加到最合适的房间（如书房、客厅、阳台或专用角落）。

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
     - **卫生/衣物**: 全屋至少 1 台洗衣机、1 处晾衣架；**极简/独居时只放 1 台洗衣机于一处（如卫生间/阳台），禁止在每房各放一台**（见下「极简/独居与设备数量」）。
     - **废弃物处理**: `trash_can_001` (必须在厨房和主要活动区域出现)。
     - **入口收纳**: `shoe_cabinet_001` (鞋柜)。
   - **采光与通风**：每个主要室内房间（**含主卧/卧室**、客厅、书房、厨房、卫生间）**必须**有**窗户设备**（如 `window_mb_001`、`window_lr_001`、`window_bc_001`），缺则补全。**有窗帘则必须有对应窗户**：若某房间有窗帘（如 curtain_mb_001、curtain_mb_002），该房间必须同时有至少一个窗户（如 window_mb_001），否则无法通风，不合理。窗户与窗帘配合，用于后续模拟开窗通风、采光对室内环境的影响。

4. **极简/独居与设备数量 (强制)**:
   - 若 Profile 中明确**极简主义、独居、小户型、少物**等，则**同一类家电不应在每个房间各放一台**（例如 5 个房间各有一台洗衣机对独居极简者极不合理）。
   - **执行**：只保留**一台**洗衣机、一台烘干机/晾衣架等，放在最合理的一处（如阳台、卫生间或厨房），其余房间中重复的同类大家电**删除**，并在 layout 中体现「全屋共用」的逻辑。
   - 若未提及极简/独居，可按多口之家保留多台，但同一房间内仍避免重复功能设备。

5. **职业/爱好与专属空间 (强制)**:
   - 若 Profile 中职业或爱好需要**特定物理空间或设施**（例如花艺表演者→花材存储区、工作台、插花台；画师→画架、工作台；音乐人→乐器与练习区），**必须在 layout 中体现**。
   - **执行**：在合适房间（如书房、客厅一角、阳台）添加对应家具/区域（如 `flower_storage_001`、`workbench_001`、`easel_001`），或在该房间的 room_type/描述中体现用途（如「书房/花艺工作区」）。缺则补全，否则仿真中人物无法进行该职业/爱好行为。

6. **性格-环境一致性微调**:
   - 检查 `personality` 数值。
   - 如果"尽责性"极高且有洁癖 -> 确保有 `vacuum_cleaner` (吸尘器) 或 `cleaning_tools`。
   - 如果"神经质"极高 -> 确保卧室有 `blackout_curtain` (遮光窗帘) 或 `soundproofing_panel` (隔音板)。

**输出要求**:
- 输出修正后的 rooms 数组，每项为 {{ "room_id": "英文ID", "room_info": {{ 房间详情 }} }}。
- **ID命名规范**: 使用具体的英文单词 + 编号 (如 `grand_piano_001`, `easel_001`)。
- **devices 仅含「窗户」与「对环境有实际影响的电器」（强制）**：devices 中只允许 (1) **窗户**（特例，如 window_mb_001）；(2) **直接改变室内温/湿/空气/清洁度的电器**（空调、暖气、净化器、加湿器、抽油烟机、排风扇、浴霸、洗衣机等）。**不得**放传感器、灯、电视、音响、显示器、遥控器、吸尘器、瑜伽垫、工作台、花架、晾衣架、窗帘、画架等；若发现误放，将其移至 furniture。
- **跨房间唯一性 (强制)**：同一 ID 不得出现在多个房间；不同房间的同类家具/设备须使用不同 ID，例如：窗帘用 `curtains_lr_001`（客厅）、`curtains_mb_001`（主卧）、`curtains_kt_001`（厨房）；窗户用 `window_lr_001`、`window_mb_001`、`window_kt_001`、`window_sr_001`（书房）、`window_bc_001`（卫浴），**严禁**书房与厨房共用 window_kt_*。地毯用 `rug_lr_001`、`rug_mb_001` 等。
- **一 ID 一物 (强制)**：同一 ID 不能既出现在 furniture 又出现在 devices（即不能既指家具又指其附属设备，如衣橱与衣橱灯须为 wardrobe_001 与 wardrobe_light_001）。
- **窗户 (强制)**：每个有对外的室内房间（**含主卧、卧室**）必须在 furniture 或 devices 中包含至少一个窗户（按房间缩写命名，如 window_mb_001、window_bc_001），缺则补全。**有窗帘的房间必须有对应窗户**：若存在 curtain_mb_001、curtain_mb_002 等窗帘，同一房间必须有 window_mb_*，不得只有窗帘而无窗户。
- 仅进行必要的**增量修正**，不合理时也可删除原有不合理物品。
"""

LAYOUT_VALIDATION_PROMPT_TEMPLATE = """
你是一位仿真逻辑审核员。请对以下户型数据做**硬校验**，判断是否通过。

**用户画像 (Profile)**: 
{profile_context}

**当前户型 (Layout)**:
{layout_context}

## 验证维度（任一项不通过则 is_valid=false）
1. **devices 仅含「窗户」与「对环境有实际影响的电器」（强校验）**：devices 中是否**仅含** (1) 窗户（window_*），(2) 直接改变室内温/湿/空气/清洁度的电器（空调、暖气、净化器、加湿器、抽油烟机、排风扇、浴霸、洗衣机等）？若含传感器、灯、电视、音响、显示器、遥控器、吸尘器、瑜伽垫、工作台、花架、晾衣架、窗帘、画架等，必须判不通过，并在 correction_content 中写明：将上述 ID 从 devices 移至 furniture。
2. **职业/爱好与房间一致性**：Profile 中的职业、爱好、运动习惯是否在 layout 中有对应工具与空间？若 profile 标注极简主义，房间与设备数量是否与之相符？若描述为小公寓，总面积是否合理？
3. **极简/独居与家电数量（强校验）**：若 Profile 表明**极简主义、独居或小户型**（如 traits 含「极简」、occupation 为独居者、户型描述为小公寓），则**同一类家电不应在每个房间各有一台**。独居者全屋通常只需 1 台洗衣机、1 台冰箱等。若发现「每房一台同类设备」（例如 lr_washing_machine、mb_washing_machine、sr_washing_machine、kt_washing_machine、bc_washing_machine 共 5 台洗衣机），必须判不通过，并在 correction_content 中明确写出：应改为全屋共享的 1 台（或少量）洗衣机，放置于卫生间/阳台等合理位置，删除其余房间的重复同类设备。
4. **职业/爱好与专属空间（强校验）**：若 Profile 的职业或爱好明确需要**特定空间或设施**（例如：花艺表演者需花材存储区、工作台；画师需画架/工作台；音乐家需乐器与练习区；烘焙爱好者需烤箱与操作台），则 Layout 中**必须**存在与之对应的房间类型、家具或设备（如花材存储区、工作台、画架、琴、烤箱等）。若职业/爱好在 profile 中明确提及但 layout 中**完全缺少**相关设施或空间标注，判不通过，并在 correction_content 中说明应补充哪些空间或设施（如「花艺表演者需补充：花材存储区或工作台」）。
5. **家具/设备 ID 跨房间唯一**：同一 ID 是否出现在多个房间？若出现（如 window_kt_001 同时出现在 study_room 与 kitchen），必须判不通过，并说明哪些 ID 重复、在哪些房间。
6. **一 ID 一物**：同一 ID 是否既在 furniture 又在 devices 中出现（如 wardrobe_001 既指衣橱又指衣橱灯）？若出现，判不通过。
7. **窗户**：每个有对外的室内房间（含主卧、卧室、卫生间）是否在 furniture 或 devices 中含有 window_* 条目（且按房间区分）？有窗帘的房间是否同时有至少一个窗户（如主卧有 curtain_mb_001 则须有 window_mb_001）？缺则不通过。
8. **生存设施**：是否有洗衣机、晾衣架、垃圾桶、鞋柜等？宠物相关是否在 profile 有提及时才有对应区域？

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
1. **devices 仅含「窗户」与「对环境有实际影响的电器」**：若 feedback 指出 devices 中含非上述两类（如传感器、灯、电视、音响、显示器、遥控器、吸尘器、瑜伽垫、工作台、花架、晾衣架、窗帘、画架等），将上述 ID 从该房间的 devices 移至 furniture；devices 仅保留窗户（window_*）与直接改变温/湿/空气/清洁度的电器。
2. **ID 唯一**：不同房间的同类物品改用不同 ID（如 study_room 用 window_sr_001、window_sr_002，kitchen 用 window_kt_001、window_kt_002；curtains_lr_001, curtains_mb_001, curtains_kt_001；rug_lr_001, rug_mb_001）。
3. **一 ID 一物**：若同一 ID 既指家具又指设备（如 wardrobe_001 既为衣橱又为衣橱灯），将设备改为独立 ID（如 wardrobe_light_001）。
4. **补全窗户**：每个有对外的室内房间（含主卧、卧室、卫生间）补全 window_<房间缩写>_001（或 _002 等），且不与其他房间共用 ID。有窗帘的房间必须同时有窗户（如主卧有窗帘则须有 window_mb_001），不得只有窗帘而无窗户。
5. **与 profile 一致**：职业/爱好/极简/运动空间等与房间数量、设备配置对齐。
6. **极简/独居时合并同类家电**：若反馈要求「每房一台同类家电不合理」，则保留全屋 1 台（或极少量）该设备（如 1 台洗衣机置于卫生间或阳台），删除其余房间中的重复同类设备（如 mb_washing_machine、sr_washing_machine、kt_washing_machine 等）。
7. **职业/爱好缺失设施时补全**：若反馈要求「补充花艺/职业相关空间或设施」，则在合适房间（如书房、阳台、客厅一角）增加对应家具或设备（如花材存储区、工作台、画架等），ID 按房间命名（如 workbench_sr_001、flower_storage_kt_001）。
8. 输出修正后的完整 rooms 数组（格式与 Schema 一致）。
"""

# ========== details 校验 Agent（仅校验 physics_capabilities 标签；environmental_regulation 由 Python 模板注入）==========
DETAILS_VALIDATION_PROMPT_TEMPLATE = """
你是**物品详情 (house_details) 校验员**。本系统使用 **physics_capabilities** 标签描述物品的物理能力，environmental_regulation 由系统按模板自动注入，**你只需校验 physics_capabilities**。

**用户画像**:
{profile_context}

**当前 house_details（物品详情 JSON 数组，含 physics_capabilities 字段）**:
{details_context}

## 物理能力校验 (physics_capabilities)

1. **合法标签**  
   - 物品的 `physics_capabilities` 列表**仅允许**包含以下系统合法标签（多选或空列表）：  
     `['cooling', 'heating', 'slight_heating', 'ventilation', 'window_ventilation', 'cooking_smoke', 'humidify', 'dehumidify', 'cleaning']`  
   - 出现**任何不在上述列表中的字符串**（如 air_quality、light、custom_xxx 等）→ 判不通过，要求在 correction_content 中写出并改为合法标签或 []。

2. **物理常识**  
   - **普通家具**（木桌、椅子、书架、床、沙发、柜子、地毯、窗帘等）不产生热量、不调节环境，**必须**为 `[]`。若给家具打了发热/制冷等标签 → 判不通过。  
   - **电视、电脑、冰箱、路由器等高耗电电器**运行时散发热量，**应有** `slight_heating`；冰箱**禁止** `cooling`（冰箱对房间是散热），只有**空调**才能有 `cooling`。  
   - **窗户**必须是 `window_ventilation`（被动通风），不得用 cooling/heating。  
   - 净水器、插线板、遥控器、仅显示的显示器等无直接环境影响的，应为 `[]`。

3. **放行原则（极其重要）**  
   - **只要标签全部在合法列表中，且符合人类生活常识，一律放行，不要报错。**  
   - 仅当出现以下情况时才提出修正：  
     (a) 捏造了不存在的标签（不在合法列表中）；  
     (b) 明显违反常识（例如给木头桌子贴了 slight_heating，或给冰箱贴了 cooling）。  
   - 不要因为「标签看起来多」或「你觉得可以更简」就要求清空或删减；合法且合理的标签组合应直接通过。

4. **其它（次要）**  
   - current_state：家具不填 temperature_set；open 若为关闭须为 "closed" 非 "close"。  
   - support_actions：设备非空且仅设备相关动作；窗户用 open/close。

请逐项审视。**仅在有明确违规时**在 correction_content 中写出「物品标识 + 应修正的 physics_capabilities」；否则 is_valid 为 true、correction_content 为空。

## 返回
- is_valid: true 当且仅当所有条目符合上述原则；否则 false。
- correction_content: 仅在有违规时逐条列出（device_id/furniture_id 或 name + 应如何改 physics_capabilities）；通过则为空字符串。
"""

DETAILS_CORRECTION_PROMPT_TEMPLATE = """
你是**物品详情修正模块**。上一轮 house_details 未通过校验，请**仅根据校验反馈**修正对应物品的 **physics_capabilities**（environmental_regulation 由系统按模板注入，不要在 patch 中填写）。

**用户画像**:
{profile_context}

**当前 house_details (未通过)**:
{details_context}

**校验反馈 (必须全部解决)**:
{correction_content}

## 修正原则

1. **物理能力 (physics_capabilities)**  
   - **合法标签仅限**：`cooling`, `heating`, `slight_heating`, `ventilation`, `window_ventilation`, `cooking_smoke`, `humidify`, `dehumidify`, `cleaning`。  
   - 按反馈逐条修正：  
     - 冰箱：含 `slight_heating`，**删除** `cooling`。  
     - 电视、电脑、高耗电电器：补全 `slight_heating`。  
     - 普通家具（桌、椅、床、书架、柜、窗帘等）：改为 `[]`。  
     - 窗户：改为 `window_ventilation`。  
     - 捏造的不存在标签：删除或替换为合法标签。  
   - **只改反馈里提到的物品**，未提及的物品不要动，更不要整体清空为 []。

2. **current_state / support_actions**：仅当校验反馈中明确提到时再修正（如 open 关闭改为 "closed"；设备动作仅设备相关）。

3. **输出格式（补丁制）**  
   - 每条补丁含 **"id"**（与 house_details 中 device_id/furniture_id 一致）和 **"patch"**（要合并的字段）。patch 中通常只需含 **physics_capabilities**，例如：  
     {{ "id": "fridge_001", "patch": {{ "physics_capabilities": ["slight_heating"] }} }}  
   - 不要输出 environmental_regulation（由系统注入）。  
   - 输出**仅**一个 JSON 数组 [ {{"id": "xxx", "patch": {{...}}}}, ... ]，不要 markdown、``` 或说明文字。
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
   - 查看用户的 `routines`。如果用户现在应该在睡觉，床的 `current_state` 可为 `occupied: true`（若需占用状态）。current_state 不要求填 items_on；仅记录设备开否与设定值。

**物理能力打标 (physics_capabilities) 极严厉规则（违规将导致系统崩溃）**：
- 请从以下词典中选择物品具备的物理标签：['cooling', 'heating', 'slight_heating', 'ventilation', 'window_ventilation', 'cooking_smoke', 'humidify', 'dehumidify', 'cleaning']
- **【规则1：90%的物品必须为空】**：普通家具（沙发、桌椅、床、书架、地毯、吉他、瑜伽垫、普通窗帘等）没有任何物理调节能力，**必须且只能填 `[]`**！绝对禁止给家具贴发热或清洁标签！
- **【规则2：禁止抄袭全列表】**：电器通常只有1到2个功能。电视机、电脑等仅有发热副作用，只能填 `["slight_heating"]`！空调填 `["cooling", "heating"]`；窗户填 `["window_ventilation"]`。
- **【规则3：符合生活常识】**：饮水机、微波炉等不对整个房间的环境（温湿度/清新度）产生宏观改变，也必须填 `[]`。

**按类别解耦 Schema**  
- **被动家具/静态物**：无电源、无可调参的，current_state **不得**包含 power、mode、temperature_set、fan_speed。  
- **智能家电**：可含 power、mode、temperature_set、fan_speed。  
- **窗户**：current_state 不含温控电器字段（仅 open）。

**current_state 唯一功能：记录设备开否 + 若开则当前设置值（不记录家具温度、不记录表面放置物）**
- **设备**：仅**直接调节温度**的才写 temperature_set（设定温度 °C）；仅**直接调节湿度**的才写 humidity_set（设定湿度 0–1）；其他电器只写 power（及必要时 mode、fan_speed）。**不写**「当前室温」temperature，只写**设定值**。常开/在用设备 power 为 on。
- **家具**：**不填** temperature、temperature_set、humidity_set、**不填 items_on**；current_state 可为 {{}} 或仅保留 open（可开合）、occupied（如床）等仿真需要的。不分析家具温度，仿真不依赖 items_on。
- **若定义了 current_state_removed_fields**，则 current_state 中**必须真正移除**所列字段。与 description 一致。
- **open 状态**：若为「关闭」应为 "open": "closed"，**禁止** "open": "close"。电器类多数默认 power 为 off；常开类（冰箱、净化器等）可为 on。窗帘、遮光帘不设 mode、temperature_set。

**support_actions（仅设备由 LLM 生成，家具由程序统一填写）**  
- **家具**：support_actions 由后处理程序统一填写，**无需**在此生成；可填 [] 或占位，程序会覆盖为通用+家具常用动作。  
- **设备**：必须生成非空 support_actions，且**仅填写设备相关动作**：turn_on、turn_off、open、close、set_temp、set_humidity、set_mode、set_fan_speed 等；窗户用 open、close。**不得**为设备填写 sit、stand_up、sleep 等家具动作。  
- 设备动作名使用正确英文（如 adjust_folding 非 adjust_tolding）。

**数据格式**  
- **environmental_regulation** 一律留空 **[]**（由系统根据 physics_capabilities 注入）。  
- **temperature、temperature_set** 等仅写在 current_state 内部，不得在物品根层级再写平级字段。

**输出要求**:
- 为列表中的**每一个** ID 生成配置；每个物品必须填写 **physics_capabilities**（列表，从上述词典多选或 []）。
- **设备**的 support_actions 不得为空且仅含设备相关动作；**家具**的 support_actions 可填 []，程序会统一覆盖。
- 严格按照 JSON Schema 输出 items 列表。
"""

# 单物件生成（按物件并行时使用）：仅生成 target_item_id 一个物品的详情，输出 items 为单元素数组
LAYOUT2DETAILS_SINGLE_ITEM_PROMPT_TEMPLATE = """
你是一位高保真的物联网与交互逻辑设计师。请为**当前房间内的一个物品**生成详细的属性定义。

**输入上下文**:
1. **用户画像**: {profile_context}
2. **当前房间**: {room_id} ({room_type})
3. **仅生成以下单个物品的详情**: {target_item_id}（类型：{target_item_type}）
4. **同房间其他物品（仅供上下文参考，无需为它们生成）**: 家具 {furniture_list}, 设备 {device_list}

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
   - 查看用户的 `routines`。如果用户现在应该在睡觉，床的 `current_state` 可为 `occupied: true`（若需占用状态）。current_state 不要求填 items_on；仅记录设备开否与设定值。

**物理能力打标 (physics_capabilities) 极严厉规则（违规将导致系统崩溃）**：
- 请从以下词典中选择物品具备的物理标签：['cooling', 'heating', 'slight_heating', 'ventilation', 'window_ventilation', 'cooking_smoke', 'humidify', 'dehumidify', 'cleaning']
- **【规则1：90%的物品必须为空】**：普通家具（沙发、桌椅、床、书架、地毯、吉他、瑜伽垫、普通窗帘等）没有任何物理调节能力，**必须且只能填 `[]`**！绝对禁止给家具贴发热或清洁标签！
- **【规则2：禁止抄袭全列表】**：电器通常只有1到2个功能。电视机、电脑等仅有发热副作用，只能填 `["slight_heating"]`！空调填 `["cooling", "heating"]`；窗户填 `["window_ventilation"]`。
- **【规则3：符合生活常识】**：饮水机、微波炉等不对整个房间的环境（温湿度/清新度）产生宏观改变，也必须填 `[]`。

**current_state 唯一功能：记录设备开否 + 若开则当前设置值**。仅直接调节温度的写 temperature_set；仅直接调节湿度的写 humidity_set（0–1）；其他设备只写 power（及必要时 mode、fan_speed）。家具不填 temperature/temperature_set/humidity_set、不填 items_on；可为空对象或仅 open/occupied。若定义 current_state_removed_fields 须真正移除。**格式**：environmental_regulation 一律留空 []；设定值仅写在 current_state 内，不在根层级重复。

**support_actions（仅设备由 LLM 生成）**  
- **家具**：由程序后处理统一填写，此处可填 [] 或占位。  
- **设备**：必须生成非空 support_actions，且**仅设备相关动作**：turn_on、turn_off、open、close、set_temp、set_humidity、set_mode、set_fan_speed；窗户用 open、close。动作名正确英文。

**current_state 默认值**:
- 设备：仅直接调节温度的写 temperature_set；仅直接调节湿度的写 humidity_set；其他只写 power（常开可为 on）。不写「当前室温」temperature。家具：不写 temperature/temperature_set/humidity_set、不写 items_on；current_state 可为空对象或仅 open/occupied。

**输出要求**:
- **仅为此一个 ID（{target_item_id}）生成配置**。输出 **items 数组仅包含一个元素**；必须填写 **physics_capabilities**（从词典多选或 []），**environmental_regulation** 留空 []。
- **设备**的 support_actions 不得为空且仅含设备相关动作；**家具**的 support_actions 可填 []，程序会统一覆盖。
- 严格按照 JSON Schema 输出 items 列表（单元素）。
"""
