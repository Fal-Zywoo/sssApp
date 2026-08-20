# -*- coding: utf-8 -*-
"""
Android Solar Radiation Collector - SSL Adapter Fix (Tabbed UI)
Last updated: 2026.08.20
- PVGIS: 直接使用 TMY（典型气象年）接口
- 网络测试增加 PVGIS TMY 端点
"""

import requests
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
import ssl
import urllib3
from geopy.geocoders import Nominatim

# 禁用 urllib3 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------- 自定义 SSL 上下文（修复 check_hostname 冲突） ----------
def create_ssl_context():
    """
    创建兼容性更好的 SSL 上下文，允许 TLSv1.0+ 和常见加密套件，
    并显式禁用证书验证与主机名检查，避免 'CERT_NONE with check_hostname' 错误。
    """
    try:
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1
        context.set_ciphers('DEFAULT@SECLEVEL=1')
        # 关键修复：禁用验证和主机名检查
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    except AttributeError:
        # 旧版 Python（<3.7）回退到完全不验证的上下文
        return ssl._create_unverified_context()

# ---------- 自定义 HTTPAdapter 注入 SSL 上下文 ----------
class CustomHTTPAdapter(requests.adapters.HTTPAdapter):
    def __init__(self, ssl_context=None, *args, **kwargs):
        self.ssl_context = ssl_context or create_ssl_context()
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs['ssl_context'] = self.ssl_context
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        kwargs['ssl_context'] = self.ssl_context
        return super().proxy_manager_for(*args, **kwargs)

# ---------- 全局 Session（带重试和自定义适配器） ----------
def get_requests_session(proxies=None):
    """
    返回配置好的 requests Session，使用自定义 SSL 适配器并设置重试策略。
    """
    session = requests.Session()
    retry = urllib3.Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    adapter = CustomHTTPAdapter(max_retries=retry)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    # 设置默认 User-Agent
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.120 Mobile Safari/537.36'
    })
    if proxies:
        session.proxies.update(proxies)
    return session

# ---------- 全局 Session 单例 ----------
_DEFAULT_SESSION = None

def get_default_session():
    global _DEFAULT_SESSION
    if _DEFAULT_SESSION is None:
        _DEFAULT_SESSION = get_requests_session()
    return _DEFAULT_SESSION

# ---------- 全局异常处理 ----------
def global_exception_handler(exc_type, exc_value, exc_tb):
    error_msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
    try:
        from kivy.app import App
        app = App.get_running_app()
        log_dir = app.user_data_dir if app else tempfile.gettempdir()
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, 'app_crash.log'), 'a', encoding='utf-8') as f:
            f.write(f"\n--- Crash at {datetime.now()} ---\n{error_msg}\n")
    except:
        pass
    print(error_msg)
    sys.__excepthook__(exc_type, exc_value, exc_tb)

sys.excepthook = global_exception_handler

# ---------- 导入 Kivy 相关 ----------
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
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelHeader

# ==============================================================
# ------------------------ 工具函数 ----------------------------
# ==============================================================

def get_app_data_dir():
    app = App.get_running_app()
    return app.user_data_dir if app else os.path.expanduser('~/.solar_collector_data')

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

def set_proxy_env(proxies):
    if proxies:
        if 'http' in proxies:
            os.environ['HTTP_PROXY'] = proxies['http']
            os.environ['http_proxy'] = proxies['http']
        if 'https' in proxies:
            os.environ['HTTPS_PROXY'] = proxies['https']
            os.environ['https_proxy'] = proxies['https']

# ---------- 地理编码（带缓存，改进 User-Agent 并降低请求频率） ----------
_geocode_cache = {}

