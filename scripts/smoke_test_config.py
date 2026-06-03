from live.config import load_config
cfg = load_config("live/configs/exp1_ml_us.yaml")
name = cfg["strategy"]["name"]
print(f"Config loaded: strategy={name}")
