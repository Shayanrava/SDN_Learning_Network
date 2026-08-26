import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix
import joblib

# ۱. خواندن دیتاست جدید چندمعیاره
df = pd.read_csv("dataset_mcd.csv")

# ۲. مهندسی ویژگی‌ها (Feature Engineering)
# محاسبه میانگین متحرک برای بررسی روند تغییرات
df['kbps_rolling_avg'] = df['kbps'].rolling(window=3, min_periods=1).mean()

# انتخاب ویژگی‌های ورودی (X)
feature_cols = [
    'kbps', 
    'kbps_diff', 
    'kbps_rolling_avg', 
    'avg_pkt_size', 
    'udp_tcp_ratio', 
    'drop_rate'
]

X = df[feature_cols]
y = df['is_congested']

# ۳. تقسیم داده‌ها به داده‌های آموزش (80%) و تست (20%)
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)

# ۴. تعریف و آموزش مدل Random Forest
model = RandomForestClassifier(n_estimators=100, random_state=42)
model.fit(X_train, y_train)

# ۵. ارزیابی مدل روی داده‌های تست
y_pred = model.predict(X_test)

print("=== Classification Report ===")
print(classification_report(y_test, y_pred))

print("\n=== Confusion Matrix ===")
print(confusion_matrix(y_test, y_pred))

# ۶. محاسبه و نمایش اهمیت هر ویژگی در تصمیم‌گیری مدل
print("\n=== Feature Importances ===")
importances = model.feature_importances_
for col, imp in zip(feature_cols, importances):
    print(f"{col:20s}: {imp:.4f}")

# ۷. ذخیره مدل نهایی
joblib.dump(model, "qos_mcd_model.pkl")
print("\nModel saved successfully as 'qos_mcd_model.pkl'")