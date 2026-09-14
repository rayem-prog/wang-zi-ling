"""LightGBM 二分类：预测未来 5 日跑赢沪深300的概率。"""

from __future__ import annotations

import joblib
import numpy as np
from lightgbm import LGBMClassifier


def train_model(X, y, seed: int = 42) -> LGBMClassifier:
    model = LGBMClassifier(
        n_estimators=200,
        learning_rate=0.05,
        num_leaves=31,
        max_depth=4,
        subsample=0.8,
        subsample_freq=1,
        colsample_bytree=0.8,
        random_state=seed,
        verbose=-1,
    )
    model.fit(X, y)
    return model


def predict_scores(model: LGBMClassifier, X) -> np.ndarray:
    return (model.predict_proba(X)[:, 1] * 100).astype(float)


def save_model(model: LGBMClassifier, path: str) -> None:
    joblib.dump(model, path)


def load_model(path: str) -> LGBMClassifier:
    return joblib.load(path)
