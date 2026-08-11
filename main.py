# -*- coding: utf-8 -*-
"""
Android Solar Radiation Collector (Kivy Version) - Final with Diagnostics & UI Improvements
Last updated: 2026.08.11
"""

import os
import sys
import traceback
import time
import threading
import csv
import tempfile
import urllib.request
from datetime import datetime
import configparser

# ---- Global exception handler ----
def global_exception_handler(exc_type, exc_value, exc_tb):
    error_msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
    try:
        from kivy.app import App
        app = App.get_running_app()
        if app:
            log_dir = app.user_data_dir
        else:
            log_dir = tempfile.gettempdir()
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, 'app_crash.log'), 'a', encoding='utf-8') as f:
            f.write(f"\n--- Crash at {datetime.now()} ---\n{error_msg}\n")
    except:
        pass
    print(error_msg)
    sys.__excepthook__(exc_type, exc_value, exc_tb)

sys.excepthook = global_exception_handler
# ---------------------------------------------

import requests
from geopy.geocoders import Nominatim

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.progressbar import ProgressBar
from kivy.uix.popup import Popup
from kivy.uix.widget import Widget
from kivy.graphics import Color, Line
from kivy.clock import Clock, mainthread
from kivy.utils import platform
from kivy.core.text import LabelBase
from kivy.config import Config
from kivy.uix.checkbox import CheckBox

# ==============================================================

# -------- Utility: internal storage and proxy detection --------
def get_app_data_dir():
    app = App.get_running_app()
    if app:
        return app.user_data_dir
    else:
        return os.path.expanduser('~/.solar_collector_data')

def detect_proxy_enhanced():
    proxies = {}
    http_proxy = os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy')
    https_proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
    if http_proxy:
        proxies['http'] = http_proxy
    if https_proxy:
        proxies['https'] = https_proxy
    if not proxies:
        system_proxies = urllib.request.getproxies()
        for k, v in system_proxies.items():
            if k in ('http', 'https'):
                proxies[k] = v
    if platform == 'android':
        try:
            from jnius import autoclass
            Settings = autoclass('android.provider.Settings$Global')
            context = autoclass('org.kivy.android.PythonActivity').mActivity
            proxy_host = Settings.getString(context.getContentResolver(), 'http_proxy')
            if proxy_host and ':' in proxy_host:
                proxies['http'] = f"http://{proxy_host}"
                proxies['https'] = f"http://{proxy_host}"
        except:
            pass
    return proxies if proxies else None

# -------- Geocoding with cache --------
_geocode_cache = {}

def get_coordinates(address, retries=2):
    print(f"🗺️ Geocoding: {address}")
    if address in _geocode_cache:
        return _geocode_cache[address]
    try:
        geolocator = Nominatim(user_agent="solar_app_android", timeout=15)
        for attempt in range(retries):
            try:
                location = geolocator.geocode(address, timeout=15)
                if location:
                    result = (location.latitude, location.longitude, location.address)
                    _geocode_cache[address] = result
                    return result
            except Exception as e:
                print(f"⚠️ Attempt {attempt+1} failed: {e}")
                time.sleep(2 ** attempt)
            time.sleep(1)
    except Exception as e:
        print(f"❌ Geocoding error: {e}")
    return None, None, None

# -------- API data fetchers --------
def fetch_openmeteo_data(lat, lon, start_year, end_year, proxies=None, retries=3):
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": lat, "longitude": lon,
        "start_date": f"{start_year}-01-01", "end_date": f"{end_year}-12-31",
        "daily": "shortwave_radiation_sum", "timezone": "Asia/Shanghai"
    }
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=30, proxies=proxies)
            if resp.status_code == 200:
                data = resp.json()
                if 'daily' not in data:
                    continue
                daily = data['daily']
                yearly = {}
                for dt, val in zip(daily['time'], daily['shortwave_radiation_sum']):
                    if val is None:
                        continue
                    year = int(dt[:4])
                    yearly[year] = yearly.get(year, 0.0) + val
                result = []
                for y in range(start_year, end_year + 1):
                    if y in yearly:
                        ghi = yearly[y] / 1000.0   # 修正单位
                        result.append({'YEAR': y, 'GHI_kWh_m2_year': ghi})
                if len(result) >= 2:
                    return result
                else:
                    continue
            elif resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            else:
                break
        except requests.exceptions.Timeout:
            time.sleep(2 ** attempt)
            continue
        except Exception:
            time.sleep(2 ** attempt)
            continue
    return None

