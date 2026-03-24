import asyncio
import threading
import time
from datetime import datetime

from bleak import BleakClient, BleakScanner
from kivy.app import App
from kivy.clock import Clock
from kivy.lang import Builder
from kivy.storage.jsonstore import JsonStore
from kivy.utils import platform

if platform == "android":
    from android.permissions import request_permissions
    from jnius import autoclass


KV = """
BoxLayout:
    orientation: "vertical"
    padding: "14dp"
    spacing: "10dp"

    Label:
        text: "SOS Ring (Kivy)"
        size_hint_y: None
        height: "36dp"
        bold: True

    TextInput:
        id: phone_input
        hint_text: "Caretaker phone number (+91...)"
        multiline: False
        size_hint_y: None
        height: "44dp"

    TextInput:
        id: message_input
        hint_text: "Custom alert text (optional)"
        multiline: False
        size_hint_y: None
        height: "44dp"

    BoxLayout:
        size_hint_y: None
        height: "44dp"
        spacing: "8dp"

        Button:
            text: "Start"
            on_release: app.start_monitoring()

        Button:
            text: "Stop"
            on_release: app.stop_monitoring()

    Label:
        text: "Status"
        size_hint_y: None
        height: "24dp"
        bold: True

    Label:
        id: status_label
        text: "Idle"
        text_size: self.width, None
        halign: "left"
        valign: "top"
"""


class SOSRingApp(App):
    DEVICE_NAME = "ESP32_SOS_BUTTON"
    SERVICE_UUID = "12345678-1234-1234-1234-1234567890ab"
    SOS_CHAR_UUID = "abcd1234-1234-1234-1234-abcdef123456"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.settings = JsonStore("settings.json")
        self.monitoring = False
        self.stop_event = threading.Event()
        self.worker_thread = None
        self.last_sos_at = 0.0
        self.caretaker_phone = ""
        self.custom_message = "Emergency alert from SOS ring."

    def build(self):
        return Builder.load_string(KV)

    def on_start(self):
        if self.settings.exists("user"):
            data = self.settings.get("user")
            self.root.ids.phone_input.text = data.get("caretaker_phone", "")
            self.root.ids.message_input.text = data.get(
                "custom_message",
                "Emergency alert from SOS ring.",
            )

    def on_stop(self):
        self.stop_monitoring()

    def start_monitoring(self):
        if self.monitoring:
            self._set_status("Already monitoring.")
            return

        phone = self.root.ids.phone_input.text.strip()
        message = self.root.ids.message_input.text.strip() or "Emergency alert from SOS ring."
        if not phone:
            self._set_status("Enter caretaker phone number first.")
            return

        self.caretaker_phone = phone
        self.custom_message = message
        self.settings.put(
            "user",
            caretaker_phone=self.caretaker_phone,
            custom_message=self.custom_message,
        )

        if platform != "android":
            self._set_status("Run this app on Android for BLE + SMS support.")
            return

        self._set_status("Requesting permissions...")
        permissions = [
            "android.permission.SEND_SMS",
            "android.permission.ACCESS_FINE_LOCATION",
            "android.permission.BLUETOOTH_SCAN",
            "android.permission.BLUETOOTH_CONNECT",
            "android.permission.BLUETOOTH",
            "android.permission.BLUETOOTH_ADMIN",
        ]
        request_permissions(permissions, self._on_permissions_result)

    def stop_monitoring(self):
        self.stop_event.set()
        self.monitoring = False
        self._set_status("Monitoring stopped.")

    def _on_permissions_result(self, permissions, grants):
        if not all(bool(grant) for grant in grants):
            self._set_status("All permissions are required.")
            return

        self.stop_event.clear()
        self.monitoring = True
        self.worker_thread = threading.Thread(target=self._run_ble_loop, daemon=True)
        self.worker_thread.start()
        self._set_status("Starting BLE monitor...")

    def _run_ble_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._ble_monitor_loop())
        except Exception as exc:
            self._set_status(f"BLE loop error: {exc}")
        finally:
            loop.close()
            self.monitoring = False

    async def _ble_monitor_loop(self):
        while not self.stop_event.is_set():
            device = await self._find_target_device()
            if not device:
                continue

            try:
                self._set_status("Connecting to ring...")
                async with BleakClient(device, timeout=15.0) as client:
                    await client.start_notify(self.SOS_CHAR_UUID, self._on_notification)
                    self._set_status("Connected. Waiting for SOS press.")

                    while not self.stop_event.is_set() and client.is_connected:
                        await asyncio.sleep(0.5)

                    try:
                        await client.stop_notify(self.SOS_CHAR_UUID)
                    except Exception:
                        pass

            except Exception as exc:
                self._set_status(f"Disconnected/retry: {exc}")
                await asyncio.sleep(2.0)

        self._set_status("Monitoring stopped.")

    async def _find_target_device(self):
        self._set_status("Scanning for SOS ring...")
        try:
            devices = await BleakScanner.discover(
                timeout=6.0,
                service_uuids=[self.SERVICE_UUID],
            )
        except Exception as exc:
            self._set_status(f"Scan failed: {exc}")
            await asyncio.sleep(2.0)
            return None

        for dev in devices:
            if (dev.name or "").strip() == self.DEVICE_NAME:
                return dev

        if devices:
            # Service UUID matched; name may be empty on some phones.
            return devices[0]

        self._set_status("Ring not found. Retrying...")
        await asyncio.sleep(1.0)
        return None

    def _on_notification(self, sender, data):
        payload = bytes(data).decode("utf-8", errors="ignore").strip()
        if payload.upper() != "SOS":
            return

        now = time.time()
        if now - self.last_sos_at < 3.0:
            return
        self.last_sos_at = now

        self._set_status("SOS received. Sending SMS...")
        threading.Thread(target=self._send_alert_sms, daemon=True).start()

    def _send_alert_sms(self):
        if platform != "android":
            self._set_status("SMS works only on Android.")
            return

        location = self._get_last_location()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        text = f"{self.custom_message} Triggered at {timestamp}."
        if location:
            lat, lng = location
            text += f" Location: https://maps.google.com/?q={lat},{lng}"
        else:
            text += " Location unavailable."

        ok = self._send_sms(self.caretaker_phone, text)
        if ok:
            self._set_status(f"SOS SMS sent to {self.caretaker_phone}")
        else:
            self._set_status("Failed to send SMS.")

    def _get_last_location(self):
        if platform != "android":
            return None
        try:
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            Context = autoclass("android.content.Context")
            activity = PythonActivity.mActivity
            location_manager = activity.getSystemService(Context.LOCATION_SERVICE)
            if location_manager is None:
                return None

            providers = location_manager.getProviders(True)
            best = None
            for provider in providers:
                loc = location_manager.getLastKnownLocation(provider)
                if loc is None:
                    continue
                if best is None or loc.getTime() > best.getTime():
                    best = loc

            if best is None:
                return None
            return best.getLatitude(), best.getLongitude()
        except Exception:
            return None

    def _send_sms(self, phone_number, message):
        if platform != "android":
            return False
        try:
            SmsManager = autoclass("android.telephony.SmsManager")
            sms = SmsManager.getDefault()
            parts = sms.divideMessage(message)
            if parts is not None and parts.size() > 1:
                sms.sendMultipartTextMessage(phone_number, None, parts, None, None)
            else:
                sms.sendTextMessage(phone_number, None, message, None, None)
            return True
        except Exception:
            return False

    def _set_status(self, text):
        def _update(_dt):
            if self.root:
                self.root.ids.status_label.text = text

        Clock.schedule_once(_update, 0)


if __name__ == "__main__":
    SOSRingApp().run()
