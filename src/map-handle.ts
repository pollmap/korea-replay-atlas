import type {Place} from '../shared/contracts';
export interface MapHandle {flyTo:(place:Place,options?:{overviewPanelVisible:boolean;focused:boolean})=>void;north:()=>void;overhead:()=>void;camera:()=>number[]|null;flatCamera?:()=>[number,number,number,number]|null;viewport?:()=>Place|null;}
