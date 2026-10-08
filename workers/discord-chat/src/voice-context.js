export function validVoiceContext(value) {
  return value === undefined || (Array.isArray(value) && value.length <= 12 &&
    value.every(e=>e && typeof e==='object' && !Array.isArray(e) && Object.keys(e).every(k=>k==='userId'||k==='text') &&
      typeof e.userId==='string' && /^[1-9][0-9]{0,19}$/.test(e.userId) && BigInt(e.userId)<2n**64n &&
      typeof e.text==='string' && e.text.trim().length>0 && e.text.length<=2000) &&
    value.reduce((n,e)=>n+e.text.length,0)<=2000);
}
