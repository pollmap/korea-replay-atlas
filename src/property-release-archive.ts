/** Audited immutable snapshots only. Never infer an archive from a user URL or a complex ID. */
const ARCHIVES:Readonly<Record<string,{origin:string;artifact:string;appRelease:string}>>={
  'property-eea48453a819f517':{
    origin:'https://ba3762eb.korea-replay.pages.dev',
    artifact:'f538ef5ee072d412f33cb5c0f8fcb876ce44f5b835f100d6d7b52371f0f93c2c',
    appRelease:'pub-b71d244ced0bff39',
  },
};

/** Restore an old v1 property link on the canonical site, without weakening existing deployment pins. */
export function propertyReleaseArchiveUrl(href:string,currentPropertyRelease:string):string|null {
  let current:URL;try{current=new URL(href);}catch{return null;}
  if(current.origin!=='https://korea-replay.pages.dev'||current.username||current.password||current.pathname!=='/'||current.searchParams.has('deployment'))return null;
  const state=new URLSearchParams(current.hash.slice(1)),requested=state.getAll('propertyRelease');
  if(requested.length!==1||!/^property-[a-f0-9]{16}$/.test(requested[0])||requested[0]===currentPropertyRelease)return null;
  const archive=ARCHIVES[requested[0]];
  if(!archive)return null;
  // An explicit source/app-release pin is also authoritative, even without a deployment query.
  const appReleases=state.getAll('release');
  if(appReleases.length>1||appReleases.length===1&&appReleases[0]!==archive.appRelease)return null;
  const destination=new URL(archive.origin);
  destination.search=current.search;
  destination.searchParams.set('deployment',`pages:${destination.hostname.slice(0,8)}:${archive.artifact}`);
  destination.hash=current.hash;
  return destination.href;
}
