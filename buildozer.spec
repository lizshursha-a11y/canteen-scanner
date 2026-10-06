[app]

# Название приложения (видно на иконке)
title = Сканер столовой

# Уникальный идентификатор пакета
package.name = canteenscanner
package.domain = org.college

# Папка с исходниками
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,wav,ttf,json,txt

# Версия приложения
version = 0.1

# Зависимости Python — ВАЖНО: версия 3.11.9 жёстко зафиксирована
requirements = python3==3.11.9,hostpython3==3.11.9,kivy,requests,urllib3,chardet,idna,certifi,android

# Ориентация
orientation = portrait

# Полноэкранный режим (0 — нет)
fullscreen = 0

# Разрешения Android
android.permissions = INTERNET, ACCESS_NETWORK_STATE

# Минимальная и целевая версии Android API
android.api = 31
android.minapi = 21
android.ndk = 25b
android.ndk_api = 21

# Архитектуры процессора
android.archs = arm64-v8a, armeabi-v7a

# Разрешить резервное копирование
android.allow_backup = True

# Автоматически принимать лицензию Android SDK
android.accept_sdk_license = True

# Использовать свежую ветку python-for-android
p4a.branch = develop

[buildozer]

log_level = 2
warn_on_root = 1