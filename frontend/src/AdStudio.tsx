import { useState } from 'react';
import { ArrowRight, Clapperboard, Film, LoaderCircle, RectangleHorizontal, RectangleVertical } from 'lucide-react';

type AdOperation = {
  type: 'cinematic_ad';
  headline: string;
  tagline: string;
  brand: string;
  cta: string;
  duration: 15 | 30;
  aspect: 'portrait' | 'landscape';
  accent: 'coral' | 'teal' | 'blue';
};

export default function AdStudio({ available, hasVideo, busy, locked, onRender }: {
  available: boolean;
  hasVideo: boolean;
  busy: boolean;
  locked: boolean;
  onRender: (operation: AdOperation) => void;
}) {
  const [headline, setHeadline] = useState('');
  const [tagline, setTagline] = useState('');
  const [brand, setBrand] = useState('');
  const [cta, setCta] = useState('Discover more');
  const [duration, setDuration] = useState<15 | 30>(15);
  const [aspect, setAspect] = useState<'portrait' | 'landscape'>('portrait');
  const [accent, setAccent] = useState<'coral' | 'teal' | 'blue'>('coral');
  const disabled = busy || locked || !available || !hasVideo;

  return <section className="ad-studio" aria-label="Cinematic ad studio">
    <div className="ad-intro"><span className="ad-icon"><Clapperboard size={20}/></span><div><h2>Give your clip a story.</h2><p>Camera moves, animated titles, and a finish with your brand.</p></div></div>
    <div className="ad-storyboard" aria-label="Ad scene sequence">
      <span><b>Opening</b><small>Your headline</small></span><ArrowRight size={12}/><span><b>The moment</b><small>Your footage</small></span><ArrowRight size={12}/><span><b>The close</b><small>Your call to action</small></span>
    </div>
    {!hasVideo && <p className="ad-notice">Open a video clip to create an ad.</p>}
    {!available && <p className="ad-notice">The cinematic renderer is not installed on this server yet. Audio editing is available.</p>}
    {locked && <p className="ad-notice">Switch to Edited to create a new ad cut.</p>}
    <form onSubmit={e => { e.preventDefault(); if (!disabled) onRender({type:'cinematic_ad', headline:headline.trim(), tagline:tagline.trim(), brand:brand.trim(), cta:cta.trim(), duration, aspect, accent}); }}>
      <fieldset disabled={disabled}>
        <label>Opening headline<input value={headline} onChange={e=>setHeadline(e.target.value)} maxLength={90} required placeholder="The line that makes them stop scrolling"/></label>
        <label>Supporting line <span>Optional</span><input value={tagline} onChange={e=>setTagline(e.target.value)} maxLength={140} placeholder="Give the moment a little context"/></label>
        <div className="ad-field-pair"><label>Brand<input value={brand} onChange={e=>setBrand(e.target.value)} maxLength={40} required placeholder="Your brand name"/></label><label>Call to action<input value={cta} onChange={e=>setCta(e.target.value)} maxLength={50} required/></label></div>
        <div className="ad-options"><div><span className="ad-label">Format</span><div className="format-toggle"><button type="button" aria-pressed={aspect==='portrait'} onClick={()=>setAspect('portrait')}><RectangleVertical size={15}/> 9:16</button><button type="button" aria-pressed={aspect==='landscape'} onClick={()=>setAspect('landscape')}><RectangleHorizontal size={15}/> 16:9</button></div></div><label>Length<select value={duration} onChange={e=>setDuration(Number(e.target.value) as 15|30)}><option value={15}>15 seconds</option><option value={30}>30 seconds</option></select></label><div><span className="ad-label">Accent</span><div className="ad-swatches">{(['coral','teal','blue'] as const).map(color=><button type="button" key={color} className={`swatch ${color}`} aria-label={`${color} accent`} aria-pressed={accent===color} onClick={()=>setAccent(color)}/>)}</div></div></div>
      </fieldset>
      <button className="render-ad-button" disabled={disabled||!headline.trim()||!brand.trim()||!cta.trim()}>{busy?<LoaderCircle size={16} className="spin"/>:<Film size={16}/>} {busy?'Rendering your edit':'Create cinematic cut'}<span>{duration}s</span></button>
    </form>
    <p className="ad-footnote">Uses your current clip and edited audio. Renders locally without video-generation credits.</p>
  </section>;
}
