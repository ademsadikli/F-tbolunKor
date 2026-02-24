# -*- coding: utf-8 -*-
import os
import re
import json
import tempfile
import subprocess
import threading
import shutil
import sys
import time
import urllib.parse
from copy import deepcopy
import wx
import mpv
if os.name == "nt":
    import ctypes

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"
LOSSLESS_PRESET = "veryslow"
FFMPEG_ERROR_TAIL_LINES = 30

# ================= ZAMANA GİT =================
class GoToTimeDialog(wx.Dialog):
    def __init__(self, parent, cur):
        super().__init__(parent, title="Zamana Git")
        total_ms = int(round(max(0.0, float(cur)) * 1000.0))
        h, rem = divmod(total_ms, 3600 * 1000)
        m, rem = divmod(rem, 60 * 1000)
        s, ms = divmod(rem, 1000)
        v = wx.BoxSizer(wx.VERTICAL)
        self.sh = wx.TextCtrl(self, value=f"{int(max(0, min(99, h))):02d}")
        self.sm = wx.TextCtrl(self, value=f"{int(max(0, min(59, m))):02d}")
        self.ss = wx.TextCtrl(self, value=f"{int(max(0, min(59, s))):02d}")
        self.sms = wx.TextCtrl(self, value=f"{ms:03d}")
        self.sh.SetName(f"Saat: {self.sh.GetValue()}")
        self.sm.SetName(f"Dakika: {self.sm.GetValue()}")
        self.ss.SetName(f"Saniye: {self.ss.GetValue()}")
        self.sms.SetName(f"MS: {self.sms.GetValue()}")
        for lbl, ctrl in (("Saat:", self.sh), ("Dakika:", self.sm), ("Saniye:", self.ss), ("MS:", self.sms)):
            v.Add(wx.StaticText(self, label=lbl), 0, wx.ALL, 4)
            v.Add(ctrl, 0, wx.ALL | wx.EXPAND, 4)
            ctrl.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        hbox = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(self, wx.ID_OK, "Tamam")
        cancel_btn = wx.Button(self, wx.ID_CANCEL, "İptal")
        hbox.Add(ok_btn, 0, wx.ALL | wx.ALIGN_CENTER, 8)
        hbox.Add(cancel_btn, 0, wx.ALL | wx.ALIGN_CENTER, 8)
        v.Add(hbox, 0, wx.EXPAND)
        self.SetSizerAndFit(v)
        self.sh.SetFocus()

    def on_key(self, e):
        if e.GetKeyCode() == wx.WXK_RETURN:
            self.EndModal(wx.ID_OK)
        elif e.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
        else:
            e.Skip()

    def seconds(self):
        def parse_2digit(ctrl, max_value):
            try:
                val = int((ctrl.GetValue() or "0").strip())
            except Exception:
                val = 0
            val = max(0, min(max_value, val))
            ctrl.SetValue(f"{val:02d}")
            return val
        h = parse_2digit(self.sh, 99)
        m = parse_2digit(self.sm, 59)
        s = parse_2digit(self.ss, 59)
        try:
            ms = int((self.sms.GetValue() or "0").strip())
        except Exception:
            ms = 0
        ms = max(0, min(999, ms))
        self.sms.SetValue(f"{ms:03d}")
        return h * 3600 + m * 60 + s + (ms / 1000.0)

# ================= İLETİŞİM VE YARDIM DİALOGALARI =================
class ContactDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="İletişim")
        v = wx.BoxSizer(wx.VERTICAL)
        self.email_btn = wx.Button(self, label="E-posta")
        self.whatsapp_btn = wx.Button(self, label="Whatsapp")
        self.instagram_btn = wx.Button(self, label="Instagram")
        self.close_btn = wx.Button(self, wx.ID_CANCEL, "Kapat")
        for btn in (self.email_btn, self.whatsapp_btn, self.instagram_btn, self.close_btn):
            v.Add(btn, 0, wx.ALL | wx.EXPAND, 6)
        self.SetSizerAndFit(v)
        self.email_btn.Bind(wx.EVT_BUTTON, self.on_email)
        self.whatsapp_btn.Bind(wx.EVT_BUTTON, self.on_whatsapp)
        self.instagram_btn.Bind(wx.EVT_BUTTON, self.on_instagram)
        self.email_btn.SetFocus()

    def on_email(self, _):
        subject = urllib.parse.quote("BlindVideoEditor Hakkında")
        mailto = f"mailto:ademsadikli@gmail.com?subject={subject}&body="
        wx.LaunchDefaultBrowser(mailto)

    def on_whatsapp(self, _):
        if not wx.LaunchDefaultBrowser("whatsapp://send?phone=905422413451"):
            wx.LaunchDefaultBrowser("https://wa.me/905422413451")

    def on_instagram(self, _):
        wx.LaunchDefaultBrowser("https://www.instagram.com/adem_sadikli/")

class BVEMenuDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="B.V.E")
        self.parent = parent
        v = wx.BoxSizer(wx.VERTICAL)
        self.help_btn = wx.Button(self, label="Yardım")
        self.shortcuts_btn = wx.Button(self, label="Kısa yol tuşları")
        self.contact_btn = wx.Button(self, label="İletişim")
        self.close_btn = wx.Button(self, wx.ID_CANCEL, "Kapat")
        for btn in (self.help_btn, self.shortcuts_btn, self.contact_btn, self.close_btn):
            v.Add(btn, 0, wx.ALL | wx.EXPAND, 6)
        self.SetSizerAndFit(v)
        self.help_btn.Bind(wx.EVT_BUTTON, self.on_help)
        self.shortcuts_btn.Bind(wx.EVT_BUTTON, self.on_shortcuts)
        self.contact_btn.Bind(wx.EVT_BUTTON, self.on_contact)
        self.help_btn.SetFocus()

    def on_help(self, _):
        self.parent._open_text_help("Beni Oku.txt", "Beni Oku")

    def on_shortcuts(self, _):
        self.parent._open_text_help("KısaYollar.txt", "Kısa Yol Tuşları")

    def on_contact(self, _):
        d = ContactDialog(self)
        try:
            d.ShowModal()
        finally:
            d.Destroy()

# ================= VİDEO ÖZELLİKLERİ =================
class VideoPropertiesDialog(wx.Dialog):
    def __init__(self, parent, props):
        super().__init__(parent, title="Video Özellikleri")
        v = wx.BoxSizer(wx.VERTICAL)
        def add_prop(label, value, name):
            v.Add(wx.StaticText(self, label=label), 0, wx.ALL, 4)
            c = wx.Choice(self)
            c.SetItems([value])
            c.SetSelection(0)
            c.SetName(name)
            v.Add(c, 0, wx.ALL | wx.EXPAND, 4)
            return c
        self.prop_controls = [
            add_prop("Boyut:", props.get("size", "Bilinmiyor"), "Boyut"),
            add_prop("Görüntü Yönü:", props.get("orientation", "Bilinmiyor"), "Görüntü Yönü"),
            add_prop("Dönüş Bilgisi:", props.get("rotation", "Bilinmiyor"), "Dönüş Bilgisi"),
            add_prop("Kare Hızı:", props.get("fps", "Bilinmiyor"), "Kare Hızı"),
            add_prop("Video Kodeği:", props.get("vcodec", "Bilinmiyor"), "Video Kodeği"),
            add_prop("Ses Kodeği:", props.get("acodec", "Bilinmiyor"), "Ses Kodeği"),
            add_prop("Süre:", props.get("duration", "Bilinmiyor"), "Süre"),
        ]
        btns = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(self, wx.ID_OK, "Tamam")
        cancel_btn = wx.Button(self, wx.ID_CANCEL, "İptal")
        btns.Add(ok_btn, 0, wx.ALL, 8)
        btns.Add(cancel_btn, 0, wx.ALL, 8)
        v.Add(btns, 0, wx.ALIGN_CENTER)
        self.SetSizerAndFit(v)
        self.prop_controls[0].SetFocus()

# ================= YARDIM METNİ =================
class HelpTextDialog(wx.Dialog):
    def __init__(self, parent, title, text):
        super().__init__(parent, title=title, size=(760, 560))
        v = wx.BoxSizer(wx.VERTICAL)
        self.text = wx.TextCtrl(self, value=text, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        close_btn = wx.Button(self, wx.ID_OK, "Tamam")
        v.Add(self.text, 1, wx.ALL | wx.EXPAND, 8)
        v.Add(close_btn, 0, wx.ALL | wx.ALIGN_CENTER, 8)
        self.SetSizer(v)
        self.text.SetFocus()

# ================= SEÇENEKLER =================
class OptionsDialog(wx.Dialog):
    def __init__(self, parent, video_opts, audio_opts, speech_opts):
        super().__init__(parent, title="Seçenekler")
        self.video_opts = video_opts
        self.audio_opts = audio_opts
        self.speech_opts = speech_opts
        notebook = wx.Notebook(self)
        video_page = wx.Panel(notebook)
        audio_page = wx.Panel(notebook)
        speech_page = wx.Panel(notebook)
        self.build_video_page(video_page)
        self.build_audio_page(audio_page)
        self.build_speech_page(speech_page)
        notebook.AddPage(video_page, "Video Seçenekleri")
        notebook.AddPage(audio_page, "Ses Seçenekleri")
        notebook.AddPage(speech_page, "Metin Okuma")
        v = wx.BoxSizer(wx.VERTICAL)
        v.Add(notebook, 1, wx.EXPAND | wx.ALL, 4)
        hbox = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(self, wx.ID_OK, "Tamam")
        cancel_btn = wx.Button(self, wx.ID_CANCEL, "İptal")
        hbox.Add(ok_btn, 0, wx.ALL | wx.ALIGN_CENTER, 8)
        hbox.Add(cancel_btn, 0, wx.ALL | wx.ALIGN_CENTER, 8)
        v.Add(hbox, 0, wx.EXPAND)
        self.SetSizerAndFit(v)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)

    def on_key(self, e):
        if e.ControlDown() and e.GetKeyCode() == wx.WXK_TAB:
            notebook = self.Children[0]
            if notebook:
                cur = notebook.GetSelection()
                direction = 1 if not e.ShiftDown() else -1
                next_page = (cur + direction) % notebook.GetPageCount()
                notebook.SetSelection(next_page)
            return
        e.Skip()

    def build_video_page(self, page):
        def select_or_default(ctrl, index):
            if index is None or index < 0 or index >= ctrl.GetCount():
                index = 0
            ctrl.SetSelection(index)
            if ctrl.GetSelection() == wx.NOT_FOUND and ctrl.GetCount() > 0:
                ctrl.SetSelection(0)
        def add_labeled_choice(label, items, name):
            v.Add(wx.StaticText(page, label=label), 0, wx.ALL, 4)
            ctrl = wx.Choice(page)
            ctrl.SetItems(items)
            ctrl.SetName(name)
            v.Add(ctrl, 0, wx.EXPAND | wx.ALL, 4)
            return ctrl
        v = wx.BoxSizer(wx.VERTICAL)
        self.formats_display = ["MP4", "MOV", "MKV", "AVI", "WMV"]
        self.formats_values = ["mp4", "mov", "mkv", "avi", "wmv"]
        self.video_format = add_labeled_choice("Kayıt Formatı (Dosya Uzantısı):", self.formats_display, "Kayıt Formatı")
        try:
            idx = self.formats_values.index(self.video_opts.get("format", "mp4"))
        except ValueError:
            idx = 0
        select_or_default(self.video_format, idx)
        self.codecs_display = [
            "Kopyala (Değiştirme Yok)",
            "H.264 (libx264 - Yaygın, Uyumlu)",
            "H.265 (libx265 - Daha Verimli)",
            "VP9 (WebM için)",
            "AV1 (Yeni, Verimli)",
        ]
        self.codecs_values = ["copy", "libx264", "libx265", "libvpx-vp9", "libaom-av1"]
        self.video_codec = add_labeled_choice("Video Kodek (Sıkıştırma Yöntemi):", self.codecs_display, "Video Kodek")
        try:
            idx = self.codecs_values.index(self.video_opts.get("codec", "copy"))
        except ValueError:
            idx = 0
        select_or_default(self.video_codec, idx)
        self.crfs_display = [
            "0 (Kayıpsız, Çok Büyük Dosya)",
            "18 (Yüksek Kalite, Büyük Dosya)",
            "23 (Standart Kalite, Dengeli)",
            "28 (Düşük Kalite, Küçük Dosya)",
        ]
        self.crfs_values = ["0", "18", "23", "28"]
        self.crf = add_labeled_choice("CRF Değeri (Düşük Değer = Yüksek Kalite, Büyük Dosya):", self.crfs_display, "CRF Değeri")
        try:
            idx = self.crfs_values.index(self.video_opts.get("crf", "23"))
        except ValueError:
            idx = 2
        select_or_default(self.crf, idx)
        self.presets_display = [
            "Ultra Hızlı (Düşük Kalite)",
            "Süper Hızlı",
            "Çok Hızlı",
            "Daha Hızlı",
            "Hızlı",
            "Orta (Dengeli)",
            "Yavaş (Yüksek Kalite)",
            "Daha Yavaş",
            "Çok Yavaş (En Yüksek Kalite)",
        ]
        self.presets_values = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
        self.preset = add_labeled_choice("Kodlama Hızı Ön Ayarı:", self.presets_display, "Kodlama Hızı Ön Ayarı")
        try:
            idx = self.presets_values.index(self.video_opts.get("preset", "medium"))
        except ValueError:
            idx = 5
        select_or_default(self.preset, idx)
        self.video_codec.MoveAfterInTabOrder(self.video_format)
        self.crf.MoveAfterInTabOrder(self.video_codec)
        self.preset.MoveAfterInTabOrder(self.crf)
        page.SetSizer(v)

    def build_audio_page(self, page):
        def select_or_default(ctrl, index):
            if index is None or index < 0 or index >= ctrl.GetCount():
                index = 0
            ctrl.SetSelection(index)
            if ctrl.GetSelection() == wx.NOT_FOUND and ctrl.GetCount() > 0:
                ctrl.SetSelection(0)
        def add_labeled_choice(label, items, name):
            v.Add(wx.StaticText(page, label=label), 0, wx.ALL, 4)
            ctrl = wx.Choice(page)
            ctrl.SetItems(items)
            ctrl.SetName(name)
            v.Add(ctrl, 0, wx.EXPAND | wx.ALL, 4)
            return ctrl
        v = wx.BoxSizer(wx.VERTICAL)
        self.audio_codecs_display = ["Kopyala (Değiştirme Yok)", "AAC (Yaygın, Kaliteli)", "MP3 (Uyumlu)", "Opus (Verimli)", "Vorbis (Açık Kaynak)"]
        self.audio_codecs_values = ["copy", "aac", "mp3", "opus", "vorbis"]
        self.audio_codec = add_labeled_choice("Ses Kodek:", self.audio_codecs_display, "Ses Kodek")
        try:
            idx = self.audio_codecs_values.index(self.audio_opts.get("codec", "copy"))
        except ValueError:
            idx = 0
        select_or_default(self.audio_codec, idx)
        self.channels_display = ["Kopyala (Değiştirme Yok)", "Mono (Tek Kanal)", "Stereo (Çift Kanal)"]
        self.channels_values = ["copy", "mono", "stereo"]
        self.channels = add_labeled_choice("Kanal:", self.channels_display, "Kanal")
        try:
            idx = self.channels_values.index(self.audio_opts.get("channels", "copy"))
        except ValueError:
            idx = 0
        select_or_default(self.channels, idx)
        self.sample_rates_display = ["Kopyala (Değiştirme Yok)", "22050 Hz (Düşük Kalite)", "44100 Hz (Standart)", "48000 Hz (Yüksek)", "96000 Hz (Çok Yüksek)"]
        self.sample_rates_values = ["copy", "22050", "44100", "48000", "96000"]
        self.sample_rate = add_labeled_choice("Örnekleme Hızı:", self.sample_rates_display, "Örnekleme Hızı")
        try:
            idx = self.sample_rates_values.index(self.audio_opts.get("sample_rate", "copy"))
        except ValueError:
            idx = 0
        select_or_default(self.sample_rate, idx)
        self.bit_rates_display = [
            "Kopyala (Değiştirme Yok)",
            "64 kbps (Düşük Kalite)",
            "96 kbps",
            "128 kbps (Standart)",
            "160 kbps",
            "192 kbps (İyi)",
            "256 kbps (Yüksek)",
            "320 kbps (En Yüksek)",
        ]
        self.bit_rates_values = ["copy", "64000", "96000", "128000", "160000", "192000", "256000", "320000"]
        self.bit_rate = add_labeled_choice("Bit Hızı (Ses Kalitesi ve Dosya Boyutu):", self.bit_rates_display, "Bit Hızı")
        try:
            idx = self.bit_rates_values.index(self.audio_opts.get("bit_rate", "copy"))
        except ValueError:
            idx = 0
        select_or_default(self.bit_rate, idx)
        self.channels.MoveAfterInTabOrder(self.audio_codec)
        self.sample_rate.MoveAfterInTabOrder(self.channels)
        self.bit_rate.MoveAfterInTabOrder(self.sample_rate)
        page.SetSizer(v)

    def build_speech_page(self, page):
        v = wx.BoxSizer(wx.VERTICAL)
        self.speech_items = [
            ("time", "Tuşlara basıldıkça zamanı oku"),
            ("in_out", "IN/OUT zamanlarını oku"),
            ("status_general", "Genel durum mesajlarını oku"),
            ("status_file", "Dosya aç/kapat mesajlarını oku"),
            ("status_edit", "Düzenleme mesajlarını oku"),
            ("status_preview", "Önizleme mesajlarını oku"),
            ("status_audio", "Ses düzeyi mesajlarını oku"),
            ("status_options", "Seçenek mesajlarını oku"),
            ("status_errors", "Hata mesajlarını oku"),
            ("status_playback", "Oynatma mesajlarını oku"),
        ]
        self.speech_checkboxes = []
        for key, label in self.speech_items:
            cb = wx.CheckBox(page, label=label)
            cb.SetValue(self.speech_opts.get(key, True))
            self.speech_checkboxes.append((key, cb))
            v.Add(cb, 0, wx.ALL, 4)
        page.SetSizer(v)

    def get_video_opts(self):
        return {
            "format": self.formats_values[self.video_format.GetSelection()],
            "codec": self.codecs_values[self.video_codec.GetSelection()],
            "crf": self.crfs_values[self.crf.GetSelection()],
            "preset": self.presets_values[self.preset.GetSelection()],
        }

    def get_audio_opts(self):
        return {
            "codec": self.audio_codecs_values[self.audio_codec.GetSelection()],
            "channels": self.channels_values[self.channels.GetSelection()],
            "sample_rate": self.sample_rates_values[self.sample_rate.GetSelection()],
            "bit_rate": self.bit_rates_values[self.bit_rate.GetSelection()],
        }

    def get_speech_opts(self):
        opts = {}
        for key, cb in self.speech_checkboxes:
            opts[key] = cb.IsChecked()
        return opts

