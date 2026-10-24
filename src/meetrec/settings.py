"""Pengaturan: a window with a menu on the left (General, Cloud, ...) and the page of the chosen entry on the right.

General: recording options, stored in QSettings when you press Simpan.
Cloud:   rclone setup and cloud accounts. These act at once (they are not part of Simpan); accounts live in
         meetrec's own rclone.conf (see cloud.py).
"""
from __future__ import annotations

from PySide6.QtCore import QSettings, QSize, Qt, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QInputDialog, QMessageBox, QProgressBar, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from . import cloud, mcpconn, upload
from .job import Job
from .meetbridge import DEFAULT_PORT
from .widgets import BUTTON_STYLE, FG, SECTION_STYLE, IconButton, Section, SidebarPages, make_icon

AUDIO_RATES = [(16000, "16 kHz: hemat tempat, cukup untuk suara"), (24000, "24 kHz"), (48000, "48 kHz: kualitas tertinggi")]
VIDEO_FPS = [4, 8, 15, 30]

DIALOG_STYLE = f"""
QDialog {{ background: #1c2029; }}
QLabel {{ color: {FG}; font-size: 13px; background: transparent; }}
#status {{ color: #8f98ad; font-size: 11px; }}
#pageTitle {{ font-size: 16px; font-weight: 600; }}
QPushButton {{ background: #2a3040; color: {FG}; border: none; border-radius: 8px; padding: 6px 12px; font-size: 13px; }}
QPushButton:hover {{ background: #384058; }}
QPushButton:disabled {{ background: #232836; color: #5d667c; }}
QCheckBox {{ color: {FG}; }}
QComboBox, QLineEdit {{ background: #2a3040; border: none; border-radius: 8px; padding: 5px 10px; color: {FG}; }}
QComboBox:disabled, QLineEdit:disabled {{ color: #5d667c; }}
QProgressBar {{ background: #2a3040; border: none; border-radius: 5px; height: 10px; text-align: center; color: {FG}; font-size: 10px; }}
QProgressBar::chunk {{ background: #4a6cf7; border-radius: 5px; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
#accountCard {{ background: #232836; border: 1px solid #2c3242; border-radius: 10px; }}
#accountCard QLabel {{ background: transparent; }}
#accountCard[selected="true"] {{ border: 1px solid #2ea043; }}
#plusCard {{ background: transparent; border: 1px dashed #3a4258; border-radius: 10px; min-height: 52px; padding: 0; }}
#plusCard:hover {{ background: #232836; border-color: #4a6cf7; }}
#plusCard:disabled {{ border-color: #2c3242; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #3a4258; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
""" + BUTTON_STYLE + SECTION_STYLE


# --- General -----------------------------------------------------------------------------------------
class GeneralPage(QWidget):
    def __init__(self, settings: QSettings, token: str, port: int):
        super().__init__()
        form = QFormLayout()
        self.video = QCheckBox("Rekam layar (video.mp4)")
        self.video.setChecked(settings.value("record_video", True, type=bool))
        self.fps = QComboBox()
        for f in VIDEO_FPS:
            self.fps.addItem(f"{f} fps", f)
        self.fps.setCurrentIndex(max(0, self.fps.findData(settings.value("video_fps", 8, type=int))))
        self.rate = QComboBox()
        for r, label in AUDIO_RATES:
            self.rate.addItem(label, r)
        self.rate.setCurrentIndex(max(0, self.rate.findData(settings.value("audio_rate", 16000, type=int))))
        form.addRow(self.video)
        form.addRow("FPS video", self.fps)
        form.addRow("Kualitas audio", self.rate)
        self.bridge = QCheckBox("Terima data dari ekstensi Chrome (siapa bicara di Meet)")
        self.bridge.setChecked(settings.value("bridge_enabled", True, type=bool))
        form.addRow(self.bridge)
        self.token = QLineEdit(token)
        self.token.setReadOnly(True)
        copy = QPushButton("Salin")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.token.text()))
        row = QHBoxLayout()
        row.addWidget(self.token, 1)
        row.addWidget(copy)
        form.addRow("Kode ekstensi Chrome", row)
        hint = QLabel(f"Tempel kode ini di Opsi ekstensi. Aplikasi mendengarkan di 127.0.0.1:{port} (hanya komputer ini).", objectName="status")
        hint.setWordWrap(True)
        form.addRow(hint)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addLayout(form)
        v.addStretch(1)
        self.video.toggled.connect(self.fps.setEnabled)
        self.fps.setEnabled(self.video.isChecked())