def get_coordinates(address, proxies=None, retries=2):
    print(f"🗺️ Geocoding: {address}")
    if address in _geocode_cache:
        return _geocode_cache[address]
    
    # 自定义 User-Agent（包含真实邮箱，降低被拒风险）
    user_agent = "SolarCollectorApp/1.0 (zhongyw@jetion.com.cn)"
    
    try:
        session = get_requests_session(proxies)
        geolocator = Nominatim(
            user_agent=user_agent,
            timeout=15,
            proxies=proxies,
            ssl_verify=False,
            session=session,
            headers={'User-Agent': user_agent}  # 显式指定 HTTP 头
        )
        for attempt in range(retries):
            try:
                location = geolocator.geocode(address, timeout=15)
                if location:
                    result = (location.latitude, location.longitude, location.address)
                    _geocode_cache[address] = result
                    return result
            except Exception as e:
                print(f"⚠️ Attempt {attempt+1} failed: {e}")
                app = App.get_running_app()
                if app and hasattr(app, 'main_screen'):
                    app.main_screen._update_log(f"Geocode attempt {attempt+1}: {traceback.format_exc()}")
                time.sleep(2 ** attempt)  # 指数退避
            time.sleep(1)  # 每次尝试后额外等待，降低频率
    except Exception as e:
        print(f"❌ Geocoding error: {e}")
        traceback.print_exc()
        app = App.get_running_app()
        if app and hasattr(app, 'main_screen'):
            app.main_screen._update_log(f"Geocode fatal error: {traceback.format_exc()}")
    return None, None, None

# ---------- API 数据获取（使用自定义 session） ----------
def fetch_openmeteo_data(lat, lon, start_year, end_year, proxies=None, retries=3):
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": lat, "longitude": lon,
        "start_date": f"{start_year}-01-01", "end_date": f"{end_year}-12-31",
        "daily": "shortwave_radiation_sum", "timezone": "Asia/Shanghai"
    }
    session = get_requests_session(proxies)
    for attempt in range(retries):
        try:
            resp = session.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                daily = data.get('daily')
                if not daily:
                    continue
                yearly = {}
                for dt, val in zip(daily['time'], daily['shortwave_radiation_sum']):
                    if val is None:
                        continue
                    year = int(dt[:4])
                    yearly[year] = yearly.get(year, 0.0) + val
                # 转换 MJ/m² → kWh/m²
                result = []
                for y in range(start_year, end_year + 1):
                    if y in yearly:
                        ghi_kwh = yearly[y] / 3.6
                        result.append({'YEAR': y, 'GHI_kWh_m2_year': ghi_kwh})
                if result:
                    return result
                else:
                    continue
            elif resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            else:
                break
        except Exception as e:
            app = App.get_running_app()
            if app and hasattr(app, 'main_screen'):
                app.main_screen._update_log(f"Open-Meteo error: {traceback.format_exc()}")
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
    session = get_requests_session(proxies)
    for attempt in range(retries):
        try:
            resp = session.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                daily_data = data['properties']['parameter']['ALLSKY_SFC_SW_DWN']
                yearly = {}
                for d, v in daily_data.items():
                    if v is None:
                        continue
                    year = int(d[:4])
                    yearly[year] = yearly.get(year, 0.0) + v
                result = [{'YEAR': y, 'GHI_kWh_m2_year': yearly[y]} for y in sorted(yearly) if start_year <= y <= end_year]
                if result:
                    return result
            elif resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            else:
                break
        except Exception as e:
            app = App.get_running_app()
            if app and hasattr(app, 'main_screen'):
                app.main_screen._update_log(f"NASA error: {traceback.format_exc()}")
            time.sleep(2 ** attempt)
            continue
    return None

