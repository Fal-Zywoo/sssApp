[app]
title = SolarCollector
package.name = solarcollector
package.domain = org.mycompany
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,otf
source.exclude_exts = spec
source.exclude_dirs = tests, bin, venv, __pycache__, .git
version = 1.0.0
requirements = python3,kivy,requests,geopy,pyjwt,setuptools
orientation = portrait
osx.kivy_version = 2.2.0
fullscreen = 0
android.permissions = INTERNET, ACCESS_NETWORK_STATE, WRITE_EXTERNAL_STORAGE, READ_EXTERNAL_STORAGE
android.api = 30
android.minapi = 24
android.sdk = 30
android.ndk = 25b
android.ndk_path = /home/runner/.buildozer/android/platform/android-ndk-r23c
android.ndk_api = 24
android.accept_sdk_license = True
android.archs = arm64-v8a
android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 1
