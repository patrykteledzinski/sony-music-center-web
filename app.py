#!/usr/bin/env python3

from flask import (
    Flask, request, jsonify, send_file,
    abort, Response
)
from pathlib import Path
from urllib.parse import quote, urlparse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
import html
import json
import mimetypes
import subprocess
import os
import re
import threading
import time
import uuid
import math
import hashlib

from sony_youtube import YouTubeResolver
from sony_group import GroupPlayback, VolumioAPI
from volumio_discovery import VolumioMonitor
from sony_discovery import SonyEndpoint
from sony_config import load_settings
from sony_seek import SeekCache
from sony_queue import QueueFollower
from sony_party import PartyClient, PartyError, PartyConflict
from werkzeug.exceptions import BadRequest, UnsupportedMediaType, HTTPException

app = Flask(__name__)
app.logger.setLevel("INFO")

# ============================================================
# KONFIGURACJA
# ============================================================

PROJECT_DIR = Path(__file__).resolve().parent
settings = load_settings(PROJECT_DIR)
SONY_IP = settings.sony_ip
SONY_PORT = settings.sony_port
SONY_BASE = f"http://{SONY_IP}:{SONY_PORT}"
SERVER_IP = settings.server_ip
WEB_PORT = settings.web_port
MUSIC_ROOT = settings.music_root
APP_DIR = settings.state_dir
DATA_DIR = APP_DIR / "data"
PLAYLIST_DIR = APP_DIR / "playlists"

QUEUE_FILE = DATA_DIR / "queue.json"
RADIO_FILE = DATA_DIR / "radio.json"

AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".wav", ".m4a", ".aac",
    ".ogg", ".opus", ".wma"
}

IMAGE_NAMES = [
    "cover.jpg", "folder.jpg", "front.jpg",
    "cover.jpeg", "folder.jpeg", "front.jpeg",
    "cover.png", "folder.png", "front.png"
]

DATA_DIR.mkdir(parents=True, exist_ok=True)
PLAYLIST_DIR.mkdir(parents=True, exist_ok=True)

queue_lock = threading.RLock()
play_lock = threading.RLock()
seek_cache = SeekCache(APP_DIR / "cache/seek", PROJECT_DIR, settings.ffmpeg_binary)
sony_config_file = Path(os.environ.get("SONY_WEB_CONFIG", "config.json")).expanduser()
if not sony_config_file.is_absolute():
    sony_config_file = PROJECT_DIR / sony_config_file
sony_endpoint = SonyEndpoint(
    SONY_BASE, DATA_DIR / "sony_device.json", sony_config_file, app.logger,
)
party_client = PartyClient(SONY_BASE, opener=sony_endpoint.open)
browser_cache = SeekCache(APP_DIR / "cache/browser", PROJECT_DIR, settings.ffmpeg_binary)
browser_audio_lock = threading.RLock()
queue_follower = QueueFollower()
queue_monitor_stop = threading.Event()
volumio_monitor = VolumioMonitor(
    f"http://{settings.volumio_ip}:{settings.volumio_port}" if settings.volumio_ip else None,
    DATA_DIR / "volumio_device.json", app.logger,
)

# Stan kolejki zarządzanej przez panel.
queue_state = {
    "items": [],
    "index": -1,
    "source": "",
    "updated": 0,
}

# ============================================================
# NARZĘDZIA
# ============================================================

def safe_path(relative=""):
    relative = str(relative or "").lstrip("/")
    target = (MUSIC_ROOT / relative).resolve()

    try:
        target.relative_to(MUSIC_ROOT)
    except ValueError:
        abort(403)

    return target


def relative_path(path):
    return str(Path(path).resolve().relative_to(MUSIC_ROOT))


def is_audio(path):
    return path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS


def clean_title(filename):
    title = Path(filename).stem

    # Usunięcie typowego ID YouTube z końca nazwy.
    title = re.sub(r"\s*\[[A-Za-z0-9_-]{6,}\]\s*$", "", title)

    return title.strip()


def natural_key(text):
    return [
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", str(text))
    ]


def format_seconds(value):
    try:
        value = int(value)
    except Exception:
        return "0:00"

    hours, rem = divmod(value, 3600)
    minutes, seconds = divmod(rem, 60)

    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"

    return f"{minutes}:{seconds:02d}"


def hms_seconds(value):
    if not value or value == "NOT_IMPLEMENTED":
        return 0

    try:
        parts = [int(x) for x in value.split(":")]

        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]

        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
    except Exception:
        pass

    return 0


def xml_value(xml_text, tag):
    match = re.search(
        rf"<(?:\w+:)?{re.escape(tag)}[^>]*>(.*?)</(?:\w+:)?{re.escape(tag)}>",
        xml_text,
        re.S | re.I,
    )

    if not match:
        return ""

    return html.unescape(match.group(1)).strip()


def json_read(path, default):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def json_write(path, data):
    tmp = Path(str(path) + ".tmp")

    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)

    tmp.replace(path)


# ============================================================
# SONY / SOAP
# ============================================================

class SonySoapError(RuntimeError):
    def __init__(self, service, action, http_status, details):
        self.upnp_code = xml_value(details, "errorCode")
        self.description = xml_value(details, "errorDescription")
        self.action = action
        self.details = details
        message = f"Sony {service}/{action}: HTTP {http_status}"
        if self.upnp_code:
            message += f", UPnP {self.upnp_code}"
        message += ": " + (self.description or "Błąd odpowiedzi urządzenia")
        super().__init__(message)


def soap(service, action, body="", timeout=4):
    envelope = f"""<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
 s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
<s:Body>
<u:{action} xmlns:u="urn:schemas-upnp-org:service:{service}:1">
{body}
</u:{action}>
</s:Body>
</s:Envelope>"""

    url = f"{SONY_BASE}/{service}/ctrl"

    req = urllib.request.Request(
        url,
        data=envelope.encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction":
                f'"urn:schemas-upnp-org:service:{service}:1#{action}"',
        },
    )

    try:
        with sony_endpoint.open(req, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        error = SonySoapError(service, action, exc.code, details)
        app.logger.warning("%s", error)
        raise error from exc
    except Exception as exc:
        raise RuntimeError(f"Brak połączenia z Sony: {exc}")


def sony_transport(action, extra=""):
    return soap(
        "AVTransport",
        action,
        f"<InstanceID>0</InstanceID>{extra}",
    )


def sony_play():
    with play_lock:
        refresh_stopped_file_metadata()
        queue_follower.resume()
        sony_transport("Play", "<Speed>1</Speed>")


def refresh_stopped_file_metadata():
    # Restart panelu nie zmienia źródła zapamiętanego przez Sony.
    # Naprawiamy stary opis tylko przed startem zatrzymanego pliku.
    state = xml_value(
        sony_transport("GetTransportInfo"), "CurrentTransportState"
    )
    if state != "STOPPED":
        return
    info = sony_transport("GetPositionInfo")
    uri = xml_value(info, "TrackURI")
    parsed = urlparse(uri)
    if (parsed.scheme != "http" or parsed.netloc != f"{SERVER_IP}:{WEB_PORT}"
            or not parsed.path.startswith("/media/")):
        return
    metadata = xml_value(info, "TrackMetaData")
    if "DLNA.ORG_OP=01" in metadata and "DLNA.ORG_FLAGS=" in metadata:
        return
    relative = urllib.parse.unquote(parsed.path[len("/media/"):])
    set_transport_file(relative, autoplay=False)


def sony_play_confirmed():
    try:
        sony_play()
    except Exception as exc:
        # After changing URI Sony may acknowledge the source before it is ready.
        # Some sources also start automatically and reject a redundant Play.
        for attempt in range(21):
            transport = sony_transport("GetTransportInfo")
            state = xml_value(transport, "CurrentTransportState")
            if state == "PLAYING":
                return
            if xml_value(transport, "CurrentTransportStatus") == "ERROR_OCCURRED":
                raise
            transient = str(getattr(exc, "upnp_code", "")) == "701"
            if state != "TRANSITIONING" and not transient:
                raise
            if attempt == 20:
                raise
            time.sleep(0.25)
            if transient and state == "STOPPED" and attempt % 4 == 3:
                try:
                    sony_transport("Play", "<Speed>1</Speed>")
                except Exception as retry_error:
                    if str(getattr(retry_error, "upnp_code", "")) != "701":
                        raise



def sony_pause():
    with play_lock:
        sony_transport("Pause")
        for attempt in range(6):
            state = xml_value(
                sony_transport("GetTransportInfo"), "CurrentTransportState"
            )
            if state == "PAUSED_PLAYBACK":
                return
            if attempt < 5:
                time.sleep(0.2)
        raise RuntimeError(
            "Sony nie potwierdziło pauzy. Uruchom utwór ponownie z biblioteki; "
            "radio internetowe może nie obsługiwać pauzy."
        )


def sony_stop():
    with play_lock:
        queue_follower.suppress()
        sony_transport("Stop")


def sony_next_native():
    sony_transport("Next")


def sony_previous_native():
    sony_transport("Previous")


def sony_set_mode(mode):
    allowed = {
        "NORMAL",
        "RANDOM",
        "REPEAT_ONE",
        "REPEAT_ALL",
    }

    if mode not in allowed:
        raise ValueError("Nieprawidłowy tryb")

    sony_transport(
        "SetPlayMode",
        f"<NewPlayMode>{mode}</NewPlayMode>",
    )


def sony_set_volume(front_volume):
    front_volume = max(0, min(33, int(front_volume)))
    upnp_volume = max(0, min(100, front_volume * 3))

    soap(
        "RenderingControl",
        "SetVolume",
        "<InstanceID>0</InstanceID>"
        "<Channel>Master</Channel>"
        f"<DesiredVolume>{upnp_volume}</DesiredVolume>",
    )


def sony_status():
    result = {
        "online": False,
        "state": "UNKNOWN",
        "title": "",
        "artist": "",
        "album": "",
        "uri": "",
        "time": "0:00:00",
        "duration": "0:00:00",
        "position": 0,
        "duration_seconds": 0,
        "volume": 0,
        "mode": "NORMAL",
    }

    try:
        transport = sony_transport("GetTransportInfo")
        info = sony_transport("GetPositionInfo")
        position_sampled_at = time.monotonic()

        volume_xml = soap(
            "RenderingControl",
            "GetVolume",
            "<InstanceID>0</InstanceID>"
            "<Channel>Master</Channel>",
        )

        settings = sony_transport("GetTransportSettings")

        result["state"] = xml_value(
            transport, "CurrentTransportState"
        ) or "UNKNOWN"

        result["time"] = xml_value(info, "RelTime") or "0:00:00"
        result["duration"] = (
            xml_value(info, "TrackDuration") or "0:00:00"
        )
        result["uri"] = xml_value(info, "TrackURI")

        metadata_raw = xml_value(info, "TrackMetaData")

        if metadata_raw:
            result["title"] = xml_value(metadata_raw, "title")
            result["artist"] = xml_value(metadata_raw, "creator")
            result["album"] = xml_value(metadata_raw, "album")

        if not result["title"] and result["uri"]:
            try:
                result["title"] = clean_title(
                    urllib.parse.unquote(
                        result["uri"].split("/")[-1]
                    )
                )
            except Exception:
                pass

        raw_volume = xml_value(volume_xml, "CurrentVolume")

        try:
            result["volume"] = round(int(raw_volume) / 3)
        except Exception:
            result["volume"] = 0

        result["mode"] = (
            xml_value(settings, "PlayMode") or "NORMAL"
        )

        result["position"] = hms_seconds(result["time"])
        result["duration_seconds"] = hms_seconds(
            result["duration"]
        )

        result["position_age"] = max(0, time.monotonic() - position_sampled_at)
        result["browser_source"] = None
        result["seekable"] = False
        context = local_playback_context(result["uri"])
        if context:
            result["browser_source"] = context["source"]
            result["seekable"] = result["duration_seconds"] > 0
            if context.get("token"):
                result["position"] = min(
                    int(context["duration"]),
                    int(context["offset"] + result["position"]),
                )
                result["duration_seconds"] = int(context["duration"])
                result["time"] = format_seconds(result["position"])
                result["duration"] = format_seconds(result["duration_seconds"])
                result["title"] = clean_title(Path(context["source"]).name)
                result["seekable"] = True

        result["online"] = True

    except Exception as exc:
        result["error"] = str(exc)

    return result


# ============================================================
# ODTWARZANIE PLIKÓW
# ============================================================

def local_playback_context(uri):
    parsed = urlparse(uri)
    if parsed.scheme != "http" or parsed.netloc != f"{SERVER_IP}:{WEB_PORT}":
        return None
    if parsed.path.startswith("/media/"):
        relative = urllib.parse.unquote(parsed.path[len("/media/"):])
        path = safe_path(relative)
        if is_audio(path):
            return {"source": relative_path(path), "offset": 0, "token": None}
    if parsed.path.startswith("/seek-media/") and Path(parsed.path).suffix in (".mp3", ".wav", ".m4a", ".aac"):
        token = Path(parsed.path).stem
        try:
            record = seek_cache.record(token)
        except ValueError:
            return None
        if (parsed.path != f"/seek-media/{token}.{record['extension']}"
                or not is_audio(safe_path(record["source"]))):
            return None
        return {**record, "token": token}
    return None


def sony_seek(seconds, expected_uri):
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds):
        raise ValueError("Pozycja musi być skończoną liczbą sekund")
    with play_lock:
        info = sony_transport("GetPositionInfo")
        uri = xml_value(info, "TrackURI")
        if not expected_uri or expected_uri != uri:
            raise ValueError("Utwór się zmienił. Poczekaj na odświeżenie panelu.")
        context = local_playback_context(uri)
        if not context:
            raise ValueError("Przewijanie jest dostępne tylko dla plików z biblioteki")
        state = xml_value(sony_transport("GetTransportInfo"), "CurrentTransportState")
        if state not in ("PLAYING", "PAUSED_PLAYBACK"):
            raise ValueError("Najpierw uruchom utwór")
        path = safe_path(context["source"])
        audio = validate_sony_audio(path)
        output_rate = min(audio["rate"], 48000) if audio and audio["source_codec"] != "aac" else None
        duration = seek_cache.duration(path)
        token, record = seek_cache.create(path, context["source"], seconds, duration,
                                          keep_token=context.get("token"), output_rate=output_rate)
        # A physical remote may change the source while FFmpeg prepares the file.
        if xml_value(sony_transport("GetPositionInfo"), "TrackURI") != uri:
            raise ValueError("Utwór zmienił się podczas przewijania. Spróbuj ponownie.")
        latest_state = xml_value(sony_transport("GetTransportInfo"), "CurrentTransportState")
        if latest_state != state:
            raise ValueError("Stan odtwarzania zmienił się podczas przewijania. Spróbuj ponownie.")
        extension = record["extension"]
        url = f"http://{SERVER_IP}:{WEB_PORT}/seek-media/{token}.{extension}"
        metadata = didl_metadata(Path(path.stem + "." + extension), url)
        sony_transport("SetAVTransportURI",
                       "<CurrentURI>" + html.escape(url) + "</CurrentURI>"
                       "<CurrentURIMetaData>" + html.escape(metadata) + "</CurrentURIMetaData>")
        sony_play_confirmed()
        if state == "PAUSED_PLAYBACK":
            sony_pause()
        return {"position": int(seconds), "duration_seconds": int(duration), "uri": url}


