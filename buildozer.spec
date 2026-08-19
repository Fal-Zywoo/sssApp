[app]
title = SolarCollector
package.name = solarcollector
package.domain = org.mycompany
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,otf
source.exclude_exts = spec
source.exclude_dirs = tests, bin, venv, __pycache__, .git
version = 1.0.0
requirements = python3,kivy,requests==2.31.0,geopy,setuptools,charset_normalizer==3.1.0
orientation = portrait
fullscreen = 0
android.permissions = INTERNET, ACCESS_NETWORK_STATE, READ_EXTERNAL_STORAGE, WRITE_EXTERNAL_STORAGE
android.api = 31
android.minapi = 24
android.ndk = 25b
android.ndk_api = 24
android.accept_sdk_license = True
android.archs = arm64-v8a
android.allow_backup = True
android.manifest.extra = <provider android:name="androidx.core.content.FileProvider" android:authorities="${applicationId}.fileprovider" android:exported="false" android:grantUriPermissions="true"><meta-data android:name="android.support.FILE_PROVIDER_PATHS" android:resource="@xml/file_paths"/></provider>
android.extra_xml = res/xml/file_paths.xml

[buildozer]
log_level = 2
warn_on_root = 1
