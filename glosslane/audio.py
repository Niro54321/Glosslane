"""What the PC is playing, as 16 kHz mono float32, straight from WASAPI.

Two sources:
- a playback device (speakers, headset) in loopback mode: everything you hear;
- one app and its child processes (Windows 10 2004+ process loopback):
  only Discord, only the browser, only the game.

Windows does the mixing down and resampling for us (AUTOCONVERTPCM), so the
capture thread only copies small packets into a queue. It never blocks on the
reader: when nobody reads fast enough the oldest audio is dropped.
"""

from __future__ import annotations

import ctypes
import queue
import threading
import time
from ctypes import HRESULT, POINTER, byref, c_void_p, wintypes
from dataclasses import dataclass

import comtypes
import numpy as np
from comtypes import COMMETHOD, GUID, IUnknown

RATE = 16000

CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
eRender, eConsole = 0, 0
DEVICE_STATE_ACTIVE = 1
CLSCTX_ALL = 23
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
WAVE_FORMAT_PCM = 1
VT_BLOB = 65
VT_LPWSTR = 31


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
                ("nSamplesPerSec", wintypes.DWORD), ("nAvgBytesPerSec", wintypes.DWORD),
                ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD), ("cbSize", wintypes.WORD)]


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


class BLOB(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.ULONG), ("pBlobData", c_void_p)]


class _PVUnion(ctypes.Union):
    _fields_ = [("pwszVal", wintypes.LPWSTR), ("blob", BLOB), ("raw", ctypes.c_byte * 16)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", wintypes.USHORT), ("r1", wintypes.USHORT), ("r2", wintypes.USHORT),
                ("r3", wintypes.USHORT), ("u", _PVUnion)]


class AUDIOCLIENT_ACTIVATION_PARAMS(ctypes.Structure):
    _fields_ = [("ActivationType", ctypes.c_int), ("TargetProcessId", wintypes.DWORD),
                ("ProcessLoopbackMode", ctypes.c_int)]


PKEY_Device_FriendlyName = PROPERTYKEY(GUID("{a45c254e-df1c-4efd-8020-67d146a850e0}"), 14)


class IPropertyStore(IUnknown):
    _iid_ = GUID("{886d8eeb-8cf2-4446-8d02-cdba1dbdcf99}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(wintypes.DWORD))),
        COMMETHOD([], HRESULT, "GetAt", (["in"], wintypes.DWORD), (["out"], POINTER(PROPERTYKEY))),
        COMMETHOD([], HRESULT, "GetValue", (["in"], POINTER(PROPERTYKEY)), (["in"], POINTER(PROPVARIANT))),
    ]


class IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Activate", (["in"], POINTER(GUID)), (["in"], wintypes.DWORD),
                  (["in"], c_void_p), (["out"], POINTER(c_void_p))),
        COMMETHOD([], HRESULT, "OpenPropertyStore", (["in"], wintypes.DWORD),
                  (["out"], POINTER(POINTER(IPropertyStore)))),
        COMMETHOD([], HRESULT, "GetId", (["out"], POINTER(wintypes.LPWSTR))),
        COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(wintypes.DWORD))),
    ]


class IMMDeviceCollection(IUnknown):
    _iid_ = GUID("{0BD7A1BE-7A1A-44DB-8397-CC5392387B5E}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(wintypes.UINT))),
        COMMETHOD([], HRESULT, "Item", (["in"], wintypes.UINT), (["out"], POINTER(POINTER(IMMDevice)))),
    ]


class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], ctypes.c_int), (["in"], wintypes.DWORD),
                  (["out"], POINTER(POINTER(IMMDeviceCollection)))),
        COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], ctypes.c_int), (["in"], ctypes.c_int),
                  (["out"], POINTER(POINTER(IMMDevice)))),
        COMMETHOD([], HRESULT, "GetDevice", (["in"], wintypes.LPCWSTR), (["out"], POINTER(POINTER(IMMDevice)))),
    ]


