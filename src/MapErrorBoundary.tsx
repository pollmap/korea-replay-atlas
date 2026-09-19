import {Component,type ReactNode} from 'react';

export default class MapErrorBoundary extends Component<{children:ReactNode},{failed:boolean}>{
  state={failed:false};
  static getDerivedStateFromError(){return {failed:true};}
  render(){
    if(this.state.failed)return <div className="map-loading" role="alert"><div className="map-error"><strong>지도를 불러오지 못했습니다</strong><p>연결 상태와 브라우저의 그래픽 가속을 확인한 뒤 다시 열어 주세요.</p><button onClick={()=>location.reload()}>지도 다시 불러오기</button></div></div>;
    return this.props.children;
  }
}
