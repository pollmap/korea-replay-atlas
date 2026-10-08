from pipeline.buildings import height_of

def test_height_evidence_and_missing_data():
    assert height_of({'height':25,'num_floors':3})==(25,'source_attribute')
    assert height_of({'num_floors':3})==(9,'estimate')
    assert height_of({'height':float('nan')})==(None,'unverified')
    assert height_of({'height':-5})==(None,'unverified')
