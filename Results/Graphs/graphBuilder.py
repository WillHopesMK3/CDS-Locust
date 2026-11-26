import sys
import pandas as pd
import matplotlib.pyplot as plt

prefix = sys.argv[1]
stats = pd.read_csv(f"{prefix}_stats.csv")
history = pd.read_csv(f"{prefix}_stats_history.csv")
stats["Name"] = stats["Name"].str.split("/").str[-1]

history["Timestamp"] = (
    pd.to_datetime(history["Timestamp"], unit="s", utc=True)
    .dt.tz_convert("Europe/London")
)
history.to_csv(f"{prefix}_stats_history.csv", index=False)

ax = stats.plot.barh(
    x="Name",
    y=["50%", "95%", "99%"],
    color=["green", "orange", "red"],
    title="Response time by endpoint for " + prefix.split("/")[-1] + " (ms)",
    figsize=(12, 6),
)
ax.invert_yaxis()
ax.set_ylabel("")
ax.margins(x=0.1)

names = stats["Name"].tolist()
if "Aggregated" in names:
    agg = names.index("Aggregated")
    for container in ax.containers:
        container[agg].set_hatch("//")
        container[agg].set_edgecolor("black")

for container in ax.containers:
        #ax.bar_label(container, label_type="center", fmt="%.0f", color="white")
        ax.bar_label(container, label_type="edge", fmt="%.0f", padding=3)

plt.tight_layout()
plt.savefig("../Results/Graphs/Endpoints.png")