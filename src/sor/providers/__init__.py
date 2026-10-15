"""Importing this package registers every provider type with PROVIDER_TYPES."""

from sor.providers import arcgis, csv_snapshot, iowa, vendor  # noqa: F401
from sor.providers.base import PROVIDER_TYPES, Provider

__all__ = ["PROVIDER_TYPES", "Provider"]
