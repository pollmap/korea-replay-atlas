import unittest
from pipeline.property_revision_archive import review_partition
from pipeline.real_estate import RealEstateError

class Revisions(unittest.TestCase):
    def row(self,identity,**extra):
        return {'id':identity,'complex_id':'molit-apt:11710:x','contract_date':'2026-08-10','area_m2':'84.99','floor':5,'price_krw':1000000000,'source_input_sha256':'a'*64,'retrieved_at':'2026-09-10T00:00:00Z','registration_date':None,'cancellation':'not_reported','statistics_eligible':True,**extra}
    def test_preserves_the_original_registration_revision(self):
        old=self.row('old');new=self.row('new',registration_date='2026-09-15')
        r=review_partition([old],[new]);self.assertEqual(r[0]['state'],'report_fields_updated');self.assertEqual(r[0]['changed_fields'],['registration_date']);self.assertIs(r[0]['previous'],old);self.assertEqual(r[0]['candidate_id'],'new')
    def test_absence_is_not_cancellation_or_an_inferred_identity(self):
        old=self.row('old');new=self.row('new',price_krw=900000000)
        r=review_partition([old],[new]);self.assertEqual(r[0]['state'],'not_in_latest_snapshot');self.assertIsNone(r[0]['candidate_id']);self.assertEqual(r[0]['previous']['cancellation'],'not_reported')
    def test_does_not_collapse_multiple_identical_reports(self):
        r=review_partition([self.row('old1'),self.row('old2')],[self.row('new')]);self.assertEqual(len(r),2);self.assertEqual(sum(x['candidate_id'] is not None for x in r),1)
    def test_unchanged_ids_do_not_consume_new_revision_candidates(self):
        self.assertEqual(review_partition([self.row('same')],[self.row('same')]),[])
        with self.assertRaises(RealEstateError):review_partition([self.row('same'),self.row('same')],[])

if __name__=='__main__':unittest.main()
