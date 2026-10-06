[app]

title = Сканер столовой
package.name = canteenscanner
package.domain = org.college

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,wav,ttf,json,txt

version = 0.1

# Python 3.11.9 — стабильная, совместимая с buildozer 1.5.0
requirements = python3==3.11.9,hostpython3==3.11.9,kivy,requests,urllib3,chardet,idna,certifi,android

orientation = portrait
fullscreen = 0

android.permissions = INTERNET, ACCESS_NETWORK_STATE

android.api = 31
android.minapi = 21
android.ndk = 25b
android.ndk_api = 21

android.archs = arm64-v8a, armeabi-v7a

android.allow_backup = True
android.accept_sdk_license = True

# ВАЖНО: используем стабильную ветку master, а не develop
p4a.branch = master

[buildozer]
log_level = 2
warn_on_root = 1
