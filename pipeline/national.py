"""Compatibility entrypoint for 2D footprint partitioning; 3D process is retired."""
import argparse
from .retired_3d import MESSAGE, retired

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['partition','process'])
    parser.add_argument('--limit',type=int,default=0)
    parser.add_argument('--workers',type=int,default=1)
    args=parser.parse_args()
    if args.command=='process':
        parser.error(MESSAGE)
    from .building_partitions import partition
    partition()

process=retired
if __name__=='__main__':
    main()