# --- Cloud -------------------------------------------------------------------------------------------
class AccountCard(QFrame):
    """One cloud account as a card: three text lines (name · platform / state / folder) and three square buttons
    (select, folder, delete). The selected card is the upload destination; only one can be selected."""

    def __init__(self, name: str, kind: str):
        super().__init__()
        self.setObjectName("accountCard")
        self.name = name
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(10)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = QLabel(f"<b>{name}</b>  ·  {kind}")
        self.status = QLabel("", objectName="status")
        self.status.setWordWrap(True)
        self.folder = QLabel("", objectName="status")
        text.addWidget(self.title)
        text.addWidget(self.status)
        text.addWidget(self.folder)
        self.select_btn = IconButton("check", "Pilih sebagai tujuan upload (sekalian menguji koneksi)")
        self.folder_btn = IconButton("folder", "Ubah folder tujuan di cloud")
        self.delete_btn = IconButton("trash", "Hapus akun dari meetrec", tone="danger")
        row.addLayout(text, 1)
        for b in (self.select_btn, self.folder_btn, self.delete_btn):
            row.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        self.select_btn.setProperty("tone", "primary" if on else "default")
        for w in (self, self.select_btn):
            w.style().unpolish(w)
            w.style().polish(w)

    def set_status(self, text: str, ok: bool | None = None) -> None:
        color = {True: "#7fd18b", False: "#ff6b6b", None: "#8f98ad"}[ok]
        self.status.setText(f'<span style="color:{color}">{text}</span>')

    def set_folder(self, folder: str) -> None:
        self.folder.setText(f"Folder tujuan: {folder or '(level atas)'}")


