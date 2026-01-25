import subprocess
import sys
import os

def run_script(script_name):
    """运行指定的 Python 脚本并处理结果"""
    print(f"--- 正在执行: {script_name} ---")
    try:
        # 使用 sys.executable 确保使用当前环境的 Python 解释器
        result = subprocess.run([sys.executable, script_name], check=True)
        print(f"成功完成: {script_name}\n")
    except subprocess.CalledProcessError as e:
        print(f"错误: {script_name} 执行失败。")
        sys.exit(1) # 停止后续所有任务
    except FileNotFoundError:
        print(f"错误: 未找到文件 {script_name}，请确保它在同目录下。")
        sys.exit(1)

def main():
    # 定义任务流水线顺序
    pipeline = [
        "profile_generator.py",
        "profile2layout.py",
        "layout_check.py",
        "layout2details.py",
        "details2interaction.py"
    ]

    # 获取当前脚本所在目录，确保路径正确
    current_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(current_dir)

    print("开始执行自动化配置流水线...\n" + "="*30)

    for script in pipeline:
        run_script(script)

    print("="*30 + "\n所有任务已成功按顺序完成！")

if __name__ == "__main__":
    main()