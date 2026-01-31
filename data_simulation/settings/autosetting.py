import subprocess
import sys
import os


def run_script(script_name):
    """Run a Python script in the current interpreter."""
    print(f"--- Running: {script_name} ---")
    try:
        subprocess.run([sys.executable, script_name], check=True)
        print(f"Done: {script_name}\n")
    except subprocess.CalledProcessError:
        print(f"Error: {script_name} failed.")
        if script_name == "profile_generator.py":
            existing_profile = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profile.json")
            if os.path.exists(existing_profile):
                print("Warning: profile_generator failed, but profile.json exists; continuing.")
                return
        sys.exit(1)
    except FileNotFoundError:
        print(f"Error: file not found: {script_name}")
        sys.exit(1)


def main():
    pipeline = [
        "profile_generator.py",
        "profile2layout.py",
        "layout_check.py",
        "layout2details.py",
        "details2interaction.py",
    ]

    current_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(current_dir)

    print("Starting settings pipeline...\n" + "=" * 30)
    for script in pipeline:
        run_script(script)
    print("=" * 30 + "\nAll tasks completed.")


if __name__ == "__main__":
    main()
