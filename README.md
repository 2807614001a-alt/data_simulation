# data_simulation
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
- SIM_RANDOM_SEED: random seed for reproducible runs
- SIM_NORMAL_WEIGHT: probability weight for Normal (default: 0.7)
- SIM_PERTURBED_WEIGHT: probability weight for Perturbed (default: 0.2)
- SIM_CRISIS_WEIGHT: probability weight for Crisis (default: 0.1)

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

Notes
-----
- If you change prompts or validation rules, re-run the simulation to regenerate outputs.
- If a run is interrupted, delete the affected day’s outputs and re-run for a clean result.
