/** Keep a menu-selected section at the panel top while earlier lazy content loads.
 * User input releases the anchor; data arriving later must not undo their scroll.
 */
export function retainDetailAnchor(body:HTMLElement,target:HTMLElement,fromStart=false):()=>void{
  let active=true,frame:number|undefined;
  const observer=new ResizeObserver(()=>{
    if(!active||frame!==undefined)return;
    frame=requestAnimationFrame(()=>{frame=undefined;align();});
  });
  const stop=()=>{
    if(!active)return;
    active=false;observer.disconnect();
    if(frame!==undefined)cancelAnimationFrame(frame);
    for(const event of ['wheel','touchmove','pointerdown','keydown'])body.removeEventListener(event,stop,true);
  };
  const align=()=>{
    if(!active)return;
    if(!body.contains(target)){stop();return;}
    const top=fromStart?0:body.scrollTop+target.getBoundingClientRect().top-body.getBoundingClientRect().top;
    if(Math.abs(body.scrollTop-top)>1)body.scrollTo({top,behavior:'instant'});
  };
  for(const event of ['wheel','touchmove','pointerdown','keydown'])body.addEventListener(event,stop,{capture:true,passive:true});
  observer.observe(body);
  for(const section of body.querySelectorAll<HTMLElement>('[data-detail-section]'))observer.observe(section);
  align();
  return stop;
}
