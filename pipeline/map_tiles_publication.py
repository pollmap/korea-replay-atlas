"""Publish a bounded completed subset of a work index without changing its inputs.

The source connection is read-only and held at one committed SQLite snapshot.
This permits early UI verification while later themes continue to build.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sqlite3

from .admin_boundaries import encoded,immutable,require
from .core import digest
from .map_tiles import (SOURCE_CATALOG,TOPICS,TO_GEO,HARD,LOW_TARGET,DETAIL_TARGET,
    pack_archives,pack_details,reserve,sha)


def publish_completed(work,output,selected=('admin-sido',)):
    work=Path(work).resolve();output=Path(output).resolve()
    require(not output.exists(),'Early candidate output must be a new directory')
    inputs=json.loads((work/'inputs.json').read_bytes())
    fingerprint=sha(encoded(inputs))
    db=sqlite3.connect(f'file:{(work/"index.sqlite").as_posix()}?mode=ro',uri=True)
    db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
    require(db.execute("SELECT value FROM meta WHERE key='fingerprint'").fetchone()==(fingerprint,),'Work input identity changed')
    completed={}
    for topic in selected:
        require(topic in TOPICS,'Unknown topic')
        count=db.execute('SELECT COUNT(*) FROM records WHERE topic=?',(topic,)).fetchone()[0]
        require(count>0,'No source records for topic')
        audits={}
        for z in range(TOPICS[topic][0],TOPICS[topic][1]+1):
            stage=db.execute('SELECT value FROM stages WHERE key=?',(f'{topic}-z{z}',)).fetchone()
            if not stage:break
            audits[str(z)]=json.loads(stage[0])
        require(audits,'No committed zoom is available')
        require(audits[str(max(map(int,audits)))]['represented_feature_count']==count,
            'Partial topic does not represent all its indexed source identities')
        completed[topic]={'feature_count':count,'audits':audits}
    identity={'publication_version':'completed-map-topics-1','work_fingerprint':fingerprint,
        'completed_topics':completed,'publication_transform_sha256':digest(Path(__file__))}
    release='map2d-'+sha(encoded(identity))[:20]
    output.mkdir(parents=True);reserve(output)
    base=output/'data/map-tiles'/release;prefix=f'/data/map-tiles/{release}';topics=[]
    for topic,info in completed.items():
        minzoom=TOPICS[topic][0];maxzoom=max(map(int,info['audits']))
        first=(4**minzoom-1)//3;last=(4**(maxzoom+1)-1)//3-1
        a,b,c,d=db.execute('SELECT MIN(s.minx),MIN(s.miny),MAX(s.maxx),MAX(s.maxy) FROM records r JOIN spatial s ON r.n=s.n WHERE r.topic=?',(topic,)).fetchone()
        lo,la=TO_GEO.transform(a,b);hi,ha=TO_GEO.transform(c,d);bounds=[lo,la,hi,ha]
        chunks=pack_archives(db.execute('SELECT tileid,body FROM tiles WHERE topic=? AND tileid BETWEEN ? AND ? ORDER BY tileid',(topic,first,last)),topic,bounds,base/topic/'tiles',prefix+'/'+topic+'/tiles')
        require(sum(c['tile_count'] for c in chunks)==sum(a['tile_count'] for a in info['audits'].values()),'Tile stage inventory changed')
        details=pack_details(db,topic,base/topic/'details',prefix+'/'+topic+'/details')
        topics.append({'id':topic,'source_layer':topic,'minzoom':minzoom,'maxzoom':maxzoom,
            'display_id_hex_length':inputs.get('display_id_hex_length',64),
            'feature_count':info['feature_count'],'bounds':bounds,'chunks':chunks,'details':details,
            'description':'Nationwide SGIS administrative boundary; not legal-dong' if topic.startswith('admin-') else 'Verified bounded source subset',
            'geometry_precision':'MVT display grid 8192; source geometry preserved; overzoom magnifies pixel error'})
    db.close()
    source=json.loads(SOURCE_CATALOG.read_bytes())
    require(digest(SOURCE_CATALOG)==inputs['source_catalog_sha256'],'Source catalog no longer matches work input')
    catalog={'schema_version':1,'release_id':release,'bounds':[124.5,33,132,38.7],'topics':topics,
        'sources':[*source['sources'],{'id':'sgis','title':'SGIS census administrative boundaries','url':'https://www.data.go.kr/data/15129688/fileData.do','license':'이용허락범위 제한 없음','description':'2025-06-30 administrative boundaries; not legal-dong'}],
        'source_release_id':source['release_id'],'reference_dates':{'sgis':'2025-06-30'},
        'attribution':'SGIS 국가데이터처 · source attribution retained in selection details',
        'coverage':{'profile':'completed-topics-only','topics':list(completed),
            'excluded_topics':[t for t in TOPICS if t not in completed],
            'basemap_proof_bbox':inputs.get('proof_bbox'),'admin_scope':'nationwide'}}
    ref=immutable(base/'catalog.json',catalog)
    files=[{'path':p.relative_to(output).as_posix(),'sha256':digest(p),'byte_length':p.stat().st_size}
        for p in sorted(base.rglob('*')) if p.is_file()]
    require(len(files)<3870,'Map publication must leave property-service headroom')
    entry={'path':(base/'catalog.json').relative_to(output).as_posix(),'sha256':ref['sha256'],'release_id':release}
    report={'schema_version':1,'status':'validated','profile':'completed-topics-only','map_catalog':entry,
        'files':files,'file_count':len(files),'bytes':sum(f['byte_length'] for f in files),
        'sources':inputs,'topics':completed,'identity':identity,
        'limits':{'archive_hard_bytes':HARD,'low_zoom_target_bytes':LOW_TARGET,'detail_target_bytes':DETAIL_TARGET},
        'limitations':['Only listed topics and zooms are published; this is not the final nationwide basemap.',
            'Original geometry remains in preserved source files; MVT is a display representation.',
            'No legal-dong crosswalk or official address location is inferred.',
            'Offline ID, byte and schema validation is not HTTP or GPU rendering validation.']}
    immutable(output/'publication.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--topics',default='admin-sido')
    args=parser.parse_args();result=publish_completed(args.work,args.output,tuple(args.topics.split(',')))
    print(json.dumps({k:result[k] for k in ('status','file_count','bytes','map_catalog')},ensure_ascii=False))


if __name__=='__main__':main()