def music_url(relative):
    encoded = quote(
        relative.replace("\\", "/"),
        safe="/"
    )

    return (
        f"http://{SERVER_IP}:{WEB_PORT}"
        f"/media/{encoded}"
    )


def dlna_features(mime):
    # Pliki są serwowane bez transkodowania, z obsługą zakresów bajtowych.
    profile = "DLNA.ORG_PN=MP3;" if mime == "audio/mpeg" else ""
    return (
        profile + "DLNA.ORG_OP=01;DLNA.ORG_CI=0;"
        "DLNA.ORG_FLAGS=01700000000000000000000000000000"
    )


def didl_metadata(path, url):
    title = html.escape(clean_title(path.name))
    url_xml = html.escape(url)

    ext = path.suffix.lower()

    mime = {".wav": "audio/wav", ".aac": "audio/vnd.dlna.adts"}.get(
        path.suffix.lower(), mimetypes.guess_type(path.name)[0]
    )

    if not mime:
        if ext == ".mp3":
            mime = "audio/mpeg"
        elif ext == ".flac":
            mime = "audio/flac"
        else:
            mime = "audio/mpeg"

    protocol = f"http-get:*:{mime}:{dlna_features(mime)}"

    return (
        '<DIDL-Lite '
        'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="1" parentID="0" restricted="1">'
        f"<dc:title>{title}</dc:title>"
        "<upnp:class>object.item.audioItem.musicTrack</upnp:class>"
        f'<res protocolInfo="{protocol}">{url_xml}</res>'
        "</item>"
        "</DIDL-Lite>"
    )


def validate_sony_audio(path):
    if path.suffix.lower() == ".mp3":
        return None
    audio = seek_cache.pcm_format(path)
    if audio["source_codec"] == "aac":
        return audio
    if audio["channels"] not in (1, 2) or audio["bits"] > 24 or audio["codec"].startswith("pcm_f"):
        raise ValueError("Sony nie obsługuje tych parametrów PCM bez zmiany jakości")
    return audio


def set_transport_file(relative, autoplay=True):
    path = safe_path(relative)

    if not is_audio(path):
        raise ValueError("To nie jest obsługiwany plik audio")

    rel = relative_path(path)
    audio = validate_sony_audio(path)
    if audio and (audio["source_codec"] not in ("pcm_s16le", "pcm_s24le", "pcm_u8")
                  or (audio["source_codec"] != "aac" and audio["rate"] > 48000)):
        # Normalize AAC to ADTS without reencoding; ALAC/FLAC need PCM <= 48 kHz.
        token, record = seek_cache.create(path, rel, 0, seek_cache.duration(path),
                                          output_rate=(min(audio["rate"], 48000)
                                                       if audio["source_codec"] != "aac" else None))
        extension = record["extension"]
        url = f"http://{SERVER_IP}:{WEB_PORT}/seek-media/{token}.{extension}"
        metadata = didl_metadata(Path(path.stem + "." + extension), url)
    else:
        url = music_url(rel)
        metadata = didl_metadata(path, url)

    sony_transport(
        "SetAVTransportURI",
        "<CurrentURI>"
        + html.escape(url)
        + "</CurrentURI>"
        "<CurrentURIMetaData>"
        + html.escape(metadata)
        + "</CurrentURIMetaData>",
    )

    if autoplay:
        sony_play_confirmed()

    return rel



# ============================================================
# RADIO INTERNETOWE
# ============================================================

radio_lock = threading.RLock()


def validate_radio_url(url):
    url = str(url or "").strip()

    if not url:
        raise ValueError("Podaj adres URL radia")

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            "Adres radia musi zaczynać się od http:// lub https://"
        )

    if not parsed.hostname:
        raise ValueError("Nieprawidłowy adres URL radia")

    return url


def radio_stations():
    with radio_lock:
        data = json_read(RADIO_FILE, [])

        if not isinstance(data, list):
            return []

        result = []

        for item in data:
            if not isinstance(item, dict):
                continue

            name = str(item.get("name", "")).strip()
            url = str(item.get("url", "")).strip()

            if not name or not url:
                continue

            result.append({
                "id": str(item.get("id", "")),
                "name": name,
                "url": url,
            })

        return result


def save_radio_stations(stations):
    with radio_lock:
        json_write(RADIO_FILE, stations)


def radio_metadata(name, url):
    title = html.escape(name or "Radio internetowe")
    url_xml = html.escape(url)

    return (
        '<DIDL-Lite '
        'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="radio" parentID="0" restricted="1">'
        f"<dc:title>{title}</dc:title>"
        "<upnp:class>object.item.audioItem.audioBroadcast</upnp:class>"
        f'<res protocolInfo="http-get:*:audio/mpeg:*">'
        f"{url_xml}</res>"
        "</item>"
        "</DIDL-Lite>"
    )


def set_transport_radio(url, name="Radio internetowe"):
    with play_lock:
        url = validate_radio_url(url)

        # MX700Ni w naszym teście odrzucił HTTPS kodem UPnP 600.
        # Jeśli użytkownik poda prosty adres HTTPS, próbujemy także
        # odpowiednika HTTP. Nie zmieniamy innych elementów adresu.
        candidates = [url]

        parsed = urlparse(url)

        if parsed.scheme == "https":
            http_url = "http://" + url[len("https://"):]
            candidates.append(http_url)

        last_error = None

        for candidate in candidates:
            metadata = radio_metadata(name, candidate)

            try:
                sony_transport(
                    "SetAVTransportURI",
                    "<CurrentURI>"
                    + html.escape(candidate)
                    + "</CurrentURI>"
                    "<CurrentURIMetaData>"
                    + html.escape(metadata)
                    + "</CurrentURIMetaData>",
                )

                sony_play_confirmed()

                return candidate

            except Exception as exc:
                last_error = exc

        if last_error:
            raise last_error

        raise RuntimeError("Nie udało się uruchomić radia")

def add_radio_station(name, url):
    with radio_lock:
        name = str(name or "").strip()
        url = validate_radio_url(url)

        if not name:
            raise ValueError("Podaj nazwę stacji")

        stations = radio_stations()

        station = {
            "id": uuid.uuid4().hex,
            "name": name[:100],
            "url": url,
        }

        stations.append(station)
        save_radio_stations(stations)

        return station

def delete_radio_station(station_id):
    with radio_lock:
        station_id = str(station_id or "").strip()
        stations = radio_stations()

        new_stations = [
            station
            for station in stations
            if station.get("id") != station_id
        ]

        if len(new_stations) == len(stations):
            raise ValueError("Nie znaleziono stacji")

        save_radio_stations(new_stations)

def find_radio_station(station_id):
    for station in radio_stations():
        if station.get("id") == station_id:
            return station

    raise ValueError("Nie znaleziono stacji")



# ============================================================
# KOLEJKA
# ============================================================

def save_queue():
    with queue_lock:
        queue_state["updated"] = int(time.time())
        json_write(QUEUE_FILE, queue_state)


def load_queue():
    stored = json_read(QUEUE_FILE, {})

    with queue_lock:
        if isinstance(stored, dict):
            queue_state["items"] = stored.get("items", [])
            queue_state["index"] = stored.get("index", -1)
            queue_state["source"] = stored.get("source", "")
            queue_state["updated"] = stored.get("updated", 0)
            queue_state["current_removed"] = stored.get("current_removed", False)


