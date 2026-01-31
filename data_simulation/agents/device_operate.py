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

# 鍔犺浇鐜鍙橀噺
current_dir = Path(__file__).resolve().parent
dotenv_path = current_dir.parent / '.env'
load_dotenv(dotenv_path=dotenv_path)

# 閰嶇疆鏃ュ織
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==========================================
# 1. 瀹氫箟杈撳嚭鏁版嵁缁撴瀯 (Pydantic Models)
# ==========================================

class PatchItem(BaseModel):
    key: str = Field(description="鐘舵€佸睘鎬у悕, e.g. 'power', 'temperature'")
    value: str = Field(description="鐘舵€佸睘鎬у€? e.g. 'on', '23'. 缁熶竴杞负瀛楃涓?")

class DevicePatch(BaseModel):
    timestamp: str = Field(description="璇ユ搷浣滃彂鐢熺殑鏃堕棿鐐?(ISO鏍煎紡)")
    device_id: str = Field(description="璁惧ID")
    # 銆愪慨澶嶃€戜娇鐢?List[PatchItem] 鏇夿唬 Dict[str, Any] 閬垮厤 OpenAI 400 閿欒
    patch_items: List[PatchItem] = Field(description="鐘舵€佸彉鏇村唴瀹瑰垪琛?")

class EventDeviceState(BaseModel):
    patch_on_start: List[DevicePatch] = Field(description="浜嬩欢寮€濮嬫椂鍒诲彂鐢熺殑璁惧鍙樻洿")
    patch_on_end: List[DevicePatch] = Field(description="浜嬩欢缁撴潫鏃跺埢鍙戠敓鐨勮澶囧彉鏇?")

# ==========================================
# 2. 鏍稿績鎻愮ず璇?(Prompt)
# ==========================================

DEVICE_STATE_GEN_PROMPT = """
浣犳槸涓€涓櫤鑳藉灞呰涓哄垎鏋愬櫒銆?
璇锋牴鎹敤鎴风殑銆愯涓轰簨浠躲€戯紝鎺ㄦ柇璁惧搴旇鍦ㄣ€愬紑濮嬨€戝拰銆愮粨鏉熴€戞椂鍙戠敓浠€涔堢姸鎬佸彉鍖栥€?

## 杈撳叆鏁版嵁
- **Event**: {description}
- **Time**: {start_time} 鑷?{end_time}
- **Devices**: {target_devices}
- **Reference**: {device_details}

## 浠诲姟瑕佹眰
1. **Patch on Start**: 浜嬩欢寮€濮嬫椂锛岃澶囩姸鎬佸浣曟敼鍙橈紵(渚嬪锛氭墦寮€鐢垫簮銆佽缃ā寮忋€佹墦寮€闂?
2. **Patch on End**: 浜嬩欢缁撴潫鏃讹紝璁惧鐘舵€佸浣曟敼鍙橈紵(渚嬪锛氬叧闂數婧愩€佸叧闂棬)銆傚鏋滆涓轰笉闇€瑕佸叧闂?濡傛寔缁繍琛?锛屽垯鍒楄〃涓虹┖銆?
3. **Timestamp**: 
   - Start Patch 鐨勬椂闂存埑蹇呴』鏄?{start_time}銆?
   - End Patch 鐨勬椂闂存埑蹇呴』鏄?{end_time}銆?
4. **鏍煎紡瑙勫垯**: 
   - 鐢变簬杈撳嚭闄愬埗锛岃灏嗙姸鎬佸彉鍖栨媶瑙ｄ负 key-value 鍒楄〃 (patch_items)銆?
   - 渚嬪锛歚{{"key": "power", "value": "on"}}`

## 杈撳嚭绀轰緥
Event: 鍋氶キ (Stove)
Time: 08:00 - 08:30
Result:
{{
  "patch_on_start": [
    {{
      "timestamp": "08:00", 
      "device_id": "stove", 
      "patch_items": [
        {{"key": "power", "value": "on"}}, 
        {{"key": "mode", "value": "cook"}}
      ]
    }}
  ],
  "patch_on_end": [
    {{
      "timestamp": "08:30", 
      "device_id": "stove", 
      "patch_items": [
        {{"key": "power", "value": "off"}}
      ]
    }}
  ]
}}
"""

