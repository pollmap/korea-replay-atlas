/** Audited immutable snapshots only. Never infer an archive from a user URL or a complex ID. */
const ARCHIVES:Readonly<Record<string,{origin:string;artifact:string;appRelease:string}>>={
  'property-8879dff1b31ac5f0':{
    origin:'https://57769488.korea-replay.pages.dev',
    artifact:'30e6b535c1c4e6d22e656c103f86a2ecf969cd53cc88b7c6e9dac0fb6a70146d',
    appRelease:'pub-b71d244ced0bff39',
  },
  'property-8deba5b9951e48da':{
    origin:'https://7ae4ec3d.korea-replay.pages.dev',
    artifact:'235848135096b7f47d0468a5688663a323364a7a87c4e74b6bc5dff488d64b72',
    appRelease:'pub-b71d244ced0bff39',
  },
  'property-87d1c67336e97209':{
    origin:'https://0c88b86f.korea-replay.pages.dev',
    artifact:'1f4d796c585e6fd2bc2a031685ad3cfad4e176fa5143e8146f59ce8ecb2886c2',
    appRelease:'pub-b71d244ced0bff39',
  },
  'property-54bf1817fdcc7bd9':{
    origin:'https://a77a2fcd.korea-replay.pages.dev',
    artifact:'e78aa32db267d446a438963056663a3cb3ecc4427e0d9159bdad4266cbed33b0',
    appRelease:'pub-b71d244ced0bff39',
  },
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
