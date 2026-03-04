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
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif", ".tif", ".tiff"}

LANGUAGES = [
    ("tr", "Türkçe / Turkish"),
    ("en", "English / İngilizce"),
    ("de", "German / Almanca"),
    ("es", "Spanish / İspanyolca"),
    ("pt", "Portuguese / Portekizce"),
    ("ar", "Arabic / Arapça"),
    ("fr", "French / Fransızca"),
    ("nl", "Dutch / Felemenkçe"),
    ("it", "Italian / İtalyanca"),
]

def _default_dir_path():
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.isdir(desktop):
        return desktop
    return os.path.expanduser("~")

def _settings_path():
    return os.path.join(os.path.expanduser("~"), ".bve_settings.dat")

def _default_settings():
    default_dir = _default_dir_path()
    return {
        "language": None,
        "last_open_dir": default_dir,
        "last_save_dir": default_dir,
        "last_project_dir": default_dir,
        "video_opts": {"format": "mp4", "codec": "copy", "crf": "23", "preset": "medium"},
        "audio_opts": {"codec": "copy", "channels": "copy", "sample_rate": "copy", "bit_rate": "copy", "output_ext": "m4a"},
        "speech_opts": {
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
        },
    }

def _load_settings():
    settings = _default_settings()
    path = _settings_path()
    if not os.path.exists(path):
        return settings
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            settings.update({k: v for k, v in data.items() if k in settings})
            for key in ("video_opts", "audio_opts", "speech_opts"):
                if isinstance(data.get(key), dict):
                    merged = dict(settings[key])
                    merged.update(data[key])
                    settings[key] = merged
    except Exception:
        return settings
    return settings

def _save_settings(settings):
    path = _settings_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _lang_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "lang")

def _load_lang_pack(lang_code):
    code = (lang_code or "tr").lower()
    def _load_single(c):
        fp = os.path.join(_lang_dir(), f"{c}.json")
        if not os.path.exists(fp):
            return {}
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            return {}
        return {}

    en = _load_single("en")
    tr = _load_single("tr")
    target = _load_single(code)

    # Temel yaklaşım:
    # 1) en + tr fallback (varsayılan metin asla boş kalmasın)
    # 2) seçilen dilde, value İngilizce ile birebir aynıysa bunu "placeholder"
    #    kabul edip override etmeyiz. Böylece yarım kalmış paketlerde ekranda
    #    yanlışlıkla toplu İngilizce görünmez.
    packs = {}
    packs.update(en)
    packs.update(tr)
    for k, v in target.items():
        if code not in ("tr", "en") and isinstance(v, str) and isinstance(en.get(k), str) and v == en.get(k):
            continue
        packs[k] = v
    return packs



def tr_value(lang_pack, key, default):
    try:
        if isinstance(lang_pack, dict) and key in lang_pack:
            return str(lang_pack.get(key) or "")
    except Exception:
        pass
    return default

def dialog_labels(lang_pack):
    return {
        "ok": tr_value(lang_pack, "btn_ok", "Tamam"),
        "cancel": tr_value(lang_pack, "btn_cancel", "İptal"),
        "yes": tr_value(lang_pack, "btn_yes", "Evet"),
        "no": tr_value(lang_pack, "btn_no", "Hayır"),
        "info": tr_value(lang_pack, "title_info", "Bilgi"),
        "error": tr_value(lang_pack, "title_error", "Hata"),
        "confirm": tr_value(lang_pack, "title_confirm", "Onay"),
    }

class LanguageSelectDialog(wx.Dialog):
    def __init__(self, parent=None, initial_language=None):
        lp = _load_lang_pack(initial_language or "tr")
        super().__init__(parent, title=tr_value(lp, "lang_select_title", "Lütfen Dil Seçin / Please Select Language"))
        v = wx.BoxSizer(wx.VERTICAL)
        v.Add(wx.StaticText(self, label=tr_value(lp, "lang_select_prompt", "Lütfen dil seçin / Please select language")), 0, wx.ALL, 8)
        labels = [label for _code, label in LANGUAGES]
        self.choice = wx.Choice(self, choices=labels)
        sel = 0
        if initial_language:
            for i, (code, _label) in enumerate(LANGUAGES):
                if code == str(initial_language).lower():
                    sel = i
                    break
        self.choice.SetSelection(sel)
        v.Add(self.choice, 0, wx.ALL | wx.EXPAND, 8)
        h = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(self, wx.ID_OK, tr_value(lp, "btn_ok", "Tamam"))
        cancel_btn = wx.Button(self, wx.ID_CANCEL, tr_value(lp, "btn_cancel", "İptal"))
        h.Add(ok_btn, 0, wx.ALL, 8)
        h.Add(cancel_btn, 0, wx.ALL, 8)
        v.Add(h, 0, wx.ALIGN_CENTER)
        self.SetSizerAndFit(v)
        self.choice.SetFocus()

    def selected_language(self):
        idx = self.choice.GetSelection()
        if idx == wx.NOT_FOUND:
            return "tr"
        return LANGUAGES[idx][0]

# ================= ZAMANA GİT =================
class GoToTimeDialog(wx.Dialog):
    def __init__(self, parent, cur):
        super().__init__(parent, title=parent.tr("dlg_goto_title", "Zamana Git"))
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
        ok_btn = wx.Button(self, wx.ID_OK, parent.ui_labels.get("ok", "Tamam"))
        cancel_btn = wx.Button(self, wx.ID_CANCEL, parent.ui_labels.get("cancel", "İptal"))
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


class ImageToVideoDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title=parent.tr("dlg_img2video_title", "Fotoğrafı Video'ya Çevir"))
        v = wx.BoxSizer(wx.VERTICAL)
        v.Add(wx.StaticText(self, label=parent.tr("dlg_img2video_duration", "Video süresini saniye cinsinden giriniz:")), 0, wx.ALL, 6)
        self.duration = wx.TextCtrl(self, value="5")
        self.duration.SetName("Süre (saniye)")
        v.Add(self.duration, 0, wx.ALL | wx.EXPAND, 6)
        self.rotate = wx.CheckBox(self, label=parent.tr("dlg_img2video_rotate", "Dönüştürülen video'ya rotay ekle"))
        self.rotate.SetValue(False)
        v.Add(self.rotate, 0, wx.ALL, 6)
        h = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(self, wx.ID_OK, parent.ui_labels.get("ok", "Tamam"))
        cancel_btn = wx.Button(self, wx.ID_CANCEL, parent.ui_labels.get("cancel", "İptal"))
        h.Add(ok_btn, 0, wx.ALL, 8)
        h.Add(cancel_btn, 0, wx.ALL, 8)
        v.Add(h, 0, wx.ALIGN_CENTER)
        self.SetSizerAndFit(v)
        self.duration.SetFocus()

    def get_values(self):
        try:
            sec = float((self.duration.GetValue() or "0").strip())
        except Exception:
            sec = 0.0
        sec = max(1.0, min(24 * 3600.0, sec))
        return sec, bool(self.rotate.IsChecked())

class OrientationDialog(wx.Dialog):
    def __init__(self, parent, is_portrait):
        super().__init__(parent, title=parent.tr("dlg_orient_title", "Boyutlandırma"))
        v = wx.BoxSizer(wx.VERTICAL)
        self.to_portrait = wx.Button(self, wx.ID_ANY, parent.tr("dlg_orient_to_portrait", "Dikey'e döndür"))
        self.to_landscape = wx.Button(self, wx.ID_ANY, parent.tr("dlg_orient_to_landscape", "Yatay'a döndür"))
        self.force_portrait = wx.Button(self, wx.ID_ANY, parent.tr("dlg_orient_force_portrait", "Zorla Dikey Kabul Et"))
        self.force_landscape = wx.Button(self, wx.ID_ANY, parent.tr("dlg_orient_force_landscape", "Zorla Yatay Kabul Et"))
        self.force_auto = wx.Button(self, wx.ID_ANY, parent.tr("dlg_orient_force_auto", "Otomatik Algıya Dön"))
        self.cancel_btn = wx.Button(self, wx.ID_CANCEL, parent.ui_labels.get("cancel", "İptal"))
        if is_portrait is None:
            self.to_portrait.Enable(True)
            self.to_landscape.Enable(True)
        else:
            self.to_portrait.Enable(not is_portrait)
            self.to_landscape.Enable(is_portrait)
        for btn in (self.to_portrait, self.to_landscape, self.force_portrait, self.force_landscape, self.force_auto, self.cancel_btn):
            v.Add(btn, 0, wx.ALL | wx.EXPAND, 6)
        self.SetSizerAndFit(v)
        self.choice = None
        self.to_portrait.Bind(wx.EVT_BUTTON, self._on_portrait)
        self.to_landscape.Bind(wx.EVT_BUTTON, self._on_landscape)
        self.force_portrait.Bind(wx.EVT_BUTTON, self._on_force_portrait)
        self.force_landscape.Bind(wx.EVT_BUTTON, self._on_force_landscape)
        self.force_auto.Bind(wx.EVT_BUTTON, self._on_force_auto)
        (self.to_portrait if self.to_portrait.IsEnabled() else self.to_landscape).SetFocus()

    def _on_portrait(self, _):
        self.choice = "portrait"
        self.EndModal(wx.ID_OK)

    def _on_landscape(self, _):
        self.choice = "landscape"
        self.EndModal(wx.ID_OK)

    def _on_force_portrait(self, _):
        self.choice = "force_portrait"
        self.EndModal(wx.ID_OK)

    def _on_force_landscape(self, _):
        self.choice = "force_landscape"
        self.EndModal(wx.ID_OK)

    def _on_force_auto(self, _):
        self.choice = "force_auto"
        self.EndModal(wx.ID_OK)

# ================= İLETİŞİM VE YARDIM DİALOGALARI =================
class ContactDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title=parent.tr("dlg_contact_title", "İletişim"))
        v = wx.BoxSizer(wx.VERTICAL)
        self.email_btn = wx.Button(self, label="E-posta")
        self.whatsapp_btn = wx.Button(self, label="Whatsapp")
        self.instagram_btn = wx.Button(self, label="Instagram")
        self.close_btn = wx.Button(self, wx.ID_CANCEL, parent.ui_labels.get("cancel", "Kapat"))
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
        super().__init__(parent, title=parent.tr("menu_title", "B.V.E Menüsü"))
        self.parent = parent
        v = wx.BoxSizer(wx.VERTICAL)
        self.help_btn = wx.Button(self, label=parent.tr("menu_about", "B.V.e Hakkında"))
        self.shortcuts_btn = wx.Button(self, label=parent.tr("menu_shortcuts", "Kısa yol tuşları"))
        self.update_btn = wx.Button(self, label=parent.tr("menu_check_update", "Güncel Sürümü Denetle"))
        self.contact_btn = wx.Button(self, label=parent.tr("menu_contact", "İletişim"))
        self.close_btn = wx.Button(self, wx.ID_CANCEL, parent.tr("menu_back", "Geri"))
        for btn in (
            self.help_btn,
            self.shortcuts_btn,
            self.update_btn,
            self.contact_btn,
            self.close_btn,
        ):
            v.Add(btn, 0, wx.ALL | wx.EXPAND, 6)
        self.SetSizerAndFit(v)
        self.help_btn.Bind(wx.EVT_BUTTON, self.on_help)
        self.shortcuts_btn.Bind(wx.EVT_BUTTON, self.on_shortcuts)
        self.update_btn.Bind(wx.EVT_BUTTON, self.on_check_update)
        self.contact_btn.Bind(wx.EVT_BUTTON, self.on_contact)
        self.help_btn.SetFocus()

    def on_help(self, _):
        self.parent._open_text_help(self.parent._localized_doc_filename("about"), "Hakkında / About")

    def on_shortcuts(self, _):
        self.parent._open_text_help(self.parent._localized_doc_filename("shortcuts"), "Kısa Yol Tuşları / Shortcuts")

    def on_check_update(self, _):
        wx.LaunchDefaultBrowser("https://disk.yandex.com/d/3g6doo21r3BOyg")

    def on_contact(self, _):
        d = ContactDialog(self)
        try:
            d.ShowModal()
        finally:
            d.Destroy()

