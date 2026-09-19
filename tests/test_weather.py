import numpy as np
import pytest
from pipeline.weather import parse_hsr_ascii,radar_rgba,geolocate
from pipeline.rail import parse_korail_time
from pipeline.depth import join_key
from pipeline.satellite import calibrated_pixels,image_coordinates

def test_radar_units_missing_and_noecho_are_distinct():
    values=parse_hsr_ascii('# fixture only\n2,2,=\n-30000,-25000,=\n1500,4500,=')
    pixels,counts=radar_rgba(values)
    assert counts=={'valid':2,'no_echo':1,'outside_or_missing':1}
    assert pixels[0,0,3]>0 and pixels[0,1,3]==0
    assert not np.array_equal(pixels[1,0,:3],pixels[1,1,:3])

def test_radar_incomplete_grid_cannot_publish():
    with pytest.raises(ValueError):parse_hsr_ascii('2,2,=\n100,200,=')

def test_geolocation_orientation_is_north_up_without_extrapolation():
    lon=np.array([[127.,127.01],[127.,127.01]])
    lat=np.array([[36.,36.01],[36.02,36.03]])
    values=np.array([[1,2],[3,4]])
    result=geolocate(values,lon,lat,bbox=(127,36,127.01,36.03),width=2,height=2)
    assert result[0,1]==4 and result[1,0]==1
    assert np.all(geolocate(values,lon,lat,bbox=(128,37,128.01,37.01),width=2,height=2)==-30000)

def test_station_dates_cross_midnight_in_kst():
    assert parse_korail_time('2026-09-10 00:32:00.0')=='2026-09-09T15:32:00Z'
    assert parse_korail_time(None) is None

def test_station_join_does_not_mix_lines_or_fuzzy_names():
    assert join_key('2','을지3가')==('2','을지로3가')
    assert join_key('3','을지로3가')!=join_key('2','을지3가')
    assert join_key('7','신대방')!=join_key('7','신대방삼거리')

def test_satellite_quality_bits_never_become_false_cold_clouds():
    lookup=np.arange(16384,dtype=float)+200
    values,quality=calibrated_pixels(np.array([10,16384+10,32768+10,49152+10],dtype=np.uint16),14,lookup)
    assert values[0]==210 and np.isnan(values[1:]).all()
    assert quality.tolist()==[0,1,2,3]

def test_geos_navigation_uses_north_positive_and_source_scan_direction():
    m={'earth_equatorial_radius':6378137.,'earth_polar_radius':6356752.3,'nominal_satellite_height':42164000.,'sub_longitude':np.radians(128.2),'cfac':20425338.903339352,'lfac':-20425338.903339352,'coff':2750.5,'loff':2750.5}
    rows,cols=image_coordinates(np.array([128.2,128.2,129.]),np.array([0.,36.,36.]),m)
    assert rows[0]==pytest.approx(2750.5) and cols[0]==pytest.approx(2750.5)
    assert rows[1]<rows[0] and cols[2]>cols[1]
