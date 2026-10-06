"""
Сканер столовой с офлайн-режимом.

Логика:
  1. При старте и раз в 60 сек — обновляем локальный кэш студентов с сервера.
  2. При сканировании:
     • Сначала пробуем отправить на сервер.
     • Если связи нет — проверяем по локальной базе и пишем в очередь.
  3. Фоновая задача раз в 60 сек отправляет накопленную очередь на сервер.
"""

import os
import re
import sqlite3
import threading
from datetime import datetime, date, timedelta

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.utils import platform

import requests


# ============================================================
# НАСТРОЙКИ
# ============================================================
# ⚠️ ЗАМЕНИТЕ НА IP ВАШЕГО ПК
SERVER_URL = "http://192.168.1.100:5000"

REQUEST_TIMEOUT = 3          # сек на ответ сервера
RESULT_DURATION = 3.0        # сколько показывать результат
AUTO_SUBMIT_DELAY = 0.8      # авто-отправка, если сканер не шлёт Enter
SYNC_INTERVAL = 60           # сек между фоновыми синхронизациями


# Путь к локальной БД (работает и на Android, и на ПК для тестов)
try:
    from android.storage import app_storage_path
    DB_PATH = os.path.join(app_storage_path(), 'local_cache.db')
except ImportError:
    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'local_cache.db')


# ============================================================
# ЗВУК — нативный Android ToneGenerator
# ============================================================
ANDROID = False
tone = None

if platform == 'android':
    try:
        from jnius import autoclass
        ToneGenerator = autoclass('android.media.ToneGenerator')
        AudioManager = autoclass('android.media.AudioManager')
        tone = ToneGenerator(AudioManager.STREAM_NOTIFICATION, 100)
        ANDROID = True
    except Exception as e:
        print(f"[sound] Ошибка инициализации: {e}")


def play_beep(success):
    """Звук: успех — двойной бип, ошибка — бззз."""
    if ANDROID and tone:
        try:
            if success:
                tone.startTone(ToneGenerator.TONE_PROP_BEEP2, 250)
            else:
                tone.startTone(ToneGenerator.TONE_SUP_ERROR, 500)
        except Exception as e:
            print(f"[sound] {e}")


