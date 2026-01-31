import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from typing import List, Dict

from llm_utils import create_chat_llm
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field

# --- 环境配置 ---
load_dotenv()
current_dir = Path(__file__).resolve().parent
dotenv_path = current_dir.parent / '.env'
load_dotenv(dotenv_path=dotenv_path)

# --- 1. 定义数据结构 (Schema) ---

# 1.1 最内层的环境状态
class EnvironmentState(BaseModel):
    temperature: float = Field(description="温度 (摄氏度)")
    humidity: float = Field(description="湿度 (0.0 - 1.0)")
    light_level: float = Field(description="光照强度 (0.0 - 1.0)")
    noise_level: float = Field(description="噪音水平 (0.0 - 1.0)")

# 1.2 房间详细信息
class RoomInfo(BaseModel):
    room_type: str = Field(description="房间中文类型，例如：客厅、主卧")
    area_sqm: float = Field(description="房间面积 (平方米)")
    # 注意：这里根据你的新要求，改成了字符串列表 (List[str])
    furniture: List[str] = Field(description="家具ID列表，如 ['sofa_001', 'table_001']")
    devices: List[str] = Field(description="设备ID列表，如 ['ac_001', 'light_001']")
    environment_state: EnvironmentState = Field(description="环境传感器状态")

# 1.3 根结构：这是一个动态字典，Key是房间ID (如 living_room)，Value是 RoomInfo
# 由于 Key 是动态的，我们用一个包装类来辅助 Parser 理解，或者在 Prompt 里强调 Map 结构
class HouseSnapshot(BaseModel):
    # 使用 Dict 来表示 key 是动态的字符串
    rooms: Dict[str, RoomInfo] = Field(description="房间ID到房间详情的映射字典")

# --- 2. 读取 Profile (保持不变) ---
def get_profile_data():
    # 模拟读取，实际使用时请确保 profile.json 存在
    return {"name": "示例家庭", "needs": ["喜欢看电影", "需要安静的睡眠环境", "智能家居控"]}

# --- 3. 运行设计 Agent ---
def run_architect_agent_json():
    user_profile = get_profile_data()
    profile_str = json.dumps(user_profile, ensure_ascii=False)

    # 初始化解析器
    # 注意：虽然我们需要输出类似 {"living_room": ...} 的字典
    # 但为了让 Parser 稳定工作，我们告诉它输出 HouseSnapshot 结构，
    # 也就是 {"rooms": {"living_room": ...}}，最后我们在 Python 代码里把 "rooms"这一层剥掉即可，
    # 或者直接在 Prompt 里强行约束。这里采用更稳定的“剥离法”。
    parser = JsonOutputParser(pydantic_object=HouseSnapshot)

    # --- Prompt ---
    template = """
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
       - 严禁生成“样板房”。**必须**包含维持人类生存的基础设施，无论人设如何：
         - 清洁: `washing_machine_001` (洗衣机), `laundry_rack_001` (晾衣架/烘干机), `trash_can_001` (垃圾桶), `broom_001` (扫把)。
         - 收纳: `shoe_cabinet_001` (鞋柜), `wardrobe_001` (衣柜)。
         - 舒适: `curtain_001` (窗帘), `rug_001` (地毯)。
    4. **特殊需求响应**:
       - 检查 Preferences 和 Routines。
       - **宠物**: 如果养猫/狗，必须生成 `cat_litter_box`, `cat_tree`, `dog_bed` 等。
       - **运动**: 如果有瑜伽/健身习惯，必须在客厅或阳台生成 `yoga_mat` 或 `treadmill`。

    **生成任务**:
    生成一个 JSON 对象。
    - **Key**: 房间英文 ID (如 living_room, master_bedroom, study_room, kitchen, bathroom)。根据人设决定房间类型。
    - **Value**: 包含 `room_type`, `area_sqm`, `furniture` (ID列表), `devices` (ID列表), `environment_state`。

    **ID命名严格规范**:
    - 格式: `物品英文名_数字编号` (例: `gaming_pc_001`, `yoga_mat_001`)。
    - **拒绝通用词**: 尽量使用具体名称（如用 `ergonomic_chair_001` 而不是 `chair_001`，如果用户长时间坐着工作）。

    **环境状态设定**:
    - 根据用户的 `routines` 设定初始状态。例如：如果用户习惯熬夜，卧室的遮光窗帘可能是 `closed`。

    {format_instructions}
    """

    prompt = ChatPromptTemplate.from_template(template)
    
    # 注入格式说明
    prompt = prompt.partial(format_instructions=parser.get_format_instructions())

    llm = create_chat_llm(model="gpt-4", temperature=0.7)
    chain = prompt | llm | parser

    print(">>> 正在生成环境状态快照...")
    
    # 执行
    result = chain.invoke({
        "profile_context": profile_str
    })

    # --- 数据清洗 ---
    # 如果 Parser 因为 HouseSnapshot 的定义多包了一层 "rooms"，我们需要把它解开
    # 如果 LLM 直接足够聪明输出 {"living_room": ...}，则直接返回
    if "rooms" in result and isinstance(result["rooms"], dict):
        return result["rooms"]
    
    return result

# --- 4. 保存 ---
def save_layout_to_file(data, filename="house_layout.json"):
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n[成功] 文件已保存至: {output_path}")

if __name__ == "__main__":
    snapshot_json = run_architect_agent_json()
    
    print("\n=== 生成的 JSON 结构 ===")
    print(json.dumps(snapshot_json, ensure_ascii=False, indent=2))
    
    save_layout_to_file(snapshot_json)
