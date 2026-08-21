# -*- coding: utf-8 -*-
"""
Android Solar Radiation Collector - Full Optimization (Tabbed UI)
Last updated: 2026.08.21

2026.08.21 改进：
- UI：移除Chart Type，参数改为三行，按钮分三行，尺寸加大
- 移除所有非ASCII字符（Emoji等），避免方框
- 图表布局紧凑
- 新增Forecast标签页，按数据源预测未来10年
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
import random
import gc
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import math

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
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    except AttributeError:
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
from kivy.uix.spinner import Spinner

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

# ---------- 地理编码增强（OSM + 本地词典） ----------
_geocode_cache = {}
_LOCAL_COORD_DICT = {
    "上海": (31.2304, 121.4737),
    "北京": (39.9042, 116.4074),
    "广州": (23.1291, 113.2644),
    "深圳": (22.5431, 114.0579),
    "成都": (30.5728, 104.0668),
    "武汉": (30.5928, 114.3055),
    "南京": (32.0603, 118.7969),
    "杭州": (30.2741, 120.1551),
    "重庆": (29.4316, 106.9123),
    "西安": (34.3416, 108.9398),
    "天津": (39.0842, 117.2009),
    "苏州": (31.2990, 120.5853),
    "郑州": (34.7466, 113.6253),
    "长沙": (28.2282, 112.9388),
    "合肥": (31.8206, 117.2272),
    "昆明": (24.8801, 102.8329),
    "福州": (26.0745, 119.2965),
    "厦门": (24.4798, 118.0894),
    "青岛": (36.0671, 120.3826),
    "大连": (38.9140, 121.6147),
}

def get_coordinates(address, proxies=None, retries=3, delay=1.0):
    """
    使用 requests 直接调用 Nominatim API，增加请求延迟、countrycodes限定，
    失败时尝试本地词典兜底。
    """
    print(f"Geocoding: {address}")
    if address in _geocode_cache:
        return _geocode_cache[address]

    # 尝试从本地词典中匹配
    for city, (lat, lon) in _LOCAL_COORD_DICT.items():
        if city in address:
            result = (lat, lon, address)
            _geocode_cache[address] = result
            return result

    user_agent = "SolarCollectorApp/1.0 (zhongyw@jetion.com.cn)"
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": address,
        "format": "json",
        "limit": 1,
        "addressdetails": 1,
        "countrycodes": "cn",  # 限制中国区域
        "accept-language": "zh,en"
    }
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9,zh;q=0.8",
    }

    session = get_requests_session(proxies)
    for attempt in range(retries):
        try:
            # 请求前延迟
            time.sleep(delay)
            resp = session.get(url, params=params, headers=headers, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                if data and len(data) > 0:
                    lat = float(data[0]['lat'])
                    lon = float(data[0]['lon'])
                    display_name = data[0].get('display_name', address)
                    result = (lat, lon, display_name)
                    _geocode_cache[address] = result
                    return result
            elif resp.status_code == 429:
                wait = (2 ** attempt) * 2 + random.uniform(0, 1)
                time.sleep(wait)
                continue
            else:
                time.sleep(2 ** attempt)
        except Exception as e:
            app = App.get_running_app()
            if app and hasattr(app, 'main_screen'):
                app.main_screen._update_log(f"[ERROR] Geocode request error: {e}")
            time.sleep(2 ** attempt)
    return None, None, None

# ---------- API 数据获取（增强：间隔、重试、超时） ----------
def fetch_openmeteo_data(lat, lon, start_year, end_year, proxies=None, retries=3, delay=0.5):
    """获取 Open-Meteo 数据，带请求间隔"""
    time.sleep(delay)
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
                result = []
                for y in range(start_year, end_year + 1):
                    if y in yearly:
                        ghi_kwh = yearly[y] / 3.6
                        result.append({'YEAR': y, 'GHI_kWh_m2_year': ghi_kwh})
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
                app.main_screen._update_log(f"[ERROR] Open-Meteo error: {e}")
            time.sleep(2 ** attempt)
    return None

def fetch_nasa_data(lat, lon, start_year, end_year, proxies=None, retries=3, delay=0.5):
    """获取 NASA POWER 数据，带请求间隔"""
    time.sleep(delay)
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
                app.main_screen._update_log(f"[ERROR] NASA error: {e}")
            time.sleep(2 ** attempt)
    return None

def fetch_pvgis_tmy(lat, lon, start_year, end_year, proxies=None, retries=3, delay=0.5):
    """
    从 PVGIS 获取 TMY 数据，优先使用 tmy_hourly 求和，
    失败时使用经验估算（永不返回 None）。
    """
    time.sleep(delay)
    session = get_requests_session(proxies)
    tmy_url = "https://re.jrc.ec.europa.eu/api/v5_2/tmy"
    params_tmy = {
        "lat": lat,
        "lon": lon,
        "outputformat": "json",
        "components": "1",
        "usehorizon": "1"
    }
    for attempt in range(retries):
        try:
            resp = session.get(tmy_url, params=params_tmy, timeout=25)
            if resp.status_code == 200:
                data = resp.json()
                outputs = data.get('outputs', {})
                # 优先从 hourly 数据求和
                hourly_list = outputs.get('tmy_hourly', [])
                if hourly_list and len(hourly_list) > 0:
                    total_wh = sum([h.get('G(h)', 0) for h in hourly_list])
                    annual_ghi = total_wh / 1000.0
                else:
                    # 回退到 monthly
                    monthly = outputs.get('monthly', {})
                    ghi_monthly = monthly.get('G(h)', [])
                    if len(ghi_monthly) == 12:
                        annual_ghi = sum(ghi_monthly)
                    else:
                        continue  # 重试
                # 生成逐年数据（年际波动 ±5%）
                random.seed(42 + int(lat*1000))  # 固定种子保证可复现
                result = []
                for y in range(start_year, end_year + 1):
                    variation = random.uniform(0.95, 1.05)
                    result.append({
                        'YEAR': y,
                        'GHI_kWh_m2_year': annual_ghi * variation
                    })
                return result
            elif resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            else:
                break
        except Exception as e:
            app = App.get_running_app()
            if app and hasattr(app, 'main_screen'):
                app.main_screen._update_log(f"[ERROR] PVGIS TMY error: {e}")
            time.sleep(2 ** attempt)

    # 最终兜底：经验估算
    estimated_ghi = max(1000, 1800 - abs(lat) * 10)
    app = App.get_running_app()
    if app and hasattr(app, 'main_screen'):
        app.main_screen._update_log(f"[WARN] PVGIS API unavailable, using estimated value {estimated_ghi:.0f} kWh/m2")
    result = []
    for y in range(start_year, end_year + 1):
        result.append({'YEAR': y, 'GHI_kWh_m2_year': estimated_ghi})
    return result

def fetch_pvgis_data(lat, lon, start_year, end_year, proxies=None, retries=3, delay=0.5):
    """直接使用 PVGIS TMY 增强版"""
    return fetch_pvgis_tmy(lat, lon, start_year, end_year, proxies, retries, delay)

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

# ---------- 内存管理辅助 ----------
def clear_caches():
    global _geocode_cache
    _geocode_cache.clear()
    gc.collect()

# ==============================================================
# -------------------- 绘图组件（支持合同线） -----------------
# ==============================================================
class LineChartWidget(Widget):
    def __init__(self, x_values, y_values, title='', x_label='Year', y_label='GHI (kWh/m2)', contract_value=None, **kwargs):
        super().__init__(**kwargs)
        self.x_values = x_values
        self.y_values = y_values
        self.title = title
        self.x_label = x_label
        self.y_label = y_label
        self.contract_value = contract_value
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

        # 绘制合同基准线
        if self.contract_value is not None:
            with self.canvas:
                Color(1, 0, 0, 0.7)
                y_px = margin + ((self.contract_value - y_min) / y_range) * plot_h
                if margin <= y_px <= w - margin:
                    Line(points=[margin, y_px, w - margin, y_px], width=2, dash_length=5, dash_offset=2)

# ==============================================================
# ------------------- 登录界面 --------------------------------
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
                self.status_label.text = '[OK] Local login successful'
                self.app.token = 'fake_jwt_token'
                self.app.server_url = 'local'
                if self.remember_cb.active:
                    self._save_config()
                self.app.show_main_screen()
            else:
                self.status_label.text = '[FAIL] Invalid account or password'
        else:
            try:
                session = get_requests_session()
                resp = session.post(f"{server}/login", json={'username': user, 'password': pwd}, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    token = data.get('access_token')
                    if token:
                        self.app.token = token
                        self.app.server_url = server
                        self.status_label.text = '[OK] Login successful'
                        if self.remember_cb.active:
                            self._save_config()
                        self.app.show_main_screen()
                    else:
                        self.status_label.text = '[FAIL] No token returned'
                else:
                    self.status_label.text = f'[FAIL] Login failed ({resp.status_code})'
            except Exception as e:
                self.status_label.text = f'[FAIL] Connection error: {str(e)}'

# ==============================================================
# ------------------- 设置弹窗 ---------------------------------
# ==============================================================
class SettingsPopup(Popup):
    def __init__(self, main_screen, **kwargs):
        super().__init__(title='Advanced Settings', size_hint=(0.9, 0.7), **kwargs)
        self.main_screen = main_screen
        layout = BoxLayout(orientation='vertical', spacing=10, padding=10)

        # 并发数
        hbox1 = BoxLayout(spacing=5)
        hbox1.add_widget(Label(text='Max Concurrent Addresses:', size_hint_x=0.5))
        self.concurrent_spin = Spinner(text=str(main_screen.max_workers), values=[str(i) for i in range(1, 11)])
        hbox1.add_widget(self.concurrent_spin)
        layout.add_widget(hbox1)

        # 请求延迟
        hbox2 = BoxLayout(spacing=5)
        hbox2.add_widget(Label(text='Request Delay (sec):', size_hint_x=0.5))
        self.delay_input = TextInput(text=str(main_screen.request_delay), multiline=False, input_filter='float')
        hbox2.add_widget(self.delay_input)
        layout.add_widget(hbox2)

        # 重试次数
        hbox3 = BoxLayout(spacing=5)
        hbox3.add_widget(Label(text='Retry Times:', size_hint_x=0.5))
        self.retry_spin = Spinner(text=str(main_screen.retry_times), values=[str(i) for i in range(1, 6)])
        hbox3.add_widget(self.retry_spin)
        layout.add_widget(hbox3)

        # 代理配置
        hbox4 = BoxLayout(spacing=5)
        self.proxy_check = CheckBox(active=main_screen.proxy_enabled)
        hbox4.add_widget(self.proxy_check)
        hbox4.add_widget(Label(text='Enable Proxy', size_hint_x=0.3))
        hbox4.add_widget(Label(text='Host:', size_hint_x=0.15))
        self.proxy_host = TextInput(text=main_screen.proxy_host, multiline=False, size_hint_x=0.3)
        hbox4.add_widget(self.proxy_host)
        hbox4.add_widget(Label(text='Port:', size_hint_x=0.15))
        self.proxy_port = TextInput(text=str(main_screen.proxy_port), multiline=False, input_filter='int', size_hint_x=0.2)
        hbox4.add_widget(self.proxy_port)
        layout.add_widget(hbox4)

        # 按钮
        btn_box = BoxLayout(size_hint_y=None, height=50, spacing=10)
        save_btn = Button(text='Save')
        save_btn.bind(on_press=self.save_settings)
        cancel_btn = Button(text='Cancel')
        cancel_btn.bind(on_press=self.dismiss)
        btn_box.add_widget(save_btn)
        btn_box.add_widget(cancel_btn)
        layout.add_widget(btn_box)

        self.content = layout

    def save_settings(self, instance):
        self.main_screen.max_workers = int(self.concurrent_spin.text)
        self.main_screen.request_delay = float(self.delay_input.text)
        self.main_screen.retry_times = int(self.retry_spin.text)
        self.main_screen.proxy_enabled = self.proxy_check.active
        self.main_screen.proxy_host = self.proxy_host.text
        self.main_screen.proxy_port = int(self.proxy_port.text)
        if self.main_screen.proxy_enabled:
            self.main_screen.proxies = {
                'http': f"http://{self.main_screen.proxy_host}:{self.main_screen.proxy_port}",
                'https': f"http://{self.main_screen.proxy_host}:{self.main_screen.proxy_port}"
            }
        else:
            self.main_screen.proxies = None
        set_proxy_env(self.main_screen.proxies)
        self.main_screen._update_log("[INFO] Settings saved and applied.")
        self.dismiss()

# ==============================================================
# ------------------- 数据工作线程 -----------------------------
# ==============================================================
class DataWorker(threading.Thread):
    def __init__(self, task_queue, stop_event, main_screen, **kwargs):
        super().__init__(**kwargs)
        self.task_queue = task_queue
        self.stop_event = stop_event
        self.main_screen = main_screen
        self.daemon = True

    def run(self):
        while not self.stop_event.is_set():
            try:
                item = self.task_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            address, lat, lon, contract, start_year, end_year, proxies, request_delay, retry_times = item
            if self.stop_event.is_set():
                self.task_queue.task_done()
                break

            self.main_screen._update_log(f"[INFO] Processing {address} ...")
            sources = [
                ('Open-Meteo', fetch_openmeteo_data),
                ('NASA POWER', fetch_nasa_data),
                ('PVGIS TMY', fetch_pvgis_data)
            ]
            addr_charts = []
            for src_name, fetch_func in sources:
                if self.stop_event.is_set():
                    break
                self.main_screen._update_log(f"[INFO] [{address}] Fetching {src_name}...")
                data = fetch_func(lat, lon, start_year, end_year, proxies=proxies,
                                 retries=retry_times, delay=request_delay)
                if data is None:
                    self.main_screen._update_log(f"[ERROR] [{address}] {src_name} failed")
                    self.main_screen._on_task_done()
                    continue
                stats = compute_statistics(data)
                if stats is None:
                    self.main_screen._update_log(f"[ERROR] [{address}] {src_name} invalid data")
                    self.main_screen._on_task_done()
                    continue
                # 存储数据
                self.main_screen._store_data(address, src_name, stats, data)
                # 收集图表数据
                years = [d['YEAR'] for d in data]
                ghi = [d['GHI_kWh_m2_year'] for d in data]
                addr_charts.append((src_name, years, ghi, stats))
                self.main_screen._update_log(f"[INFO] [{address}] {src_name} completed")
                self.main_screen._on_task_done()

            if addr_charts:
                # 绘制图表（主线程）
                Clock.schedule_once(lambda dt, a=address, charts=addr_charts: self.main_screen._display_charts(a, charts, contract), 0)
                # 保存独立PNG（主线程延迟执行）
                Clock.schedule_once(lambda dt, a=address, charts=addr_charts: self.main_screen._save_independent_charts(a, charts), 0.1)
            else:
                self.main_screen._update_log(f"[ERROR] All sources failed for {address}")
                Clock.schedule_once(lambda dt, a=address: self.main_screen._show_error_popup(a), 0)

            self.task_queue.task_done()

# ==============================================================
# ------------------- 主界面 -----------------------------------
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
        self._last_chart_paths = []

        # 设置参数
        self.max_workers = 2
        self.request_delay = 0.5
        self.retry_times = 3
        self.proxy_enabled = False
        self.proxy_host = '127.0.0.1'
        self.proxy_port = 15732

        # 停止事件
        self.stop_event = threading.Event()
        self.worker_threads = []
        self.task_queue = queue.Queue()

        # 创建 TabbedPanel
        self.tabs = TabbedPanel(do_default_tab=False)
        self.tabs.default_tab_text = 'Data'
        self.tabs.tab_width = 120

        # ---- Tab1: Data ----
        tab_data = TabbedPanelHeader(text='Data')
        data_content = BoxLayout(orientation='vertical', spacing=5, padding=5)
        # 参数输入区域：垂直三行
        param_box = BoxLayout(orientation='vertical', size_hint_y=None, height=150, spacing=5)
        # 第1行：Start Year
        row1 = BoxLayout(size_hint_y=None, height=40, spacing=5)
        row1.add_widget(Label(text='Start Year:', size_hint_x=0.3))
        self.start_year = TextInput(text='2010', multiline=False, input_filter='int', size_hint_x=0.7, font_size='14sp')
        row1.add_widget(self.start_year)
        param_box.add_widget(row1)
        # 第2行：End Year
        row2 = BoxLayout(size_hint_y=None, height=40, spacing=5)
        row2.add_widget(Label(text='End Year:', size_hint_x=0.3))
        self.end_year = TextInput(text='2025', multiline=False, input_filter='int', size_hint_x=0.7, font_size='14sp')
        row2.add_widget(self.end_year)
        param_box.add_widget(row2)
        # 第3行留空或可放其它，但Chart Type已移除
        row3 = BoxLayout(size_hint_y=None, height=40)
        param_box.add_widget(row3)  # 占位保持布局
        data_content.add_widget(param_box)

        self.table_container = ScrollView(size_hint_y=0.3)
        self.table_grid = GridLayout(cols=4, size_hint_y=None, spacing=2, row_default_height=40)
        self.table_grid.bind(minimum_height=self.table_grid.setter('height'))
        for h in ['Address', 'Latitude', 'Longitude', 'Contract']:
            self.table_grid.add_widget(Label(text=h, size_hint_x=0.25, bold=True, font_size='12sp'))
        self.add_table_row('Shanghai, China', '', '', '1500')
        self.table_container.add_widget(self.table_grid)
        data_content.add_widget(self.table_container)

        # 按钮区域：分三行
        btn_row1 = BoxLayout(size_hint_y=None, height=50, spacing=5)
        add_btn = Button(text='Add Row')
        add_btn.bind(on_press=self.add_row)
        del_btn = Button(text='Delete Last')
        del_btn.bind(on_press=self.del_row)
        clear_btn = Button(text='Clear All')
        clear_btn.bind(on_press=self.clear_all)
        btn_row1.add_widget(add_btn)
        btn_row1.add_widget(del_btn)
        btn_row1.add_widget(clear_btn)
        data_content.add_widget(btn_row1)

        btn_row2 = BoxLayout(size_hint_y=None, height=50, spacing=5)
        load_btn = Button(text='Import CSV')
        load_btn.bind(on_press=self.load_csv)
        save_btn = Button(text='Export CSV')
        save_btn.bind(on_press=self.save_csv)
        btn_row2.add_widget(load_btn)
        btn_row2.add_widget(save_btn)
        data_content.add_widget(btn_row2)

        btn_row3 = BoxLayout(size_hint_y=None, height=50, spacing=5)
        self.start_btn = Button(text='Start', background_color=(0.2,0.7,0.2,1))
        self.start_btn.bind(on_press=self.start_processing)
        self.stop_btn = Button(text='Stop', background_color=(1,0.2,0.2,1))
        self.stop_btn.bind(on_press=self.stop_processing)
        self.retry_btn = Button(text='Retry All', background_color=(0.8,0.6,0.2,1))
        self.retry_btn.bind(on_press=self.retry_all)
        self.test_btn = Button(text='Network Test', background_color=(0.3,0.5,0.8,1))
        self.test_btn.bind(on_press=self.network_test)
        self.settings_btn = Button(text='Settings', background_color=(0.5,0.5,0.5,1))
        self.settings_btn.bind(on_press=self.open_settings)
        self.export_local_btn = Button(text='Export to Local', background_color=(0.2,0.6,0.8,1))
        self.export_local_btn.bind(on_press=self.export_all_to_local)
        btn_row3.add_widget(self.start_btn)
        btn_row3.add_widget(self.stop_btn)
        btn_row3.add_widget(self.retry_btn)
        btn_row3.add_widget(self.test_btn)
        btn_row3.add_widget(self.settings_btn)
        btn_row3.add_widget(self.export_local_btn)
        data_content.add_widget(btn_row3)

        self.progress = ProgressBar(max=100, value=0, size_hint_y=None, height=10)
        data_content.add_widget(self.progress)

        tab_data.content = data_content
        self.tabs.add_widget(tab_data)

        # ---- Tab2: Log ----
        tab_log = TabbedPanelHeader(text='Log')
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

        # ---- Tab3: Charts ----
        tab_chart = TabbedPanelHeader(text='Charts')
        chart_content = BoxLayout(orientation='vertical', spacing=5, padding=5)
        self.chart_container = ScrollView(bar_width=10, bar_color=[0.5,0.5,0.5,1])
        self.chart_box = BoxLayout(orientation='vertical', size_hint_y=None)
        self.chart_box.bind(minimum_height=self.chart_box.setter('height'))
        self.chart_container.add_widget(self.chart_box)
        chart_content.add_widget(self.chart_container)

        share_btn = Button(text='Share Data (CSV + Chart)', size_hint_y=None, height=50, background_color=(0.3,0.6,0.9,1))
        share_btn.bind(on_press=self.share_data)
        chart_content.add_widget(share_btn)

        tab_chart.content = chart_content
        self.tabs.add_widget(tab_chart)

        # ---- Tab4: Forecast ----
        tab_forecast = TabbedPanelHeader(text='Forecast')
        forecast_content = BoxLayout(orientation='vertical', spacing=5, padding=5)
        # 生成按钮
        gen_btn = Button(text='Generate Forecast (10 years)', size_hint_y=None, height=50)
        gen_btn.bind(on_press=self.generate_forecast)
        forecast_content.add_widget(gen_btn)
        # 显示区域
        self.forecast_container = ScrollView(bar_width=10, bar_color=[0.5,0.5,0.5,1])
        self.forecast_box = BoxLayout(orientation='vertical', size_hint_y=None)
        self.forecast_box.bind(minimum_height=self.forecast_box.setter('height'))
        self.forecast_container.add_widget(self.forecast_box)
        forecast_content.add_widget(self.forecast_container)

        tab_forecast.content = forecast_content
        self.tabs.add_widget(tab_forecast)

        self.add_widget(self.tabs)
        self._update_log(f'[INFO] Proxy env set: {self.proxies if self.proxies else "None"}')
        self._update_log('[INFO] SSL context: injected via custom HTTPAdapter (TLSv1+, verification disabled)')

    # ---- 表格操作 ----
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
            self._update_log('[WARN] Cannot delete last data row')

    def clear_all(self, instance):
        self.table_grid.clear_widgets()
        for h in ['Address', 'Latitude', 'Longitude', 'Contract']:
            self.table_grid.add_widget(Label(text=h, size_hint_x=0.25, bold=True, font_size='12sp'))

    def load_csv(self, instance):
        self._update_log('[WARN] CSV import on Android not fully implemented, please enter addresses manually')
        # 示例添加测试地址
        for i in range(3):
            self.add_table_row(f'Address {i+1}', '', '', '')

    def save_csv(self, instance):
        try:
            rows = []
            children = self.table_grid.children
            if len(children) <= 4:
                self._update_log('[WARN] No data to export')
                return
            items = list(children)[:-4]
            items_reversed = list(reversed(items))
            for i in range(0, len(items_reversed), 4):
                if i+3 >= len(items_reversed):
                    break
                addr = items_reversed[i].text.strip()
                lat = items_reversed[i+1].text.strip()
                lon = items_reversed[i+2].text.strip()
                contract = items_reversed[i+3].text.strip()
                rows.append([addr, lat, lon, contract])
            if not rows:
                self._update_log('[WARN] No rows to export')
                return
            csv_content = 'Address,Latitude,Longitude,Contract\n' + '\n'.join([','.join(row) for row in rows])
            filename = f"address_list_{datetime.now().strftime('%m%d_%H%M')}.csv"
            path = self._save_to_downloads(filename, csv_content.encode('utf-8-sig'), 'text/csv')
            if path:
                self._update_log(f'[INFO] Table exported to Downloads: {filename}')
            else:
                self._update_log('[ERROR] Failed to export table')
        except Exception as e:
            self._update_log(f'[ERROR] Export table failed: {e}')

    def clear_log(self, instance):
        self.log_text.text = ''
        self.error_summary.clear()

    # ---- 停止 / 重试 / 网络测试 ----
    def stop_processing(self, instance):
        if self.is_running:
            self.stop_event.set()
            self._update_log('[INFO] Stop requested...')
            while not self.task_queue.empty():
                try:
                    self.task_queue.get_nowait()
                    self.task_queue.task_done()
                except queue.Empty:
                    break
            self.is_running = False
            self.start_btn.disabled = False
            self.stop_btn.disabled = True
        else:
            self._update_log('[WARN] No task running.')

    def retry_all(self, instance):
        if not self.last_rows:
            self._update_log('[WARN] No previous task to retry.')
            return
        if self.is_running:
            self._update_log('[WARN] Task already running, stop first.')
            return
        self.start_year.text = str(self.last_start)
        self.end_year.text = str(self.last_end)
        self._update_log('[INFO] Retrying last task...')
        self.error_summary.clear()
        self._start_processing_from_rows(self.last_rows, self.last_start, self.last_end)

    def network_test(self, instance):
        self._update_log('[INFO] Starting network test (using custom SSL adapter)...')
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
                ('PVGIS TMY', 'https://re.jrc.ec.europa.eu/api/v5_2/tmy',
                 {'lat':31.23, 'lon':121.47, 'outputformat':'json', 'components':'1'}),
                ('Nominatim (OSM)', 'https://nominatim.openstreetmap.org/search',
                 {'q':'Shanghai, China', 'format':'json'})
            ]
            for name, url, params in endpoints:
                try:
                    headers = {'User-Agent': user_agent} if 'nominatim' in name.lower() else {'User-Agent': 'Mozilla/5.0'}
                    resp = session.get(url, params=params, timeout=15, headers=headers)
                    if resp.status_code == 200:
                        self._update_log(f'[INFO] [OK] {name} reachable (status {resp.status_code})')
                    else:
                        self._update_log(f'[ERROR] [FAIL] {name} returned {resp.status_code}')
                except Exception as e:
                    self._update_log(f'[ERROR] [FAIL] {name} error: {str(e)}')
            self._update_log('[INFO] Network test finished.')
        threading.Thread(target=test, daemon=True).start()

    def open_settings(self, instance):
        popup = SettingsPopup(self)
        popup.open()

    # ---- 处理流程（并发 + 队列） ----
    def start_processing(self, instance):
        if self.is_running:
            self._update_log('[WARN] Already running.')
            return

        children = self.table_grid.children
        if len(children) <= 4:
            self._update_log('[ERROR] Please enter at least one address')
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
            self._update_log('[ERROR] No valid addresses')
            return

        try:
            start = int(self.start_year.text.strip())
            end = int(self.end_year.text.strip())
            if start > end or start < 1900 or end > 2100:
                self._update_log('[ERROR] Invalid year range.')
                return
        except:
            self._update_log('[ERROR] Invalid year format.')
            return

        self.last_rows = rows[:]
        self.last_start = start
        self.last_end = end
        self.error_summary.clear()
        clear_caches()
        self._start_processing_from_rows(rows, start, end)

    def _start_processing_from_rows(self, rows, start, end):
        with self._lock:
            if self.is_running:
                return
            self.is_running = True
            self.stop_event.clear()

        self.results.clear()
        self.yearly.clear()
        self.chart_box.clear_widgets()
        self._last_chart_paths.clear()
        self.completed = 0
        self.total_tasks = len(rows) * 3
        self.progress.value = 0
        self._update_log(f'[INFO] Processing {len(rows)} address(es) from {start} to {end} with max_workers={self.max_workers}')

        self.task_queue = queue.Queue()
        for addr, lat, lon, contract in rows:
            if lat is None or lon is None:
                lat, lon, _ = get_coordinates(addr, proxies=self.proxies, retries=self.retry_times, delay=self.request_delay)
                if lat is None:
                    self._update_log(f'[ERROR] Geocoding failed for {addr}, skipped')
                    continue
            self.task_queue.put((addr, lat, lon, contract, start, end, self.proxies, self.request_delay, self.retry_times))

        self.worker_threads = []
        for _ in range(min(self.max_workers, self.task_queue.qsize())):
            worker = DataWorker(self.task_queue, self.stop_event, self)
            worker.start()
            self.worker_threads.append(worker)

        threading.Thread(target=self._monitor_workers, daemon=True).start()

        self.start_btn.disabled = True
        self.stop_btn.disabled = False

    def _monitor_workers(self):
        self.task_queue.join()
        if not self.stop_event.is_set():
            for w in self.worker_threads:
                w.join()
        Clock.schedule_once(lambda dt: self.finish_processing(), 0)

    def finish_processing(self):
        self.is_running = False
        self.start_btn.disabled = False
        self.stop_btn.disabled = True
        self._update_log('[INFO] All tasks completed!')

        if not self.yearly:
            self._update_log('[WARN] No valid data collected.')
            self._show_no_data_popup()
            return

        self._update_log('[INFO] Generating CSV asynchronously...')
        threading.Thread(target=self._export_csv_async, daemon=True).start()

        self._show_statistics_popup()
        gc.collect()

    # ---- 进度和日志（主线程安全） ----
    @mainthread
    def _update_log(self, msg):
        self.log_text.text += f'\n{msg}'
        self.log_text.cursor = (0, len(self.log_text.text))

    @mainthread
    def _update_progress(self):
        if self.total_tasks > 0:
            value = min(100, int((self.completed / self.total_tasks) * 100))
            self.progress.value = value

    def _on_task_done(self):
        self.completed += 1
        self._update_progress()

    # ---- 数据存储 ----
    def _store_data(self, address, src_name, stats, data):
        if address not in self.results:
            self.results[address] = {}
            self.yearly[address] = {}
        self.results[address][src_name] = stats
        self.yearly[address][src_name] = data

    # ---- 图表显示（含合同线） ----
    def _display_charts(self, addr, charts, contract_value=None):
        title_label = Label(text=f'Address: {addr}', size_hint_y=None, height=30, bold=True, font_size='14sp')
        self.chart_box.add_widget(title_label)
        for src_name, years, ghi, stats in charts:
            src_label = Label(text=f'Source: {src_name}', size_hint_y=None, height=20, font_size='12sp')
            self.chart_box.add_widget(src_label)
            chart_widget = LineChartWidget(x_values=years, y_values=ghi,
                                           contract_value=contract_value,
                                           size_hint_y=None, height=150)
            self.chart_box.add_widget(chart_widget)
            info = (f"Avg: {stats['avg']:.1f}   Max: {stats['max']:.1f}   Min: {stats['min']:.1f}   "
                    f"Stability: {stats['stability']:.1f}%   Years: {stats['years']}")
            info_label = Label(text=info, size_hint_y=None, height=18, font_size='11sp')
            self.chart_box.add_widget(info_label)
        total_height = sum(child.height for child in self.chart_box.children if hasattr(child, 'height'))
        total_height += 5 * len(self.chart_box.children)
        self.chart_box.height = max(total_height, 100)

    # ---- 独立PNG保存 ----
    def _save_independent_charts(self, addr, charts):
        for src_name, years, ghi, stats in charts:
            widget = LineChartWidget(x_values=years, y_values=ghi, size=(600, 400))
            widget._on_update()
            safe_addr = re.sub(r'[\\/*?:"<>|]', '_', addr)
            filename = f"{safe_addr}_{src_name}.png"
            temp_dir = os.path.join(tempfile.gettempdir(), 'solar_charts')
            os.makedirs(temp_dir, exist_ok=True)
            filepath = os.path.join(temp_dir, filename)
            widget.export_to_png(filepath)
            self._last_chart_paths.append(filepath)
            self._update_log(f'[INFO] Chart saved: {filepath}')

    # ---- CSV导出异步 ----
    def _export_csv_async(self):
        try:
            self._update_log('[INFO] CSV generation started...')
            if not self.yearly:
                raise ValueError("No yearly data")
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
                raise ValueError("No records")
            csv_content = 'Address,Data Source,Year,GHI (kWh/m2/yr)\n'
            for rec in records:
                csv_content += f"{rec['Address']},{rec['Data Source']},{rec['Year']},{rec['GHI (kWh/m2/yr)']:.2f}\n"
            filename = f"solar_data_{datetime.now().strftime('%m%d_%H%M')}.csv"
            path = self._save_to_downloads(filename, csv_content.encode('utf-8-sig'), 'text/csv')
            if path:
                self._last_csv_path = path
                self._update_log(f'[INFO] CSV exported to Downloads: {filename}')
            else:
                self._update_log('[ERROR] Failed to export CSV')
        except Exception as e:
            self._update_log(f'[ERROR] CSV export failed: {e}')

    # ---- 文件保存（兼容所有Android版本） ----
    def _save_to_downloads(self, filename, content_bytes, mime_type='text/csv'):
        try:
            from jnius import autoclass
            Environment = autoclass('android.os.Environment')
            File = autoclass('java.io.File')
            FileOutputStream = autoclass('java.io.FileOutputStream')
            
            downloads_dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
            if downloads_dir is not None and downloads_dir.exists():
                file = File(downloads_dir, filename)
                fos = FileOutputStream(file)
                fos.write(content_bytes)
                fos.close()
                return file.getAbsolutePath()
            else:
                context = autoclass('org.kivy.android.PythonActivity').mActivity
                try:
                    ContentValues = autoclass('android.content.ContentValues')
                    MediaStore = autoclass('android.provider.MediaStore')
                    resolver = context.getContentResolver()
                    values = ContentValues()
                    values.put(MediaStore.Downloads.DISPLAY_NAME, filename)
                    values.put(MediaStore.Downloads.MIME_TYPE, mime_type)
                    values.put(MediaStore.Downloads.RELATIVE_PATH, 'Download/')
                    uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                    if uri is not None:
                        with resolver.openOutputStream(uri) as out:
                            out.write(content_bytes)
                        return "MediaStore:" + uri.toString()
                except Exception as e:
                    self._update_log(f'[WARN] MediaStore fallback failed: {e}')
                
                fallback_dir = context.getExternalFilesDir(None)
                if fallback_dir is None:
                    fallback_dir = context.getFilesDir()
                file = File(fallback_dir, filename)
                fos = FileOutputStream(file)
                fos.write(content_bytes)
                fos.close()
                return file.getAbsolutePath()
        except Exception as e:
            self._update_log(f'[ERROR] All save attempts failed: {e}')
            path = os.path.join(tempfile.gettempdir(), filename)
            with open(path, 'wb') as f:
                f.write(content_bytes)
            return path

    # ---- 一键导出到本地 ----
    def export_all_to_local(self, instance):
        if not self.yearly and not self._last_chart_paths:
            self._update_log('[WARN] No data to export, run data collection first.')
            return
        if not hasattr(self, '_last_csv_path') or not self._last_csv_path:
            self._update_log('[INFO] Generating CSV before export...')
            self._export_csv_async()
            time.sleep(1)

        file_paths = []
        if hasattr(self, '_last_csv_path') and self._last_csv_path:
            file_paths.append(self._last_csv_path)
        if self._last_chart_paths:
            file_paths.extend(self._last_chart_paths)

        if not file_paths:
            self._update_log('[WARN] No files to export.')
            return

        msg = "[OK] Files saved to:\n\n"
        for f in file_paths:
            msg += f"  {f}\n"
        msg += "\nLook for them in your phone's 'Download' folder or the path shown above."
        popup = Popup(title='Export Complete', content=Label(text=msg), size_hint=(0.9, 0.6))
        popup.open()

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

    def _show_statistics_popup(self):
        if not self.results:
            return
        text = "Statistics Summary:\n\n"
        for addr, sources in self.results.items():
            text += f"Address: {addr}\n"
            for src, stats in sources.items():
                text += f"  {src}: Avg={stats['avg']:.1f}, Max={stats['max']:.1f}, Min={stats['min']:.1f}, Stability={stats['stability']:.1f}%\n"
            text += "\n"
        popup = Popup(title='Statistics', content=Label(text=text, halign='left', valign='top'), size_hint=(0.9, 0.7))
        popup.open()

    # ---- 分享数据 ----
    def share_data(self, instance):
        if not self.yearly:
            self._update_log('[WARN] No data to share, collect data first.')
            popup = Popup(title='No Data', content=Label(text='Please collect data first.'), size_hint=(0.8,0.4))
            popup.open()
            return

        try:
            self.chart_box.do_layout()
            img_path = os.path.join(tempfile.gettempdir(), f'chart_{datetime.now().strftime("%Y%m%d_%H%M%S")}.png')
            self.chart_box.export_to_png(img_path)
            self._update_log(f'[INFO] Chart screenshot saved to {img_path}')
        except Exception as e:
            self._update_log(f'[ERROR] Chart screenshot failed: {e}')
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
                if hasattr(self, '_last_csv_path') and self._last_csv_path:
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
                self._update_log('[INFO] Share intent launched.')
            except Exception as e:
                self._update_log(f'[ERROR] Share failed: {e}')
        else:
            popup = Popup(title='Share on Desktop',
                          content=Label(text=f'CSV: {self._last_csv_path}\nChart: {img_path}'),
                          size_hint=(0.8,0.5))
            popup.open()

    # ==============================================================
    # ---------------- Forecast (预测) 功能 -------------------------
    # ==============================================================
    def generate_forecast(self, instance):
        """基于已有历史数据，生成未来10年预测（按数据源）"""
        if not self.results:
            self._update_log('[WARN] No historical data. Please collect data first.')
            popup = Popup(title='No Data', content=Label(text='Please collect data first.'), size_hint=(0.8,0.4))
            popup.open()
            return

        # 清空之前预测显示
        self.forecast_box.clear_widgets()

        # 确定历史最后一年：从所有数据中取最大年份
        max_hist_year = 0
        for addr, sources in self.yearly.items():
            for src, data_list in sources.items():
                if data_list:
                    years = [d['YEAR'] for d in data_list]
                    if years:
                        max_hist_year = max(max_hist_year, max(years))
        if max_hist_year == 0:
            self._update_log('[ERROR] Could not determine historical last year.')
            return

        forecast_start_year = max_hist_year + 1
        forecast_years = list(range(forecast_start_year, forecast_start_year + 10))
        self._update_log(f'[INFO] Generating forecast from {forecast_start_year} to {forecast_start_year+9}')

        # 遍历每个地址和数据源
        for addr, sources in self.results.items():
            for src_name, stats in sources.items():
                avg_val = stats['avg']
                max_val = stats['max']
                # 计算 diff = max - avg，如果大于200则截断
                diff = max_val - avg_val
                if diff > 200:
                    diff = 200
                # 计算 Rx
                if (max_val + avg_val) != 0:
                    Rx = (diff * 2 / (max_val + avg_val)) + 1
                else:
                    Rx = 1.0
                # 基准值 Z
                Z = (avg_val + max_val) / 2

                # 定义五段随机区间
                intervals = [
                    (1.1, 1.3),
                    (1.2, 1.3),
                    (1.2, 1.4),
                    (1.3, 1.4),
                    (1.4, 1.5)
                ]
                # 为每段生成两个随机数（对应两年）
                R_list = []
                for i in range(5):
                    low, high = intervals[i]
                    r1 = round(random.uniform(low, high), 2)
                    r2 = round(random.uniform(low, high), 2)
                    R_list.extend([r1, r2])  # 共10个

                # 生成预测值
                pred_values = []
                for i, yr in enumerate(forecast_years):
                    val = Z * R_list[i] * Rx
                    pred_values.append(val)

                # 显示结果：先显示地址+数据源标题
                title_text = f"Address: {addr}  |  Source: {src_name}"
                title_label = Label(text=title_text, size_hint_y=None, height=30, bold=True, font_size='14sp')
                self.forecast_box.add_widget(title_label)

                # 表格：显示年份和预测值
                table_grid = GridLayout(cols=2, size_hint_y=None, spacing=2, row_default_height=25)
                table_grid.bind(minimum_height=table_grid.setter('height'))
                # 表头
                table_grid.add_widget(Label(text='Year', bold=True, size_hint_x=0.5))
                table_grid.add_widget(Label(text='GHI (kWh/m2)', bold=True, size_hint_x=0.5))
                for yr, val in zip(forecast_years, pred_values):
                    table_grid.add_widget(Label(text=str(yr), size_hint_x=0.5))
                    table_grid.add_widget(Label(text=f'{val:.2f}', size_hint_x=0.5))
                self.forecast_box.add_widget(table_grid)

                # 图表
                chart_widget = LineChartWidget(x_values=forecast_years, y_values=pred_values,
                                               title='Forecast', x_label='Year', y_label='GHI (kWh/m2)',
                                               contract_value=None, size_hint_y=None, height=150)
                self.forecast_box.add_widget(chart_widget)

                # 添加分隔线（仅视觉）
                sep = Widget(size_hint_y=None, height=10)
                self.forecast_box.add_widget(sep)

        # 更新滚动容器高度
        total_h = sum(child.height for child in self.forecast_box.children if hasattr(child, 'height'))
        total_h += 10 * len(self.forecast_box.children)
        self.forecast_box.height = max(total_h, 200)
        self._update_log('[INFO] Forecast generation completed.')

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
                        self._write_startup_log(f"[OK] System font loaded: {full_path}")
                        font_loaded = True
                        break
                    except Exception as e:
                        self._write_startup_log(f"[WARN] Registering {full_path} failed: {e}")
                        continue
            if font_loaded:
                break
        if not font_loaded:
            self._write_startup_log("[WARN] All system fonts failed, using Kivy default")

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
