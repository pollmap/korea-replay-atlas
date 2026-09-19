import argparse
import json
from .core import REGIONS

def main():
    parser=argparse.ArgumentParser(description='KOREA REPLAY data pipeline')
    parser.add_argument('command',choices=['country','buildings','tiles','terrain','merge-terrain','transportation','depth','bootstrap','audit'])
    parser.add_argument('--region',choices=list(REGIONS),default='daejeon')
    parser.add_argument('--max-level',type=int,default=10)
    args=parser.parse_args()
    if args.command in ('country','bootstrap'):
        from .geography import country
        country()
    if args.command in ('terrain','bootstrap'):
        from .terrain import terrain
        terrain(args.region,args.max_level)
    if args.command in ('buildings','bootstrap'):
        from .buildings import buildings
        buildings(args.region)
    if args.command in ('tiles','bootstrap'):
        from .mesh import tiles
        tiles(args.region)
    if args.command in ('transportation','bootstrap'):
        from .transportation import transportation
        transportation(args.region)
    if args.command=='audit':
        from .audit import audit
        print(json.dumps(audit(),ensure_ascii=False,indent=2))
    if args.command=='merge-terrain':
        from .terrain import merge_terrain
        merge_terrain()
    if args.command=='depth':
        from .depth import depth
        depth()

if __name__=='__main__':main()
