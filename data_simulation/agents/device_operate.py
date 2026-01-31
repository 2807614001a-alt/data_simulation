import os
import json
import sys
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
from settings.llm_utils import create_chat_llm

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

# Load environment variables
current_dir = Path(__file__).resolve().parent
dotenv_path = current_dir.parent / '.env'
load_dotenv(dotenv_path=dotenv_path)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==========================================
# 1. Output data models (Pydantic Models)
# ==========================================

class PatchItem(BaseModel):
    key: str = Field(description="State attribute name, e.g. 'power', 'temperature'")
    value: str = Field(description="State attribute value, e.g. 'on', '23' (string)")

class DevicePatch(BaseModel):
    timestamp: str = Field(description="When the patch happens (ISO timestamp)")
    device_id: str = Field(description="Device ID")
    patch_items: List[PatchItem] = Field(description="List of key/value state changes")

class EventDeviceState(BaseModel):
    patch_on_start: List[DevicePatch] = Field(description="Device changes at event start")
    patch_on_end: List[DevicePatch] = Field(description="Device changes at event end")

# ==========================================
# 2. Core prompt
# ==========================================

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

# ==========================================
# 3. Helpers
# ==========================================

_thread_local = threading.local()

def get_max_workers(total: int, env_name: str = "MAX_WORKERS", default: int = 12) -> int:
    """Decide parallelism based on workload and env settings."""
    if total <= 1:
        return 1
    env_value = os.getenv(env_name)
    if env_value:
        try:
            value = int(env_value)
            if value > 0:
                return min(value, total)
        except ValueError:
            pass
    cpu_count = os.cpu_count() or default
    return min(total, max(1, min(8, cpu_count)))

def get_thread_structured_llm():
    structured_llm = getattr(_thread_local, "structured_llm", None)
    if structured_llm is None:
        llm = create_chat_llm(model="gpt-4o", temperature=0.3)
        structured_llm = llm.with_structured_output(EventDeviceState)
        _thread_local.structured_llm = structured_llm
    return structured_llm

def load_settings_data(project_root: Path) -> Dict[str, Any]:
    """"""
    settings_path = project_root / "settings"
    data = {"house_details_map": {}}

    if (settings_path / "house_details.json").exists():
        with open(settings_path / "house_details.json", 'r', encoding='utf-8') as f:
            details_list = json.load(f)
            for item in details_list:
                item_id = item.get("furniture_id") or item.get("device_id")
                if item_id:
                    data["house_details_map"][item_id] = item
    return data

def get_device_context(target_ids: List[str], details_map: Dict) -> str:
    """?"""
    context = []
    for tid in target_ids:
        if tid in details_map:
            info = details_map[tid]
            context.append(f"{tid} ({info.get('name', 'Unknown')}) - Supports: {info.get('support_actions', [])}")
    return "; ".join(context)

def _normalize_patch_items(patch_items: List[PatchItem]) -> List[PatchItem]:
    allowed = {
        "power",
        "mode",
        "temperature",
        "brightness",
        "volume",
        "color",
        "fan_speed",
        "timer",
    }
    normalized: List[PatchItem] = []
    for item in patch_items:
        key = (item.key or "").strip().lower()
        value = str(item.value).strip()
        if key in {"open", "open_door", "door"}:
            normalized.append(PatchItem(key="state", value="open"))
            continue
        if key in {"close", "close_door"}:
            normalized.append(PatchItem(key="state", value="closed"))
            continue
        if key == "state":
            if value.lower() in {"open", "opened", "true", "on"}:
                normalized.append(PatchItem(key="state", value="open"))
                continue
            if value.lower() in {"close", "closed", "false", "off"}:
                normalized.append(PatchItem(key="state", value="closed"))
                continue
        if key in allowed:
            normalized.append(PatchItem(key=key, value=value))
    return normalized

def convert_patch_to_dict(patch_obj: DevicePatch) -> Dict:
    """?????? PatchItem ???? Dict ??????? JSON ????"""
    normalized = _normalize_patch_items(patch_obj.patch_items)
    kv_dict = {item.key: item.value for item in normalized}
    return {
        "timestamp": patch_obj.timestamp,
        "device_id": patch_obj.device_id,
        "patch": kv_dict  # ??? {"power": "on"}
    }

# ==========================================
# ==========================================

def run_event_chain_generation():
    project_root = Path(__file__).resolve().parent.parent
    
    settings = load_settings_data(project_root)
    events_file = project_root / "data" / "final_events_full_day.json"
    
    if not events_file.exists():
        events_file = project_root / "data" / "events.json"
        
    if not events_file.exists():
        logger.error("No events file found. Please run Layer 3 first.")
        return

    with open(events_file, 'r', encoding='utf-8') as f:
        events_list = json.load(f)

    logger.info(f"Generating Action Event Chain for {len(events_list)} events...")

    final_chain = []
    tasks = []

    for index, event in enumerate(events_list):
        target_ids = event.get("target_object_ids", [])
        is_outside = event.get("room_id") == "Outside"
        use_devices = len(target_ids) > 0 and not is_outside
        
        event_output = {
            "event_id": event.get("activity_id", f"evt_{index:03d}"),
            "room_id": event.get("room_id"),
            "start_time": event.get("start_time"),
            "end_time": event.get("end_time"),
            "description": event.get("description"),
            "use_devices": use_devices,
            "devices": target_ids,
            "layer5_device_state": {
                "patch_on_start": [],
                "patch_on_end": []
            }
        }

        final_chain.append(event_output)

        if use_devices:
            desc_short = event.get('description', '')[:20]
            logger.info(f"Analyzing devices for event [{index+1}]: {desc_short}...")
            tasks.append((index, event, target_ids))

    def _worker(task):
        index, event, target_ids = task
        device_context = get_device_context(target_ids, settings["house_details_map"])
        prompt = ChatPromptTemplate.from_template(DEVICE_STATE_GEN_PROMPT)
        chain = prompt | get_thread_structured_llm()
        try:
            result = chain.invoke({
                "description": event.get("description"),
                "start_time": event.get("start_time"),
                "end_time": event.get("end_time"),
                "target_devices": ", ".join(target_ids),
                "device_details": device_context
            })
            
            start_patches = [convert_patch_to_dict(p) for p in result.patch_on_start]
            end_patches = [convert_patch_to_dict(p) for p in result.patch_on_end]
            return index, start_patches, end_patches, None
        except Exception as e:
            return index, None, None, e

    if tasks:
        max_workers = get_max_workers(len(tasks))
        if max_workers > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                for index, start_patches, end_patches, error in executor.map(_worker, tasks):
                    if error:
                        logger.error(f"LLM error on event {index}: {error}")
                        continue
                    final_chain[index]["layer5_device_state"] = {
                        "patch_on_start": start_patches,
                        "patch_on_end": end_patches
                    }
        else:
            for task in tasks:
                index, start_patches, end_patches, error = _worker(task)
                if error:
                    logger.error(f"LLM error on event {index}: {error}")
                    continue
                final_chain[index]["layer5_device_state"] = {
                    "patch_on_start": start_patches,
                    "patch_on_end": end_patches
                }

    output_data = {"action_event_chain": final_chain}
    output_path = project_root / "data" / "action_event_chain.json"
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    logger.info(f"Generated {len(final_chain)} event chains.")
    logger.info(f"Result saved to: {output_path}")

if __name__ == "__main__":
    run_event_chain_generation()
