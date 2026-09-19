"""Optional cell-average estimates; NEVER measured heights of individual buildings."""
from functools import lru_cache
import math
import zipfile
import time
import numpy as np
import rasterio
from pyproj import Transformer
from .core import LOCAL,download,digest

BASE='https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_H_GLOBE_R2023A/GHS_BUILT_H_ANBH_E2018_GLOBE_R2023A_54009_100/V1-0/'
PROJECTION=Transformer.from_crs(4326,'ESRI:54009',always_xy=True)
UNAVAILABLE={}  # Brief retry backoff only; successful source tiles remain immutable.

@lru_cache(maxsize=8)
def tile(row:int,column:int):
    name=f'GHS_BUILT_H_ANBH_E2018_GLOBE_R2023A_54009_100_V1_0_R{row}_C{column}'
    archive=download(BASE+f'tiles/{name}.zip',LOCAL/'raw'/'ghsl'/f'R{row}_C{column}.zip')
    download(BASE+'copyright.txt',LOCAL/'raw'/'ghsl'/'copyright.txt')
    with zipfile.ZipFile(archive) as z:
        tif=next(p for p in z.namelist() if p.endswith('.tif'))
    uri='/vsizip/'+str(archive.resolve()).replace('\\','/')+'/'+tif
    return rasterio.open(uri),digest(archive)

def estimates(lons,lats,*,failures=None):
    xs,ys=PROJECTION.transform(lons,lats)
    result=[]
    for x,y in zip(xs,ys):
        row=math.floor((9000000-y)/1000000)+1
        column=math.floor((x+18041000)/1000000)+1
        key=f'R{row}_C{column}'
        previous=UNAVAILABLE.get(key)
        if failures is not None and previous and time.monotonic()-previous[0]<300:
            failures[key]=previous[1];result.append((None,None));continue
        try:
            ds,sha=tile(row,column)
        except (RuntimeError,OSError,zipfile.BadZipFile,rasterio.errors.RasterioError) as error:
            if failures is None:raise
            reason=type(error).__name__
            UNAVAILABLE[key]=(time.monotonic(),reason);failures[key]=reason
            result.append((None,None));continue
        UNAVAILABLE.pop(key,None)
        if not(ds.bounds.left<=x<ds.bounds.right and ds.bounds.bottom<y<=ds.bounds.top):
            raise ValueError('GHSL tile index and actual georeferencing disagree')
        value=float(next(ds.sample([(x,y)],masked=True))[0])
        result.append((value if np.isfinite(value) and 0<value<255 else None,sha))
    return result
