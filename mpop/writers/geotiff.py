#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2015.

# Author(s):

#   David Hoese <david.hoese@ssec.wisc.edu>

# This file is part of mpop.

# mpop is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.

# mpop is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE.  See the GNU General Public License for more details.

# You should have received a copy of the GNU General Public License along with
# mpop.  If not, see <http://www.gnu.org/licenses/>.

"""GeoTIFF writer objects for creating GeoTIFF files from `Projectable` objects.

"""

import os
import logging
import numpy as np
from mpop import PACKAGE_CONFIG_PATH
from mpop.plugin_base import Writer
from mpop.utils import ensure_dir
from osgeo import gdal, osr

from pyresample import utils
from mpop.projector import get_area_def


LOG = logging.getLogger(__name__)


class GeoTIFFWriter(Writer):
    GDAL_OPTIONS = (
        "tfw",
        "rpb",
        "rpctxt",
        "interleave",
        "tiled",
        "blockxsize",
        "blockysize",
        "nbits",
        "compress",
        "num_threads",
        "predictor",
        "discard_lsb",
        "sparse_ok",
        "jpeg_quality",
        "jpegtablesmode",
        "zlevel",
        "photometric",
        "alpha",
        "profile",
        "bigtiff",
        "pixeltype",
        "copy_src_overviews",

    )

    def __init__(self, tags=None, **kwargs):
        kwargs.setdefault("name", "geotiff")
        kwargs.setdefault("config_file", os.path.join(PACKAGE_CONFIG_PATH, "writers", "geotiff.cfg"))
        Writer.__init__(self, **kwargs)

        self.floating_point = self.options.get("floating_point", False)
        self.fill_value = self.options.get("fill_value", None)
        self.tags = self.options.get("tags", {})
        if not isinstance(self.tags, dict):
            # if it's coming from a config file
            self.tags = dict(tuple(x.split("=")) for x in self.tags.split(","))

        # Geotiff specific settings
        self.gdal_options = {k: v for k, v in kwargs.items() if k in self.GDAL_OPTIONS}

    def _gdal_write_channels(self, dst_ds, channels, opacity, fill_value):
        """Write *channels* in a gdal raster structure *dts_ds*, using
        *opacity* as alpha value for valid data, and *fill_value*.
        """
        if fill_value is not None:
            for i, chan in enumerate(channels):
                chn = chan.filled(fill_value[i])
                dst_ds.GetRasterBand(i + 1).WriteArray(chn)
        else:
            mask = np.zeros(channels[0].shape, dtype=np.bool)
            i = 0
            for i, chan in enumerate(channels):
                dst_ds.GetRasterBand(i + 1).WriteArray(chan.filled(0))
                mask |= np.ma.getmaskarray(chan)
            try:
                mask |= np.ma.getmaskarray(opacity)
            except AttributeError:
                pass

            alpha = np.where(mask, 0, opacity).astype(chan.dtype)
            dst_ds.GetRasterBand(i + 2).WriteArray(alpha)

    def save_image(self, img, metadata, floating_point=False, **kwargs):
        """Save the image to the given *filename* in geotiff_ format.
        `floating_point` allows the saving of
        'L' mode images in floating point format if set to True.

        .. _geotiff: http://trac.osgeo.org/geotiff/
        """
        raster = gdal.GetDriverByName("GTiff")

        filename = kwargs.pop("filename", self.get_filename(**metadata))

        # Update global GDAL options with these specific ones
        gdal_options = self.gdal_options.copy()
        for k in kwargs.keys():
            if k in self.GDAL_OPTIONS:
                gdal_options[k] = kwargs[k]

        if "area" not in metadata:
            raise ValueError("Image metadata must have an 'area' defined for geotiff creation")
        area = metadata["area"]
        floating_point = floating_point if floating_point is not None else self.floating_point

        if "alpha" in kwargs:
            raise ValueError("Keyword 'alpha' is automatically set and should not be specified")

        if floating_point:
            if img.mode != "L":
                raise ValueError("Image must be in 'L' mode for floating point geotiff saving")
            if img.fill_value is None:
                LOG.warning("Image with floats cannot be transparent, so setting fill_value to 0")
                fill_value = 0
            channels = [img.channels[0].astype(np.float64)]
            fill_value = img.fill_value or [0]
            gformat = gdal.GDT_Float64
            opacity = 0
        else:
            nbits = int(gdal_options.get("nbits", "8"))
            if nbits > 16:
                dtype = np.uint32
                gformat = gdal.GDT_UInt32
            elif nbits > 8:
                dtype = np.uint16
                gformat = gdal.GDT_UInt16
            else:
                dtype = np.uint8
                gformat = gdal.GDT_Byte
            opacity = np.iinfo(dtype).max
            channels, fill_value = img._finalize(dtype)

        LOG.debug("Saving to GeoTiff: %s", filename)

        g_opts = ["{}={}".format(k.upper(), str(v)) for k, v in self.gdal_options.items()]

        ensure_dir(filename)
        if img.mode == "L":
            if fill_value is not None:
                dst_ds = raster.Create(filename,
                                       img.width,
                                       img.height,
                                       1,
                                       gformat,
                                       g_opts)
            else:
                g_opts.append("ALPHA=YES")
                dst_ds = raster.Create(filename,
                                       img.width,
                                       img.height,
                                       2,
                                       gformat,
                                       g_opts)
            self._gdal_write_channels(dst_ds, channels,
                                      opacity, fill_value)
        elif img.mode == "LA":
            g_opts.append("ALPHA=YES")
            dst_ds = raster.Create(filename,
                                   img.width,
                                   img.height,
                                   2,
                                   gformat,
                                   g_opts)
            self._gdal_write_channels(dst_ds,
                                      channels[:-1], channels[1],
                                      fill_value)
        elif img.mode == "RGB":
            if fill_value is not None:
                dst_ds = raster.Create(filename,
                                       img.width,
                                       img.height,
                                       3,
                                       gformat,
                                       g_opts)
            else:
                g_opts.append("ALPHA=YES")
                dst_ds = raster.Create(filename,
                                       img.width,
                                       img.height,
                                       4,
                                       gformat,
                                       g_opts)

            self._gdal_write_channels(dst_ds, channels,
                                      opacity, fill_value)

        elif img.mode == "RGBA":
            g_opts.append("ALPHA=YES")
            dst_ds = raster.Create(filename,
                                   img.width,
                                   img.height,
                                   4,
                                   gformat,
                                   g_opts)

            self._gdal_write_channels(dst_ds,
                                      channels[:-1], channels[3],
                                      fill_value)
        else:
            raise NotImplementedError("Saving to GeoTIFF using image mode %s is not implemented." % img.mode)

        # Create raster GeoTransform based on upper left corner and pixel
        # resolution ... if not overwritten by argument geotransform.

        try:
            area = get_area_def(area)
        except (utils.AreaNotFound, AttributeError):
            area = area

        try:
            geotransform = [area.area_extent[0], area.pixel_size_x, 0,
                               area.area_extent[3], 0, -area.pixel_size_y]
            dst_ds.SetGeoTransform(geotransform)
            srs = osr.SpatialReference()

            srs.ImportFromProj4(area.proj4_string)
            srs.SetProjCS(area.proj_id)
            try:
                srs.SetWellKnownGeogCS(area.proj_dict['ellps'])
            except KeyError:
                pass
            try:
                # Check for epsg code.
                srs.SetAuthority('PROJCS', 'EPSG',
                                 int(area.proj_dict['init'].
                                     split('epsg:')[1]))
            except (KeyError, IndexError):
                pass
            srs = srs.ExportToWkt()
            dst_ds.SetProjection(srs)
        except AttributeError:
            LOG.exception("Could not load geographic data, invalid area")

        tags = self.tags.copy()
        if "start_time" in metadata:
            tags.update({'TIFFTAG_DATETIME': metadata["start_time"].strftime("%Y:%m:%d %H:%M:%S")})

        dst_ds.SetMetadata(tags, '')

        # Close the dataset

        dst_ds = None