def fetch_nasa_data(lat, lon, start_year, end_year, proxies=None, retries=3):
    # NASA POWER 单位：kWh/m²/day，累加得到年总量
    url = "https://power.larc.nasa.gov/api/temporal/daily/point"
    params = {
        "parameters": "ALLSKY_SFC_SW_DWN", "community": "RE",
        "longitude": lon, "latitude": lat,
        "start": f"{start_year}0101", "end": f"{end_year}1231",
        "format": "JSON", "user": "pvuser"
    }
    headers = {'User-Agent': 'Mozilla/5.0'}
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=30, proxies=proxies)
            if resp.status_code == 200:
                data = resp.json()
                daily_data = data['properties']['parameter']['ALLSKY_SFC_SW_DWN']
                yearly = {}
                for d, v in daily_data.items():
                    if v is None:
                        continue
                    year = int(d[:4])
                    yearly[year] = yearly.get(year, 0.0) + v
                result = [{'YEAR': y, 'GHI_kWh_m2_year': yearly[y]} for y in sorted(yearly) if y >= start_year and y <= end_year]
                if result:
                    return result
            elif resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            else:
                break
        except requests.exceptions.Timeout:
            time.sleep(2 ** attempt)
            continue
        except Exception:
            time.sleep(2 ** attempt)
            continue
    return None

def fetch_pvgis_data(lat, lon, start_year, end_year, proxies=None, retries=3):
    return fetch_nasa_data(lat, lon, start_year, end_year, proxies, retries)

# -------- Statistics --------
def compute_statistics(data_list):
    if not data_list:
        return None
    vals = [d['GHI_kWh_m2_year'] for d in data_list]
    avg = sum(vals) / len(vals)
    std = (sum((x - avg) ** 2 for x in vals) / len(vals)) ** 0.5
    return {
        'avg': avg,
        'max': max(vals),
        'min': min(vals),
        'stability': (std / avg * 100) if avg > 0 else 0,
        'years': len(vals)
    }

# ========== Canvas Plot Widget ==========
class LineChartWidget(Widget):
    def __init__(self, x_values, y_values, title='', x_label='Year', y_label='GHI (kWh/m²)', **kwargs):
        super().__init__(**kwargs)
        self.x_values = x_values
        self.y_values = y_values
        self.title = title
        self.x_label = x_label
        self.y_label = y_label
        self._last_size = (0, 0)
        self.bind(pos=self._on_update, size=self._on_update)

    def _on_update(self, *args):
        new_size = (self.width, self.height)
        if abs(new_size[0] - self._last_size[0]) > 5 or abs(new_size[1] - self._last_size[1]) > 5:
            self._last_size = new_size
            self.redraw()

    def redraw(self, *args):
        self.canvas.clear()
        if not self.x_values or not self.y_values:
            return
        w, h = self.width, self.height
        margin = 40
        plot_w = w - 2 * margin
        plot_h = h - 2 * margin
        if plot_w <= 0 or plot_h <= 0:
            return

        x_min, x_max = min(self.x_values), max(self.x_values)
        y_min, y_max = min(self.y_values), max(self.y_values)
        y_range = y_max - y_min if y_max != y_min else 1.0
        x_range = x_max - x_min if x_max != x_min else 1.0

        with self.canvas:
            Color(0.9, 0.9, 0.9, 0.5)
            for i in range(6):
                y_frac = i / 5.0
                y_pos = margin + y_frac * plot_h
                Line(points=[margin, y_pos, w - margin, y_pos], width=1)

        points = []
        for x, y in zip(self.x_values, self.y_values):
            px = margin + ((x - x_min) / x_range) * plot_w
            py = margin + ((y - y_min) / y_range) * plot_h
            points.extend([px, py])
        with self.canvas:
            Color(0.12, 0.56, 1.0, 1)
            if len(points) >= 4:
                Line(points=points, width=2, close=False)
            for i in range(0, len(points), 2):
                Color(1, 0, 0, 1)
                Line(circle=(points[i], points[i+1], 5), width=2)