# ================= KAYDETME İLETİŞİM KUTUSU =================
class ProgressDialog(wx.Dialog):
    def __init__(self, parent, title="Kaydediliyor", message="İşlem devam ediyor...", modal=True, pulse=False):
        super().__init__(parent, title=title, style=wx.DEFAULT_DIALOG_STYLE)
        self.abort = False
        self.ffmpeg_process = None
        self._pulse_value = 0
        self._pulse = pulse
        vbox = wx.BoxSizer(wx.VERTICAL)
        self.message_text = wx.StaticText(self, label=message)
        self.gauge = wx.Gauge(self, range=100, style=wx.GA_SMOOTH)
        self.cancel_btn = wx.Button(self, wx.ID_CANCEL, "İptal")
        vbox.Add(self.message_text, 0, wx.ALL | wx.EXPAND, 8)
        vbox.Add(self.gauge, 0, wx.ALL | wx.EXPAND, 8)
        vbox.Add(self.cancel_btn, 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        self.SetSizerAndFit(vbox)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.Bind(wx.EVT_CLOSE, self.on_close_request)
        self.cancel_btn.Bind(wx.EVT_BUTTON, lambda evt: self.confirm_abort())
        if modal:
            self.MakeModal(True)

    def attach_process(self, process):
        self.ffmpeg_process = process

    def clear_process(self):
        self.ffmpeg_process = None

    def update_progress(self, percent, message):
        wx.CallAfter(self._update, percent, message)

    def _update(self, percent, message):
        if not self:
            return
        self.message_text.SetLabel(message)
        if percent == -1:
            if self._pulse:
                self._pulse_value = (self._pulse_value + 5) % 101
                self.gauge.SetValue(self._pulse_value)
        else:
            self.gauge.SetValue(max(0, min(100, percent)))

    def on_key(self, event):
        if event.GetKeyCode() != wx.WXK_ESCAPE:
            event.Skip()
            return
        self.confirm_abort()

    def on_close_request(self, event):
        if self.confirm_abort():
            event.Skip()
            return
        event.Veto()

    def confirm_abort(self):
        dlg = wx.MessageDialog(
            self,
            "İptal etmek istiyor musunuz?",
            "Onay",
            style=wx.YES_NO | wx.ICON_QUESTION,
        )
        if hasattr(dlg, "SetYesNoLabels"):
            dlg.SetYesNoLabels("Evet", "Hayır")
        if hasattr(dlg, "SetEscapeId"):
            dlg.SetEscapeId(wx.ID_NO)
        result = dlg.ShowModal()
        dlg.Destroy()
        if result == wx.ID_YES:
            self.abort = True
            if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
                try:
                    self.ffmpeg_process.kill()
                except Exception:
                    pass
            return True
        return False

    def destroy(self):
        wx.CallAfter(self._safe_destroy)

    def _safe_destroy(self):
        try:
            self.MakeModal(False)
        except Exception:
            pass
        if self:
            self.Destroy()

# ================= ANA EDITÖR =================
class Editor(wx.Frame):
    def __init__(self):
        super().__init__(None, title="BlindVideoEditor", size=(900, 550))
        self.player = None
        self.source_path = None
        self.media_path = None
        self.length = 0.0
        self.mark_in = None
        self.mark_out = None
        self.cuts = []
        self.undo_stack = []
        self.last_cut = None
        self.last_in = None
        self.last_out = None
        self.merged_cuts = None
        self.volume = 100
        self.muted = False
        self.previewing = False
        self.preview_restore = None
        self.preview_segments = None
        self.preview_segment_index = 0
        self.active_progress = None
        self.audio_codec = None
        self.video_codec = None
        self.video_opts = {"format": "mp4", "codec": "copy", "crf": "23", "preset": "medium"}
        self.audio_opts = {"codec": "copy", "channels": "copy", "sample_rate": "copy", "bit_rate": "copy"}
        self.project_dirty = False
        self.project_path = None
        self.project_temp_path = None
        self.last_open_dir = ""
        self.last_save_dir = ""
        self.last_project_dir = ""
        self.active_workspace = 0
        self.workspaces = [None]
        self.adapted_temp_files = set()
        self.segment_clipboard = None
        self._media_session = 0
        self.speech_opts = {
            "time": True,
            "in_out": True,
            "status_general": True,
            "status_file": True,
            "status_edit": True,
            "status_preview": True,
            "status_audio": True,
            "status_options": True,
            "status_errors": True,
            "status_playback": True,
        }
        self.build_ui()
        self.init_player()
        self.workspace_players = [self.player]
        self.workspace_loaded_media = [None]
        self.bind_keys()
        self._refresh_external_tools()
        self._init_workspaces()
        self.Bind(wx.EVT_CLOSE, self.on_close_app)
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.update_time, self.timer)
        self.timer.Start(500)
        self.Show()
        wx.CallAfter(self.set_video_handle)

    # ---------- UI ----------
    def build_ui(self):
        p = wx.Panel(self)
        self.video_panel = wx.Panel(p)
        self.video_panel.SetBackgroundColour("BLACK")
        self.video_panel.SetCanFocus(False)
        self.video_panel.Show(False)
        self.status = wx.StaticText(p, label="Hoş Geldiniz, yardım için f bir tuşuna basın")
        self.time = wx.StaticText(p, label="")
        self.time.Hide()
        self.reader = wx.TextCtrl(p, style=wx.TE_READONLY | wx.TE_PROCESS_ENTER)
        self.reader.Hide()
        v = wx.BoxSizer(wx.VERTICAL)
        v.Add(self.video_panel, 1, wx.EXPAND)
        v.Add(self.status, 0, wx.ALL, 4)
        v.Add(self.time, 0, wx.ALL, 4)
        v.Add(self.reader, 0)
        p.SetSizer(v)
        self.status.SetFocus()

    def _create_player(self):
        handle = int(self.video_panel.GetHandle())
        p = mpv.MPV(
            wid=handle,
            keep_open=True,
            log_handler=None,
            input_default_bindings=False,
            input_vo_keyboard=False,
            osc=False,
        )
        p.pause = True
        return p

    def init_player(self):
        self.player = self._create_player()

    def _ensure_workspace_player_slots(self):
        while len(self.workspace_players) < len(self.workspaces):
            self.workspace_players.append(None)
        while len(self.workspace_loaded_media) < len(self.workspaces):
            self.workspace_loaded_media.append(None)

    def _ensure_workspace_player(self, idx):
        self._ensure_workspace_player_slots()
        p = self.workspace_players[idx]
        if p is None:
            p = self._create_player()
            self.workspace_players[idx] = p
        return p

    def _next_media_session(self):
        self._media_session += 1
        return self._media_session

    def _call_later_if_session(self, delay_ms, fn, *args):
        sid = self._media_session
        def _wrapped():
            if sid != self._media_session:
                return
            fn(*args)
        wx.CallLater(delay_ms, _wrapped)

    def set_video_handle(self):
        if not self.video_panel:
            return
        if self.player is None:
            self.init_player()
            return
        self.player.wid = int(self.video_panel.GetHandle())

    def _runtime_base_dirs(self):
        dirs = []
        try:
            if getattr(sys, "frozen", False):
                exe_dir = os.path.dirname(os.path.abspath(sys.executable))
                dirs.append(exe_dir)
                meipass = getattr(sys, "_MEIPASS", None)
                if meipass:
                    dirs.append(meipass)
            else:
                dirs.append(os.path.dirname(os.path.abspath(__file__)))
        except Exception:
            pass
        dirs.append(os.getcwd())
        uniq = []
        for d in dirs:
            if d and d not in uniq:
                uniq.append(d)
        return uniq

    def _resolve_tool_path(self, tool_name):
        exe_name = tool_name + ".exe" if os.name == "nt" else tool_name
        candidates = []
        for base in self._runtime_base_dirs():
            candidates.append(os.path.join(base, "bin", exe_name))
            candidates.append(os.path.join(base, exe_name))
        for c in candidates:
            if os.path.exists(c):
                return c
        in_path = shutil.which(tool_name)
        if in_path:
            return in_path
        return exe_name

    def _refresh_external_tools(self):
        self.ffmpeg_executable = self._resolve_tool_path(FFMPEG)
        self.ffprobe_executable = self._resolve_tool_path(FFPROBE)

    def _workspace_default(self):
        return {
            "media_kind": "video",
            "source_path": None,
            "media_path": None,
            "length": 0.0,
            "mark_in": None,
            "mark_out": None,
            "cuts": [],
            "undo_stack": [],
            "last_cut": None,
            "last_in": None,
            "last_out": None,
            "merged_cuts": None,
            "volume": 100,
            "muted": False,
            "audio_codec": None,
            "video_codec": None,
            "video_opts": {"format": "mp4", "codec": "copy", "crf": "23", "preset": "medium"},
            "audio_opts": {"codec": "copy", "channels": "copy", "sample_rate": "copy", "bit_rate": "copy"},
            "project_dirty": False,
            "project_path": None,
            "project_temp_path": None,
            "position": 0.0,
        }

    def _workspace_snapshot(self):
        return {
            "media_kind": self._detect_media_kind(self.source_path or self.media_path),
            "source_path": self.source_path,
            "media_path": self.media_path,
            "length": self.length,
            "mark_in": self.mark_in,
            "mark_out": self.mark_out,
            "cuts": deepcopy(self.cuts),
            "undo_stack": deepcopy(self.undo_stack),
            "last_cut": self.last_cut,
            "last_in": self.last_in,
            "last_out": self.last_out,
            "merged_cuts": deepcopy(self.merged_cuts),
            "volume": self.volume,
            "muted": self.muted,
            "audio_codec": self.audio_codec,
            "video_codec": self.video_codec,
            "video_opts": deepcopy(self.video_opts),
            "audio_opts": deepcopy(self.audio_opts),
            "project_dirty": self.project_dirty,
            "project_path": self.project_path,
            "project_temp_path": self.project_temp_path,
            "position": self.real_to_virtual(self.current_time()) if self.media_path else 0.0,
        }

    def _workspace_apply(self, ws):
        ws = deepcopy(ws or self._workspace_default())
        self.source_path = ws.get("source_path")
        self.media_path = ws.get("media_path")
        self.length = float(ws.get("length", 0.0) or 0.0)
        self.mark_in = ws.get("mark_in")
        self.mark_out = ws.get("mark_out")
        self.cuts = deepcopy(ws.get("cuts") or [])
        self.undo_stack = deepcopy(ws.get("undo_stack") or [])
        self.last_cut = ws.get("last_cut")
        self.last_in = ws.get("last_in")
        self.last_out = ws.get("last_out")
        self.merged_cuts = deepcopy(ws.get("merged_cuts"))
        self.volume = int(ws.get("volume", 100) or 100)
        self.muted = bool(ws.get("muted", False))
        self.audio_codec = ws.get("audio_codec")
        self.video_codec = ws.get("video_codec")
        self.video_opts = deepcopy(ws.get("video_opts") or {"format": "mp4", "codec": "copy", "crf": "23", "preset": "medium"})
        self.audio_opts = deepcopy(ws.get("audio_opts") or {"codec": "copy", "channels": "copy", "sample_rate": "copy", "bit_rate": "copy"})
        self.project_dirty = bool(ws.get("project_dirty", False))
        self.project_path = ws.get("project_path")
        self.project_temp_path = ws.get("project_temp_path")
        self.player = self._ensure_workspace_player(self.active_workspace)
        self.set_video_handle()
        if self.media_path and os.path.exists(self.media_path):
            pos = float(ws.get("position", 0.0) or 0.0)
            loaded_path = self.workspace_loaded_media[self.active_workspace]
            if loaded_path != self.media_path:
                self._load_media(self.media_path)
            else:
                self.player.pause = True
                self._on_media_loaded()
            self.timer.Start(300)
            self._call_later_if_session(80, self.seek_virtual, max(0.0, pos))
            self._call_later_if_session(150, self._apply_player_volume)
        else:
            self._next_media_session()
            if self.player:
                self.player.pause = True
            self.timer.Stop()
            self._on_media_closed()

    def _save_active_workspace(self):
        self.workspaces[self.active_workspace] = self._workspace_snapshot()

    def _switch_workspace(self, target_idx):
        if target_idx == self.active_workspace or target_idx < 0 or target_idx >= len(self.workspaces):
            return
        self._save_active_workspace()
        self.active_workspace = target_idx
        self._workspace_apply(self.workspaces[target_idx])
        file_speak = self.speech_opts.get("status_file", True)
        name = os.path.basename(self.media_path) if self.media_path else "boş alan"
        self.say(f"Çalışma alanı {target_idx + 1}: {name}", speak=file_speak, update_status=file_speak)

    def _switch_workspace_relative(self, step):
        step = -1 if step < 0 else 1
        if not self.workspaces:
            return
        self._switch_workspace((self.active_workspace + step) % len(self.workspaces))

    def _create_new_workspace(self):
        self._save_active_workspace()
        self.workspaces.append(self._workspace_default())
        self.workspace_players.append(None)
        self.workspace_loaded_media.append(None)
        self._switch_workspace(len(self.workspaces) - 1)

    def _remove_active_workspace(self):
        if len(self.workspaces) <= 1:
            edit_speak = self.speech_opts.get("status_edit", True)
            self.say("Son çalışma alanı silinemez", speak=edit_speak, update_status=edit_speak)
            return
        current = self.workspaces[self.active_workspace] if 0 <= self.active_workspace < len(self.workspaces) else None
        if current and current.get("media_path"):
            edit_speak = self.speech_opts.get("status_edit", True)
            self.say("Sadece boş çalışma alanı silinebilir", speak=edit_speak, update_status=edit_speak)
            return
        old_idx = self.active_workspace
        old_player = self.workspace_players[old_idx] if old_idx < len(self.workspace_players) else None
        self.workspaces.pop(old_idx)
        if old_idx < len(self.workspace_players):
            self.workspace_players.pop(old_idx)
        if old_idx < len(self.workspace_loaded_media):
            self.workspace_loaded_media.pop(old_idx)
        if self.active_workspace >= len(self.workspaces):
            self.active_workspace = len(self.workspaces) - 1
        self._workspace_apply(self.workspaces[self.active_workspace])
        if old_player and old_player is not self.player:
            try:
                old_player.terminate()
            except Exception:
                pass
        file_speak = self.speech_opts.get("status_file", True)
        name = os.path.basename(self.media_path) if self.media_path else "boş alan"
        self.say(f"Çalışma alanı {self.active_workspace + 1}: {name}", speak=file_speak, update_status=file_speak)

    def _close_active_workspace(self):
        if self.media_path and self.project_dirty:
            dlg = wx.MessageDialog(self, "Aktif alan proje olarak kaydedilsin mi?", "Onay", style=wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION)
            if hasattr(dlg, "SetYesNoCancelLabels"):
                dlg.SetYesNoCancelLabels("Evet", "Hayır", "İptal")
            if hasattr(dlg, "SetEscapeId"):
                dlg.SetEscapeId(wx.ID_CANCEL)
            try:
                res = dlg.ShowModal()
            finally:
                dlg.Destroy()
            if res == wx.ID_CANCEL:
                return
            if res == wx.ID_YES:
                if not self._save_project_as_dialog(single_workspace=True):
                    return
        had_media = bool(self.media_path)
        self.close_video(skip_confirm=True)
        if had_media:
            self._remove_active_workspace()

    def _close_all_workspaces(self):
        has_any_media = any((ws or {}).get("media_path") for ws in self.workspaces)
        if not has_any_media:
            return
        dlg = wx.MessageDialog(self, "Tüm alanlar proje olarak kaydedilsin mi?", "Onay", style=wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION)
        if hasattr(dlg, "SetYesNoCancelLabels"):
            dlg.SetYesNoCancelLabels("Evet", "Hayır", "İptal")
        if hasattr(dlg, "SetEscapeId"):
            dlg.SetEscapeId(wx.ID_CANCEL)
        try:
            res = dlg.ShowModal()
        finally:
            dlg.Destroy()
        if res == wx.ID_CANCEL:
            return
        if res == wx.ID_YES:
            if not self._save_project_as_dialog(save_all_workspaces=True, force_default_name="proje.bve"):
                return
        for idx in range(len(self.workspaces)):
            if idx != self.active_workspace:
                self._switch_workspace(idx)
            if self.media_path:
                self.close_video(skip_confirm=True)

    def _init_workspaces(self):
        self.workspaces = [self._workspace_snapshot()]
        self.active_workspace = 0
        self.workspace_players = [self.player]
        self.workspace_loaded_media = [self.media_path]

    def _build_media_from_clipboard(self):
        if not self.segment_clipboard or not self.segment_clipboard.get("path"):
            raise Exception("Yapıştırılacak kopya yok")
        src_path = self.segment_clipboard.get("path")
        if not src_path or not os.path.exists(src_path):
            raise Exception("Kopyalanan kaynak bulunamadı")
        segments = self.segment_clipboard.get("segments") or [
            (float(self.segment_clipboard.get("start", 0.0) or 0.0), float(self.segment_clipboard.get("end", 0.0) or 0.0))
        ]
        valid_segments = [(float(a), float(b)) for a, b in segments if float(b) - float(a) > 0.001]
        if not valid_segments:
            raise Exception("Yapıştırılacak geçerli bölüm yok")
        media_kind = self.segment_clipboard.get("media_kind", "video")
        suffix = ".mp4" if media_kind == "video" else ".m4a"
        fd_out, out_path = self._temp_media_path("edit", suffix, src_path)
        os.close(fd_out)
        cleanup_files = []
        try:
            parts = []
            for seg_start, seg_end in valid_segments:
                fd_seg, seg_path = self._temp_media_path("part", suffix, src_path)
                os.close(fd_seg)
                cleanup_files.append(seg_path)
                if media_kind == "video":
                    source_gain = self.segment_clipboard.get("mix_gain", 1.0)
                    self._extract_fast_reencode_segment(src_path, seg_start, seg_end, seg_path, source_gain)
                else:
                    self._extract_audio_aac_segment(src_path, seg_start, seg_end, seg_path)
                parts.append(seg_path)
            if len(parts) == 1:
                shutil.copyfile(parts[0], out_path)
            else:
                self._concat_files_copy(parts, out_path)
            self.adapted_temp_files.add(out_path)
            return out_path
        except Exception:
            try:
                if os.path.exists(out_path):
                    os.remove(out_path)
            except Exception:
                pass
            raise
        finally:
            for f in cleanup_files:
                try:
                    os.remove(f)
                except Exception:
                    pass

    def _detect_media_kind(self, path):
        if not path:
            return "video"
        audio_exts = {".wav", ".aac", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wma"}
        ext = os.path.splitext(path)[1].lower()
        return "audio" if ext in audio_exts else "video"

    def _probe_signature(self, path):
        cmd_v = [
            self.ffprobe_executable, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate", "-of", "json", self._ffmpeg_safe_path(path)
        ]
        data_v = json.loads(self._check_output(cmd_v, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore"))
        sv = (data_v.get("streams") or [{}])[0]
        fps = 30.0
        rate = sv.get("r_frame_rate", "30/1")
        try:
            n, d = rate.split("/", 1)
            fps = float(n) / max(float(d), 1.0)
        except Exception:
            try:
                fps = float(rate)
            except Exception:
                pass
        cmd_a = [
            self.ffprobe_executable, "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=sample_rate,channels", "-of", "json", self._ffmpeg_safe_path(path)
        ]
        data_a = json.loads(self._check_output(cmd_a, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore"))
        sa = (data_a.get("streams") or [{}])[0]
        return {
            "width": int(sv.get("width") or 0),
            "height": int(sv.get("height") or 0),
            "fps": float(fps),
            "sample_rate": int(sa.get("sample_rate") or 48000),
            "channels": int(sa.get("channels") or 2),
        }

    def _adapt_to_signature(self, src_path, sig):
        fd, out_path = self._temp_media_path("edit", ".tmp", src_path)
        os.close(fd)
        vf = (
            f"scale={sig['width']}:{sig['height']}:force_original_aspect_ratio=decrease,"
            f"pad={sig['width']}:{sig['height']}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={sig['fps']}"
        )
        cmd = [
            self.ffmpeg_executable, "-y", "-i", self._ffmpeg_safe_path(src_path),
            "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", str(sig["sample_rate"]), "-ac", str(sig["channels"]),
            "-f", "mp4",
            self._ffmpeg_safe_path(out_path),
        ]
        try:
            self._check_output(cmd, stderr=subprocess.STDOUT)
        except Exception as ex:
            try:
                os.remove(out_path)
            except Exception:
                pass
            raise Exception(f"Video uyarlama başarısız: {ex}")
        self.adapted_temp_files.add(out_path)
        return out_path

    def _get_media_duration(self, path):
        cmd = [
            self.ffprobe_executable,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            self._ffmpeg_safe_path(path),
        ]
        try:
            out = self._check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore").strip()
            return max(0.0, float(out))
        except Exception:
            return 0.0

    def _extract_normalized_segment(self, input_path, start, end, sig, out_path, audio_gain=1.0):
        dur = max(0.0, end - start)
        vf = (
            f"scale={sig['width']}:{sig['height']}:force_original_aspect_ratio=decrease,"
            f"pad={sig['width']}:{sig['height']}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={sig['fps']}"
        )
        cmd = [
            self.ffmpeg_executable,
            "-y",
            "-ss",
            str(max(0.0, start)),
            "-i",
            self._ffmpeg_safe_path(input_path),
            "-t",
            str(max(0.001, dur)),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
        ]
        if audio_gain <= 0.0001:
            cmd += ["-an"]
        else:
            cmd += ["-c:a", "aac", "-ar", str(sig["sample_rate"]), "-ac", str(sig["channels"])]
            if abs(audio_gain - 1.0) > 0.001:
                cmd += ["-af", f"volume={audio_gain:.4f}"]
        cmd += [self._ffmpeg_safe_path(out_path)]
        self._check_output(cmd, stderr=subprocess.STDOUT)

    def _extract_copy_segment(self, input_path, start, end, out_path):
        dur = max(0.0, end - start)
        cmd = [
            self.ffmpeg_executable,
            "-y",
            "-ss",
            str(max(0.0, start)),
            "-i",
            self._ffmpeg_safe_path(input_path),
            "-t",
            str(max(0.001, dur)),
            "-c",
            "copy",
            self._ffmpeg_safe_path(out_path),
        ]
        self._check_output(cmd, stderr=subprocess.STDOUT)

    def _extract_fast_reencode_segment(self, input_path, start, end, out_path, audio_gain=1.0):
        dur = max(0.0, end - start)
        cmd = [
            self.ffmpeg_executable,
            "-y",
            "-ss",
            str(max(0.0, start)),
            "-i",
            self._ffmpeg_safe_path(input_path),
            "-t",
            str(max(0.001, dur)),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "20",
            "-c:a",
            "aac",
        ]
        if audio_gain <= 0.0001:
            cmd += ["-an"]
        elif abs(audio_gain - 1.0) > 0.001:
            cmd += ["-af", f"volume={audio_gain:.4f}"]
        cmd += [self._ffmpeg_safe_path(out_path)]
        self._check_output(cmd, stderr=subprocess.STDOUT)

    def _extract_audio_aac_segment(self, input_path, start, end, out_path):
        dur = max(0.0, end - start)
        cmd = [
            self.ffmpeg_executable,
            "-y",
            "-ss",
            str(max(0.0, start)),
            "-i",
            self._ffmpeg_safe_path(input_path),
            "-t",
            str(max(0.001, dur)),
            "-vn",
            "-sn",
            "-c:a",
            "aac",
            "-b:a",
            "320k",
            self._ffmpeg_safe_path(out_path),
        ]
        self._check_output(cmd, stderr=subprocess.STDOUT)

    def _temp_media_path(self, label, suffix, ref_path=None):
        base_src = ref_path or self.source_path or self.media_path or "video"
        base = os.path.splitext(os.path.basename(base_src))[0]
        safe = "".join(ch if ch.isalnum() else "_" for ch in base).strip("_") or "video"
        return tempfile.mkstemp(prefix=f"{safe}_{label}_", suffix=suffix)

    def _is_signature_compatible(self, sig_a, sig_b):
        if not sig_a or not sig_b:
            return False
        for key in ("width", "height", "sample_rate", "channels"):
            if int(sig_a.get(key, 0)) != int(sig_b.get(key, 0)):
                return False
        return abs(float(sig_a.get("fps", 0.0)) - float(sig_b.get("fps", 0.0))) <= 0.01

    def _concat_files_copy(self, files, out_path):
        fd, list_path = tempfile.mkstemp(prefix="bve_concat_", suffix=".txt")
        os.close(fd)
        try:
            with open(list_path, "w", encoding="utf-8") as f:
                for item in files:
                    esc = item.replace("'", "'\\''")
                    f.write(f"file '{esc}'\n")
            cmd = [
                self.ffmpeg_executable,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                self._ffmpeg_safe_path(list_path),
                "-c",
                "copy",
                self._ffmpeg_safe_path(out_path),
            ]
            self._check_output(cmd, stderr=subprocess.STDOUT)
        finally:
            try:
                os.remove(list_path)
            except Exception:
                pass

    def _virtual_length_for_total_length(self, total_length):
        total_length = max(0.0, float(total_length))
        merged = self.get_merged()
        kept = 0.0
        prev = 0.0
        for s, e in merged:
            if prev >= total_length:
                break
            cut_start = max(0.0, min(float(s), total_length))
            cut_end = max(cut_start, min(float(e), total_length))
            if cut_start > prev:
                kept += cut_start - prev
            prev = max(prev, cut_end)
        if prev < total_length:
            kept += total_length - prev
        return max(0.0, kept)

    def _split_keep_segments_at_virtual(self, virtual_pos, total_length=None):
        virtual_pos = max(0.0, float(virtual_pos))
        if total_length is None:
            total_length = self.length
        total_length = max(0.0, float(total_length))
        merged = self.get_merged()
        keep_segments = []
        last = 0.0
        for a, b in merged:
            if last >= total_length:
                break
            start = max(0.0, min(float(a), total_length))
            end = max(start, min(float(b), total_length))
            if last < start:
                keep_segments.append((last, start))
            last = max(last, end)
        if last < total_length:
            keep_segments.append((last, total_length))
        before = []
        after = []
        remain = virtual_pos
        for seg_start, seg_end in keep_segments:
            seg_dur = max(0.0, float(seg_end) - float(seg_start))
            if seg_dur <= 0.001:
                continue
            if remain <= 0.001:
                after.append((seg_start, seg_end))
                continue
            if remain >= seg_dur - 0.001:
                before.append((seg_start, seg_end))
                remain -= seg_dur
                continue
            split_point = seg_start + remain
            if split_point - seg_start > 0.001:
                before.append((seg_start, split_point))
            if seg_end - split_point > 0.001:
                after.append((split_point, seg_end))
            remain = 0.0
        return before, after

    def _selection_kept_segments(self, start, end):
        start = max(0.0, float(start))
        end = max(start, float(end))
        if end - start <= 0:
            return []
        merged = self.get_merged()
        if not merged:
            return [(start, end)]
        parts = []
        cursor = start
        for cut_start, cut_end in merged:
            if cut_end <= start:
                continue
            if cut_start >= end:
                break
            if cursor < cut_start:
                parts.append((cursor, min(cut_start, end)))
            cursor = max(cursor, cut_end)
            if cursor >= end:
                break
        if cursor < end:
            parts.append((cursor, end))
        return [(a, b) for (a, b) in parts if b - a > 0.001]

    def _run_blocking_with_progress(self, title, message, worker):
        progress = ProgressDialog(self, title=title, message=message, modal=False, pulse=True)
        result = {"value": None, "error": None}
        def _job():
            try:
                result["value"] = worker()
            except Exception as ex:
                result["error"] = ex
        t = threading.Thread(target=_job, daemon=True)
        t.start()
        progress.Show()
        try:
            while t.is_alive():
                wx.YieldIfNeeded()
                time.sleep(0.05)
        finally:
            progress.destroy()
        if result["error"] is not None:
            raise result["error"]
        return result["value"]

    def _copy_selection(self):
        if not self._require_media():
            return
        if self.mark_in is None or self.mark_out is None or self.mark_in >= self.mark_out:
            edit_speak = self.speech_opts.get("status_edit", True)
            self.say("Kopyalamak için geçerli seçim yok", speak=edit_speak, update_status=edit_speak)
            return
        segments = self._selection_kept_segments(float(self.mark_in), float(self.mark_out))
        if not segments:
            edit_speak = self.speech_opts.get("status_edit", True)
            self.say("Kopyalanacak görünür bölüm yok", speak=edit_speak, update_status=edit_speak)
            return
        media_kind = self._detect_media_kind(self.source_path or self.media_path)
        mix_gain = self._effective_gain_from_volume(self.volume)
        if self.muted:
            mix_gain = 0.0
        self.segment_clipboard = {
            "path": self.media_path,
            "start": float(self.mark_in),
            "end": float(self.mark_out),
            "segments": segments,
            "media_kind": media_kind,
            "mix_gain": mix_gain,
            "workspace_index": self.active_workspace,
        }
        edit_speak = self.speech_opts.get("status_edit", True)
        self.say("Seçim kopyalandı", speak=edit_speak, update_status=edit_speak)

    def _cut_selection(self):
        if not self._require_media():
            return
        if self.mark_in is None or self.mark_out is None or self.mark_in >= self.mark_out:
            edit_speak = self.speech_opts.get("status_edit", True)
            self.say("Kesmek için geçerli seçim yok", speak=edit_speak, update_status=edit_speak)
            return
        self._copy_selection()
        segments = self.segment_clipboard.get("segments", []) if self.segment_clipboard else []
        for seg in segments:
            a = self._normalize_time(seg[0])
            b = self._normalize_time(seg[1])
            if b - a <= 0.0005:
                continue
            self.cuts.append((a, b))
            self.last_cut = (a, b)
            self.undo_stack.append(("cut", (a, b)))
        self.mark_in_out_changed()
        self.mark_in = self.mark_out = None
        self._mark_project_dirty()
        edit_speak = self.speech_opts.get("status_edit", True)
        self.say("Seçim kesildi", speak=edit_speak, update_status=edit_speak)

    def _push_replace_media_undo(self):
        if not self.media_path:
            return
        snapshot = {
            "media_path": self.media_path,
            "source_path": self.source_path,
            "cuts": deepcopy(self.cuts),
            "merged_cuts": deepcopy(self.merged_cuts),
            "mark_in": self.mark_in,
            "mark_out": self.mark_out,
            "position": self.real_to_virtual(self.current_time()),
            "was_playing": self.is_playing(),
        }
        self.undo_stack.append(("replace_media", snapshot))

    def _restore_replace_media_undo(self, snapshot):
        self.media_path = snapshot.get("media_path")
        self.source_path = snapshot.get("source_path")
        self.cuts = deepcopy(snapshot.get("cuts") or [])
        self.merged_cuts = deepcopy(snapshot.get("merged_cuts"))
        self.mark_in = snapshot.get("mark_in")
        self.mark_out = snapshot.get("mark_out")
        restore_pos = max(0.0, float(snapshot.get("position", 0.0)))
        was_playing = bool(snapshot.get("was_playing", False))
        self._reload_current_media(restore_pos, was_playing)
        self._save_active_workspace()
        self._mark_project_dirty()

    def _paste_clipboard_segment(self):
        if not self.segment_clipboard or not self.segment_clipboard.get("path"):
            edit_speak = self.speech_opts.get("status_edit", True)
            self.say("Yapıştırılacak kopya yok", speak=edit_speak, update_status=edit_speak)
            return
        if not self.media_path:
            try:
                created_media = self._run_blocking_with_progress("Hazırlanıyor", "Boş alana yapıştırma hazırlanıyor...", self._build_media_from_clipboard)
                self.media_path = created_media
                self.source_path = created_media
                self.cuts = []
                self.undo_stack = []
                self.merged_cuts = None
                self.mark_in = self.mark_out = None
                self.last_cut = None
                self.last_in = None
                self.last_out = None
                self.audio_codec = None
                self.video_codec = None
                self._load_media(self.media_path)
                self.timer.Start(300)
                self._mark_project_dirty()
                self._save_active_workspace()
                edit_speak = self.speech_opts.get("status_edit", True)
                self.say("Boş alana yapıştırıldı", speak=edit_speak, update_status=edit_speak)
                return
            except Exception as ex:
                self._show_error_dialog(f"Yapıştırma başarısız: {ex}", "Hata")
                return
        src_path = self.segment_clipboard["path"]
        src_segments = self.segment_clipboard.get("segments") or [(self.segment_clipboard["start"], self.segment_clipboard["end"])]
        src_kind = self.segment_clipboard.get("media_kind", "video")
        dst_kind = self._detect_media_kind(self.source_path or self.media_path)
        if src_kind != "video" or dst_kind != "video":
            self._show_error_dialog("Ctrl+V yalnızca video->video segment yapıştırmada kullanılabilir. Ses için Ctrl+M kullanın.", "Bilgi")
            return
        if not os.path.exists(src_path):
            self._show_error_dialog("Kopyalanan kaynak video bulunamadı.", "Hata")
            return
        requested_insert_virtual = max(0.0, self.real_to_virtual(self.current_time()))
        edit_speak = self.speech_opts.get("status_edit", True)
        self.say("Yapıştırma başladı", speak=edit_speak, update_status=edit_speak)
        try:
            def worker():
                target_len = self._get_media_duration(self.media_path)
                if target_len <= 0:
                    target_len = self.length
                target_sig = self._probe_signature(self.media_path)
                src_sig = self._probe_signature(src_path)
                compatible = self._is_signature_compatible(target_sig, src_sig)
                fd1, part1 = self._temp_media_path("before", ".mp4", self.media_path)
                source_parts = []
                valid_segments = [(a, b) for (a, b) in src_segments if float(b) - float(a) > 0.001]
                for _ in valid_segments:
                    fd_seg, seg_path = self._temp_media_path("insert", ".mp4", src_path)
                    os.close(fd_seg)
                    source_parts.append(seg_path)
                fd2, part2 = self._temp_media_path("inserted", ".mp4", src_path)
                fd3, part3 = self._temp_media_path("after", ".mp4", self.media_path)
                fd4, merged = self._temp_media_path("edit", ".mp4", self.media_path)
                os.close(fd1); os.close(fd2); os.close(fd3); os.close(fd4)
                cleanup_files = [part1, part2, part3] + source_parts
                try:
                    if not valid_segments:
                        raise Exception("Yapıştırılacak geçerli bir segment bulunamadı.")
                    target_gain = self._effective_gain_from_volume(self.volume) if not self.muted else 0.0
                    source_gain = self.segment_clipboard.get("mix_gain", 1.0)
                    virtual_total = self._virtual_length_for_total_length(target_len)
                    insert_at_virtual = max(0.0, min(requested_insert_virtual, virtual_total))
                    before_ranges, after_ranges = self._split_keep_segments_at_virtual(insert_at_virtual, target_len)
                    fast_extract = self._extract_fast_reencode_segment if compatible else None
                    part1_segments = []
                    for seg_start, seg_end in before_ranges:
                        fd_pre, pre_path = self._temp_media_path("pre", ".mp4", self.media_path)
                        os.close(fd_pre)
                        cleanup_files.append(pre_path)
                        part1_segments.append(pre_path)
                        if compatible:
                            fast_extract(self.media_path, seg_start, seg_end, pre_path, target_gain)
                        else:
                            self._extract_normalized_segment(self.media_path, seg_start, seg_end, target_sig, pre_path, target_gain)
                    if not part1_segments:
                        part1 = None
                    elif len(part1_segments) == 1:
                        shutil.copyfile(part1_segments[0], part1)
                    else:
                        self._concat_files_copy(part1_segments, part1)
                    for idx, (seg_start, seg_end) in enumerate(valid_segments):
                        if compatible:
                            fast_extract(src_path, seg_start, seg_end, source_parts[idx], source_gain)
                        else:
                            self._extract_normalized_segment(src_path, seg_start, seg_end, target_sig, source_parts[idx], source_gain)
                    if len(source_parts) == 1:
                        shutil.copyfile(source_parts[0], part2)
                    else:
                        self._concat_files_copy(source_parts, part2)
                    part3_segments = []
                    for seg_start, seg_end in after_ranges:
                        fd_post, post_path = self._temp_media_path("post", ".mp4", self.media_path)
                        os.close(fd_post)
                        cleanup_files.append(post_path)
                        part3_segments.append(post_path)
                        if compatible:
                            fast_extract(self.media_path, seg_start, seg_end, post_path, target_gain)
                        else:
                            self._extract_normalized_segment(self.media_path, seg_start, seg_end, target_sig, post_path, target_gain)
                    if not part3_segments:
                        part3 = None
                    elif len(part3_segments) == 1:
                        shutil.copyfile(part3_segments[0], part3)
                    else:
                        self._concat_files_copy(part3_segments, part3)
                    concat_inputs = [p for p in (part1, part2, part3) if p]
                    if len(concat_inputs) == 1:
                        shutil.copyfile(concat_inputs[0], merged)
                    else:
                        self._concat_files_copy(concat_inputs, merged)
                finally:
                    for f in cleanup_files:
                        try:
                            os.remove(f)
                        except Exception:
                            pass
                return merged
            merged = self._run_blocking_with_progress("Uyarlanıyor", "Yapıştırma hazırlanıyor...", worker)
            self._push_replace_media_undo()
            self.adapted_temp_files.add(merged)
            self.media_path = merged
            self.source_path = merged
            self.cuts = []
            self.merged_cuts = None
            self.mark_in = self.mark_out = None
            self._load_media(self.media_path)
            self.timer.Start(300)
            self._mark_project_dirty()
            self._save_active_workspace()
            self._show_info_dialog("Seçim başarıyla yapıştırıldı.", "Bilgi")
        except Exception as ex:
            self._show_error_dialog(f"Yapıştırma başarısız: {ex}", "Hata")

    def _mix_clipboard_audio(self):
        if not self._require_media():
            return
        dst_kind = self._detect_media_kind(self.source_path or self.media_path)
        if dst_kind != "video":
            self._show_error_dialog("Ses mixleme hedefi video olmalıdır.", "Bilgi")
            return
        if not self.segment_clipboard or self.segment_clipboard.get("media_kind") != "audio":
            self._show_error_dialog("Önce ses alanından bir bölüm kopyalayın (Ctrl+C / Ctrl+Shift+C).", "Bilgi")
            return
        src_path = self.segment_clipboard.get("path")
        if not src_path or not os.path.exists(src_path):
            self._show_error_dialog("Kopyalanan ses kaynağı bulunamadı.", "Hata")
            return
        insert_at_virtual = self.real_to_virtual(self.current_time())
        edit_speak = self.speech_opts.get("status_edit", True)
        self.say("Mixleme başladı", speak=edit_speak, update_status=edit_speak)
        try:
            def worker():
                target_len = self._get_media_duration(self.media_path)
                if target_len <= 0:
                    target_len = self.length
                insert_at = max(0.0, min(self.virtual_to_real(insert_at_virtual), target_len))
                mix_trim_end = max(0.01, target_len)
                target_sig = self._probe_signature(self.media_path)
                target_channels = int(target_sig.get("channels", 2) or 2)
                target_sample_rate = int(target_sig.get("sample_rate", 48000) or 48000)
                clip_gain = self.segment_clipboard.get("mix_gain", 1.0)
                target_gain = self._effective_gain_from_volume(self.volume) if not self.muted else 0.0
                fd1, clip_path = self._temp_media_path("mixclip", ".m4a", src_path)
                fd2, out_path = self._temp_media_path("edit", ".mp4", self.media_path)
                os.close(fd1); os.close(fd2)
                src_input = self._ffmpeg_safe_path(src_path)
                copied_input = None
                if os.name == "nt" and src_input == src_path and any(ord(ch) > 127 or ch == "'" for ch in src_path):
                    fd_in, tmp_in = self._temp_media_path("mixsrc", os.path.splitext(src_path)[1] or ".tmp", src_path)
                    os.close(fd_in)
                    shutil.copyfile(src_path, tmp_in)
                    copied_input = tmp_in
                    src_input = self._ffmpeg_safe_path(tmp_in)
                try:
                    clip_segments = self.segment_clipboard.get("segments") or [
                        (float(self.segment_clipboard["start"]), float(self.segment_clipboard["end"]))
                    ]
                    clip_segments = [(float(a), float(b)) for (a, b) in clip_segments if float(b) - float(a) > 0.001]
                    if not clip_segments:
                        raise Exception("Mix için geçerli ses seçimi bulunamadı.")
                    def run_cmd(cmd):
                        try:
                            self._check_output(cmd, stderr=subprocess.STDOUT)
                        except subprocess.CalledProcessError as e:
                            output = e.output.decode("utf-8", errors="replace") if isinstance(e.output, (bytes, bytearray)) else str(e.output)
                            raise Exception(output.strip() or str(e))
                    if len(clip_segments) == 1:
                        seg_start, seg_end = clip_segments[0]
                        dur = max(0.001, seg_end - seg_start)
                        clip_cmd = [
                            self.ffmpeg_executable,
                            "-y",
                            "-ss",
                            str(max(0.0, seg_start)),
                            "-i",
                            src_input,
                            "-t",
                            str(dur),
                            "-vn",
                            "-sn",
                            "-c:a",
                            "aac",
                            self._ffmpeg_safe_path(clip_path),
                        ]
                        try:
                            run_cmd(clip_cmd)
                        except Exception:
                            clip_cmd_retry = [
                                self.ffmpeg_executable,
                                "-y",
                                "-i",
                                src_input,
                                "-ss",
                                str(max(0.0, seg_start)),
                                "-t",
                                str(dur),
                                "-vn",
                                "-sn",
                                "-c:a",
                                "aac",
                                self._ffmpeg_safe_path(clip_path),
                            ]
                            run_cmd(clip_cmd_retry)
                    else:
                        part_files = []
                        try:
                            for seg_start, seg_end in clip_segments:
                                fd_part, part_path = self._temp_media_path("mixpart", ".m4a", src_path)
                                os.close(fd_part)
                                part_files.append(part_path)
                                part_cmd = [
                                    self.ffmpeg_executable,
                                    "-y",
                                    "-ss",
                                    str(max(0.0, seg_start)),
                                    "-i",
                                    src_input,
                                    "-t",
                                    str(max(0.001, seg_end - seg_start)),
                                    "-vn",
                                    "-sn",
                                    "-c:a",
                                    "aac",
                                    self._ffmpeg_safe_path(part_path),
                                ]
                                run_cmd(part_cmd)
                            if len(part_files) == 1:
                                shutil.copyfile(part_files[0], clip_path)
                            else:
                                self._concat_files_copy(part_files, clip_path)
                        finally:
                            for part in part_files:
                                try:
                                    os.remove(part)
                                except Exception:
                                    pass
                    delay_ms = int(insert_at * 1000)
                    adelay = str(delay_ms)
                    has_audio = self.has_audio_stream()
                    if has_audio:
                        target_chain = "anull"
                        if target_gain <= 0.0001:
                            target_chain = "volume=0"
                        elif abs(target_gain - 1.0) > 0.001:
                            target_chain = f"volume={target_gain:.4f}"
                        clip_chain = f"adelay={adelay}:all=1"
                        if abs(clip_gain - 1.0) > 0.001:
                            clip_chain += f",volume={clip_gain:.4f}"
                        fcx = f"[0:a]{target_chain}[a0];[1:a]{clip_chain}[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
                        cmd = [
                            self.ffmpeg_executable,
                            "-y",
                            "-i",
                            self._ffmpeg_safe_path(self.media_path),
                            "-i",
                            self._ffmpeg_safe_path(clip_path),
                            "-filter_complex",
                            fcx,
                            "-map",
                            "0:v",
                            "-map",
                            "[aout]",
                            "-c:v",
                            "copy",
                            "-c:a",
                            "aac",
                            "-b:a",
                            "320k",
                            "-ac",
                            str(target_channels),
                            "-ar",
                            str(target_sample_rate),
                            self._ffmpeg_safe_path(out_path),
                        ]
                    else:
                        clip_only_chain = f"adelay={adelay}:all=1"
                        if abs(clip_gain - 1.0) > 0.001:
                            clip_only_chain += f",volume={clip_gain:.4f}"
                        fcx = f"[1:a]{clip_only_chain},atrim=0:{mix_trim_end}[aout]"
                        cmd = [
                            self.ffmpeg_executable,
                            "-y",
                            "-i",
                            self._ffmpeg_safe_path(self.media_path),
                            "-i",
                            self._ffmpeg_safe_path(clip_path),
                            "-filter_complex",
                            fcx,
                            "-map",
                            "0:v",
                            "-map",
                            "[aout]",
                            "-c:v",
                            "copy",
                            "-c:a",
                            "aac",
                            "-b:a",
                            "320k",
                            "-ac",
                            str(target_channels),
                            "-ar",
                            str(target_sample_rate),
                            self._ffmpeg_safe_path(out_path),
                        ]
                    self._check_output(cmd, stderr=subprocess.STDOUT)
                finally:
                    if copied_input:
                        try:
                            os.remove(copied_input)
                        except Exception:
                            pass
                    try:
                        os.remove(clip_path)
                    except Exception:
                        pass
                return out_path
            merged = self._run_blocking_with_progress("Mixleniyor", "Ses video üstüne ekleniyor...", worker)
            self._push_replace_media_undo()
            self.adapted_temp_files.add(merged)
            self.media_path = merged
            self.source_path = merged
            self._load_media(self.media_path)
            self.timer.Start(300)
            self._mark_project_dirty()
            self._save_active_workspace()
            self._show_info_dialog("Ses başarıyla mixlendi.", "Bilgi")
        except Exception as ex:
            self._show_error_dialog(f"Mixleme başarısız: {ex}", "Hata")

    def _on_media_loaded(self):
        self.time.Show(True)
        self.video_panel.GetParent().Layout()

    def _on_media_closed(self):
        self.time.SetLabel("")
        self.time.Hide()
        self.video_panel.GetParent().Layout()

    def _load_media(self, path):
        self._next_media_session()
        if self.player is None:
            self.init_player()
        self.player.command("loadfile", path, "replace")
        self.player.pause = True
        self._ensure_workspace_player_slots()
        if 0 <= self.active_workspace < len(self.workspace_loaded_media):
            self.workspace_loaded_media[self.active_workspace] = path
        probed_len = self._get_media_duration(path)
        self.length = probed_len if probed_len > 0 else 0.0
        self._on_media_loaded()
        self._call_later_if_session(150, self._apply_player_volume)

    def _subprocess_hidden_kwargs(self):
        kwargs = {}
        if os.name == "nt":
            try:
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                kwargs["startupinfo"] = si
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            except Exception:
                pass
        return kwargs

    def _check_output(self, cmd, **kwargs):
        opts = self._subprocess_hidden_kwargs()
        opts.update(kwargs)
        return subprocess.check_output(cmd, **opts)

    def _popen(self, cmd, **kwargs):
        opts = self._subprocess_hidden_kwargs()
        opts.update(kwargs)
        return subprocess.Popen(cmd, **opts)

    def _ffmpeg_safe_path(self, path):
        if not path:
            return path
        normalized = os.path.abspath(path)
        if os.name != "nt":
            return normalized
        try:
            get_short_path_name = ctypes.windll.kernel32.GetShortPathNameW
            get_short_path_name.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
            get_short_path_name.restype = ctypes.c_uint
            out_buffer = ctypes.create_unicode_buffer(32768)
            result = get_short_path_name(normalized, out_buffer, len(out_buffer))
            if result > 0:
                return out_buffer.value
        except Exception:
            pass
        return normalized

    def _reload_current_media(self, restore_virtual_time=None, resume_playback=False):
        if not self.media_path or not self.player:
            return
        restore_virtual_time = 0.0 if restore_virtual_time is None else max(0.0, restore_virtual_time)
        self._load_media(self.media_path)
        self._call_later_if_session(80, self.seek_virtual, min(restore_virtual_time, self.get_virtual_length()))
        self._call_later_if_session(150, self._apply_player_volume)
        if resume_playback:
            self._call_later_if_session(200, setattr, self.player, "pause", False)

    # ---------- YENİ SES KONTROL FONKSİYONLARI (VLC BUG'I ÇÖZÜLDÜ) ----------
    def _effective_gain_from_volume(self, volume_value):
        """Volume (0-300) → FFmpeg/mpv gain (0.0-3.0). Artık ses yükseltme çalışıyor!"""
        try:
            v = float(volume_value)
        except Exception:
            v = 100.0
        v = max(0.0, min(300.0, v))
        return v / 100.0

    def _apply_player_volume(self):
        """MpV için güvenli ses ayarı - sistem seslerini kısıp VLC kalıntısı yapmıyor"""
        if not self.player:
            return
        gain = self._effective_gain_from_volume(self.volume)
        if self.muted:
            self.player.af = "volume=0"
        elif abs(gain - 1.0) < 0.001:
            self.player.af = ""
        else:
            self.player.af = f"volume={gain:.4f}"
        self.player.mute = self.muted

    def _build_audio_filters(self):
        if self.muted:
            return None, ["-an"]
        filters = []
        gain = self._effective_gain_from_volume(self.volume)
        if abs(gain - 1.0) > 0.001:
            filters.append(f"volume={gain:.4f}")
        return filters, []

    # ---------- STATUS ----------
    def say(self, text, speak=None, update_status=True):
        if update_status:
            self.status.SetLabel(text)
        if speak is None:
            speak = self.speech_opts.get("status_general", True)
        if speak:
            self.reader.SetValue(text)
            self.reader.SetInsertionPointEnd()

    def _reset_project_state(self):
        self.project_dirty = False
        self.project_path = None
        self.project_temp_path = None

    def _project_default_path(self):
        if not self.media_path:
            return "proje.bve"
        base = os.path.splitext(os.path.basename(self.media_path))[0]
        return f"{base}.bve"

    def _autosave_project_temp(self):
        if not self.media_path:
            return
        if not self.project_temp_path:
            base = os.path.splitext(os.path.basename(self.media_path))[0]
            temp_name = f"kor_editor_{base}_{os.getpid()}.bve.tmp"
            self.project_temp_path = os.path.join(tempfile.gettempdir(), temp_name)
        try:
            data = self._current_project_data()
            with open(self.project_temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _mark_project_dirty(self):
        self.project_dirty = True
        self._autosave_project_temp()
        self._save_active_workspace()

    def _current_project_data(self):
        self._save_active_workspace()
        return {
            "version": 3,
            "active_workspace": self.active_workspace,
            "workspaces": self.workspaces,
            "media_path": self.media_path,
            "source_path": self.source_path,
            "cuts": self.cuts,
            "undo_stack": self.undo_stack,
            "mark_in": self.mark_in,
            "mark_out": self.mark_out,
            "last_in": self.last_in,
            "last_out": self.last_out,
            "volume": self.volume,
            "muted": self.muted,
            "video_opts": self.video_opts,
            "audio_opts": self.audio_opts,
            "speech_opts": self.speech_opts,
            "position": self.real_to_virtual(self.current_time()),
        }

    def _save_project_as_dialog(self, single_workspace=False, save_all_workspaces=False, force_default_name=None):
        default_dir = self.last_project_dir or (os.path.dirname(self.project_path) if self.project_path else self.last_open_dir)
        default_file = force_default_name or self._project_default_path()
        project_data = self._current_project_data()
        if single_workspace:
            ws = self._workspace_snapshot()
            project_data["workspaces"] = [self._workspace_default() for _ in range(len(self.workspaces))]
            project_data["workspaces"][self.active_workspace] = ws
            project_data["active_workspace"] = self.active_workspace
        elif save_all_workspaces:
            self._save_active_workspace()
            project_data = self._current_project_data()
        d = wx.FileDialog(
            self,
            "Projeyi Kaydet",
            defaultDir=default_dir,
            defaultFile=default_file,
            wildcard="Proje Dosyası|*.bve|Tüm Dosyalar|*.*",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )
        path = None
        if d.ShowModal() == wx.ID_OK:
            path = d.GetPath()
            if not path.lower().endswith(".bve"):
                path += ".bve"
        d.Destroy()
        if not path:
            return False
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(project_data, f, ensure_ascii=False, indent=2)
            self.project_path = path
            self.last_project_dir = os.path.dirname(path)
            self.project_dirty = False
            file_speak = self.speech_opts.get("status_file", True)
            self.say("Proje kaydedildi", speak=file_speak, update_status=file_speak)
            return True
        except Exception as ex:
            self._show_error_dialog(f"Proje kaydedilemedi: {ex}", "Hata")
            return False

    def _confirm_save_project_before_close(self):
        if not self.media_path or not self.project_dirty:
            return True
        dlg = wx.MessageDialog(
            self,
            "Proje olarak kaydedilsin mi?",
            "Onay",
            style=wx.YES_NO | wx.ICON_QUESTION,
        )
        if hasattr(dlg, "SetYesNoLabels"):
            dlg.SetYesNoLabels("Evet", "Hayır")
        if hasattr(dlg, "SetEscapeId"):
            dlg.SetEscapeId(wx.ID_NO)
        try:
            result = dlg.ShowModal()
        finally:
            dlg.Destroy()
        if result == wx.ID_YES:
            return self._save_project_as_dialog()
        return True

    def on_close_app(self, event):
        self._save_active_workspace()
        if not self._confirm_save_project_before_close():
            event.Veto()
            return
        for p in list(self.adapted_temp_files):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        for p in getattr(self, "workspace_players", []):
            if not p:
                continue
            try:
                p.terminate()
            except Exception:
                pass
        event.Skip()

    def _doc_file_path(self, filename):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)

    def _open_text_help(self, filename, title):
        path = self._doc_file_path(filename)
        if not os.path.exists(path):
            self._show_error_dialog(f"Yardım dosyası bulunamadı: {filename}", "Hata")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as ex:
            self._show_error_dialog(f"Yardım dosyası açılamadı: {ex}", "Hata")
            return
        d = HelpTextDialog(self, title, content)
        try:
            d.ShowModal()
        finally:
            d.Destroy()

    # ---------- KEYS ----------
    def bind_keys(self):
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)

    def _require_media(self):
        if self.media_path:
            return True
        file_speak = self.speech_opts.get("status_file", True)
        self.say("dosya yok", speak=file_speak, update_status=file_speak)
        return False

    def on_key(self, e):
        k = e.GetKeyCode()
        ctrl = e.ControlDown()
        alt = e.AltDown()
        shift = e.ShiftDown()
        key_n = k in (ord("N"), ord("n"))
        key_delete = k in (wx.WXK_DELETE, getattr(wx, "WXK_NUMPAD_DELETE", -1))
        if k == wx.WXK_ESCAPE and self.active_progress:
            self.active_progress.confirm_abort()
            return
        if ctrl and shift and key_n:
            self._create_new_workspace()
            return
        if ctrl and shift and key_delete:
            self._remove_active_workspace()
            return
        if ctrl and k == wx.WXK_TAB:
            if shift:
                self._switch_workspace_relative(-1)
            else:
                self._switch_workspace_relative(1)
            return
        if k == wx.WXK_TAB:
            return
        if k == wx.WXK_F1:
            d = BVEMenuDialog(self)
            try:
                d.ShowModal()
            finally:
                d.Destroy()
            return
        if alt and k == wx.WXK_F4:
            self.Close()
            return
        if ctrl and k == ord("O"):
            self.open_file()
        elif ctrl and k == ord("S"):
            if shift:
                self.open_options()
            else:
                self.save_file()
        elif ctrl and shift and k == ord("W"):
            self._close_all_workspaces()
        elif ctrl and k == ord("W"):
            self._close_active_workspace()
        elif ctrl and k == ord("A"):
            if not self._require_media():
                return
            self.mark_in = 0.0
            self.mark_out = self.virtual_to_real(self.get_virtual_length())
            self.last_in = self.mark_in
            self.last_out = self.mark_out
            in_out_speak = self.speech_opts.get("in_out", True)
            self.say("Tüm video seçildi", speak=in_out_speak, update_status=in_out_speak)
        elif ctrl and k == ord("C"):
            self._copy_selection()
        elif ctrl and shift and k == ord("C"):
            self._copy_selection()
        elif ctrl and k == ord("X"):
            self._cut_selection()
        elif ctrl and shift and k == ord("X"):
            self._cut_selection()
        elif ctrl and k == ord("V"):
            self._paste_clipboard_segment()
        elif ctrl and k == ord("M"):
            self._mix_clipboard_audio()
        elif ctrl and k == ord("Z"):
            if self.undo_stack:
                action, payload = self.undo_stack.pop()
                if action == "cut":
                    cur_v = self.real_to_virtual(self.current_time())
                    was_playing = self.is_playing()
                    target_cut = payload
                    if self.cuts and self.cuts[-1] == target_cut:
                        self.cuts.pop()
                    else:
                        for idx in range(len(self.cuts) - 1, -1, -1):
                            if self.cuts[idx] == target_cut:
                                self.cuts.pop(idx)
                                break
                    self.mark_in_out_changed()
                    self._reload_current_media(cur_v, was_playing)
                    edit_speak = self.speech_opts.get("status_edit", True)
                    self.say("Son kesim geri alındı", speak=edit_speak, update_status=edit_speak)
                    self._mark_project_dirty()
                elif action == "replace_media":
                    self._restore_replace_media_undo(payload)
                    edit_speak = self.speech_opts.get("status_edit", True)
                    self.say("Son işlem geri alındı", speak=edit_speak, update_status=edit_speak)
        elif k == wx.WXK_SPACE:
            if not self._require_media():
                return
            if self.previewing:
                self.stop_preview_clip()
            else:
                if self.is_playing():
                    self.player.pause = True
                else:
                    cur = self._normalize_time(self.current_time())
                    if self.length and cur >= self.length - 0.1:
                        self.player.time_pos = 0.0
                        self._call_later_if_session(30, setattr, self.player, "pause", False)
                    else:
                        self.player.pause = False
        elif ctrl and k == ord("G"):
            self.goto_time()
        elif ctrl and k == ord("T"):
            if not self._require_media():
                return
            in_out_speak = self.speech_opts.get("in_out", True)
            total_v = self.get_virtual_length()
            self.say(
                f"Toplam süre: {self.fmt(total_v)}",
                speak=in_out_speak,
                update_status=in_out_speak,
            )
        elif k == wx.WXK_LEFT:
            self.seek(-1)
        elif k == wx.WXK_RIGHT:
            self.seek(1)
        elif k == wx.WXK_UP and not ctrl:
            self.seek(0.5)
        elif k == wx.WXK_DOWN and not ctrl:
            self.seek(-0.5)
        elif k == wx.WXK_F5:
            self.seek(-5)
        elif k == wx.WXK_F6:
            self.seek(5)
        elif ctrl and k == wx.WXK_UP:
            self.seek(60)
        elif ctrl and k == wx.WXK_DOWN:
            self.seek(-60)
        elif ctrl and shift and k == wx.WXK_HOME:
            if not self._require_media():
                return
            cur = self._normalize_time(self.current_time())
            self.mark_in = 0.0
            self.mark_out = self._normalize_time(cur)
            self.last_in = self.mark_in
            self.last_out = self.mark_out
            in_out_speak = self.speech_opts.get("in_out", True)
            if self.mark_out > self.mark_in:
                self.say("Baştan mevcut konuma kadar seçildi", speak=in_out_speak, update_status=in_out_speak)
                self._mark_project_dirty()
            else:
                self.say("Seçim yapılamadı", speak=in_out_speak, update_status=in_out_speak)
        elif ctrl and shift and k == wx.WXK_END:
            if not self._require_media():
                return
            cur = self._normalize_time(self.current_time())
            virt_len = self.get_virtual_length()
            end_pos = self.virtual_to_real(virt_len) if virt_len > 0 else cur
            self.mark_in = self._normalize_time(cur)
            self.mark_out = self._normalize_time(end_pos)
            self.last_in = self.mark_in
            self.last_out = self.mark_out
            in_out_speak = self.speech_opts.get("in_out", True)
            if self.mark_out > self.mark_in:
                self.say("Mevcut konumdan sona kadar seçildi", speak=in_out_speak, update_status=in_out_speak)
                self._mark_project_dirty()
            else:
                self.say("Seçim yapılamadı", speak=in_out_speak, update_status=in_out_speak)
        elif k == wx.WXK_HOME:
            if not self._require_media():
                return
            self.seek_virtual(0)
            playback_speak = self.speech_opts.get("status_playback", True)
            self.say("Başa dönüldü", speak=playback_speak, update_status=playback_speak)
        elif k == wx.WXK_END:
            if not self._require_media():
                return
            virt_len = self.get_virtual_length()
            self.seek_virtual(max(0.0, virt_len))
            playback_speak = self.speech_opts.get("status_playback", True)
            self.say("Video sonuna gidildi", speak=playback_speak, update_status=playback_speak)
        elif ctrl and not shift and k == wx.WXK_PAGEUP:
            self.mark_in = self._normalize_time(self.current_time())
            self.last_in = self.mark_in
            in_out_speak = self.speech_opts.get("in_out", True)
            self.say(
                f"IN: {self.fmt(self.real_to_virtual(self.mark_in))}",
                speak=in_out_speak,
                update_status=in_out_speak,
            )
            self._mark_project_dirty()
        elif ctrl and not shift and k == wx.WXK_PAGEDOWN:
            self.mark_out = self._normalize_time(self.current_time())
            self.last_out = self.mark_out
            in_out_speak = self.speech_opts.get("in_out", True)
            self.say(
                f"OUT: {self.fmt(self.real_to_virtual(self.mark_out))}",
                speak=in_out_speak,
                update_status=in_out_speak,
            )
            self._mark_project_dirty()
        elif ctrl and shift and k == wx.WXK_PAGEUP:
            self.mark_in = None
            in_out_speak = self.speech_opts.get("in_out", True)
            self.say("Başlangıç işareti iptal edildi", speak=in_out_speak, update_status=in_out_speak)
            self._mark_project_dirty()
        elif ctrl and shift and k == wx.WXK_PAGEDOWN:
            self.mark_out = None
            in_out_speak = self.speech_opts.get("in_out", True)
            self.say("Bitiş işareti iptal edildi", speak=in_out_speak, update_status=in_out_speak)
            self._mark_project_dirty()
        elif k == wx.WXK_DELETE:
            if self.mark_in is not None and self.mark_out is not None and self.mark_in < self.mark_out:
                cut_in = self._normalize_time(self.mark_in)
                cut_out = self._normalize_time(self.mark_out)
                if cut_out - cut_in <= 0.0005:
                    return
                self.cuts.append((cut_in, cut_out))
                self.last_cut = (cut_in, cut_out)
                self.undo_stack.append(("cut", (cut_in, cut_out)))
                self.mark_in_out_changed()
                cur = self._normalize_time(self.current_time())
                if self.mark_in <= cur < self.mark_out:
                    if self.player:
                        self.player.time_pos = self.mark_out
                edit_speak = self.speech_opts.get("status_edit", True)
                self.say("Silindi", speak=edit_speak, update_status=edit_speak)
                self._mark_project_dirty()
                self.mark_in = self.mark_out = None
                if self.get_virtual_length() <= 0 and self.player:
                    self.player.pause = True
                    self.player.time_pos = 0
        elif ctrl and k == ord("1"):
            self.preview(self.mark_in)
        elif ctrl and k == ord("2"):
            self.preview(self.mark_out)
        elif ctrl and shift and k == ord("R"):
            in_out_speak = self.speech_opts.get("in_out", True)
            if self.last_in is None:
                self.say("Son IN yok", speak=in_out_speak, update_status=in_out_speak)
            else:
                self.say(
                    f"Son IN: {self.fmt(self.real_to_virtual(self.last_in))}",
                    speak=in_out_speak,
                    update_status=in_out_speak,
                )
        elif ctrl and shift and k == ord("T"):
            in_out_speak = self.speech_opts.get("in_out", True)
            if self.last_out is None:
                self.say("Son OUT yok", speak=in_out_speak, update_status=in_out_speak)
            else:
                self.say(
                    f"Son OUT: {self.fmt(self.real_to_virtual(self.last_out))}",
                    speak=in_out_speak,
                    update_status=in_out_speak,
                )
        elif ctrl and shift and k == ord("D"):
            self.show_video_properties_dialog()
        elif k == wx.WXK_F2:
            self.muted = not self.muted
            self._apply_player_volume()
            audio_speak = self.speech_opts.get("status_audio", True)
            self.say("Ses kapalı" if self.muted else "Ses açık", speak=audio_speak, update_status=audio_speak)
            self._mark_project_dirty()
        elif k == wx.WXK_F3:
            self.volume = max(0, self.volume - 10)
            self._apply_player_volume()
            audio_speak = self.speech_opts.get("status_audio", True)
            self.say(f"Ses: {self.volume}%", speak=audio_speak, update_status=audio_speak)
            self._mark_project_dirty()
        elif k == wx.WXK_F4:
            self.volume = min(300, self.volume + 10)
            self._apply_player_volume()
            audio_speak = self.speech_opts.get("status_audio", True)
            self.say(f"Ses: {self.volume}%", speak=audio_speak, update_status=audio_speak)
            self._mark_project_dirty()
        else:
            e.Skip()

    # ---------- CORE ----------
    def _normalize_time(self, value, precision=3):
        try:
            v = float(value)
        except Exception:
            v = 0.0
        return round(max(0.0, v), precision)

    def current_time(self):
        if not self.player:
            return 0.0
        t = self.player.time_pos
        if t is None:
            return 0.0
        return self._normalize_time(t)

    def is_playing(self):
        if not self.player:
            return False
        return not bool(self.player.pause)

    def seek(self, sec):
        if not self.media_path:
            return
        cur_v = self.real_to_virtual(self.current_time())
        new_v = max(0, min(self.get_virtual_length(), cur_v + sec))
        self.seek_virtual(new_v)
        time_speak = self.speech_opts.get("time", True)
        self.say(self.fmt(new_v), speak=time_speak, update_status=time_speak)

    def seek_virtual(self, v):
        r = self.virtual_to_real(v)
        if self.player:
            self.player.time_pos = r

    def goto_time(self):
        if not self.media_path:
            return
        virt_len = self.get_virtual_length()
        cur_v = self.real_to_virtual(self.current_time())
        if cur_v < 0.1:
            cur_v = 0.0
        if virt_len > 0 and abs(virt_len - cur_v) <= 0.002:
            cur_v = virt_len
        d = GoToTimeDialog(self, cur_v)
        if d.ShowModal() == wx.ID_OK:
            new_v = min(d.seconds(), virt_len)
            if virt_len > 0 and abs(virt_len - new_v) <= 0.002:
                new_v = virt_len
            self.seek_virtual(new_v)
            time_speak = self.speech_opts.get("time", True)
            self.say(self.fmt(new_v), speak=time_speak, update_status=time_speak)
        d.Destroy()

    def preview(self, point):
        if point is None:
            edit_speak = self.speech_opts.get("status_edit", True)
            self.say("İşaret yok", speak=edit_speak, update_status=edit_speak)
            return
        if not self.media_path:
            return
        self.preview_restore = self.current_time()
        self.preview_segments = None
        self.preview_segment_index = 0
        start = max(0, point - 5)
        if self.player:
            self.player.time_pos = start
        self.video_panel.Show(True)
        self.video_panel.GetParent().Layout()
        self.set_video_handle()
        if self.player:
            self.player.pause = False
        self.previewing = True
        self.timer.Start(50)
        preview_speak = self.speech_opts.get("status_preview", True)
        self.say("Önizleme başladı", speak=preview_speak, update_status=preview_speak)

    def _start_preview_segment(self, start_time):
        if not self.player:
            return
        self.player.pause = False
        self._call_later_if_session(50, self._set_time_pos, start_time)

    def _set_time_pos(self, pos):
        if self.player:
            self.player.time_pos = pos

    def stop_preview_clip(self):
        if not self.previewing:
            return
        if self.player:
            self.player.pause = True
        if self.preview_restore is not None:
            if self.player:
                self.player.time_pos = self.preview_restore
        self.video_panel.Show(False)
        self.video_panel.GetParent().Layout()
        self.previewing = False
        self.preview_restore = None
        self.preview_segments = None
        self.preview_segment_index = 0
        self.timer.Start(500)
        preview_speak = self.speech_opts.get("status_preview", True)
        self.say("Önizleme bitti", speak=preview_speak, update_status=preview_speak)

    def fmt(self, t):
        return f"{int(t // 3600):02d}:{int((t % 3600) // 60):02d}:{int(t % 60):02d}"

    def update_time(self, e):
        if not self.media_path:
            return
        cur = self._normalize_time(self.current_time())
        if self.player:
            duration = self.player.duration
            if duration and duration > 0 and self.length == 0:
                self.length = float(duration)
        if not self.previewing:
            cur = self.skip_cut_if_needed(cur)
        if self.previewing and self.preview_segments:
            seg_start, seg_end = self.preview_segments[self.preview_segment_index]
            if cur + 0.02 >= seg_end:
                if self.preview_segment_index + 1 < len(self.preview_segments):
                    self.preview_segment_index += 1
                    next_start, _ = self.preview_segments[self.preview_segment_index]
                    self._start_preview_segment(next_start)
                else:
                    self.stop_preview_clip()
                    return
        cur_v = self.real_to_virtual(cur)
        time_str = self.fmt(cur_v)
        if self.length > 0:
            time_str += f" / {self.fmt(self.get_virtual_length())}"
        self.time.SetLabel(time_str)

    def skip_cut_if_needed(self, cur):
        if not self.player or not self.is_playing():
            return cur
        merged = self.get_merged()
        if not merged:
            return cur
        epsilon = 0.01
        for start, end in merged:
            if start <= cur < end:
                new_time = min(self.length, end + epsilon)
                self.player.time_pos = new_time
                return new_time
        return cur

    def get_merged(self):
        if self.merged_cuts is None:
            if not self.cuts:
                self.merged_cuts = []
            else:
                intervals = sorted(self.cuts, key=lambda x: x[0])
                merged = [[intervals[0][0], intervals[0][1]]]
                for current in intervals[1:]:
                    prev = merged[-1]
                    if prev[1] >= current[0]:
                        prev[1] = max(prev[1], current[1])
                    else:
                        merged.append([current[0], current[1]])
                self.merged_cuts = merged
        return self.merged_cuts

    def mark_in_out_changed(self):
        self.merged_cuts = None

    def real_to_virtual(self, r):
        merged = self.get_merged()
        sub = 0.0
        for s, e in merged:
            if r <= s:
                break
            sub += min(r, e) - s
        return r - sub

    def virtual_to_real(self, v):
        merged = self.get_merged()
        if v <= 0:
            return 0.0
        cum_v = 0.0
        prev_r = 0.0
        for s, e in merged:
            kept = s - prev_r
            if v <= cum_v + kept:
                return prev_r + (v - cum_v)
            cum_v += kept
            prev_r = e
        last_kept = self.length - prev_r
        if v <= cum_v + last_kept:
            return prev_r + (v - cum_v)
        return self.length

    def get_virtual_length(self):
        return self.real_to_virtual(self.length)

    def get_audio_codec(self):
        if not self.media_path:
            return "aac"
        cmd = [
            self.ffprobe_executable,
            "-v",
            "error",
            "-i",
            self._ffmpeg_safe_path(self.media_path),
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "csv=p=0",
        ]
        try:
            output = self._check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore").strip()
            return output if output else "aac"
        except Exception:
            return "aac"

    def _probe_video_properties(self):
        if not self.media_path:
            return None
        cmd = [
            self.ffprobe_executable,
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type,codec_name,width,height,r_frame_rate:stream_tags=rotate:format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=0",
            self._ffmpeg_safe_path(self.media_path),
        ]
        props = {
            "size": "Bilinmiyor",
            "orientation": "Bilinmiyor",
            "rotation": "0 derece",
            "fps": "Bilinmiyor",
            "vcodec": "Bilinmiyor",
            "acodec": "Yok",
            "duration": "Bilinmiyor",
        }
        try:
            output = self._check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore")
        except Exception:
            return props
        current_type = None
        width = None
        height = None
        rotation = 0
        for raw in output.splitlines():
            line = raw.strip()
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key == "codec_type":
                current_type = value
                continue
            if key == "codec_name":
                if current_type == "video" and props["vcodec"] == "Bilinmiyor":
                    props["vcodec"] = value
                elif current_type == "audio" and props["acodec"] in ("Yok", "Bilinmiyor"):
                    props["acodec"] = value
                continue
            if key == "width":
                try:
                    width = int(value)
                except Exception:
                    pass
                continue
            if key == "height":
                try:
                    height = int(value)
                except Exception:
                    pass
                continue
            if key == "TAG:rotate":
                try:
                    rotation = int(float(value))
                except Exception:
                    rotation = 0
                continue
            if key == "r_frame_rate" and props["fps"] == "Bilinmiyor":
                try:
                    if "/" in value:
                        n, d = value.split("/", 1)
                        fps = float(n) / max(float(d), 1.0)
                    else:
                        fps = float(value)
                    props["fps"] = f"{fps:.2f} fps"
                except Exception:
                    pass
                continue
            if key == "duration" and props["duration"] == "Bilinmiyor":
                try:
                    props["duration"] = self.fmt(float(value))
                except Exception:
                    pass
        rotation = ((rotation % 360) + 360) % 360
        props["rotation"] = f"{rotation} derece"
        if width and height:
            props["size"] = f"{width} x {height}"
            shown_w, shown_h = (height, width) if rotation in (90, 270) else (width, height)
            if shown_w > shown_h:
                orientation = "Yatay"
            elif shown_h > shown_w:
                orientation = "Dikey"
            else:
                orientation = "Kare"
            props["orientation"] = f"{orientation} (görüntüleme: {shown_w} x {shown_h})"
        return props

    def show_video_properties_dialog(self):
        if not self.media_path:
            file_speak = self.speech_opts.get("status_file", True)
            self.say("dosya yok", speak=file_speak, update_status=file_speak)
            return
        props = self._probe_video_properties()
        d = VideoPropertiesDialog(self, props or {})
        d.ShowModal()
        d.Destroy()

    def get_video_codec(self):
        if not self.media_path:
            return "h264"
        cmd = [
            self.ffprobe_executable,
            "-v",
            "error",
            "-i",
            self._ffmpeg_safe_path(self.media_path),
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "csv=p=0",
        ]
        try:
            output = self._check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore").strip()
            return output if output else "h264"
        except Exception:
            return "h264"

    def get_video_geometry(self):
        if not self.media_path:
            return None, None, 0
        cmd = [
            self.ffprobe_executable,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:stream_tags=rotate",
            "-of",
            "default=noprint_wrappers=1:nokey=0",
            self._ffmpeg_safe_path(self.media_path),
        ]
        width = None
        height = None
        rotation = 0
        try:
            output = self._check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore")
            for raw in output.splitlines():
                line = raw.strip()
                if not line or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if key == "width":
                    width = int(value)
                elif key == "height":
                    height = int(value)
                elif key == "TAG:rotate":
                    try:
                        rotation = int(float(value))
                    except Exception:
                        rotation = 0
        except Exception:
            return None, None, 0
        rotation = ((rotation % 360) + 360) % 360
        return width, height, rotation

    def _build_transform_filters(self):
        return []

    # ---------- OPTIONS ----------
    def open_options(self):
        d = OptionsDialog(self, self.video_opts, self.audio_opts, self.speech_opts)
        if d.ShowModal() == wx.ID_OK:
            self.video_opts = d.get_video_opts()
            self.audio_opts = d.get_audio_opts()
            self.speech_opts = d.get_speech_opts()
            options_speak = self.speech_opts.get("status_options", True)
            self.say("Seçenekler kaydedildi", speak=options_speak, update_status=options_speak)
            self._mark_project_dirty()
        d.Destroy()

    # ---------- FILE ----------
    def _normalize_project_undo_stack(self, stack):
        normalized = []
        if not isinstance(stack, list):
            return normalized
        for item in stack:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            action, payload = item[0], item[1]
            if action == "cut" and isinstance(payload, (list, tuple)) and len(payload) == 2:
                try:
                    payload = (float(payload[0]), float(payload[1]))
                except Exception:
                    continue
            normalized.append((action, payload))
        return normalized

    def _load_project_file(self, project_path):
        try:
            with open(project_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as ex:
            self._show_error_dialog(f"Proje açılamadı: {ex}", "Hata")
            return False
        if isinstance(data.get("workspaces"), list) and len(data.get("workspaces")) >= 1:
            loaded = []
            for i in range(len(data.get("workspaces", []))):
                ws = data.get("workspaces", [])[i] if i < len(data.get("workspaces", [])) else self._workspace_default()
                if ws.get("media_path") and not os.path.exists(ws.get("media_path")):
                    ws["media_path"] = None
                loaded.append(ws)
            self.workspaces = loaded
            self.workspace_players = [None for _ in loaded]
            self.workspace_loaded_media = [None for _ in loaded]
            self.active_workspace = int(data.get("active_workspace", 0) or 0)
            if self.active_workspace < 0 or self.active_workspace >= len(self.workspaces):
                self.active_workspace = 0
            if isinstance(data.get("speech_opts"), dict):
                self.speech_opts.update(data.get("speech_opts"))
            self._workspace_apply(self.workspaces[self.active_workspace])
            self.project_path = project_path
            self.last_project_dir = os.path.dirname(project_path)
            self.project_dirty = False
            self.project_temp_path = None
            file_speak = self.speech_opts.get("status_file", True)
            self.say("Proje yüklendi", speak=file_speak, update_status=file_speak)
            return True
        media_path = data.get("media_path")
        if not media_path or not os.path.exists(media_path):
            self._show_error_dialog("Projede kayıtlı video bulunamadı.", "Hata")
            return False
        self.source_path = data.get("source_path") or media_path
        self.media_path = media_path
        self.cuts = []
        for c in data.get("cuts", []):
            if isinstance(c, (list, tuple)) and len(c) == 2:
                try:
                    self.cuts.append((float(c[0]), float(c[1])))
                except Exception:
                    pass
        self.undo_stack = self._normalize_project_undo_stack(data.get("undo_stack", []))
        self.mark_in = data.get("mark_in")
        self.mark_out = data.get("mark_out")
        self.last_in = data.get("last_in", self.mark_in)
        self.last_out = data.get("last_out", self.mark_out)
        self.merged_cuts = None
        self.last_cut = self.cuts[-1] if self.cuts else None
        self.audio_codec = None
        self.video_codec = None
        if isinstance(data.get("video_opts"), dict):
            self.video_opts.update(data.get("video_opts"))
        if isinstance(data.get("audio_opts"), dict):
            self.audio_opts.update(data.get("audio_opts"))
        if isinstance(data.get("speech_opts"), dict):
            self.speech_opts.update(data.get("speech_opts"))
        try:
            self.volume = int(data.get("volume", 100))
        except Exception:
            self.volume = 100
        self.volume = max(0, min(300, self.volume))
        self.muted = bool(data.get("muted", False))
        try:
            pos = float(data.get("position", 0.0) or 0.0)
        except Exception:
            pos = 0.0
        self._load_media(self.media_path)
        self.timer.Start(300)
        self._call_later_if_session(80, self.seek_virtual, max(0.0, pos))
        self._call_later_if_session(150, self._apply_player_volume)
        self.project_path = project_path
        self.last_project_dir = os.path.dirname(project_path)
        self.project_dirty = False
        self.project_temp_path = None
        file_speak = self.speech_opts.get("status_file", True)
        self.say("Proje yüklendi", speak=file_speak, update_status=file_speak)
        return True

    def open_file(self):
        default_dir = self.last_open_dir or (os.path.dirname(self.media_path) if self.media_path else "")
        d = wx.FileDialog(
            self,
            "Video Aç",
            defaultDir=default_dir,
            wildcard="Video/Ses/Proje Dosyaları|*.mp4;*.avi;*.mkv;*.mov;*.wmv;*.wav;*.aac;*.mp3;*.m4a;*.flac;*.ogg;*.opus;*.wma;*.bve| Proje Dosyası|*.bve| Tüm Dosyalar|*.*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if d.ShowModal() == wx.ID_OK:
            selected_path = d.GetPath()
            self.last_open_dir = os.path.dirname(selected_path)
            if selected_path.lower().endswith(".bve"):
                self._load_project_file(selected_path)
            else:
                self._save_active_workspace()
                target_idx = self.active_workspace
                if self.workspaces[self.active_workspace].get("media_path"):
                    found_empty = False
                    for i in range(len(self.workspaces)):
                        if not self.workspaces[i].get("media_path"):
                            target_idx = i
                            found_empty = True
                            break
                    if not found_empty:
                        self.workspaces.append(self._workspace_default())
                        self.workspace_players.append(None)
                        self.workspace_loaded_media.append(None)
                        target_idx = len(self.workspaces) - 1
                if target_idx != self.active_workspace:
                    self._switch_workspace(target_idx)
                opened_path = selected_path
                self.source_path = selected_path
                self.media_path = opened_path
                self.mark_in = self.mark_out = None
                self.cuts = []
                self.undo_stack = []
                self.merged_cuts = None
                self.last_cut = None
                self.last_in = None
                self.last_out = None
                self.audio_codec = None
                self.video_codec = None
                ext = os.path.splitext(self.source_path)[1].lstrip(".").lower()
                if ext:
                    self.video_opts["format"] = ext
                self._reset_project_state()
                self._load_media(self.media_path)
                self.timer.Start(300)
                self._save_active_workspace()
                file_speak = self.speech_opts.get("status_file", True)
                self.say(f"Çalışma alanı {self.active_workspace + 1}: {os.path.basename(self.media_path)}", speak=file_speak, update_status=file_speak)
        d.Destroy()

    def close_video(self, skip_confirm=False):
        if not self.media_path:
            file_speak = self.speech_opts.get("status_file", True)
            should_announce = self.status.GetLabel() != "dosya yok"
            self.say("dosya yok", speak=file_speak and should_announce, update_status=should_announce)
            return
        if not skip_confirm and not self._confirm_save_project_before_close():
            return
        self._next_media_session()
        if self.player:
            self.player.command("stop")
            self.player.pause = True
        self.timer.Stop()
        self.media_path = None
        self.source_path = None
        self._ensure_workspace_player_slots()
        if 0 <= self.active_workspace < len(self.workspace_loaded_media):
            self.workspace_loaded_media[self.active_workspace] = None
        self.length = 0.0
        self.cuts.clear()
        self.undo_stack = []
        self.merged_cuts = None
        self.last_cut = None
        self.last_in = None
        self.last_out = None
        self.mark_in = self.mark_out = None
        self.volume = 100
        self.muted = False
        if self.player:
            self.player.mute = False
            self.player.af = ""
        self.audio_codec = None
        self.video_codec = None
        self.video_opts = {"format": "mp4", "codec": "copy", "crf": "23", "preset": "medium"}
        self.audio_opts = {"codec": "copy", "channels": "copy", "sample_rate": "copy", "bit_rate": "copy"}
        self._on_media_closed()
        file_speak = self.speech_opts.get("status_file", True)
        self.say("dosya kapatıldı", speak=file_speak, update_status=file_speak)
        self._reset_project_state()
        self._save_active_workspace()

    def save_file(self):
        if not self.media_path:
            file_speak = self.speech_opts.get("status_file", True)
            self.say("dosya yok", speak=file_speak, update_status=file_speak)
            return
        default_dir = self.last_save_dir or os.path.dirname(self.media_path)
        default_file = os.path.splitext(os.path.basename(self.media_path))[0] + "." + self.video_opts["format"]
        ext = self.video_opts["format"]
        wildcard = f"Video Dosyaları|*.{ext}| Tüm Dosyalar|*.*"
        d = wx.FileDialog(
            self,
            "Farklı Kaydet",
            defaultDir=default_dir,
            defaultFile=default_file,
            wildcard=wildcard,
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )
        if d.ShowModal() == wx.ID_OK:
            out_path = d.GetPath()
            if not out_path.lower().endswith(f".{ext}"):
                out_path += f".{ext}"
            self.last_save_dir = os.path.dirname(out_path)
            progress = ProgressDialog(self, modal=False)
            self._set_active_progress(progress)
            thread = threading.Thread(target=self.apply_cuts_with_progress, args=(out_path, progress))
            thread.start()
            progress.Show()
        d.Destroy()

    def _set_active_progress(self, progress):
        self.active_progress = progress

    def _clear_active_progress(self, progress):
        if self.active_progress is progress:
            self.active_progress = None

    def _video_args_for_cut(self):
        selected_codec = self.video_opts["codec"]
        codec = "libx264" if selected_codec == "copy" else selected_codec
        crf = self.video_opts.get("crf", "23")
        preset = self.video_opts.get("preset", "medium")
        args = ["-c:v", codec]
        if codec in ("libx264", "libx265"):
            args += ["-crf", crf, "-preset", preset]
            return args
        preset_to_cpu = {
            "ultrafast": "8",
            "superfast": "7",
            "veryfast": "6",
            "faster": "5",
            "fast": "4",
            "medium": "3",
            "slow": "2",
            "slower": "1",
            "veryslow": "0",
        }
        cpu_used = preset_to_cpu.get(preset, "3")
        if codec == "libvpx-vp9":
            args += ["-crf", crf, "-b:v", "0", "-deadline", "good", "-cpu-used", cpu_used]
            return args
        if codec == "libaom-av1":
            args += ["-crf", crf, "-b:v", "0", "-cpu-used", cpu_used]
            return args
        return args

    def _build_keep_segments(self):
        merged = self.get_merged()
        parts = []
        last = 0.0
        for a, b in merged:
            if last < a:
                parts.append((last, a))
            last = max(last, b)
        if last < self.length:
            parts.append((last, self.length))
        return parts

    def _build_concat_filters(self, parts, audio_filters, has_audio_input):
        v_filters = []
        a_filters = []
        for i, (start, end) in enumerate(parts):
            v_filters.append(
                f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{i}]"
            )
            if has_audio_input:
                a_filters.append(
                    f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{i}]"
                )
        v_inputs = "".join([f"[v{i}]" for i in range(len(parts))])
        filter_complex = ";".join(v_filters + a_filters)
        if has_audio_input:
            concat_inputs = "".join([f"[v{i}][a{i}]" for i in range(len(parts))])
            filter_complex += f";{concat_inputs}concat=n={len(parts)}:v=1:a=1[v][a]"
            if audio_filters:
                filter_complex += f";[a]{','.join(audio_filters)}[aout]"
                audio_map = "[aout]"
            else:
                audio_map = "[a]"
        else:
            filter_complex += f";{v_inputs}concat=n={len(parts)}:v=1:a=0[v]"
            audio_map = None
        return filter_complex, audio_map

    def _run_ffmpeg_with_progress(self, cmd, progress, message, total_dur=None):
        last_lines = []
        p = None
        try:
            try:
                p = self._popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    encoding="utf-8",
                    errors="replace",
                )
                progress.attach_process(p)
            except FileNotFoundError:
                raise Exception(f"FFmpeg bulunamadı. Denenen: {self.ffmpeg_executable}")
            while p.poll() is None:
                if progress.abort:
                    p.kill()
                    break
                line = p.stdout.readline().strip()
                if line:
                    last_lines.append(line)
                    if len(last_lines) > FFMPEG_ERROR_TAIL_LINES:
                        last_lines.pop(0)
                if total_dur and "time=" in line:
                    time_match = re.search(r"time=(\d+:\d+:\d+\.\d+)", line)
                    if time_match:
                        time_str = time_match.group(1)
                        h, m, s = map(float, time_str.split(":"))
                        current_time = h * 3600 + m * 60 + s
                        percent = int(min(100, (current_time / max(total_dur, 0.001)) * 100))
                        progress.update_progress(percent, f"{message} %{percent}")
                elif not total_dur:
                    progress.update_progress(-1, message)
            if p.returncode != 0 and not progress.abort:
                details = "\n".join(last_lines[-8:]) if last_lines else "Detay yok"
                raise Exception(f"FFmpeg hata verdi:\n{details}")
            if progress.abort:
                raise Exception("İşlem iptal edildi")
        finally:
            progress.clear_process()

    def apply_cuts_with_progress(self, out, progress):
        try:
            input_path = self._ffmpeg_safe_path(self.media_path)
            output_path = self._ffmpeg_safe_path(out)
            if self.audio_codec is None:
                self.audio_codec = self.get_audio_codec()
            if self.video_codec is None:
                self.video_codec = self.get_video_codec()
            parts = self._build_keep_segments()
            has_audio_input = self.has_audio_stream()
            audio_filters, mute_args = self._build_audio_filters()
            video_filters = self._build_transform_filters()
            needs_video_processing = bool(video_filters)
            has_cuts = len(self.cuts) > 0
            needs_audio_processing = bool(audio_filters) or mute_args or (
                self.audio_opts["codec"] != "copy"
                or self.audio_opts["channels"] != "copy"
                or self.audio_opts["sample_rate"] != "copy"
                or self.audio_opts["bit_rate"] != "copy"
            )
            if not has_audio_input:
                mute_args = ["-an"]
                audio_filters = []
            if not has_cuts and not needs_audio_processing and not needs_video_processing and self.video_opts["codec"] == "copy":
                copy_cmd = [self.ffmpeg_executable, "-y", "-i", input_path, "-c", "copy", output_path]
                self._run_ffmpeg_with_progress(copy_cmd, progress, "Kopyalanıyor...")
            else:
                if mute_args:
                    audio_filters = []
                if has_cuts:
                    filter_complex, audio_map = self._build_concat_filters(parts, audio_filters, has_audio_input and not mute_args)
                else:
                    filter_complex = None
                    audio_map = None
                cmd = [self.ffmpeg_executable, "-y", "-i", input_path]
                if filter_complex:
                    cmd += ["-filter_complex", filter_complex, "-map", "[v]"]
                    if audio_map and not mute_args:
                        cmd += ["-map", audio_map]
                if video_filters:
                    if filter_complex:
                        filter_complex += f";[v]{','.join(video_filters)}[vout]"
                        cmd = [self.ffmpeg_executable, "-y", "-i", input_path, "-filter_complex", filter_complex, "-map", "[vout]"]
                        if audio_map and not mute_args:
                            cmd += ["-map", audio_map]
                    else:
                        cmd += ["-vf", ",".join(video_filters)]
                cmd += self._video_args_for_cut()
                if mute_args:
                    cmd += mute_args
                else:
                    if not filter_complex and audio_filters:
                        cmd += ["-af", ",".join(audio_filters)]
                    cmd += self._audio_args(
                        include_filters=not (filter_complex and audio_filters),
                        force_reencode=bool(filter_complex),
                    )
                cmd += ["-movflags", "+faststart", output_path]
                total_dur = self.get_virtual_length()
                if has_cuts:
                    phase_message = "Kesimler uygulanıyor..."
                else:
                    phase_message = "Ses/video ayarları uygulanıyor..."
                self._run_ffmpeg_with_progress(cmd, progress, phase_message, total_dur)
            progress.update_progress(100, "Tamamlandı")
            file_speak = self.speech_opts.get("status_file", True)
            wx.CallAfter(lambda: self.say("Kaydedildi", speak=file_speak, update_status=file_speak))
            wx.CallAfter(self._show_info_dialog, "Video'nuz kaydedildi.", "Bilgi")
        except Exception as ex:
            if str(ex) == "İşlem iptal edildi":
                file_speak = self.speech_opts.get("status_file", True)
                wx.CallAfter(lambda: self.say("İşlem iptal edildi", speak=file_speak, update_status=file_speak))
                return
            error_speak = self.speech_opts.get("status_errors", True)
            wx.CallAfter(lambda: self.say(f"Hata: {str(ex)}", speak=error_speak, update_status=error_speak))
            wx.CallAfter(self._show_error_dialog, f"Hata: {str(ex)}", "Hata")
        finally:
            wx.CallAfter(self._clear_active_progress, progress)
            progress.destroy()

    def _show_info_dialog(self, message, title):
        dlg = wx.MessageDialog(None, message, title, style=wx.OK | wx.ICON_INFORMATION)
        if hasattr(dlg, "SetOKLabel"):
            dlg.SetOKLabel("Tamam")
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def _copy_text_to_clipboard(self, text):
        if not text:
            return False
        if not wx.TheClipboard.Open():
            return False
        try:
            wx.TheClipboard.SetData(wx.TextDataObject(text))
            wx.TheClipboard.Flush()
            return True
        except Exception:
            return False
        finally:
            wx.TheClipboard.Close()

    def _build_detailed_error_report(self, message):
        raw = (message or "").strip()
        text = raw.lower()
        error_code = "E-GENERAL"
        reason = "Beklenmeyen bir hata oluştu."
        suggestions = [
            "Aynı işlemi tekrar deneyin.",
            "Sorun devam ederse hata raporunu kopyalayıp geliştiriciyle paylaşın.",
        ]
        if "ffmpeg bulunamadı" in text or "no such file or directory" in text and "ffmpeg" in text:
            error_code = "E-FFMPEG-NOT-FOUND"
            reason = "FFmpeg yürütülebilir dosyası bulunamadı."
            suggestions = [
                "Program klasöründe bin/ffmpeg.exe ve bin/ffprobe.exe dosyalarının bulunduğunu doğrulayın.",
                "Tek dosya EXE kullanıyorsanız kurulumun eksik kopyalanmadığını kontrol edin.",
                "Gerekirse farklı bir klasöre yeniden çıkarıp tekrar deneyin.",
            ]
        elif "ffmpeg hata verdi" in text:
            error_code = "E-FFMPEG-RUNTIME"
            reason = "FFmpeg işlemi çalıştı ancak dönüştürme sırasında hata oluştu."
            suggestions = [
                "Girdi dosyasının erişilebilir olduğundan ve bozuk olmadığından emin olun.",
                "Çıktı klasörüne yazma izninizin olduğunu kontrol edin.",
                "Farklı bir codec/format seçip tekrar deneyin.",
            ]
        elif "proje açılamadı" in text:
            error_code = "E-PROJECT-OPEN"
            reason = "Proje dosyası okunamadı veya geçersiz içerik barındırıyor."
            suggestions = [
                "Proje dosyasının mevcut ve erişilebilir olduğundan emin olun.",
                "Dosyanın JSON içeriği bozulduysa yedek bir proje dosyası deneyin.",
            ]
        elif "proje kaydedilemedi" in text:
            error_code = "E-PROJECT-SAVE"
            reason = "Proje dosyası belirtilen konuma yazılamadı."
            suggestions = [
                "Hedef klasöre yazma izninizin olduğunu doğrulayın.",
                "Dosya adında geçersiz karakter olmadığını kontrol edin.",
            ]
        elif "işlem iptal edildi" in text:
            error_code = "E-OP-CANCELED"
            reason = "İşlem kullanıcı tarafından iptal edildi."
            suggestions = ["İşlemi yeniden başlatabilirsiniz."]
        report_lines = [
            f"Hata Kodu: {error_code}",
            f"Açıklama: {reason}",
            "",
            "Önerilen Adımlar:",
        ]
        for idx, item in enumerate(suggestions, start=1):
            report_lines.append(f"{idx}. {item}")
        report_lines.extend([
            "",
            "Teknik Detay:",
            raw if raw else "Detay yok",
        ])
        return "\n".join(report_lines)

    def _show_error_dialog(self, message, title):
        detailed_report = self._build_detailed_error_report(message)
        msg = detailed_report + "\n\nHata kodu ayrıntılarıyla panoya kopyalansın mı?"
        ask = wx.MessageDialog(None, msg, title, style=wx.YES_NO | wx.ICON_ERROR)
        if hasattr(ask, "SetYesNoLabels"):
            ask.SetYesNoLabels("Evet", "Hayır")
        if hasattr(ask, "SetEscapeId"):
            ask.SetEscapeId(wx.ID_NO)
        try:
            result = ask.ShowModal()
        finally:
            ask.Destroy()
        if result == wx.ID_YES:
            self._copy_text_to_clipboard(detailed_report)

    # ---------- SES ARGS (eski _effective_player_volume çağrılarını temizledik) ----------
    def _select_audio_codec(self, needs_reencode):
        if not needs_reencode:
            return "copy"
        if self.audio_opts["codec"] != "copy":
            return self.audio_opts["codec"]
        return self.get_audio_codec()

    def has_audio_stream(self):
        if not self.media_path:
            return False
        cmd = [
            self.ffprobe_executable,
            "-v",
            "error",
            "-i",
            self._ffmpeg_safe_path(self.media_path),
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
        ]
        try:
            output = self._check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore").strip()
            return bool(output)
        except Exception:
            return False

    def _audio_args(self, include_filters=True, force_reencode=False):
        filters, mute_args = self._build_audio_filters()
        if mute_args:
            return mute_args
        needs_reencode = (
            force_reencode
            or
            self.audio_opts["codec"] != "copy"
            or self.audio_opts["channels"] != "copy"
            or self.audio_opts["sample_rate"] != "copy"
            or self.audio_opts["bit_rate"] != "copy"
            or filters
        )
        args = []
        if include_filters and filters:
            args += ["-af", ",".join(filters)]
        args += ["-c:a", self._select_audio_codec(needs_reencode)]
        if self.audio_opts["channels"] != "copy":
            ch = "1" if self.audio_opts["channels"] == "mono" else "2"
            args += ["-ac", ch]
        if self.audio_opts["sample_rate"] != "copy":
            args += ["-ar", self.audio_opts["sample_rate"]]
        if self.audio_opts["bit_rate"] != "copy":
            args += ["-b:a", self.audio_opts["bit_rate"]]
        return args

# ================= RUN =================
class App(wx.App):
    def OnInit(self):
        self.frame = Editor()
        return True

if __name__ == "__main__":
    App(False).MainLoop()