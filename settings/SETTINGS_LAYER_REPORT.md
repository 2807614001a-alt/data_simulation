# Settings 层管线报告

本文档为**管线与使用**的简要报告。更完整的「每层针对什么、数据从哪来、依据什么标准、furniture/device 如何处置」说明见：**[SETTINGS_设计说明.md](./SETTINGS_设计说明.md)**。

下面说明 `settings` 目录下每一层**如何生成**、**读取什么数据**、**产出用于何处**，以及 agents 层实际使用了哪些 settings 产物。

---

## 管线总览（autosetting.py 顺序）

```
profile_generator.py → profile.json
       ↓
profile2layout.py     → house_layout.json  （读 profile.json）
       ↓
layout_check.py      → 修正 house_layout.json（读 profile + layout）
       ↓
layout2details.py    → house_details.json （读 house_layout.json + profile.json）
```

---

## 1. profile_generator.py

| 项目 | 说明 |
|------|------|
| **读取** | 无（或可选种子约束） |
| **生成** | `profile.json`：居民画像（性格、作息、饮食、职业、爱好、住宅描述、routines 等） |
| **用途** | 下游 layout / details / planning / event 均依赖 profile 做人设与空间一致性约束 |
| **产出路径** | `settings/profile.json` |

---

## 2. profile2layout.py

| 项目 | 说明 |
|------|------|
| **读取** | `settings/profile.json` |
| **生成** | `house_layout.json`：各房间 `room_id`、`room_type`、`area_sqm`、`furniture`（ID 列表）、`devices`（ID 列表）、`environment_state`（温度/湿度/光照等初始值） |
| **用途** | 定义「有哪些房间、每房有哪些家具/设备 ID」；agents 用 layout 做房间列表、物品归属、环境初始值。 |
| **产出路径** | `settings/house_layout.json` |

**furniture / devices 划分（当前约定）**  
- **devices**：仅两类 —— (1) **窗户（特例）**，一律放 devices，便于仿真开窗通风/采光；(2) **对环境有实际影响的电器**（空调、暖气、净化器、加湿器、抽油烟机、排风扇、浴霸、洗衣机等）。**不生成传感器**。  
- **furniture**：其余全部（灯、电视、音响、显示器、遥控器、吸尘器、窗帘、床、桌、椅、柜、书架、瑜伽垫、工作台、花架、晾衣架等）。

---

## 3. layout_check.py

| 项目 | 说明 |
|------|------|
| **读取** | `profile.json`、当前 `house_layout.json`（或上一步产出） |
| **生成** | 修正后的 `house_layout.json`（同路径覆盖）：补全职业/爱好所需空间与物品、极简/独居时合并同类家电、窗户/窗帘/ID 唯一性等 |
| **用途** | 保证 layout 与 profile 逻辑闭环，供 layout2details 与 agents 使用 |
| **产出路径** | `settings/house_layout.json`（覆盖） |

---

## 4. layout2details.py

| 项目 | 说明 |
|------|------|
| **读取** | `house_layout.json`、`profile.json` |
| **生成** | `house_details.json`：每个 furniture_id / device_id 的详细属性（name、room、support_actions、current_state、environmental_regulation 等）；家具与设备在 details 层分开处理（FurnitureState 仅 open/occupied；DeviceState 含 power、temperature_set、humidity_set、mode、fan_speed）。 |
| **用途** | agents 用 details 做「物品支持的动作」、设备状态、环境调节计算（physics_engine 用 environmental_regulation + current_state）；event 校验 target_object_ids、device_patches 时依赖 details。 |
| **产出路径** | `settings/house_details.json` |

**current_state 设计**  
- 唯一功能：记录**设备是否开启** + **若开启则当前设置值**（如 temperature_set、humidity_set）。  
- 家具不填 temperature/items_on；设备仅直接调温的写 temperature_set、仅直接调湿的写 humidity_set。

---

## Agents 层对 settings 的实际使用

| 数据 | 是否加载 | 使用位置与用途 |
|------|----------|----------------|
| **profile.json** | ✅ 是 | `event.py`：`load_settings_data()` 读为 `profile_json`，注入事件生成/校验上下文。 |
| **house_layout.json** | ✅ 是 | `event.py` / `planning.py`：房间列表、每房 furniture/devices ID、environment_state；用于房间裁剪与上下文。 |
| **house_details.json** | ✅ 是 | `event.py` / `device_operate.py` / `n_day_simulation.py`：转为 `house_details_map`（id → item）；用于 **support_actions**（事件生成/校验中「动作是否在 support_actions 或通用列表中」）、设备状态、device_patches、物理引擎所需 environmental_regulation 与 current_state。 |

**support_actions 使用说明**：event 层用 `house_details` 中的 support_actions 构建「家具与设备详情」上下文，供 LLM 生成事件时知道每件物品支持哪些动作；校验时要求 target_object_ids 内物品要么在 support_actions 中列出该动作，要么属于通用物理交互（clean、fix、inspect 等）。device_operate 层在推断 device_patches 时也会把 support_actions 写入上下文。故 **support_actions 保留且被 agents 使用**。

---

## 文件依赖简图

```
profile.json
    ↓
house_layout.json  ← layout_check（读 profile + layout）
    ↓
house_details.json ← layout2details（读 layout + profile）
```

**Agents 实际读取**：`profile.json`、`house_layout.json`、`house_details.json`。
