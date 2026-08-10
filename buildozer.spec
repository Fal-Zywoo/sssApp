[app]
title = SolarCollector
package.name = solarcollector
package.domain = org.mycompany
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,otf
source.exclude_exts = spec
source.exclude_dirs = tests, bin, venv, __pycache__, .git
version = 1.0.0
requirements = python3,kivy==2.2.0,requests,geopy,kvdroid,setuptools
# 移除了 pyjwt（未使用）
orientation = portrait
osx.kivy_version = 2.2.0
fullscreen = 0
# 仅保留网络权限，外部存储相关权限移除（因为不再使用）
android.permissions = INTERNET, ACCESS_NETWORK_STATE
android.api = 31
android.minapi = 24
android.ndk = 25b
android.ndk_api = 24
android.accept_sdk_license = True
android.archs = arm64-v8a
android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 1
