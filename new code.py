import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report

# ============================================================
# 1. GENERATE INDUSTRIAL SENSOR DATA
# ============================================================

np.random.seed(42)

N = 1000

data = pd.DataFrame({
    "Temperature": np.random.normal(70, 10, N),
    "Vibration": np.random.normal(4, 1.5, N),
    "Pressure": np.random.normal(100, 8, N),
    "RPM": np.random.normal(1500, 120, N),
    "Current": np.random.normal(30, 5, N)
})

# Prevent unrealistic negative values
data["Temperature"] = data["Temperature"].clip(30, 120)
data["Vibration"] = data["Vibration"].clip(0, 10)
data["Pressure"] = data["Pressure"].clip(50, 150)
data["RPM"] = data["RPM"].clip(500, 2000)
data["Current"] = data["Current"].clip(5, 60)


# ============================================================
# 2. CALCULATE INDIVIDUAL SENSOR HEALTH SCORES
# ============================================================

def temperature_score(x):
    """
    Ideal temperature approximately 50-75 C.
    """
    if 50 <= x <= 75:
        return 100
    elif x < 50:
        return max(0, 100 - (50 - x) * 2)
    else:
        return max(0, 100 - (x - 75) * 4)


def vibration_score(x):
    """
    Lower vibration is generally healthier.
    """
    return max(0, 100 - (x * 10))


def pressure_score(x):
    """
    Ideal pressure approximately 90-110.
    """
    deviation = abs(x - 100)
    return max(0, 100 - deviation * 5)


def rpm_score(x):
    """
    Ideal RPM approximately 1400-1600.
    """
    deviation = abs(x - 1500)

    if deviation <= 100:
        return 100

    return max(0, 100 - (deviation - 100) * 0.5)


def current_score(x):
    """
    Ideal current approximately 20-40 A.
    """
    if 20 <= x <= 40:
        return 100

    if x < 20:
        return max(0, 100 - (20 - x) * 4)

    return max(0, 100 - (x - 40) * 4)


data["Temperature_Score"] = data["Temperature"].apply(temperature_score)
data["Vibration_Score"] = data["Vibration"].apply(vibration_score)
data["Pressure_Score"] = data["Pressure"].apply(pressure_score)
data["RPM_Score"] = data["RPM"].apply(rpm_score)
data["Current_Score"] = data["Current"].apply(current_score)


# ============================================================
# 3. CALCULATE OVERALL EQUIPMENT HEALTH SCORE
# ============================================================

# Weights can be changed depending on the machine.
WEIGHTS = {
    "Temperature_Score": 0.20,
    "Vibration_Score": 0.30,
    "Pressure_Score": 0.15,
    "RPM_Score": 0.15,
    "Current_Score": 0.20
}

data["Health_Score"] = (
    data["Temperature_Score"] * WEIGHTS["Temperature_Score"] +
    data["Vibration_Score"] * WEIGHTS["Vibration_Score"] +
    data["Pressure_Score"] * WEIGHTS["Pressure_Score"] +
    data["RPM_Score"] * WEIGHTS["RPM_Score"] +
    data["Current_Score"] * WEIGHTS["Current_Score"]
)


# ============================================================
# 4. CREATE HEALTH CONDITION LABEL
# ============================================================

def health_condition(score):

    if score >= 80:
        return "Healthy"

    elif score >= 60:
        return "Warning"

    else:
        return "Critical"


data["Condition"] = data["Health_Score"].apply(health_condition)


# ============================================================
# 5. TRAIN AI MODEL
# ============================================================

features = [
    "Temperature",
    "Vibration",
    "Pressure",
    "RPM",
    "Current"
]

X = data[features]
y = data["Condition"]

X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=42,
    stratify=y
)

model = RandomForestClassifier(
    n_estimators=200,
    random_state=42
)

model.fit(X_train, y_train)

prediction = model.predict(X_test)

accuracy = accuracy_score(y_test, prediction)

print("=" * 60)
print("AI EQUIPMENT HEALTH MONITORING SYSTEM")
print("=" * 60)

print(f"\nModel Accuracy: {accuracy * 100:.2f}%")

print("\nClassification Report:")
print(classification_report(y_test, prediction))


# ============================================================
# 6. FEATURE IMPORTANCE
# ============================================================

importance = pd.Series(
    model.feature_importances_,
    index=features
).sort_values(ascending=False)

print("\nSensor Importance:")
print(importance)


# ============================================================
# 7. CURRENT EQUIPMENT STATUS
# ============================================================