# ================= VİDEO ÖZELLİKLERİ =================
class VideoPropertiesDialog(wx.Dialog):
    def __init__(self, parent, original_props, current_props):
        self.parent = parent
        super().__init__(parent, title=parent.tr("dlg_props_title", "Video Özellikleri"))
        v = wx.BoxSizer(wx.VERTICAL)
        self._copy_text = (
            f"{parent.tr_text('Orijinal Özellikler')}\n"
            + self._format_props_block(original_props)
            + "\n\n"
            + f"{parent.tr_text('Mevcut Özellikler')}\n"
            + self._format_props_block(current_props)
        )

        nb = wx.Notebook(self)
        self.original_text = self._build_props_page(nb, original_props)
        self.current_text = self._build_props_page(nb, current_props)
        nb.AddPage(self.original_text.GetParent(), parent.tr("tab_original", "Orijinal"))
        nb.AddPage(self.current_text.GetParent(), parent.tr("tab_current", "Mevcut"))
        v.Add(nb, 1, wx.ALL | wx.EXPAND, 8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        copy_btn = wx.Button(self, wx.ID_ANY, parent.tr("btn_copy_video_props", "Tüm video özelliklerini kopyala"))
        warn_btn = wx.Button(self, wx.ID_ANY, parent.tr("btn_important_warning", "Önemli Uyarı"))
        ok_btn = wx.Button(self, wx.ID_OK, parent.ui_labels.get("ok", "Tamam"))
        cancel_btn = wx.Button(self, wx.ID_CANCEL, parent.ui_labels.get("cancel", "İptal"))
        btns.Add(copy_btn, 0, wx.ALL, 8)
        btns.Add(warn_btn, 0, wx.ALL, 8)
        btns.Add(ok_btn, 0, wx.ALL, 8)
        btns.Add(cancel_btn, 0, wx.ALL, 8)
        v.Add(btns, 0, wx.ALIGN_CENTER)
        self.SetSizerAndFit(v)
        copy_btn.Bind(wx.EVT_BUTTON, self.on_copy_all)
        warn_btn.Bind(wx.EVT_BUTTON, self.on_warning)
        self.original_text.SetFocus()

    def _build_props_page(self, parent, props):
        panel = wx.Panel(parent)
        s = wx.BoxSizer(wx.VERTICAL)
        t = wx.TextCtrl(panel, value=self._format_props_block(props), style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        s.Add(t, 1, wx.ALL | wx.EXPAND, 6)
        panel.SetSizer(s)
        return t

    def _format_props_block(self, props):
        return "\n".join([
            f"Boyut: {props.get('size', 'Bilinmiyor')}",
            f"Görüntü Yönü: {props.get('orientation', 'Bilinmiyor')}",
            f"Dönüş Bilgisi: {props.get('rotation', 'Bilinmiyor')}",
            f"Kare Hızı: {props.get('fps', 'Bilinmiyor')}",
            f"Video Kodeği: {props.get('vcodec', 'Bilinmiyor')}",
            f"Ses Kodeği: {props.get('acodec', 'Bilinmiyor')}",
            f"Orijinal Süre: {props.get('duration', 'Bilinmiyor')}",
            f"Mevcut Toplam Süre: {props.get('current_duration', 'Bilinmiyor')}",
        ])

    def on_copy_all(self, _):
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(self._copy_text))
                wx.TheClipboard.Flush()
            finally:
                wx.TheClipboard.Close()

    def on_warning(self, _):
        msg = (
            "Bu program, en doğru video özelliklerini sunmayı amaçlasa da bazı videolardaki metadata hataları nedeniyle "
            "dikey videoyu yatay, yatay videoyu dikey algılayabilir. Bu nedenle, üzerinde çalıştığınız videonun en-boy "
            "oranını (yönelimini) teyit etmeniz önerilir."
        )
        d = wx.MessageDialog(self, msg, self.parent.tr("title_warning_dialog", "Uyarı iletişim kutusu"), wx.OK | wx.ICON_WARNING)
        try:
            if hasattr(d, "SetOKLabel"):
                d.SetOKLabel(self.parent.ui_labels.get("ok", "Tamam"))
            d.ShowModal()
        finally:
            d.Destroy()

# ================= YARDIM METNİ =================
class HelpTextDialog(wx.Dialog):
    def __init__(self, parent, title, text):
        super().__init__(parent, title=title, size=(760, 560))
        v = wx.BoxSizer(wx.VERTICAL)
        self.text = wx.TextCtrl(self, value=text, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        close_btn = wx.Button(self, wx.ID_OK, parent.ui_labels.get("ok", "Tamam") if hasattr(parent, "ui_labels") else "Tamam")
        v.Add(self.text, 1, wx.ALL | wx.EXPAND, 8)
        v.Add(close_btn, 0, wx.ALL | wx.ALIGN_CENTER, 8)
        self.SetSizer(v)
        self.text.SetFocus()

# ================= SEÇENEKLER =================
class OptionsDialog(wx.Dialog):
    def __init__(self, parent, video_opts, audio_opts, speech_opts):
        self.parent = parent
        super().__init__(parent, title=parent.tr("dlg_options_title", "Seçenekler"))
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
        notebook.AddPage(video_page, parent.tr("tab_video_options", "Video Seçenekleri"))
        notebook.AddPage(audio_page, parent.tr("tab_audio_options", "Ses Seçenekleri"))
        notebook.AddPage(speech_page, parent.tr("tab_speech", "Metin Okuma"))
        v = wx.BoxSizer(wx.VERTICAL)
        v.Add(notebook, 1, wx.EXPAND | wx.ALL, 4)
        hbox = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(self, wx.ID_OK, parent.ui_labels.get("ok", "Tamam"))
        cancel_btn = wx.Button(self, wx.ID_CANCEL, parent.ui_labels.get("cancel", "İptal"))
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
        t = self.parent.tr_text
        def select_or_default(ctrl, index):
            if index is None or index < 0 or index >= ctrl.GetCount():
                index = 0
            ctrl.SetSelection(index)
            if ctrl.GetSelection() == wx.NOT_FOUND and ctrl.GetCount() > 0:
                ctrl.SetSelection(0)
        def add_labeled_choice(label, items, name):
            v.Add(wx.StaticText(page, label=t(label)), 0, wx.ALL, 4)
            ctrl = wx.Choice(page)
            ctrl.SetItems([t(i) for i in items])
            ctrl.SetName(t(name))
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
        t = self.parent.tr_text
        def select_or_default(ctrl, index):
            if index is None or index < 0 or index >= ctrl.GetCount():
                index = 0
            ctrl.SetSelection(index)
            if ctrl.GetSelection() == wx.NOT_FOUND and ctrl.GetCount() > 0:
                ctrl.SetSelection(0)
        def add_labeled_choice(label, items, name):
            v.Add(wx.StaticText(page, label=t(label)), 0, wx.ALL, 4)
            ctrl = wx.Choice(page)
            ctrl.SetItems([t(i) for i in items])
            ctrl.SetName(t(name))
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
        self.audio_ext_display = ["M4A", "MP3", "WAV", "AAC", "FLAC", "OGG", "OPUS", "WMA"]
        self.audio_ext_values = ["m4a", "mp3", "wav", "aac", "flac", "ogg", "opus", "wma"]
        self.audio_ext = add_labeled_choice("Ses Kaydetme Uzantısı:", self.audio_ext_display, "Ses Uzantısı")
        try:
            idx = self.audio_ext_values.index(self.audio_opts.get("output_ext", "m4a"))
        except ValueError:
            idx = 0
        select_or_default(self.audio_ext, idx)
        self.channels.MoveAfterInTabOrder(self.audio_codec)
        self.sample_rate.MoveAfterInTabOrder(self.channels)
        self.bit_rate.MoveAfterInTabOrder(self.sample_rate)
        self.audio_ext.MoveAfterInTabOrder(self.bit_rate)
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
            cb = wx.CheckBox(page, label=self.parent.tr_text(label))
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
            "output_ext": self.audio_ext_values[self.audio_ext.GetSelection()],
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
        self.cancel_btn = wx.Button(self, wx.ID_CANCEL, parent.ui_labels.get("cancel", "İptal"))
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

class SaveAnalysisDialog(wx.Dialog):
    def __init__(self, parent, analysis_text):
        super().__init__(parent, title="Kayıt Öncesi Analiz")
        v = wx.BoxSizer(wx.VERTICAL)
        v.Add(wx.StaticText(self, label="Kayıt öncesi analiz sonucu:"), 0, wx.ALL, 8)
        self.summary = wx.TextCtrl(self, value=analysis_text, style=wx.TE_MULTILINE | wx.TE_READONLY)
        v.Add(self.summary, 1, wx.ALL | wx.EXPAND, 8)
        self.current_btn = wx.Button(self, wx.ID_OK, "Mevcut düzende kaydet")
        self.safe_btn = wx.Button(self, wx.ID_APPLY, "Güvenli çözüm: gerekli yerde re-encode")
        self.cancel_btn = wx.Button(self, wx.ID_CANCEL, parent.ui_labels.get("cancel", "İptal"))
        v.Add(self.current_btn, 0, wx.ALL | wx.EXPAND, 6)
        v.Add(self.safe_btn, 0, wx.ALL | wx.EXPAND, 6)
        v.Add(self.cancel_btn, 0, wx.ALL | wx.EXPAND, 6)
        self.SetSizerAndFit(v)
        self.current_btn.Bind(wx.EVT_BUTTON, lambda evt: self.EndModal(wx.ID_OK))
        self.safe_btn.Bind(wx.EVT_BUTTON, lambda evt: self.EndModal(wx.ID_APPLY))
        self.summary.SetFocus()


class EffectSelectDialog(wx.Dialog):
    def __init__(self, parent, plugin_name, effects, source_label=""):
        super().__init__(parent, title=f"Efektler - {plugin_name}")
        self.effects = list(effects or [])
        self.previewing = False
        self.preview_start_cb = None
        self.preview_stop_cb = None
        v = wx.BoxSizer(wx.VERTICAL)
        v.Add(wx.StaticText(self, label="Efekt seçin:"), 0, wx.ALL, 8)
        if source_label:
            v.Add(wx.StaticText(self, label=f"Kaynak: {source_label}"), 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.listbox = wx.ListBox(self, choices=[e["name"] for e in self.effects], style=wx.LB_SINGLE)
        v.Add(self.listbox, 1, wx.ALL | wx.EXPAND, 8)
        h = wx.BoxSizer(wx.HORIZONTAL)
        self.preview_btn = wx.Button(self, wx.ID_ANY, "Önizle")
        self.ok_btn = wx.Button(self, wx.ID_OK, parent.ui_labels.get("ok", "Tamam"))
        self.back_btn = wx.Button(self, wx.ID_BACKWARD, "Geri")
        self.cancel_btn = wx.Button(self, wx.ID_CANCEL, parent.ui_labels.get("cancel", "İptal"))
        for b in (self.preview_btn, self.back_btn, self.ok_btn, self.cancel_btn):
            h.Add(b, 0, wx.ALL, 6)
        v.Add(h, 0, wx.ALIGN_RIGHT)
        self.SetSizerAndFit(v)
        if self.effects:
            self.listbox.SetSelection(0)
        self.preview_btn.Bind(wx.EVT_BUTTON, self.on_preview)
        self.back_btn.Bind(wx.EVT_BUTTON, lambda evt: self.EndModal(wx.ID_BACKWARD))
        self.listbox.Bind(wx.EVT_CHAR_HOOK, self.on_list_key)
        self.listbox.Bind(wx.EVT_LISTBOX, self.on_selection_changed)

    def on_list_key(self, e):
        k = e.GetKeyCode()
        if k == wx.WXK_TAB:
            self.preview_btn.SetFocus()
            return
        # Savihost benzeri gezinme: yukarı=önceki, aşağı=sonraki
        if k in (wx.WXK_UP, wx.WXK_DOWN):
            sel = self.listbox.GetSelection()
            if sel == wx.NOT_FOUND:
                sel = 0
            if k == wx.WXK_UP:
                sel = max(0, sel - 1)
            else:
                sel = min(max(0, self.listbox.GetCount() - 1), sel + 1)
            self.listbox.SetSelection(sel)
            self.on_selection_changed(None)
            return
        e.Skip()

    def on_selection_changed(self, _evt):
        if self.previewing and callable(self.preview_stop_cb):
            self.preview_stop_cb()
            idx = self.listbox.GetSelection()
            if idx != wx.NOT_FOUND and callable(self.preview_start_cb):
                self.preview_start_cb(self.effects[idx])

    def on_preview(self, _evt):
        idx = self.listbox.GetSelection()
        if idx == wx.NOT_FOUND:
            return
        effect = self.effects[idx]
        self.previewing = not self.previewing
        if self.previewing:
            if callable(self.preview_start_cb):
                self.preview_start_cb(effect)
            self.preview_btn.SetLabel("Durdur")
        else:
            if callable(self.preview_stop_cb):
                self.preview_stop_cb()
            self.preview_btn.SetLabel("Önizle")

    def bind_preview_callbacks(self, on_start, on_stop):
        self.preview_start_cb = on_start
        self.preview_stop_cb = on_stop

    def selected_effect(self):
        idx = self.listbox.GetSelection()
        if idx == wx.NOT_FOUND:
            return None
        return self.effects[idx]

# ================= ANA EDITÖR =================
class Editor(wx.Frame):
    def __init__(self, app_settings=None):
        super().__init__(None, title="BlindVideoEditor", size=(900, 550))
        self.player = None
        self.source_path = None
        self.original_media_path = None
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
        self.orientation_mode = None
        self.orientation_override = None
        self.current_video_rotation = 0
        self.video_opts = {"format": "mp4", "codec": "copy", "crf": "23", "preset": "medium"}
        self.audio_opts = {"codec": "copy", "channels": "copy", "sample_rate": "copy", "bit_rate": "copy", "output_ext": "m4a"}
        self.project_dirty = False
        self.project_path = None
        self.project_temp_path = None
        self.app_settings = app_settings or _default_settings()
        self.language = (self.app_settings.get("language") or "tr").lower()
        self.lang_pack = _load_lang_pack(self.language)
        self.ui_labels = dialog_labels(self.lang_pack)
        self.ui_labels = dialog_labels(self.lang_pack)
        default_dir = _default_dir_path()
        self.last_open_dir = self.app_settings.get("last_open_dir") or default_dir
        self.last_save_dir = self.app_settings.get("last_save_dir") or default_dir
        self.last_project_dir = self.app_settings.get("last_project_dir") or default_dir
        self.active_workspace = 0
        self.workspaces = [None]
        self.adapted_temp_files = set()
        self.segment_clipboard = None
        self._media_session = 0
        self.media_analysis_cache = {}
        self._effect_preview_prev_loop = None
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
        if isinstance(self.app_settings.get("video_opts"), dict):
            self.video_opts.update(self.app_settings.get("video_opts"))
        if isinstance(self.app_settings.get("audio_opts"), dict):
            self.audio_opts.update(self.app_settings.get("audio_opts"))
        if isinstance(self.app_settings.get("speech_opts"), dict):
            self.speech_opts.update(self.app_settings.get("speech_opts"))
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

    def tr(self, key, default=None):
        if key in self.lang_pack:
            return str(self.lang_pack.get(key) or "")
        return default if default is not None else key

    def tr_text(self, text):
        try:
            if isinstance(text, str) and text in self.lang_pack:
                return str(self.lang_pack.get(text) or text)
        except Exception:
            pass
        return text

    # ---------- UI ----------
    def build_ui(self):
        p = wx.Panel(self)
        self.video_panel = wx.Panel(p)
        self.video_panel.SetBackgroundColour("BLACK")
        self.video_panel.SetCanFocus(False)
        self.video_panel.Show(False)
        self.status = wx.StaticText(p, label=self.tr("welcome_status", "Hoş Geldiniz, yardım için f bir tuşuna basın"))
        self.visual_status_enabled = True
        self.last_status_message = self.tr("welcome_status", "Hoş Geldiniz, yardım için f bir tuşuna basın")
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
            "original_media_path": None,
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
            "audio_opts": {"codec": "copy", "channels": "copy", "sample_rate": "copy", "bit_rate": "copy", "output_ext": "m4a"},
            "project_dirty": False,
            "project_path": None,
            "project_temp_path": None,
            "position": 0.0,
            "orientation_mode": None,
            "orientation_override": None,
            "current_video_rotation": 0,
        }

    def _workspace_snapshot(self):
        return {
            "media_kind": self._detect_media_kind(self.source_path or self.media_path),
            "source_path": self.source_path,
            "original_media_path": self.original_media_path,
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
            "orientation_mode": self.orientation_mode,
            "orientation_override": self.orientation_override,
            "current_video_rotation": int(self.current_video_rotation or 0),
        }

    def _workspace_apply(self, ws):
        ws = deepcopy(ws or self._workspace_default())
        self.source_path = ws.get("source_path")
        self.original_media_path = ws.get("original_media_path") or self.source_path or self.media_path
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
        self.audio_opts = deepcopy(ws.get("audio_opts") or {"codec": "copy", "channels": "copy", "sample_rate": "copy", "bit_rate": "copy", "output_ext": "m4a"})
        self.project_dirty = bool(ws.get("project_dirty", False))
        self.project_path = ws.get("project_path")
        self.project_temp_path = ws.get("project_temp_path")
        self.orientation_mode = ws.get("orientation_mode")
        self.orientation_override = ws.get("orientation_override")
        self.current_video_rotation = int(ws.get("current_video_rotation", 0) or 0)
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
        if had_media and len(self.workspaces) > 1:
            self._remove_active_workspace()

    def _close_all_workspaces(self):
        has_any_media = any((ws or {}).get("media_path") for ws in self.workspaces)
        if not has_any_media:
            self._reset_to_single_default_workspace()
            return
        if self.project_dirty:
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
        self._reset_to_single_default_workspace()
        file_speak = self.speech_opts.get("status_file", True)
        self.say("tüm dosyalar kapatıldı", speak=file_speak, update_status=file_speak)

    def _init_workspaces(self):
        self.workspaces = [self._workspace_snapshot()]
        self.active_workspace = 0
        self.workspace_players = [self.player]
        self.workspace_loaded_media = [self.media_path]

    def _reset_to_single_default_workspace(self):
        current_player = self.player
        self.workspaces = [self._workspace_default()]
        self.active_workspace = 0
        self.workspace_players = [current_player]
        self.workspace_loaded_media = [None]
        self._workspace_apply(self.workspaces[0])

    def _build_media_from_clipboard(self):
        if not self.segment_clipboard or not self.segment_clipboard.get("path"):
            raise Exception("Yapıştırılacak kopya yok")
        src_path = self.segment_clipboard.get("path")
        if not src_path or not os.path.exists(src_path):
            raise Exception("Kopyalanan kaynak bulunamadı")
        segments = self.segment_clipboard.get("segments") or [
            (float(self.segment_clipboard.get("start", 0.0) or 0.0), float(self.segment_clipboard.get("end", 0.0) or 0.0))
        ]
        locked_real = self.segment_clipboard.get("segments_locked_real") or []
        if locked_real:
            segments = [(float(a), float(b)) for (a, b) in locked_real if float(b) - float(a) > 0.001]
        snap_cuts = self.segment_clipboard.get("cuts_snapshot") or []
        snap_len = float(self.segment_clipboard.get("length_snapshot") or 0.0)
        virtual_segments = self.segment_clipboard.get("virtual_segments") or []
        if not segments and virtual_segments and snap_len > 0.0:
            rebuilt = []
            for va, vb in virtual_segments:
                ra = self._virtual_to_real_with_cuts(va, snap_cuts, snap_len)
                rb = self._virtual_to_real_with_cuts(vb, snap_cuts, snap_len)
                if rb - ra > 0.001:
                    rebuilt.append((ra, rb))
            if rebuilt:
                segments = rebuilt
        valid_segments = [(float(a), float(b)) for a, b in segments if float(b) - float(a) > 0.001]
        if not valid_segments:
            raise Exception("Yapıştırılacak geçerli bölüm yok")
        media_kind = self.segment_clipboard.get("media_kind", "video")
        suffix = ".mp4" if media_kind == "video" else ".m4a"
        fd_out, out_path = self._temp_media_path("edit", suffix, src_path)
        os.close(fd_out)
        cleanup_files = []
        try:
            if media_kind == "video":
                part_files = []
                for seg_start, seg_end in valid_segments:
                    fd_seg, seg_path = self._temp_media_path("part_copy", ".mp4", src_path)
                    os.close(fd_seg)
                    cleanup_files.append(seg_path)
                    self._extract_copy_segment(src_path, seg_start, seg_end, seg_path)
                    part_files.append(seg_path)
                if len(part_files) == 1:
                    shutil.copyfile(part_files[0], out_path)
                else:
                    self._concat_files_copy(part_files, out_path)
            else:
                parts = []
                for seg_start, seg_end in valid_segments:
                    fd_seg, seg_path = self._temp_media_path("part", suffix, src_path)
                    os.close(fd_seg)
                    cleanup_files.append(seg_path)
                    self._extract_audio_aac_segment(src_path, seg_start, seg_end, seg_path)
                    parts.append(seg_path)
                if len(parts) == 1:
                    shutil.copyfile(parts[0], out_path)
                else:
                    self._concat_files_resilient(parts, out_path)
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

    def _resolve_stream_rotation(self, stream, width=0, height=0):
        """Rotate bilgisini stream tags + side_data'dan güvenli şekilde seçer."""
        tags = (stream or {}).get("tags") or {}
        tag_rotation = None
        try:
            val = int(float(tags.get("rotate", 0) or 0))
            if val != 0:
                tag_rotation = val
        except Exception:
            pass

        side_rotation = None
        for side in ((stream or {}).get("side_data_list") or (stream or {}).get("side_data") or []):
            if "rotation" not in side:
                # Bazı ffprobe sürümlerinde rotation ayrı alan yerine
                # displaymatrix metni içinde gelir.
                dm = (side or {}).get("displaymatrix")
                if isinstance(dm, str):
                    m = re.search(r"rotation\s+of\s+(-?\d+(?:\.\d+)?)\s+degrees", dm, flags=re.IGNORECASE)
                    if m:
                        try:
                            val = int(round(float(m.group(1))))
                            if val != 0:
                                side_rotation = val
                        except Exception:
                            pass
                    # Bazı ffprobe çıktılarında 'rotation of ... degrees' metni yok,
                    # yalnızca matris satırları bulunuyor. Bu durumda matrisi çöz.
                    if side_rotation is None:
                        nums = [int(x) for x in re.findall(r"-?\d+", dm)]
                        if len(nums) >= 6:
                            a, b, _, c, d, _ = nums[:6]
                            if abs(a) < abs(b) and abs(d) < abs(c):
                                if b < 0 and c > 0:
                                    side_rotation = 90
                                elif b > 0 and c < 0:
                                    side_rotation = 270
                            elif a < 0 and d < 0:
                                side_rotation = 180
                if side_rotation is None:
                    continue
                break
            try:
                val = int(float(side.get("rotation", 0) or 0))
                if val != 0:
                    side_rotation = val
            except Exception:
                pass
            break

        rotation = side_rotation if side_rotation is not None else (tag_rotation or 0)
        rotation = ((rotation % 360) + 360) % 360

        # Bazı kaynaklarda görüntü zaten dikey piksel boyutunda iken rotate=90/270
        # metadata'sı da yazılabiliyor. Bu durumda ikinci kez çevirmeyelim.
        if rotation in (90, 270) and width and height and int(height) > int(width):
            rotation = 0
        return rotation

    def _parse_ratio_value(self, ratio_text):
        txt = (ratio_text or "").strip()
        if not txt or txt in ("0:1", "N/A"):
            return None
        try:
            if ":" in txt:
                a, b = txt.split(":", 1)
                num = float(a)
                den = float(b)
                if den == 0:
                    return None
                return num / den
            val = float(txt)
            return val if val > 0 else None
        except Exception:
            return None

    def _normalize_rotation_with_display_ratio(self, stream, width, height, rotation):
        """DAR/SAR ile çelişen yön bilgisini olabildiğince normalize eder."""
        try:
            w = int(width or 0)
            h = int(height or 0)
        except Exception:
            return rotation
        if w <= 0 or h <= 0:
            return rotation

        dar = self._parse_ratio_value((stream or {}).get("display_aspect_ratio"))
        if dar is None:
            sar = self._parse_ratio_value((stream or {}).get("sample_aspect_ratio"))
            if sar is not None:
                dar = (w * sar) / max(float(h), 1.0)

        if dar is None:
            return rotation

        display_portrait = dar < 1.0
        shown_w, shown_h = (h, w) if rotation in (90, 270) else (w, h)
        meta_portrait = shown_h > shown_w

        # Metadata yatay derken DAR net biçimde dikey (veya tersi) ise düzelt.
        if display_portrait != meta_portrait:
            if rotation in (90, 270):
                return 0
            return 90
        return rotation

    def _rotation_from_format_tags(self, fmt_tags):
        tags = fmt_tags or {}
        # Bazı iPhone/QuickTime dosyalarında orientation bilgisi format tag'lerinde olabilir.
        lowered = {str(k).strip().lower(): v for k, v in tags.items()}
        keys = (
            "rotate",
            "com.apple.quicktime.video-orientation",
            "com.apple.quicktime.rotate",
            "quicktime:rotate",
        )
        for k in keys:
            if k not in lowered:
                continue
            try:
                raw = str(lowered.get(k) or "0").strip().lower()
                # iPhone tarafında bazen metin değerleri gelebiliyor.
                named = {
                    "portrait": 90,
                    "portraitupside down": 270,
                    "portraitupsidedown": 270,
                    "landscape": 0,
                    "landscapeleft": 0,
                    "landscaperight": 180,
                    "up": 0,
                    "down": 180,
                    "left": 90,
                    "right": 270,
                }
                if raw in named:
                    v = named[raw]
                else:
                    v = int(float(raw))
                    # Bazı cihazlar orientation enum (1..4) ya da EXIF (1..8) yazar.
                    if "orientation" in k:
                        enum_map_1_4 = {1: 90, 2: 270, 3: 0, 4: 180}
                        exif_map_1_8 = {1: 0, 3: 180, 6: 90, 8: 270}
                        if v in enum_map_1_4:
                            v = enum_map_1_4[v]
                        elif v in exif_map_1_8:
                            v = exif_map_1_8[v]
                if v != 0:
                    return ((v % 360) + 360) % 360
            except Exception:
                pass

        # Son çare: beklenmedik anahtar adlarında rotate/orientation geçiyorsa da dene.
        for rk, rv in lowered.items():
            key = str(rk)
            if ("rotate" not in key) and ("orientation" not in key):
                continue
            try:
                raw = str(rv or "").strip().lower()
                if not raw:
                    continue
                if raw in ("portrait", "up", "left"):
                    return 90
                if raw in ("portraitupsidedown", "down"):
                    return 270
                v = int(float(raw))
                if v != 0:
                    return ((v % 360) + 360) % 360
            except Exception:
                continue
        return 0

    def _probe_primary_video_stream_index(self, path):
        if not path:
            return None
        cmd = [
            self.ffprobe_executable,
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type,width,height,duration,disposition",
            "-of",
            "json",
            self._ffmpeg_safe_path(path),
        ]
        try:
            output = self._check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore")
            data = json.loads(output)
        except Exception:
            return None
        stream = self._pick_primary_video_stream(data.get("streams") or [])
        if not stream:
            return None
        try:
            return int(stream.get("index"))
        except Exception:
            return None

    def _probe_frame_rotation(self, path):
        if not path:
            return 0
        stream_index = self._probe_primary_video_stream_index(path)
        selectors = []
        if stream_index is not None:
            selectors.append(str(stream_index))
        selectors.append("v:0")
        intervals = ["%+#24", "10%+#24"]

        def _extract_rotation_from_side(side):
            try:
                val = int(float((side or {}).get("rotation", 0) or 0))
                if val != 0:
                    return ((val % 360) + 360) % 360
            except Exception:
                pass
            dm = (side or {}).get("displaymatrix")
            if isinstance(dm, str):
                m = re.search(r"rotation\s+of\s+(-?\d+(?:\.\d+)?)\s+degrees", dm, flags=re.IGNORECASE)
                if m:
                    try:
                        val = int(round(float(m.group(1))))
                        if val != 0:
                            return ((val % 360) + 360) % 360
                    except Exception:
                        pass
                nums = [int(x) for x in re.findall(r"-?\d+", dm)]
                if len(nums) >= 6:
                    a, b, _, c, d, _ = nums[:6]
                    if abs(a) < abs(b) and abs(d) < abs(c):
                        if b < 0 and c > 0:
                            return 90
                        if b > 0 and c < 0:
                            return 270
                    elif a < 0 and d < 0:
                        return 180
            return 0

        for sel in selectors:
            for interval in intervals:
                cmd = [
                    self.ffprobe_executable,
                    "-v",
                    "error",
                    "-select_streams",
                    sel,
                    "-read_intervals",
                    interval,
                    "-show_entries",
                    "frame=side_data_list,side_data",
                    "-of",
                    "json",
                    self._ffmpeg_safe_path(path),
                ]
                try:
                    output = self._check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore")
                    data = json.loads(output)
                except Exception:
                    continue
                for fr in (data.get("frames") or []):
                    for side in (fr.get("side_data_list") or fr.get("side_data") or []):
                        rot = _extract_rotation_from_side(side)
                        if rot in (90, 180, 270):
                            return rot
        return 0

    def _apply_orientation_override(self, width, height, rotation):
        mode = (self.orientation_override or "").strip().lower()
        if mode not in ("portrait", "landscape"):
            return rotation
        try:
            w = int(width or 0)
            h = int(height or 0)
        except Exception:
            return rotation
        if w <= 0 or h <= 0:
            return rotation
        r = ((int(rotation or 0) % 360) + 360) % 360
        shown_w, shown_h = (h, w) if r in (90, 270) else (w, h)
        is_portrait = shown_h > shown_w
        target_portrait = (mode == "portrait")
        if is_portrait == target_portrait:
            return r
        return (r + 90) % 360

    def _detect_true_rotation(self, path):
        """Tüm güvenilir kaynakları sırayla deneyerek dönüş açısını bulur."""
        if not path or not os.path.exists(path):
            return 0

        cmd = [
            self.ffprobe_executable,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            self._ffmpeg_safe_path(path),
        ]
        try:
            output = self._check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore")
            data = json.loads(output)
        except Exception:
            return 0

        stream = self._pick_primary_video_stream(data.get("streams") or [])
        if not stream:
            return 0
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)

        rotation = self._resolve_stream_rotation(stream, width=width, height=height)
        if rotation == 0:
            rotation = self._rotation_from_format_tags(((data.get("format") or {}).get("tags") or {}))
        if rotation == 0:
            rotation = self._probe_frame_rotation(path)

        rotation = self._normalize_rotation_with_display_ratio(stream, width, height, rotation)
        rotation = ((rotation % 360) + 360) % 360
        if rotation in (90, 270) and width and height and int(height) > int(width):
            rotation = 0
        return rotation
    def _pick_primary_video_stream(self, streams):
        """En olası gerçek video akışını seçer (kapak görselini eleyerek)."""
        vids = []
        for s in (streams or []):
            if (s.get("codec_type") or "").lower() != "video":
                continue
            disp = s.get("disposition") or {}
            attached = int(disp.get("attached_pic") or 0)
            w = int(s.get("width") or 0)
            h = int(s.get("height") or 0)
            area = max(0, w * h)
            try:
                dur = float(s.get("duration") or 0.0)
            except Exception:
                dur = 0.0
            vids.append((attached, area, dur, s))
        if not vids:
            return None
        # attached_pic olmayan, alanı büyük ve süresi dolu akışı tercih et.
        vids.sort(key=lambda x: (x[0], -x[1], -x[2]))
        return vids[0][3]

    def _probe_signature(self, path):
        cmd_v = [
            self.ffprobe_executable, "-v", "error",
            "-show_entries", "stream=codec_type,codec_name,width,height,r_frame_rate,duration,side_data_list,disposition,sample_aspect_ratio,display_aspect_ratio:stream_tags:stream_side_data:format_tags", "-of", "json", self._ffmpeg_safe_path(path)
        ]
        data_v = json.loads(self._check_output(cmd_v, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore"))
        sv = self._pick_primary_video_stream(data_v.get("streams") or []) or {}
        width = int(sv.get("width") or 0)
        height = int(sv.get("height") or 0)
        rotation = self._detect_true_rotation(path)
        if rotation in (90, 270):
            width, height = height, width
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
            "width": width,
            "height": height,
            "fps": float(fps),
            "sample_rate": int(sa.get("sample_rate") or 48000),
            "channels": int(sa.get("channels") or 2),
        }

    def _analyze_media(self, path):
        if not path:
            return {}
        apath = os.path.abspath(path)
        cached = self.media_analysis_cache.get(apath)
        if cached:
            return deepcopy(cached)
        cmd = [
            self.ffprobe_executable,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels,bit_rate",
            "-of",
            "json",
            self._ffmpeg_safe_path(apath),
        ]
        info = {
            "video": {},
            "audio": {},
        }
        try:
            data = json.loads(self._check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore"))
        except Exception:
            return info
        for stream in data.get("streams") or []:
            stype = (stream.get("codec_type") or "").strip().lower()
            if stype == "video" and not info["video"]:
                rate = stream.get("r_frame_rate", "0/1")
                fps = 0.0
                try:
                    n, d = rate.split("/", 1)
                    fps = float(n) / max(float(d), 1.0)
                except Exception:
                    try:
                        fps = float(rate)
                    except Exception:
                        fps = 0.0
                info["video"] = {
                    "codec": stream.get("codec_name") or "",
                    "width": int(stream.get("width") or 0),
                    "height": int(stream.get("height") or 0),
                    "fps": float(fps),
                    "bit_rate": int(stream.get("bit_rate") or 0),
                }
            elif stype == "audio" and not info["audio"]:
                info["audio"] = {
                    "codec": stream.get("codec_name") or "",
                    "sample_rate": int(stream.get("sample_rate") or 0),
                    "channels": int(stream.get("channels") or 0),
                    "bit_rate": int(stream.get("bit_rate") or 0),
                }
        self.media_analysis_cache[apath] = deepcopy(info)
        return info

    def _pick_highest_compatible_audio_bitrate(self, paths, fallback_bps=192000):
        if self.audio_opts.get("bit_rate", "copy") != "copy":
            try:
                return int(self.audio_opts.get("bit_rate"))
            except Exception:
                return fallback_bps
        candidates = []
        for p in paths:
            a = self._analyze_media(p).get("audio") or {}
            br = int(a.get("bit_rate") or 0)
            if br > 0:
                candidates.append(br)
        if candidates:
            return max(candidates)
        return fallback_bps

    def _stable_mix_audio_bitrate(self, paths, fallback_bps=256000):
        """
        Mix sonrası sesin gereksiz kalite düşüşünü azaltmak için
        güvenli bir alt bitrate uygular.
        """
        picked = self._pick_highest_compatible_audio_bitrate(paths, fallback_bps=fallback_bps)
        try:
            picked_i = int(picked)
        except Exception:
            picked_i = int(fallback_bps)
        # Stereo AAC için pratikte daha stabil kalite tabanı
        return max(256000, picked_i)

    def _adapt_to_signature(self, src_path, sig):
        fd, out_path = self._temp_media_path("edit", ".tmp", src_path)
        os.close(fd)
        if fill_to_frame:
            vf = (
                f"scale={sig['width']}:{sig['height']}:force_original_aspect_ratio=increase,"
                f"crop={sig['width']}:{sig['height']},setsar=1,fps={sig['fps']}"
            )
        else:
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

    def _get_video_duration(self, path):
        cmd = [
            self.ffprobe_executable,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            self._ffmpeg_safe_path(path),
        ]
        try:
            out = self._check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore").strip()
            return max(0.0, float(out))
        except Exception:
            return 0.0

    def _get_audio_duration(self, path):
        cmd = [
            self.ffprobe_executable,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            self._ffmpeg_safe_path(path),
        ]
        try:
            out = self._check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore").strip()
            return max(0.0, float(out))
        except Exception:
            return 0.0

    def _duration_mismatch_report(self, path):
        vd = self._get_video_duration(path)
        ad = self._get_audio_duration(path)
        if vd <= 0.0 or ad <= 0.0:
            return None
        return abs(vd - ad), vd, ad

    def _get_effective_duration(self, path):
        md = self._get_media_duration(path)
        vd = self._get_video_duration(path)
        ad = self._get_audio_duration(path)
        # Video düzenleyicide timeline uzunluğu video akışıyla hizalı olmalı.
        # Bazı dosyalarda ses akışı gereksiz uzun raporlanabildiği için (özellikle mix/paste sonrası)
        # toplam süreyi şişirmemek adına öncelik video süresidir.
        if vd and vd > 0.0:
            return vd
        if md and md > 0.0:
            return md
        if ad and ad > 0.0:
            return ad
        return 0.0

    def _keyframes_near(self, path, center, radius=1.0):
        start = max(0.0, float(center) - float(radius))
        end = max(start + 0.001, float(center) + float(radius))
        interval = f"{start}%{end}"
        cmd = [
            self.ffprobe_executable,
            "-v",
            "error",
            "-skip_frame",
            "nokey",
            "-select_streams",
            "v:0",
            "-read_intervals",
            interval,
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-of",
            "csv=p=0",
            self._ffmpeg_safe_path(path),
        ]
        try:
            out = self._check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore")
        except Exception:
            return []
        times = []
        for line in out.splitlines():
            v = line.strip().split(",")[0].strip()
            if not v:
                continue
            try:
                times.append(float(v))
            except Exception:
                pass
        return times

    def _nearest_keyframe_distance(self, path, t, radius=1.0):
        kfs = self._keyframes_near(path, t, radius=radius)
        if not kfs:
            return None
        return min(abs(float(k) - float(t)) for k in kfs)

    def _copy_mode_is_safe_for_parts(self, path, parts, threshold=0.12):
        total = max(0.0, float(self.length))
        risky_points = []
        for seg_start, seg_end in parts:
            seg_start = float(seg_start)
            seg_end = float(seg_end)
            if seg_start > 0.001:
                d = self._nearest_keyframe_distance(path, seg_start)
                if d is None or d > threshold:
                    risky_points.append(seg_start)
            if seg_end < total - 0.001:
                d = self._nearest_keyframe_distance(path, seg_end)
                if d is None or d > threshold:
                    risky_points.append(seg_end)
        return len(risky_points) == 0, risky_points

    def _segment_copy_is_safe(self, path, seg_start, seg_end, threshold=0.12, keyframe_cache=None):
        """Tek parça için copy kesimin keyframe açısından güvenli olup olmadığını döndürür."""
        try:
            seg_start = float(seg_start)
            seg_end = float(seg_end)
        except Exception:
            return False
        if seg_end - seg_start <= 0.001:
            return False
        total = max(0.0, float(self._get_effective_duration(path)))
        if total <= 0.0:
            return False

        def _nearest_cached(ts):
            key = (str(path).lower(), round(float(ts), 3), round(float(threshold), 3))
            if isinstance(keyframe_cache, dict) and key in keyframe_cache:
                return keyframe_cache[key]
            d = self._nearest_keyframe_distance(path, ts)
            if isinstance(keyframe_cache, dict):
                keyframe_cache[key] = d
            return d

        if seg_start > 0.001:
            d = _nearest_cached(seg_start)
            if d is None or d > threshold:
                return False
        if seg_end < total - 0.001:
            d = _nearest_cached(seg_end)
            if d is None or d > threshold:
                return False
        return True

    def _build_save_analysis(self):
        if not self.media_path:
            return "Dosya yok.", "current"
        has_cuts = len(self.cuts) > 0
        has_audio_input = self.has_audio_stream()
        audio_filters, mute_args = self._build_audio_filters()
        video_filters = self._build_transform_filters()
        needs_video_processing = bool(video_filters)
        needs_audio_processing = bool(audio_filters) or bool(mute_args) or (
            self.audio_opts["codec"] != "copy"
            or self.audio_opts["channels"] != "copy"
            or self.audio_opts["sample_rate"] != "copy"
            or self.audio_opts["bit_rate"] != "copy"
        )
        if not has_audio_input:
            needs_audio_processing = False
        parts = self._build_keep_segments()
        copy_safe, risky_points = self._copy_mode_is_safe_for_parts(self.media_path, parts)
        _, _, rot = self.get_video_geometry()
        lines = [
            f"Toplam kesim: {len(self.cuts)}",
            f"Video işlem gerekli: {'Evet' if needs_video_processing else 'Hayır'}",
            f"Ses işlem gerekli: {'Evet' if needs_audio_processing else 'Hayır'}",
            f"Keyframe riski: {'Düşük' if copy_safe else 'Orta/Yüksek'}",
            f"Algılanan dönüş: {int(rot or 0)} derece",
        ]
        if risky_points:
            shown = ", ".join(self.fmt(self.real_to_virtual(p)) for p in risky_points[:4])
            lines.append(f"Riskli kesim noktaları: {shown}")
        if not has_cuts:
            lines.append("Kesim yok: mevcut düzende kaydetme güvenli.")
            return "\n".join(lines), "current"
        if needs_video_processing or needs_audio_processing:
            lines.append("Akıllı çözüm: gerekli yerlerde re-encode ile kaydet önerilir.")
            return "\n".join(lines), "safe"
        if copy_safe:
            lines.append("Kesimler keyframe'e yakın: copy modu uygun.")
            return "\n".join(lines), "current"
        lines.append("Kesimler keyframe'den uzak: senkron için güvenli çözüm önerilir.")
        return "\n".join(lines), "safe"

    def _choose_save_mode_with_analysis(self):
        text, recommended = self._build_save_analysis()
        file_speak = self.speech_opts.get("status_file", True)
        self.say(f"Kayıt analizi: {'güvenli mod' if recommended == 'safe' else 'mevcut mod'} seçildi", speak=file_speak, update_status=file_speak)
        return recommended

    def _should_fill_without_bars(self, input_path, sig):
        try:
            src_v = (self._analyze_media(input_path).get("video") or {})
            sw = int(src_v.get("width") or 0)
            sh = int(src_v.get("height") or 0)
            tw = int(sig.get("width") or 0)
            th = int(sig.get("height") or 0)
            if sw <= 0 or sh <= 0 or tw <= 0 or th <= 0:
                return False
            src_portrait = sh > sw
            dst_portrait = th > tw
            return src_portrait != dst_portrait
        except Exception:
            return False

    def _extract_normalized_segment(self, input_path, start, end, sig, out_path, audio_gain=1.0, audio_bitrate_bps=None, fill_to_frame=False):
        dur = max(0.0, end - start)
        if fill_to_frame:
            vf = (
                f"scale={sig['width']}:{sig['height']}:force_original_aspect_ratio=increase,"
                f"crop={sig['width']}:{sig['height']},setsar=1,fps={sig['fps']}"
            )
        else:
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
            sr = int(sig.get("sample_rate") or 48000)
            ch = int(sig.get("channels") or 2)
            if audio_bitrate_bps is None:
                audio_bitrate_bps = self._pick_highest_compatible_audio_bitrate([input_path, self.media_path or input_path])
            cmd += [
                "-c:a",
                "aac",
                "-b:a",
                str(max(64000, int(audio_bitrate_bps or 192000))),
                "-ar",
                str(sr),
                "-ac",
                str(ch),
            ]
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
            "-fflags",
            "+genpts",
            "-avoid_negative_ts",
            "make_zero",
            "-c",
            "copy",
            self._ffmpeg_safe_path(out_path),
        ]
        self._check_output(cmd, stderr=subprocess.STDOUT)

    def _extract_copy_segment_audio_reencode(self, input_path, start, end, out_path, sample_rate=48000, channels=2, bitrate_bps=192000):
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
            "-fflags",
            "+genpts",
            "-avoid_negative_ts",
            "make_zero",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            str(max(64000, int(bitrate_bps or 192000))),
            "-ar",
            str(max(8000, int(sample_rate or 48000))),
            "-ac",
            str(max(1, int(channels or 2))),
            self._ffmpeg_safe_path(out_path),
        ]
        self._check_output(cmd, stderr=subprocess.STDOUT)


    def _segment_duration_sum(self, segments):
        total = 0.0
        for a, b in (segments or []):
            try:
                total += max(0.0, float(b) - float(a))
            except Exception:
                pass
        return max(0.0, total)

    def _extract_fast_reencode_segment(self, input_path, start, end, out_path, audio_gain=1.0, audio_bitrate_bps=192000):
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
            "-ar",
            "48000",
            "-ac",
            "2",
        ]
        if audio_gain <= 0.0001:
            cmd += ["-an"]
        elif abs(audio_gain - 1.0) > 0.001:
            cmd += ["-af", f"volume={audio_gain:.4f}"]
        if audio_gain > 0.0001:
            cmd += ["-b:a", str(max(32000, int(audio_bitrate_bps or 192000)))]
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
                    esc = item.replace("'", "'\''")
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

    def _concat_files_copy_video_reencode_audio(self, files, out_path, sample_rate=48000, channels=2, bitrate_bps=192000):
        fd, list_path = tempfile.mkstemp(prefix="bve_concat_va_", suffix=".txt")
        os.close(fd)
        try:
            with open(list_path, "w", encoding="utf-8") as f:
                for item in files:
                    esc = item.replace("'", "'\''")
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
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                str(max(64000, int(bitrate_bps or 192000))),
                "-ar",
                str(max(8000, int(sample_rate or 48000))),
                "-ac",
                str(max(1, int(channels or 2))),
                self._ffmpeg_safe_path(out_path),
            ]
            self._check_output(cmd, stderr=subprocess.STDOUT)
        finally:
            try:
                os.remove(list_path)
            except Exception:
                pass


    def _concat_ranges_copy(self, ranges, out_path):
        fd, list_path = tempfile.mkstemp(prefix="bve_concat_ranges_", suffix=".txt")
        os.close(fd)
        try:
            with open(list_path, "w", encoding="utf-8") as f:
                for item_path, start, end in ranges:
                    esc = str(item_path).replace("'", "'\\''")
                    f.write(f"file '{esc}'\n")
                    f.write(f"inpoint {max(0.0, float(start)):.6f}\n")
                    f.write(f"outpoint {max(0.0, float(end)):.6f}\n")
            cmd = [
                self.ffmpeg_executable,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                self._ffmpeg_safe_path(list_path),
                "-fflags",
                "+genpts",
                "-avoid_negative_ts",
                "make_zero",
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

    def _concat_files_resilient(self, files, out_path, reencode_on_fail=True):
        try:
            self._concat_files_copy(files, out_path)
            return
        except Exception:
            if not reencode_on_fail:
                raise
        fd, list_path = tempfile.mkstemp(prefix="bve_concat_safe_", suffix=".txt")
        os.close(fd)
        try:
            with open(list_path, "w", encoding="utf-8") as f:
                for item in files:
                    esc = item.replace("'", "'\''")
                    f.write(f"file '{esc}'\n")
            bitrate = self._pick_highest_compatible_audio_bitrate(files)
            cmd = [
                self.ffmpeg_executable,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                self._ffmpeg_safe_path(list_path),
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-b:a",
                str(bitrate),
                "-movflags",
                "+faststart",
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

    def _real_to_virtual_with_cuts(self, real_pos, cuts):
        r = max(0.0, float(real_pos))
        merged = sorted([(float(a), float(b)) for (a, b) in (cuts or []) if float(b) > float(a)], key=lambda x: x[0])
        sub = 0.0
        for s, e in merged:
            if r <= s:
                break
            sub += min(r, e) - s
        return max(0.0, r - sub)

    def _virtual_to_real_with_cuts(self, virtual_pos, cuts, total_length):
        v = max(0.0, float(virtual_pos))
        total = max(0.0, float(total_length or 0.0))
        merged = sorted([(float(a), float(b)) for (a, b) in (cuts or []) if float(b) > float(a)], key=lambda x: x[0])
        cum_v = 0.0
        prev_r = 0.0
        for s, e in merged:
            kept = max(0.0, s - prev_r)
            if kept > 1e-9 and v <= cum_v + kept:
                return prev_r + (v - cum_v)
            cum_v += kept
            prev_r = max(prev_r, e)
        last_kept = max(0.0, total - prev_r)
        if last_kept > 1e-9 and v <= cum_v + last_kept:
            return prev_r + (v - cum_v)
        return total

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
            "segments_locked_real": [(float(a), float(b)) for (a, b) in segments],
            "virtual_segments": [
                (
                    self._real_to_virtual_with_cuts(float(a), self.get_merged()),
                    self._real_to_virtual_with_cuts(float(b), self.get_merged()),
                )
                for (a, b) in segments
            ],
            "cuts_snapshot": deepcopy(self.get_merged()),
            "length_snapshot": float(self.length or 0.0),
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
        locked_real = self.segment_clipboard.get("segments_locked_real") or []
        if locked_real:
            src_segments = [(float(a), float(b)) for (a, b) in locked_real if float(b) - float(a) > 0.001]
        snap_cuts = self.segment_clipboard.get("cuts_snapshot") or []
        snap_len = float(self.segment_clipboard.get("length_snapshot") or 0.0)
        virtual_segments = self.segment_clipboard.get("virtual_segments") or []
        if not src_segments and virtual_segments and snap_len > 0.0:
            rebuilt = []
            for va, vb in virtual_segments:
                ra = self._virtual_to_real_with_cuts(va, snap_cuts, snap_len)
                rb = self._virtual_to_real_with_cuts(vb, snap_cuts, snap_len)
                if rb - ra > 0.001:
                    rebuilt.append((ra, rb))
            if rebuilt:
                src_segments = rebuilt
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
                target_len = max(
                    self._get_effective_duration(self.media_path),
                    float(self.length or 0.0),
                )
                if target_len <= 0.001:
                    raise Exception("Hedef video süresi okunamadı.")
                target_sig = self._probe_signature(self.media_path)
                src_sig = self._probe_signature(src_path)
                compatible = self._is_signature_compatible(target_sig, src_sig)
                valid_segments = [(a, b) for (a, b) in src_segments if float(b) - float(a) > 0.001]
                fd4, merged = self._temp_media_path("edit", ".mp4", self.media_path)
                os.close(fd4)
                temp_insert_files = []
                if not valid_segments:
                    raise Exception("Yapıştırılacak geçerli bir segment bulunamadı.")
                source_gain = float(self.segment_clipboard.get("mix_gain", 1.0))
                virtual_total = self._virtual_length_for_total_length(target_len)
                insert_at_virtual = max(0.0, min(requested_insert_virtual, virtual_total))
                insert_real = max(0.0, min(self.virtual_to_real(insert_at_virtual), target_len))
                keep_parts = self._build_keep_segments()
                before_ranges = []
                after_ranges = []
                for seg_start, seg_end in keep_parts:
                    s = max(0.0, min(float(seg_start), target_len))
                    e = max(s, min(float(seg_end), target_len))
                    if e - s <= 0.001:
                        continue
                    if e <= insert_real + 0.0005:
                        before_ranges.append((s, e))
                        continue
                    if s >= insert_real - 0.0005:
                        after_ranges.append((s, e))
                        continue
                    if insert_real - s > 0.001:
                        before_ranges.append((s, insert_real))
                    if e - insert_real > 0.001:
                        after_ranges.append((insert_real, e))

                insert_ranges = []
                audio_br = self._pick_highest_compatible_audio_bitrate([self.media_path, src_path])
                if compatible and abs(source_gain - 1.0) <= 0.001:
                    insert_ranges = [(src_path, seg_start, seg_end) for (seg_start, seg_end) in valid_segments]
                else:
                    expected_insert_dur = self._segment_duration_sum(valid_segments)
                    fill_to_frame = self._should_fill_without_bars(src_path, target_sig)
                    for seg_start, seg_end in valid_segments:
                        fd_ins, ins_path = self._temp_media_path("insert_adapt", ".mp4", src_path)
                        os.close(fd_ins)
                        temp_insert_files.append(ins_path)
                        self._extract_normalized_segment(
                            src_path,
                            seg_start,
                            seg_end,
                            target_sig,
                            ins_path,
                            source_gain,
                            audio_bitrate_bps=audio_br,
                            fill_to_frame=fill_to_frame,
                        )
                    if len(temp_insert_files) == 1:
                        dur = expected_insert_dur if expected_insert_dur > 0.0 else self._get_media_duration(temp_insert_files[0])
                        insert_ranges = [(temp_insert_files[0], 0.0, dur)]
                    else:
                        fd_join, joined_path = self._temp_media_path("insert_join", ".mp4", src_path)
                        os.close(fd_join)
                        temp_insert_files.append(joined_path)
                        self._concat_files_resilient(temp_insert_files[:-1], joined_path)
                        dur = expected_insert_dur if expected_insert_dur > 0.0 else self._get_media_duration(joined_path)
                        insert_ranges = [(joined_path, 0.0, dur)]

                concat_ranges = []
                for seg_start, seg_end in before_ranges:
                    concat_ranges.append((self.media_path, seg_start, seg_end, "copy"))
                for r_path, seg_start, seg_end in insert_ranges:
                    mode = "copy" if (compatible and abs(source_gain - 1.0) <= 0.001 and r_path == src_path) else "ready"
                    concat_ranges.append((r_path, seg_start, seg_end, mode))
                for seg_start, seg_end in after_ranges:
                    concat_ranges.append((self.media_path, seg_start, seg_end, "copy"))
                if not concat_ranges:
                    raise Exception("Yapıştırma için birleştirilecek geçerli aralık yok.")
                concat_files = []
                force_resilient_concat = False
                needs_audio_reencode = any(mode != "copy" for (_p, _s, _e, mode) in concat_ranges)
                target_audio = self._analyze_media(self.media_path).get("audio") or {}
                has_target_audio = bool(target_audio.get("codec"))
                audio_sr = int(target_audio.get("sample_rate") or target_sig.get("sample_rate") or 48000)
                audio_ch = int(target_audio.get("channels") or target_sig.get("channels") or 2)
                keyframe_cache = {}
                try:
                    for idx, (r_path, seg_start, seg_end, mode) in enumerate(concat_ranges):
                        fd_seg, seg_path = self._temp_media_path(f"merge_{idx}", ".mp4", r_path)
                        os.close(fd_seg)
                        concat_files.append(seg_path)
                        temp_insert_files.append(seg_path)
                        if mode == "ready":
                            shutil.copyfile(r_path, seg_path)
                        else:
                            copy_safe = self._segment_copy_is_safe(
                                r_path,
                                seg_start,
                                seg_end,
                                threshold=0.10,
                                keyframe_cache=keyframe_cache,
                            )
                            if not copy_safe:
                                force_resilient_concat = True
                                needs_audio_reencode = True
                                self._extract_fast_reencode_segment(
                                    r_path,
                                    seg_start,
                                    seg_end,
                                    seg_path,
                                    audio_gain=1.0,
                                    audio_bitrate_bps=audio_br,
                                )
                                continue
                            if needs_audio_reencode and has_target_audio:
                                self._extract_copy_segment_audio_reencode(
                                    r_path,
                                    seg_start,
                                    seg_end,
                                    seg_path,
                                    sample_rate=audio_sr,
                                    channels=audio_ch,
                                    bitrate_bps=audio_br,
                                )
                            else:
                                self._extract_copy_segment(r_path, seg_start, seg_end, seg_path)
                    if len(concat_files) == 1:
                        shutil.copyfile(concat_files[0], merged)
                    else:
                        if force_resilient_concat:
                            self._concat_files_resilient(concat_files, merged)
                        elif needs_audio_reencode and has_target_audio:
                            self._concat_files_copy_video_reencode_audio(
                                concat_files,
                                merged,
                                sample_rate=audio_sr,
                                channels=audio_ch,
                                bitrate_bps=audio_br,
                            )
                        else:
                            self._concat_files_copy(concat_files, merged)
                finally:
                    for tmp in temp_insert_files:
                        try:
                            if tmp and os.path.exists(tmp):
                                os.remove(tmp)
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
        self.say("Mix'leme işlemi", speak=edit_speak, update_status=edit_speak)
        try:
            def worker():
                target_len_video = self._get_video_duration(self.media_path)
                target_len_media = self._get_media_duration(self.media_path)
                # Mix çıktısında ses uzunluğu video uzunluğunu takip etmeli.
                if target_len_video > 0:
                    target_len = target_len_video
                elif target_len_media > 0:
                    target_len = target_len_media
                else:
                    target_len = self.length
                if target_len <= 0:
                    target_len = self.length
                insert_at = max(0.0, min(self.virtual_to_real(insert_at_virtual), target_len))
                mix_trim_end = max(0.01, target_len)
                target_sig = self._probe_signature(self.media_path)
                target_channels = int(target_sig.get("channels") or 2)
                target_sample_rate = int(target_sig.get("sample_rate") or 48000)
                mix_bitrate_bps = self._stable_mix_audio_bitrate([self.media_path, src_path])
                clip_gain = self.segment_clipboard.get("mix_gain", 1.0)
                target_gain = self._effective_gain_from_volume(self.volume) if not self.muted else 0.0
                fd1, clip_path = self._temp_media_path("mixclip", ".wav", src_path)
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
                            "pcm_s16le",
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
                                "pcm_s16le",
                                self._ffmpeg_safe_path(clip_path),
                            ]
                            run_cmd(clip_cmd_retry)
                    else:
                        part_files = []
                        try:
                            for seg_start, seg_end in clip_segments:
                                fd_part, part_path = self._temp_media_path("mixpart", ".wav", src_path)
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
                                    "pcm_s16le",
                                    self._ffmpeg_safe_path(part_path),
                                ]
                                run_cmd(part_cmd)
                            if len(part_files) == 1:
                                shutil.copyfile(part_files[0], clip_path)
                            else:
                                fd_list, list_path = tempfile.mkstemp(prefix="bve_mixparts_", suffix=".txt")
                                os.close(fd_list)
                                try:
                                    with open(list_path, "w", encoding="utf-8") as f:
                                        for item in part_files:
                                            esc = item.replace("'", "'\''")
                                            f.write(f"file '{esc}'\n")
                                    join_cmd = [
                                        self.ffmpeg_executable,
                                        "-y",
                                        "-f",
                                        "concat",
                                        "-safe",
                                        "0",
                                        "-i",
                                        self._ffmpeg_safe_path(list_path),
                                        "-c:a",
                                        "pcm_s16le",
                                        self._ffmpeg_safe_path(clip_path),
                                    ]
                                    run_cmd(join_cmd)
                                finally:
                                    try:
                                        os.remove(list_path)
                                    except Exception:
                                        pass
                        finally:
                            for part in part_files:
                                try:
                                    os.remove(part)
                                except Exception:
                                    pass
                    delay_ms = int(round(insert_at * 1000.0))
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
                        fcx = f"[0:a]{target_chain}[a0];[1:a]{clip_chain}[a1];[a0][a1]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,atrim=0:{mix_trim_end}[aout]"
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
                            str(mix_bitrate_bps),
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
                            str(mix_bitrate_bps),
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
            mismatch = self._duration_mismatch_report(self.media_path)
            if mismatch and mismatch[0] > 0.12:
                delta, _vd, _ad = mismatch
                warn_speak = self.speech_opts.get("status_errors", True)
                self.say(f"Uyarı: Ses görüntü süresi farkı {self.fmt_ms(delta)}", speak=warn_speak, update_status=warn_speak)
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
        probed_len = self._get_effective_duration(path)
        self.length = probed_len if probed_len > 0 else 0.0
        self._on_media_loaded()
        self._call_later_if_session(150, self._apply_player_volume)

        self.current_video_rotation = 0
        if self._detect_media_kind(path) == "video":
            self.current_video_rotation = int(self._detect_true_rotation(path) or 0)



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
        text = self.tr_text(text)
        self.last_status_message = text
        if update_status and getattr(self, "visual_status_enabled", False):
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

    def _mark_project_clean_after_media_save(self):
        self.project_dirty = False
        self.project_temp_path = None
        self._save_active_workspace()

    def _current_project_data(self):
        self._save_active_workspace()
        return {
            "version": 3,
            "active_workspace": self.active_workspace,
            "workspaces": self.workspaces,
            "media_path": self.media_path,
            "source_path": self.source_path,
            "original_media_path": self.original_media_path,
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
            "orientation_mode": self.orientation_mode,
            "orientation_override": self.orientation_override,
            "current_video_rotation": int(self.current_video_rotation or 0),
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
            self._persist_app_settings()
            self.project_dirty = False
            file_speak = self.speech_opts.get("status_file", True)
            self.say(self.tr("project_saved", "Proje kaydedildi"), speak=file_speak, update_status=file_speak)
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
            style=wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION,
        )
        if hasattr(dlg, "SetYesNoCancelLabels"):
            dlg.SetYesNoCancelLabels("Evet", "Hayır", "İptal")
        if hasattr(dlg, "SetEscapeId"):
            dlg.SetEscapeId(wx.ID_CANCEL)
        try:
            result = dlg.ShowModal()
        finally:
            dlg.Destroy()
        if result == wx.ID_YES:
            return self._save_project_as_dialog()
        if result == wx.ID_CANCEL:
            return False
        return True

    def _cleanup_unused_temp_media(self):
        referenced = set()
        for ws in (self.workspaces or []):
            if not isinstance(ws, dict):
                continue
            mp = ws.get("media_path")
            sp = ws.get("source_path")
            if mp:
                referenced.add(os.path.abspath(mp))
            if sp:
                referenced.add(os.path.abspath(sp))
        if self.media_path:
            referenced.add(os.path.abspath(self.media_path))
        if self.source_path:
            referenced.add(os.path.abspath(self.source_path))
        for p in list(self.adapted_temp_files):
            try:
                ap = os.path.abspath(p)
            except Exception:
                ap = p
            if ap in referenced:
                continue
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
            self.adapted_temp_files.discard(p)

    def on_close_app(self, event):
        self._save_active_workspace()
        if not self._confirm_save_project_before_close():
            event.Veto()
            return
        if not self.project_path:
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
        self._persist_app_settings()
        event.Skip()

    def _doc_file_path(self, filename):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)

    def _persist_app_settings(self):
        self.app_settings["language"] = (self.language or "tr").lower()
        self.app_settings["last_open_dir"] = self.last_open_dir or _default_dir_path()
        self.app_settings["last_save_dir"] = self.last_save_dir or _default_dir_path()
        self.app_settings["last_project_dir"] = self.last_project_dir or _default_dir_path()
        self.app_settings["video_opts"] = deepcopy(self.video_opts)
        self.app_settings["audio_opts"] = deepcopy(self.audio_opts)
        self.app_settings["speech_opts"] = deepcopy(self.speech_opts)
        _save_settings(self.app_settings)

    def _localized_doc_filename(self, key):
        docs = {
            "tr": {"about": "Hakkında.txt", "shortcuts": "KısaYollar.txt", "readme": "Beni Oku.txt"},
            "en": {"about": "About.en.txt", "shortcuts": "Shortcuts.en.txt", "readme": "Readme.en.txt"},
            "de": {"about": "About.en.txt", "shortcuts": "Shortcuts.en.txt", "readme": "Readme.en.txt"},
            "es": {"about": "About.en.txt", "shortcuts": "Shortcuts.en.txt", "readme": "Readme.en.txt"},
            "pt": {"about": "About.en.txt", "shortcuts": "Shortcuts.en.txt", "readme": "Readme.en.txt"},
            "ar": {"about": "About.en.txt", "shortcuts": "Shortcuts.en.txt", "readme": "Readme.en.txt"},
            "fr": {"about": "About.en.txt", "shortcuts": "Shortcuts.en.txt", "readme": "Readme.en.txt"},
            "nl": {"about": "About.en.txt", "shortcuts": "Shortcuts.en.txt", "readme": "Readme.en.txt"},
            "it": {"about": "About.en.txt", "shortcuts": "Shortcuts.en.txt", "readme": "Readme.en.txt"},
        }
        selected = docs.get((self.language or "tr").lower()) or docs["tr"]
        fname = selected.get(key) or docs["tr"].get(key)
        path = self._doc_file_path(fname)
        if os.path.exists(path):
            return fname
        return docs["tr"][key]

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


    def _build_preview_filter_chain(self, effect_filter):
        chain = []
        if effect_filter:
            chain.append(effect_filter)
        gain = self._effective_gain_from_volume(self.volume)
        if self.muted:
            chain.append("volume=0")
        elif abs(gain - 1.0) > 0.001:
            chain.append(f"volume={gain:.4f}")
        return ",".join(chain)

    def _start_effect_preview(self, effect):
        if not self.player or not self.media_path:
            return
        effect_filter = (effect or {}).get("filter", "")
        self.player.af = self._build_preview_filter_chain(effect_filter)
        try:
            self._effect_preview_prev_loop = getattr(self.player, "loop_file", None)
        except Exception:
            self._effect_preview_prev_loop = None
        try:
            self.player.loop_file = "inf"
        except Exception:
            pass
        self.player.pause = False

    def _stop_effect_preview(self):
        if not self.player:
            return
        self.player.pause = True
        try:
            if self._effect_preview_prev_loop is None:
                self.player.loop_file = "no"
            else:
                self.player.loop_file = self._effect_preview_prev_loop
        except Exception:
            pass
        self._apply_player_volume()


    def _list_ffmpeg_audio_effects(self):
        """
        Kontrollü presetleri + FFmpeg'den gelen ek efektleri birlikte döndürür.
        Böylece önce temel liste korunur, eksik kalanlar da görünür.
        """
        try:
            raw = self._check_output([self.ffmpeg_executable, "-hide_banner", "-filters"], stderr=subprocess.STDOUT).decode("utf-8", errors="ignore")
        except Exception:
            return []

        curated_effects = [
            # echo category
            {"name": "echo1", "filter": "aecho=0.85:0.65:280:0.30"},
            {"name": "echo2", "filter": "aecho=0.88:0.70:420|840:0.32|0.18"},
            # reverb category (4/5/6 -> 1/2/3)
            {"name": "reverb1", "filter": "aecho=0.68:0.95:55|95|145|220|320:0.52|0.40|0.30|0.22|0.14,extrastereo=m=1.35"},
            {"name": "reverb2", "filter": "aecho=0.65:0.98:110|190|290|430|620|860:0.62|0.52|0.42|0.32|0.24|0.16"},
            {"name": "reverb3", "filter": "aecho=0.66:0.96:85|140|210|300|420|580:0.58|0.46|0.36|0.26|0.18|0.12"},
            {"name": "telephone1", "filter": "highpass=f=300,lowpass=f=3000"},
        ]

        preferred_keywords = (
            "reverb", "echo", "delay", "phaser", "stereo", "vibrato",
            "flanger", "wah", "chorus", "tremolo", "ringmod", "pitch",
            "distortion", "overdrive", "crystalizer", "surround", "haas",
            "telephone", "phone",
        )
        blocked_names = {
            "amix", "amerge", "aformat", "aresample", "atrim", "asetpts", "asetrate",
            "asetnsamples", "silenceremove", "silencedetect", "astats", "ebur128",
            "showwaves", "showwavespic", "abuffer", "abuffersink", "concat",
            "anull", "anullsink", "ashowinfo", "afdelaysrc", "asdr", "asisdr",
            "compensationdelay", "acue", "adelay", "aexciter", "crossfeed", "headphone",
            "stereotools",
        }

        extra = []
        for line in raw.splitlines():
            row = (line or "").strip()
            if not row:
                continue
            parts = row.split(None, 3)
            if len(parts) < 3:
                continue
            flags = parts[0]
            name = parts[1].strip()
            io = parts[2].strip()
            desc = parts[3].strip() if len(parts) >= 4 else ""
            name_l = name.lower()
            desc_l = desc.lower()
            if not re.match(r"^[A-Za-z0-9_]+$", name):
                continue
            if not re.match(r"^[\.TSC\|]+$", flags):
                continue
            if "A" not in io:
                continue
            if name_l in blocked_names:
                continue
            if not any(k in name_l or k in desc_l for k in preferred_keywords):
                continue
            extra.append((name, desc))

        # İstenen adlandırma/eleme kuralları
        rename_by_filter = {
            "aecho": "echo3",
            "aphaser": "phaser",
            "chorus": "chorus",
            "earwax": "stereo - Under water",
            "flanger": "flanger",
            "haas": "stereo",
            "stereowiden": "stereo2",
            "tremolo": "tremolo",
            "vibrato": "vibrato",
        }
        drop_filters = {"crystalizer", "extrastereo", "rubberband", "surround"}

        idx = 1
        for nm, ds in sorted(extra, key=lambda x: x[0].lower()):
            flt = (nm or "").strip().lower()
            if flt in drop_filters:
                continue
            if flt in rename_by_filter:
                label = rename_by_filter[flt]
            else:
                label = f"fx{idx}_{nm}" if not ds else f"fx{idx}_{nm} - {ds}"
                idx += 1
            curated_effects.append({"name": label, "filter": nm})

        # unique by filter while preserving first occurrence
        seen = set()
        out = []
        for e in curated_effects:
            k = (e.get("filter") or "").strip().lower()
            if not k or k in seen:
                continue
            seen.add(k)
            out.append(e)
        return out


    def _apply_audio_effect_to_media(self, effect):
        if not self._require_media():
            return
        effect_name = (effect or {}).get("name", "Efekt")
        effect_filter = (effect or {}).get("filter", "")
        if not effect_filter:
            self._show_error_dialog("Efekt filtresi boş.", "Hata")
            return
        kind = self._detect_media_kind(self.source_path or self.media_path)
        if kind == "video" and not self.has_audio_stream():
            self._show_error_dialog("Video içinde ses akışı bulunamadı.", "Bilgi")
            return

        def worker():
            if kind == "video":
                fd, out_path = self._temp_media_path("effect", ".mp4", self.media_path)
                os.close(fd)
                audio_br = self._stable_mix_audio_bitrate([self.media_path])
                cmd = [
                    self.ffmpeg_executable,
                    "-y",
                    "-i",
                    self._ffmpeg_safe_path(self.media_path),
                    "-map",
                    "0:v",
                    "-map",
                    "0:a",
                    "-c:v",
                    "copy",
                    "-af",
                    effect_filter,
                    "-c:a",
                    "aac",
                    "-b:a",
                    str(audio_br),
                    "-ac",
                    "2",
                    "-ar",
                    "48000",
                    self._ffmpeg_safe_path(out_path),
                ]
            else:
                fd, out_path = self._temp_media_path("effect", ".wav", self.media_path)
                os.close(fd)
                cmd = [
                    self.ffmpeg_executable,
                    "-y",
                    "-i",
                    self._ffmpeg_safe_path(self.media_path),
                    "-vn",
                    "-af",
                    effect_filter,
                    "-c:a",
                    "pcm_s16le",
                    self._ffmpeg_safe_path(out_path),
                ]
            self._check_output(cmd, stderr=subprocess.STDOUT)
            return out_path

        merged = self._run_blocking_with_progress("Efekt uygulanıyor", f"{effect_name} uygulanıyor...", worker)
        self._push_replace_media_undo()
        self.adapted_temp_files.add(merged)
        self.media_path = merged
        self.source_path = merged
        self._load_media(self.media_path)
        self._mark_project_dirty()
        edit_speak = self.speech_opts.get("status_edit", True)
        self.say(f"Efekt uygulandı: {effect_name}", speak=edit_speak, update_status=edit_speak)

    def _open_add_effect_dialog(self):
        if not self._require_media():
            return

        effects = self._list_ffmpeg_audio_effects()
        if not effects:
            self._show_info_dialog("FFmpeg ses efekt listesi alınamadı. Efekt özelliği devre dışı bırakıldı.", "Bilgi")
            return

        ed = EffectSelectDialog(self, "FFmpeg Efektleri", effects, source_label="FFmpeg")
        ed.bind_preview_callbacks(self._start_effect_preview, self._stop_effect_preview)
        res2 = ed.ShowModal()
        chosen = ed.selected_effect()
        if ed.previewing:
            self._stop_effect_preview()
        ed.Destroy()

        if res2 in (wx.ID_CANCEL, wx.ID_BACKWARD):
            return
        if res2 == wx.ID_OK and chosen:
            self._apply_audio_effect_to_media(chosen)

    # ---------- KEYS ----------
    def bind_keys(self):
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)

    def _require_media(self):
        if self.media_path:
            return True
        file_speak = self.speech_opts.get("status_file", True)
        self.say("dosya yok", speak=file_speak, update_status=file_speak)
        return False

    def _is_media_required_shortcut(self, k, ctrl, shift, alt):
        if alt:
            return False
        if k in (wx.WXK_SPACE, wx.WXK_HOME, wx.WXK_END, wx.WXK_DELETE, wx.WXK_F2):
            return True
        if ctrl and k in (ord("A"), ord("C"), ord("X"), ord("V"), ord("M"), ord("Z"), ord("G"), ord("T"), ord("1"), ord("2")):
            return True
        if ctrl and not shift and k in (ord("R"), ord("r"), wx.WXK_PAGEUP, wx.WXK_PAGEDOWN):
            return True
        if ctrl and shift and k in (ord("C"), ord("V"), ord("T"), ord("D"), ord("B"), ord("b"), ord("E"), ord("e"), wx.WXK_HOME, wx.WXK_END, wx.WXK_PAGEUP, wx.WXK_PAGEDOWN):
            return True
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
        if ctrl and shift and k in (ord("L"), ord("l")):
            self._open_language_selection_dialog()
            return
        if ctrl and k == wx.WXK_TAB:
            if shift:
                self._switch_workspace_relative(-1)
            else:
                self._switch_workspace_relative(1)
            return
        if k == wx.WXK_TAB:
            file_speak = self.speech_opts.get("status_file", True)
            name = os.path.basename(self.media_path) if self.media_path else "boş alan"
            self.say(f"Çalışma alanı {self.active_workspace + 1}: {name}", speak=file_speak, update_status=file_speak)
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
        if (not self.media_path) and self._is_media_required_shortcut(k, ctrl, shift, alt):
            self._require_media()
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
        elif ctrl and k == ord("X"):
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
                f"Toplam süre: {self.fmt_ms(total_v)}",
                speak=in_out_speak,
                update_status=in_out_speak,
            )
        elif ctrl and not shift and k in (ord("R"), ord("r")):
            if not self._require_media():
                return
            time_speak = self.speech_opts.get("time", True)
            cur_v, _total_v = self._current_virtual_stable()
            self.say(
                f"Mevcut Zaman: {self.fmt_ms(cur_v)}",
                speak=time_speak,
                update_status=time_speak,
            )
        elif k == wx.WXK_LEFT:
            self.seek(-5)
        elif k == wx.WXK_RIGHT:
            self.seek(5)
        elif k == wx.WXK_UP and not ctrl:
            self.seek(1)
        elif k == wx.WXK_DOWN and not ctrl:
            self.seek(-1)
        elif k == wx.WXK_F5:
            self.seek(-0.5)
        elif k == wx.WXK_F6:
            self.seek(0.5)
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
            if self.player and self.length > 0:
                end_pos = float(self.length)
                self._call_later_if_session(20, self._set_time_pos, end_pos)
                self._call_later_if_session(80, self._set_time_pos, end_pos)
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
        elif ctrl and shift and k == ord("C"):
            in_out_speak = self.speech_opts.get("in_out", True)
            if self.last_in is None:
                self.say("Son IN yok", speak=in_out_speak, update_status=in_out_speak)
            else:
                self.say(
                    f"Son IN: {self.fmt(self.real_to_virtual(self.last_in))}",
                    speak=in_out_speak,
                    update_status=in_out_speak,
                )
        elif ctrl and shift and k == ord("V"):
            in_out_speak = self.speech_opts.get("in_out", True)
            if self.last_out is None:
                self.say("Son OUT yok", speak=in_out_speak, update_status=in_out_speak)
            else:
                self.say(
                    f"Son OUT: {self.fmt(self.real_to_virtual(self.last_out))}",
                    speak=in_out_speak,
                    update_status=in_out_speak,
                )
        elif ctrl and shift and k == ord("T"):
            in_out_speak = self.speech_opts.get("in_out", True)
            if self.mark_in is None or self.mark_out is None or self.mark_out <= self.mark_in:
                self.say("IN ve OUT aralığı geçerli değil", speak=in_out_speak, update_status=in_out_speak)
            else:
                seg = self._normalize_time(self.mark_out - self.mark_in)
                self.say(f"IN-OUT süresi: {self.fmt_ms(seg)}", speak=in_out_speak, update_status=in_out_speak)
        elif ctrl and shift and k == ord("D"):
            self.show_video_properties_dialog()
        elif ctrl and shift and k in (ord("B"), ord("b")):
            self._open_orientation_dialog()
        elif ctrl and shift and k in (ord("E"), ord("e")):
            self._open_add_effect_dialog()
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

    def _current_virtual_stable(self):
        cur_v = self.real_to_virtual(self.current_time())
        total_v = self.get_virtual_length()
        if total_v > 0:
            if cur_v < 0.0 and abs(cur_v) <= 0.20:
                cur_v = 0.0
            if 0.0 < (total_v - cur_v) <= 0.30:
                cur_v = total_v
            elif 0.0 < (cur_v - total_v) <= 0.20:
                cur_v = total_v
        return cur_v, total_v

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
            # Bazı videolarda (keyframe aralığı geniş olduğunda) ilk seek bir önceki
            # anahtar kareye düşebiliyor. Okuma düzenini değiştirmeden, aynı hedefe
            # kısa gecikmeyle tekrar konumlayarak kaymayı azaltıyoruz.
            self._call_later_if_session(40, self._set_time_pos, r)
            self._call_later_if_session(120, self._set_time_pos, r)

    def goto_time(self):
        if not self.media_path:
            return
        cur_v, virt_len = self._current_virtual_stable()
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

    def fmt_ms(self, t):
        t = max(0.0, float(t or 0.0))
        total_ms = int(round(t * 1000.0))
        h, rem = divmod(total_ms, 3600 * 1000)
        m, rem = divmod(rem, 60 * 1000)
        sec, ms = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{sec:02d}:{ms:03d}"

    def update_time(self, e):
        if not self.media_path:
            return
        cur = self._normalize_time(self.current_time())
        if self.player:
            duration = self.player.duration
            if duration and duration > 0:
                d = float(duration)
                # Bazı birleştirme çıktılarında ffprobe'dan gelen başlangıç süresi kısa
                # raporlanabiliyor (özellikle copy + concat zaman damgası sapmalarında).
                # Oynatıcı daha uzun gerçek süre bildiriyorsa timeline toplamını güncelle.
                if self.length <= 0:
                    self.length = d
                elif d > self.length + 0.25:
                    self.length = d
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
            # Sınır değerinde (tam segment sonu) kesilen aralığa düşmemek için
            # iç segmentlerde sağ sınırı dışarıda bırakıyoruz.
            if kept > 1e-9 and v < cum_v + kept:
                return prev_r + (v - cum_v)
            cum_v += kept
            prev_r = e
        last_kept = self.length - prev_r
        if last_kept > 1e-9 and v <= cum_v + last_kept:
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
    def _probe_video_properties(self, path=None):
        target_path = path or self.media_path
        if not target_path:
            return None

        cmd = [
            self.ffprobe_executable,
            "-v", "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,r_frame_rate,side_data_list,sample_aspect_ratio,display_aspect_ratio:stream_tags:stream_side_data:format=duration:format_tags",
            "-of", "json",
            self._ffmpeg_safe_path(target_path),
        ]

        props = {
            "size": "Bilinmiyor",
            "orientation": "Bilinmiyor",
            "rotation": "0 derece",
            "fps": "Bilinmiyor",
            "vcodec": "Bilinmiyor",
            "acodec": "Yok",
            "duration": "Bilinmiyor",
            "current_duration": self.fmt(self.get_virtual_length()) if self.media_path else "Bilinmiyor",
        }

        try:
            output = self._check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore")
            data = json.loads(output)
        except Exception:
            return props

        width = height = 0
        for stream in (data.get("streams") or []):
            if (stream.get("codec_type") or "").lower() == "video":
                props["vcodec"] = stream.get("codec_name") or "Bilinmiyor"
                width = int(stream.get("width") or 0)
                height = int(stream.get("height") or 0)
                rate = stream.get("r_frame_rate") or ""
                try:
                    if "/" in rate:
                        n, d = rate.split("/", 1)
                        fps = float(n) / max(float(d), 1.0)
                        props["fps"] = f"{fps:.2f} fps"
                except Exception:
                    pass
            elif (stream.get("codec_type") or "").lower() == "audio" and props["acodec"] in ("Yok", "Bilinmiyor"):
                props["acodec"] = stream.get("codec_name") or "Bilinmiyor"

        raw_rotation = self._detect_true_rotation(target_path)
        effective_rotation = int(self.current_video_rotation or raw_rotation)
        effective_rotation = self._apply_orientation_override(width, height, effective_rotation)
        effective_rotation = ((effective_rotation % 360) + 360) % 360

        props["rotation"] = f"{effective_rotation} derece"
        if effective_rotation != raw_rotation or int(self.current_video_rotation or 0) != 0:
            props["rotation"] += " (otomatik düzeltildi)"

        if width and height:
            props["size"] = f"{width} x {height}"
            shown_w, shown_h = (height, width) if effective_rotation in (90, 270) else (width, height)
            orientation = "Dikey" if shown_h > shown_w else "Yatay" if shown_w > shown_h else "Kare"
            props["orientation"] = f"{orientation} (görüntüleme: {shown_w} x {shown_h})"

        return props




    def show_video_properties_dialog(self):
        if not self.media_path:
            file_speak = self.speech_opts.get("status_file", True)
            self.say("dosya yok", speak=file_speak, update_status=file_speak)
            return
        current_props = self._probe_video_properties(self.media_path) or {}
        original_path = self.original_media_path or self.source_path or self.media_path
        original_props = self._probe_video_properties(original_path) or {}
        d = VideoPropertiesDialog(self, original_props, current_props)
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
            "-show_entries",
            "stream=codec_type,codec_name,width,height,duration,side_data_list,disposition,sample_aspect_ratio,display_aspect_ratio:stream_tags:stream_side_data:format_tags",
            "-of",
            "json",
            self._ffmpeg_safe_path(self.media_path),
        ]
        width = None
        height = None
        try:
            output = self._check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore")
            data = json.loads(output)
            stream = self._pick_primary_video_stream(data.get("streams") or [])
            if stream:
                width = int(stream.get("width") or 0)
                height = int(stream.get("height") or 0)
        except Exception:
            return None, None, 0

        # mpv'de uyguladığımız gerçek rotasyonu kullan
        raw_rot = self._detect_true_rotation(self.media_path)
        effective_rot = int(self.current_video_rotation or raw_rot)
        effective_rot = self._apply_orientation_override(width, height, effective_rot)
        effective_rot = ((effective_rot % 360) + 360) % 360

        return width, height, effective_rot


    def _current_orientation_is_portrait(self):
        w, h, rot = self.get_video_geometry()
        if not w or not h:
            return None
        shown_w, shown_h = (h, w) if rot in (90, 270) else (w, h)
        return shown_h > shown_w

    def _open_orientation_dialog(self):
        if not self._require_media():
            return
        if self._detect_media_kind(self.media_path) != "video":
            self._show_error_dialog("Yalnız video dosyalarında boyutlandırma uygulanır.", "Bilgi")
            return
        is_portrait = self._current_orientation_is_portrait()
        d = OrientationDialog(self, is_portrait)
        try:
            if d.ShowModal() != wx.ID_OK or not d.choice:
                return
            if d.choice == "force_portrait":
                self.orientation_override = "portrait"
                self._mark_project_dirty()
                playback_speak = self.speech_opts.get("status_playback", True)
                self.say("Yön algısı zorla dikey olarak ayarlandı", speak=playback_speak, update_status=playback_speak)
                return
            if d.choice == "force_landscape":
                self.orientation_override = "landscape"
                self._mark_project_dirty()
                playback_speak = self.speech_opts.get("status_playback", True)
                self.say("Yön algısı zorla yatay olarak ayarlandı", speak=playback_speak, update_status=playback_speak)
                return
            if d.choice == "force_auto":
                self.orientation_override = None
                self._mark_project_dirty()
                playback_speak = self.speech_opts.get("status_playback", True)
                self.say("Yön algısı otomatiğe döndü", speak=playback_speak, update_status=playback_speak)
                return
            self.orientation_override = None
            self.orientation_mode = d.choice
            self._mark_project_dirty()
            msg = "Boyutlandırma hedefi: Dikey" if d.choice == "portrait" else "Boyutlandırma hedefi: Yatay"
            playback_speak = self.speech_opts.get("status_playback", True)
            self.say(msg, speak=playback_speak, update_status=playback_speak)
        finally:
            d.Destroy()

    def _build_transform_filters(self):
        mode = self.orientation_mode
        if mode not in ("portrait", "landscape"):
            return []
        w, h, rot = self.get_video_geometry()
        if not w or not h:
            return []
        shown_w, shown_h = (h, w) if rot in (90, 270) else (w, h)
        now_portrait = shown_h > shown_w
        target_portrait = (mode == "portrait")
        if now_portrait == target_portrait:
            return []
        target_w, target_h = (1080, 1920) if target_portrait else (1920, 1080)
        return [
            "transpose=1",
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase",
            f"crop={target_w}:{target_h}",
            "setsar=1",
            "fps=30",
        ]

    def _open_language_selection_dialog(self):
        dlg = LanguageSelectDialog(self, initial_language=self.language)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            new_lang = dlg.selected_language()
        finally:
            dlg.Destroy()
        if new_lang == self.language:
            return
        self.language = new_lang
        self.lang_pack = _load_lang_pack(self.language)
        self.ui_labels = dialog_labels(self.lang_pack)
        self.status.SetLabel(self.tr("welcome_status", "Hoş Geldiniz, yardım için f bir tuşuna basın"))
        self.last_status_message = self.status.GetLabel()
        self._persist_app_settings()
        speak = self.speech_opts.get("status_options", True)
        self.say(f"Dil değiştirildi: {new_lang}", speak=speak, update_status=speak)

    # ---------- OPTIONS ----------
    def open_options(self):
        d = OptionsDialog(self, self.video_opts, self.audio_opts, self.speech_opts)
        if d.ShowModal() == wx.ID_OK:
            self.video_opts = d.get_video_opts()
            self.audio_opts = d.get_audio_opts()
            self.speech_opts = d.get_speech_opts()
            options_speak = self.speech_opts.get("status_options", True)
            self.say(self.tr("options_saved", "Seçenekler kaydedildi"), speak=options_speak, update_status=options_speak)
            self._mark_project_dirty()
            self._persist_app_settings()
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
            self._persist_app_settings()
            self.project_dirty = False
            self.project_temp_path = None
            file_speak = self.speech_opts.get("status_file", True)
            self.say(self.tr("project_loaded", "Proje yüklendi"), speak=file_speak, update_status=file_speak)
            return True
        media_path = data.get("media_path")
        if not media_path or not os.path.exists(media_path):
            self._show_error_dialog("Projede kayıtlı video bulunamadı.", "Hata")
            return False
        self.source_path = data.get("source_path") or media_path
        self.original_media_path = data.get("original_media_path") or self.source_path or media_path
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
        self.orientation_mode = data.get("orientation_mode")
        self.orientation_override = data.get("orientation_override")
        self.current_video_rotation = int(data.get("current_video_rotation", 0) or 0)
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
        self._persist_app_settings()
        self.project_dirty = False
        self.project_temp_path = None
        file_speak = self.speech_opts.get("status_file", True)
        self.say(self.tr("project_loaded", "Proje yüklendi"), speak=file_speak, update_status=file_speak)
        return True

    def _is_image_file(self, path):
        ext = os.path.splitext(path or "")[1].lower()
        return ext in IMAGE_EXTS

    def _default_image_video_signature(self):
        # Tek çalışma alanında mevcut video varsa onun şartlarını temel al.
        if self.media_path and os.path.exists(self.media_path) and self._detect_media_kind(self.media_path) == "video":
            sig = self._probe_signature(self.media_path)
            if sig.get("width") and sig.get("height"):
                return {
                    "width": int(sig.get("width") or 1920),
                    "height": int(sig.get("height") or 1080),
                    "fps": float(sig.get("fps") or 30.0),
                }
        return {"width": 1920, "height": 1080, "fps": 30.0}

    def _create_video_from_image(self, image_path, duration_sec, enable_rotate):
        sig = self._default_image_video_signature()
        w = int(max(16, sig.get("width", 1920)))
        h = int(max(16, sig.get("height", 1080)))
        fps = max(1.0, float(sig.get("fps", 30.0)))
        fd, out_path = self._temp_media_path("image", ".mp4", image_path)
        os.close(fd)
        base = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        if enable_rotate:
            # Daha canlı bir hareket için hafif zoom + pan + düşük açılı dinamik dönüş.
            vf = (
                base
                + f",zoompan=z='1.03+0.02*sin(2*PI*on/{max(10,int(fps*4))})':"
                  f"x='(iw-iw/zoom)/2 + 12*sin(2*PI*on/{max(10,int(fps*3))})':"
                  f"y='(ih-ih/zoom)/2 + 8*cos(2*PI*on/{max(10,int(fps*5))})':d=1:s={w}x{h}:fps={fps}"
                + ",rotate=0.02*sin(2*PI*t/3)+0.015*cos(2*PI*t/5):ow=rotw(iw):oh=roth(ih):c=black"
                + f",scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"
            )
        else:
            vf = base
        cmd = [
            self.ffmpeg_executable,
            "-y",
            "-loop",
            "1",
            "-i",
            self._ffmpeg_safe_path(image_path),
            "-t",
            str(max(1.0, float(duration_sec))),
            "-vf",
            vf,
            "-r",
            str(fps),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-an",
            self._ffmpeg_safe_path(out_path),
        ]
        try:
            self._check_output(cmd, stderr=subprocess.STDOUT)
        except Exception:
            try:
                if os.path.exists(out_path):
                    os.remove(out_path)
            except Exception:
                pass
            raise
        self.adapted_temp_files.add(out_path)
        return out_path

    def _load_opened_media(self, opened_path, selected_source_path):
        self.source_path = selected_source_path
        self.original_media_path = selected_source_path
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
        self.orientation_mode = None
        self.orientation_override = None
        ext = os.path.splitext(self.source_path)[1].lstrip(".").lower()
        if ext and ext not in {"jpg", "jpeg", "png", "bmp", "webp", "gif", "tif", "tiff"}:
            self.video_opts["format"] = ext
        self._reset_project_state()
        self._load_media(self.media_path)
        self.timer.Start(300)
        self._save_active_workspace()
        file_speak = self.speech_opts.get("status_file", True)
        self.say(f"Çalışma alanı {self.active_workspace + 1}: {os.path.basename(self.media_path)}", speak=file_speak, update_status=file_speak)

    def open_file(self):
        default_dir = self.last_open_dir or (os.path.dirname(self.media_path) if self.media_path else "")
        d = wx.FileDialog(
            self,
            self.tr("file_open_title", "Dosya Aç"),
            defaultDir=default_dir,
            wildcard="Video/Ses/Fotoğraf/Proje Dosyaları|*.mp4;*.avi;*.mkv;*.mov;*.wmv;*.wav;*.aac;*.mp3;*.m4a;*.flac;*.ogg;*.opus;*.wma;*.jpg;*.jpeg;*.png;*.bmp;*.webp;*.gif;*.tif;*.tiff;*.bve| Proje Dosyası|*.bve| Tüm Dosyalar|*.*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if d.ShowModal() == wx.ID_OK:
            selected_path = d.GetPath()
            self.last_open_dir = os.path.dirname(selected_path)
            self._persist_app_settings()
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
                if self._is_image_file(selected_path):
                    img_dlg = ImageToVideoDialog(self)
                    converted_path = None
                    try:
                        if img_dlg.ShowModal() != wx.ID_OK:
                            opened_path = None
                        else:
                            duration_sec, enable_rotate = img_dlg.get_values()
                            def _worker():
                                return self._create_video_from_image(selected_path, duration_sec, enable_rotate)
                            converted_path = self._run_blocking_with_progress("Dönüştürülüyor", "Fotoğraf video'ya dönüştürülüyor...", _worker)
                    finally:
                        img_dlg.Destroy()
                    opened_path = converted_path if converted_path else opened_path
                if opened_path:
                    self._analyze_media(opened_path)
                    self._load_opened_media(opened_path, selected_path)
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
        self.original_media_path = None
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
        self.orientation_mode = None
        self.orientation_override = None
        self.current_video_rotation = 0
        self.video_opts = {"format": "mp4", "codec": "copy", "crf": "23", "preset": "medium"}
        self.audio_opts = {"codec": "copy", "channels": "copy", "sample_rate": "copy", "bit_rate": "copy", "output_ext": "m4a"}
        self._on_media_closed()
        file_speak = self.speech_opts.get("status_file", True)
        self.say("dosya kapatıldı", speak=file_speak, update_status=file_speak)
        self._reset_project_state()
        self._save_active_workspace()
        self._cleanup_unused_temp_media()

    def save_file(self):
        if not self.media_path:
            file_speak = self.speech_opts.get("status_file", True)
            self.say("dosya yok", speak=file_speak, update_status=file_speak)
            return
        media_kind = self._detect_media_kind(self.media_path)
        save_mode = self._choose_save_mode_with_analysis() if media_kind == "video" else "current"
        default_dir = self.last_save_dir or os.path.dirname(self.media_path)
        base_name = os.path.splitext(os.path.basename(self.media_path))[0]
        if media_kind == "audio":
            audio_exts = ["wav", "mp3", "m4a", "aac", "flac", "ogg", "opus", "wma"]
            pref_ext = (self.audio_opts.get("output_ext") or "m4a").lower()
            ext = pref_ext if pref_ext in audio_exts else "m4a"
            default_file = f"{base_name}.{ext}"
            wildcard = "Ses Dosyaları|*.wav;*.mp3;*.m4a;*.aac;*.flac;*.ogg;*.opus;*.wma| Tüm Dosyalar|*.*"
        else:
            ext = self.video_opts["format"]
            default_file = f"{base_name}.{ext}"
            wildcard = f"Video Dosyaları|*.{ext}| Tüm Dosyalar|*.*"
        d = wx.FileDialog(
            self,
            self.tr("save_as_title", "Farklı Kaydet"),
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
            self._persist_app_settings()
            progress = ProgressDialog(self, modal=False)
            self._set_active_progress(progress)
            if media_kind == "audio":
                thread = threading.Thread(target=self.apply_audio_save_with_progress, args=(out_path, progress))
            else:
                thread = threading.Thread(target=self.apply_cuts_with_progress, args=(out_path, progress, save_mode))
            thread.start()
            progress.Show()
        d.Destroy()

    def _set_active_progress(self, progress):
        self.active_progress = progress

    def _clear_active_progress(self, progress):
        if self.active_progress is progress:
            self.active_progress = None

    def _video_args_for_cut(self, force_reencode=False):
        selected_codec = self.video_opts["codec"]
        if selected_codec == "copy" and not force_reencode:
            return ["-c:v", "copy"]
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


    def _render_keep_segments_safe(self, input_path, parts, out_path, progress):
        target_sig = self._probe_signature(input_path)
        safe_bitrate = self._pick_highest_compatible_audio_bitrate([input_path])
        audio_gain = self._effective_gain_from_volume(self.volume) if not self.muted else 0.0
        temp_parts = []
        try:
            total = max(1, len(parts))
            for idx, (start, end) in enumerate(parts, start=1):
                if end - start <= 0.0005:
                    continue
                fd_seg, seg_path = self._temp_media_path("safe", ".mp4", self.media_path)
                os.close(fd_seg)
                temp_parts.append(seg_path)
                self._extract_normalized_segment(input_path, start, end, target_sig, seg_path, audio_gain, fill_to_frame=False)
                if audio_gain > 0.0001:
                    try:
                        # ensure target bitrate policy on safe pieces
                        fd_fix, fixed_path = self._temp_media_path("safefix", ".mp4", self.media_path)
                        os.close(fd_fix)
                        cmd = [
                            self.ffmpeg_executable,
                            "-y",
                            "-i",
                            self._ffmpeg_safe_path(seg_path),
                            "-c:v",
                            "copy",
                            "-c:a",
                            "aac",
                            "-ar",
                            "48000",
                            "-ac",
                            "2",
                            "-b:a",
                            str(safe_bitrate),
                            self._ffmpeg_safe_path(fixed_path),
                        ]
                        self._check_output(cmd, stderr=subprocess.STDOUT)
                        os.remove(seg_path)
                        temp_parts[-1] = fixed_path
                    except Exception:
                        pass
                pct = int((idx / total) * 90)
                progress.update_progress(pct, f"Güvenli kesimler hazırlanıyor... %{pct}")
            if not temp_parts:
                raise Exception("Güvenli modda birleştirilecek bölüm bulunamadı")
            if len(temp_parts) == 1:
                shutil.copyfile(temp_parts[0], out_path)
            else:
                self._concat_files_resilient(temp_parts, out_path)
            progress.update_progress(95, "Güvenli kesimler birleştiriliyor...")
        finally:
            for seg in temp_parts:
                try:
                    os.remove(seg)
                except Exception:
                    pass

    def _concat_keep_segments_copy(self, input_path, parts, out_path, progress):
        temp_parts = []
        try:
            total = max(1, len(parts))
            for idx, (start, end) in enumerate(parts, start=1):
                fd_seg, seg_path = self._temp_media_path("keep", ".mp4", self.media_path)
                os.close(fd_seg)
                temp_parts.append(seg_path)
                self._extract_copy_segment(input_path, start, end, seg_path)
                pct = int((idx / total) * 90)
                progress.update_progress(pct, f"Kesimler kopya modunda hazırlanıyor... %{pct}")
            if not temp_parts:
                raise Exception("Kopya modunda birleştirilecek bölüm bulunamadı")
            self._concat_files_resilient(temp_parts, out_path)
            progress.update_progress(95, "Kopya modunda birleştiriliyor...")
        finally:
            for seg in temp_parts:
                try:
                    os.remove(seg)
                except Exception:
                    pass

    def apply_cuts_with_progress(self, out, progress, save_mode="current"):
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
            keyframe_safe, _risky = self._copy_mode_is_safe_for_parts(self.media_path, parts)
            can_cut_copy_mode = (
                save_mode == "current"
                and has_cuts
                and not needs_audio_processing
                and not needs_video_processing
                and keyframe_safe
                and self.video_opts["codec"] == "copy"
                and self.audio_opts["codec"] == "copy"
            )
            if not has_cuts and not needs_audio_processing and not needs_video_processing and self.video_opts["codec"] == "copy":
                copy_cmd = [self.ffmpeg_executable, "-y", "-i", input_path, "-c", "copy", output_path]
                self._run_ffmpeg_with_progress(copy_cmd, progress, "Kopyalanıyor...")
            elif has_cuts and save_mode == "safe":
                self._render_keep_segments_safe(self.media_path, parts, out, progress)
            elif can_cut_copy_mode:
                self._concat_keep_segments_copy(self.media_path, parts, out, progress)
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
                cmd += self._video_args_for_cut(force_reencode=bool(filter_complex or video_filters))
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
            wx.CallAfter(self._mark_project_clean_after_media_save)
            wx.CallAfter(self._show_info_dialog, "Video'nuz kaydedildi.", "Bilgi")
            wx.CallAfter(self._cleanup_unused_temp_media)
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

    def apply_audio_save_with_progress(self, out, progress):
        try:
            progress.update_progress(1, "Ses kaydı hazırlanıyor...")
            input_path = self._ffmpeg_safe_path(self.media_path)
            output_path = self._ffmpeg_safe_path(out)
            has_cuts = len(self.cuts) > 0
            parts = self._build_keep_segments()
            filters, mute_args = self._build_audio_filters()
            if mute_args:
                filters = ["volume=0"]
                mute_args = []
            needs_processing = has_cuts or bool(filters) or (
                self.audio_opts["codec"] != "copy"
                or self.audio_opts["channels"] != "copy"
                or self.audio_opts["sample_rate"] != "copy"
                or self.audio_opts["bit_rate"] != "copy"
            )
            if not needs_processing:
                cmd = [self.ffmpeg_executable, "-y", "-i", input_path, "-vn", "-sn", "-c:a", "copy", output_path]
                self._run_ffmpeg_with_progress(cmd, progress, "Ses kopyalanıyor...")
            else:
                cmd = [self.ffmpeg_executable, "-y", "-i", input_path]
                if has_cuts:
                    achains = []
                    valid_parts = []
                    for (start, end) in parts:
                        if end - start <= 0.0005:
                            continue
                        idx = len(valid_parts)
                        valid_parts.append((start, end))
                        achains.append(f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{idx}]")
                    if not achains:
                        raise Exception("Kaydedilecek geçerli ses bölümü bulunamadı")
                    concat_inputs = "".join([f"[a{i}]" for i in range(len(valid_parts))])
                    filter_complex = ";".join(achains) + f";{concat_inputs}concat=n={len(valid_parts)}:v=0:a=1[a]"
                    if filters:
                        filter_complex += f";[a]{','.join(filters)}[aout]"
                        audio_map = "[aout]"
                    else:
                        audio_map = "[a]"
                    cmd += ["-filter_complex", filter_complex, "-map", audio_map]
                    cmd += ["-vn", "-sn"]
                    cmd += self._audio_args(include_filters=False, force_reencode=True)
                else:
                    if filters:
                        cmd += ["-af", ",".join(filters)]
                    cmd += ["-vn", "-sn"]
                    cmd += self._audio_args(include_filters=False, force_reencode=bool(filters))
                cmd += [output_path]
                total_dur = self.get_virtual_length()
                self._run_ffmpeg_with_progress(cmd, progress, "Ses kaydediliyor...", total_dur)
            progress.update_progress(100, "Tamamlandı")
            file_speak = self.speech_opts.get("status_file", True)
            wx.CallAfter(lambda: self.say("Ses kaydedildi", speak=file_speak, update_status=file_speak))
            wx.CallAfter(self._mark_project_clean_after_media_save)
            wx.CallAfter(self._show_info_dialog, "Ses dosyanız kaydedildi.", "Bilgi")
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
        dlg = wx.MessageDialog(None, self.tr_text(message), self.tr_text(title or self.ui_labels.get("info", "Bilgi")), style=wx.OK | wx.ICON_INFORMATION)
        if hasattr(dlg, "SetOKLabel"):
            dlg.SetOKLabel(self.ui_labels.get("ok", "Tamam"))
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
        ask = wx.MessageDialog(None, self.tr_text(msg), self.tr_text(title or self.ui_labels.get("error", "Hata")), style=wx.YES_NO | wx.ICON_ERROR)
        if hasattr(ask, "SetYesNoLabels"):
            ask.SetYesNoLabels(self.ui_labels.get("yes", "Evet"), self.ui_labels.get("no", "Hayır"))
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
        if needs_reencode:
            args += ["-ac", "2", "-ar", "48000"]
        else:
            if self.audio_opts["channels"] != "copy":
                ch = "1" if self.audio_opts["channels"] == "mono" else "2"
                args += ["-ac", ch]
            if self.audio_opts["sample_rate"] != "copy":
                args += ["-ar", self.audio_opts["sample_rate"]]
        if self.audio_opts["bit_rate"] != "copy":
            args += ["-b:a", self.audio_opts["bit_rate"]]
        elif needs_reencode:
            selected_bps = self._pick_highest_compatible_audio_bitrate([self.media_path])
            args += ["-b:a", str(max(192000, int(selected_bps or 192000)))]
        return args

# ================= RUN =================
class App(wx.App):
    def OnInit(self):
        settings = _load_settings()
        lang = (settings.get("language") or "").strip().lower()
        if not lang:
            dlg = LanguageSelectDialog(None, initial_language="tr")
            try:
                if dlg.ShowModal() == wx.ID_OK:
                    lang = dlg.selected_language()
                else:
                    lang = "tr"
            finally:
                dlg.Destroy()
            settings["language"] = lang
            _save_settings(settings)
        self.frame = Editor(app_settings=settings)
        return True

if __name__ == "__main__":
    App(False).MainLoop()