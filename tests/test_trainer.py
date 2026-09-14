import numpy as np

from model import trainer


def test_train_and_predict_scores():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(200, 5))
    y = ((X[:, 0] + X[:, 1]) > 0).astype(int)
    model = trainer.train_model(X, y, seed=1)
    scores = trainer.predict_scores(model, X)
    assert scores.shape == (200,)
    assert scores.min() >= 0 and scores.max() <= 100


def test_model_roundtrip(tmp_path):
    rng = np.random.default_rng(0)
    X = rng.normal(size=(100, 3))
    y = (X[:, 0] > 0).astype(int)
    model = trainer.train_model(X, y, seed=1)
    path = str(tmp_path / "model.joblib")
    trainer.save_model(model, path)
    loaded = trainer.load_model(path)
    assert loaded.predict(X[:5]).shape == (5,)