# =============================================

# -------- Login Screen --------
class LoginScreen(BoxLayout):
    def __init__(self, app, **kwargs):
        super().__init__(orientation='vertical', spacing=10, padding=20)
        self.app = app
        self.add_widget(Label(text='Solar Collector', font_size='24sp', size_hint_y=None, height=80))
        self.add_widget(Label(text='Server Address (local for offline)'))
        self.server_input = TextInput(text='local', multiline=False)
        self.add_widget(self.server_input)
        self.add_widget(Label(text='Username'))
        self.user_input = TextInput(text='admin', multiline=False)
        self.add_widget(self.user_input)
        self.add_widget(Label(text='Password'))
        self.pass_input = TextInput(password=True, multiline=False)
        self.add_widget(self.pass_input)

        remember_box = BoxLayout(size_hint_y=None, height=40, spacing=10)
        self.remember_cb = CheckBox(active=False)
        remember_box.add_widget(self.remember_cb)
        remember_box.add_widget(Label(text='Remember server & username'))
        self.add_widget(remember_box)

        self.login_btn = Button(text='Login', size_hint_y=None, height=50)
        self.login_btn.bind(on_press=self.do_login)
        self.add_widget(self.login_btn)
        self.status_label = Label(text='', size_hint_y=None, height=30)
        self.add_widget(self.status_label)

        self._load_config()

    def _load_config(self):
        try:
            config = configparser.ConfigParser()
            config_path = os.path.join(self.app.user_data_dir, 'config.ini')
            if os.path.exists(config_path):
                config.read(config_path, encoding='utf-8')
                if 'login' in config:
                    self.server_input.text = config['login'].get('server', 'local')
                    self.user_input.text = config['login'].get('username', 'admin')
                    self.remember_cb.active = config['login'].getboolean('remember', False)
        except:
            pass

    def _save_config(self):
        try:
            config = configparser.ConfigParser()
            config['login'] = {
                'server': self.server_input.text.strip(),
                'username': self.user_input.text.strip(),
                'remember': str(self.remember_cb.active)
            }
            config_path = os.path.join(self.app.user_data_dir, 'config.ini')
            os.makedirs(self.app.user_data_dir, exist_ok=True)
            with open(config_path, 'w', encoding='utf-8') as f:
                config.write(f)
        except:
            pass

    def do_login(self, instance):
        server = self.server_input.text.strip()
        user = self.user_input.text.strip()
        pwd = self.pass_input.text.strip()
        if server.lower() == 'local':
            if (user == 'admin' and pwd == '123456') or (user == 'test' and pwd == '123456'):
                self.status_label.text = '✅ Local login successful'
                self.app.token = 'fake_jwt_token'
                self.app.server_url = 'local'
                if self.remember_cb.active:
                    self._save_config()
                self.app.show_main_screen()
            else:
                self.status_label.text = '❌ Invalid account or password'
        else:
            try:
                resp = requests.post(f"{server}/login", json={'username': user, 'password': pwd}, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    token = data.get('access_token')
                    if token:
                        self.app.token = token
                        self.app.server_url = server
                        self.status_label.text = '✅ Login successful'
                        if self.remember_cb.active:
                            self._save_config()
                        self.app.show_main_screen()
                    else:
                        self.status_label.text = '❌ No token returned'
                else:
                    self.status_label.text = f'❌ Login failed ({resp.status_code})'
            except Exception as e:
                self.status_label.text = f'❌ Connection error: {str(e)}'

# -------- Main Screen (Enhanced) --------
class MainScreen(BoxLayout):
    def __init__(self, app, **kwargs):
        try:
            super().__init__(orientation='vertical', spacing=5, padding=5)
            self.app = app
            self._lock = threading.Lock()
            self.is_running = False
            self._geocode_cache = {}
            self.results = {}
            self.yearly = {}
            self.completed = 0
            self.total_tasks = 0
            self.proxies = detect_proxy_enhanced()
            self.error_summary = []   # 记录失败详情
            self.last_rows = []       # 用于重试
            self.last_start = 2010
            self.last_end = 2025

            # ---------- 参数输入行（改为两行，更紧凑） ----------
            param_box = BoxLayout(orientation='vertical', size_hint_y=None, height=120, spacing=3)
            row1 = BoxLayout(spacing=5)
            row1.add_widget(Label(text='Start Year:', size_hint_x=0.3))
            self.start_year = TextInput(text='2010', multiline=False, input_filter='int', size_hint_x=0.7)
            row1.add_widget(self.start_year)
            row1.add_widget(Label(text='End Year:', size_hint_x=0.3))
            self.end_year = TextInput(text='2025', multiline=False, input_filter='int', size_hint_x=0.7)
            row1.add_widget(self.end_year)
            param_box.add_widget(row1)

            row2 = BoxLayout(spacing=5)
            row2.add_widget(Label(text='Chart Type:', size_hint_x=0.3))
            self.chart_type = TextInput(text='line', multiline=False, size_hint_x=0.7)
            row2.add_widget(self.chart_type)
            # 占位控件保持对齐
            row2.add_widget(Widget(size_hint_x=0.3))
            row2.add_widget(Widget(size_hint_x=0.7))
            param_box.add_widget(row2)
            self.add_widget(param_box)

            # ---------- 表格区域 ----------
            self.table_container = ScrollView(size_hint_y=0.25)  # 占用25%高度
            self.table_grid = GridLayout(cols=4, size_hint_y=None, spacing=2, row_default_height=40)
            self.table_grid.bind(minimum_height=self.table_grid.setter('height'))
            for h in ['Address', 'Latitude', 'Longitude', 'Contract']:
                self.table_grid.add_widget(Label(text=h, size_hint_x=0.25, bold=True, font_size='12sp'))
            self.add_table_row('Shanghai, China', '', '', '1500')
            self.table_container.add_widget(self.table_grid)
            self.add_widget(self.table_container)

            # ---------- 按钮行（分两行，避免拥挤） ----------
            btn_row1 = BoxLayout(size_hint_y=None, height=50, spacing=5)
            add_btn = Button(text='Add Row')
            add_btn.bind(on_press=self.add_row)
            del_btn = Button(text='Delete Last')
            del_btn.bind(on_press=self.del_row)
            clear_btn = Button(text='Clear All')
            clear_btn.bind(on_press=self.clear_all)
            load_btn = Button(text='Import CSV')
            load_btn.bind(on_press=self.load_csv)
            save_btn = Button(text='Export CSV')
            save_btn.bind(on_press=self.save_csv)
            btn_row1.add_widget(add_btn)
            btn_row1.add_widget(del_btn)
            btn_row1.add_widget(clear_btn)
            btn_row1.add_widget(load_btn)
            btn_row1.add_widget(save_btn)
            self.add_widget(btn_row1)

            btn_row2 = BoxLayout(size_hint_y=None, height=50, spacing=5)
            self.start_btn = Button(text='Start', background_color=(0.2,0.7,0.2,1))
            self.start_btn.bind(on_press=self.start_processing)
            self.stop_btn = Button(text='Stop', background_color=(1,0.2,0.2,1))
            self.stop_btn.bind(on_press=self.stop_processing)
            self.retry_btn = Button(text='Retry All', background_color=(0.8,0.6,0.2,1))
            self.retry_btn.bind(on_press=self.retry_all)
            self.test_btn = Button(text='Network Test', background_color=(0.3,0.5,0.8,1))
            self.test_btn.bind(on_press=self.network_test)
            btn_row2.add_widget(self.start_btn)
            btn_row2.add_widget(self.stop_btn)
            btn_row2.add_widget(self.retry_btn)
            btn_row2.add_widget(self.test_btn)
            self.add_widget(btn_row2)

            # ---------- 进度条 ----------
            self.progress = ProgressBar(max=100, value=0, size_hint_y=None, height=20)
            self.add_widget(self.progress)

            # ---------- 日志区域（可滚动，设置滚动条样式） ----------
            log_box = BoxLayout(orientation='vertical', size_hint_y=0.3, spacing=2)
            log_box.add_widget(Label(text='Log Output:', size_hint_y=None, height=20, font_size='12sp'))
            self.log_text = TextInput(text='', readonly=True, multiline=True, halign='left', font_size='11sp')
            log_scroll = ScrollView(bar_width=10, bar_color=[0.5,0.5,0.5,1])
            log_scroll.add_widget(self.log_text)
            log_box.add_widget(log_scroll)
            clear_log_btn = Button(text='Clear Log', size_hint_y=None, height=30)
            clear_log_btn.bind(on_press=self.clear_log)
            log_box.add_widget(clear_log_btn)
            self.add_widget(log_box)

            # ---------- 图表区域 ----------
            self.chart_container = ScrollView(size_hint_y=0.4, bar_width=10, bar_color=[0.5,0.5,0.5,1])
            self.chart_box = BoxLayout(orientation='vertical', size_hint_y=None)
            self.chart_box.bind(minimum_height=self.chart_box.setter('height'))
            self.chart_container.add_widget(self.chart_box)
            self.add_widget(self.chart_container)

            self._update_log(f'Proxy: {self.proxies if self.proxies else "None (direct)"}')
        except Exception as e:
            error_text = f"MainScreen init error:\n{traceback.format_exc()}"
            self.app._write_startup_log(error_text)
            self.clear_widgets()
            self.add_widget(Label(text=error_text, color=(1,0,0,1)))

    # ---- Table operations ----
    def add_table_row(self, addr='', lat='', lon='', contract=''):
        self.table_grid.add_widget(TextInput(text=addr, multiline=False, font_size='12sp'))
        self.table_grid.add_widget(TextInput(text=lat, multiline=False, font_size='12sp'))
        self.table_grid.add_widget(TextInput(text=lon, multiline=False, font_size='12sp'))
        self.table_grid.add_widget(TextInput(text=contract, multiline=False, font_size='12sp'))

    def add_row(self, instance):
        self.add_table_row()

    def del_row(self, instance):
        children = self.table_grid.children
        if len(children) > 4:
            for _ in range(4):
                self.table_grid.remove_widget(children[0])
        else:
            self._update_log('⚠️ Cannot delete last data row')

    def clear_all(self, instance):
        self.table_grid.clear_widgets()
        for h in ['Address', 'Latitude', 'Longitude', 'Contract']:
            self.table_grid.add_widget(Label(text=h, size_hint_x=0.25, bold=True, font_size='12sp'))

    def load_csv(self, instance):
        pass  # stub

    def save_csv(self, instance):
        self._export_csv()

    def clear_log(self, instance):
        self.log_text.text = ''
        self.error_summary.clear()

    # ---- Stop / Retry / Network Test ----
    def stop_processing(self, instance):
        if self.is_running:
            self.is_running = False
            self._update_log('🛑 Stop requested...')
        else:
            self._update_log('⚠️ No task running.')

    def retry_all(self, instance):
        if not self.last_rows:
            self._update_log('⚠️ No previous task to retry.')
            return
        if self.is_running:
            self._update_log('⚠️ Task already running, stop first.')
            return
        # 复用上次参数
        self.start_year.text = str(self.last_start)
        self.end_year.text = str(self.last_end)
        self._update_log('🔄 Retrying last task...')
        # 直接调用处理，但先清空错误记录
        self.error_summary.clear()
        self._start_processing_from_rows(self.last_rows, self.last_start, self.last_end)

    def network_test(self, instance):
        self._update_log('🌐 Starting network test...')
        def test():
            endpoints = [
                ('Open-Meteo', 'https://archive-api.open-meteo.com/v1/archive', {'latitude':31.23, 'longitude':121.47, 'start_date':'2020-01-01', 'end_date':'2020-01-02', 'daily':'shortwave_radiation_sum', 'timezone':'Asia/Shanghai'}),
                ('NASA POWER', 'https://power.larc.nasa.gov/api/temporal/daily/point', {'parameters':'ALLSKY_SFC_SW_DWN','community':'RE','longitude':121.47,'latitude':31.23,'start':'20200101','end':'20200102','format':'JSON','user':'pvuser'}),
                ('PVGIS (fallback to NASA)', 'https://power.larc.nasa.gov/api/temporal/daily/point', {'parameters':'ALLSKY_SFC_SW_DWN','community':'RE','longitude':121.47,'latitude':31.23,'start':'20200101','end':'20200102','format':'JSON','user':'pvuser'})
            ]
            for name, url, params in endpoints:
                try:
                    if 'open-meteo' in url:
                        resp = requests.get(url, params=params, timeout=10, proxies=self.proxies)
                    else:
                        resp = requests.get(url, params=params, timeout=10, proxies=self.proxies, headers={'User-Agent':'Mozilla/5.0'})
                    if resp.status_code == 200:
                        self._update_log(f'✅ {name} reachable (status {resp.status_code})')
                    else:
                        self._update_log(f'❌ {name} returned {resp.status_code}')
                except Exception as e:
                    self._update_log(f'❌ {name} error: {str(e)}')
            self._update_log('🏁 Network test finished.')
        threading.Thread(target=test, daemon=True).start()

    # ---- Processing ----
    def start_processing(self, instance):
        if self.is_running:
            self._update_log('⚠️ Already running.')
            return
        # 收集地址
        children = self.table_grid.children
        if len(children) <= 4:
            self._update_log('❌ Please enter at least one address')
            return
        rows = []
        items = list(children)[:-4]
        for i in range(0, len(items), 4):
            addr = items[i].text.strip()
            lat = items[i+1].text.strip()
            lon = items[i+2].text.strip()
            contract = items[i+3].text.strip()
            if not addr:
                continue
            try:
                lat_val = float(lat) if lat else None
            except:
                lat_val = None
            try:
                lon_val = float(lon) if lon else None
            except:
                lon_val = None
            try:
                contract_val = float(contract) if contract else None
            except:
                contract_val = None
            rows.append((addr, lat_val, lon_val, contract_val))
        if not rows:
            self._update_log('❌ No valid addresses')
            return
        # 验证年份
        try:
            start = int(self.start_year.text.strip())
            end = int(self.end_year.text.strip())
            if start > end or start < 1900 or end > 2100:
                self._update_log('❌ Invalid year range.')
                return
        except:
            self._update_log('❌ Invalid year format.')
            return

        # 保存用于重试
        self.last_rows = rows[:]
        self.last_start = start
        self.last_end = end
        self.error_summary.clear()
        self._start_processing_from_rows(rows, start, end)

    def _start_processing_from_rows(self, rows, start, end):
        with self._lock:
            if self.is_running:
                return
            self.is_running = True

        self.results.clear()
        self.yearly.clear()
        self.chart_box.clear_widgets()
        self.completed = 0
        self.total_tasks = len(rows)
        self.progress.value = 0
        self._update_log(f'🚀 Processing {len(rows)} address(es) from {start} to {end}...')
        threading.Thread(target=self._process_serial, args=(rows, start, end), daemon=True).start()

    def _process_serial(self, rows, start_year, end_year):
        any_success = False
        for idx, (addr, lat, lon, contract) in enumerate(rows, 1):
            if not self.is_running:
                self._update_log('⏹️ Stopped by user.')
                break

            # Geocode
            if lat is None or lon is None:
                lat, lon, _ = self._geocode_address(addr)
                if lat is None:
                    err_msg = f'Geocoding failed for {addr}'
                    self.error_summary.append(err_msg)
                    self._update_log(f'❌ {err_msg}, skipped')
                    self._update_progress(1)
                    continue

            sources = [
                ('Open-Meteo', fetch_openmeteo_data),
                ('NASA POWER', fetch_nasa_data),
                ('PVGIS', fetch_pvgis_data)
            ]
            addr_charts = []
            for src_name, fetch_func in sources:
                if not self.is_running:
                    break
                self._update_log(f'[{addr}] Fetching {src_name}...')
                data = fetch_func(lat, lon, start_year, end_year, proxies=self.proxies)
                if not data:
                    err_msg = f'{src_name} failed for {addr}'
                    self.error_summary.append(err_msg)
                    self._update_log(f'❌ {err_msg}')
                    continue
                stats = compute_statistics(data)
                if stats is None:
                    err_msg = f'{src_name} returned invalid data for {addr}'
                    self.error_summary.append(err_msg)
                    self._update_log(f'❌ {err_msg}')
                    continue
                any_success = True
                Clock.schedule_once(lambda dt, a=addr, s=src_name, st=stats, d=data: self._store_data(a, s, st, d), 0)
                years = [d['YEAR'] for d in data]
                ghi = [d['GHI_kWh_m2_year'] for d in data]
                addr_charts.append((src_name, years, ghi, stats))
                self._update_log(f'[{addr}] {src_name} completed')
            if addr_charts:
                Clock.schedule_once(lambda dt, a=addr, charts=addr_charts: self._display_charts(a, charts), 0)
            else:
                self._update_log(f'❌ All sources failed for {addr}')
                Clock.schedule_once(lambda dt, a=addr: self._show_error_popup(a), 0)

            self._update_progress(1)
            time.sleep(0.5)

        self._update_log('🎉 All tasks completed!')
        with self._lock:
            self.is_running = False

        if not any_success or not self.yearly:
            Clock.schedule_once(lambda dt: self._show_no_data_popup(), 0)
        else:
            self._export_csv()

    # ---- Geocoding ----
    def _geocode_address(self, addr):
        if addr in self._geocode_cache:
            return self._geocode_cache[addr]
        lat, lon, full = get_coordinates(addr)
        if lat is not None:
            self._geocode_cache[addr] = (lat, lon, full)
        return lat, lon, full

    # ---- Popups ----
    def _show_error_popup(self, addr):
        popup = Popup(title='Collection Failed',
                      content=Label(text=f'All data sources failed for:\n{addr}\nCheck network or address.'),
                      size_hint=(0.8, 0.5))
        popup.open()

    def _show_no_data_popup(self):
        # 显示前几条错误
        err_text = "No valid data collected.\n"
        if self.error_summary:
            err_text += "\nRecent errors:\n" + "\n".join(self.error_summary[-5:])
        else:
            err_text += "Please check network settings and try again."
        popup = Popup(title='No Data Collected',
                      content=Label(text=err_text, halign='left', valign='top'),
                      size_hint=(0.9, 0.6))
        popup.open()

    # ---- Data storage & display ----
    def _store_data(self, addr, src_name, stats, data):
        if addr not in self.results:
            self.results[addr] = {}
            self.yearly[addr] = {}
        self.results[addr][src_name] = stats
        self.yearly[addr][src_name] = data

    def _display_charts(self, addr, charts):
        title_label = Label(text=f'📍 {addr}', size_hint_y=None, height=35, bold=True, font_size='14sp')
        self.chart_box.add_widget(title_label)
        for src_name, years, ghi, stats in charts:
            src_label = Label(text=f'📊 {src_name}', size_hint_y=None, height=25, font_size='12sp')
            self.chart_box.add_widget(src_label)
            chart_widget = LineChartWidget(x_values=years, y_values=ghi, size_hint_y=None, height=180)
            self.chart_box.add_widget(chart_widget)
            info = (f"Avg: {stats['avg']:.1f}   Max: {stats['max']:.1f}   Min: {stats['min']:.1f}   "
                    f"Stability: {stats['stability']:.1f}%   Years: {stats['years']}")
            info_label = Label(text=info, size_hint_y=None, height=20, font_size='11sp')
            self.chart_box.add_widget(info_label)
        # 调整高度
        total_height = sum(child.height for child in self.chart_box.children if hasattr(child, 'height'))
        total_height += 10 * len(self.chart_box.children)
        self.chart_box.height = max(total_height, 100)

    # ---- Logging & Progress ----
    @mainthread
    def _update_log(self, msg):
        self.log_text.text += f'\n{msg}'
        self.log_text.cursor = (0, len(self.log_text.text))

    @mainthread
    def _update_progress(self, step):
        self.completed += step
        if self.total_tasks > 0:
            self.progress.value = min(100, int(self.completed / self.total_tasks * 100))

    # ---- CSV Export ----
    def _export_csv(self):
        if not self.yearly:
            self._update_log('⚠️ No data to export')
            popup = Popup(title='Export Failed',
                          content=Label(text='No data available to export.'),
                          size_hint=(0.8, 0.5))
            popup.open()
            return

        records = []
        for addr, sources in self.yearly.items():
            for src, data_list in sources.items():
                for d in data_list:
                    records.append({
                        'Address': addr,
                        'Data Source': src,
                        'Year': d['YEAR'],
                        'GHI (kWh/m2/yr)': d['GHI_kWh_m2_year']
                    })
        if not records:
            return
        data_dir = self.app.user_data_dir
        os.makedirs(data_dir, exist_ok=True)
        time_str = datetime.now().strftime("%m%d_%H%M")
        filename = f"solar_data_{time_str}.csv"
        filepath = os.path.join(data_dir, filename)
        try:
            with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=records[0].keys())
                writer.writeheader()
                writer.writerows(records)
            self._update_log(f'✅ CSV exported: {filepath}')
            if platform == 'android':
                self._share_file_android(filepath)
            else:
                popup = Popup(title='Export Complete',
                              content=Label(text=f'CSV saved to:\n{filepath}'),
                              size_hint=(0.8, 0.5))
                popup.open()
        except Exception as e:
            self._update_log(f'❌ Export failed: {e}')

    def _share_file_android(self, filepath):
        try:
            from jnius import autoclass
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            Intent = autoclass('android.content.Intent')
            Uri = autoclass('android.net.Uri')
            File = autoclass('java.io.File')
            context = PythonActivity.mActivity
            file_obj = File(filepath)
            uri = Uri.fromFile(file_obj)
            intent = Intent(Intent.ACTION_SEND)
            intent.setType('text/csv')
            intent.putExtra(Intent.EXTRA_STREAM, uri)
            chooser = Intent.createChooser(intent, 'Share CSV')
            context.startActivity(chooser)
        except Exception as e:
            self._update_log(f'Share failed: {e}')

