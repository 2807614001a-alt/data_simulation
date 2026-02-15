# -*- coding: utf-8 -*-
"""
环境物理模拟器：懒加载更新，仅在进入房间或产生事件时计算从「上次离开」到「当前」的状态变化。

- 室外温湿度：由调用方提供（如从 OpenWeather API 拉取），格式 { "temperature": °C, "humidity": 0–1 }。
- 长时间未访问：若居民某房间 12h 未进入，下次进入时仍按完整 dt 推进该房间状态（室外趋近、设备持续影响等）。
"""
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

# 自然衰减系数（关窗极慢保温）
K_TEMPERATURE = 0.008
K_HUMIDITY = 0.005
# 开窗状态下的高速收敛系数（极快趋近室外）
K_TEMPERATURE_OPEN = 0.15
K_HUMIDITY_OPEN = 0.10
K_AIR_FRESHNESS_OPEN = 0.15

K_HYGIENE_DECAY = 0.002
HYGIENE_FLOOR = 0.4
HYGIENE_MIN = 0.2

# 室内温度合理范围（解除绝对天花板，允许酷暑严寒）
TEMPERATURE_MIN = 5.0
TEMPERATURE_MAX = 45.0

K_TEMPERATURE_SETPOINT = 0.03
AIR_FRESHNESS_DECAY = 0.001
AIR_FRESHNESS_FLOOR = 0.4
AIR_FRESHNESS_DEFAULT = 0.7
HUMIDITY_MIN = 0.15
HUMIDITY_MAX = 0.85


def _to_minutes(t: Any) -> float:
    """将 datetime 或 ISO 字符串转为「从当日 0 点起的分钟数」便于计算 dt。"""
    if isinstance(t, (int, float)):
        return float(t)
    if isinstance(t, str):
        try:
            dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
            return dt.hour * 60 + dt.minute + dt.second / 60.0
        except Exception:
            return 0.0
    if isinstance(t, datetime):
        return t.hour * 60 + t.minute + t.second / 60.0
    return 0.0


def _dt_minutes(last_update_time: Any, current_time: Any) -> float:
    """计算时间差（分钟）。若跨天则只按当日内分钟数差近似。"""
    t0 = _to_minutes(last_update_time)
    t1 = _to_minutes(current_time)
    if t1 >= t0:
        return t1 - t0
    return t1 + (24 * 60 - t0)


def get_outdoor_weather_at_time(
    outdoor_weather: Optional[Dict[str, Any]],
    current_time: Any,
) -> Dict[str, Any]:
    """
    根据当前时刻返回室外温湿度。支持两种格式：
    - 简单格式：{"temperature": °C, "humidity": 0–1}，直接返回。
    - 日变化格式：{"temperature_min", "temperature_max", "humidity_min", "humidity_max"}，
      按一日内时刻插值：约 5:00 最低、14:00 最高，使白天高夜间低。
    """
    out = outdoor_weather or {}
    if "temperature_min" in out and "temperature_max" in out:
        mins = _to_minutes(current_time)
        # 5:00 = 300 分钟为最低，14:00 = 840 分钟为最高，正弦插值
        import math
        phase = (mins - 300) / (24 * 60) * 2 * math.pi
        t_factor = (math.sin(phase) + 1) / 2.0
        T = float(out["temperature_min"]) + t_factor * (float(out["temperature_max"]) - float(out["temperature_min"]))
        H_min = float(out.get("humidity_min", 0.4))
        H_max = float(out.get("humidity_max", 0.7))
        H = H_min + (1 - t_factor) * (H_max - H_min)  # 湿度与温度大致反相
        return {"temperature": round(T, 2), "humidity": round(max(0, min(1, H)), 2)}
    return {"temperature": out.get("temperature", 24.0), "humidity": out.get("humidity", 0.5)}


def _matches_condition(device_state: Dict[str, Any], working_condition: Dict[str, str]) -> bool:
    """设备当前 state 是否满足 working_condition。空字符串或缺失的条件键视为「任意值」；键名大小写不敏感（兼容 LLM 输出 Power/Temperature）。"""
    state_norm = {str(key).strip().lower(): val for key, val in (device_state or {}).items()}
    for k, v in (working_condition or {}).items():
        v_str = str(v).strip() if v is not None else ""
        if v_str == "":
            continue  # 不要求该键，任意值均可
        state_val = state_norm.get(str(k).strip().lower()) if k else None
        if state_val is None:
            return False
        if str(state_val).lower() != v_str.lower():
            return False
    return True


