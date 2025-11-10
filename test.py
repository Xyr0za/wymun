import json

with open("delegates.json") as file:
    dele = json.load(file)
    DELEGATES = list(dele["Delegates"].keys())
    print(DELEGATES)
