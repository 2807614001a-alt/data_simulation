"""
Settings 管线最终校验：在 details2interaction 之后运行。
检查并修复：interaction_rules 中 applicable_objects 的 name 均在 details 中存在、无前导/尾随空格、无不合理 action-object 组合。
"""
import json
import os
import sys

def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(data: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def main():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    details_path = os.path.join(current_dir, "house_details.json")
    rules_path = os.path.join(current_dir, "interaction_rules.json")

    if not os.path.exists(details_path):
        print("[final_validation] 跳过：house_details.json 不存在")
        return
    if not os.path.exists(rules_path):
        print("[final_validation] 跳过：interaction_rules.json 不存在")
        return

    details = load_json(details_path)
    data = load_json(rules_path)
    rules = data.get("interaction_rules", data) if isinstance(data, dict) else data
    if not isinstance(rules, list):
        print("[final_validation] interaction_rules 格式异常，跳过")
        return

    details_by_name = {}
    for item in details:
        name = (item.get("name") or "").strip()
        if name:
            details_by_name[name] = item
    changed = False
    report = {"missing_names": [], "stripped": 0, "invalid_removed": 0, "support_actions_filtered": 0}

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        action = (rule.get("action") or "").strip()
        action_lower = action.lower()
        objs = rule.get("applicable_objects") or []
        new_objs = []
        for o in objs:
            s = str(o).strip()
            if s != o:
                report["stripped"] += 1
                changed = True
            if s not in details_by_name:
                report["missing_names"].append((action, s))
                report["invalid_removed"] += 1
                changed = True
                continue
            support = details_by_name[s].get("support_actions") or []
            if action in support or action_lower in [a.lower() for a in support]:
                new_objs.append(s)
            else:
                report["support_actions_filtered"] += 1
                changed = True
        rule["applicable_objects"] = new_objs
        if new_objs != objs or any(str(o).strip() != o for o in objs):
            changed = True
        if not new_objs:
            rule["_drop"] = True

    rules[:] = [r for r in rules if not r.get("_drop")]
    for r in rules:
        r.pop("_drop", None)

    if changed:
        save_json({"interaction_rules": rules}, rules_path)
        print("[final_validation] 已修正并写回 interaction_rules.json")
    if report["missing_names"]:
        print(f"  - 移除了 {len(report['missing_names'])} 个不在 details 中的 name")
    if report["stripped"]:
        print(f"  - 去除了 {report['stripped']} 处前导/尾随空格")
    if report["support_actions_filtered"]:
        print(f"  - 按 support_actions 正向过滤：移除了 {report['support_actions_filtered']} 个不支撑该动作的物品")
    if not changed:
        print("[final_validation] 校验通过，无需修改")

if __name__ == "__main__":
    main()
