import subprocess
import sys
import os

def run_script(script_name, work_dir):
    """
    在指定目录下运行脚本
    """
    # 拼接文件的完整绝对路径
    script_path = os.path.join(work_dir, script_name)

    # 1. 检查文件是否存在（使用绝对路径检查）
    if not os.path.exists(script_path):
        print(f"❌ 错误: 在 {work_dir} 下找不到文件 {script_name}")
        return False

    print(f"🚀 正在启动: {script_name} ...")
    
    try:
        # 2. 关键修改：添加 cwd=work_dir 参数
        # 这会让子脚本觉得它是直接在该文件夹下运行的，避免找不到它依赖的其他文件
        result = subprocess.run(
            [sys.executable, script_name], 
            cwd=work_dir,  # <--- 强制设置工作目录为脚本所在文件夹
            check=True
        )
        print(f"✅ {script_name} 运行完成。\n")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ {script_name} 运行失败，退出代码: {e.returncode}")
        return False
    except Exception as e:
        print(f"❌ 发生未知错误: {e}")
        return False

def main():
    # 获取当前这个 main.py 文件所在的绝对路径目录
    # 无论你在哪个终端路径下运行，这一行都能找到正确的文件夹
    current_base_dir = os.path.dirname(os.path.abspath(__file__))

    # 按顺序定义要运行的文件列表
    scripts_to_run = [
        "planning.py",
        "event.py",
        "device_operate.py"  # <--- 已修正文件名（去掉末尾的 r）
    ]

    print(f"📂 工作目录已锁定为: {current_base_dir}\n")

    for script in scripts_to_run:
        # 将目录路径传给执行函数
        success = run_script(script, current_base_dir)
        
        if not success:
            print("🛑 由于上一步失败，程序终止。")
            break
    
    print("🏁 所有任务处理完毕。")

if __name__ == "__main__":
    main()