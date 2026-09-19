import {afterEach,describe,expect,it,vi} from 'vitest';
import {createCameraSettler,type CameraPose} from '../src/camera-settle';

const pose:CameraPose={position:[4_000_000,3_000_000,4_000_000],direction:[0,0,-1],up:[0,1,0],frustum:[Math.PI/3,16/9]};
const moved=(metres:number):CameraPose=>({...pose,position:[pose.position[0]+metres,pose.position[1],pose.position[2]]});

afterEach(()=>vi.useRealTimers());
describe('camera detail settling',()=>{
  it('restores detail once after a burst of wheel gestures has stayed still for 350ms',()=>{
    vi.useFakeTimers();const settled=vi.fn(),gate=createCameraSettler(settled);
    gate.start();gate.end();vi.advanceTimersByTime(300);expect(settled).not.toHaveBeenCalled();
    gate.start();vi.advanceTimersByTime(500);expect(settled).not.toHaveBeenCalled();
    gate.end();vi.advanceTimersByTime(349);expect(settled).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);expect(settled).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1000);expect(settled).toHaveBeenCalledTimes(1);gate.dispose();
  });
  it('does not update the viewport or terrain after the viewer is disposed',()=>{
    vi.useFakeTimers();const settled=vi.fn(),gate=createCameraSettler(settled);
    gate.end();gate.dispose();gate.start();gate.end();vi.runAllTimers();expect(settled).not.toHaveBeenCalled();
  });
  it('uses the first pose as a baseline and ignores sub-visible position and direction noise',()=>{
    vi.useFakeTimers();const settled=vi.fn(),moving=vi.fn(),gate=createCameraSettler(settled,350,moving);
    expect(gate.observe(pose)).toBe(false);
    for(let frame=0;frame<40;frame++){
      vi.advanceTimersByTime(50);
      expect(gate.observe({...moved(frame%2?0.000001:-0.000001),direction:[frame%2?1e-9:-1e-9,0,-1]})).toBe(false);
    }
    expect(moving).not.toHaveBeenCalled();expect(settled).not.toHaveBeenCalled();gate.dispose();
  });
  it('waits through an actual flight and detects a later movement without any native events',()=>{
    vi.useFakeTimers();const settled=vi.fn(),moving=vi.fn(),gate=createCameraSettler(settled,350,moving);
    gate.observe(pose);
    for(let frame=1;frame<=120;frame++){
      vi.advanceTimersByTime(16);expect(gate.observe(moved(frame*10))).toBe(true);expect(settled).not.toHaveBeenCalled();
    }
    expect(moving).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(349);gate.observe(moved(1200));expect(settled).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);gate.observe(moved(1200));expect(settled).toHaveBeenCalledTimes(1);
    expect(gate.observe(moved(1201))).toBe(true);expect(moving).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(350);gate.observe(moved(1201));expect(settled).toHaveBeenCalledTimes(2);
    gate.end();vi.advanceTimersByTime(1000);gate.observe(moved(1201));expect(settled).toHaveBeenCalledTimes(2);gate.dispose();
  });
  it('settles despite continuous micrometre jitter after meaningful movement',()=>{
    vi.useFakeTimers();const settled=vi.fn(),moving=vi.fn(),gate=createCameraSettler(settled,350,moving);
    gate.observe(pose);gate.observe(moved(1));
    for(let frame=1;frame<=40;frame++){vi.advanceTimersByTime(50);gate.observe(moved(1+(frame%2?0.000001:-0.000001)));}
    expect(moving).toHaveBeenCalledTimes(1);expect(settled).toHaveBeenCalledTimes(1);gate.dispose();
  });
  it('accumulates slow movement against the last meaningful pose instead of erasing each small step',()=>{
    vi.useFakeTimers();const settled=vi.fn(),moving=vi.fn(),gate=createCameraSettler(settled,350,moving);
    gate.observe(pose);
    for(let step=1;step<=12;step++){
      vi.advanceTimersByTime(100);gate.observe(moved(step*.004));expect(settled).not.toHaveBeenCalled();
      if(step<3)expect(moving).not.toHaveBeenCalled();else expect(moving).toHaveBeenCalledTimes(1);
    }
    vi.advanceTimersByTime(350);gate.observe(moved(.048));expect(settled).toHaveBeenCalledTimes(1);gate.dispose();
  });
  it.each([
    ['direction',{...pose,direction:[2e-8,0,-1]}],
    ['up',{...pose,up:[2e-8,1,0]}],
    ['fov',{...pose,frustum:[Math.PI/3+2e-8,16/9]}],
    ['aspect',{...pose,frustum:[Math.PI/3,2]}],
  ] as [string,CameraPose][])('detects meaningful %s changes even without translation',(_,next)=>{
    vi.useFakeTimers();const settled=vi.fn(),moving=vi.fn(),gate=createCameraSettler(settled,350,moving);
    gate.observe(pose);expect(gate.observe(next)).toBe(true);expect(moving).toHaveBeenCalledOnce();
    vi.advanceTimersByTime(350);gate.observe(next);expect(settled).toHaveBeenCalledOnce();gate.dispose();
  });
  it('does not declare an unobserved background flight settled just because the timer elapsed',()=>{
    vi.useFakeTimers();const settled=vi.fn(),moving=vi.fn(),gate=createCameraSettler(settled,350,moving);
    gate.observe(pose);gate.observe(moved(10));vi.advanceTimersByTime(60_000);expect(settled).not.toHaveBeenCalled();
    gate.observe(moved(1000));expect(settled).not.toHaveBeenCalled();expect(moving).toHaveBeenCalledOnce();
    vi.advanceTimersByTime(350);gate.observe(moved(1000));expect(settled).toHaveBeenCalledOnce();gate.dispose();
  });
  it('copies the anchor so mutable Cesium vectors cannot conceal movement',()=>{
    vi.useFakeTimers();const settled=vi.fn(),moving=vi.fn(),gate=createCameraSettler(settled,350,moving);
    const position:[number,number,number]=[...pose.position],frustum:[number,number]=[...pose.frustum],mutable={...pose,position,frustum};
    gate.observe(mutable);position[0]+=.05;expect(gate.observe(mutable)).toBe(true);
    vi.advanceTimersByTime(350);gate.observe(mutable);expect(settled).toHaveBeenCalledOnce();
    frustum[1]=2;expect(gate.observe(mutable)).toBe(true);expect(moving).toHaveBeenCalledTimes(2);gate.dispose();
  });
  it('requires a fresh quiet interval after invalid observations and ignores all observations after dispose',()=>{
    vi.useFakeTimers();const settled=vi.fn(),moving=vi.fn(),gate=createCameraSettler(settled,350,moving);
    gate.observe(pose);gate.observe(moved(1));vi.advanceTimersByTime(349);
    expect(gate.observe({...pose,position:[NaN,0,0]})).toBe(false);vi.advanceTimersByTime(1000);expect(settled).not.toHaveBeenCalled();
    gate.observe(moved(1));vi.advanceTimersByTime(349);gate.observe(moved(1));expect(settled).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);gate.observe(moved(1));expect(settled).toHaveBeenCalledOnce();
    gate.observe(moved(2));gate.dispose();vi.advanceTimersByTime(1000);expect(gate.observe(moved(3))).toBe(false);gate.start();gate.end();
    vi.runAllTimers();expect(settled).toHaveBeenCalledOnce();expect(moving).toHaveBeenCalledTimes(2);
  });
  it('can observe a flight whose native start arrived before the first sample',()=>{
    vi.useFakeTimers();const settled=vi.fn(),moving=vi.fn(),gate=createCameraSettler(settled,350,moving);
    gate.start();gate.observe(pose);expect(moving).toHaveBeenCalledOnce();
    vi.advanceTimersByTime(350);gate.observe(pose);expect(settled).toHaveBeenCalledOnce();gate.dispose();
  });
});
