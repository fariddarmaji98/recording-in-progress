const $ = (id) => document.getElementById(id);
const DEFAULTS = { port: 47831, token: '', sensitivity: 'normal', autoCaptions: false };

chrome.storage.local.get(DEFAULTS).then((c) => {
  $('token').value = c.token; $('port').value = c.port; $('sens').value = c.sensitivity; $('auto').checked = c.autoCaptions;
});

async function save() {
  await chrome.storage.local.set({ token: $('token').value.trim(), port: parseInt($('port').value, 10) || 47831, sensitivity: $('sens').value, autoCaptions: $('auto').checked });
  $('msg').textContent = 'Tersimpan. Muat ulang tab Meet agar kepekaan baru dipakai.';
}
$('save').onclick = save;
$('test').onclick = async () => {
  await save();
  const st = await chrome.runtime.sendMessage({ kind: 'test' });
  $('msg').textContent = st && st.connected ? ('Terhubung ke meetrec' + (st.recording ? ' (sedang merekam)' : ' (belum merekam)')) : ('Gagal: ' + ((st && st.lastError) || 'tidak ada jawaban'));
};