class IAudioClient(IUnknown):
    _iid_ = GUID("{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Initialize", (["in"], ctypes.c_int), (["in"], wintypes.DWORD),
                  (["in"], ctypes.c_longlong), (["in"], ctypes.c_longlong),
                  (["in"], POINTER(WAVEFORMATEX)), (["in"], c_void_p)),
        COMMETHOD([], HRESULT, "GetBufferSize", (["out"], POINTER(wintypes.UINT))),
        COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], POINTER(ctypes.c_longlong))),
        COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], POINTER(wintypes.UINT))),
        COMMETHOD([], HRESULT, "IsFormatSupported", (["in"], ctypes.c_int), (["in"], POINTER(WAVEFORMATEX)),
                  (["out"], POINTER(POINTER(WAVEFORMATEX)))),
        COMMETHOD([], HRESULT, "GetMixFormat", (["out"], POINTER(POINTER(WAVEFORMATEX)))),
        COMMETHOD([], HRESULT, "GetDevicePeriod", (["out"], POINTER(ctypes.c_longlong)),
                  (["out"], POINTER(ctypes.c_longlong))),
        COMMETHOD([], HRESULT, "Start"),
        COMMETHOD([], HRESULT, "Stop"),
        COMMETHOD([], HRESULT, "Reset"),
        COMMETHOD([], HRESULT, "SetEventHandle", (["in"], wintypes.HANDLE)),
        COMMETHOD([], HRESULT, "GetService", (["in"], POINTER(GUID)), (["out"], POINTER(c_void_p))),
    ]


class IAudioCaptureClient(IUnknown):
    _iid_ = GUID("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetBuffer", (["out"], POINTER(c_void_p)), (["out"], POINTER(wintypes.UINT)),
                  (["out"], POINTER(wintypes.DWORD)), (["in"], c_void_p), (["in"], c_void_p)),
        COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], wintypes.UINT)),
        COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], POINTER(wintypes.UINT))),
    ]


class IAudioSessionControl2(IUnknown):
    _iid_ = GUID("{bfb7ff88-7239-4fc9-8fa2-07c950be9c6d}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(ctypes.c_int))),
        COMMETHOD([], HRESULT, "GetDisplayName", (["out"], POINTER(wintypes.LPWSTR))),
        COMMETHOD([], HRESULT, "SetDisplayName", (["in"], wintypes.LPCWSTR), (["in"], c_void_p)),
        COMMETHOD([], HRESULT, "GetIconPath", (["out"], POINTER(wintypes.LPWSTR))),
        COMMETHOD([], HRESULT, "SetIconPath", (["in"], wintypes.LPCWSTR), (["in"], c_void_p)),
        COMMETHOD([], HRESULT, "GetGroupingParam", (["out"], POINTER(GUID))),
        COMMETHOD([], HRESULT, "SetGroupingParam", (["in"], POINTER(GUID)), (["in"], c_void_p)),
        COMMETHOD([], HRESULT, "RegisterAudioSessionNotification", (["in"], c_void_p)),
        COMMETHOD([], HRESULT, "UnregisterAudioSessionNotification", (["in"], c_void_p)),
        COMMETHOD([], HRESULT, "GetSessionIdentifier", (["out"], POINTER(wintypes.LPWSTR))),
        COMMETHOD([], HRESULT, "GetSessionInstanceIdentifier", (["out"], POINTER(wintypes.LPWSTR))),
        COMMETHOD([], HRESULT, "GetProcessId", (["out"], POINTER(wintypes.DWORD))),
        COMMETHOD([], HRESULT, "IsSystemSoundsSession"),
    ]


class IAudioSessionEnumerator(IUnknown):
    _iid_ = GUID("{E2F5BB11-0570-40CA-ACDD-3AA01277DEE8}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(ctypes.c_int))),
        COMMETHOD([], HRESULT, "GetSession", (["in"], ctypes.c_int), (["out"], POINTER(POINTER(IUnknown)))),
    ]


class IAudioSessionManager2(IUnknown):
    _iid_ = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetAudioSessionControl", (["in"], c_void_p), (["in"], wintypes.DWORD),
                  (["out"], POINTER(c_void_p))),
        COMMETHOD([], HRESULT, "GetSimpleAudioVolume", (["in"], c_void_p), (["in"], wintypes.DWORD),
                  (["out"], POINTER(c_void_p))),
        COMMETHOD([], HRESULT, "GetSessionEnumerator", (["out"], POINTER(POINTER(IAudioSessionEnumerator)))),
    ]


class IActivateAudioInterfaceAsyncOperation(IUnknown):
    _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetActivateResult", (["out"], POINTER(HRESULT)),
                  (["out"], POINTER(POINTER(IUnknown)))),
    ]


class IActivateAudioInterfaceCompletionHandler(IUnknown):
    _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
    _methods_ = [
        COMMETHOD([], HRESULT, "ActivateCompleted", (["in"], POINTER(IActivateAudioInterfaceAsyncOperation))),
    ]


class IAgileObject(IUnknown):
    _iid_ = GUID("{94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90}")
    _methods_ = []


class _Completion(comtypes.COMObject):
    _com_interfaces_ = [IActivateAudioInterfaceCompletionHandler, IAgileObject]

    def __init__(self):
        super().__init__()
        self.done = threading.Event()

    def ActivateCompleted(self, operation):  # noqa: N802 (COM name)
        self.done.set()
        return 0


