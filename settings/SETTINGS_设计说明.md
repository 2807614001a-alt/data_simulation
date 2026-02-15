# Settings 层设计说明

本文档说明 settings 管线中**每一层针对什么数据、如何处置、数据从哪来、依据什么标准、产出给谁用**，以及 **furniture 与 device 在各层的定义与处理方式**。

---

## 一、管线总览与数据流

```
autosetting.py 依次执行：

  profile_generator.py   →  profile.json
           ↓
  profile2layout.py      →  house_layout.json   （读 profile.json）
           ↓
  layout_check.py        →  修正并覆盖 house_layout.json  （读 profile + layout）
           ↓
  layout2details.py      →  house_details.json  （读 house_layout.json + profile.json）
```

**下游使用**：agents 层（planning、event、device_operate、n_day_simulation、physics_engine）只读取 **profile.json**、**house_layout.json**、**house_details.json**，不依赖其他 settings 文件。

---

## 二、各层逐项说明

### 1. profile_generator.py

| 项目 | 说明 |
|------|------|
| **输入** | 无（或可选种子）。若同目录下已有 `profile.json` 可被后续步骤读取，本步为**重新生成**一份新画像。 |
| **针对什么** | 生成「一户居民」的完整画像，用于驱动后续所有与「人设、作息、职业、爱好」相关的空间与行为生成。 |
| **依据标准** | 提示词中的 `PROFILE_GENERATOR_PROMPT_TEMPLATE`、`ROLE_DICE_BRAINSTORM_PROMPT`：性格（Big Five、traits）、价值观、作息（睡眠/饮食/运动）、周日程、偏好（娱乐、音乐、舒适室温）、随机事件配置等；职业与爱好需可落地到后续 layout 中的空间或物品。 |
| **产出** | `settings/profile.json`：单一大 JSON 对象，含 user_id、name、age、gender、occupation、personality、values、routines、preferences、random_event_config 等。 |
| **产出给谁用** | profile2layout、layout_check、layout2details 用其做人设与空间一致性约束；planning / event 用其做活动与事件生成的上下文。 |

**本层不涉及 furniture / device**，只产出「人」的配置。

---

### 2. profile2layout.py

| 项目 | 说明 |
|------|------|
| **输入** | `settings/profile.json`（整份 JSON 序列化为字符串注入提示词）。 |
| **针对什么** | 根据人设生成「户型」：有哪些房间、每房面积、每房有哪些**家具 ID** 与**设备 ID**、每房环境初始值。 |
| **依据标准** | 提示词 `PROFILE2LAYOUT_PROMPT_TEMPLATE` + Schema（RoomEntry / RoomInfo / EnvironmentState）。约束包括：<br>• 房间 ID、房间类型、面积、environment_state（temperature、humidity、light_level、noise_level、hygiene、air_freshness）符合常识与 profile。<br>• **furniture 与 devices 的划分**（见下「furniture / device 在本层的定义」）。<br>• 一 ID 一物、跨房间唯一、按房间命名（如 window_lr_001、curtains_mb_001）。<br>• 每间主要室内房间必须有窗户；有窗帘则必有对应窗户。 |
| **产出** | `settings/house_layout.json`：键为 `room_id`（如 living_room、master_bedroom），值为 `room_info`（含 room_type、area_sqm、**furniture**（ID 列表）、**devices**（ID 列表）、environment_state）。 |
| **产出给谁用** | layout_check 修正 layout；layout2details 按 layout 中的 furniture / devices 列表逐项生成详情；agents 用 layout 做房间列表、物品归属、环境初始值。 |

#### furniture / device 在本层的定义（按原则，不按设备名打表）

- **devices 仅放两类**  
  1. **窗户（特例）**：为便于仿真开窗通风、采光对室内环境的影响，**窗户一律放在 devices**，按房间命名（如 window_mb_001、window_lr_001）。  
  2. **对环境有实际影响的电器**：能**直接改变**室内温度、湿度、空气或清洁度的，如空调、暖气、净化器、加湿器、抽油烟机、排风扇、浴霸、洗衣机等。  
  **不生成传感器**（温湿度传感器、人体传感器等仅感测不调节，仿真不依赖）。

- **furniture 放其余全部**  
  灯、电视、音响、显示器、遥控器、吸尘器、窗帘、床、桌、椅、柜、书架、瑜伽垫、工作台、花架、晾衣架等一律归 furniture。窗帘本体、智能窗帘电机（若不直接调环境）也归 furniture。

**数据怎么来的**：由 LLM 根据 profile 与上述原则一次性生成整份 house_layout；程序不修改内容，只做 Schema 校验与保存。

