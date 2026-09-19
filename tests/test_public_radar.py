import json
import numpy as np
import pytest
from pipeline.public_radar import CRS,EXTENT,validate_metadata,frame_url,project_rgb

def test_metadata_drift_stops_spatial_publication():
    valid='mapInfo["radar.extent"] = '+json.dumps(EXTENT)+'; '+CRS
    validate_metadata(valid)
    with pytest.raises(ValueError):validate_metadata(valid.replace('-440000.0','-430000.0'))
    with pytest.raises(ValueError):validate_metadata(valid.replace('lon_0=126','lon_0=127'))

def test_frame_image_date_and_product_must_match_list():
    row={'tm':'202609161420','url':'/w/cgi-bin/rdr_new/nph-vs_rdr_cmp_img?tm=202609161420&cmp=SFC&obs=HSR&disp=X'}
    assert frame_url(row).startswith('https://www.weather.go.kr/')
    with pytest.raises(ValueError):frame_url({**row,'tm':'202609161425'})
    with pytest.raises(ValueError):frame_url({**row,'url':'https://example.com/image.png'})

def test_rendered_background_is_not_claimed_as_numeric_noecho():
    source=np.full((640,640,3),249,dtype=np.uint8)
    image,stats=project_rgb(source,bbox=(126,36,127,37),width=4,height=4)
    assert image[:,:,3].sum()==0 and stats['background_or_missing_pixels']==16
    source[:]=[0,80,200]
    image,stats=project_rgb(source,bbox=(126,36,127,37),width=4,height=4)
    assert np.all(image[:,:,:3]==[0,80,200]) and np.all(image[:,:,3]==205)
    assert stats['source_grid_m_per_display_pixel']==1600
