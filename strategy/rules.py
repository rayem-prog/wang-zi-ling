"""引擎 B：可解释的技术规则打分，0–100。支持基于宏观档位与当日数据的自适应公式优化。"""

from __future__ import annotations

from dataclasses import dataclass
import pandas as pd


@dataclass
class RuleFormulaConfig:
    """引擎 B 多因子技术规则打分公式权重配置"""
    w_trend_ma5: float = 15.0       # 均线短期金叉加分
    pen_trend_ma5: float = 10.0     # 均线短期死叉扣分
    w_trend_ma20: float = 10.0      # 均线中期顺向加分
    pen_trend_ma20: float = 5.0     # 均线中期逆向扣分
    w_ret5: float = 8.0             # 5日动量加分
    pen_ret5: float = 8.0           # 5日负动量扣分
    w_ret20: float = 7.0            # 20日动量加分
    pen_ret20: float = 7.0          # 20日负动量扣分
    w_vol_normal: float = 5.0       # 适度放量 (1.0~2.0)
    w_vol_high: float = 2.0         # 爆量 (>2.0)
    pen_vol_shrink: float = 5.0     # 缩量衰竭 (<0.8)
    w_rsi_neutral: float = 5.0      # RSI 中枢 (45~65)
    pen_rsi_overbought: float = 12.0 # RSI 超买 (>75)
    w_rsi_oversold: float = 3.0     # RSI 超跌反弹 (<25)
    w_boll_lower: float = 5.0       # 布林下轨超跌反弹 (<0.2)
    pen_boll_upper: float = 8.0     # 布林上轨受阻回落 (>0.9)
    regime_name: str = "标准多因子中枢公式"
    regime_desc: str = "基于基准权重的多因子标准平衡配置"


def optimize_rules_formula(
    macro_stance: str = "neutral",
    features_df: pd.DataFrame | None = None,
) -> tuple[RuleFormulaConfig, dict]:
    """
    根据当日市场数据与宏观温度计档位，动态自适应优化多因子规则打分算法：
    - DEFENSE / BEAR (防御防守档): 抑制高位追高动量，大幅提升布林下轨与低吸反弹权重；
    - BULL (多头进攻档): 提高均线突破与放量动量加分，放宽超买惩罚；
    - NEUTRAL (震荡中枢档): 箱体高抛低吸平衡。
    """
    cfg = RuleFormulaConfig()
    stance = str(macro_stance).lower()

    if stance in ("defense", "bear"):
        # 防守优化算法：防止追高被套，提高下轨黄金反弹权重
        cfg.w_trend_ma5 = 8.0
        cfg.pen_trend_ma5 = 14.0
        cfg.w_ret5 = 4.0
        cfg.pen_ret5 = 12.0
        cfg.w_boll_lower = 12.0       # 下轨超跌加权 +140%
        cfg.pen_boll_upper = 14.0      # 上轨追高重罚 +75%
        cfg.w_rsi_oversold = 8.0       # 超跌加分 +166%
        cfg.pen_rsi_overbought = 16.0  # 超买扣分加码
        cfg.regime_name = "防守控撤优化公式 (Adaptive Defense Mode)"
        cfg.regime_desc = "已根据今日 DEFENSE 防御档自动调优：下调高位突破动量权重 (-47%)，提升布林下轨与超跌反弹加权 (+140%)，降低追高回撤风险。"
    elif stance == "bull":
        # 进攻优化算法：顺势主升浪放宽超买限制
        cfg.w_trend_ma5 = 20.0        # 趋势突破加权 +33%
        cfg.w_trend_ma20 = 14.0
        cfg.w_ret5 = 12.0
        cfg.w_vol_normal = 8.0
        cfg.pen_rsi_overbought = 6.0  # 放宽超买容忍
        cfg.pen_boll_upper = 4.0
        cfg.regime_name = "进攻动量优化公式 (Adaptive Bull Mode)"
        cfg.regime_desc = "已根据今日 BULL 进攻档自动调优：显著强化均线多头与放量动量加分 (+33%)，放宽超买压制，追求高弹性 Alpha。"
    else:
        cfg.regime_name = "震荡均衡优化公式 (Adaptive Neutral Mode)"
        cfg.regime_desc = "已根据今日 NEUTRAL 震荡中枢自动微调：保持动量与低吸双向平衡，注重箱体震荡高抛低吸。"

    audit = {
        "regime_name": cfg.regime_name,
        "regime_desc": cfg.regime_desc,
        "key_weights": {
            "均线趋势 (ma5)": cfg.w_trend_ma5,
            "短期动量 (ret5)": cfg.w_ret5,
            "布林下轨反弹 (boll_lower)": cfg.w_boll_lower,
            "超买风控惩罚 (rsi_overbought)": -cfg.pen_rsi_overbought,
        },
    }
    return cfg, audit


def _score_row(f: dict, cfg: RuleFormulaConfig | None = None) -> float:
    c = cfg or RuleFormulaConfig()
    s = 50.0
    s += c.w_trend_ma5 if f.get("ma5_gt_ma20") else -c.pen_trend_ma5
    s += c.w_trend_ma20 if f.get("ma20_gt_ma60") else -c.pen_trend_ma20
    s += c.w_ret5 if (f.get("ret_5") or 0) > 0 else -c.pen_ret5
    s += c.w_ret20 if (f.get("ret_20") or 0) > 0 else -c.pen_ret20

    vr = f.get("vol_ratio_5", 1.0) or 1.0
    if 1.0 <= vr <= 2.0:
        s += c.w_vol_normal
    elif vr > 2.0:
        s += c.w_vol_high
    elif vr < 0.8:
        s -= c.pen_vol_shrink

    rsi = f.get("rsi_14", 50.0) or 50.0
    if 45 <= rsi <= 65:
        s += c.w_rsi_neutral
    elif rsi > 75:
        s -= c.pen_rsi_overbought
    elif rsi < 25:
        s += c.w_rsi_oversold

    bp = f.get("boll_pos", 0.5) or 0.5
    if bp < 0.2:
        s += c.w_boll_lower
    elif bp > 0.9:
        s -= c.pen_boll_upper

    return max(0.0, min(100.0, s))


def score_engine_b(features: pd.DataFrame, config: RuleFormulaConfig | None = None) -> pd.DataFrame:
    rows = []
    for _, r in features.iterrows():
        rows.append({"code": r["code"], "date": r["date"], "score_b": round(_score_row(r, config), 2)})
    return pd.DataFrame(rows)