---

### 3. layout_check.py

| 项目 | 说明 |
|------|------|
| **输入** | `settings/profile.json`、`settings/house_layout.json`（当前已存在的 layout）。 |
| **针对什么** | 对**整份 house_layout** 做逻辑审查与修正：职业/爱好是否有对应空间与物品、宠物是否有对应设施、生存底线设施是否齐全、极简/独居时是否重复堆叠同类家电、**devices 是否仅含窗户与对环境有实际影响的电器**、窗户/窗帘/ID 是否唯一与一致。 |
| **依据标准** | 提示词 `LAYOUT_CHECK_PROMPT_TEMPLATE`（逻辑审查）、`LAYOUT_VALIDATION_PROMPT_TEMPLATE`（硬校验）、`LAYOUT_CORRECTION_PROMPT_TEMPLATE`（按反馈修正）。校验维度包括：devices 仅含窗户与环保电器；跨房间 ID 唯一；一 ID 一物；每间有窗、有窗帘则有窗；极简/独居时合并同类家电等。 |
| **产出** | 修正后的 **house_layout.json**（同路径覆盖）。结构不变，仍为 room_id → room_info，仅对 rooms 内容做增删改。 |
| **产出给谁用** | layout2details 读取的是**当前磁盘上的 house_layout.json**（即本步覆盖后的结果）；agents 同样读取该文件。 |

#### furniture / device 在本层的处置

- **不改变「furniture 与 devices 划分」的定义**，只按同一套原则**校验与修正**：若发现 devices 里出现传感器、灯、电视、音响、显示器、遥控器、吸尘器、瑜伽垫、工作台、花架、晾衣架、窗帘、画架等，则在修正时将其**从 devices 移至 furniture**；仅保留窗户与直接改变温/湿/空气/清洁度的电器在 devices 中。

**数据怎么来的**：读入 profile + layout → LLM 输出修正后的 rooms → 程序转成 room_id → room_info 字典并写回 house_layout.json。若校验未通过会多轮修正（校验 → 修正 → 再校验），直到通过或达上限。

---

### 4. layout2details.py

| 项目 | 说明 |
|------|------|
| **输入** | `settings/house_layout.json`、`settings/profile.json`。 |
| **针对什么** | 对 layout 中**每一个 furniture_id 和每一个 device_id** 生成一条「物品详情」：名称、所属房间、support_actions、current_state、environmental_regulation。家具与设备在**数据结构与后处理**上区分处理（见下）。 |
| **依据标准** | 提示词 `LAYOUT2DETAILS_ROOM_PROMPT_TEMPLATE`（整房生成）或 `LAYOUT2DETAILS_SINGLE_ITEM_PROMPT_TEMPLATE`（单物生成）；校验与修正使用 `DETAILS_VALIDATION_PROMPT_TEMPLATE`、`DETAILS_CORRECTION_PROMPT_TEMPLATE`。程序侧：Schema 为 FurnitureItem（FurnitureState）/ DeviceItem（DeviceState）；后处理对 environmental_regulation、current_state 做清理与归一（见下）。 |
| **产出** | `settings/house_details.json`：**一个 JSON 数组**，每个元素为一件物品的完整配置。每条要么带 `furniture_id`（家具），要么带 `device_id`（设备），二者互斥；其余字段包括 name、room、support_actions、current_state、environmental_regulation 等。 |
| **产出给谁用** | agents 将数组按 id 转为 `house_details_map`（id → item），用于：事件生成/校验中的「家具与设备详情」与 support_actions；device_operate 的 support_actions 上下文；物理引擎的 environmental_regulation 与 current_state（设备状态、设定温度/湿度等）。 |

#### furniture 与 device 在本层的区分与处置

**1）生成阶段**

- **来源**：layout 中每个房间的 `furniture` 列表与 `devices` 列表；程序按「先 furniture 后 devices」顺序遍历，对每个 ID 调用 LLM 生成一条详情。
- **如何区分**：遍历时已知该 ID 来自 `room_info.furniture` 还是 `room_info.devices`，对应传入提示词的「家具」或「设备」类型；LLM 按类型与提示词约束生成不同结构的 current_state 等。
- **Schema 差异**：  
  - **家具 (FurnitureItem)**：`furniture_id`、name、room、support_actions、comfort_level、**current_state: FurnitureState**、environmental_regulation。  
  - **设备 (DeviceItem)**：`device_id`、name、room、support_actions、**current_state: DeviceState**、environmental_regulation。

**2）current_state 的处置标准（程序 + 提示词）**