# -------- App Class --------
class SolarApp(App):
    def build(self):
        self._write_startup_log("App starting...")
        # 字体加载
        font_dirs = ['/system/fonts/', '/system/fonts/fallback/']
        font_files = ['DroidSansFallback.ttf', 'NotoSansCJK-Regular.ttc', 'NotoSansSC-Regular.otf',
                      'NotoSansCJKsc-Regular.otf', 'NotoSansCJK.ttc', 'NotoSans-Regular.ttf']
        font_loaded = False
        for dir_path in font_dirs:
            for fname in font_files:
                full_path = os.path.join(dir_path, fname)
                if os.path.exists(full_path):
                    try:
                        LabelBase.register(name='SystemFont', fn_regular=full_path)
                        Config.set('kivy', 'default_font', ['SystemFont'])
                        self._write_startup_log(f"✅ System font loaded: {full_path}")
                        font_loaded = True
                        break
                    except Exception as e:
                        self._write_startup_log(f"⚠️ Registering {full_path} failed: {e}")
                        continue
            if font_loaded:
                break
        if not font_loaded:
            self._write_startup_log("❌ All system fonts failed, using Kivy default")

        # 权限
        if platform == 'android':
            try:
                from android.permissions import request_permissions, Permission
                request_permissions([Permission.INTERNET, Permission.READ_EXTERNAL_STORAGE, Permission.WRITE_EXTERNAL_STORAGE])
                self._write_startup_log("Permissions requested")
            except Exception as e:
                self._write_startup_log(f"Permission request error: {e}")

        self.token = None
        self.server_url = None
        self.login_screen = LoginScreen(self)
        return self.login_screen

    def _write_startup_log(self, msg):
        try:
            log_path = os.path.join(self.user_data_dir, 'startup.log')
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(f"{datetime.now()}: {msg}\n")
        except:
            pass

    def show_main_screen(self):
        try:
            self.main_screen = MainScreen(self)
            self.root.clear_widgets()
            self.root.add_widget(self.main_screen)
        except Exception as e:
            error_text = f"Error showing main screen:\n{traceback.format_exc()}"
            self._write_startup_log(error_text)
            self.root.clear_widgets()
            self.root.add_widget(Label(text=error_text, color=(1,0,0,1)))

if __name__ == '__main__':
    SolarApp().run()
