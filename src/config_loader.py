"""
配置加载器 —— 从 YAML 读取所有策略参数，提供单例访问。

设计原则：
  - 单例缓存：首次读取后常驻内存
  - 不修改任何现有 Python 模块——YAML 是未来真源，目前代码仍用硬编码
  - 迁移路径：逐步将 constants.py / strategy.py / market_data.py 中的硬编码
    替换为 load_config() 调用

用法：
    from src.config_loader import load_config, get_target_weights, get_thresholds

    cfg = load_config()
    weights = get_target_weights()
    thresholds = get_thresholds()
"""

from __future__ import annotations

from pathlib import Path

# ── 尝试导入 yaml，如果不存在则降级 ──
try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "strategy.yaml"
_cache: dict | None = None


def load_config() -> dict:
    """加载完整策略配置（单例缓存）。

    Returns:
        配置 dict。如果 yaml 不可用或文件不存在，返回空 dict。
    """
    global _cache

    if _cache is not None:
        return _cache

    if not _HAS_YAML:
        _cache = {}
        return _cache

    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            _cache = yaml.safe_load(f) or {}
    except (FileNotFoundError, OSError):
        _cache = {}

    return _cache


def reload_config() -> dict:
    """强制重新加载配置（清除缓存）。"""
    global _cache
    _cache = None
    return load_config()


# ── 便捷 getter ──

def get_target_weights() -> dict[str, float]:
    """资产大类目标权重。"""
    return load_config().get("target_weights", {})


def get_thresholds() -> dict[str, float]:
    """阶梯阈值。"""
    return load_config().get("thresholds", {})


def get_cooldown_days() -> int:
    """冷却期天数。"""
    return load_config().get("cooldown", {}).get("days", 3)


def get_signals() -> dict:
    """信号元数据。"""
    return load_config().get("signals", {})


def get_incremental() -> dict:
    """增量资金参数。"""
    return load_config().get("incremental", {})


def get_psyche_facts() -> list[str]:
    """心理防御文案。"""
    return load_config().get("psyche_facts", [])


def get_etf_maps() -> dict:
    """ETF/指数名称映射表。"""
    return load_config().get("etf_maps", {})


def get_radar_class_map() -> dict[str, str]:
    """雷达大类映射。"""
    return load_config().get("radar_class_map", {})
