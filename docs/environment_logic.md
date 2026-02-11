# 环境（温度/湿度/清洁度）逻辑说明

## 一、整体流程

```
house_layout (environment_state 初始) 
    → layout2details 生成 house_details (environmental_regulation)
    → 仿真时：event 层按「活动开始时间」懒更新房间状态 → 拼成文本注入 LLM → 生成事件
    → 输出里附带 room_environment / environment_by_activity
```

---

## 二、数据来源

### 1. 房间初始环境：`house_layout.json`

- 每个房间有 `environment_state`：`temperature`, `humidity`, `light_level`, `noise_level`。
- 仅作 layout 描述；**物理推演**用的是 physics_engine 维护的 snapshot（见下）。

### 2. 物品对环境的影响：`house_details.json`（layout2details 生成）

- 每个家具/设备可有 **`environmental_regulation`** 列表。
- 每项包含：
  - `target_attribute`: `"temperature"` | `"humidity"` | `"hygiene"`
  - `delta_per_minute`: 每分钟变化量（如空调制冷 -1.0）
  - `working_condition`: 生效条件（如 `{"power": "on", "mode": "cool"}`），可选。
- 只有填写了 `environmental_regulation` 的物品才会在 **physics_engine** 里参与「设备干预」计算。

---

## 三、物理引擎：`agents/physics_engine.py`

- **入口**：`calculate_room_state(current_state, last_update_time, current_time, active_devices, details_map, outdoor_weather)`。
- **算法**：
  1. **自然衰减**（无设备时）  
     - 温度/湿度向室外趋近：`T_new = T_old + k * (T_outdoor - T_old) * dt`。  
     - 清洁度缓慢下降，趋向 0.4。  
  2. **设备干预**  
     - 遍历 `active_devices`，根据 `details_map` 中的 `environmental_regulation`，若设备 `state` 满足 `working_condition`，则对对应属性加上 `delta_per_minute * dt`。
- **懒更新**：只在「需要当前房间环境」时，从 `last_update_ts` 推到 `current_time` 算一次，不按秒步进。

---

## 四、Event 层如何使用环境（`agents/event.py`）

### 1. 状态维护

- **environment_snapshot**：`room_id -> { temperature, humidity, hygiene, last_update_ts }`，按活动顺序在内存中更新。
- **outdoor_weather**：来自 `data/simulation_context.json` 的 `outdoor_weather`；若没有则默认 `{"temperature": 28.0, "humidity": 0.6}`。

### 2. 每次生成事件前

- 对当前活动的 **main_rooms** 调用 `_update_room_environments_and_format()`：
  - 用 **physics_engine.calculate_room_state** 从「上次时间」推到「当前活动开始时间」；
  - **注意**：这里目前传入 **`active_devices=[]`**，因此**只有自然衰减 + 室外趋近**，没有空调/加湿器/扫地机等设备对数值的干预。
- 拼成「当前房间环境」文案（每房间一行：温度、湿度、清洁度），再追加居民 **舒适温度偏好**（profile 的 `preferences.home_temperature`，默认 24°C）。

### 3. 注入 LLM

- Prompt 中有 **「### 2.1 当前房间环境」** 和 **「环境响应」** 指令（`prompt.py`）：
  - 若 |室温 - 舒适温度| > 2°C，可插入开/关空调、开窗等事件；
  - 若清洁度 < 0.5 且居民尽责性高，可插入打扫类事件。
- 因此：**环境参与的是「是否生成调节类事件」的推理**；数值本身目前未受设备状态反馈影响。

### 4. 活动结束后

- 将该活动涉及房间的 snapshot 的 **last_update_ts** 设为该活动的 **end_time**，供下个活动懒更新时使用。

### 5. 输出

- **events.json**：每个 event 带 **`room_environment`**（该事件所在房间在「该活动开始时」的 temperature/humidity/hygiene）；顶层 **meta.environment_by_activity** 存每个活动开始时各房间的完整快照。
- **action_event_chain**：若 event 有 `room_environment`，会原样带到 chain 对应条目。

---

## 五、当前限制与可改进点

| 项目 | 现状 | 可改进 |
|------|------|--------|
| 设备对环境的影响 | event 里调用 physics 时 **active_devices=[]**，设备不参与计算 | 在生成事件后、更新 snapshot 前，根据当次事件的 device 操作推断设备状态，再在下次活动前用 **active_devices** 调用 calculate_room_state |
| 室外天气 | 从 simulation_context 读，缺省固定值 | 可由 planning 或单独模块按日/天气类型生成写入 simulation_context |
| 跨天 | snapshot 按「活动时间」推进，跨天用 last_update_ts 仍为前一日 end_time | 多日仿真时每日可重置或按日边界做一次自然衰减 |

---

## 六、相关文件速查

- **定义/配置**：`settings/layout2details.py`（environmental_regulation 结构）、`settings/house_layout.json`（environment_state）、`settings/house_details.json`（各物品 environmental_regulation）。
- **物理计算**：`agents/physics_engine.py`（calculate_room_state）。
- **使用与输出**：`agents/event.py`（snapshot、格式化、注入 prompt、写 room_environment / environment_by_activity）。
- **Prompt 文案**：`prompt.py`（EVENT_GENERATION_PROMPT_TEMPLATE 中 2.1 与「环境响应」）。
