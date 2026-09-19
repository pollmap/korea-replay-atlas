import {createLiveBroker,fixedLiveBrokerStub,LIVE_BROKER_PATH,LIVE_BROKER_STATUS_PATH} from './live-broker';
import {SqliteLiveBrokerStore} from './live-broker-store';
import {fetchCanonicalTransitSnapshot,type LiveTransitEnv} from './live-transit';
import targets from '../config/live-transit-routes.json';

export interface LiveBrokerEnv extends LiveTransitEnv {
  LIVE_TRANSIT_COORDINATOR:DurableObjectNamespace;
}

/** The namespace is private and always addressed through one fixed object ID. */
export class TransitCoordinator {
  private readonly broker:ReturnType<typeof createLiveBroker>;
  constructor(ctx:DurableObjectState,env:LiveBrokerEnv) {
    this.broker=createLiveBroker({
      store:new SqliteLiveBrokerStore(ctx.storage),
      credentials:kind=>kind==='bus'?env.DATA_GO_KR_SERVICE_KEY:env.SEOUL_SUBWAY_API_KEY,
      subwayLines:targets.subway_lines,
      allowSubwayHttp:env.SEOUL_SUBWAY_ALLOW_HTTP==='true',
      source:context=>fetchCanonicalTransitSnapshot({
        ...context,
        env:context.kind==='bus'?{DATA_GO_KR_SERVICE_KEY:context.credential}
          :{SEOUL_SUBWAY_API_KEY:context.credential,SEOUL_SUBWAY_ALLOW_HTTP:env.SEOUL_SUBWAY_ALLOW_HTTP},
      }),
    });
  }
  fetch(request:Request):Promise<Response> {return this.broker.fetch(request);}
}

export default {
  async fetch(request:Request,env:LiveBrokerEnv):Promise<Response> {
    const url=new URL(request.url);
    if(url.search||!(request.method==='POST'&&url.pathname===LIVE_BROKER_PATH||request.method==='GET'&&url.pathname===LIVE_BROKER_STATUS_PATH))return new Response(null,{status:404});
    try {return await fixedLiveBrokerStub(env.LIVE_TRANSIT_COORDINATOR).fetch(request);}
    catch {return Response.json({protocol:1,ok:false,httpStatus:503,error:{code:'broker_unavailable',retry_after_seconds:30}},
      {status:503,headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});}
  },
} satisfies ExportedHandler<LiveBrokerEnv>;
