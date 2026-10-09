import {expect,it} from 'vitest';
import {visibleMapPadding} from '../src/property-map-viewport';
it('places the selected apartment inside the actual unobscured rail area',()=>{
 for(const [width,height,rail] of [[1280,720,360],[1366,768,360],[1600,900,360],[1920,1080,640]]){
  const pad=visibleMapPadding(width,height,{left:0,top:92,right:rail,bottom:height,width:rail,height:height-92});
  const x=(width+pad.left-pad.right)/2,y=(height+pad.top-pad.bottom)/2;
  expect(x).toBeGreaterThan(rail);expect(x).toBeLessThan(width-36);expect(y).toBeGreaterThan(108);
 }
});
it('accounts for an explicitly opened bottom sheet and for a closed panel',()=>{
 const pad=visibleMapPadding(692,704,{left:0,top:338,right:692,bottom:704,width:692,height:366});
 expect((704+pad.top-pad.bottom)/2).toBeLessThan(338);
 expect(visibleMapPadding(692,704,null).left).toBe(24);
});
