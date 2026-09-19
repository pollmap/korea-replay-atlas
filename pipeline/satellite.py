"""GK2A infrared L1B: official calibration lookup and GEOS navigation.

Reference: NMSC data processing software, calibration table v3.0.
The public 2022 sample is an archived observation, never a live frame.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile
import numpy as np
from netCDF4 import Dataset
from pyproj import CRS, Transformer
from .core import LOCAL, REGIONS, atomic_json, digest, download
from .weather import publish_frame

SAMPLE_URL='https://nmsc.kma.go.kr/homepage/json/base/resources/selectAtchFile.do?attachFileUsq=41934'
TABLE_URL='https://nmsc.kma.go.kr/homepage/json/base/bbs/selectAtchFile.do?attachFileUsq=40092&refTbUsq=200057'
TABLE_COLUMNS={'SW038':('N','P'),'WV063':('Q','S'),'WV069':('T','V'),'WV073':('W','Y'),'IR087':('Z','AB'),'IR096':('AC','AE'),'IR105':('AF','AH'),'IR112':('AI','AK'),'IR123':('AL','AN'),'IR133':('AO','AQ')}

def calibration_table(path,channel):
    if channel not in TABLE_COLUMNS:raise ValueError('Only calibrated infrared channels are supported')
    rad_col,temp_col=TABLE_COLUMNS[channel]
    radiance=np.full(16384,np.nan);kelvin=np.full(16384,np.nan)
    ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(path) as archive:
        with archive.open('xl/worksheets/sheet1.xml') as stream:
            for _,row in ET.iterparse(stream,events=('end',)):
                if row.tag!='{'+ns['m']+'}row':continue
                values={}
                for cell in row:
                    v=cell.find('m:v',ns)
                    if v is not None and cell.attrib.get('t') not in ('s','e'):
                        values[''.join(c for c in cell.attrib['r'] if c.isalpha())]=float(v.text)
                if 'A' in values and 0<=values['A']<16384:
                    index=int(values['A'])
                    if values['A']!=index:raise ValueError('Noninteger calibration count')
                    radiance[index]=values.get(rad_col,np.nan);kelvin[index]=values.get(temp_col,np.nan)
                row.clear()
    if np.isfinite(kelvin).sum()<1000:raise ValueError('Incomplete official calibration table')
    return radiance,kelvin

def calibrated_pixels(raw,valid_bits,kelvin):
    if not 1<=valid_bits<=14:raise ValueError('Unexpected GK2A valid bit count')
    raw=np.asarray(raw,dtype=np.uint16)
    quality=raw>>14;counts=raw&((1<<valid_bits)-1)
    result=kelvin[counts].astype(np.float32)
    # DQF=1 is conditionally usable; omit it conservatively, along with space/errors.
    result[(quality!=0)|~np.isfinite(result)]=np.nan
    return result,quality

def image_coordinates(lon,lat,metadata):
    a=float(metadata['earth_equatorial_radius']);b=float(metadata['earth_polar_radius'])
    h=float(metadata['nominal_satellite_height'])-a
    projection=CRS.from_proj4(f'+proj=geos +h={h} +a={a} +b={b} +lon_0={np.degrees(metadata["sub_longitude"])} +sweep=y +units=m')
    x,y=Transformer.from_crs('EPSG:4326',projection,always_xy=True).transform(lon,lat)
    columns=metadata['coff']+np.degrees(np.asarray(x)/h)*metadata['cfac']/65536
    rows=metadata['loff']+np.degrees(np.asarray(y)/h)*metadata['lfac']/65536
    return rows,columns

def import_gk2a(path:Path,table:Path,*,official_sample=False):
    # netCDF-C on Windows can reject Unicode paths; bytes preserve the exact input.
    with Dataset('gk2a',memory=path.read_bytes()) as data:
        metadata={name:data.getncattr(name) for name in data.ncattrs()}
        if metadata.get('satellite_name')!='GK-2A' or metadata.get('projection_type')!='GEOS':raise ValueError('Expected GK2A GEOS data')
        if not str(metadata.get('calibration_table_version','')).startswith('v.3.0_'):raise ValueError('Calibration version needs a matching official table')
        pixel=data['image_pixel_values'];channel=str(pixel.channel_name)
        radiance,kelvin=calibration_table(table,channel)
        # Reject a table for the wrong calibration before looking up any temperatures.
        for count in (0,1000):
            expected=count*float(metadata['DN_to_Radiance_Gain'])+float(metadata['DN_to_Radiance_Offset'])
            if abs(radiance[count]-expected)>1e-4:raise ValueError('Calibration table and NetCDF gain/offset disagree')
        west,south,east,north=REGIONS['korea'];width,height=1536,1168
        lon,lat=np.meshgrid(np.linspace(west,east,width),np.linspace(north,south,height))
        rows,columns=image_coordinates(lon,lat,metadata)
        rr=np.rint(rows).astype(int);cc=np.rint(columns).astype(int)
        if np.any(rr<0) or np.any(cc<0) or np.any(rr>=pixel.shape[0]) or np.any(cc>=pixel.shape[1]):raise ValueError('Korea target extends outside input scene')
        r0,r1=int(rr.min()),int(rr.max())+1;c0,c1=int(cc.min()),int(cc.max())+1
        pixel.set_auto_mask(False)
        raw=np.asarray(pixel[r0:r1,c0:c1])[rr-r0,cc-c0]
        values,quality=calibrated_pixels(raw,int(pixel.number_of_valid_bits_per_pixel),kelvin)
        # Actual scene acquisition time, not file generation or download time.
        instant=datetime.strptime(metadata['scene_acquisition_time'],'%Y%m%d_%H%M%S').replace(tzinfo=timezone.utc).isoformat().replace('+00:00','Z')
    valid=np.isfinite(values)&(values>=130)&(values<=400)
    rgba=np.zeros((*values.shape,4),dtype=np.uint8)
    rgba[valid,:3]=235;rgba[valid,3]=(np.clip((310-values[valid])/110,0,1)*210).astype(np.uint8)
    navigation={k:float(metadata[k]) for k in ('earth_equatorial_radius','earth_polar_radius','nominal_satellite_height','sub_longitude','cfac','lfac','coff','loff')}
    nav=LOCAL/'silver'/f'gk2a-navigation-{digest(path)[:16]}.json';atomic_json(nav,navigation)
    statistics={'valid':int(valid.sum()),'missing':int((~valid).sum()),'quality_flag_counts':{str(i):int((quality==i).sum()) for i in range(4)},'min_kelvin':float(values[valid].min()),'max_kelvin':float(values[valid].max()),'channel':channel,'calibration_table_sha256':digest(table),'official_sample':official_sample,'representation':'Infrared brightness temperature, not cloud height; SW038 may contain reflected sunlight by day.','source_resolution_km':float(metadata['channel_spatial_resolution']),'observation_end_seconds_since_2000':float(metadata['observation_end_time'])}
    publish_frame('satellite',instant,rgba,path,'kma-satellite',f'GK2A-{channel}-L1B-v3.0','K',statistics,digest(nav),label='기상청 공식 예제 · 2022-01-01' if official_sample else f'천리안 2A {channel}',duration_seconds=600)
    audit={'stage':'satellite-published','observed_at':instant,'source_sha256':digest(path),**statistics,'navigation':navigation}
    atomic_json(LOCAL/'audit'/'satellite-gk2a.json',audit);print(json.dumps(audit,ensure_ascii=False))

def official_example():
    archive=download(SAMPLE_URL,LOCAL/'raw'/'kma'/'gk2a-official-sample.zip')
    table=download(TABLE_URL,LOCAL/'references'/'gk2a-calibration-v3.0.xlsx')
    name='gk2a_ami_le1b_sw038_fd020ge_202201010000.nc'
    path=LOCAL/'raw'/'kma'/'gk2a-sample-extracted.nc'
    with zipfile.ZipFile(archive) as z:path.write_bytes(z.read(name))
    import_gk2a(path,table,official_sample=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--official-example',action='store_true');parser.add_argument('--input',type=Path);parser.add_argument('--table',type=Path);args=parser.parse_args()
    if args.official_example:official_example()
    elif args.input and args.table:import_gk2a(args.input,args.table)
    else:parser.error('Use --official-example or --input and --table')