class McpCard(QFrame):
    """One MCP server as a card: name · url, a state line, the tool list once connected, and the icon
    buttons of THAT state — Sambung when idle, Putus + Restart when connected (never all three)."""

    connect_clicked = Signal()
    disconnect_clicked = Signal()
    restart_clicked = Signal()

    def __init__(self, name: str, title: str, url: str, key: str = ""):
        super().__init__()
        self.name = name
        self.setObjectName("accountCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(10)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = QLabel(f"<b>{title}</b>  ·  MCP")
        self.status = QLabel("", objectName="status")
        self.status.setWordWrap(True)
        self.tools = QLabel("", objectName="status")
        self.tools.setWordWrap(True)
        self.tools.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text.addWidget(self.title)
        text.addWidget(self.status)
        text.addWidget(self.tools)
        self.connect_btn = IconButton("play", "Sambungkan ke server MCP")
        self.disconnect_btn = IconButton("close", "Putuskan sambungan", tone="danger")
        self.restart_btn = IconButton("undo", "Restart: putus, hapus cache koneksi, lalu sambung ulang")
        row.addLayout(text, 1)
        for b in (self.connect_btn, self.disconnect_btn, self.restart_btn):
            row.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(row)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.url_edit = QLineEdit(url)
        self.url_edit.setPlaceholderText(mcpconn.DEFAULT_URL)
        self.key_edit = QLineEdit(key)
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("opsional — MCP publik tanpa kunci biarkan kosong")
        form.addRow("URL", self.url_edit)
        form.addRow("Key", self.key_edit)
        outer.addLayout(form)
        self.connect_btn.clicked.connect(self.connect_clicked.emit)
        self.disconnect_btn.clicked.connect(self.disconnect_clicked.emit)
        self.restart_btn.clicked.connect(self.restart_clicked.emit)

    def refresh(self, conn: "mcpconn.Connection", busy: bool = False) -> None:
        st = conn.state
        # visibility follows the CONNECTION, disabled-ness follows the running job: pressing Sambung must
        # not make the button vanish (the user would have no idea what happened).
        self.connect_btn.setVisible(st != "connected")
        self.disconnect_btn.setVisible(st == "connected")
        self.restart_btn.setVisible(st == "connected")
        for b in (self.connect_btn, self.disconnect_btn, self.restart_btn):
            b.setEnabled(not busy)
        if st == "connected":
            s = conn.server
            who = " ".join(x for x in (s.get("name", ""), s.get("version", "")) if x) or "server MCP"
            self.set_status(f"Tersambung ke {who} ({conn.url})", True)
            names = [t["name"] for t in conn.tools]
            self.tools.setText(f"{len(names)} tool: " + (", ".join(names) if names else "(tidak ada)"))
        elif st == "connecting":
            self.set_status(f"Menyambung ke {conn.url}…", None)
            self.tools.setText("")
        else:
            self.set_status(f"Belum tersambung ({conn.url})", None)
            self.tools.setText("")

    def set_status(self, text: str, ok: bool | None = None) -> None:
        color = {True: "#7fd18b", False: "#ff6b6b", None: "#8f98ad"}[ok]
        self.status.setText(f'<span style="color:{color}">{text}</span>')


# one Connection per MCP server, kept for the life of the process: closing the settings window must not
# tear down a live session (and reopening it must show the same state).
CONNECTIONS: dict[str, "mcpconn.Connection"] = {}


def mcp_connection(name: str, url: str) -> "mcpconn.Connection":
    if name not in CONNECTIONS:
        CONNECTIONS[name] = mcpconn.Connection(name, url)
    return CONNECTIONS[name]


class McpPage(QScrollArea):
    """MCP page: the servers meetrec can talk to, each with its own connect / disconnect / restart.

    URL and key are typed here (key optional: public MCP servers need none) and kept in QSettings,
    NOT in ~/.meetrec/mcp — that directory is the connection cache and Restart wipes it.
    """

    SERVERS = [("transkrip", "Ultraman Transkrip", mcpconn.DEFAULT_URL)]

    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.settings = settings or QSettings()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._job: Job | None = None
        self._cards: dict[str, McpCard] = {}

        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 8, 0)
        v.setSpacing(12)
        sec = Section("Server MCP")
        hint = QLabel("Server MCP dijalankan oleh project pemiliknya (di sini: ultraman-transkrip, `python -m mcp_server` "
                      "di port 8100). Meetrec hanya menyambung — tombol Sambung tidak menyalakan servernya. "
                      "URL dan Key disimpan di setelan meetrec; Key opsional (MCP publik tanpa kunci biarkan kosong). "
                      "Restart = putus, hapus cache koneksi (~/.meetrec/mcp), lalu sambung lagi.",
                      objectName="status")
        hint.setWordWrap(True)
        sec.body.addWidget(hint)
        self.list = QVBoxLayout()
        self.list.setSpacing(8)
        for name, title, url in self.SERVERS:
            card = McpCard(name, title, self._load(name, "url", url), self._load(name, "key", ""))
            card.connect_clicked.connect(lambda n=name: self.run("connect", n))
            card.disconnect_clicked.connect(lambda n=name: self.run("disconnect", n))
            card.restart_clicked.connect(lambda n=name: self.run("restart", n))
            card.url_edit.editingFinished.connect(lambda n=name: self.save(n))
            card.key_edit.editingFinished.connect(lambda n=name: self.save(n))
            self._cards[name] = card
            self.list.addWidget(card)
        sec.body.addLayout(self.list)
        self.msg = QLabel("", objectName="status")
        self.msg.setWordWrap(True)
        sec.body.addWidget(self.msg)
        v.addWidget(sec)
        v.addStretch(1)
        self.setWidget(inner)
        self.refresh()

    def _load(self, name: str, field: str, default: str) -> str:
        return str(self.settings.value(f"mcp/{name}/{field}", default) or default)

    def save(self, name: str) -> None:
        """Persist what is typed on the card and push it into the (not-yet-connected) connection."""
        card, conn = self._cards[name], self.conn(name)
        url = card.url_edit.text().strip() or mcpconn.DEFAULT_URL
        key = card.key_edit.text().strip()
        self.settings.setValue(f"mcp/{name}/url", url)
        self.settings.setValue(f"mcp/{name}/key", key)
        conn.url, conn.key = url, key
        if conn.state == "idle":
            card.refresh(conn)

    def conn(self, name: str) -> "mcpconn.Connection":
        card = self._cards.get(name)
        url = card.url_edit.text().strip() if card else ""
        return mcp_connection(name, url or mcpconn.DEFAULT_URL)

    def busy(self) -> bool:
        return bool(self._job and self._job.isRunning())

    def refresh(self) -> None:
        for name, card in self._cards.items():
            card.refresh(self.conn(name), self.busy())

    def run(self, action: str, name: str) -> None:
        if self.busy():
            return
        self.save(name)  # URL/key typed on the card are what we connect with
        conn = self.conn(name)
        self._say({"connect": "Menyambung…", "disconnect": "Memutus…", "restart": "Restart: cache dibuang, menyambung ulang…"}[action], None)
        self._job = Job(getattr(conn, action))
        self._job.done.connect(lambda err, n=name, a=action: self._done(n, a, err))
        card = self._cards[name]
        card.refresh(conn, True)
        self._job.start()

    def _done(self, name: str, action: str, err: str) -> None:
        conn = self.conn(name)
        self._cards[name].refresh(conn)
        if err:
            return self._say(f"{action.capitalize()} gagal: {err}", True)
        if action == "disconnect":
            self._say("Sambungan diputus.", False)
        else:
            self._say(f"Tersambung ke {conn.server.get('name') or name} — {len(conn.tools)} tool tersedia.", False)

    def _say(self, text: str, error: bool | None) -> None:
        color = {True: "#ff6b6b", False: "#7fd18b", None: "#8f98ad"}[error]
        self.msg.setText(f'<span style="color:{color}">{text}</span>')


