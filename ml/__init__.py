"""
ML 模型训练模块 — 独立于 Engine

Provides:
    ModelTrainer: 多因子模型训练器 (OLS/Ridge/LightGBM)
"""

from .trainer import ModelTrainer

__all__ = ["ModelTrainer"]
