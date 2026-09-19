"""Bounded archive of KMA Weather public map frames, with official LCC extent.

RGB is preserved, not inverted into numeric rainfall or radar reflectivity.
White/gray background cannot reliably distinguish no echo from missing data.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone,timedelta
import json
import re
from urllib.parse import urlencode,urljoin,urlparse,parse_qs
import numpy as np
from PIL import Image
from pyproj import Transformer
from .core import LOCAL,REGIONS,atomic_json,digest,download
from .weather import publish_frame

KST=timezone(timedelta(hours=9))
BASE='https://www.weather.go.kr'
METADATA_URL=BASE+'/w/resources/js/kmap.bb.js?ver=202607091110'
EXTENT=(-440000.,3797382.7212162036,584000.,4821382.721216239)
CRS='+proj=lcc +lat_1=30 +lat_2=60 +lat_0=0 +lon_0=126 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs'

def validate_metadata(text):
    match=re.search(r'mapInfo\["radar\.extent"\]\s*=\s*(\[[^\]]+\])',text)
    if not match or not np.allclose(json.loads(match.group(1)),EXTENT,rtol=0,atol=.01):
        raise ValueError('Official radar extent changed; projection review required')
    if CRS not in text:raise ValueError('Official radar CRS changed; projection review required')

def frame_url(row):
    source_url=urljoin(BASE,row['url']);parsed=urlparse(source_url)
    if parsed.scheme!='https' or parsed.netloc!='www.weather.go.kr' or not parsed.path.startswith('/w/cgi-bin/rdr_new/'):
        raise ValueError('Unexpected radar origin')
    query=parse_qs(parsed.query)
    if query.get('tm')!=[row['tm']] or query.get('cmp')!=['SFC'] or query.get('obs')!=['HSR'] or query.get('disp')!=['X']:
        raise ValueError('Radar image timestamp or product does not match manifest')
    return source_url

def project_rgb(rgb,bbox=REGIONS['korea'],width=1536,height=1168):
    if rgb.ndim!=3 or rgb.shape[2]!=3:raise ValueError('Expected RGB radar render')
    west,south,east,north=bbox
    lon,lat=np.meshgrid(np.linspace(west,east,width),np.linspace(north,south,height))
    x,y=Transformer.from_crs('EPSG:4326',CRS,always_xy=True).transform(lon,lat)
    ix=np.floor((x-EXTENT[0])/(EXTENT[2]-EXTENT[0])*rgb.shape[1]).astype(int)
    iy=np.floor((EXTENT[3]-y)/(EXTENT[3]-EXTENT[1])*rgb.shape[0]).astype(int)
    inside=(ix>=0)&(ix<rgb.shape[1])&(iy>=0)&(iy<rgb.shape[0])
    projected=np.full((height,width,3),255,dtype=np.uint8);projected[inside]=rgb[iy[inside],ix[inside]]
    colored=inside&((projected.max(axis=2).astype(int)-projected.min(axis=2))>20)
    rgba=np.zeros((height,width,4),dtype=np.uint8);rgba[colored,:3]=projected[colored];rgba[colored,3]=205
    return rgba,{'colored_pixels':int(colored.sum()),'background_or_missing_pixels':int((~colored).sum()),'background_meaning':'No echo and missing data cannot be distinguished from this rendered product.','representation':'Official SFC-HSR color rendering; no inverse conversion to mm/h or dBZ.','source_grid_m_per_display_pixel':(EXTENT[2]-EXTENT[0])/rgb.shape[1]}

def collect(kst=None,limit=13):
    if not 1<=limit<=25:raise ValueError('Public capture is limited to 25 frames per invocation')
    if kst is None:
        dt=datetime.now(KST)-timedelta(minutes=10);dt=dt.replace(minute=dt.minute//5*5,second=0,microsecond=0);kst=dt.strftime('%Y%m%d%H%M')
    else:dt=datetime.strptime(kst,'%Y%m%d%H%M').replace(tzinfo=KST)
    if dt.minute%5:raise ValueError('Radar time must be a 5 minute boundary')
    url=BASE+'/w/wnuri-img/rest/radar/cmp/images.do?'+urlencode({'data':'SFC-HSR','tm':kst,'timeTerm':5,'leaflet':1,'wgis':1,'unit':'m/s'})
    manifest=download(url,LOCAL/'raw'/'kma'/f'public-radar-list-{kst}.json')
    metadata=download(METADATA_URL,LOCAL/'references'/'kma-kmap-official.js')
    validate_metadata(metadata.read_text(encoding='utf-8'))
    rows=json.loads(manifest.read_text(encoding='utf-8'))
    if not isinstance(rows,list) or not rows:raise ValueError('No official radar frame list')
    excluded=sum(datetime.strptime(r['tm'],'%Y%m%d%H%M').replace(tzinfo=KST)>dt for r in rows)
    rows=[r for r in rows if datetime.strptime(r['tm'],'%Y%m%d%H%M').replace(tzinfo=KST)<=dt]
    seen=set();published=[]
    for row in rows[-limit:]:
        time=row['tm'];observed=datetime.strptime(time,'%Y%m%d%H%M').replace(tzinfo=KST)
        if observed>dt or time in seen:raise ValueError('Unexpected or duplicate frame timestamp')
        seen.add(time);source_url=frame_url(row)
        source=download(source_url,LOCAL/'raw'/'kma'/f'public-radar-{time}.png')
        with Image.open(source) as image:
            if image.size!=(640,640):raise ValueError('Official radar layout changed; extent requires review')
            rgb=np.asarray(image.convert('RGB'))
        rgba,statistics=project_rgb(rgb)
        statistics.update({'source_url':source_url,'frame_list_sha256':digest(manifest),'projection_source_url':METADATA_URL,'crs':CRS,'extent':list(EXTENT)})
        instant=observed.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
        publish_frame('radar',instant,rgba,source,'kma-radar','Weather-SFC-HSR-render','official-color',statistics,digest(metadata),label='기상청 강수 에코 · 공식 색상')
        published.append({'time':instant,'sha256':digest(source),'colored_pixels':statistics['colored_pixels']})
        print(json.dumps({'stage':'public-radar-frame',**published[-1]}),flush=True)
    atomic_json(LOCAL/'audit'/'public-radar.json',{'projection':CRS,'extent':EXTENT,'frames':published,'numeric_inversion':False,'excluded_after_requested_time':excluded})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--time');p.add_argument('--limit',type=int,default=13);a=p.parse_args();collect(a.time,a.limit)