class AddConnectionPage(QWidget):
    """A page of its own for connecting a cloud account (opened by the + card on the Cloud page).
    `added(name)` fires after a successful login / save; `back` is the Kembali button."""

    back = Signal()
    added = Signal(str)

    OAUTH_NOTE = ("Browser akan terbuka untuk login dan memberi izin. Setelah halaman menampilkan tulisan sukses, kembali ke sini. "
                  "Token disimpan di ~/.meetrec/rclone.conf (hanya bisa dibaca akunmu).")

    def __init__(self):
        super().__init__()
        self._job: Job | None = None
        self._login: cloud.OAuthLogin | None = None
        self._inputs: dict[str, QWidget] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        self.back_btn = QPushButton("←  Kembali")
        self.back_btn.clicked.connect(self.back.emit)
        head.addWidget(self.back_btn)
        head.addWidget(QLabel("Tambah koneksi cloud", objectName="pageTitle"), 1)
        outer.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 8, 8, 0)
        v.setSpacing(10)
        form = QFormLayout()
        self.prov = QComboBox()
        for pr in cloud.PROVIDERS:
            self.prov.addItem(pr.title, pr.key)
        self.name = QLineEdit()
        self.name.setPlaceholderText("nama akun, mis. gdrive")
        form.addRow("Platform", self.prov)
        form.addRow("Nama akun", self.name)
        v.addLayout(form)
        self.fields_box = QWidget()
        self.fields_form = QFormLayout(self.fields_box)
        self.fields_form.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.fields_box)
        self.note = QLabel("", objectName="status")
        self.note.setWordWrap(True)
        v.addWidget(self.note)
        act = QHBoxLayout()
        self.login_btn = QPushButton("Login")
        self.login_btn.setProperty("tone", "primary")
        self.login_btn.clicked.connect(self.login)
        self.cancel_btn = QPushButton("Batal login")
        self.cancel_btn.clicked.connect(self.cancel_login)
        self.cancel_btn.hide()
        act.addWidget(self.login_btn)
        act.addWidget(self.cancel_btn)
        act.addStretch(1)
        v.addLayout(act)
        self.msg = QLabel("", objectName="status")
        self.msg.setWordWrap(True)
        v.addWidget(self.msg)
        v.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        self.prov.currentIndexChanged.connect(self._provider_changed)
        self._provider_changed()

    def reset(self) -> None:
        """Fresh form each time the page is opened."""
        self.prov.setCurrentIndex(0)
        self._provider_changed()
        self.msg.setText("")
        self.cancel_btn.hide()
        self._buttons()

    def _provider_changed(self) -> None:
        prov = cloud.provider(self.prov.currentData())
        while self.fields_form.rowCount():
            self.fields_form.removeRow(0)
        self._inputs = {}
        for f in prov.fields:
            if f.choices:
                w = QComboBox()
                w.addItems(f.choices)
                w.setCurrentText(f.default or f.choices[0])
            else:
                w = QLineEdit(f.default)
                if f.secret:
                    w.setEchoMode(QLineEdit.EchoMode.Password)
            self._inputs[f.key] = w
            self.fields_form.addRow(f.label + ("" if f.required else " (opsional)"), w)
        self.fields_box.setVisible(prov.auth == "keys")
        self.login_btn.setText("Login" if prov.auth == "oauth" else "Simpan akun")
        self.note.setText(self.OAUTH_NOTE if prov.auth == "oauth" else
                          "Kunci disimpan di ~/.meetrec/rclone.conf (hanya bisa dibaca akunmu), bukan di baris perintah.")
        base, taken, i = prov.key, set(cloud.read_remotes()), 2
        name = base
        while name in taken:
            name, i = f"{base}{i}", i + 1
        self.name.setText(name)

    def _values(self) -> dict[str, str]:
        return {k: (w.currentText() if isinstance(w, QComboBox) else w.text()) for k, w in self._inputs.items()}

    def busy(self) -> bool:
        return bool(self._job and self._job.isRunning())

    def _say(self, text: str, error: bool | None) -> None:
        color = {True: "#ff6b6b", False: "#7fd18b", None: "#8f98ad"}[error]
        self.msg.setText(f'<span style="color:{color}">{text}</span>')

    def _buttons(self, busy: bool | None = None) -> None:
        busy = self.busy() if busy is None else busy  # a job just created is not "running" until start()
        for w in (self.login_btn, self.prov, self.name):
            w.setEnabled(not busy)
        for w in self._inputs.values():
            w.setEnabled(not busy)

    def login(self) -> None:
        if self.busy():
            return
        prov = cloud.provider(self.prov.currentData())
        name = self.name.text().strip()
        if not cloud.valid_name(name):
            return self._say("Nama akun: huruf/angka/_ . + @ -, maksimal 40 karakter, diawali huruf atau angka.", True)
        if name in cloud.read_remotes():
            return self._say(f"Nama '{name}' sudah dipakai. Pilih nama lain atau hapus akun lamanya.", True)
        if prov.auth == "keys":
            try:
                cloud.save_keys(name, prov, self._values())
            except ValueError as e:
                return self._say(str(e), True)
            for f in prov.fields:  # the keys now live in rclone.conf only, not in this page's text boxes
                if f.secret and isinstance(self._inputs.get(f.key), QLineEdit):
                    self._inputs[f.key].clear()
            self.added.emit(name)
            return
        exe = cloud.find_rclone()
        if not exe:
            return self._say("Pasang rclone dulu: tutup halaman ini, lalu klik Pasang rclone di bagian Setup rclone.", True)
        self._login = cloud.OAuthLogin(exe, name, prov.backend)
        self._job = Job(self._login.run)
        self._job.done.connect(lambda err, n=name: self._logged_in(n, err))
        self.cancel_btn.show()
        self._buttons(busy=True)
        self._say(f"Menunggu login {prov.title} di browser… (maksimal 5 menit)", None)
        self._job.start()

    def _logged_in(self, name: str, err: str) -> None:
        self.cancel_btn.hide()
        self._login = None
        self._buttons()
        if err:
            return self._say(f"Login gagal: {err}", True)
        self.added.emit(name)

    def cancel_login(self) -> None:
        if self._login:
            self._login.cancel()

    def shutdown(self) -> None:
        self.cancel_login()