- **设计原则**：current_state **只表示**「设备是否开启」以及「若开启则当前设定值」（如设定温度、设定湿度）；不表示家具温度、不表示表面放置物。
- **家具 (FurnitureState)**：  
  - **允许字段**：仅 `open`、`occupied`（可选）。  
  - **程序**：`_clean_current_state` 会**删掉** furniture 条目中的 temperature、items_on、power、temperature_set、humidity_set 等，只保留 open、occupied。  
  - 占位条目（生成失败时）的 current_state 为 `{}`。
- **设备 (DeviceState)**：  
  - **允许字段**：power、temperature_set、humidity_set、mode、fan_speed、open。  
  - **程序**：power 若非 on/off 会归一为 off；只保留上述键，其余丢弃；若缺少 power 则补 "off"。  
  - 仅**直接调节温度**的设备才应写 temperature_set；仅**直接调节湿度**的才写 humidity_set（0–1）。

**3）environmental_regulation 的处置标准**

- **适用对象**：仅当物品**主要用途**为直接改变室内温度/湿度/空气/清洁度时才应有非空 regulation；否则应为 `[]`。  
- **程序**：`_validate_and_clean_env_regulation` 会：  
  - 按「主要用途」语义剔除误填（如净水器、插线板、卫生纸架、洗碗机、电视、显示器、遥控/控制等不填 temperature/air_freshness/humidity）；  
  - 统一 working_condition 为机器可读键值对（只保留 power、open、mode）；  
  - 制冷时 target_value 钳位（不超过合理室温）、制热/制冷与 mode 方向一致（如加热器不得 mode: "cool"）；  
  - 同条件同 target_attribute 合并、delta=0 等清理。  
- **家具**：多数家具无 environmental_regulation；窗户若在 layout 的 devices 中，则其详情条目标记为设备，可含换气类 regulation（仅 open，无 mode: cool 等）。

**4）support_actions（设备相关动作由 LLM 生成，家具由程序统一填写）**

- **设计**：**仅设备**的 support_actions 表示「该设备支持的操作」；**家具**的 support_actions 统一为「通用 + 家具常用动作」固定列表，不依赖 LLM，避免 token 膨胀与不一致。  
- **家具**：后处理 `_apply_furniture_support_actions_default` 将**所有家具**的 support_actions 覆盖为 `FURNITURE_DEFAULT_SUPPORT_ACTIONS`（含 use、interact、sit、sleep、open、close 及 EVENT_UNIVERSAL_ACTIONS：clean、fix、inspect、touch、move_to、examine、photograph、wipe、repair）。LLM 可为家具填 [] 或占位，程序会覆盖。  
- **设备**：由 LLM 生成，且**仅填写设备相关动作**（turn_on、turn_off、open、close、set_temp、set_humidity、set_mode、set_fan_speed 等；窗户用 open/close）。若设备 support_actions 为空，程序兜底填入 `["turn_on", "turn_off", "use"]`。  
- **下游**：event 层用 support_actions 构建「家具与设备详情」上下文，并校验「动作是否在 support_actions 或通用物理交互列表中」；device_operate 用 support_actions 作设备能力上下文（仅设备有实质差异，家具为统一列表）。

**5）缺失与占位**

- 若 layout 中某 ID 在 details 中缺失，程序会**补一条默认条目**：带 device_id、name、room、support_actions: []、current_state: {"power": "off"}、environmental_regulation: []，再经 _fill_empty_support_actions 等后处理。  
- 单物生成失败时用占位条目：家具 current_state 为 `{}`，设备 current_state 为 `{"power": "off"}`。

**数据怎么来的**：layout 中每个 (room_id, furniture_id) 与 (room_id, device_id) 各调用一次 LLM 生成单条详情；合并为列表后做去重、与 layout 对齐、补全缺失、执行 _validate_and_clean_env_regulation、_clean_current_state、**_apply_furniture_support_actions_default**（家具 support_actions 统一覆盖）、**_fill_empty_support_actions**（仅设备空时兜底），最后写入 house_details.json。

---

## 三、furniture 与 device 汇总表

