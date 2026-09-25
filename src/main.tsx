import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import {startDownloadGate} from './download-gate';
import './styles.css';
import './download-gate.css';
import './property-discovery.css';
import './property-history.css';
import './atlas-shell.css';
import './atlas-property.css';
import './atlas-colors.css';
import './property-map-workspace.css';
import './property-tools.css';

void startDownloadGate().catch(()=>{
  document.body.dataset.downloadGate='unavailable';document.body.dataset.downloadGateReason='startup_failed';document.body.dataset.downloadGateLimit='unavailable';
  const notice=document.getElementById('download-gate-notice')??document.createElement('p');
  notice.id='download-gate-notice';notice.setAttribute('role','status');notice.textContent='이 브라우저에서는 지도 불러오기 최적화가 제한됩니다.';
  if(!notice.isConnected)document.body.appendChild(notice);
  return ()=>{};
}).then(dispose=>{
  if(import.meta.hot)import.meta.hot.dispose(dispose);
  createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
});
