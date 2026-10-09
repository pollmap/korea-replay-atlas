/** Use the actual visible panel, including collapsed/dual rails, when placing a selected apartment. */
export function visibleMapPadding(width:number,height:number,panel?:{left:number;top:number;right:number;bottom:number;width:number;height:number}|null,focused=false){
 const top=focused?60:108,right=36;let left=24,bottom=40;
 if(panel&&panel.width>0&&panel.height>0){
  if(panel.top>height*.35&&panel.width>width*.7)bottom=Math.max(bottom,height-panel.top+16);
  else if(panel.left<width*.1)left=Math.max(left,panel.right+24);
 }
 return {top:Math.min(top,height*.25),right,left:Math.min(left,width-100),bottom:Math.min(bottom,height*.65)};
}