class CloudPage(QScrollArea):
    """Setup rclone + the account cards. The + card asks the window to open the add-connection page."""

    add_requested = Signal()

    def __init__(self, settings: QSettings, uploads: "upload.UploadManager | None" = None):
        super().__init__()
        self.settings, self.uploads = settings, uploads
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._job: Job | None = None
        self._cards: dict[str, AccountCard] = {}
        self._status: dict[str, tuple[str, bool | None]] = {}  # last test result per account, survives card rebuilds

        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 8, 0)
        v.setSpacing(12)

        # section 1: rclone
        self.rc = Section("Setup rclone")
        self.rc_status = QLabel()
        self.rc_status.setWordWrap(True)
        self.rc_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.rc_check = QPushButton("Cek ulang")
        self.rc_check.clicked.connect(self.refresh_rclone)
        self.rc_install = QPushButton("Pasang rclone")
        self.rc_install.clicked.connect(self.install)
        btns = QHBoxLayout()
        btns.addWidget(self.rc_check)
        btns.addWidget(self.rc_install)
        btns.addStretch(1)
        self.rc_bar = QProgressBar()
        self.rc_bar.setRange(0, 100)
        self.rc_bar.hide()
        self.rc_hint = QLabel("rclone = program gratis untuk menyalin file ke penyimpanan cloud. Pemasangan mengunduh build resmi dari "
                              "downloads.rclone.org, mencocokkan SHA-256 dengan SHA256SUMS, lalu menaruhnya di ~/.meetrec/bin "
                              "(tanpa hak admin).", objectName="status")
        self.rc_hint.setWordWrap(True)
        self.rc.body.addWidget(self.rc_status)
        self.rc.body.addLayout(btns)
        self.rc.body.addWidget(self.rc_bar)
        self.rc.body.addWidget(self.rc_hint)
        v.addWidget(self.rc)

        # section 2: accounts as cards, then the "+" card
        self.acc = Section("Login platform penyimpanan cloud")
        self.acc_hint = QLabel("Klik tombol centang pada satu akun untuk memakainya sebagai tujuan upload rekaman (hanya satu akun yang bisa dipilih).",
                               objectName="status")
        self.acc_hint.setWordWrap(True)
        self.acc.body.addWidget(self.acc_hint)
        self.acc_list = QVBoxLayout()
        self.acc_list.setSpacing(8)
        self.acc.body.addLayout(self.acc_list)
        self.plus = QPushButton()
        self.plus.setObjectName("plusCard")
        self.plus.setToolTip("Tambah koneksi cloud (membuka halaman baru)")
        self.plus.setIcon(make_icon("plus"))
        self.plus.setIconSize(QSize(22, 22))
        self.plus.clicked.connect(self.add_requested.emit)
        self.acc.body.addWidget(self.plus)
        self.msg = QLabel("", objectName="status")
        self.msg.setWordWrap(True)
        self.acc.body.addWidget(self.msg)
        v.addWidget(self.acc)
        v.addStretch(1)
        self.setWidget(inner)
        self.refresh()

    # --- rclone ----------------------------------------------------------------------------------------
    def refresh(self) -> None:
        self.refresh_rclone()
        self.refresh_accounts()

    def refresh_rclone(self) -> None:
        exe = cloud.find_rclone()
        ver = cloud.rclone_version(exe) if exe else None
        if exe and ver:
            self.rc_status.setText(f'<span style="color:#7fd18b">rclone {ver}</span> terpasang<br><span style="color:#8f98ad">{exe}</span>')
            self.rc_install.setText("Perbarui rclone")
        elif exe:
            self.rc_status.setText(f'<span style="color:#ff6b6b">rclone di {exe} tidak bisa dijalankan</span>')
            self.rc_install.setText("Pasang rclone")
        else:
            self.rc_status.setText('<span style="color:#ff6b6b">rclone belum terpasang</span>')
            self.rc_install.setText("Pasang rclone")
        self._buttons()

    def install(self) -> None:
        if self._busy():
            return
        self.rc_bar.setValue(0)
        self.rc_bar.show()
        self._job = Job(cloud.install_rclone, report=True)
        self._job.progress.connect(lambda f: self.rc_bar.setValue(int(f * 100)))
        self._job.done.connect(self._installed)
        self._buttons(busy=True)
        self.rc_status.setText("Mengunduh rclone…")
        self._job.start()

    def _installed(self, err: str) -> None:
        self.rc_bar.hide()
        self.refresh_rclone()
        if err:
            self.rc_status.setText(self.rc_status.text() + f'<br><span style="color:#ff6b6b">{err}</span>')

    # --- accounts --------------------------------------------------------------------------------------
    def refresh_accounts(self) -> None:
        for card in self._cards.values():
            card.setParent(None)
            card.deleteLater()
        self._cards = {}
        titles = {p.backend: p.title for p in cloud.PROVIDERS}
        sel = upload.selected_account(self.settings)
        for name, backend in cloud.read_remotes().items():
            card = AccountCard(name, titles.get(backend, backend or "?"))
            card.set_folder(upload.folder_for(self.settings, name))
            card.set_selected(name == sel)
            text, ok = self._status.get(name, ("Dipilih sebagai tujuan upload" if name == sel else "Belum dites", True if name == sel else None))
            card.set_status(text, ok)
            card.select_btn.clicked.connect(lambda _=False, n=name: self.select(n))
            card.folder_btn.clicked.connect(lambda _=False, n=name: self.change_folder(n))
            card.delete_btn.clicked.connect(lambda _=False, n=name: self.remove(n))
            self._cards[name] = card
            self.acc_list.addWidget(card)
        self.acc_hint.setVisible(bool(self._cards))
        self._buttons()

    def account_added(self, name: str) -> None:
        """Called by the window when the add-connection page finished successfully."""
        self.refresh_accounts()
        self._say(f"Akun '{name}' tersambung. Klik tombol centangnya untuk memakai sebagai tujuan upload.", False)

    # --- the three card buttons ------------------------------------------------------------------------
    def select(self, name: str) -> None:
        """Test the connection (the same check as before). If it passes, this account becomes THE upload destination."""
        exe = cloud.find_rclone()
        card = self._cards.get(name)
        if not card or self._busy():
            return
        if not exe:
            return self._say("Pasang rclone dulu (bagian Setup rclone di atas).", True)
        card.set_status("Menguji koneksi…", None)
        self._job = Job(lambda: cloud.test_remote(exe, name))
        self._job.done.connect(lambda err, n=name: self._selected_tested(n, err))
        self._buttons(busy=True)
        self._job.start()

    def _selected_tested(self, name: str, err: str) -> None:
        if err:
            self._status[name] = (err, False)
            self.refresh_accounts()
            return self._say(f"'{name}' tidak dipilih: koneksi gagal.", True)
        self._status[name] = (self._job.result, True)
        if name != upload.selected_account(self.settings):
            if not self._allow_destination_change(f"mengganti tujuan upload ke '{name}'"):
                self.refresh_accounts()
                return self._say("Tujuan upload tidak diganti; upload yang berjalan dilanjutkan.", None)
            upload.set_selected(self.settings, name)
            self._say(f"Tujuan upload sekarang: {name}.", False)
        self.refresh_accounts()

    def ask_folder(self, name: str, current: str) -> str | None:
        text, ok = QInputDialog.getText(self, "Folder tujuan", f"Folder di akun '{name}' tempat rekaman diunggah\n"
                                        "(contoh: meetrec atau Rapat/2026; kosong = level atas):", text=current)
        return text if ok else None

    def change_folder(self, name: str) -> None:
        if self._busy() or name not in self._cards:
            return
        text = self.ask_folder(name, upload.folder_for(self.settings, name))
        if text is None:
            return
        try:
            folder = upload.clean_folder(text)
        except ValueError as e:
            return self._say(str(e), True)
        if folder == upload.folder_for(self.settings, name):
            return
        if name == upload.selected_account(self.settings) and not self._allow_destination_change(f"mengganti folder tujuan di '{name}'"):
            return self._say("Folder tujuan tidak diganti; upload yang berjalan dilanjutkan.", None)
        upload.set_folder(self.settings, name, folder)
        self.refresh_accounts()
        self._say(f"Folder tujuan '{name}': {folder or '(level atas)'}", False)

    def confirm(self, text: str) -> bool:
        return QMessageBox.question(self, "Konfirmasi", text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def _allow_destination_change(self, what: str) -> bool:
        """True when nothing is uploading, or the user accepted losing the running uploads (they are cancelled here)."""
        active = self.uploads.active() if self.uploads else []
        if not active:
            return True
        if not self.confirm(f"Sedang upload {len(active)} rekaman. Kalau kamu lanjut {what}, upload itu dibatalkan dan progress-nya hilang."
                            "\n\nLanjutkan?"):
            return False
        self.uploads.cancel_all()
        return True

    def remove(self, name: str) -> None:
        if self._busy():
            return
        is_dest = name == upload.selected_account(self.settings)
        active = self.uploads.active() if (self.uploads and is_dest) else []
        text = (f"Hapus akun '{name}' dari meetrec?\n\nToken/kunci lokalnya dihapus. Izin di sisi penyedia (mis. Google) "
                "tetap ada sampai kamu mencabutnya di pengaturan akun penyedia itu.")
        if active:
            text += f"\n\nAkun ini tujuan upload dan sedang ada upload {len(active)} rekaman: upload dibatalkan dan progress-nya hilang."
        if not self.confirm(text):
            return
        if active:
            self.uploads.cancel_all()
        if is_dest:
            upload.set_selected(self.settings, "")
        cloud.remove_remote(name)
        self._status.pop(name, None)
        self.refresh_accounts()
        self._say(f"Akun '{name}' dihapus dari meetrec.", False)

    # --- helpers ---------------------------------------------------------------------------------------
    def _say(self, text: str, error: bool | None) -> None:
        color = {True: "#ff6b6b", False: "#7fd18b", None: "#8f98ad"}[error]
        self.msg.setText(f'<span style="color:{color}">{text}</span>')

    def _busy(self) -> bool:
        return bool(self._job and self._job.isRunning())

    def _buttons(self, busy: bool | None = None) -> None:
        busy = self._busy() if busy is None else busy
        self.rc_check.setEnabled(not busy)
        self.rc_install.setEnabled(not busy and cloud.platform_tag() is not None)
        self.plus.setEnabled(not busy)
        for card in self._cards.values():
            for b in (card.select_btn, card.folder_btn, card.delete_btn):
                b.setEnabled(not busy)


# --- the window --------------------------------------------------------------------------------------
class SettingsDialog(QDialog):
    """Pengaturan. General is stored by store(); Cloud acts immediately.
    Two pages: the settings (menu on the left) and, opened by the + card, the add-connection page. Like every sub page of
    the app, closing the window (or Esc) on the add page goes BACK to the settings instead of closing them."""

    TITLE = "Pengaturan"

    def __init__(self, settings: QSettings, parent=None, token: str = "", port: int = DEFAULT_PORT, tab: int = 0, uploads=None):
        super().__init__(parent)
        self.setWindowTitle(self.TITLE)
        self.setModal(True)
        self.setStyleSheet(DIALOG_STYLE)
        self.resize(780, 540)
        self.setMinimumSize(640, 420)
        self.general = GeneralPage(settings, token, port)
        self.cloud = CloudPage(settings, uploads)
        self.mcp = McpPage(settings)
        self.add_page = AddConnectionPage()
        self.tabs = SidebarPages()
        self.tabs.add_page("General", self.general)
        self.tabs.add_page("Cloud", self.cloud)
        self.tabs.add_page("MCP", self.mcp)
        self.tabs.set_page(tab)
        # the General controls keep their old names on the dialog
        self.video, self.fps, self.rate = self.general.video, self.general.fps, self.general.rate
        self.bridge, self.token = self.general.bridge, self.general.token

        main = QWidget()
        mv = QVBoxLayout(main)
        mv.setContentsMargins(0, 0, 0, 0)
        buttons = QHBoxLayout()
        hint = QLabel("Simpan berlaku untuk tab General. Tab Cloud dan MCP langsung berlaku.", objectName="status")
        buttons.addWidget(hint, 1)
        cancel = QPushButton("Batal")
        cancel.clicked.connect(self.reject)
        self.save = QPushButton("Simpan")
        self.save.setDefault(True)
        self.save.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.save)
        mv.addWidget(self.tabs, 1)
        mv.addLayout(buttons)
        self.stack = QStackedWidget()
        self.stack.addWidget(main)
        self.stack.addWidget(self.add_page)
        v = QVBoxLayout(self)
        v.addWidget(self.stack)

        self.cloud.add_requested.connect(self.open_add)
        self.add_page.back.connect(self.close_add)
        self.add_page.added.connect(self._connection_added)

    def open_add(self) -> None:
        self.add_page.reset()
        self.stack.setCurrentWidget(self.add_page)
        self.setWindowTitle(f"{self.TITLE}: tambah koneksi cloud")

    def close_add(self) -> None:
        self.add_page.shutdown()  # a login still waiting in the browser is stopped
        self.stack.setCurrentIndex(0)
        self.setWindowTitle(self.TITLE)
        self.cloud.refresh_accounts()

    def _connection_added(self, name: str) -> None:
        self.stack.setCurrentIndex(0)
        self.setWindowTitle(self.TITLE)
        self.tabs.set_page(1)
        self.cloud.account_added(name)

    def reject(self) -> None:
        if self.stack.currentWidget() is self.add_page:  # sub page: close / Esc = back
            self.close_add()
            return
        super().reject()

    def done(self, result: int) -> None:
        self.add_page.shutdown()
        super().done(result)

    def store(self, settings: QSettings) -> None:
        settings.setValue("record_video", self.video.isChecked())
        settings.setValue("video_fps", self.fps.currentData())
        settings.setValue("audio_rate", self.rate.currentData())
        settings.setValue("bridge_enabled", self.bridge.isChecked())