| 阶段 | furniture | devices |
|------|-----------|---------|
| **profile2layout（生成）** | 灯、电视、音响、显示器、遥控器、吸尘器、窗帘、床、桌、椅、柜、书架、瑜伽垫、工作台、花架、晾衣架等；一切「非窗户且非环保电器」的物体。 | ① 窗户（特例）；② 直接改变室内温/湿/空气/清洁度的电器（空调、暖气、净化器、加湿器、抽油烟机、排风扇、浴霸、洗衣机等）。不生成传感器。 |
| **layout_check（修正）** | 误放在 devices 中的传感器、灯、电视、音响、显示器、遥控器、吸尘器、瑜伽垫、工作台、花架、晾衣架、窗帘、画架等移入 furniture。 | 只保留窗户与「对环境有实际影响」的电器。 |
| **layout2details（详情）** | 每条带 furniture_id；current_state 只保留 open/occupied；无 power/temperature_set/humidity_set；多数无 environmental_regulation。**support_actions** 由程序统一覆盖为固定列表（use、interact、sit、sleep、open、close + 通用物理交互）。 | 每条带 device_id；current_state 含 power，可选 temperature_set、humidity_set、mode、fan_speed、open；可有 environmental_regulation（仅主要用途为调节环境时）。**support_actions** 由 LLM 生成且仅含设备相关动作（turn_on、turn_off、open、close、set_temp 等），空时兜底 ["turn_on","turn_off","use"]。 |

---

## 四、下游对 settings 数据的使用

| 数据文件 | 谁读 | 怎么用 |
|----------|------|--------|
| **profile.json** | event.py（load_settings_data） | 序列化为 profile_json 注入事件生成/校验提示词；人设、作息、偏好驱动事件内容与设备操作。 |
| **house_layout.json** | event.py、planning.py | 房间列表、每房 furniture + devices ID 列表、environment_state。**存在性以 layout 为准**：某物品是否「存在」于某房间只看 layout 中该房间的 furniture/devices 列表；event 中 target_object_ids 合法性由 _sanitize_events 按 layout 的 room_item_map 校验。 |
| **house_details.json** | event.py、device_operate.py、n_day_simulation.py、physics_engine（间接） | 转为 house_details_map（id → item）。**调设备、动作能力、环境调节**用 details：<br>• **support_actions**：家具为程序统一固定列表；设备为 LLM 生成的「仅设备相关」动作；**窗户**由程序固定为 open/close。<br>• **current_state**：设备当前开关与设定值；窗户仅保留 open（无 power）。<br>• **environmental_regulation**：物理引擎按 regulation + working_condition 计算房间变化；窗户仅允许 air_freshness（working_condition 为 open: open）。 |

---

## 五、数据流简图（含 furniture / device）

```
profile.json（人）
      ↓
house_layout.json（房间 + 每房 furniture[] / devices[] + environment_state）
      ↓ layout_check 修正 layout，保证 devices 仅窗户+环保电器、furniture 为其余
      ↓
house_details.json（物品详情数组：每条 furniture_id 或 device_id + name, room, support_actions, current_state, environmental_regulation）
      ↓ layout2details 按 layout 逐项生成，并按 furniture/device 区分 current_state、清理 regulation
      ↓
agents：profile_json / house_layout / house_details_map → planning、event、device_operate、physics_engine
```

---

## 六、存在性检查 vs 调设备（逻辑分工）

- **存在性检查**：以 **layout** 为准。事件里「某房间是否有某物品」、target_object_ids 是否合法，由 `event._sanitize_events` 用 `full_layout[room_id]["furniture"] + full_layout[room_id]["devices"]` 校验；`get_room_specific_context` 也按 layout 列出每房物品，缺失 details 时用兜底 name/support_actions 仍展示，避免 layout 有而 details 缺失时物品「消失」。
- **调设备 / 动作能力**：用 **details**。device_operate、事件生成与校验中的「该物品支持哪些动作」、device_patches、物理引擎的 current_state / environmental_regulation 均来自 house_details_map。

---

## 七、窗户特例与 environmental_regulation 审计要点

- **窗户**：在 layout 中一律放在 devices；在 details 中由 `_normalize_window_devices` 统一：support_actions 仅 `["open", "close"]`，current_state 仅 `{"open": "open"|"closed"}`，environmental_regulation 仅一条「开窗换气」：target_attribute=air_freshness、working_condition=`{"open": "open"}`、delta_per_minute=0.08。禁止窗户出现 temperature、power 等误填。
- **不应有调节能力的物品**：灯、窗帘、地毯、桌、椅、柜、梳妆台、床头柜、电视/显示器、插线板、净水器、洗碗机等在 `_validate_and_clean_env_regulation` 中按 id/name 语义剔除 temperature 或 air_freshness；窗帘等亦不填 air_freshness。
- **数值合理性**：temperature 的 target_value 钳位 18–30°C；制冷时 target_value≤24、制热时≥18；delta 方向与 mode 一致（制热设备不得 mode: cool）。

以上即为 settings 部分每一层**针对什么、如何处置、数据来源、依据标准、产出用途**，以及 **furniture 与 device 在各层的定义与处理**的完整说明。
