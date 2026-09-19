import numpy as np
import pytest
from pipeline.buildings import height_of
from pipeline.terrain import tile_bounds,grid_indices
from pipeline.mesh import frame

def test_height_evidence_and_missing_data():
    assert height_of({'height':25,'num_floors':3})==(25,'source_attribute')
    assert height_of({'num_floors':3})==(9,'estimate')
    assert height_of({'height':float('nan')})==(None,'unverified')
    assert height_of({'height':-5})==(None,'unverified')

def test_tms_adjacent_tiles_share_exact_edges():
    a=tile_bounds(12,6997,2874);b=tile_bounds(12,6998,2874)
    assert a[2]==b[0]
    assert tile_bounds(0,0,0)==(-180,-90,0,90)
    assert tile_bounds(0,1,0)==(0,-90,180,90)

def test_grid_has_all_faces_without_invalid_indices():
    indices=grid_indices(65)
    assert indices.shape==(64*64*2,3)
    assert indices.min()==0 and indices.max()==65*65-1
    assert len(np.unique(indices))==65*65

def test_enu_frame_is_right_handed_and_ecef_transform_matches():
    origin,rotation,transform=frame(127.433,36.332)
    assert np.linalg.det(rotation)==pytest.approx(1)
    assert np.allclose(rotation.T@rotation,np.eye(3))
    assert np.allclose(np.array(transform).reshape((4,4),order='F')[:3,3],origin)
