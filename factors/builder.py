"""Deprecated — import TechFactorBuilder from factors.tech_builder instead."""

from factors.tech_builder import TechFactorBuilder
import warnings


class FactorBuilder(TechFactorBuilder):
    """Deprecated alias for TechFactorBuilder — use TechFactorBuilder directly."""
    def __init__(self, *args, **kwargs):
        warnings.warn("FactorBuilder is deprecated, use TechFactorBuilder", DeprecationWarning, stacklevel=2)
        super().__init__(*args, **kwargs)