def queue_public():
    with queue_lock:
        items = []

        for index, rel in enumerate(queue_state["items"]):
            path = safe_path(rel)

            items.append({
                "index": index,
                "path": rel,
                "title": clean_title(path.name),
                "active": (index == queue_state["index"]
                           and not queue_state.get("current_removed", False)),
            })

        return {
            "items": items,
            "index": queue_state["index"],
            "source": queue_state["source"],
        }


def play_queue_index(index):
    with play_lock, queue_lock:
        if not queue_state["items"]:
            raise ValueError("Kolejka jest pusta")
        index = int(index)
        if index < 0 or index >= len(queue_state["items"]):
            raise ValueError("Pozycja poza kolejką")
        relative = queue_state["items"][index]
        set_transport_file(relative)
        queue_state["index"] = index
        queue_state["current_removed"] = False
        save_queue()
        return relative


def set_queue(items, source="", start=0):
    clean = []
    for item in items:
        try:
            path = safe_path(item)
            if is_audio(path):
                clean.append(relative_path(path))
        except Exception:
            continue
    if not clean:
        raise ValueError("Brak plików audio")
    index = max(0, min(int(start), len(clean) - 1))
    with play_lock, queue_lock:
        relative = set_transport_file(clean[index])
        queue_state.update(items=clean, index=index, source=source,
                           current_removed=False)
        save_queue()
        return relative


def queue_next():
    with play_lock, queue_lock:
        if not queue_state["items"]:
            sony_next_native()
            return
        new_index = (queue_state["index"] + 1) % len(queue_state["items"])
        play_queue_index(new_index)


def queue_previous():
    with play_lock, queue_lock:
        if not queue_state["items"]:
            sony_previous_native()
            return
        offset = 0 if queue_state.get("current_removed") else 1
        new_index = (queue_state["index"] - offset) % len(queue_state["items"])
        play_queue_index(new_index)


def queue_monitor_tick():
    # Serialize observations and advancement with user controls. In particular,
    # an explicit Stop cancels a pending ending while holding the same lock.
    with play_lock:
        with queue_lock:
            items = list(queue_state["items"])
            index = queue_state["index"]
            removed = queue_state.get("current_removed", False)
        if not items or index < 0 or index >= len(items) or removed:
            queue_follower.cancel()
            return
        transport = sony_transport("GetTransportInfo")
        info = sony_transport("GetPositionInfo")
        uri = xml_value(info, "TrackURI")
        context = local_playback_context(uri)
        position = hms_seconds(xml_value(info, "RelTime"))
        duration = hms_seconds(xml_value(info, "TrackDuration"))
        if context and context.get("token"):
            position += context["offset"]
            duration = context["duration"]
        state = xml_value(transport, "CurrentTransportState")
        mode = "NORMAL"
        if state == "STOPPED" and queue_follower.previous:
            mode = xml_value(sony_transport("GetTransportSettings"), "PlayMode") or "NORMAL"
        status = dict(online=True, state=state, uri=uri,
                      source=context["source"] if context else None,
                      position=position, duration=duration,
                      end_tolerance=3 if context and context.get("extension") == "aac" else 1,
                      error=xml_value(transport, "CurrentTransportStatus") == "ERROR_OCCURRED")
        previous = queue_follower.previous
        next_index = queue_follower.observe(status, items, index, mode, removed)
        if state == "STOPPED" and previous and next_index is None:
            app.logger.info("Monitor playlisty: zatrzymanie bez przejścia; kolejka %s/%s, tryb %s, pozycja %.2f/%.2f s, źródło %s",
                            index + 1, len(items), mode, previous["position"], previous["duration"], status["source"])
        if next_index is not None:
            app.logger.info("Koniec utworu: przechodzę z pozycji %s do %s", index, next_index)
            play_queue_index(next_index)


def queue_monitor_loop():
    while not queue_monitor_stop.wait(1):
        # Long conversions/control operations should not queue background polls.
        if not play_lock.acquire(blocking=False):
            continue
        try:
            queue_monitor_tick()
        except Exception as exc:
            queue_follower.cancel()
            app.logger.warning("Monitor playlisty: %s", exc)
        finally:
            play_lock.release()


load_queue()

# ============================================================
# BIBLIOTEKA
# ============================================================

def folder_contents(relative=""):
    folder = safe_path(relative)

    if not folder.exists() or not folder.is_dir():
        raise FileNotFoundError("Folder nie istnieje")

    folders = []
    tracks = []

    try:
        entries = sorted(
            folder.iterdir(),
            key=lambda p: natural_key(p.name)
        )
    except PermissionError:
        raise PermissionError("Brak dostępu do folderu")

    for entry in entries:
        if entry.name.startswith("."):
            continue

        if entry.is_dir():
            folders.append({
                "name": entry.name,
                "path": relative_path(entry),
            })

        elif is_audio(entry):
            tracks.append({
                "name": entry.name,
                "title": clean_title(entry.name),
                "path": relative_path(entry),
                "size": entry.stat().st_size,
            })

    rel_folder = (
        "" if folder == MUSIC_ROOT
        else relative_path(folder)
    )

    parent = ""

    if folder != MUSIC_ROOT:
        parent_path = folder.parent

        if parent_path != MUSIC_ROOT:
            parent = relative_path(parent_path)

    return {
        "path": rel_folder,
        "parent": parent,
        "folders": folders,
        "tracks": tracks,
    }


def recursive_tracks(relative):
    folder = safe_path(relative)

    if not folder.is_dir():
        raise ValueError("To nie jest folder")

    tracks = [
        relative_path(path)
        for path in folder.rglob("*")
        if is_audio(path)
    ]

    tracks.sort(key=natural_key)

    return tracks


def search_library(query, limit=150):
    query = query.strip().casefold()

    if not query:
        return []

    results = []

    for path in MUSIC_ROOT.rglob("*"):
        if len(results) >= limit:
            break

        if path.name.startswith("."):
            continue

        if query not in path.name.casefold():
            continue

        if path.is_dir():
            results.append({
                "type": "folder",
                "name": path.name,
                "path": relative_path(path),
            })

        elif is_audio(path):
            results.append({
                "type": "track",
                "name": clean_title(path.name),
                "path": relative_path(path),
            })

    return results


# ============================================================
# PLAYLISTY
# ============================================================

def playlist_safe_name(name):
    name = str(name).strip()

    name = re.sub(
        r'[\\/:*?"<>|\x00-\x1f]',
        "_",
        name
    )

    name = name.strip(" .")

    if not name:
        raise ValueError("Podaj nazwę playlisty")

    return name[:120]


def playlist_file(name):
    return PLAYLIST_DIR / (
        playlist_safe_name(name) + ".json"
    )


def list_playlists():
    result = []

    for file in sorted(
        PLAYLIST_DIR.glob("*.json"),
        key=lambda p: natural_key(p.stem)
    ):
        data = json_read(file, {})

        items = data.get("items", [])

        result.append({
            "name": data.get("name", file.stem),
            "count": len(items),
        })

    return result


def read_playlist(name):
    file = playlist_file(name)

    if not file.exists():
        raise FileNotFoundError("Playlista nie istnieje")

    data = json_read(file, {})

    return {
        "name": data.get("name", file.stem),
        "items": data.get("items", []),
    }


def write_playlist(name, items):
    name = playlist_safe_name(name)

    valid = []

    for item in items:
        try:
            path = safe_path(item)

            if is_audio(path):
                valid.append(relative_path(path))
        except Exception:
            pass

    data = {
        "name": name,
        "items": valid,
        "updated": int(time.time()),
    }

    json_write(playlist_file(name), data)

    return data


# ============================================================
# API
# ============================================================


@app.get("/api/radio")
def api_radio_list():
    try:
        return jsonify(
            ok=True,
            stations=radio_stations()
        )

    except Exception as exc:
        return jsonify(
            ok=False,
            error=str(exc)
        ), 500


@app.post("/api/radio/play")
def api_radio_play():
    try:
        data = request.get_json(force=True)

        station_id = str(
            data.get("id", "")
        ).strip()

        if station_id:
            station = find_radio_station(station_id)
            name = station["name"]
            url = station["url"]
        else:
            name = str(
                data.get("name", "Radio internetowe")
            ).strip()

            url = data.get("url", "")

        played_url = set_transport_radio(
            url,
            name or "Radio internetowe"
        )

        return jsonify(
            ok=True,
            name=name,
            url=url,
            played_url=played_url
        )

    except Exception as exc:
        return jsonify(
            ok=False,
            error=str(exc)
        ), 400


@app.post("/api/radio")
def api_radio_save():
    try:
        data = request.get_json(force=True)

        station = add_radio_station(
            data.get("name", ""),
            data.get("url", "")
        )

        return jsonify(
            ok=True,
            station=station
        )

    except Exception as exc:
        return jsonify(
            ok=False,
            error=str(exc)
        ), 400


@app.delete("/api/radio/<station_id>")
def api_radio_delete(station_id):
    try:
        delete_radio_station(station_id)

        return jsonify(ok=True)

    except Exception as exc:
        return jsonify(
            ok=False,
            error=str(exc)
        ), 400


group_playlists = {}
group_playlists_lock = threading.Lock()


def group_playlist_url(uri):
    # Volumio 2's HTTP service first calls MPD load: give it a small playlist,
    # never a multi-hour audio file to parse as playlist data.
    token = hashlib.sha256(uri.encode()).hexdigest()
    with group_playlists_lock:
        group_playlists[token] = uri
        while len(group_playlists) > 16:
            del group_playlists[next(iter(group_playlists))]
    return f"http://{SERVER_IP}:{WEB_PORT}/group-playlist/{token}.m3u"


@app.get("/group-playlist/<token>.m3u")
def group_playlist(token):
    with group_playlists_lock:
        uri = group_playlists.get(token)
    if not uri:
        abort(404)
    return Response("#EXTM3U\n" + uri + "\n", mimetype="audio/x-mpegurl",
                    headers={"Cache-Control": "no-store"})


group_playback = GroupPlayback(sony_status, local_playback_context,
                               VolumioAPI(volumio_monitor), app.logger, group_playlist_url)


def group_origin_allowed():
    origin = request.headers.get("Origin")
    if not origin:
        return True
    def address(value):
        parsed = urlparse(value)
        return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
    return address(origin) in (address(request.host_url),
                              address(volumio_monitor.snapshot().get("url") or ""))


@app.route("/api/group", methods=["GET", "POST", "OPTIONS"])
def api_group():
    if request.method == "GET":
        response = jsonify(group_playback.snapshot())
        response.headers["Access-Control-Allow-Origin"] = "*"
    else:
        if not group_origin_allowed():
            return jsonify(ok=False, error="Niedozwolone źródło żądania"), 403
        if request.method == "OPTIONS":
            response = app.make_response(("", 204))
        else:
            data = request.get_json(silent=True)
            if not isinstance(data, dict) or not isinstance(data.get("enabled"), bool):
                return jsonify(ok=False, error="Nieprawidłowy stan checkboxa"), 400
            try:
                response = jsonify(group_playback.set_enabled(data["enabled"]))
            except Exception as exc:
                response = jsonify(ok=False, error=str(exc))
                response.status_code = 502
        if request.headers.get("Origin"):
            response.headers["Access-Control-Allow-Origin"] = request.headers["Origin"]
            response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/group/control/<action>", methods=["POST", "OPTIONS"])
