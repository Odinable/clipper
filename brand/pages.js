// Links marked data-ext open in the user's normal browser (or mail app), not inside the Clipper window.
document.addEventListener('click', async e => {
  const a = e.target.closest('a[data-ext]');
  if (!a) return;
  e.preventDefault();
  try {
    const r = await fetch('/api/open', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: a.href }) });
    const j = await r.json();
    if (!j.ok) location.href = a.href;
  } catch { location.href = a.href; }
});

function toast(msg) {
  let t = document.querySelector('.toast');
  if (!t) { t = document.createElement('div'); t.className = 'toast'; document.body.appendChild(t); }
  t.textContent = msg; t.classList.add('on');
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('on'), 2600);
}

// "Copy app details" for support requests: settings + the recent log, no stream links.
async function copyDetails() {
  try {
    const s = await (await fetch('/api/state')).json();
    const lines = [
      'Clipper by Growthforge — app details',
      'Date: ' + new Date().toString(),
      'Encoding: ' + (s.nvenc ? 'GPU (NVENC)' : 'CPU'),
      'F9 hotkey: ' + (s.hotkey ? 'on' : 'unavailable'),
      'Live: ' + (s.live ? s.live.phase : 'not running'),
      'Last error: ' + (s.error || 'none'),
      '', 'Recent log:', ...s.log.slice(-40).map(l => l.replace(/https?:\/\/\S+/g, '[link]'))
    ];
    await navigator.clipboard.writeText(lines.join('\n'));
    toast('Copied. Paste it into your email to us.');
  } catch { toast('Couldn\'t copy. Is Clipper running?'); }
}
