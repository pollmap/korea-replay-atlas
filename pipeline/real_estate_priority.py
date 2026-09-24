"""User-selected acquisition order; does not change public coverage or source codes."""
POLICY_ID = 'capital-south-gyeonggi-chungcheong-20260924'
GROUPS = (
    '서울·인천', '경기 남부', '대전', '세종', '청주',
    '나머지 경기·충남·충북', '부산·경상권', '나머지 전국',
)
# Operational collection scope, not a new administrative-code classification.
SOUTH_GYEONGGI = frozenset((
    '수원시', '용인시', '성남시', '부천시', '화성시', '안산시', '안양시',
    '평택시', '시흥시', '김포시', '광주시', '광명시', '하남시', '군포시',
    '오산시', '이천시', '안성시', '과천시', '의왕시', '양평군', '여주시',
))


def region_priority(region):
    code = region['lawd_code']
    province = code[:2]
    city = region.get('name', '').split()
    city = city[1] if len(city) > 1 else ''
    if province in ('11', '28'):
        return 0
    if province == '41' and city in SOUTH_GYEONGGI:
        return 1
    if province == '30':
        return 2
    if province == '36':
        return 3
    if province == '43' and city == '청주시':
        return 4
    if province in ('41', '43', '44'):
        return 5
    if province in ('26', '27', '31', '47', '48'):
        return 6
    return 7


def priority_map(regions):
    return {row['lawd_code']: region_priority(row) for row in regions}