def api_group_control(action):
    if not group_origin_allowed():
        return jsonify(ok=False, error="Niedozwolone źródło żądania"), 403
    if request.method == "OPTIONS":
        response = app.make_response(("", 204))
    elif not group_playback.snapshot()["enabled"]:
        response = jsonify(ok=False, error="Najpierw włącz Graj razem")
        response.status_code = 409
    elif action in ("play", "pause", "stop", "next", "prev"):
        response = app.make_response(api_control(action))
    elif action == "seek":
        response = app.make_response(api_seek())
    else:
        response = jsonify(ok=False, error="W trybie Graj razem wybierz utwór w bibliotece Sony")
        response.status_code = 400
    if request.headers.get("Origin"):
        response.headers["Access-Control-Allow-Origin"] = request.headers["Origin"]
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


youtube_resolver = YouTubeResolver(PROJECT_DIR)


@app.get("/api/youtube/info/<video_id>")
def api_youtube_info(video_id):
    try:
        info = youtube_resolver.resolve(video_id)
        # Old MPD cannot reliably consume current Google HTTPS/AAC streams.
        # Keep extraction private; hand Volumio one local MP3 stream instead.
        audio = dict(info["formats"][0],
                     url=f"http://{SERVER_IP}:{WEB_PORT}/youtube-audio/{video_id}.mp3",
                     mimeType='audio/mpeg; codecs="mp3"', container="mp3", codecs="mp3",
                     audioCodec="mp3", audioBitrate=192, bitrate=192000, contentLength="")
        return jsonify(dict(info, formats=[audio]))
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    except Exception as exc:
        app.logger.warning("Resolver YouTube: %s", exc)
        return jsonify(ok=False, error=str(exc)), 502


youtube_stream_slots = threading.BoundedSemaphore(2)


@app.route("/youtube-audio/<video_id>.mp3", methods=["GET", "HEAD"])
def youtube_audio(video_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        abort(400)
    if request.method == "HEAD":
        return Response(mimetype="audio/mpeg", headers={"Cache-Control": "no-store"})
    if not youtube_stream_slots.acquire(blocking=False):
        return jsonify(ok=False, error="Zbyt wiele strumieni YouTube"), 429
    source = None
    try:
        info = youtube_resolver.resolve(video_id)
        # The bundled FFmpeg crashes in its HTTPS transport. Python handles TLS;
        # FFmpeg receives only audio bytes through a pipe.
        source = urllib.request.urlopen(info["formats"][0]["url"], timeout=15)
        command = [seek_cache.ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
                   "-re", "-i", "pipe:0", "-vn", "-ac", "2", "-ar", "44100",
                   "-codec:a", "libmp3lame", "-b:a", "192k", "-flush_packets", "1",
                   "-f", "mp3", "pipe:1"]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL)
    except Exception as exc:
        if source is not None:
            source.close()
        youtube_stream_slots.release()
        app.logger.warning("Strumień YouTube: %s", exc)
        return jsonify(ok=False, error="Nie udało się uruchomić strumienia YouTube"), 502

    def feed_audio():
        try:
            while process.poll() is None:
                data = source.read(16384)
                if not data:
                    break
                process.stdin.write(data)
                process.stdin.flush()
        except (OSError, ValueError):
            pass
        finally:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass
            source.close()

    threading.Thread(target=feed_audio, name="youtube-audio-feed", daemon=True).start()
    cleanup_lock = threading.Lock()
    cleaned = False

    def cleanup():
        nonlocal cleaned
        with cleanup_lock:
            if cleaned:
                return
            cleaned = True
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            source.close()
            process.stdout.close()
            youtube_stream_slots.release()

    def stream():
        try:
            while True:
                chunk = process.stdout.read(4096)
                if not chunk:
                    break
                yield chunk
        finally:
            cleanup()

    response = Response(stream(), mimetype="audio/mpeg", headers={"Cache-Control": "no-store"})
    response.call_on_close(cleanup)
    return response


@app.get("/api/studio/status")
def api_studio_status():
    """Read-only Sony preview for the separate Volumio Studio interface."""
    response = jsonify(sony_status())
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/volumio/status")
def api_volumio_status():
    return jsonify(volumio_monitor.snapshot())


@app.get("/api/status")
def api_status():
    status = sony_status()
    status["queue"] = queue_public()
    return jsonify(status)


@app.post("/api/control/<action>")
def api_control(action):
    with play_lock:
        try:
            if action == "play":
                sony_play_confirmed()

            elif action == "pause":
                sony_pause()

            elif action == "stop":
                sony_stop()

            elif action == "next":
                queue_next()

            elif action in ("previous", "prev"):
                queue_previous()

            else:
                return jsonify(
                    ok=False,
                    error="Nieznana komenda"
                ), 400

            return jsonify(ok=True)

        except Exception as exc:
            return jsonify(ok=False, error=str(exc)), 500

def prepare_browser_audio(uri):
    context = local_playback_context(str(uri or ""))
    if not context:
        raise ValueError("Odsłuch w przeglądarce jest dostępny dla plików z biblioteki")
    path = safe_path(context["source"])
    relative = relative_path(path)
    if path.suffix.lower() == ".mp3":
        return {"source": relative, "url": "/media/" + quote(relative, safe="/")}
    # Use WAV for browsers without AAC/ALAC support. Separate cache never removes
    # fragments currently being served to the Sony renderer.
    stat = path.stat()
    key = hashlib.sha256(f"{relative}:{stat.st_mtime_ns}:{stat.st_size}".encode()).hexdigest()
    with browser_audio_lock:
        for file in browser_cache.directory.glob("*.json"):
            try:
                record = browser_cache.record(file.stem)
                if record.get("browser_key") == key:
                    return {"source": relative, "url": f"/browser-media/{file.stem}.wav"}
            except ValueError:
                continue
        audio = browser_cache.pcm_format(path)
        token, record = browser_cache.create(
            path, relative, 0, browser_cache.duration(path),
            output_rate=min(audio["rate"], 48000), force_pcm=True,
        )
        record["browser_key"] = key
        (browser_cache.directory / (token + ".json")).write_text(json.dumps(record), encoding="utf-8")
        return {"source": relative, "url": f"/browser-media/{token}.wav"}


@app.post("/api/browser/audio")
def api_browser_audio():
    try:
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError("Nieprawidłowe dane odsłuchu")
        return jsonify(ok=True, **prepare_browser_audio(data.get("uri")))
    except (ValueError, BadRequest, UnsupportedMediaType) as exc:
        return jsonify(ok=False, error=str(exc)), 400
    except HTTPException as exc:
        return jsonify(ok=False, error=exc.description), exc.code
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 500


@app.get("/browser-media/<token>.wav")
def browser_media(token):
    try:
        record = browser_cache.record(token)
        if record["extension"] != "wav" or not is_audio(safe_path(record["source"])):
            abort(404)
    except ValueError:
        abort(404)
    response = send_file(browser_cache.directory / (token + ".wav"),
                         mimetype="audio/wav", conditional=True)
    response.headers["Accept-Ranges"] = "bytes"
    return response


def party_response(operation):
    try:
        return jsonify(ok=True, **operation())
    except PartyConflict as exc:
        return jsonify(ok=False, error=str(exc)), 409
    except PartyError as exc:
        return jsonify(ok=False, error=str(exc)), 502


@app.get("/api/party/status")
def api_party_status():
    return party_response(party_client.status)


@app.post("/api/party/start")
def api_party_start():
    return party_response(party_client.start)


@app.post("/api/party/stop")
def api_party_stop():
    return party_response(party_client.stop)


@app.post("/api/seek")
def api_seek():
    try:
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError("Nieprawidłowe dane przewijania")
        result = sony_seek(data.get("seconds"), data.get("uri"))
        return jsonify(ok=True, **result)
    except (ValueError, BadRequest, UnsupportedMediaType) as exc:
        return jsonify(ok=False, error=str(exc)), 400
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 502


@app.post("/api/volume")
def api_volume():
    try:
        data = request.get_json(force=True)
        volume = int(data.get("volume", 0))

        sony_set_volume(volume)

        return jsonify(ok=True, volume=volume)

    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post("/api/mode")
def api_mode():
    try:
        data = request.get_json(force=True)
        mode = data.get("mode", "NORMAL")

        sony_set_mode(mode)

        return jsonify(ok=True, mode=mode)

    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.get("/api/browse")
def api_browse():
    try:
        path = request.args.get("path", "")
        return jsonify(folder_contents(path))

    except Exception as exc:
        return jsonify(error=str(exc)), 400


@app.get("/api/search")
def api_search():
    try:
        query = request.args.get("q", "")
        return jsonify(results=search_library(query))

    except Exception as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/play-track")
def api_play_track():
    try:
        data = request.get_json(force=True)
        relative = data.get("path", "")

        path = safe_path(relative)
        if not is_audio(path):
            raise ValueError("To nie jest obsługiwany plik audio")
        relative = relative_path(path)
        with play_lock, queue_lock:
            items = queue_state["items"]
            preserve = len(items) > 1 or queue_state["source"].startswith("Playlista: ")
            if preserve and relative in items:
                play_queue_index(items.index(relative))
            else:
                folder = "" if path.parent == MUSIC_ROOT else relative_path(path.parent)
                items = [track["path"] for track in folder_contents(folder)["tracks"]]
                set_queue(items, source=folder or MUSIC_ROOT.name, start=items.index(relative))
            return jsonify(ok=True, count=len(queue_state["items"]), index=queue_state["index"])

    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post("/api/play-folder")
def api_play_folder():
    try:
        data = request.get_json(force=True)
        relative = data.get("path", "")

        tracks = recursive_tracks(relative)

        set_queue(
            tracks,
            source=(
                relative if relative
                else MUSIC_ROOT.name
            ),
            start=0,
        )

        return jsonify(
            ok=True,
            count=len(tracks)
        )

    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.get("/api/queue")
def api_queue():
    return jsonify(queue_public())


@app.post("/api/queue/play")
def api_queue_play():
    try:
        data = request.get_json(force=True)
        play_queue_index(int(data["index"]))
        return jsonify(ok=True)

    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post("/api/queue/remove")
def api_queue_remove():
    try:
        data = request.get_json(force=True)
        index = int(data["index"])

        with queue_lock:
            if index < 0 or index >= len(queue_state["items"]):
                raise ValueError("Nieprawidłowa pozycja")

            queue_state["items"].pop(index)
            if index == queue_state["index"]:
                queue_state["current_removed"] = True
                queue_state["index"] = index - 1

            if not queue_state["items"]:
                queue_state["index"] = -1

            elif index < queue_state["index"]:
                queue_state["index"] -= 1

            elif queue_state["index"] >= len(queue_state["items"]):
                queue_state["index"] = len(
                    queue_state["items"]
                ) - 1

            save_queue()

        return jsonify(ok=True)

    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post("/api/queue/clear")
def api_queue_clear():
    with queue_lock:
        queue_state["items"] = []
        queue_state["index"] = -1
        queue_state["source"] = ""
        save_queue()

    return jsonify(ok=True)


@app.get("/api/playlists")
def api_playlists():
    return jsonify(playlists=list_playlists())


@app.get("/api/playlist/<path:name>")
def api_playlist_get(name):
    try:
        playlist = read_playlist(name)

        detailed = []

        for rel in playlist["items"]:
            try:
                path = safe_path(rel)

                detailed.append({
                    "path": rel,
                    "title": clean_title(path.name),
                })
            except Exception:
                pass

        playlist["tracks"] = detailed

        return jsonify(playlist)

    except Exception as exc:
        return jsonify(error=str(exc)), 404


