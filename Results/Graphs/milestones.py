import sys
import pandas as pd

prefix = sys.argv[1]
log = pd.read_csv(f"{prefix}_requests.csv", parse_dates=["timestamp"])
log = log.sort_values("request_no")
log["outcome"] = (log["category"] == "OK").map({True: "success", False: "failure"})

rows = []
for (endpoint, outcome), g in log.groupby(["endpoint", "outcome"]):
    first, last = g.iloc[0], g.iloc[-1]
    rows.append({
        "endpoint": endpoint,
        "outcome": outcome,
        "count": len(g),
        "first_time": first["timestamp"],
        "first_request_no": first["request_no"],
        "first_endpoint_request_no": first["endpoint_request_no"],
        "first_users": first["users"],
        "first_category": first["category"],
        "last_time": last["timestamp"],
        "last_request_no": last["request_no"],
        "last_endpoint_request_no": last["endpoint_request_no"],
        "last_users": last["users"],
        "last_category": last["category"],
    })

milestones = pd.DataFrame(rows)
milestones.to_csv(f"{prefix}_milestones.csv", index=False)