_mmdevapi = ctypes.WinDLL("Mmdevapi")
_mmdevapi.ActivateAudioInterfaceAsync.argtypes = [wintypes.LPCWSTR, POINTER(GUID), POINTER(PROPVARIANT),
                                                  c_void_p, POINTER(c_void_p)]
_mmdevapi.ActivateAudioInterfaceAsync.restype = HRESULT
_ole32 = ctypes.WinDLL("ole32")
_ole32.CoTaskMemFree.argtypes = [c_void_p]
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


# --- what can be captured ----------------------------------------------------

@dataclass
class Device:
    id: str
    name: str
    default: bool


@dataclass
class AppSource:
    exe: str      # "chrome.exe", how the choice is saved
    pid: int      # the process tree to capture right now
    title: str


def _com() -> None:
    """COM for this thread, whatever mode it already has."""
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except OSError:
        pass  # already set up (the UI thread is single-threaded)


def _enumerator():
    return comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, CLSCTX_ALL)


def _device_name(dev) -> str:
    store = dev.OpenPropertyStore(0)
    pv = PROPVARIANT()
    store.GetValue(byref(PKEY_Device_FriendlyName), byref(pv))
    name = pv.u.pwszVal if pv.vt == VT_LPWSTR else ""
    return name or "Audio device"


def list_devices() -> list[Device]:
    """Playback devices that can be captured in loopback mode."""
    _com()
    en = _enumerator()
    try:
        default_id = en.GetDefaultAudioEndpoint(eRender, eConsole).GetId()
    except OSError:
        default_id = ""
    coll = en.EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE)
    out = []
    for i in range(coll.GetCount()):
        dev = coll.Item(i)
        dev_id = dev.GetId()
        out.append(Device(dev_id, _device_name(dev), dev_id == default_id))
    return out


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260)]


def processes() -> dict[int, tuple[str, int]]:
    """pid -> (exe name, parent pid) for every running process."""
    snap = _kernel32.CreateToolhelp32Snapshot(0x2, 0)
    out = {}
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    ok = _kernel32.Process32FirstW(ctypes.c_void_p(snap), byref(entry))
    while ok:
        out[entry.th32ProcessID] = (entry.szExeFile, entry.th32ParentProcessID)
        ok = _kernel32.Process32NextW(ctypes.c_void_p(snap), byref(entry))
    _kernel32.CloseHandle(ctypes.c_void_p(snap))
    return out


def _root_of(pid: int, procs: dict[int, tuple[str, int]]) -> int:
    """Browsers and Discord play sound from a helper process; capture from the
    top process of the same exe, so the whole app (and a restarted helper) is
    included."""
    exe = procs.get(pid, ("", 0))[0].lower()
    seen = {pid}
    while True:
        parent = procs.get(pid, ("", 0))[1]
        if parent in seen or procs.get(parent, ("", 0))[0].lower() != exe:
            return pid
        seen.add(parent)
        pid = parent


def list_apps() -> list[AppSource]:
    """Apps that have an audio session on any playback device right now."""
    _com()
    procs = processes()
    en = _enumerator()
    coll = en.EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE)
    found: dict[str, AppSource] = {}
    for i in range(coll.GetCount()):
        try:
            mgr = ctypes.cast(coll.Item(i).Activate(byref(IAudioSessionManager2._iid_), CLSCTX_ALL, None),
                              POINTER(IAudioSessionManager2))
            sessions = mgr.GetSessionEnumerator()
            for j in range(sessions.GetCount()):
                ctl = sessions.GetSession(j).QueryInterface(IAudioSessionControl2)
                pid = ctl.GetProcessId()
                if not pid or pid not in procs:
                    continue
                exe = procs[pid][0]
                if exe.lower() not in found:
                    found[exe.lower()] = AppSource(exe, _root_of(pid, procs), exe.rsplit(".", 1)[0])
        except (OSError, comtypes.COMError):
            continue
    return sorted(found.values(), key=lambda a: a.title.lower())


def find_app(exe: str) -> int | None:
    """The process tree to capture for a saved app choice, if it is running."""
    procs = processes()
    for app in list_apps():
        if app.exe.lower() == exe.lower():
            return app.pid
    pids = [pid for pid, (name, _) in procs.items() if name.lower() == exe.lower()]
    return _root_of(pids[0], procs) if pids else None


# --- capture -------------------------------------------------------------------

def _format(rate: int, channels: int) -> WAVEFORMATEX:
    fmt = WAVEFORMATEX()
    fmt.wFormatTag, fmt.nChannels, fmt.nSamplesPerSec, fmt.wBitsPerSample = WAVE_FORMAT_PCM, channels, rate, 16
    fmt.nBlockAlign = channels * 2
    fmt.nAvgBytesPerSec = rate * fmt.nBlockAlign
    return fmt


