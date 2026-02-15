"""
一键运行：先执行 settings 流水线（autosetting），再执行多日仿真（n_day_simulation）。
请在本项目根目录或 data_simulation 目录下执行：python run_all.py 或 python data_simulation/run_all.py
"""
import os
import subprocess
import sys


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base)

    print("Step 1: Running settings pipeline (autosetting)...\n")
    subprocess.run([sys.executable, "settings/autosetting.py"], check=True, cwd=base)

    print("\nStep 2: Running n-day simulation...\n")
    subprocess.run([sys.executable, "agents/n_day_simulation.py"], check=True, cwd=base)

    print("\nAll done.")


if __name__ == "__main__":
    main()
