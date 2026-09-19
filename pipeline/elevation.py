from __future__ import annotations
import math
from functools import lru_cache
import numpy as np
from PIL import Image
from pyproj import Transformer, datadir
from .core import LOCAL, download

@lru_cache(maxsize=1)
def vertical_transformer():
    grid=download('https://cdn.proj.org/us_nga_egm96_15.tif',LOCAL/'grids'/'us_nga_egm96_15.tif')
    datadir.append_data_dir(str(grid.parent))
    transformer=Transformer.from_crs('EPSG:4326+5773','EPSG:4979',always_xy=True,allow_ballpark=False,only_best=True)
    test=transformer.transform(127.,37.,0.,errcheck=True)
    if not all(math.isfinite(v) for v in test): raise ValueError('Vertical datum conversion failed')
    return transformer

@lru_cache(maxsize=128)
def elevation_tile(z:int,x:int,y:int):
    path=download(f'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png',LOCAL/'raw'/'terrain'/str(z)/str(x)/f'{y}.png')
    pixels=np.array(Image.open(path).convert('RGB'),dtype=np.float64)
    return pixels[:,:,0]*256+pixels[:,:,1]+pixels[:,:,2]/256-32768

def sample_heights(lons,lats,zoom:int=12,ellipsoidal:bool=True):
    lons=np.asarray(lons,dtype=np.float64);lats=np.asarray(lats,dtype=np.float64)
    count=2**zoom
    # Raster samples are pixel centers. Interpolate across neighboring tile boundaries.
    px=(lons+180)/360*count*256-.5
    py=(1-np.arcsinh(np.tan(np.radians(lats)))/np.pi)/2*count*256-.5
    ix=np.floor(px).astype(np.int64);iy=np.floor(py).astype(np.int64)
    fx=px-ix;fy=py-iy
    output=np.zeros(lons.shape,dtype=np.float64)
    for dx,dy,weight in [(0,0,(1-fx)*(1-fy)),(1,0,fx*(1-fy)),(0,1,(1-fx)*fy),(1,1,fx*fy)]:
        gx=(ix+dx)%(count*256);gy=np.clip(iy+dy,0,count*256-1)
        tx=gx//256;ty=gy//256
        for x,y in set(zip(tx.ravel().tolist(),ty.ravel().tolist())):
            mask=(tx==x)&(ty==y)
            values=elevation_tile(zoom,int(x),int(y))[gy[mask]%256,gx[mask]%256]
            if np.any(values<=-32768): raise ValueError('No-data elevation cannot be used as ground')
            output[mask]+=values*weight[mask]
    if ellipsoidal:
        _,_,output=vertical_transformer().transform(lons,lats,output,errcheck=True)
    return np.asarray(output)
