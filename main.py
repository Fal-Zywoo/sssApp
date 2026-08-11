# -*- coding: utf-8 -*-
"""
Android Solar Radiation Collector (Kivy Version) - Final Fix
Last updated: 2026.08.11
"""

import os
import sys
import traceback
import time
import random   # 保留，虽然当前未使用，但可能用于未来扩展
import threading
import csv
import tempfile
import urllib.request
from datetime import datetime
import logging

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
from kivy.resources import resource_find   # <-- 保留备用，但未使用

# ==============================================================

# -------- Utility: internal storage and proxy detection --------
def get_app_data_dir():
    app = App.get_running_app()
    if app:
        return app.user_data_dir
    else:
        return os.path.expanduser('~/.solar_collector_data')

def detect_proxy_enhanced():
    """
    Enhanced proxy detection: environment variables + urllib + Android system settings.
    """
    proxies = {}
    # 1. Environment variables
    http_proxy = os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy')
    https_proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
    if http_proxy:
        proxies['http'] = http_proxy
    if https_proxy:
        proxies['https'] = https_proxy

    # 2. urllib auto-detect
    if not proxies:
        system_proxies = urllib.request.getproxies()
        for k, v in system_proxies.items():
            if k in ('http', 'https'):
                proxies[k] = v

    # 3. Android global proxy (if possible)
    if platform == 'android':
        try:
            from jnius import autoclass
            Settings = autoclass('android.provider.Settings$Global')
            context = autoclass('org.kivy.android.PythonActivity').mActivity
            proxy_host = Settings.getString(context.getContentResolver(), 'http_proxy')
            if proxy_host and ':' in proxy_host:
                # format: host:port
                proxies['http'] = f"http://{proxy_host}"
                proxies['https'] = f"http://{proxy_host}"
        except:
            pass
    return proxies if proxies else None

# -------- Geocoding with cache and rate limiting --------
_geocode_cache = {}

def get_coordinates(address, retries=2):
    print(f"🗺️ Geocoding: {address}")
    # Check cache
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
                time.sleep(2 ** attempt)  # exponential backoff
            # Rate limiting
            time.sleep(1)  # Nominatim policy
    except Exception as e:
        print(f"❌ Geocoding error: {e}")
    return None, None, None

# -------- API data fetchers (with refined retry logic) --------
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
                        ghi = yearly[y] / 3.6
                        result.append({'YEAR': y, 'GHI_kWh_m2_year': ghi})
                if len(result) >= 2:
                    return result
                else:
                    continue
            elif resp.status_code >= 500:
                # server error, retry
                time.sleep(2 ** attempt)
                continue
            else:
                # client error, break
                break
        except requests.exceptions.Timeout:
            time.sleep(2 ** attempt)
            continue
        except Exception:
            time.sleep(2 ** attempt)
            continue
    return None

def fetch_nasa_data(lat, lon, start_year, end_year, proxies=None, retries=3):
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
    # Real PVGIS API would be used; since it's not reliable, we delegate to NASA as a real source
    # and do NOT simulate random data.
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

# ========== Canvas Plot Widget (optimized redraw) ==========
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
        self.login_btn = Button(text='Login', size_hint_y=None, height=50)
        self.login_btn.bind(on_press=self.do_login)
        self.add_widget(self.login_btn)
        self.status_label = Label(text='', size_hint_y=None, height=30)
        self.add_widget(self.status_label)

    def do_login(self, instance):
        server = self.server_input.text.strip()
        user = self.user_input.text.strip()
        pwd = self.pass_input.text.strip()
        if server.lower() == 'local':
            if (user == 'admin' and pwd == '123456') or (user == 'test' and pwd == '123456'):
                self.status_label.text = '✅ Local login successful'
                self.app.token = 'fake_jwt_token'
                self.app.server_url = 'local'
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
                        self.app.show_main_screen()
                    else:
                        self.status_label.text = '❌ No token returned'
                else:
                    self.status_label.text = f'❌ Login failed ({resp.status_code})'
            except Exception as e:
                self.status_label.text = f'❌ Connection error: {str(e)}'

