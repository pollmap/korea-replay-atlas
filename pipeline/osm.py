"""Stream the country extract into source-backed stations, railway graph and water."""
import json
import osmium
import shapely
from shapely.geometry import shape,mapping,LineString
from .core import LOCAL,PUBLIC,REGIONS,atomic_json,digest,publish_file
from .rail_lifecycle import rail_lifecycle

def extract_osm(path):
    meta=json.loads(path.with_suffix(path.suffix+'.meta.json').read_text(encoding='utf-8'))
    version=meta['retrieved_at'][:10];fingerprint=meta['sha256'][:12]
    factory=osmium.geom.GeoJSONFactory()
    stations=[];rails=[];water=[];edges=[];errors={'geometry':0}
    pilot=[shapely.box(*REGIONS[r]) for r in ('daejeon','sejong','cheongju')]
    def props(identifier,tags,kind):
        return {'name':tags.get('name',''),'kind':kind,'source_id':'osm','dataset_version':version,'source_record_id':identifier,'evidence_type':'source_attribute','description':'OpenStreetMap 공개 공간 자료입니다. 교량·터널의 실측 높이나 승강장 배치를 의미하지 않습니다.'}
    class Handler(osmium.SimpleHandler):
        def node(self,node):
            if node.tags.get('railway') not in ('station','halt'):return
            if not node.location.valid():return
            tags=dict(node.tags)
            if rail_lifecycle(tags)!='existing':return
            properties=props(f'node/{node.id}',tags,'station')
            properties.update({k:tags.get(k) for k in ('operator','station','ref','railway:ref','wikidata','name:en')})
            stations.append({'type':'Feature','id':f'node/{node.id}','geometry':{'type':'Point','coordinates':[node.location.lon,node.location.lat]},'properties':properties})
        def way(self,way):
            if way.tags.get('railway') not in ('rail','subway','light_rail'):return
            if rail_lifecycle(way.tags)!='existing':return
            if way.tags.get('service') in ('yard','siding','spur'):return
            try:coords=[(n.lon,n.lat) for n in way.nodes];ids=[n.ref for n in way.nodes]
            except osmium.InvalidLocationError:errors['geometry']+=1;return
            if len(coords)<2:return
            line=LineString(coords);properties=props(f'way/{way.id}',dict(way.tags),'rail')
            properties.update({'bridge':way.tags.get('bridge'),'tunnel':way.tags.get('tunnel'),'railway':way.tags.get('railway')})
            rails.append({'type':'Feature','id':f'way/{way.id}','geometry':mapping(line.simplify(.000025)),'properties':properties})
            for index in range(len(ids)-1):edges.append([ids[index],ids[index+1],*coords[index],*coords[index+1],way.id])
        def area(self,area):
            if area.tags.get('natural')!='water' and area.tags.get('waterway')!='riverbank':return
            try:geom=shape(json.loads(factory.create_multipolygon(area)))
            except (RuntimeError,ValueError):errors['geometry']+=1;return
            if geom.is_empty or not geom.is_valid:return
            near=any(geom.intersects(b) for b in pilot)
            if not near and geom.area<.00005:return
            geom=geom.simplify(.00001 if near else .0001,preserve_topology=True)
            water.append({'type':'Feature','id':f'area/{area.id}','geometry':mapping(geom),'properties':props(f'area/{area.id}',dict(area.tags),'water')})
    handler=Handler();handler.apply_file(str(path),locations=True,idx='flex_mem')
    for name,features,layer in [('stations',stations,'rail'),('rail',rails,'rail'),('water',water,'terrain')]:
        output=PUBLIC/'osm'/f'{name}-{fingerprint}.geojson';atomic_json(output,{'type':'FeatureCollection','features':features})
        publish_file(output,asset_id=f'osm-{name}-korea',layer=layer,format='geojson',bbox=REGIONS['korea'],source_id='osm',version=version,count=len(features))
    atomic_json(LOCAL/'silver'/'rail-graph.json',{'source_hash':meta['sha256'],'version':version,'edges':edges})
    report={'stations':len(stations),'rail_ways':len(rails),'water_areas':len(water),'graph_edges':len(edges),'errors':errors,'source_sha256':meta['sha256']}
    atomic_json(LOCAL/'audit'/'osm-korea.json',report);print(json.dumps(report))

if __name__=='__main__':
    import argparse
    from pathlib import Path
    p=argparse.ArgumentParser();p.add_argument('pbf',type=Path);a=p.parse_args();extract_osm(a.pbf)
