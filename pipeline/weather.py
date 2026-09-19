"""KMA HSR source decoding and geolocated imagery publication.

No authenticated source fixture is published. Inputs are either fetched with an
issued key or explicitly imported real files; errors are quarantined.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime,timezone,timedelta
import numpy as np
import requests
from PIL import Image
from scipy.spatial import cKDTree
from netCDF4 import Dataset
from .core import LOCAL,PUBLIC,REGIONS,atomic_json,digest,now,publish_file

def fetch_kma(endpoint,params,path):
    if path.exists() and path.with_suffix(path.suffix+'.meta.json').exists():return path
    key=os.getenv('KMA_AUTH_KEY')
    if not key:raise ValueError('KMA_AUTH_KEY is required; no synthetic weather is substituted')
    try:
        response=requests.get('https://apihub.kma.go.kr'+endpoint,params={**params,'authKey':key},timeout=(20,120))
        if response.status_code!=200:raise ValueError(f'KMA HTTP {response.status_code}')
    except requests.RequestException:raise RuntimeError('KMA network request failed; credentials omitted') from None
    content=response.content
    if len(content)<100 or b'<html' in content[:1000].lower() or b'"error"' in content[:500].lower():raise ValueError('KMA response is not a data file')
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
    atomic_json(path.with_suffix(path.suffix+'.meta.json'),{'endpoint':endpoint,'params':params,'retrieved_at':now(),'sha256':digest(path),'bytes':len(content)})
    return path

def parse_hsr_ascii(text):
    lines=[line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    if not lines:raise ValueError('Empty HSR grid')
    dimensions=lines[0].replace('=','').strip(',').split(',')
    if len(dimensions)!=2:raise ValueError('Invalid HSR dimensions')
    nx,ny=map(int,dimensions)
    if nx<1 or ny<1 or nx*ny>20000000:raise ValueError('HSR grid dimensions outside limit')
    values=np.fromstring(','.join(lines[1:]).replace('=','').replace(',,',','),sep=',',dtype=np.float32)
    if values.size!=nx*ny:raise ValueError('HSR grid cell count mismatch')
    return values.reshape(ny,nx)

def radar_rgba(values):
    # Source ECHO integers are dBZ * 100; -30000 and -25000 have distinct meanings.
    nodata=(values==-30000)|~np.isfinite(values);noecho=values==-25000
    physical=values/100;valid=~(nodata|noecho)&(physical>=-50)&(physical<=100)
    nodata|=~valid&~noecho
    output=np.zeros((*values.shape,4),dtype=np.uint8)
    bins=np.array([-50,0,10,20,30,40,50,60,100])
    palette=np.array([[194,221,230],[139,208,223],[94,181,207],[69,157,162],[116,171,87],[219,176,57],[220,113,48],[170,66,82]],dtype=np.uint8)
    indices=np.clip(np.searchsorted(bins,physical,side='right')-1,0,len(palette)-1)
    output[valid,:3]=palette[indices[valid]];output[valid,3]=185
    output[nodata]=[115,121,126,65]
    return output,{'valid':int(valid.sum()),'no_echo':int(noecho.sum()),'outside_or_missing':int(nodata.sum())}

def geolocate(values,lon,lat,bbox=REGIONS['korea'],width=1536,height=1168):
    if values.shape!=lon.shape or lon.shape!=lat.shape:raise ValueError('Value/longitude/latitude shapes differ')
    good=np.isfinite(lon)&np.isfinite(lat)&(lon>=-180)&(lon<=180)&(lat>=-90)&(lat<=90)
    if good.sum()<4:raise ValueError('Insufficient valid geolocation')
    # Spherical Cartesian coordinates avoid lat/lon Euclidean distortion.
    def xyz(lo,la):
        lo=np.radians(lo);la=np.radians(la)
        return np.column_stack([(np.cos(la)*np.cos(lo)).ravel(),(np.cos(la)*np.sin(lo)).ravel(),np.sin(la).ravel()])
    tree=cKDTree(xyz(lon[good],lat[good]))
    xs=np.linspace(bbox[0],bbox[2],width);ys=np.linspace(bbox[3],bbox[1],height)
    grid_lon,grid_lat=np.meshgrid(xs,ys)
    distance,index=tree.query(xyz(grid_lon,grid_lat),distance_upper_bound=1500/6371000,workers=2)
    out=np.full(width*height,-30000,dtype=np.float32);available=np.isfinite(distance)
    out[available]=values[good].ravel()[index[available]]
    return out.reshape(height,width)

def read_coordinates(path):
    with Dataset('coordinates',memory=Path(path).read_bytes()) as data:
        names=list(data.variables)
        lon_name=next((n for n in names if n.lower() in ('lon','longitude','lons')),None)
        lat_name=next((n for n in names if n.lower() in ('lat','latitude','lats')),None)
        if not lon_name or not lat_name:raise ValueError('Official coordinate file lacks recognized lat/lon variables')
        lon=np.ma.filled(data[lon_name][:],np.nan);lat=np.ma.filled(data[lat_name][:],np.nan)
        return np.asarray(lon),np.asarray(lat)

def publish_frame(layer,instant,rgba,source_path,source_id,version,unit,statistics,coordinate_hash,*,label=None,duration_seconds=300):
    identity={'time':instant,'coordinate_hash':coordinate_hash,'source_hash':digest(source_path),'version':version,'unit':unit,'statistics':statistics,'label':label,'duration_seconds':duration_seconds}
    fingerprint=hashlib.sha256(rgba.tobytes()+json.dumps(identity,sort_keys=True).encode()).hexdigest()[:16]
    root=PUBLIC/'weather'/layer;image=root/f'{fingerprint}.png';root.mkdir(parents=True,exist_ok=True);Image.fromarray(rgba).save(image,optimize=True)
    frame={'time':instant,'valid_until':(datetime.fromisoformat(instant)+timedelta(seconds=duration_seconds)).isoformat().replace('+00:00','Z'),'url':'/data/'+image.relative_to(PUBLIC).as_posix(),'bbox':list(REGIONS['korea']),'unit':unit,'source_id':source_id,'source_hash':digest(source_path),'coordinate_hash':coordinate_hash,'image_sha256':digest(image),'statistics':statistics,'evidence_type':'observation','label':label}
    manifest=root/f'{fingerprint}.json';atomic_json(manifest,{'schema_version':1,'frames':[frame]})
    publish_file(manifest,asset_id=f'{layer}-{instant}',layer=layer,format='imagery',bbox=REGIONS['korea'],source_id=source_id,version=version,count=1,**{'from':instant,'to':frame['valid_until'],'cadence_seconds':duration_seconds,'label':label})

def radar(kst):
    dt=datetime.strptime(kst,'%Y%m%d%H%M').replace(tzinfo=timezone(timedelta(hours=9)))
    if dt.minute%5:raise ValueError('HSR time must align to a 5 minute boundary')
    instant=dt.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
    root=LOCAL/'raw'/'kma'
    raw=fetch_kma('/api/typ01/cgi-bin/url/nph-rdr_cmp1_api',{'tm':kst,'cmp':'HSR','qcd':'MSK','obs':'ECHO','map':'HB','disp':'A'},root/f'hsr-{kst}.txt')
    coordinates=fetch_kma('/api/typ01/url/rdr_latlon_file_down.php',{'cmp':'HSR'},root/'hsr-latlon.nc')
    values=parse_hsr_ascii(raw.read_text(encoding='utf-8'));lon,lat=read_coordinates(coordinates)
    projected=geolocate(values,lon,lat);rgba,statistics=radar_rgba(projected)
    publish_frame('radar',instant,rgba,raw,'kma-radar','HSR-MSK-ECHO','dBZ',statistics,digest(coordinates))
    print(json.dumps({'stage':'radar-published','observed_at':instant,**statistics}))

def satellite_cf(path,variable,instant):
    """Import calibrated Kelvin CF NetCDF with explicit lat/lon.

    Raw GK2A digital numbers fail closed until the channel calibration and
    geostationary navigation are supplied and verified against official metadata.
    """
    dt=datetime.fromisoformat(instant)
    if dt.tzinfo is None:raise ValueError('Satellite time requires a timezone')
    with Dataset('satellite',memory=Path(path).read_bytes()) as data:
        if variable not in data.variables:raise ValueError('Satellite variable not found')
        v=data[variable]
        if getattr(v,'units','').lower() not in ('k','kelvin'):raise ValueError('Satellite input must be calibrated Kelvin, not raw digital numbers')
        values=np.asarray(np.ma.filled(v[:],np.nan)).squeeze()
    lon,lat=read_coordinates(path);projected=geolocate(values,lon,lat)
    valid=np.isfinite(projected)&(projected>=150)&(projected<=350)
    rgba=np.zeros((*projected.shape,4),dtype=np.uint8);brightness=np.clip((310-projected)/110,0,1)
    rgba[valid,:3]=235;rgba[valid,3]=(brightness[valid]*185).astype(np.uint8)
    publish_frame('satellite',dt.astimezone(timezone.utc).isoformat().replace('+00:00','Z'),rgba,path,'kma-satellite','calibrated-cf-netcdf','K',{'valid':int(valid.sum()),'missing':int((~valid).sum()),'representation':'infrared brightness; not cloud height or volume'},digest(path))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('kind',choices=['radar','satellite-cf']);p.add_argument('--time',required=True);p.add_argument('--input',type=Path);p.add_argument('--variable');a=p.parse_args()
    if a.kind=='radar':radar(a.time)
    elif a.input and a.variable:satellite_cf(a.input,a.variable,a.time)
    else:p.error('satellite-cf requires --input and --variable')