@app.post("/api/playlist")
def api_playlist_save():
    try:
        data = request.get_json(force=True)

        name = data.get("name", "")
        items = data.get("items")

        if items is None:
            with queue_lock:
                items = list(queue_state["items"])

        playlist = write_playlist(name, items)

        return jsonify(ok=True, playlist=playlist)

    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.post("/api/playlist/play")
def api_playlist_play():
    try:
        data = request.get_json(force=True)
        name = data.get("name", "")

        playlist = read_playlist(name)

        set_queue(
            playlist["items"],
            source=f"Playlista: {playlist['name']}",
            start=0,
        )

        return jsonify(ok=True)

    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 400


@app.delete("/api/playlist/<path:name>")
def api_playlist_delete(name):
    try:
        file = playlist_file(name)

        if file.exists():
            file.unlink()

        return jsonify(ok=True)

    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 400


# ============================================================
# SERWOWANIE MUZYKI DLA SONY
# ============================================================

@app.get("/media/<path:relative>")
def media(relative):
    path = safe_path(relative)

    if not is_audio(path):
        abort(404)

    mime = {".wav": "audio/wav", ".aac": "audio/vnd.dlna.adts"}.get(
        path.suffix.lower(), mimetypes.guess_type(path.name)[0]
    )

    response = send_file(
        path,
        mimetype=mime or "application/octet-stream",
        conditional=True,
    )
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["contentFeatures.dlna.org"] = dlna_features(
        mime or "application/octet-stream"
    )
    response.headers["transferMode.dlna.org"] = "Streaming"
    return response


@app.get("/seek-media/<token>.<extension>")
def seek_media(token, extension):
    try:
        record = seek_cache.record(token)
        if extension != record["extension"] or not is_audio(safe_path(record["source"])):
            abort(404)
    except ValueError:
        abort(404)
    mime = {"wav": "audio/wav", "mp3": "audio/mpeg", "m4a": "audio/mp4",
            "aac": "audio/vnd.dlna.adts"}[extension]
    response = send_file(seek_cache.directory / (token + "." + extension),
                         mimetype=mime, conditional=True)
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["contentFeatures.dlna.org"] = dlna_features(mime)
    response.headers["transferMode.dlna.org"] = "Streaming"
    return response


# ============================================================
# OKŁADKI
# ============================================================

@app.get("/cover")
def cover():
    relative = request.args.get("path", "")

    try:
        path = safe_path(relative)

        folder = path if path.is_dir() else path.parent

        for name in IMAGE_NAMES:
            image = folder / name

            if image.exists() and image.is_file():
                return send_file(image)

    except Exception:
        pass

    svg = """<svg xmlns="http://www.w3.org/2000/svg"
width="600" height="600" viewBox="0 0 600 600">
<defs>
<linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#272b38"/>
<stop offset="1" stop-color="#101116"/>
</linearGradient>
</defs>
<rect width="600" height="600" rx="48" fill="url(#g)"/>
<circle cx="300" cy="300" r="180"
fill="#171920" stroke="#555d72" stroke-width="8"/>
<circle cx="300" cy="300" r="52" fill="#8fa8ff"/>
<text x="300" y="325" text-anchor="middle"
font-size="120" fill="white">♫</text>
</svg>"""

    return Response(svg, mimetype="image/svg+xml")


# ============================================================
# INTERFEJS WWW
# ============================================================

