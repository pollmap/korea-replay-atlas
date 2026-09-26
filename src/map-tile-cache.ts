/** Own decoded bytes; MapLibre may transfer and detach every returned buffer. */
export class DecodedMapTileCache {
  private entries=new Map<string,ArrayBuffer>();
  private bytes=0;
  private hits=0;
  constructor(private readonly limit:number,private readonly entryLimit=512){}
  get(key:string):ArrayBuffer|undefined {
    const value=this.entries.get(key);
    if(!value)return;
    this.entries.delete(key);this.entries.set(key,value);this.hits++;
    return value.slice(0);
  }
  set(key:string,value:ArrayBuffer):void {
    if(this.limit===0||value.byteLength>this.limit)return;
    const old=this.entries.get(key);if(old){this.entries.delete(key);this.bytes-=old.byteLength;}
    while(this.entries.size&&(this.bytes+value.byteLength>this.limit||this.entries.size>=this.entryLimit)){
      const [first,body]=this.entries.entries().next().value!;
      this.entries.delete(first);this.bytes-=body.byteLength;
    }
    const owned=value.slice(0);this.entries.set(key,owned);this.bytes+=owned.byteLength;
  }
  snapshot(){return {decodedTileBytes:this.bytes,decodedTileCount:this.entries.size,decodedTileHits:this.hits,decodedTileLimitBytes:this.limit};}
  clear(){this.entries.clear();this.bytes=0;}
}
