"""StockPilot 配置中心。用户可直接修改默认值或通过看板写入 user_settings.json。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RiskSettings:
    max_single_position: float = 0.20
    max_total_position: float = 0.80
    stop_loss: float = 0.05
    take_profit: float = 0.15
    drawdown_alert: float = 0.08
    risk_tier: str = "B"



@dataclass(frozen=True)
class EngineSettings:
    w_a: float = 0.5
    w_b: float = 0.5
    w_min: float = 0.2
    w_max: float = 0.8
    adjust_every_days: int = 20
    lookback_days: int = 60
    manual_override: bool = False
    manual_w_a: float | None = None
    use_intraday_factors: bool = False



@dataclass(frozen=True)
class DataSettings:
    start_date: str = "2021-01-01"
    end_date: str | None = None
    adjust: str = "qfq"
    db_path: str = "data/market.db"
    index_symbol: str = "sh000300"


@dataclass(frozen=True)
class UniverseSettings:
    base_pool: str = "hs300"
    extra_codes: tuple[str, ...] = ()
    exclude_st: bool = True


@dataclass(frozen=True)
class AccountSettings:
    cash: float = 100_000.0
    max_positions: int = 5


@dataclass(frozen=True)
class NotifySettings:
    backend: str = "local"  # "local" | "webhook" | "both" | "mock"
    enable_mac_notify: bool = True
    webhook_url: str = ""
    webhook_type: str = "generic"  # "wecom" | "feishu" | "dingtalk" | "generic"
    p1_cooldown_minutes: int = 15
    daily_cap: int = 30


@dataclass(frozen=True)
class PathSettings:
    artifact_dir: str = "artifacts"
    user_settings_path: str = "artifacts/user_settings.json"


@dataclass(frozen=True)
class Settings:
    risk: RiskSettings = field(default_factory=RiskSettings)
    engine: EngineSettings = field(default_factory=EngineSettings)
    data: DataSettings = field(default_factory=DataSettings)
    universe: UniverseSettings = field(default_factory=UniverseSettings)
    account: AccountSettings = field(default_factory=AccountSettings)
    paths: PathSettings = field(default_factory=PathSettings)
    notify: NotifySettings = field(default_factory=NotifySettings)


def settings_from_dict(d: dict) -> Settings:
    return Settings(
        risk=RiskSettings(**d.get("risk", {})),
        engine=EngineSettings(**d.get("engine", {})),
        data=DataSettings(**d.get("data", {})),
        universe=UniverseSettings(**d.get("universe", {})),
        account=AccountSettings(**d.get("account", {})),
        paths=PathSettings(**d.get("paths", {})),
        notify=NotifySettings(**d.get("notify", {})),
    )



def load_settings(path: str | None = None) -> Settings:
    target = Path(path) if path else Path(Settings().paths.user_settings_path)
    if not target.exists():
        return Settings()
    return settings_from_dict(json.loads(target.read_text(encoding="utf-8")))


def save_settings(s: Settings, path: str | None = None) -> None:
    target = Path(path) if path else Path(s.paths.user_settings_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(asdict(s), ensure_ascii=False, indent=2), encoding="utf-8")