INDEX_HTML = r"""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport"
 content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#111318">
<title>Sony Music Center</title>

<style>
:root {
    --bg: #0c0e12;
    --panel: #151820;
    --panel2: #1b1f29;
    --line: #292e3b;
    --text: #f3f5fa;
    --muted: #969eae;
    --accent: #9db2ff;
    --accent2: #c6d2ff;
    --danger: #ff8f98;
    --radius: 18px;
}

* {
    box-sizing: border-box;
}

html {
    background: var(--bg);
}

body {
    margin: 0;
    color: var(--text);
    background:
        radial-gradient(circle at top left,
            #1c2232 0, transparent 32rem),
        var(--bg);
    font-family:
        Inter, system-ui, -apple-system,
        BlinkMacSystemFont, "Segoe UI", sans-serif;
}

button,
input,
select {
    font: inherit;
}

button {
    color: var(--text);
}

.app {
    width: min(1500px, 100%);
    margin: auto;
    padding: 18px;
}

.header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    margin-bottom: 18px;
}

.brand {
    display: flex;
    align-items: center;
    gap: 12px;
}

.logo {
    width: 48px;
    height: 48px;
    border-radius: 15px;
    display: grid;
    place-items: center;
    font-size: 26px;
    background: linear-gradient(
        145deg, #809bff, #5365b9
    );
    box-shadow: 0 8px 30px #0008;
}

.brand h1 {
    font-size: 19px;
    margin: 0;
}

.brand small {
    color: var(--muted);
}

.online {
    padding: 7px 11px;
    border: 1px solid var(--line);
    border-radius: 999px;
    color: var(--muted);
    background: #11141a;
}

.grid {
    display: grid;
    grid-template-columns: 380px 1fr;
    gap: 18px;
}

.card {
    background: color-mix(
        in srgb, var(--panel) 94%, transparent
    );
    border: 1px solid var(--line);
    border-radius: var(--radius);
    box-shadow: 0 15px 40px #0004;
}

.player {
    padding: 22px;
    position: sticky;
    top: 18px;
}

.cover {
    width: 100%;
    aspect-ratio: 1;
    object-fit: cover;
    border-radius: 15px;
    background: #111;
    box-shadow: 0 12px 30px #0008;
}

.now-label {
    margin-top: 20px;
    color: var(--muted);
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: .12em;
}

.track-title {
    font-size: 22px;
    font-weight: 700;
    margin-top: 6px;
    min-height: 29px;
    overflow-wrap: anywhere;
}

.track-meta {
    color: var(--muted);
    min-height: 22px;
    margin-top: 5px;
}

.progress {
    position: relative;
    height: 18px;
    background: #292d36;
    border-radius: 99px;
    overflow: hidden;
    margin-top: 20px;
}

.progress input {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    margin: 0;
    opacity: 0;
    cursor: pointer;
}
.progress:focus-within { outline: 2px solid var(--accent); outline-offset: 3px; }
.progress input:disabled { cursor: default; }
.progress > div {
    width: 0;
    height: 100%;
    background: var(--accent);
    border-radius: inherit;
    transition: width .6s linear;
}

.times {
    display: flex;
    justify-content: space-between;
    color: var(--muted);
    font-size: 12px;
    margin-top: 7px;
}

.controls {
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 11px;
    margin: 20px 0;
}

.round {
    border: 1px solid var(--line);
    background: var(--panel2);
    width: 48px;
    height: 48px;
    border-radius: 50%;
    cursor: pointer;
    font-size: 19px;
}

.round.main {
    width: 64px;
    height: 64px;
    background: var(--accent);
    color: #111521;
    font-size: 25px;
    border: 0;
}

.round:hover,
.action:hover,
.folder:hover,
.track:hover,
.playlist:hover {
    filter: brightness(1.15);
}

.party-controls, .browser-listening { margin-top: 18px; }
.browser-listening { border-top: 1px solid var(--line); padding-top: 16px; }
.browser-listening .party-buttons { flex-wrap: wrap; }
.browser-listening small, #browserListenStatus { color: var(--muted); font-size: 12px; }
#browserListenStatus { margin: 8px 0 4px; }
.browser-listening button:disabled { opacity: .45; cursor: default; }
.browser-listening .volume-row label { font-size: 12px; white-space: nowrap; }
.party-buttons { display: flex; gap: 8px; }
.party-controls button:disabled { opacity: .45; cursor: default; }
#partyStatus { color: var(--muted); font-size: 13px; margin-top: 8px; }

.volume-row {
    display: grid;
    grid-template-columns: auto 1fr 36px;
    align-items: center;
    gap: 10px;
}

input[type=range] {
    width: 100%;
    accent-color: var(--accent);
}

.mode {
    margin-top: 15px;
}

select,
.search {
    width: 100%;
    color: var(--text);
    background: #101319;
    border: 1px solid var(--line);
    border-radius: 11px;
    padding: 11px 12px;
    outline: none;
}

.main {
    min-width: 0;
}

.tabs {
    display: flex;
    gap: 7px;
    padding: 8px;
    overflow-x: auto;
    margin-bottom: 14px;
}

.tab {
    border: 0;
    background: transparent;
    color: var(--muted);
    padding: 10px 15px;
    border-radius: 10px;
    cursor: pointer;
    white-space: nowrap;
}

.tab.active {
    background: var(--panel2);
    color: var(--text);
}

.content {
    padding: 18px;
}

.toolbar {
    display: flex;
    gap: 10px;
    margin-bottom: 16px;
}

.toolbar .search {
    flex: 1;
}

.action {
    border: 1px solid var(--line);
    background: var(--panel2);
    border-radius: 11px;
    padding: 10px 14px;
    cursor: pointer;
    white-space: nowrap;
}

.action.primary {
    background: var(--accent);
    color: #111521;
    border-color: transparent;
    font-weight: 700;
}

.action.danger {
    color: var(--danger);
}

.breadcrumb {
    color: var(--muted);
    margin: 8px 2px 14px;
    overflow-wrap: anywhere;
}

.library-grid {
    display: grid;
    grid-template-columns:
        repeat(auto-fill, minmax(175px, 1fr));
    gap: 11px;
}

.folder {
    min-height: 105px;
    border: 1px solid var(--line);
    background: var(--panel2);
    border-radius: 14px;
    padding: 15px;
    cursor: pointer;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
}

.folder-icon {
    font-size: 30px;
}

.folder-name {
    font-weight: 650;
    overflow-wrap: anywhere;
}

.track-list {
    margin-top: 18px;
}

.track {
    display: grid;
    grid-template-columns: 42px 1fr auto;
    gap: 11px;
    align-items: center;
    padding: 9px;
    border-radius: 11px;
    cursor: pointer;
}

.track:hover {
    background: var(--panel2);
}

.track-number {
    width: 38px;
    height: 38px;
    border-radius: 9px;
    display: grid;
    place-items: center;
    background: #252a35;
    color: var(--muted);
}

.track-name {
    min-width: 0;
    overflow-wrap: anywhere;
}

.track-path {
    color: var(--muted);
    font-size: 12px;
    margin-top: 3px;
    overflow-wrap: anywhere;
}

.queue-item {
    display: grid;
    grid-template-columns: 32px 1fr auto;
    gap: 10px;
    align-items: center;
    padding: 10px;
    border-bottom: 1px solid var(--line);
}

.queue-item.active {
    background: #22283a;
}

.playlist {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 14px;
    border-bottom: 1px solid var(--line);
}

.empty {
    color: var(--muted);
    text-align: center;
    padding: 50px 15px;
}

.hidden {
    display: none !important;
}

.toast {
    position: fixed;
    bottom: 22px;
    left: 50%;
    transform: translateX(-50%) translateY(100px);
    opacity: 0;
    max-width: min(520px, calc(100% - 30px));
    padding: 12px 16px;
    border-radius: 12px;
    background: #252a36;
    border: 1px solid #3a4151;
    box-shadow: 0 12px 40px #0009;
    transition: .25s;
    z-index: 100;
}

.toast.show {
    transform: translateX(-50%) translateY(0);
    opacity: 1;
}

@media (max-width: 900px) {
    .grid {
        grid-template-columns: 1fr;
    }

    .player {
        position: static;
    }

    .player-layout {
        display: grid;
        grid-template-columns: 180px 1fr;
        gap: 20px;
    }
}

@media (max-width: 600px) {
    .app {
        padding: 10px;
    }

    .grid {
        gap: 10px;
    }

    .player,
    .content {
        padding: 14px;
    }

    .player-layout {
        display: block;
    }

    .cover {
        max-height: 330px;
    }

    .library-grid {
        grid-template-columns: repeat(2, 1fr);
    }

    .toolbar {
        flex-wrap: wrap;
    }

    .toolbar .search {
        flex-basis: 100%;
    }

    .header {
        margin-top: env(safe-area-inset-top);
    }
}
</style>
</head>

<body>
<div class="app">

<header class="header">
    <div class="brand">
        <div class="logo">♫</div>
        <div>
            <h1>Sony Music Center</h1>
            <small>Sony PARTY STREAMING</small>
        </div>
    </div>
    <div class="online" id="online">Łączenie…</div>
</header>

<div id="volumio-status" class="online" hidden style="margin: 0 0 12px">
    <a id="volumio-link" target="_blank" rel="noopener">Volumio</a>
    <span id="volumio-state"></span>
</div>

<div style="margin:12px 0 20px;line-height:1.6">
<label><input type="checkbox" id="group-enabled"> Graj razem (test) · Sony + Volumio</label>
<small style="display:block;opacity:.7">Utwory wybieraj w Sony. Tryb zastępuje kolejkę Volumio. Możliwe echo.</small>
<small id="group-message" style="display:block;opacity:.7">Sony prowadzi · synchronizacja przybliżona</small>
</div>
<div class="grid">

<aside>
<div class="card player">
<div class="player-layout">

<img
    id="cover"
    class="cover"
    src="/cover"
    alt="Okładka">

<div>
<div class="now-label" id="nowLabel">Odtwarzacz</div>
<div class="track-title" id="title">—</div>
<div class="track-meta" id="meta">Sony</div>

<div class="progress" id="seekTrack">
    <div id="progressBar"></div>
    <input type="range" id="seekInput" min="0" max="1" value="0" step="1"
           disabled aria-label="Pozycja odtwarzania" title="Uruchom utwór z biblioteki">
</div>

<div class="times">
    <span id="position">0:00</span>
    <span id="duration">0:00</span>
</div>

<div class="controls">
    <button class="round"
        onclick="control('previous')"
        title="Poprzedni">⏮</button>

    <button class="round"
        onclick="control('stop')"
        title="Stop">⏹</button>

    <button class="round main"
        id="playButton"
        onclick="togglePlay()"
        title="Play/Pause">▶</button>

    <button class="round"
        onclick="control('next')"
        title="Następny">⏭</button>
</div>

<div class="volume-row">
    <span>🔊</span>
    <input
        id="volume"
        type="range"
        min="0"
        max="33"
        value="0">
    <span id="volumeText">0</span>
</div>

<div class="browser-listening">
    <div class="party-buttons">
        <button class="action primary" id="browserListen" disabled onclick="startBrowserListening()">Słuchaj na tym urządzeniu</button>
        <button class="action" id="browserListenStop" disabled onclick="stopBrowserListening()">Wyłącz odsłuch</button>
    </div>
    <div class="volume-row">
        <label for="browserVolume">Głośność tutaj</label>
        <input id="browserVolume" type="range" min="0" max="100" value="50">
        <span id="browserVolumeText">50%</span>
    </div>
    <div id="browserListenStatus" role="status" aria-live="polite">Odsłuch wyłączony</div>
    <small>Podąża za Sony · synchronizacja przybliżona · pliki z biblioteki</small>
    <audio id="browserAudio" preload="metadata"></audio>
</div>

<div class="party-controls">
    <div class="party-buttons">
        <button class="action primary" id="partyOn" disabled onclick="setParty(true)">Party On</button>
        <button class="action" id="partyOff" disabled onclick="setParty(false)">Party Off</button>
    </div>
    <div id="partyStatus" role="status" aria-live="polite">PARTY: sprawdzam…</div>
</div>

<div class="mode">
<select id="mode">
    <option value="NORMAL">Normalnie</option>
    <option value="RANDOM">Losowo</option>
    <option value="REPEAT_ONE">Powtarzaj utwór</option>
    <option value="REPEAT_ALL">Powtarzaj wszystko</option>
</select>
</div>

</div>
</div>
</div>
</aside>

<main class="main">

<div class="card tabs">
    <button class="tab active"
        data-tab="library">🎵 Biblioteka</button>
    <button class="tab"
        data-tab="queue">☷ Kolejka</button>
    <button class="tab"
        data-tab="playlists">♥ Playlisty</button>
    <button class="tab"
        data-tab="radio">📻 Radio</button>
    <button class="tab"
        data-tab="search">⌕ Szukaj</button>
</div>

<section class="card content" id="libraryView">

<div class="toolbar">
    <button class="action"
        onclick="goUp()">← Wstecz</button>

    <button class="action primary"
        onclick="playCurrentFolder()">
        ▶ Graj folder
    </button>
</div>

<div class="breadcrumb" id="breadcrumb">
    /Muzyka
</div>

<div class="library-grid" id="folders"></div>
<div class="track-list" id="tracks"></div>

</section>

<section class="card content hidden" id="queueView">

<div class="toolbar">
    <button class="action primary"
        onclick="saveQueueAsPlaylist()">
        ♥ Zapisz jako playlistę
    </button>

    <button class="action danger"
        onclick="clearQueue()">
        Wyczyść kolejkę
    </button>
</div>

<div class="breadcrumb" id="queueSource"></div>
<div id="queue"></div>

</section>

<section class="card content hidden" id="playlistsView">

<div class="toolbar">
    <button class="action primary"
        onclick="saveQueueAsPlaylist()">
        + Playlista z kolejki
    </button>
</div>

<div id="playlists"></div>

</section>


<section class="card content hidden" id="radioView">
    <div class="section-head">
        <div>
            <h2>📻 Radio internetowe</h2>
            <div class="muted">
                Odtwarzaj strumień na Sony i zapisuj ulubione stacje.
            </div>
        </div>
    </div>

    <div class="toolbar" style="margin-top:16px;">
        <input
            id="radioName"
            type="text"
            placeholder="Nazwa stacji, np. Smooth Jazz"
            style="flex:1; min-width:180px;"
        >
    </div>

    <div class="toolbar" style="margin-top:10px;">
        <input
            id="radioUrl"
            type="text"
            placeholder="http://... lub https://..."
            style="flex:1; min-width:240px;"
        >

        <button onclick="playRadioUrl()">
            ▶ Graj
        </button>

        <button onclick="saveRadio()">
            ♥ Zapisz
        </button>
    </div>

    <div
        class="muted"
        style="margin-top:10px; line-height:1.45;"
    >
        Sony najlepiej obsługuje strumienie HTTP.
        Dla adresu HTTPS panel spróbuje również odpowiednika HTTP.
    </div>

    <div
        id="radioList"
        style="margin-top:18px;"
    ></div>
</section>

<section class="card content hidden" id="searchView">

<div class="toolbar">
    <input
        id="searchInput"
        class="search"
        placeholder="Szukaj wykonawcy, albumu, utworu…">

    <button class="action primary"
        onclick="doSearch()">Szukaj</button>
</div>

<div id="searchResults"></div>

</section>

</main>
</div>
</div>

<div class="toast" id="toast"></div>

<script>
let currentPath = "";
let parentPath = "";
let currentStatus = {};
let statusBusy = false;
let statusGeneration = 0;
let seekBusy = false;
let seekDragging = false;
let seekDragUri = "";

function esc(value) {
    const d = document.createElement("div");
    d.textContent = value ?? "";
    return d.innerHTML;
}

function attrArg(value) {
    return JSON.stringify(value).replace(/[&<>"']/g, char => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;",
        '"': "&quot;", "'": "&#39;"
    }[char]));
}

function toast(message) {
    const el = document.getElementById("toast");
    el.textContent = message;
    el.classList.add("show");

    clearTimeout(el._timer);

    el._timer = setTimeout(() => {
        el.classList.remove("show");
    }, 2600);
}

async function api(url, options = {}) {
    if (options.body && typeof options.body !== "string") {
        options.headers = {
            ...(options.headers || {}),
            "Content-Type": "application/json"
        };

        options.body = JSON.stringify(options.body);
    }

    const response = await fetch(url, options);
    const data = await response.json();

    if (!response.ok || data.ok === false) {
        throw new Error(data.error || "Błąd");
    }

    return data;
}

function fmt(seconds) {
    seconds = Math.max(0, Number(seconds) || 0);

    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);

    return h
        ? `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`
        : `${m}:${String(s).padStart(2,"0")}`;
}

let groupBusy = false;
async function refreshGroup() {
    if (groupBusy) return;
    try {
        const status = await api("/api/group");
        document.getElementById("group-enabled").checked = status.enabled;
        document.getElementById("group-message").textContent = status.error || status.message
            + (status.drift_seconds !== null ? ` · różnica pozycji ${status.drift_seconds.toFixed(1)} s` : "");
    } catch(error) { document.getElementById("group-message").textContent = "Brak stanu wspólnego grania"; }
}
document.getElementById("group-enabled").addEventListener("change", async event => {
    groupBusy = true; event.target.disabled = true;
    try { await api("/api/group", {method:"POST", body:{enabled:event.target.checked}}); }
    catch(error) { alert(error.message); }
    finally { groupBusy = false; event.target.disabled = false; refreshGroup(); }
});
refreshGroup();
setInterval(refreshGroup, 3000);

async function refreshVolumio() {
    try {
        const status = await api("/api/volumio/status");
        document.getElementById("volumio-status").hidden = !status.enabled;
        const link = document.getElementById("volumio-link");
        link.href = status.url || "#";
        link.textContent = status.name || "Volumio";
        document.getElementById("volumio-state").textContent = status.online
            ? ` ● online · ${new URL(status.url).hostname}`
            : " ○ offline · automatyczne wyszukiwanie";
    } catch (error) {
        document.getElementById("volumio-state").textContent = " — brak aktualnego stanu";
    }
}

async function refreshStatus() {
    if (statusBusy) return;
    statusBusy = true;
    const generation = statusGeneration;

    try {
        const s = await api("/api/status");
        if (generation !== statusGeneration || seekBusy) return;
        currentStatus = s;
        browserStatusAt = Date.now();
        syncBrowserAudio(s);

        const online = document.getElementById("online");

        if (s.online) {
            online.textContent = "● Sony online";
        } else {
            online.textContent = "○ Sony offline";
        }

        document.getElementById("nowLabel").textContent = {
            PLAYING: "Teraz gra", PAUSED_PLAYBACK: "Na pauzie",
            STOPPED: "Odtwarzanie zatrzymane", TRANSITIONING: "Przygotowuję utwór…"
        }[s.state] || "Odtwarzacz";
        document.getElementById("title").textContent =
            s.title || "—";

        document.getElementById("meta").textContent =
            [s.artist, s.album].filter(Boolean).join(" · ")
            || s.state
            || "Sony";

        if (!seekBusy && !seekDragging) {
            document.getElementById("position").textContent = fmt(s.position);
        }

        document.getElementById("duration").textContent =
            fmt(s.duration_seconds);

        const percent = s.duration_seconds
            ? Math.min(
                100,
                s.position / s.duration_seconds * 100
              )
            : 0;

        const seekInput = document.getElementById("seekInput");
        if (!seekBusy && !seekDragging) {
            document.getElementById("progressBar").style.width = `${percent}%`;
            seekInput.max = Math.max(0, (s.duration_seconds || 0) - 1);
            seekInput.value = s.position || 0;
        }
        seekInput.disabled = seekBusy || !s.seekable
            || !["PLAYING", "PAUSED_PLAYBACK"].includes(s.state);
        seekInput.title = s.seekable
            ? "Kliknij lub przeciągnij, aby przewinąć utwór"
            : "Przewijanie dostępne dla plików z biblioteki";

        document.getElementById("playButton").textContent =
            s.state === "PLAYING" ? "⏸" : "▶";

        const vol = document.getElementById("volume");

        if (!vol.matches(":active")) {
            vol.value = s.volume ?? 0;
            document.getElementById("volumeText").textContent =
                s.volume ?? 0;
        }

        const mode = document.getElementById("mode");

        if ([...mode.options].some(
            o => o.value === s.mode
        )) {
            mode.value = s.mode;
        }

    } catch (e) {
        syncBrowserAudio({...currentStatus, online: false});
        document.getElementById("online").textContent =
            "○ Sony offline";
    } finally {
        statusBusy = false;
    }
}

const seekInput = document.getElementById("seekInput");
seekInput.addEventListener("pointerdown", () => {
    seekDragging = true;
    seekDragUri = currentStatus.uri;
});
seekInput.addEventListener("input", () => {
    if (!seekDragging) seekDragUri = currentStatus.uri;
    seekDragging = true;
    document.getElementById("position").textContent = fmt(seekInput.value);
    document.getElementById("progressBar").style.width =
        `${Number(seekInput.value) / Math.max(1, currentStatus.duration_seconds) * 100}%`;
});
seekInput.addEventListener("change", async () => {
    if (seekBusy) return;
    seekBusy = true;
    statusGeneration += 1;
    seekDragging = false;
    seekInput.disabled = true;
    try {
        toast("Przewijam utwór…");
        const result = await api("/api/seek", {
            method: "POST",
            body: {seconds: Number(seekInput.value), uri: seekDragUri || currentStatus.uri}
        });
        currentStatus = {...currentStatus, ...result};
        document.getElementById("position").textContent = fmt(result.position);
        document.getElementById("progressBar").style.width =
            `${result.position / Math.max(1, result.duration_seconds) * 100}%`;
        toast(`Ustawiono ${fmt(result.position)}`);
    } catch (e) {
        toast(e.message);
    } finally {
        seekBusy = false;
        seekInput.disabled = !currentStatus.seekable
            || !["PLAYING", "PAUSED_PLAYBACK"].includes(currentStatus.state);
        await refreshStatus();
    }
});
seekInput.addEventListener("pointercancel", () => {
    seekDragging = false;
    refreshStatus();
});
seekInput.addEventListener("blur", () => {
    seekDragging = false;
});
seekInput.addEventListener("pointerup", () => {
    setTimeout(() => { if (!seekBusy) seekDragging = false; }, 0);
});

const browserAudio = document.getElementById("browserAudio");
let browserEnabled = false;
let browserBlocked = false;
let browserFailed = false;
let browserEpoch = 0;
let browserRequestedSource = null;
let browserLoadedSource = null;
let browserStatusAt = Date.now();
let browserPlayPending = false;
browserAudio.volume = 0.5;

function browserMessage(text) {
    document.getElementById("browserListenStatus").textContent = text;
}

function renderBrowserControls(s = currentStatus) {
    const button = document.getElementById("browserListen");
    button.disabled = !s.online || !s.browser_source || (browserEnabled && !browserBlocked && !browserFailed);
    button.textContent = browserBlocked ? "Uruchom dźwięk" : browserFailed ? "Ponów odsłuch" : "Słuchaj na tym urządzeniu";
    document.getElementById("browserListenStop").disabled = !browserEnabled;
}

function stopBrowserListening() {
    browserEnabled = false;
    browserBlocked = false;
    browserFailed = false;
    browserEpoch += 1;
    browserRequestedSource = null;
    browserLoadedSource = null;
    browserAudio.pause();
    browserAudio.removeAttribute("src");
    browserAudio.load();
    browserMessage("Odsłuch wyłączony");
    renderBrowserControls();
}

function startBrowserListening() {
    if (!currentStatus.online || !currentStatus.browser_source) return;
    if (browserFailed) stopBrowserListening();
    browserEnabled = true;
    browserBlocked = false;
    syncBrowserAudio(currentStatus);
}

async function loadBrowserAudio(s) {
    const epoch = ++browserEpoch;
    browserRequestedSource = s.browser_source;
    browserLoadedSource = null;
    browserFailed = false;
    browserAudio.pause();
    browserAudio.removeAttribute("src");
    browserAudio.load();
    browserMessage("Przygotowuję dźwięk…");
    renderBrowserControls(s);
    try {
        const result = await api("/api/browser/audio", {method: "POST", body: {uri: s.uri}});
        if (!browserEnabled || epoch !== browserEpoch) return;
        if (result.source !== currentStatus.browser_source) return;
        browserLoadedSource = result.source;
        browserAudio.src = result.url;
        browserAudio.load();
    } catch (e) {
        if (epoch !== browserEpoch || !browserEnabled) return;
        browserFailed = true;
        browserMessage(`Nie można przygotować odsłuchu: ${e.message}`);
        renderBrowserControls();
    }
}

function syncBrowserAudio(s) {
    renderBrowserControls(s);
    if (!browserEnabled) return;
    if (!s.online || !s.browser_source) {
        browserEpoch += 1;
        browserRequestedSource = null;
        browserLoadedSource = null;
        browserAudio.pause();
        browserMessage(!s.online ? "Odsłuch wstrzymany — brak połączenia z Sony" : "Odsłuch dostępny dla plików z biblioteki");
        return;
    }
    if (browserRequestedSource !== s.browser_source) {
        loadBrowserAudio(s);
        return;
    }
    if (browserFailed || browserLoadedSource !== s.browser_source || browserAudio.readyState < 1) return;
    const playing = s.state === "PLAYING";
    if (!playing) browserAudio.pause();
    if (!["PLAYING", "PAUSED_PLAYBACK"].includes(s.state)) {
        browserMessage("Odsłuch wstrzymany — Sony zatrzymane");
        return;
    }
    const elapsed = playing ? Math.max(0, (Date.now() - browserStatusAt) / 1000) + (s.position_age || 0) : 0;
    const target = Math.max(0, (s.position || 0) + elapsed);
    if (Number.isFinite(browserAudio.duration)) {
        const desired = Math.min(target, Math.max(0, browserAudio.duration - 0.1));
        if (Math.abs(browserAudio.currentTime - desired) > (playing ? 2 : 0.3)) {
            try { browserAudio.currentTime = desired; } catch (e) { /* Await seekable metadata. */ }
        }
    }
    if (!playing) {
        browserMessage("Pauza — zgodnie z Sony");
    } else if (!browserBlocked) {
        browserMessage("Odtwarzanie na tym urządzeniu · synchronizacja przybliżona");
        if (browserAudio.paused && !browserPlayPending) {
            const epoch = browserEpoch;
            browserPlayPending = true;
            browserAudio.play().catch(e => {
                if (epoch !== browserEpoch || !browserEnabled) return;
                if (e.name === "AbortError") return;
                browserBlocked = e.name === "NotAllowedError";
                browserFailed = !browserBlocked;
                browserMessage(browserBlocked ? "Kliknij „Uruchom dźwięk”, aby zezwolić na odsłuch" : "Przeglądarka nie może odtworzyć tego pliku audio");
                renderBrowserControls();
            }).finally(() => { browserPlayPending = false; });
        }
    }
}

browserAudio.addEventListener("loadedmetadata", () => syncBrowserAudio(currentStatus));
browserAudio.addEventListener("error", () => {
    if (!browserEnabled || !browserLoadedSource) return;
    browserFailed = true;
    browserAudio.pause();
    browserMessage("Błąd pobierania lub formatu audio. Kliknij „Ponów odsłuch”.");
    renderBrowserControls();
});
document.getElementById("browserVolume").addEventListener("input", event => {
    browserAudio.volume = Math.max(0, Math.min(1, Number(event.target.value) / 100));
    document.getElementById("browserVolumeText").textContent = `${event.target.value}%`;
});

let partyState = null;
let partyReadBusy = false;
let partyWriteBusy = false;
let partyVersion = 0;

function renderParty() {
    const state = partyState?.state;
    document.getElementById("partyOn").disabled = partyWriteBusy || state !== "IDLE";
    document.getElementById("partyOff").disabled = partyWriteBusy || state !== "SINGING";
    document.getElementById("partyStatus").textContent = partyWriteBusy
        ? "PARTY: zmieniam stan…"
        : state === "SINGING"
            ? `PARTY: włączone · uczestnicy: ${partyState.listeners}`
            : state === "IDLE"
                ? "PARTY: wyłączone"
                : state === "LISTENING"
                    ? "PARTY: Sony uczestniczy w innej sesji"
                    : state ? `PARTY: zmiana stanu (${state})` : "PARTY: brak połączenia";
}

async function refreshParty() {
    if (partyReadBusy || partyWriteBusy) return;
    partyReadBusy = true;
    const version = partyVersion;
    try {
        const data = await api("/api/party/status");
        if (version === partyVersion) partyState = data;
    } catch (e) {
        if (version === partyVersion) partyState = null;
    } finally {
        partyReadBusy = false;
        renderParty();
    }
}

async function setParty(enabled) {
    if (partyWriteBusy) return;
    partyWriteBusy = true;
    partyVersion += 1;
    renderParty();
    try {
        partyState = await api(`/api/party/${enabled ? "start" : "stop"}`, {method: "POST"});
        toast(enabled ? "PARTY włączone" : "PARTY wyłączone");
    } catch (e) {
        partyState = null;
        toast(e.message);
    } finally {
        partyWriteBusy = false;
        renderParty();
        if (!partyState) refreshParty();
    }
}

async function control(action) {
    try {
        await api(`/api/control/${action}`, {
            method: "POST"
        });

        setTimeout(refreshStatus, 350);

        if (action === "next" || action === "previous") {
            setTimeout(loadQueue, 500);
        }
    } catch (e) {
        toast(e.message);
    }
}

function togglePlay() {
    if (currentStatus.state === "PLAYING") {
        control("pause");
    } else {
        control("play");
    }
}

let volumeTimer;

document.getElementById("volume").addEventListener(
    "input",
    event => {
        const value = event.target.value;

        document.getElementById("volumeText").textContent =
            value;

        clearTimeout(volumeTimer);

        volumeTimer = setTimeout(async () => {
            try {
                await api("/api/volume", {
                    method: "POST",
                    body: {volume: Number(value)}
                });
            } catch (e) {
                toast(e.message);
            }
        }, 180);
    }
);

document.getElementById("mode").addEventListener(
    "change",
    async event => {
        try {
            await api("/api/mode", {
                method: "POST",
                body: {mode: event.target.value}
            });

            toast("Zmieniono tryb odtwarzania");
        } catch (e) {
            toast(e.message);
        }
    }
);

async function browse(path = "") {
    try {
        const data = await api(
            `/api/browse?path=${encodeURIComponent(path)}`
        );

        currentPath = data.path;
        parentPath = data.parent;

        document.getElementById("breadcrumb").textContent =
            "/Muzyka" +
            (data.path ? "/" + data.path : "");

        const folders = document.getElementById("folders");

        folders.innerHTML = data.folders.map(folder => `
            <div class="folder"
                 onclick='browse(${attrArg(folder.path)})'>
                <div class="folder-icon">📁</div>
                <div class="folder-name">
                    ${esc(folder.name)}
                </div>
            </div>
        `).join("");

        const tracks = document.getElementById("tracks");

        tracks.innerHTML = data.tracks.map(
            (track, index) => `
            <div class="track"
                 onclick='playTrack(${attrArg(track.path)})'>
                <div class="track-number">
                    ${index + 1}
                </div>
                <div class="track-name">
                    ${esc(track.title)}
                </div>
                <div>▶</div>
            </div>
        `).join("");

        if (!data.folders.length && !data.tracks.length) {
            tracks.innerHTML =
                '<div class="empty">Folder jest pusty</div>';
        }

    } catch (e) {
        toast(e.message);
    }
}

function goUp() {
    if (!currentPath) return;
    browse(parentPath || "");
}

async function playTrack(path) {
    try {
        toast("Uruchamiam utwór…");

        const result = await api("/api/play-track", {
            method: "POST",
            body: {path}
        });
        toast(`Utworów w kolejce: ${result.count}`);

        setTimeout(refreshStatus, 600);
        setTimeout(loadQueue, 500);

    } catch (e) {
        toast(e.message);
    }
}

async function playCurrentFolder() {
    try {
        toast("Tworzę kolejkę z folderu…");

        const result = await api("/api/play-folder", {
            method: "POST",
            body: {path: currentPath}
        });

        toast(`Dodano ${result.count} utworów`);

        setTimeout(refreshStatus, 600);
        setTimeout(loadQueue, 500);

    } catch (e) {
        toast(e.message);
    }
}

async function loadQueue() {
    try {
        const q = await api("/api/queue");

        document.getElementById("queueSource").textContent =
            q.source
            ? `Źródło: ${q.source}`
            : "";

        const container = document.getElementById("queue");

        if (!q.items.length) {
            container.innerHTML =
                '<div class="empty">Kolejka jest pusta</div>';
            return;
        }

        container.innerHTML = q.items.map(item => `
            <div class="queue-item
                 ${item.active ? "active" : ""}">
                <div>${item.active ? "♫" : item.index + 1}</div>

                <div onclick="playQueue(${item.index})"
                     style="cursor:pointer">
                    ${esc(item.title)}
                </div>

                <button class="action danger"
                    onclick="removeQueue(${item.index})">
                    ×
                </button>
            </div>
        `).join("");

    } catch (e) {
        toast(e.message);
    }
}

async function playQueue(index) {
    try {
        await api("/api/queue/play", {
            method: "POST",
            body: {index}
        });

        await loadQueue();
        setTimeout(refreshStatus, 500);

    } catch (e) {
        toast(e.message);
    }
}

async function removeQueue(index) {
    try {
        await api("/api/queue/remove", {
            method: "POST",
            body: {index}
        });

        loadQueue();

    } catch (e) {
        toast(e.message);
    }
}

async function clearQueue() {
    if (!confirm("Wyczyścić kolejkę?")) return;

    try {
        await api("/api/queue/clear", {
            method: "POST"
        });

        loadQueue();

    } catch (e) {
        toast(e.message);
    }
}

async function loadPlaylists() {
    try {
        const data = await api("/api/playlists");
        const container =
            document.getElementById("playlists");

        if (!data.playlists.length) {
            container.innerHTML = `
                <div class="empty">
                    Nie masz jeszcze playlist.<br>
                    Utwórz kolejkę z folderu i zapisz ją tutaj.
                </div>`;
            return;
        }

        container.innerHTML = data.playlists.map(p => `
            <div class="playlist">
                <div>
                    <strong>${esc(p.name)}</strong>
                    <div class="track-path">
                        ${p.count} utworów
                    </div>
                </div>

                <div>
                    <button class="action primary"
                        onclick='playPlaylist(
                            ${attrArg(p.name)}
                        )'>
                        ▶
                    </button>

                    <button class="action danger"
                        onclick='deletePlaylist(
                            ${attrArg(p.name)}
                        )'>
                        Usuń
                    </button>
                </div>
            </div>
        `).join("");

    } catch (e) {
        toast(e.message);
    }
}

async function saveQueueAsPlaylist() {
    const name = prompt("Nazwa playlisty:");

    if (!name) return;

    try {
        await api("/api/playlist", {
            method: "POST",
            body: {name}
        });

        toast("Playlista zapisana");
        loadPlaylists();

    } catch (e) {
        toast(e.message);
    }
}

async function playPlaylist(name) {
    try {
        await api("/api/playlist/play", {
            method: "POST",
            body: {name}
        });

        toast(`Gram playlistę: ${name}`);

        setTimeout(refreshStatus, 500);
        setTimeout(loadQueue, 500);

    } catch (e) {
        toast(e.message);
    }
}

async function deletePlaylist(name) {
    if (!confirm(`Usunąć playlistę "${name}"?`)) return;

    try {
        await api(
            `/api/playlist/${encodeURIComponent(name)}`,
            {method: "DELETE"}
        );

        loadPlaylists();

    } catch (e) {
        toast(e.message);
    }
}

async function doSearch() {
    const query =
        document.getElementById("searchInput")
        .value.trim();

    if (!query) return;

    const container =
        document.getElementById("searchResults");

    container.innerHTML =
        '<div class="empty">Szukam…</div>';

    try {
        const data = await api(
            `/api/search?q=${encodeURIComponent(query)}`
        );

        if (!data.results.length) {
            container.innerHTML =
                '<div class="empty">Brak wyników</div>';
            return;
        }

        container.innerHTML = data.results.map(item => {
            if (item.type === "folder") {
                return `
                <div class="track"
                     onclick='openSearchFolder(
                        ${attrArg(item.path)}
                     )'>
                    <div class="track-number">📁</div>
                    <div>
                        <div>${esc(item.name)}</div>
                        <div class="track-path">
                            ${esc(item.path)}
                        </div>
                    </div>
                    <div>→</div>
                </div>`;
            }

            return `
            <div class="track"
                 onclick='playTrack(
                    ${attrArg(item.path)}
                 )'>
                <div class="track-number">♫</div>
                <div>
                    <div>${esc(item.name)}</div>
                    <div class="track-path">
                        ${esc(item.path)}
                    </div>
                </div>
                <div>▶</div>
            </div>`;
        }).join("");

    } catch (e) {
        container.innerHTML = "";
        toast(e.message);
    }
}

function openSearchFolder(path) {
    switchTab("library");
    browse(path);
}

document.getElementById("searchInput")
.addEventListener("keydown", event => {
    if (event.key === "Enter") {
        doSearch();
    }
});


async function loadRadio() {
    const box = document.getElementById("radioList");

    if (!box) return;

    box.innerHTML = '<div class="muted">Ładowanie stacji…</div>';

    try {
        const data = await api("/api/radio");
        const stations = data.stations || [];

        if (!stations.length) {
            box.innerHTML = `
                <div class="empty">
                    Nie masz jeszcze zapisanych stacji.
                </div>
            `;
            return;
        }

        box.innerHTML = stations.map(station => `
            <div class="row">
                <div class="row-main">
                    <div class="row-title">
                        📻 ${esc(station.name)}
                    </div>
                    <div class="row-meta">
                        ${esc(station.url)}
                    </div>
                </div>

                <div class="row-actions">
                    <button
                        onclick="playRadioStation('${station.id}')"
                    >
                        ▶ Graj
                    </button>

                    <button
                        onclick="deleteRadioStation('${station.id}')"
                        title="Usuń stację"
                    >
                        ✕
                    </button>
                </div>
            </div>
        `).join("");

    } catch (err) {
        box.innerHTML = `
            <div class="empty">
                ${esc(err.message)}
            </div>
        `;
    }
}


async function playRadioUrl() {
    const name = document
        .getElementById("radioName")
        .value
        .trim();

    const url = document
        .getElementById("radioUrl")
        .value
        .trim();

    if (!url) {
        toast("Podaj adres URL radia");
        return;
    }

    try {
        await api("/api/radio/play", {
            method: "POST",
            body: {
                name: name || "Radio internetowe",
                url: url
            }
        });

        toast("Radio uruchomione");
        setTimeout(refreshStatus, 500);

    } catch (err) {
        toast(err.message);
    }
}


async function playRadioStation(id) {
    try {
        await api("/api/radio/play", {
            method: "POST",
            body: {
                id: id
            }
        });

        toast("Radio uruchomione");
        setTimeout(refreshStatus, 500);

    } catch (err) {
        toast(err.message);
    }
}


async function saveRadio() {
    const name = document
        .getElementById("radioName")
        .value
        .trim();

    const url = document
        .getElementById("radioUrl")
        .value
        .trim();

    if (!name) {
        toast("Podaj nazwę stacji");
        return;
    }

    if (!url) {
        toast("Podaj adres URL radia");
        return;
    }

    try {
        await api("/api/radio", {
            method: "POST",
            body: {
                name: name,
                url: url
            }
        });

        toast("Stacja zapisana");

        document.getElementById("radioName").value = "";
        document.getElementById("radioUrl").value = "";

        await loadRadio();

    } catch (err) {
        toast(err.message);
    }
}


async function deleteRadioStation(id) {
    if (!confirm("Usunąć tę stację z ulubionych?")) {
        return;
    }

    try {
        await api(
            `/api/radio/${encodeURIComponent(id)}`,
            {
                method: "DELETE"
            }
        );

        toast("Stacja usunięta");
        await loadRadio();

    } catch (err) {
        toast(err.message);
    }
}


function switchTab(name) {
    document.querySelectorAll(".tab")
        .forEach(tab => {
            tab.classList.toggle(
                "active",
                tab.dataset.tab === name
            );
        });

    const views = {
        library: "libraryView",
        queue: "queueView",
        playlists: "playlistsView",
        radio: "radioView",
        search: "searchView"
    };

    Object.entries(views).forEach(([key, id]) => {
        document.getElementById(id)
            .classList.toggle("hidden", key !== name);
    });

    if (name === "queue") loadQueue();
    if (name === "playlists") loadPlaylists();
    if (name === "radio") loadRadio();

    if (name === "search") {
        setTimeout(() => {
            document.getElementById("searchInput").focus();
        }, 50);
    }
}

document.querySelectorAll(".tab")
.forEach(tab => {
    tab.addEventListener("click", () => {
        switchTab(tab.dataset.tab);
    });
});

browse("");
loadQueue();
loadPlaylists();
loadRadio();
refreshStatus();

refreshParty();
refreshVolumio();
setInterval(refreshVolumio, 10000);
setInterval(refreshParty, 10000);
setInterval(refreshStatus, 2500);
setInterval(loadQueue, 10000);
</script>
</body>
</html>
"""


@app.get("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html")


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    print("")
    print("Sony Music Center")
    print("=================")
    print(f"Sony:      {SONY_BASE}")
    print(f"Muzyka:    {MUSIC_ROOT}")
    print(f"Panel WWW: http://{SERVER_IP}:{WEB_PORT}")
    print("")

    threading.Thread(target=volumio_monitor.run, args=(queue_monitor_stop,),
                     name="volumio-monitor", daemon=True).start()
    threading.Thread(target=group_playback.run, args=(queue_monitor_stop,),
                     name="group-playback", daemon=True).start()
    sony_endpoint.remember()

    threading.Thread(target=queue_monitor_loop, name="sony-queue-monitor", daemon=True).start()

    app.run(
        host=settings.bind_host,
        port=WEB_PORT,
        threaded=True,
        debug=False,
    )