def _process_client(pid: int):
    params = AUDIOCLIENT_ACTIVATION_PARAMS(1, pid, 0)  # PROCESS_LOOPBACK, include the process tree
    pv = PROPVARIANT()
    pv.vt = VT_BLOB
    pv.u.blob.cbSize = ctypes.sizeof(params)
    pv.u.blob.pBlobData = ctypes.cast(byref(params), c_void_p)
    handler = _Completion()
    op = c_void_p()
    iid = GUID(str(IAudioClient._iid_))
    handler_ptr = handler.QueryInterface(IActivateAudioInterfaceCompletionHandler)
    _mmdevapi.ActivateAudioInterfaceAsync("VAD\\Process_Loopback", byref(iid), byref(pv),
                                          ctypes.cast(handler_ptr, c_void_p), byref(op))
    if not handler.done.wait(5):
        raise OSError("Windows did not open the app's audio in time")
    operation = ctypes.cast(op, POINTER(IActivateAudioInterfaceAsyncOperation))
    hr, unk = operation.GetActivateResult()
    if hr < 0:
        raise OSError(f"Can't capture that app's audio (0x{hr & 0xFFFFFFFF:08X})")
    return unk.QueryInterface(IAudioClient)


def _device_client(device_id: str):
    en = _enumerator()
    dev = en.GetDevice(device_id) if device_id else en.GetDefaultAudioEndpoint(eRender, eConsole)
    return ctypes.cast(dev.Activate(byref(IAudioClient._iid_), CLSCTX_ALL, None), POINTER(IAudioClient)), dev.GetId()


class Capture:
    """Reads one source on its own thread; chunks of float32 audio come out of
    `queue` with the wall-clock time (time.monotonic) of their last sample."""

    MAX_QUEUED = 2000  # ~20 s of 10 ms packets; older audio is dropped beyond that

    def __init__(self, mode: str = "device", device_id: str = "", app_exe: str = ""):
        self.mode, self.device_id, self.app_exe = mode, device_id, app_exe
        self.queue: queue.Queue = queue.Queue()
        self.error = ""
        self.level = 0.0  # RMS of the latest packet, for the settings meter
        self.packets = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="glosslane-capture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(2)

    def _push(self, chunk: np.ndarray) -> None:
        if self.queue.qsize() > self.MAX_QUEUED:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
        self.queue.put((chunk, time.monotonic()))

    def _open(self):
        if self.mode == "app":
            pid = find_app(self.app_exe)
            if pid is None:
                raise OSError(f"{self.app_exe} is not running")
            client, key = _process_client(pid), f"pid:{pid}"
        else:
            client, key = _device_client(self.device_id)
        fmt = _format(RATE, 1)
        flags = (AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM
                 | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY)
        client.Initialize(AUDCLNT_SHAREMODE_SHARED, flags, 2_000_000, 0, byref(fmt), None)  # 200 ms buffer
        capture = ctypes.cast(client.GetService(byref(IAudioCaptureClient._iid_)), POINTER(IAudioCaptureClient))
        return client, capture, key

    def _run(self) -> None:
        _com()
        while not self._stop.is_set():
            try:
                client, capture, key = self._open()
                self.error = ""
                client.Start()
                self._pump(client, capture, key)
                client.Stop()
            except (OSError, comtypes.COMError) as exc:
                self.error = str(exc)
            # The device went away, the app quit, or opening failed: try again soon.
            self._stop.wait(1.5)

    def _pump(self, client, capture, key: str) -> None:
        last_check = time.monotonic()
        while not self._stop.is_set():
            size = capture.GetNextPacketSize()
            if not size:
                time.sleep(0.01)
                now = time.monotonic()
                if now - last_check > 3:  # notice a new default device or a closed app
                    last_check = now
                    if self._moved(key):
                        return
                continue
            data, frames, flags = capture.GetBuffer(None, None)
            if frames:
                if flags & AUDCLNT_BUFFERFLAGS_SILENT or not data:
                    chunk = np.zeros(frames, np.float32)
                else:
                    raw = ctypes.string_at(data, frames * 2)
                    chunk = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
                capture.ReleaseBuffer(frames)
                self.level = float(np.sqrt(np.mean(chunk * chunk))) if len(chunk) else 0.0
                self.packets += 1
                self._push(chunk)
            else:
                capture.ReleaseBuffer(0)

    def _moved(self, key: str) -> bool:
        try:
            if self.mode == "app":
                pid = int(key.split(":")[1])
                return pid not in processes()
            if not self.device_id:
                return _enumerator().GetDefaultAudioEndpoint(eRender, eConsole).GetId() != key
        except (OSError, comtypes.COMError):
            return True
        return False
