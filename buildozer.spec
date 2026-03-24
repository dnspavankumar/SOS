[app]
title = SOSRingKivy
package.name = sosringkivy
package.domain = org.sosring
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,txt,md
version = 0.1.0
requirements = python3,kivy,bleak,pyjnius
orientation = portrait
fullscreen = 0

# BLE + SMS + location permissions.
android.permissions = BLUETOOTH,BLUETOOTH_ADMIN,BLUETOOTH_SCAN,BLUETOOTH_CONNECT,ACCESS_FINE_LOCATION,SEND_SMS,FOREGROUND_SERVICE,POST_NOTIFICATIONS

android.api = 34
android.sdk = 34
android.build_tools = 34.0.0
android.minapi = 26
android.ndk = 25b
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1
