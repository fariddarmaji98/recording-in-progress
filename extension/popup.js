const $ = (id) => document.getElementById(id);

async function render() {
  const st = await chrome.runtime.sendMessage({ kind: 'status' }).catch(() => null);
  if (st) {
    $('appdot').className = 'dot' + (st.connected ? ' on' : '');
    $('app').textContent = st.connected ? 'Aplikasi meetrec terhubung' : 'Aplikasi meetrec tidak terhubung';
    $('rec').textContent = st.connected ? (st.recording ? 'Sedang merekam: data dikirim (' + st.sent + ' kejadian)' : 'Aplikasi belum merekam: data belum disimpan') : '';
    $('err').textContent = st.lastError || '';
  }
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const onMeet = tab && /^https:\/\/meet\.google\.com\//.test(tab.url || '');
  if (!onMeet) { $('now').textContent = 'Buka tab Google Meet untuk melihat deteksi.'; return; }
  const live = await chrome.tabs.sendMessage(tab.id, { kind: 'get_live' }).catch(() => null);
  if (!live) { $('now').textContent = 'Halaman Meet belum siap. Muat ulang tab Meet sekali.'; return; }
  $('tiles').textContent = live.tiles + ' tile';
  $('now').innerHTML = live.speaking.length ? 'Bicara: <span class="speaking">' + live.speaking.join(', ') + '</span>' : 'Belum ada yang bicara';
  $('totals').innerHTML = live.totals.map((t) => '<li><span>' + t.name.replace(/</g, '&lt;') + '</span><span class="muted">' + t.seconds + ' dtk</span></li>').join('');
  $('caps').textContent = live.captionsFound ? 'Caption terbaca: ya' : 'Caption terbaca: tidak (nyalakan CC untuk sinyal tambahan)';
}

$('cc').onclick = async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab) await chrome.tabs.sendMessage(tab.id, { kind: 'enable_captions' }).catch(() => null);
};
$('opt').onclick = () => chrome.runtime.openOptionsPage();
render();
setInterval(render, 1000);
