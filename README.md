Data Simulation Pipeline
========================

Overview
--------
This project generates multi-day human activity simulations, decomposes them into events,
and (optionally) produces device state changes over time.

Main entrypoint
---------------
Run the simulation:

```powershell
C:/Users/wanghaocheng123/.conda/envs/lang/python.exe c:/Users/wanghaocheng123/Desktop/data_simulation/agents/14day_simulation.py
```

Environment variables
---------------------
- SIM_DAYS: number of days to simulate (default: 14)
- SIM_START_DATE: start date in YYYY-MM-DD (default: today)
- SIM_RUN_EVENTS: whether to generate events/device chains (1=on, 0=off)
- SIM_RUN_EVALUATION: run evaluator after simulation (1=on, 0=off)
- SIM_RANDOM_SEED: random seed for reproducible runs
- SIM_NORMAL_WEIGHT: probability weight for Normal (default: 0.7)
- SIM_PERTURBED_WEIGHT: probability weight for Perturbed (default: 0.2)
- SIM_CRISIS_WEIGHT: probability weight for Crisis (default: 0.1)
- SIM_RANDOM_EVENT_MEAN: mean of daily random event count for Perturbed/Crisis (default: 1.0)
- SIM_RANDOM_EVENT_STD: std dev of daily random event count for Perturbed/Crisis (default: 0.5)
- SIM_RANDOM_EVENT_MAX: max daily random event count for Perturbed/Crisis (default: 3)

Force Day1 state (for testing)
------------------------------
You can temporarily force Day1 to a specific state:

```powershell
set SIM_FORCE_DAY1_STATE=Perturbed
C:/Users/wanghaocheng123/.conda/envs/lang/python.exe c:/Users/wanghaocheng123/Desktop/data_simulation/agents/14day_simulation.py
```

Valid values: Normal | Perturbed | Crisis

Pipeline stages
---------------
1) Profile & environment (settings/)
- profile.json
- house_layout.json
- house_details.json

2) Daily activity planning
- data/activity_dayX.json

3) Event decomposition
- data/events_dayX.json

4) Device state chains
- data/action_event_chain_dayX.json

5) Daily summaries
- data/previous_day_summary_dayX.json

Output files
------------
All outputs are written under `data/`.
In addition, the evaluator writes:
- data/evaluation_report.json


Notes
-----
- If you change prompts or validation rules, re-run the simulation to regenerate outputs.
- If a run is interrupted, delete the affected day's outputs and re-run for a clean result.

Daily time window
-----------------
Each day runs from the profile's wake time to bedtime (not 00:00-23:59).
The planner must start with a wake activity and end with a sleep activity.

Evaluation Prompt (Days 1-3)
----------------------------
Use this prompt to evaluate whether the system behaves correctly over the first three days.
It focuses on: (1) special events when Perturbed/Crisis, (2) alignment with persona/profile,
and (3) cross-day influence, with explicit checks for sleep/meal timing and fixed weekly items.

```
You are a multi-day simulation auditor. Evaluate the first three days of outputs.
Focus on:
1) Special events: when simulation_state is Perturbed/Crisis, are there concrete event outputs?
2) Persona fit: do activities match the profile (routines, values, preferences)?
3) Cross-day influence: does Day N affect Day N+1 logically?

Inputs:
- Profile:
{profile_json}

- Simulation Contexts (use current_date to map days):
Day1: {simulation_context_day1_json}
Day2: {simulation_context_day2_json}
Day3: {simulation_context_day3_json}

- Activities:
Day1: {activity_day1_json}
Day2: {activity_day2_json}
Day3: {activity_day3_json}

- Events or Action Chains (pick one):
Day1: {events_or_action_event_chain_day1_json}
Day2: {events_or_action_event_chain_day2_json}
Day3: {events_or_action_event_chain_day3_json}

- Previous Day Summaries:
Day1: {previous_day_summary_day1_json}
Day2: {previous_day_summary_day2_json}
Day3: {previous_day_summary_day3_json}

Output (JSON):
{
  "overall_pass": true/false,
  "per_day": [
    {
      "day": 1,
      "special_event_ok": true/false,
      "persona_ok": true/false,
      "cross_day_ok": true/false,
      "issues": ["..."]
    }
  ],
  "summary": "one-sentence conclusion"
}
```

Evaluator checklist (use as strict criteria):
- Special events:
  - If simulation_state is Perturbed/Crisis, at least one activity/event must clearly show an abnormal event.
    Acceptable cues include: event marker, sudden incident, accident, breakdown, illness, cold, injury, dizziness, unwell, cancel, delay, interrupt, emergency, plan adjustment.
  - The event must materially change the schedule (cancel/push/shorten/relocate).
- Persona fit:
  - Sleep start/end and wake time align with profile (workday vs weekend).
  - Meals align with breakfast/lunch/dinner time windows.
  - Fixed weekly items (meetings, planned outings) appear on the correct day.
- Cross-day influence:
  - If Day N has abnormal load (late sleep, illness, crisis), Day N+1 reflects adjustment.
  - If Day N is normal, Day N+1 should not show unexplained abnormality.