def fetch_pvgis_tmy(lat, lon, start_year, end_year, proxies=None, retries=3):
    """
    从 PVGIS 获取 TMY（典型气象年）数据，返回逐年列表（所有年份使用同一典型年值）。
    """
    session = get_requests_session(proxies)
    tmy_url = "https://re.jrc.ec.europa.eu/api/v5_2/pvgis"
    params_tmy = {
        "lat": lat,
        "lon": lon,
        "outputformat": "json",
        "components": "1",
        "usehorizon": "1",
        "userhorizon": "0",
        "pvsyst": "0"
    }
    for attempt in range(retries):
        try:
            resp = session.get(tmy_url, params=params_tmy, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                monthly = data.get('outputs', {}).get('monthly', {})
                ghi_monthly = monthly.get('G(h)', [])  # 12个月
                if len(ghi_monthly) == 12:
                    annual_ghi = sum(ghi_monthly)  # 年总值 (kWh/m²/yr)
                    # 构造逐年列表，每年都等于该典型年值
                    result = []
                    for y in range(start_year, end_year + 1):
                        result.append({'YEAR': y, 'GHI_kWh_m2_year': annual_ghi})
                    return result
                else:
                    continue
            elif resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            else:
                break
        except Exception as e:
            app = App.get_running_app()
            if app and hasattr(app, 'main_screen'):
                app.main_screen._update_log(f"PVGIS TMY error: {traceback.format_exc()}")
            time.sleep(2 ** attempt)
            continue
    return None

def fetch_pvgis_data(lat, lon, start_year, end_year, proxies=None, retries=3):
    """直接使用 PVGIS TMY（典型气象年）接口"""
    return fetch_pvgis_tmy(lat, lon, start_year, end_year, proxies, retries)

# ---------- 统计计算 ----------
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

# ==============================================================
# -------------------- 绘图组件 --------------------------------
# ==============================================================
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

# ==============================================================
# ------------------- 登录界面（未变动） ------------------------
# ==============================================================
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
                # 使用自定义 session（支持 SSL）
                session = get_requests_session()
                resp = session.post(f"{server}/login", json={'username': user, 'password': pwd}, timeout=10)
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

# ==============================================================
# ------------------- 主界面（含修复） --------------------------
# ==============================================================
class MainScreen(BoxLayout):
    def __init__(self, app, **kwargs):
        super().__init__(orientation='vertical', **kwargs)
        self.app = app
        self._lock = threading.Lock()
        self.is_running = False
        self._geocode_cache = {}
        self.results = {}
        self.yearly = {}
        self.completed = 0
        self.total_tasks = 0
        self.proxies = detect_proxy_enhanced()
        set_proxy_env(self.proxies)
        self.error_summary = []
        self.last_rows = []
        self.last_start = 2010
        self.last_end = 2025
        self._last_csv_path = None

        # 创建 TabbedPanel
        self.tabs = TabbedPanel(do_default_tab=False)
        self.tabs.default_tab_text = 'Data'
        self.tabs.tab_width = 120

        # ---- Tab1: 数据 ----
        tab_data = TabbedPanelHeader(text='📊 Data')
        data_content = BoxLayout(orientation='vertical', spacing=5, padding=5)
        # 参数输入
        param_box = BoxLayout(orientation='vertical', size_hint_y=None, height=120, spacing=3)
        row1 = BoxLayout(spacing=5)
        row1.add_widget(Label(text='Start:', size_hint_x=0.2))
        self.start_year = TextInput(text='2010', multiline=False, input_filter='int', size_hint_x=0.3)
        row1.add_widget(self.start_year)
        row1.add_widget(Label(text='End:', size_hint_x=0.2))
        self.end_year = TextInput(text='2025', multiline=False, input_filter='int', size_hint_x=0.3)
        row1.add_widget(self.end_year)
        param_box.add_widget(row1)

        row2 = BoxLayout(spacing=5)
        row2.add_widget(Label(text='Chart Type:', size_hint_x=0.3))
        self.chart_type = TextInput(text='line', multiline=False, size_hint_x=0.7)
        row2.add_widget(self.chart_type)
        row2.add_widget(Widget(size_hint_x=0.3))
        row2.add_widget(Widget(size_hint_x=0.7))
        param_box.add_widget(row2)
        data_content.add_widget(param_box)

        # 表格
        self.table_container = ScrollView(size_hint_y=0.3)
        self.table_grid = GridLayout(cols=4, size_hint_y=None, spacing=2, row_default_height=40)
        self.table_grid.bind(minimum_height=self.table_grid.setter('height'))
        for h in ['Address', 'Latitude', 'Longitude', 'Contract']:
            self.table_grid.add_widget(Label(text=h, size_hint_x=0.25, bold=True, font_size='12sp'))
        self.add_table_row('Shanghai, China', '', '', '1500')
        self.table_container.add_widget(self.table_grid)
        data_content.add_widget(self.table_container)

        # 按钮行1
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
        data_content.add_widget(btn_row1)

        # 按钮行2
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
        data_content.add_widget(btn_row2)

        # 进度条
        self.progress = ProgressBar(max=100, value=0, size_hint_y=None, height=20)
        data_content.add_widget(self.progress)

        tab_data.content = data_content
        self.tabs.add_widget(tab_data)

        # ---- Tab2: 日志 ----
        tab_log = TabbedPanelHeader(text='📝 Log')
        log_content = BoxLayout(orientation='vertical', spacing=2, padding=5)
        log_content.add_widget(Label(text='Log Output:', size_hint_y=None, height=20, font_size='12sp'))
        self.log_text = TextInput(text='', readonly=True, multiline=True, halign='left', font_size='11sp')
        log_scroll = ScrollView(bar_width=10, bar_color=[0.5,0.5,0.5,1])
        log_scroll.add_widget(self.log_text)
        log_content.add_widget(log_scroll)
        clear_log_btn = Button(text='Clear Log', size_hint_y=None, height=30)
        clear_log_btn.bind(on_press=self.clear_log)
        log_content.add_widget(clear_log_btn)
        tab_log.content = log_content
        self.tabs.add_widget(tab_log)

        # ---- Tab3: 图表 ----
        tab_chart = TabbedPanelHeader(text='📈 Charts')
        chart_content = BoxLayout(orientation='vertical', spacing=5, padding=5)
        self.chart_container = ScrollView(bar_width=10, bar_color=[0.5,0.5,0.5,1])
        self.chart_box = BoxLayout(orientation='vertical', size_hint_y=None)
        self.chart_box.bind(minimum_height=self.chart_box.setter('height'))
        self.chart_container.add_widget(self.chart_box)
        chart_content.add_widget(self.chart_container)

        share_btn = Button(text='📤 Share Data (CSV + Chart)', size_hint_y=None, height=50, background_color=(0.3,0.6,0.9,1))
        share_btn.bind(on_press=self.share_data)
        chart_content.add_widget(share_btn)

        tab_chart.content = chart_content
        self.tabs.add_widget(tab_chart)

        self.add_widget(self.tabs)
        self._update_log(f'Proxy env set: {self.proxies if self.proxies else "None"}')
        self._update_log('SSL context: injected via custom HTTPAdapter (TLSv1+, verification disabled)')

    # ---- 表格操作（未变） ----
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
        # 待实现（可留空）
        pass

    def save_csv(self, instance):
        self._export_csv()

    def clear_log(self, instance):
        self.log_text.text = ''
        self.error_summary.clear()

    # ---- 停止 / 重试 / 网络测试（含 PVGIS TMY） ----
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
        self.start_year.text = str(self.last_start)
        self.end_year.text = str(self.last_end)
        self._update_log('🔄 Retrying last task...')
        self.error_summary.clear()
        self._start_processing_from_rows(self.last_rows, self.last_start, self.last_end)

    def network_test(self, instance):
        self._update_log('🌐 Starting network test (using custom SSL adapter)...')
        def test():
            session = get_requests_session(self.proxies)
            user_agent = "SolarCollectorApp/1.0 (zhongyw@jetion.com.cn)"
            endpoints = [
                ('Open-Meteo', 'https://archive-api.open-meteo.com/v1/archive',
                 {'latitude':31.23, 'longitude':121.47, 'start_date':'2020-01-01', 'end_date':'2020-01-02',
                  'daily':'shortwave_radiation_sum', 'timezone':'Asia/Shanghai'}),
                ('NASA POWER', 'https://power.larc.nasa.gov/api/temporal/daily/point',
                 {'parameters':'ALLSKY_SFC_SW_DWN','community':'RE','longitude':121.47,'latitude':31.23,
                  'start':'20200101','end':'20200102','format':'JSON','user':'pvuser'}),
                ('PVGIS TMY', 'https://re.jrc.ec.europa.eu/api/v5_2/pvgis',
                 {'lat':31.23, 'lon':121.47, 'outputformat':'json', 'components':'1'}),
                ('Nominatim (OSM)', 'https://nominatim.openstreetmap.org/search',
                 {'q':'Shanghai, China', 'format':'json'})
            ]
            for name, url, params in endpoints:
                try:
                    headers = {'User-Agent': user_agent} if 'nominatim' in name.lower() else {'User-Agent': 'Mozilla/5.0'}
                    resp = session.get(url, params=params, timeout=15, headers=headers)
                    if resp.status_code == 200:
                        self._update_log(f'✅ {name} reachable (status {resp.status_code})')
                    else:
                        self._update_log(f'❌ {name} returned {resp.status_code}')
                except Exception as e:
                    self._update_log(f'❌ {name} error: {str(e)}')
            self._update_log('🏁 Network test finished.')
        threading.Thread(target=test, daemon=True).start()

    # ---- 处理流程 ----
    def start_processing(self, instance):
        if self.is_running:
            self._update_log('⚠️ Already running.')
            return

        children = self.table_grid.children
        if len(children) <= 4:
            self._update_log('❌ Please enter at least one address')
            return

        items = list(children)[:-4]
        items_reversed = list(reversed(items))
        rows = []
        for i in range(0, len(items_reversed), 4):
            if i+3 >= len(items_reversed):
                break
            addr = items_reversed[i].text.strip()
            lat_text = items_reversed[i+1].text.strip()
            lon_text = items_reversed[i+2].text.strip()
            contract_text = items_reversed[i+3].text.strip()
            if not addr:
                continue
            try:
                lat_val = float(lat_text) if lat_text else None
            except:
                lat_val = None
            try:
                lon_val = float(lon_text) if lon_text else None
            except:
                lon_val = None
            try:
                contract_val = float(contract_text) if contract_text else None
            except:
                contract_val = None
            rows.append((addr, lat_val, lon_val, contract_val))

        if not rows:
            self._update_log('❌ No valid addresses')
            return

        try:
            start = int(self.start_year.text.strip())
            end = int(self.end_year.text.strip())
            if start > end or start < 1900 or end > 2100:
                self._update_log('❌ Invalid year range.')
                return
        except:
            self._update_log('❌ Invalid year format.')
            return

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

            if lat is None or lon is None:
                lat, lon, _ = self._geocode_address(addr)
                # 地理编码后额外延迟，降低 OSM 请求频率
                time.sleep(0.5)
                if lat is None:
                    err_msg = f'Geocoding failed for {addr}'
                    self.error_summary.append(err_msg)
                    self._update_log(f'❌ {err_msg}, skipped')
                    self._update_progress(1)
                    continue

            sources = [
                ('Open-Meteo', fetch_openmeteo_data),
                ('NASA POWER', fetch_nasa_data),
                ('PVGIS TMY', fetch_pvgis_data)
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
                sample = data[:3] if len(data) >= 3 else data
                sample_str = ', '.join([f"{d['YEAR']}:{d['GHI_kWh_m2_year']:.1f}" for d in sample])
                self._update_log(f'[{addr}] {src_name} sample: {sample_str}')
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
            time.sleep(0.5)  # 每个地址处理完后等待，避免过载

        self._update_log('🎉 All tasks completed!')
        with self._lock:
            self.is_running = False

        if not any_success or not self.yearly:
            Clock.schedule_once(lambda dt: self._show_no_data_popup(), 0)
        else:
            self._export_csv()

    def _geocode_address(self, addr):
        if addr in self._geocode_cache:
            return self._geocode_cache[addr]
        lat, lon, full = get_coordinates(addr, proxies=self.proxies)
        if lat is not None:
            self._geocode_cache[addr] = (lat, lon, full)
        return lat, lon, full

    # ---- 弹窗 ----
    def _show_error_popup(self, addr):
        popup = Popup(title='Collection Failed',
                      content=Label(text=f'All data sources failed for:\n{addr}\nCheck network or address.'),
                      size_hint=(0.8, 0.5))
        popup.open()

    def _show_no_data_popup(self):
        err_text = "No valid data collected.\n"
        if self.error_summary:
            err_text += "\nRecent errors:\n" + "\n".join(self.error_summary[-5:])
        else:
            err_text += "Please check network settings and try again."
        popup = Popup(title='No Data Collected',
                      content=Label(text=err_text, halign='left', valign='top'),
                      size_hint=(0.9, 0.6))
        popup.open()

    # ---- 数据存储与展示 ----
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
        total_height = sum(child.height for child in self.chart_box.children if hasattr(child, 'height'))
        total_height += 10 * len(self.chart_box.children)
        self.chart_box.height = max(total_height, 100)

    # ---- 日志和进度 ----
    @mainthread
    def _update_log(self, msg):
        self.log_text.text += f'\n{msg}'
        self.log_text.cursor = (0, len(self.log_text.text))

    @mainthread
    def _update_progress(self, step):
        self.completed += step
        if self.total_tasks > 0:
            self.progress.value = min(100, int(self.completed / self.total_tasks * 100))

    # ---- CSV 导出 ----
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
            self._last_csv_path = filepath
            if platform != 'android':
                popup = Popup(title='Export Complete',
                              content=Label(text=f'CSV saved to:\n{filepath}'),
                              size_hint=(0.8, 0.5))
                popup.open()
        except Exception as e:
            self._update_log(f'❌ Export failed: {e}')

    # ---- 分享数据 ----
    def share_data(self, instance):
        if not self.yearly:
            self._update_log('⚠️ No data to share, collect data first.')
            popup = Popup(title='No Data', content=Label(text='Please collect data first.'), size_hint=(0.8,0.4))
            popup.open()
            return

        if not hasattr(self, '_last_csv_path') or not os.path.exists(self._last_csv_path):
            self._export_csv()
            if not hasattr(self, '_last_csv_path') or not os.path.exists(self._last_csv_path):
                self._update_log('❌ CSV export failed, cannot share.')
                return

        # 截图图表
        try:
            # 强制布局刷新
            self.chart_box.do_layout()
            import tempfile
            img_path = os.path.join(tempfile.gettempdir(), f'chart_{datetime.now().strftime("%Y%m%d_%H%M%S")}.png')
            self.chart_box.export_to_png(img_path)
            self._update_log(f'📸 Chart saved to {img_path}')
        except Exception as e:
            self._update_log(f'❌ Chart screenshot failed: {e}')
            popup = Popup(title='Share Error', content=Label(text=f'Failed to capture chart: {e}'), size_hint=(0.8,0.4))
            popup.open()
            return

        if platform == 'android':
            try:
                from jnius import autoclass
                PythonActivity = autoclass('org.kivy.android.PythonActivity')
                Intent = autoclass('android.content.Intent')
                Uri = autoclass('android.net.Uri')
                File = autoclass('java.io.File')
                ArrayList = autoclass('java.util.ArrayList')
                context = PythonActivity.mActivity

                uris = ArrayList()
                csv_file = File(self._last_csv_path)
                csv_uri = Uri.fromFile(csv_file)
                uris.add(csv_uri)
                png_file = File(img_path)
                png_uri = Uri.fromFile(png_file)
                uris.add(png_uri)

                intent = Intent(Intent.ACTION_SEND_MULTIPLE)
                intent.setType('*/*')
                intent.putExtra(Intent.EXTRA_STREAM, uris)
                intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)

                chooser = Intent.createChooser(intent, 'Share Data via')
                context.startActivity(chooser)
                self._update_log('📤 Share intent launched.')
            except Exception as e:
                self._update_log(f'❌ Share failed: {e}')
                popup = Popup(title='Share Error', content=Label(text=f'Share failed: {e}'), size_hint=(0.8,0.4))
                popup.open()
        else:
            popup = Popup(title='Share on Desktop',
                          content=Label(text=f'CSV: {self._last_csv_path}\nChart: {img_path}'),
                          size_hint=(0.8,0.5))
            popup.open()

# ==============================================================
# ------------------- 应用程序类 -------------------------------
# ==============================================================
class SolarApp(App):
    def build(self):
        self._write_startup_log("App starting...")
        # 尝试加载系统字体
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

        # Android 权限请求
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
            os.makedirs(self.user_data_dir, exist_ok=True)
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
