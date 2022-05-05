# This file is part of dax_obscore.
#
# Developed for the LSST Data Management System.
# This product includes software developed by the LSST Project
# (http://www.lsst.org).
# See the COPYRIGHT file at the top-level directory of this distribution
# for details of code ownership.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import annotations

__all__ = ["DatasetTypeConfig", "ExporterConfig"]

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel


class DatasetTypeConfig(BaseModel):
    """Configuration describing dataset type-related options."""

    dataproduct_type: str
    """Value for the ``dataproduct_type`` column."""

    dataproduct_subtype: Optional[str] = None
    """Value for the ``dataproduct_subtype`` column, optional."""

    calib_level: int
    """Value for the ``calib_level`` column."""

    o_ucd: Optional[str] = None
    """Value for the ``o_ucd`` column, optional."""

    access_format: Optional[str] = None
    """Value for the ``access_format`` column, optional."""

    obs_id_fmt: Optional[str] = None
    """Format string for ``obs_id`` column, optional. Uses `str.format`
    syntax.
    """

    datalink_url_fmt: Optional[str] = None
    """Format string for ``access_url`` column for DataLink (when
    ``use_butler_uri`` is False), optional.
    """

    obs_collection: Optional[str] = None
    """Value for the ``obs_collection`` column, if specified it overrides
    global value in `ExporterConfig`."""

    extra_columns: Optional[Dict[str, Any]] = None
    """Values for additional columns, optional"""


class ExporterConfig(BaseModel):
    """Complete configuration for ObscoreExporter."""

    collections: Optional[List[str]] = None
    """Names of registry collections to search, if missing then all collections
    are used. This value can be overridden with command line options.
    """

    dataset_types: Dict[str, DatasetTypeConfig]
    """Per-dataset type configuration, key is the dataset type name."""

    where: Optional[str] = None
    """User expression to restrict the output. This value can be overridden
    with command line options.
    """

    obs_collection: Optional[str] = None
    """Value for the ``obs_collection`` column. This can be overridden in
    dataset type configuration.
    """

    facility_name: str
    """Value for the ``facility_name`` column."""

    extra_columns: Optional[Dict[str, Any]] = None
    """Values for additional columns, optional."""

    spectral_ranges: Dict[str, Tuple[float, float]] = {}
    """Maps band name or filter name to a min/max of spectral range."""

    use_butler_uri: bool = True
    """If true then use Butler URI for ``access_url``, otherwise generate a
    DataLink URL."""

    batch_size: int = 10_000
    """Number of records in a pyarrow RecordBatch"""

    parquet_compression: str = "snappy"
    """Compression method for parquet files"""