latest = data.iloc[-1]

print("\n" + "=" * 60)
print("CURRENT EQUIPMENT STATUS")
print("=" * 60)

print(f"Temperature : {latest['Temperature']:.2f} °C")
print(f"Vibration   : {latest['Vibration']:.2f} mm/s")
print(f"Pressure    : {latest['Pressure']:.2f} bar")
print(f"RPM         : {latest['RPM']:.2f}")
print(f"Current     : {latest['Current']:.2f} A")

print(f"\nHealth Score: {latest['Health_Score']:.2f}/100")
print(f"Condition   : {latest['Condition']}")


# ============================================================
# 8. GRAPH DESIGN
# ============================================================

sns.set_style("darkgrid")

fig = plt.figure(figsize=(16, 11))

# ------------------------------------------------------------
# GRAPH 1 - HEALTH SCORE TREND
# ------------------------------------------------------------

ax1 = plt.subplot(2, 2, 1)

colors = []

for score in data["Health_Score"]:
    if score >= 80:
        colors.append("green")
    elif score >= 60:
        colors.append("orange")
    else:
        colors.append("red")

ax1.plot(
    data.index,
    data["Health_Score"],
    color="royalblue",
    linewidth=2
)

ax1.axhline(
    80,
    color="green",
    linestyle="--",
    label="Healthy threshold"
)

ax1.axhline(
    60,
    color="orange",
    linestyle="--",
    label="Warning threshold"
)

ax1.fill_between(
    data.index,
    data["Health_Score"],
    80,
    where=(data["Health_Score"] >= 80),
    color="green",
    alpha=0.15
)

ax1.fill_between(
    data.index,
    data["Health_Score"],
    60,
    where=(data["Health_Score"] < 80),
    color="orange",
    alpha=0.10
)

ax1.set_title(
    "Equipment Health Score",
    fontsize=14,
    fontweight="bold"
)

ax1.set_xlabel("Time / Sensor Reading")
ax1.set_ylabel("Health Score (0-100)")
ax1.set_ylim(0, 105)
ax1.legend()


# ------------------------------------------------------------
# GRAPH 2 - SENSOR TRENDS
# ------------------------------------------------------------

ax2 = plt.subplot(2, 2, 2)

ax2.plot(
    data.index,
    data["Temperature"],
    label="Temperature",
    linewidth=2
)

ax2.plot(
    data.index,
    data["Vibration"] * 10,
    label="Vibration ×10",
    linewidth=2
)

ax2.plot(
    data.index,
    data["Pressure"],
    label="Pressure",
    linewidth=2
)

ax2.set_title(
    "Industrial Sensor Trends",
    fontsize=14,
    fontweight="bold"
)

ax2.set_xlabel("Time / Sensor Reading")
ax2.set_ylabel("Sensor Value")
ax2.legend()


# ------------------------------------------------------------
# GRAPH 3 - CONDITION DISTRIBUTION
# ------------------------------------------------------------

ax3 = plt.subplot(2, 2, 3)

condition_counts = data["Condition"].value_counts()

condition_order = ["Healthy", "Warning", "Critical"]

condition_counts = condition_counts.reindex(
    condition_order,
    fill_value=0
)

bar_colors = [
    "green",
    "orange",
    "red"
]

bars = ax3.bar(
    condition_counts.index,
    condition_counts.values,
    color=bar_colors
)

ax3.set_title(
    "Equipment Condition Distribution",
    fontsize=14,
    fontweight="bold"
)

ax3.set_xlabel("Condition")
ax3.set_ylabel("Number of Readings")

for bar in bars:
    height = bar.get_height()

    ax3.text(
        bar.get_x() + bar.get_width() / 2,
        height,
        str(int(height)),
        ha="center",
        va="bottom",
        fontweight="bold"
    )


# ------------------------------------------------------------
# GRAPH 4 - SENSOR IMPORTANCE
# ------------------------------------------------------------

ax4 = plt.subplot(2, 2, 4)

importance.sort_values().plot(
    kind="barh",
    ax=ax4,
    color="royalblue"
)

ax4.set_title(
    "AI Sensor Feature Importance",
    fontsize=14,
    fontweight="bold"
)

ax4.set_xlabel("Importance")


# ============================================================
# FINAL DASHBOARD
# ============================================================

plt.suptitle(
    "AI-Based Industrial Equipment Health Monitoring Dashboard",
    fontsize=20,
    fontweight="bold"
)

plt.tight_layout()

plt.show()
