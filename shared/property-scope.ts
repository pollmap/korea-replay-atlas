/** Current apartment discovery scope. Historical release rows remain readable. */
export const PROPERTY_PRIMARY_CODES:ReadonlySet<string>=new Set(["11110", "11140", "11170", "11200", "11215", "11230", "11260", "11290", "11305", "11320", "11350", "11380", "11410", "11440", "11470", "11500", "11530", "11545", "11560", "11590", "11620", "11650", "11680", "11710", "11740", "26110", "26140", "26170", "26200", "26230", "26260", "26290", "26320", "26350", "26380", "26410", "26440", "26470", "26500", "26530", "26710", "28125", "28155", "28177", "28185", "28200", "28237", "28245", "28275", "28290", "28710", "28720", "30110", "30140", "30170", "30200", "30230", "36110", "41111", "41113", "41115", "41117", "41131", "41133", "41135", "41150", "41171", "41173", "41192", "41194", "41196", "41210", "41220", "41250", "41271", "41273", "41281", "41285", "41287", "41290", "41310", "41360", "41370", "41390", "41410", "41430", "41450", "41461", "41463", "41465", "41480", "41500", "41550", "41570", "41591", "41593", "41595", "41597", "41610", "41630", "41650", "41670", "41800", "41820", "41830", "43111", "43112", "43113", "43114", "44131", "44133", "44200"]);
export const isPrimaryPropertyRegion=(code:string)=>PROPERTY_PRIMARY_CODES.has(code);
export function propertyExplorationRegions<T extends {lawd_code:string}>(regions:readonly T[],selectedCode=''):T[]{
  return regions.filter(row=>isPrimaryPropertyRegion(row.lawd_code)||row.lawd_code===selectedCode);
}
export function propertyProvinceLabel(name:string,regions:readonly {lawd_code:string;name:string}[]=[]):string{
  if(regions.some(row=>row.name.trim().split(/\s+/)[0]===name&&!isPrimaryPropertyRegion(row.lawd_code)))return name;
  return name==='충청남도'?'천안·아산':name==='충청북도'?'청주':name;
}
