# -*- coding: utf-8 -*-
"""
环境物理模拟器：懒加载更新，仅在进入房间或产生事件时计算从「上次离开」到「当前」的状态变化。
"""
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

# 自然衰减系数（每分钟）：房间温度/湿度向室外趋近的速率
K_TEMPERATURE = 0.008
K_HUMIDITY = 0.005
# 清洁度自然衰减（无人打扫时缓慢下降），趋向 0.4
K_HYGIENE_DECAY = 0.002
HYGIENE_FLOOR = 0.4


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


def _matches_condition(device_state: Dict[str, Any], working_condition: Dict[str, str]) -> bool:
    """设备当前 state 是否满足 working_condition（所有键值一致才为 True）。"""
    for k, v in (working_condition or {}).items():
        if str(device_state.get(k)).lower() != str(v).lower():
            return False
    return True


def calculate_room_state(
    current_state: Dict[str, Any],
    last_update_time: Any,
    current_time: Any,
    active_devices: List[Dict[str, Any]],
    details_map: Dict[str, Any],
    outdoor_weather: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    懒更新：根据时间差与当前设备状态，计算房间从 last_update_time 到 current_time 的环境状态。

    - current_state: 当前房间状态，至少含 temperature, humidity, hygiene（可选），以及 last_update_ts（可选，用于下次调用）。
    - last_update_time / current_time: datetime 或 ISO 字符串或分钟数。
    - active_devices: 当前在该房间内且处于「开启」等生效状态的设备列表，每项为 {"device_id": str, "state": {"power": "on", "mode": "cool", ...}}。
    - details_map: device_id/furniture_id -> 物品详情，需含 environmental_regulation 列表。
    - outdoor_weather: {"temperature": float, "humidity": float}，当前室外温湿度；若为 None 则不做自然衰减的室外趋近。

    算法:
    1. 自然衰减: T_new = T_old + k * (T_outdoor - T_old) * dt，湿度和清洁度类似。
    2. 设备干预: 对每个 active_device 的 environmental_regulation，若 working_condition 匹配则 state[attr] += delta_per_minute * dt。
    3. 返回新状态（含 last_update_ts 供下次懒更新使用）。
    """
    import copy
    state = copy.deepcopy(current_state)
    dt = max(0.0, _dt_minutes(last_update_time, current_time))

    # 默认值
    T = state.get("temperature", 24.0)
    H = state.get("humidity", 0.5)
    Hy = state.get("hygiene", 0.7)

    outdoor = outdoor_weather or {}
    T_out = outdoor.get("temperature", T)
    H_out = outdoor.get("humidity", H)

    # 1. 自然衰减
    T = T + K_TEMPERATURE * (T_out - T) * dt
    H = H + K_HUMIDITY * (H_out - H) * dt
    Hy = Hy - K_HYGIENE_DECAY * (Hy - HYGIENE_FLOOR) * dt
    Hy = max(0.0, min(1.0, Hy))

    # 2. 设备干预
    for dev in active_devices:
        device_id = dev.get("device_id") or dev.get("furniture_id")
        device_state = dev.get("state") or dev.get("current_state") or {}
        item = details_map.get(device_id) if details_map else None
        if not item:
            continue
        regs = item.get("environmental_regulation") or []
        for reg in regs:
            if not isinstance(reg, dict):
                continue
            cond = reg.get("working_condition") or {}
            if not _matches_condition(device_state, cond):
                continue
            attr = reg.get("target_attribute")
            delta = reg.get("delta_per_minute", 0.0)
            if attr == "temperature":
                T = T + delta * dt
            elif attr == "humidity":
                H = H + delta * dt
            elif attr == "hygiene":
                Hy = Hy + delta * dt
                Hy = max(0.0, min(1.0, Hy))

    state["temperature"] = round(T, 2)
    state["humidity"] = round(max(0.0, min(1.0, H)), 2)
    state["hygiene"] = round(Hy, 2)
    state["last_update_ts"] = current_time
    return state