def calculate_room_state(
    current_state: Dict[str, Any],
    last_update_time: Any,
    current_time: Any,
    active_devices: List[Dict[str, Any]],
    details_map: Dict[str, Any],
    outdoor_weather: Optional[Dict[str, Any]] = None,
    activity_deltas_per_minute: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    懒更新：根据时间差与当前设备状态，计算房间从 last_update_time 到 current_time 的环境状态。

    - current_state: 当前房间状态，至少含 temperature, humidity, hygiene, air_freshness（可选），以及 last_update_ts（可选，用于下次调用）。
    - last_update_time / current_time: datetime 或 ISO 字符串或分钟数。
    - active_devices: 当前在该房间内且处于「开启」等生效状态的设备列表，每项为 {"device_id": str, "state": {"power": "on", "mode": "cool", ...}}。
    - details_map: device_id/furniture_id -> 物品详情，需含 environmental_regulation 列表。
    - outdoor_weather: 室外温湿度（或日变化格式）。
    - activity_deltas_per_minute: 可选，人的活动带来的额外每分钟变化，如 {"temperature": 0.3, "humidity": 0.1, "air_freshness": -0.08}（烹饪/淋浴等）。

    **懒加载与长时间未访问**：若居民某房间长时间未进入（如 12h），本次更新仍按**完整 dt** 推进该房间状态（室外趋近、设备持续影响等）。

    算法:
    1. 自然衰减: T_new = T_old + k * (T_outdoor - T_old) * dt，湿度、清洁度、空气清新度类似。
    2. 设备干预: 同上。
    3. 返回新状态（含 last_update_ts 供下次懒更新使用）。
    """
    import copy
    state = copy.deepcopy(current_state)
    dt = max(0.0, _dt_minutes(last_update_time, current_time))

    # 默认值
    T = state.get("temperature", 24.0)
    H = state.get("humidity", 0.5)
    Hy = state.get("hygiene", 0.7)
    Af = state.get("air_freshness", AIR_FRESHNESS_DEFAULT)

    # 支持 outdoor_weather 按时刻日变化（temperature_min/max 等）
    outdoor = get_outdoor_weather_at_time(outdoor_weather, current_time)
    T_out = outdoor.get("temperature", T)
    H_out = outdoor.get("humidity", H)

    # --- 探测当前房间是否有敞开的窗户 ---
    is_window_open = False
    for dev in active_devices:
        did = str(dev.get("device_id") or dev.get("furniture_id") or "").lower()
        state_dict = dev.get("state") or dev.get("current_state") or {}
        if "window" in did and str(state_dict.get("open")).lower() == "open":
            is_window_open = True
            break

    # 动态赋予当前时间切片的收敛系数
    current_k_temp = K_TEMPERATURE_OPEN if is_window_open else K_TEMPERATURE
    current_k_hum = K_HUMIDITY_OPEN if is_window_open else K_HUMIDITY

    # 1. 自然衰减（动态边界）
    T = T + current_k_temp * (T_out - T) * dt
    H = H + current_k_hum * (H_out - H) * dt
    Hy = Hy - K_HYGIENE_DECAY * (Hy - HYGIENE_FLOOR) * dt
    Hy = max(HYGIENE_MIN, min(1.0, Hy))
    # 空气清新度逻辑
    if is_window_open:
        Af = Af + K_AIR_FRESHNESS_OPEN * (0.9 - Af) * dt
    else:
        Af = Af - AIR_FRESHNESS_DECAY * (Af - AIR_FRESHNESS_FLOOR) * dt
    Af_before_devices = Af

    # 2. 设备干预：温控设备有 temperature_set 时房间温度向设定值趋近，否则按 delta 变化；其余属性按 delta
    for dev in active_devices:
        device_id = dev.get("device_id") or dev.get("furniture_id")
        device_state = dev.get("state") or dev.get("current_state") or {}
        did = (device_id or "").strip() if isinstance(device_id, str) else device_id
        item = (details_map.get(did) or details_map.get(device_id)) if details_map else None
        if not item:
            continue
        regs = item.get("environmental_regulation") or []
        for reg in regs:
            if not isinstance(reg, dict):
                continue
            cond = reg.get("working_condition") or {}
            if not _matches_condition(device_state, cond):
                continue
            attr = (reg.get("target_attribute") or "").strip().lower()  # 兼容 LLM 输出大写 Temperature/Humidity
            delta = reg.get("delta_per_minute", 0.0)
            if attr == "temperature":
                # 优先用目标值做指数趋近。室温目标必须钳在室内合理范围，否则烤箱/灶台的烹饪温度(180/200°C)会误把室温推到荒谬值
                T_target = None
                if isinstance(reg.get("target_value"), (int, float)):
                    T_target = float(reg["target_value"])
                if T_target is None and isinstance(device_state.get("temperature_set"), (int, float)):
                    T_target = float(device_state["temperature_set"])
                if T_target is not None:
                    T_target = max(TEMPERATURE_MIN, min(TEMPERATURE_MAX, T_target))
                    T = T + K_TEMPERATURE_SETPOINT * (T_target - T) * dt
                else:
                    T = T + delta * dt
            elif attr == "humidity":
                H = H + delta * dt
            elif attr == "hygiene":
                Hy = Hy + delta * dt
                Hy = max(HYGIENE_MIN, min(1.0, Hy))
            elif attr == "air_freshness":
                Af = Af + delta * dt
                Af = max(0.0, min(1.0, Af))

    # 无显著通风/净化贡献时，高 air_freshness 缓慢衰减，避免密闭房间无人开窗/开净化器却自动升到 1.0
    if Af > 0.9 and (Af - Af_before_devices) < 0.01:
        Af = Af - 0.004 * (Af - 0.85) * dt
        Af = max(0.0, min(1.0, Af))

    # 3. 活动类型带来的额外影响（烹饪、淋浴等）
    act_d = activity_deltas_per_minute or {}
    if act_d:
        T = T + act_d.get("temperature", 0) * dt
        H = H + act_d.get("humidity", 0) * dt
        Hy = Hy + act_d.get("hygiene", 0) * dt
        Af = Af + act_d.get("air_freshness", 0) * dt
    Hy = max(HYGIENE_MIN, min(1.0, Hy))
    Af = max(0.0, min(1.0, Af))

    T = max(TEMPERATURE_MIN, min(TEMPERATURE_MAX, T))
    state["temperature"] = round(T, 2)
    state["humidity"] = round(max(HUMIDITY_MIN, min(HUMIDITY_MAX, H)), 2)
    state["hygiene"] = round(Hy, 2)
    state["air_freshness"] = round(max(0.0, min(1.0, Af)), 2)
    # 光照：保留传入的 light_level（0–1），若无则保持或由设备/时间简化推算
    if "light_level" not in state or state.get("light_level") is None:
        state["light_level"] = 0.5
    state["last_update_ts"] = current_time
    return state