# ============================================================
# ЛОКАЛЬНАЯ БАЗА
# ============================================================
def init_local_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS students (
        student_id INTEGER PRIMARY KEY,
        full_name TEXT,
        group_name TEXT,
        is_beneficiary INTEGER DEFAULT 1,
        benefit_type TEXT,
        benefit_end TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS offline_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER,
        meal_date TEXT,
        meal_time TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS local_meals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER,
        meal_date TEXT
    )''')
    conn.commit()
    conn.close()


def save_students_to_cache(students):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM students")
    for s in students:
        c.execute('''INSERT OR REPLACE INTO students
            (student_id, full_name, group_name, is_beneficiary, benefit_type, benefit_end)
            VALUES (?, ?, ?, ?, ?, ?)''',
            (s['id'], s['name'], s.get('group') or '',
             1 if s.get('active') else 0,
             s.get('benefit_type') or '', s.get('benefit_end') or ''))
    conn.commit()
    conn.close()


def fetch_students_from_server():
    try:
        r = requests.get(f"{SERVER_URL}/api/students", timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def refresh_cache():
    """Скачивает список студентов и сохраняет в локальную БД. Возвращает True при успехе."""
    students = fetch_students_from_server()
    if students is not None:
        save_students_to_cache(students)
        return True
    return False


def save_local_meal(student_id):
    """Фиксирует, что студент поел — для защиты от дубля в офлайне."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = str(date.today())
    c.execute("SELECT COUNT(*) FROM local_meals WHERE student_id = ? AND meal_date = ?",
              (student_id, today))
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO local_meals (student_id, meal_date) VALUES (?, ?)",
                  (student_id, today))
        conn.commit()
    conn.close()


def cleanup_old_local_meals():
    """Удаляет старые записи (старше 30 дней), чтобы БД не пухла."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = str(date.today() - timedelta(days=30))
    c.execute("DELETE FROM local_meals WHERE meal_date < ?", (cutoff,))
    conn.commit()
    conn.close()


def check_student_offline(student_id):
    """Проверка по локальной базе. Возвращает (success, message)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT full_name, is_beneficiary, benefit_end FROM students WHERE student_id = ?",
              (student_id,))
    row = c.fetchone()

    if not row:
        conn.close()
        return False, "Студент не найден (офлайн)"

    full_name, is_benef, benefit_end = row
    today = str(date.today())
    now_time = datetime.now().strftime("%H:%M:%S")

    if not is_benef:
        conn.close()
        return False, f"{full_name}: Льгота отключена"

    if benefit_end and today > benefit_end:
        conn.close()
        return False, f"{full_name}: Срок истёк ({benefit_end})"

    # Дубль в локальном журнале (онлайн-запись, которая уже прошла)
    c.execute("SELECT COUNT(*) FROM local_meals WHERE student_id = ? AND meal_date = ?",
              (student_id, today))
    if c.fetchone()[0] > 0:
        conn.close()
        return False, f"{full_name}: Уже получал сегодня"

    # Дубль в очереди (офлайн-запись, ещё не ушла на сервер)
    c.execute("SELECT COUNT(*) FROM offline_queue WHERE student_id = ? AND meal_date = ?",
              (student_id, today))
    if c.fetchone()[0] > 0:
        conn.close()
        return False, f"{full_name}: Уже получал сегодня"

    # Записываем в очередь и локальный журнал
    c.execute("INSERT INTO offline_queue (student_id, meal_date, meal_time) VALUES (?, ?, ?)",
              (student_id, today, now_time))
    c.execute("INSERT INTO local_meals (student_id, meal_date) VALUES (?, ?)",
              (student_id, today))
    conn.commit()
    conn.close()
    return True, f"{full_name}: Выдано в {now_time} (офлайн)"


def sync_offline_queue():
    """Отправляет офлайн-записи на сервер. Возвращает (success, count)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT student_id, meal_date, meal_time FROM offline_queue")
    rows = c.fetchall()
    conn.close()

    if not rows:
        return True, 0

    payload = [{"student_id": r[0], "meal_date": r[1], "meal_time": r[2]} for r in rows]

    try:
        r = requests.post(f"{SERVER_URL}/api/sync_offline", json=payload, timeout=5)
        if r.status_code == 200:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("DELETE FROM offline_queue")
            conn.commit()
            conn.close()
            return True, len(rows)
    except Exception:
        pass
    return False, 0


def get_queue_size():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM offline_queue")
    n = c.fetchone()[0]
    conn.close()
    return n


# ============================================================
# ЭКРАН СКАНЕРА
# ============================================================
class ScannerScreen(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation='vertical', **kwargs)

        # Фон
        with self.canvas.before:
            self.bg_color = Color(0.12, 0.12, 0.12, 1)
            self.bg_rect = Rectangle(size=self.size, pos=self.pos)
        self.bind(size=self._update_rect, pos=self._update_rect)

        # Статус (мелкий, сверху)
        self.status_label = Label(
            text="Проверка связи...",
            font_size='18sp',
            size_hint=(1, 0.08),
            color=(0.7, 0.7, 0.7, 1),
        )

        # Главный индикатор
        self.result_label = Label(
            text="Наведите сканер\nна QR-код",
            font_size='52sp',
            halign='center',
            valign='middle',
            size_hint=(1, 0.84),
            color=(1, 1, 1, 1),
        )
        self.result_label.bind(size=self.result_label.setter('text_size'))

        # Скрытое поле ввода
        self.input = TextInput(
            text='', font_size='1sp', size_hint=(1, 0.08),
            opacity=0.01, multiline=False,
            background_color=(0, 0, 0, 0),
            foreground_color=(0, 0, 0, 0),
            cursor_color=(0, 0, 0, 0),
        )
        self.input.bind(on_text_validate=self.on_scan)
        self.input.bind(text=self.on_text_change)

        self.add_widget(self.status_label)
        self.add_widget(self.result_label)
        self.add_widget(self.input)

        self.submit_timer = None
        self.is_online = False

        Clock.schedule_once(self.focus_input, 0.5)
        Clock.schedule_interval(self.keep_focus, 1.0)

        # Стартовая синхронизация
        Clock.schedule_once(
            lambda dt: threading.Thread(target=self.initial_sync, daemon=True).start(),
            1
        )
        # Периодическая синхронизация
        Clock.schedule_interval(
            lambda dt: threading.Thread(target=self.background_sync, daemon=True).start(),
            SYNC_INTERVAL
        )

    def _update_rect(self, *args):
        self.bg_rect.pos = self.pos
        self.bg_rect.size = self.size

    def focus_input(self, dt=0):
        try:
            self.input.focus = True
        except Exception:
            pass

    def keep_focus(self, dt):
        if not self.input.focus:
            self.input.focus = True

    # ---- Синхронизация ----
    def initial_sync(self):
        """При старте: обновить кэш + отправить накопленную очередь."""
        ok_cache = refresh_cache()
        ok_sync, count = sync_offline_queue()

        if ok_cache:
            self.is_online = True
            if count > 0:
                msg = f"Онлайн • синхронизировано {count} записей"
            else:
                msg = "Онлайн • кэш обновлён"
        else:
            self.is_online = False
            msg = "Офлайн • работаем по кэшу"

        Clock.schedule_once(lambda dt: self.update_status(msg), 0)

    def background_sync(self):
        """Фоновая задача: раз в 60 сек пробуем обновить кэш и синхронизировать."""
        ok_cache = refresh_cache()
        ok_sync, count = sync_offline_queue()

        self.is_online = ok_cache

        if ok_cache:
            if count > 0:
                msg = f"Онлайн • синхронизировано {count}"
            else:
                q = get_queue_size()
                msg = "Онлайн • готов" if q == 0 else f"Онлайн • в очереди {q}"
        else:
            q = get_queue_size()
            msg = "Офлайн • готов" if q == 0 else f"Офлайн • в очереди {q}"

        Clock.schedule_once(lambda dt: self.update_status(msg), 0)

    def update_status(self, text):
        self.status_label.text = text

    # ---- Ввод ----
    def on_text_change(self, instance, text):
        if self.submit_timer:
            self.submit_timer.cancel()
            self.submit_timer = None
        if text.strip():
            self.submit_timer = Clock.schedule_once(self.auto_submit, AUTO_SUBMIT_DELAY)

    def auto_submit(self, dt):
        self.submit_timer = None
        if self.input.text.strip():
            self.on_scan(self.input)

    def on_scan(self, instance):
        if self.submit_timer:
            self.submit_timer.cancel()
            self.submit_timer = None

        scanned = instance.text.strip()
        instance.text = ''

        if not scanned:
            Clock.schedule_once(self.focus_input, 0.1)
            return

        if not re.match(r'^\d+$', scanned):
            self.show_result(False, f"Неверный код:\n{scanned}")
            return

        self.status_label.text = "Проверка..."
        threading.Thread(target=self.check_student, args=(scanned,), daemon=True).start()

    # ---- Проверка ----
    def check_student(self, student_id):
        # 1) Пытаемся онлайн
        try:
            r = requests.get(f"{SERVER_URL}/api/check/{student_id}", timeout=REQUEST_TIMEOUT)
            data = r.json()
            success = data.get('success', False)
            message = data.get('message', '')
            if success:
                save_local_meal(student_id)
            self.is_online = True
            Clock.schedule_once(lambda dt: self.show_result(success, message), 0)
            return
        except Exception:
            pass

        # 2) Офлайн-режим
        self.is_online = False
        success, message = check_student_offline(student_id)
        Clock.schedule_once(lambda dt: self.show_result(success, message), 0)

    # ---- Показ ----
    def show_result(self, success, message):
        if success:
            self.bg_color.rgba = (0.18, 0.49, 0.20, 1)
            self.result_label.text = f"✓\n\n{message}"
        else:
            self.bg_color.rgba = (0.78, 0.16, 0.16, 1)
            self.result_label.text = f"✗\n\n{message}"

        play_beep(success)
        Clock.unschedule(self.reset_display)
        Clock.schedule_once(self.reset_display, RESULT_DURATION)

    def reset_display(self, dt=0):
        self.bg_color.rgba = (0.12, 0.12, 0.12, 1)
        self.result_label.text = "Наведите сканер\nна QR-код"

        if self.is_online:
            q = get_queue_size()
            self.status_label.text = "Онлайн • готов" if q == 0 else f"Онлайн • в очереди {q}"
        else:
            q = get_queue_size()
            self.status_label.text = "Офлайн • готов" if q == 0 else f"Офлайн • в очереди {q}"

        self.input.text = ''
        self.focus_input()


# ============================================================
# ПРИЛОЖЕНИЕ
# ============================================================
class CanteenScannerApp(App):
    def build(self):
        Window.clearcolor = (0.12, 0.12, 0.12, 1)
        init_local_db()
        cleanup_old_local_meals()
        return ScannerScreen()


if __name__ == '__main__':
    CanteenScannerApp().run()
