import numpy as np
import pytest
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from pipeline.height_quality import raster_point


def test_pixel_center_interpolation_and_nodata_rejection():
    with MemoryFile() as memory:
        with memory.open(driver='GTiff', width=3, height=3, count=1, dtype='float32',
                         crs='EPSG:4326', transform=from_origin(126, 38, .01, .01), nodata=-9999) as raster:
            values = np.array([[10,20,30],[30,40,50],[50,60,70]], dtype='float32')
            raster.write(values, 1)
            result = raster_point(raster, 126.01, 37.99)
            assert result['orthometric_height_m'] == pytest.approx(25)
            assert sum(sum(x) for x in result['weights']) == pytest.approx(1)
            values[0,0] = -9999
            raster.write(values, 1)
            with pytest.raises(ValueError, match='No-data'):
                raster_point(raster, 126.01, 37.99)
            with pytest.raises(ValueError, match='adjacent'):
                raster_point(raster, 126, 38)
