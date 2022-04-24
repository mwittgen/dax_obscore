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

__all__ = ["ObscoreExporter"]

import logging
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union, cast

import astropy.time
import pyarrow
from lsst.daf.butler import Butler, DatasetRef, Dimension, DimensionRecord
from lsst.sphgeom import ConvexPolygon, LonLat, Region
from pyarrow import RecordBatch, Schema
from pyarrow.csv import CSVWriter
from pyarrow.parquet import ParquetWriter

from . import DatasetTypeConfig, ExporterConfig

_LOG = logging.getLogger(__name__)

# List of standard columns in output file. This should include at least all
# mandatory columns defined in ObsCore note (revision 1.1, Appendix B). Extra
# columns can be added via `extra_columns` parameters in configuration.
_STATIC_SCHEMA = (
    ("dataproduct_type", pyarrow.string()),
    ("dataproduct_subtype", pyarrow.string()),
    ("facility_name", pyarrow.string()),
    ("calib_level", pyarrow.int8()),
    ("target_name", pyarrow.string()),
    ("obs_id", pyarrow.string()),
    ("obs_collection", pyarrow.string()),
    ("obs_publisher_did", pyarrow.string()),  # not filled
    ("access_url", pyarrow.string()),
    ("access_format", pyarrow.string()),
    ("s_ra", pyarrow.float64()),
    ("s_dec", pyarrow.float64()),
    ("s_fov", pyarrow.float64()),
    ("s_region", pyarrow.string()),
    ("s_resolution", pyarrow.float64()),  # not filled
    ("s_xel1", pyarrow.int16()),  # not filled
    ("s_xel2", pyarrow.int16()),  # not filled
    ("t_xel", pyarrow.int16()),  # not filled
    ("t_min", pyarrow.float64()),
    ("t_max", pyarrow.float64()),
    ("t_exptime", pyarrow.float64()),
    ("t_resolution", pyarrow.float64()),  # not filled
    ("em_xel", pyarrow.int16()),  # not filled
    ("em_min", pyarrow.float64()),
    ("em_max", pyarrow.float64()),
    ("em_res_power", pyarrow.float64()),  # not filled
    ("em_filter_name", pyarrow.string()),  # non-standard
    ("o_ucd", pyarrow.string()),
    ("pol_xel", pyarrow.int16()),  # not filled
    ("instrument_name", pyarrow.string()),
)


# Map few standard Python types to pyarrow types
_PYARROW_TYPE = {
    bool: pyarrow.bool_(),
    int: pyarrow.int64(),
    float: pyarrow.float64(),
    str: pyarrow.string(),
}


class _BatchCollector:
    """Helper class to collect records data before making a record batch."""

    def __init__(self, schema: Schema):
        self.schema = schema
        self.batch: List[List] = [[] for column in self.schema.names]
        self.size = 0

    def add_to_batch(self, data: Dict[str, Any]) -> None:
        """Add new row to a batch.

        Notes
        -----
        `data` dictionary is updated in place for efficiency.
        """
        for i, column in enumerate(self.schema.names):
            value = data.pop(column, None)
            self.batch[i].append(value)
        self.size += 1

        # watch for unknown columns
        if data:
            columns = set(data.keys())
            raise ValueError(f"Unexpected column names: {columns}")

    def make_record_batch(self) -> RecordBatch:
        """Make a record batch out of accumulated data, and reset."""
        if self.size == 0:
            return None

        # make pyarrow batch out of collected data
        batch = pyarrow.record_batch(self.batch, self.schema)

        # reset to empty
        self.batch = [[] for column in self.schema.names]
        self.size = 0

        return batch