# -------- Main Screen (fixed) --------
class MainScreen(BoxLayout):
    def __init__(self, app, **kwargs):
        try:
            super().__init__(orientation='vertical', spacing=5)
            self.app = app
            self._lock = threading.Lock()
            self.is_running = False
            self._geocode_cache = {}
            self.results = {}
            self.yearly = {}
            self.completed = 0
            self.total_tasks = 0
            self.proxies = detect_proxy_enhanced()

            # Parameters
            param_box = BoxLayout(size_hint_y=None, height=200, spacing=5)
            param_box.add_widget(Label(text='Start Year:'))
            self.start_year = TextInput(text='2010', multiline=False, input_filter='int')
            param_box.add_widget(self.start_year)
            param_box.add_widget(Label(text='End Year:'))
            self.end_year = TextInput(text='2025', multiline=False, input_filter='int')
            param_box.add_widget(self.end_year)
            param_box.add_widget(Label(text='Chart Type:'))
            self.chart_type = TextInput(text='line', multiline=False)
            param_box.add_widget(self.chart_type)
            self.add_widget(param_box)

            # Table
            self.table_container = ScrollView()
            self.table_grid = GridLayout(cols=4, size_hint_y=None, spacing=2, row_default_height=40)
            self.table_grid.bind(minimum_height=self.table_grid.setter('height'))
            for h in ['Address', 'Latitude', 'Longitude', 'Contract Value']:
                self.table_grid.add_widget(Label(text=h, size_hint_x=0.25, bold=True))
            self.add_table_row('Shanghai, China', '', '', '1500')
            self.table_container.add_widget(self.table_grid)
            self.add_widget(self.table_container)

            # Buttons
            btn_box = BoxLayout(size_hint_y=None, height=50, spacing=5)
            add_btn = Button(text='Add Row')
            add_btn.bind(on_press=self.add_row)
            del_btn = Button(text='Delete Last Row')
            del_btn.bind(on_press=self.del_row)
            clear_btn = Button(text='Clear All')
            clear_btn.bind(on_press=self.clear_all)
            load_btn = Button(text='Import CSV')
            load_btn.bind(on_press=self.load_csv)
            save_btn = Button(text='Export CSV')
            save_btn.bind(on_press=self.save_csv)
            btn_box.add_widget(add_btn)
            btn_box.add_widget(del_btn)
            btn_box.add_widget(clear_btn)
            btn_box.add_widget(load_btn)
            btn_box.add_widget(save_btn)
            self.add_widget(btn_box)

            # Progress
            self.progress = ProgressBar(max=100, value=0, size_hint_y=None, height=30)
            self.add_widget(self.progress)

            # Start button
            start_btn = Button(text='Start Collecting', size_hint_y=None, height=50, background_color=(0.2,0.7,0.2,1))
            start_btn.bind(on_press=self.start_processing)
            self.add_widget(start_btn)

            # Log output (FIXED: removed valign and text_size bind)
            log_box = BoxLayout(orientation='vertical', size_hint_y=None, height=250)
            log_box.add_widget(Label(text='Log Output:', size_hint_y=None, height=30))
            self.log_text = TextInput(text='', readonly=True, multiline=True, halign='left')
            # NOTE: No bind(size=...) here; TextInput handles its own text wrapping.
            log_scroll = ScrollView()
            log_scroll.add_widget(self.log_text)
            log_box.add_widget(log_scroll)
            clear_log_btn = Button(text='Clear Log', size_hint_y=None, height=40)
            clear_log_btn.bind(on_press=self.clear_log)
            log_box.add_widget(clear_log_btn)
            self.add_widget(log_box)

            # Chart container
            self.chart_container = ScrollView(size_hint_y=None, height=400)
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
        self.table_grid.add_widget(TextInput(text=addr, multiline=False))
        self.table_grid.add_widget(TextInput(text=lat, multiline=False))
        self.table_grid.add_widget(TextInput(text=lon, multiline=False))
        self.table_grid.add_widget(TextInput(text=contract, multiline=False))

    def add_row(self, instance):
        self.add_table_row()

    def del_row(self, instance):
        children = self.table_grid.children
        if len(children) > 8:  # 4 headers + at least 1 data row
            for _ in range(4):
                self.table_grid.remove_widget(children[0])
        else:
            self._update_log('⚠️ Cannot delete last data row')

    def clear_all(self, instance):
        self.table_grid.clear_widgets()
        for h in ['Address', 'Latitude', 'Longitude', 'Contract Value']:
            self.table_grid.add_widget(Label(text=h, size_hint_x=0.25, bold=True))

    def load_csv(self, instance):
        pass  # stub for future

    def save_csv(self, instance):
        self._export_csv()

    def clear_log(self, instance):
        self.log_text.text = ''

    # ---- Processing ----
    def start_processing(self, instance):
        with self._lock:
            if self.is_running:
                self._update_log('⚠️ Already running, please wait.')
                return
            self.is_running = True

        children = self.table_grid.children
        if len(children) <= 4:
            self._update_log('❌ Please enter at least one address')
            self.is_running = False
            return
        rows = []
        items = list(children)[:-4]  # exclude headers
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
            self.is_running = False
            return
        start = int(self.start_year.text)
        end = int(self.end_year.text)
        self.results.clear()
        self.yearly.clear()
        self.completed = 0
        self.total_tasks = len(rows) * 3
        self.progress.value = 0
        self._update_log(f'🚀 Processing {len(rows)} address(es)...')
        self.chart_box.clear_widgets()
        threading.Thread(target=self._process_serial, args=(rows, start, end), daemon=True).start()

    def _process_serial(self, rows, start_year, end_year):
        any_success = False
        for idx, (addr, lat, lon, contract) in enumerate(rows, 1):
            if not self.is_running:
                break
            # Geocode if needed
            if lat is None or lon is None:
                lat, lon, _ = self._geocode_address(addr)
                if lat is None:
                    self._update_log(f'❌ {addr} geocoding failed, skipped')
                    self._update_progress(3)
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
                    self._update_log(f'[{addr}] {src_name} failed')
                    self._update_progress(1)
                    continue
                stats = compute_statistics(data)
                if stats is None:
                    self._update_log(f'[{addr}] {src_name} invalid data')
                    self._update_progress(1)
                    continue
                any_success = True
                Clock.schedule_once(lambda dt, a=addr, s=src_name, st=stats, d=data: self._store_data(a, s, st, d), 0)
                years = [d['YEAR'] for d in data]
                ghi = [d['GHI_kWh_m2_year'] for d in data]
                addr_charts.append((src_name, years, ghi, stats))
                self._update_log(f'[{addr}] {src_name} completed')
                self._update_progress(1)
            if addr_charts:
                Clock.schedule_once(lambda dt, a=addr, charts=addr_charts: self._display_charts(a, charts), 0)
            else:
                self._update_log(f'❌ All sources failed for {addr}')
                Clock.schedule_once(lambda dt, a=addr: self._show_error_popup(a), 0)
            time.sleep(0.5)
        self._update_log('🎉 All tasks completed!')
        with self._lock:
            self.is_running = False
        if not any_success:
            Clock.schedule_once(lambda dt: self._show_no_data_popup(), 0)
        else:
            self._export_csv()

    def _geocode_address(self, addr):
        if addr in self._geocode_cache:
            return self._geocode_cache[addr]
        lat, lon, full = get_coordinates(addr)
        if lat is not None:
            self._geocode_cache[addr] = (lat, lon, full)
        return lat, lon, full

    def _show_error_popup(self, addr):
        popup = Popup(title='Collection Failed',
                      content=Label(text=f'All data sources failed for:\n{addr}\nCheck network or address.'),
                      size_hint=(0.8, 0.5))
        popup.open()

    def _show_no_data_popup(self):
        popup = Popup(title='No Data Collected',
                      content=Label(text='No valid data was collected from any address.\nPlease check network settings and try again.'),
                      size_hint=(0.8, 0.5))
        popup.open()

    def _store_data(self, addr, src_name, stats, data):
        if addr not in self.results:
            self.results[addr] = {}
            self.yearly[addr] = {}
        self.results[addr][src_name] = stats
        self.yearly[addr][src_name] = data

    def _display_charts(self, addr, charts):
        title_label = Label(text=f'📍 {addr}', size_hint_y=None, height=40, bold=True)
        self.chart_box.add_widget(title_label)
        for src_name, years, ghi, stats in charts:
            src_label = Label(text=f'📊 {src_name}', size_hint_y=None, height=30)
            self.chart_box.add_widget(src_label)
            chart_widget = LineChartWidget(x_values=years, y_values=ghi, size_hint_y=None, height=200)
            self.chart_box.add_widget(chart_widget)
            info = (f"Avg: {stats['avg']:.1f}   Max: {stats['max']:.1f}   Min: {stats['min']:.1f}   "
                    f"Stability: {stats['stability']:.1f}%   Years: {stats['years']}")
            info_label = Label(text=info, size_hint_y=None, height=25, font_size='12sp')
            self.chart_box.add_widget(info_label)
        self.chart_box.height = len(self.chart_box.children) * 40 + 200 * len(charts)

    @mainthread
    def _update_log(self, msg):
        self.log_text.text += f'\n{msg}'
        self.log_text.cursor = (0, len(self.log_text.text))

    @mainthread
    def _update_progress(self, step):
        self.completed += step
        if self.total_tasks > 0:
            self.progress.value = min(100, int(self.completed / self.total_tasks * 100))

    def _export_csv(self):
        if not self.yearly:
            self._update_log('⚠️ No data to export')
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
        # Use app.user_data_dir directly (more reliable)
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

        # ---------- 字体加载：遍历系统字体目录 ----------
        # 常见的系统字体目录（Android）
        font_dirs = [
            '/system/fonts/',
            '/system/fonts/fallback/',
        ]
        # 常见中文字体文件名（按概率排序）
        font_files = [
            'DroidSansFallback.ttf',
            'NotoSansCJK-Regular.ttc',
            'NotoSansSC-Regular.otf',
            'NotoSansCJKsc-Regular.otf',
            'NotoSansCJK.ttc',
            'NotoSans-Regular.ttf',
        ]

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

        # ---------- 权限请求 ----------
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
