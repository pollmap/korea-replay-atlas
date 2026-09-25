import unittest

from pipeline.map2d_rail_colors import seoul_line


class RailIdentityTests(unittest.TestCase):
    def test_explicit_operating_seoul_route(self):
        row = {'type': 'route', 'route': 'subway', 'name': '서울 지하철 2호선: 내선순환',
               'ref': '2', 'network': '수도권 전철'}
        self.assertEqual(seoul_line(row), '2')
        for changed in ({'name': '2호선'}, {'name': '부산 도시철도 2호선'}, {'ref': '3'},
                        {'network': '부산 도시철도'}, {'construction': 'yes'},
                        {'state': 'in_progress'}, {'type': 'route_master'}):
            self.assertIsNone(seoul_line({**row, **changed}))


if __name__ == '__main__':
    unittest.main()