class ObscoreExporter:
    """Class for extracting and exporting of the datasets in ObsCore format.

    Parameters
    ----------
    butler : `lsst.daf.butler.Butler`
        Data butler.
    config : `lsst.daf.butler.Config`
    """

    def __init__(self, butler: Butler, config: ExporterConfig):
        self.butler = butler
        self.config = config
        self.extra_columns: Dict[str, Any] = {}
        self.schema = self._make_schema(config)
        self._visit_regions: Optional[Dict[Tuple[str, int], Region]] = None

    def to_parquet(self, output: str) -> None:
        """Export Butler datasets as ObsCore Data Model in parquet format.

        Parameters
        ----------
        output : `str`
            Location of the output file.
        """

        compression = self.config.parquet_compression
        with ParquetWriter(output, self.schema, compression=compression) as writer:
            for record_batch in self._make_record_batches(self.config.batch_size):
                writer.write_batch(record_batch)

    def to_csv(self, output: str) -> None:
        """Export Butler datasets as ObsCore Data Model in CSV format.

        Parameters
        ----------
        output : `str`
            Location of the output file.
        """

        with CSVWriter(output, self.schema) as writer:
            for record_batch in self._make_record_batches(self.config.batch_size):
                writer.write_batch(record_batch)

    def _make_schema(self, config: ExporterConfig) -> Schema:
        """Create schema definition for output data.

        Returns
        -------
        schema : `pyarrow.Schema`
            Schema definition.
        """
        schema = list(_STATIC_SCHEMA)

        columns = set(col[0] for col in schema)

        all_configs: List[Union[ExporterConfig, DatasetTypeConfig]] = [config]
        if config.dataset_types:
            all_configs += config.dataset_types
        for cfg in all_configs:
            if cfg.extra_columns:
                for col_name, col_value in cfg.extra_columns.items():
                    if col_name in columns:
                        continue
                    col_type = _PYARROW_TYPE.get(type(col_value))
                    if col_type is None:
                        raise TypeError(
                            f"Unexpected type in extra_columns: column={col_name}, value={col_value:r}"
                        )
                    schema.append((col_name, col_type))
                    columns.add(col_name)
                    self.extra_columns[col_name] = col_value

        return pyarrow.schema(schema)

    def _make_record_batches(self, batch_size: int = 10_000) -> Iterator[RecordBatch]:
        """Generate batches of records to save to a file."""

        batch = _BatchCollector(self.schema)

        collections: Any = self.config.collections
        if not collections:
            collections = ...

        universe = self.butler.dimensions
        band = cast(Dimension, universe["band"])
        exposure = universe["exposure"]
        visit = universe["visit"]

        registry = self.butler.registry
        for dataset_config in self.config.dataset_types:

            _LOG.debug("Reading data for dataset %s", dataset_config.name)
            refs = registry.queryDatasets(dataset_config.name, collections=collections)

            # need dimension records
            refs = refs.expanded()

            for ref in refs:

                dataId = ref.dataId
                _LOG.debug("New record, dataId=%s", dataId.full)
                # _LOG.debug("New record, records=%s", dataId.records)

                record: Dict[str, Any] = {}

                record["dataproduct_type"] = dataset_config.dataproduct_type
                record["dataproduct_subtype"] = dataset_config.dataproduct_subtype
                record["o_ucd"] = dataset_config.o_ucd
                record["facility_name"] = self.config.facility_name
                record["calib_level"] = dataset_config.calib_level
                record["obs_collection"] = self.config.obs_collection
                record["access_format"] = dataset_config.access_format

                record["instrument_name"] = dataId.get("instrument")

                timespan = dataId.timespan
                if timespan is not None:
                    t_min = cast(astropy.time.Time, timespan.begin)
                    t_max = cast(astropy.time.Time, timespan.end)
                    record["t_min"] = t_min.mjd
                    record["t_max"] = t_max.mjd

                region = dataId.region
                if exposure in dataId.records:
                    if (dimension_record := dataId.records[exposure]) is not None:
                        self._exposure_records(dimension_record, record)
                        region = self._exposure_region(dimension_record)
                elif visit in dataId.records:
                    if (dimension_record := dataId.records[visit]) is not None:
                        self._visit_records(dimension_record, record)

                self._region_to_columns(region, record)

                if band in dataId:
                    em_range = None
                    if (label := dataId.get("physical_filter")) is not None:
                        em_range = self.config.spectral_ranges.get(label)
                    if not em_range:
                        band_name = dataId[band]
                        assert isinstance(band_name, str), "Band name must be string"
                        em_range = self.config.spectral_ranges.get(band_name)
                    if em_range:
                        record["em_min"], record["em_max"] = em_range
                    else:
                        _LOG.warning("could not find spectral range for dataId=%s", dataId)
                    record["em_filter_name"] = dataId["band"]

                if dataset_config.obs_id_fmt:
                    record["obs_id"] = dataset_config.obs_id_fmt.format(**dataId.byName(), **record)

                if self.config.use_butler_uri:
                    try:
                        url = self.butler.datastore.getURI(ref)
                        record["access_url"] = str(url)
                    except FileNotFoundError:
                        # could happen in some cases (e.g. mock running), can
                        # ignore for now
                        _LOG.warning(f"Datastore file does not exist for {ref}")
                else:
                    record["access_url"] = self._make_datalink_url(ref)

                # add extra columns
                record.update(self.extra_columns)

                batch.add_to_batch(record)
                if batch.size >= batch_size:
                    _LOG.debug("Saving next record batch, size=%s", batch.size)
                    yield batch.make_record_batch()

        # Final batch if anything is there
        if batch.size > 0:
            _LOG.debug("Saving final record batch, size=%s", batch.size)
            yield batch.make_record_batch()

    def _region_to_columns(self, region: Optional[Region], record: Dict[str, Any]) -> None:
        if region is None:
            return

        # get spacial parameters from the bounding circle
        circle = region.getBoundingCircle()
        center = LonLat(circle.getCenter())
        record["s_ra"] = center.getLon().asDegrees()
        record["s_dec"] = center.getLat().asDegrees()
        record["s_fov"] = circle.getOpeningAngle().asDegrees() * 2

        if isinstance(region, ConvexPolygon):
            poly = ["POLYGON ICRS"]
            for vertex in region.getVertices():
                lon_lat = LonLat(vertex)
                poly += [
                    f"{lon_lat.getLon().asDegrees():.6f}",
                    f"{lon_lat.getLat().asDegrees():.6f}",
                ]
            record["s_region"] = " ".join(poly)
        else:
            _LOG.warning(f"Unexpected region type: {type(region)}")

    def _exposure_records(self, dimension_record: DimensionRecord, record: Dict[str, Any]) -> None:
        """Extract all needed info from a visit dimension record."""
        record["t_exptime"] = dimension_record.exposure_time
        record["target_name"] = dimension_record.target_name

    def _visit_records(self, dimension_record: DimensionRecord, record: Dict[str, Any]) -> None:
        """Extract all needed info from an exposure dimension record."""
        record["t_exptime"] = dimension_record.exposure_time
        record["target_name"] = dimension_record.target_name

    def _exposure_region(self, exposure_record: DimensionRecord) -> Optional[Region]:
        """Return a Region for an exposure.

        This code tries to find a matching visit for an exposure and use the
        region from that visit.
        """

        if self._visit_regions is None:

            self._visit_regions = {}

            # Read all visits, there is a chance we need most of them anyways,
            # and trying to filter by dataset type and collection makes it
            # much slower.
            records = self.butler.registry.queryDimensionRecords("visit")
            for record in records:
                self._visit_regions[(record.instrument, record.id)] = record.region

        return self._visit_regions.get(
            (exposure_record.instrument, exposure_record.group_id)
        )

    def _make_datalink_url(self, ref: DatasetRef) -> str:
        """Generate DataLink URI for a given dataset."""
        raise NotImplementedError()