# ==========================================
# 3. 杈呭姪鍑芥暟
# ==========================================

_thread_local = threading.local()

def get_max_workers(total: int, env_name: str = "MAX_WORKERS", default: int = 4) -> int:
    """
    鏍规嵁鏁版嵁閲忎笌鐜鍙橀噺鍐冲畾骞惰搴?
    """
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
    """鍔犺浇閰嶇疆鏁版嵁"""
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
    """鑾峰彇娑夊強璁惧鐨勭畝瑕佷俊鎭?"""
    context = []
    for tid in target_ids:
        if tid in details_map:
            info = details_map[tid]
            context.append(f"{tid} ({info.get('name', 'Unknown')}) - Supports: {info.get('support_actions', [])}")
    return "; ".join(context)

def convert_patch_to_dict(patch_obj: DevicePatch) -> Dict:
    """銆愬悗澶勭悊銆戝皢 PatchItem 鍒楄〃杞洖 Dict 鏍煎紡锛岀鍚堟渶缁?JSON 杈撳嚭瑕佹眰"""
    kv_dict = {item.key: item.value for item in patch_obj.patch_items}
    return {
        "timestamp": patch_obj.timestamp,
        "device_id": patch_obj.device_id,
        "patch": kv_dict  # 杞崲鍥?{"power": "on"}
    }

# ==========================================
# 4. 涓婚€昏緫
# ==========================================

def run_event_chain_generation():
    project_root = Path(__file__).resolve().parent.parent
    
    settings = load_settings_data(project_root)
    events_file = project_root / "data" / "final_events_full_day.json"
    
    if not events_file.exists():
        events_file = project_root / "data" / "events.json"
        
    if not events_file.exists():
        logger.error("鉂?No events file found. Please run Layer 3 first.")
        return

    with open(events_file, 'r', encoding='utf-8') as f:
        events_list = json.load(f)

    logger.info(f"馃殌 Generating Action Event Chain for {len(events_list)} events...")

    final_chain = []
    tasks = []

    for index, event in enumerate(events_list):
        target_ids = event.get("target_object_ids", [])
        is_outside = event.get("room_id") == "Outside"
        # 绠€鍗曡繃婊わ細鍙湁鏄庣‘娑夊強璁惧涓旈潪澶栧嚭浜嬩欢鎵嶅鐞?
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
            logger.info(f"鈿?Analyzing Devices for Event [{index+1}]: {desc_short}...")
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
            
            # 銆愬叧閿慨澶嶃€? 鎵嬪姩灏?LLM 杈撳嚭鐨?List[Item] 缁撴瀯杞洖 Dict 缁撴瀯
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
                        logger.error(f"鉂?LLM Error on event {index}: {error}")
                        continue
                    final_chain[index]["layer5_device_state"] = {
                        "patch_on_start": start_patches,
                        "patch_on_end": end_patches
                    }
        else:
            for task in tasks:
                index, start_patches, end_patches, error = _worker(task)
                if error:
                    logger.error(f"鉂?LLM Error on event {index}: {error}")
                    continue
                final_chain[index]["layer5_device_state"] = {
                    "patch_on_start": start_patches,
                    "patch_on_end": end_patches
                }

    # 杈撳嚭缁撴灉
    output_data = {"action_event_chain": final_chain}
    output_path = project_root / "data" / "action_event_chain.json"
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    logger.info(f"鉁?Generated {len(final_chain)} event chains.")
    logger.info(f"馃搨 Result saved to: {output_path}")

if __name__ == "__main__":
    run_event_chain_generation()
