/** A hidden retained list still qualifies the selected apartment. Fetch that
 * apartment's bounded summaries, and defer district history until the list opens. */
export function propertyListSummaryComplex(visible:boolean,selectedId?:string):string|undefined{
  return visible?undefined:selectedId||undefined;
}
