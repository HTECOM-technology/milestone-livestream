r"""
Milestone XProtect Mobile Server 2022 R1 - Worker Probe

Purpose:
- Reuse the known-good Connect/Login flow with DH + AES-CBC PKCS7 encryption.
- Fetch camera list.
- RequestStream for exactly one camera.
- Open /XProtectMobile/Video/{VideoId} for a short sample.
- Extract JPEG frames if possible.
- Optionally pipe sampled JPEG frames to FFmpeg and create HLS output.

This file is diagnostic-first. It is intended to produce logs for the next implementation step.

Recommended install:
    pip install requests pycryptodome

Example:
    python milestone_worker_probe.py ^
      --base-url http://10.2.18.16:8081 ^
      --login-type ActiveDirectory ^
      --domain VMS-ITS ^
      --username administrator ^
      --password "YOUR_PASSWORD" ^
      --camera-id 66a23788-b7ef-45cf-9bb1-b154857a06b5 ^
      --sample-seconds 8 ^
      --ffmpeg-bin ffmpeg ^
      --hls-root C:\hls ^
      --thumbnail-root C:\thumbnails
"""

from __future__ import annotations

import argparse
import base64
import os
import random
import re
import shutil
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

import requests
from urllib3.exceptions import InsecureRequestWarning

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
except Exception as exc:  # pragma: no cover
    print("[FATAL] Missing pycryptodome. Install with: pip install pycryptodome")
    print("Import error:", repr(exc))
    raise

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

PRIME_1024_HEX = (
    "F488FD584E49DBCD20B49DE49107366B336C380D451D0F7C88B31C7C5B2D8EF6"
    "F3C923C043F0A55B188D8EBB558CB85D38D334FD7C175743A31D186CDE33212"
    "CB52AFF3CE1B1294018118D7C84A70A72D686C40319C807297ACA950CD9969F"
    "ABD00A509B0246D3083D66A45D419F9C7CBD894B221926BAABA25EC355E92F78C7"
)

PRIME = int(PRIME_1024_HEX, 16)
GENERATOR = 2
JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"


@dataclass
class ProbeConfig:
    base_url: str
    login_type: str
    domain: str
    username: str
    password: str
    verify_ssl: bool
    timeout: int
    camera_id: Optional[str]
    sample_seconds: int
    output_dir: Path
    hls_root: Path
    thumbnail_root: Path
    ffmpeg_bin: str
    width: int
    height: int
    fps: int
    bitrate: str
    maxrate: str
    bufsize: str
    nginx_root: Optional[Path]
    request_variant: str


@dataclass
class LoginSession:
    connection_id: str
    iv: bytes
    aes_key: bytes


class Logger:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = self.output_dir / f"milestone_worker_probe_{ts}.log"

    def write(self, msg: str = "") -> None:
        print(msg)
        with self.log_path.open("a", encoding="utf-8", errors="replace") as f:
            f.write(msg + "\n")

    def section(self, title: str) -> None:
        self.write("\n" + "=" * 100)
        self.write(title)
        self.write("=" * 100)

    def sub(self, title: str) -> None:
        self.write("\n" + "-" * 100)
        self.write(title)
        self.write("-" * 100)


def int_to_little_endian_bytes(value: int) -> bytes:
    if value == 0:
        return b"\x00"
    length = (value.bit_length() + 7) // 8
    raw = value.to_bytes(length, byteorder="little", signed=False)
    if raw[-1] & 0x80:
        raw += b"\x00"
    return raw


def little_endian_bytes_to_int(data: bytes) -> int:
    return int.from_bytes(data, byteorder="little", signed=False)


def xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def parse_xml(text: str) -> ET.Element:
    return ET.fromstring(text)


def extract_params(xml_text: str) -> Dict[str, object]:
    root = parse_xml(xml_text)
    params: Dict[str, str] = {}

    for elem in root.iter():
        if elem.tag.endswith("Param"):
            name = elem.attrib.get("Name")
            value = elem.attrib.get("Value")
            if name:
                params[name] = value or ""

    connection_id = None
    for elem in root.iter():
        if elem.tag.endswith("ConnectionId") and elem.text:
            connection_id = elem.text.strip()

    if not connection_id:
        connection_id = params.get("ConnectionId")

    result = None
    error_code = None
    error_text = None

    for elem in root.iter():
        tag = elem.tag.split("}")[-1]
        if tag == "Result" and elem.text:
            result = elem.text.strip()
        if tag == "ErrorCode" and elem.text:
            error_code = elem.text.strip()
        if tag == "ErrorText" and elem.text:
            error_text = elem.text.strip()

    return {
        "params": params,
        "connection_id": connection_id,
        "result": result,
        "error_code": error_code,
        "error_text": error_text,
    }


def extract_camera_items(xml_text: str) -> List[Dict[str, str]]:
    root = parse_xml(xml_text)
    cameras: Dict[str, Dict[str, str]] = {}

    for elem in root.iter():
        if not elem.tag.endswith("Item"):
            continue
        if elem.attrib.get("Type") != "Camera":
            continue

        cam_id = elem.attrib.get("Id") or ""
        if not cam_id:
            continue

        if cam_id in cameras:
            continue

        props = {}
        for child in list(elem):
            if child.tag.endswith("Properties"):
                props = dict(child.attrib)
                break

        cameras[cam_id] = {
            "id": cam_id,
            "name": elem.attrib.get("Name") or "",
            "live": props.get("Live", ""),
            "playback": props.get("Playback", ""),
            "ptz": props.get("PTZ", ""),
        }

    return list(cameras.values())


def encrypt_value(value: str, aes_key: bytes, iv: bytes) -> str:
    cipher = AES.new(aes_key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(pad(value.encode("utf-8"), AES.block_size, style="pkcs7"))
    return base64.b64encode(encrypted).decode("ascii")


class MilestoneProbeClient:
    def __init__(self, cfg: ProbeConfig, logger: Logger) -> None:
        self.cfg = cfg
        self.log = logger
        self.base_url = cfg.base_url.rstrip("/")
        self.comm_url = f"{self.base_url}/XProtectMobile/Communication"
        self.http = requests.Session()
        self.sequence_id = 1

    def next_sequence(self) -> int:
        value = self.sequence_id
        self.sequence_id += 1
        return value

    def post_xml(self, title: str, xml_body: str, save_name: str) -> requests.Response:
        self.log.sub(title)
        self.log.write("POST: " + self.comm_url)
        self.log.write("REQUEST XML:")
        self.log.write(xml_body)

        resp = self.http.post(
            self.comm_url,
            data=xml_body.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8", "Accept": "text/xml"},
            timeout=self.cfg.timeout,
            verify=self.cfg.verify_ssl,
        )

        self.log.write("STATUS: " + str(resp.status_code))
        self.log.write("RESPONSE HEADERS:")
        for k, v in resp.headers.items():
            self.log.write(f"  {k}: {v}")
        self.log.write("BODY:")
        self.log.write(resp.content.decode("utf-8", errors="replace"))

        raw_path = self.log.output_dir / save_name
        raw_path.write_text(resp.content.decode("utf-8", errors="replace"), encoding="utf-8", errors="replace")
        self.log.write("Saved response: " + str(raw_path))

        return resp

    def connect(self) -> LoginSession:
        private_key = random.getrandbits(160)
        public_key_int = pow(GENERATOR, private_key, PRIME)
        public_key_le = int_to_little_endian_bytes(public_key_int)
        public_key_b64 = base64.b64encode(public_key_le).decode("ascii")

        xml = f'''<?xml version="1.0" encoding="utf-8"?>
<Communication xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <ConnectionId />
  <Command SequenceId="{self.next_sequence()}">
    <Type>Request</Type>
    <Name>Connect</Name>
    <InputParams>
      <Param Name="PublicKey" Value="{public_key_b64}" />
      <Param Name="PrimeLength" Value="1024" />
      <Param Name="EncryptionPadding" Value="PKCS7" />
    </InputParams>
    <OutputParams />
  </Command>
</Communication>'''

        resp = self.post_xml("CONNECT", xml, "01_connect_response.xml")
        resp.raise_for_status()

        parsed = extract_params(resp.content.decode("utf-8", errors="replace"))
        params = parsed["params"]  # type: ignore[assignment]
        assert isinstance(params, dict)

        connection_id = parsed["connection_id"] or params.get("ConnectionId")
        server_public_key_b64 = params.get("PublicKey")

        self.log.write("CONNECT PARSED:")
        self.log.write("  connection_id: " + str(connection_id))
        self.log.write("  server_public_key exists: " + str(bool(server_public_key_b64)))

        if not connection_id:
            raise RuntimeError("Connect OK HTTP but missing ConnectionId")
        if not server_public_key_b64:
            raise RuntimeError("Connect OK HTTP but missing PublicKey")

        server_public_key_le = base64.b64decode(server_public_key_b64)
        server_public_key_int = little_endian_bytes_to_int(server_public_key_le)

        shared_key_int = pow(server_public_key_int, private_key, PRIME)
        shared_key_bytes = int_to_little_endian_bytes(shared_key_int)

        if len(shared_key_bytes) < 48:
            raise RuntimeError(f"Shared key too short: {len(shared_key_bytes)}")

        iv = shared_key_bytes[0:16]
        aes_key = shared_key_bytes[16:48]

        return LoginSession(connection_id=str(connection_id), iv=iv, aes_key=aes_key)

    def login(self, login_session: LoginSession) -> None:
        if self.cfg.login_type == "Basic":
            full_username = self.cfg.username
        elif self.cfg.domain and "\\" not in self.cfg.username:
            full_username = f"{self.cfg.domain}\\{self.cfg.username}"
        else:
            full_username = self.cfg.username

        encrypted_username = encrypt_value(full_username, login_session.aes_key, login_session.iv)
        encrypted_password = encrypt_value(self.cfg.password, login_session.aes_key, login_session.iv)

        xml = f'''<?xml version="1.0" encoding="utf-8"?>
<Communication xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <ConnectionId>{login_session.connection_id}</ConnectionId>
  <Command SequenceId="{self.next_sequence()}">
    <Type>Request</Type>
    <Name>LogIn</Name>
    <InputParams>
      <Param Name="Username" Value="{encrypted_username}" />
      <Param Name="Password" Value="{encrypted_password}" />
      <Param Name="LoginType" Value="{self.cfg.login_type}" />
      <Param Name="SupportsResampling" Value="Yes" />
      <Param Name="SupportsExtendedResamplingFactor" Value="Yes" />
      <Param Name="ClientType" Value="WebClient" />
    </InputParams>
    <OutputParams />
  </Command>
</Communication>'''

        resp = self.post_xml("LOGIN", xml, "02_login_response.xml")
        resp.raise_for_status()
        parsed = extract_params(resp.content.decode("utf-8", errors="replace"))

        self.log.write("LOGIN PARSED:")
        self.log.write("  result: " + str(parsed.get("result")))
        self.log.write("  error_code: " + str(parsed.get("error_code")))
        self.log.write("  error_text: " + str(parsed.get("error_text")))

        if parsed.get("result") != "OK" and "<Result>OK</Result>" not in resp.content.decode("utf-8", errors="replace"):
            raise RuntimeError("Login failed. See 02_login_response.xml")

    def get_all_views_and_cameras(self, connection_id: str) -> List[Dict[str, str]]:
        xml = f'''<?xml version="1.0" encoding="utf-8"?>
<Communication xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <ConnectionId>{connection_id}</ConnectionId>
  <Command SequenceId="{self.next_sequence()}">
    <Type>Request</Type>
    <Name>GetAllViewsAndCameras</Name>
    <InputParams />
    <OutputParams />
  </Command>
</Communication>'''

        resp = self.post_xml("GET ALL VIEWS AND CAMERAS", xml, "03_get_all_views_and_cameras_response.xml")
        resp.raise_for_status()
        parsed = extract_params(resp.content.decode("utf-8", errors="replace"))
        if parsed.get("result") != "OK" and "<Result>OK</Result>" not in resp.content.decode("utf-8", errors="replace"):
            raise RuntimeError("GetAllViewsAndCameras failed. See 03_get_all_views_and_cameras_response.xml")

        cameras = extract_camera_items(resp.content.decode("utf-8", errors="replace"))
        self.log.write(f"Parsed camera count: {len(cameras)}")
        for idx, cam in enumerate(cameras[:10], start=1):
            self.log.write(f"  {idx}. {cam['id']} | Live={cam['live']} | PTZ={cam['ptz']} | {cam['name']}")
        if len(cameras) > 10:
            self.log.write(f"  ... {len(cameras) - 10} more")
        return cameras

    def build_request_stream_xml(self, connection_id: str, camera_id: str) -> str:
        # Based on the common mipsdkmobile-web RequestStream parameter shape.
        # For diagnostics we keep it explicit and easy to edit.
        now_ms = int(time.time() * 1000)
        seq = self.next_sequence()
        stream_type = "Transcoded"
        signal_type = "Live"
        method_type = "Push"

        variant = self.cfg.request_variant.lower().strip()

        if variant == "minimal":
            params = [
                ("ItemId", camera_id),
                ("SignalType", signal_type),
                ("MethodType", method_type),
                ("StreamType", stream_type),
            ]
        elif variant == "cameraid":
            params = [
                ("CameraId", camera_id),
                ("SignalType", signal_type),
                ("MethodType", method_type),
                ("StreamType", stream_type),
                ("Fps", str(self.cfg.fps)),
                ("DestWidth", str(self.cfg.width)),
                ("DestHeight", str(self.cfg.height)),
                ("ComprLevel", "70"),
                ("KeyFramesOnly", "No"),
            ]
        else:
            params = [
                ("Fps", str(self.cfg.fps)),
                ("DestHeight", str(self.cfg.height)),
                ("StreamType", stream_type),
                ("KeyFramesOnly", "No"),
                ("DestWidth", str(self.cfg.width)),
                ("MethodType", method_type),
                ("ItemId", camera_id),
                ("SignalType", signal_type),
                ("ComprLevel", "70"),
                ("Time", str(now_ms)),
                ("ExportAvi", "Yes"),
                ("ExportDatabase", "Yes"),
                ("ExportJpeg", "Yes"),
            ]

        param_xml = "\n".join(
            f'      <Param Name="{xml_escape(k)}" Value="{xml_escape(v)}" />' for k, v in params
        )

        return f'''<?xml version="1.0" encoding="utf-8"?>
<Communication xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <ConnectionId>{connection_id}</ConnectionId>
  <Command SequenceId="{seq}">
    <Type>Request</Type>
    <Name>RequestStream</Name>
    <InputParams>
{param_xml}
    </InputParams>
    <OutputParams />
  </Command>
</Communication>'''

    def request_stream(self, connection_id: str, camera_id: str) -> Tuple[str, Optional[str]]:
        xml = self.build_request_stream_xml(connection_id, camera_id)
        resp = self.post_xml("REQUEST STREAM", xml, "04_request_stream_response.xml")
        resp.raise_for_status()
        parsed = extract_params(resp.content.decode("utf-8", errors="replace"))
        params = parsed["params"]  # type: ignore[assignment]
        assert isinstance(params, dict)

        self.log.write("REQUEST STREAM PARSED:")
        self.log.write("  result: " + str(parsed.get("result")))
        self.log.write("  error_code: " + str(parsed.get("error_code")))
        self.log.write("  error_text: " + str(parsed.get("error_text")))
        self.log.write("  params: " + str(params))

        if parsed.get("result") != "OK" and "<Result>OK</Result>" not in resp.content.decode("utf-8", errors="replace"):
            raise RuntimeError("RequestStream failed. See 04_request_stream_response.xml")

        video_id = params.get("VideoId") or params.get("StreamId")
        stream_id = params.get("StreamId")

        if not video_id:
            raise RuntimeError("RequestStream OK but missing VideoId/StreamId in OutputParams")

        return str(video_id), str(stream_id) if stream_id else None

    def close_stream(self, connection_id: str, video_id: str, stream_id: Optional[str]) -> None:
        # Try close with both VideoId and StreamId. If Mobile Server ignores one, the other may still be enough.
        params = [("VideoId", video_id)]
        if stream_id and stream_id != video_id:
            params.append(("StreamId", stream_id))
        param_xml = "\n".join(
            f'      <Param Name="{xml_escape(k)}" Value="{xml_escape(v)}" />' for k, v in params
        )
        xml = f'''<?xml version="1.0" encoding="utf-8"?>
<Communication xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <ConnectionId>{connection_id}</ConnectionId>
  <Command SequenceId="{self.next_sequence()}">
    <Type>Request</Type>
    <Name>CloseStream</Name>
    <InputParams>
{param_xml}
    </InputParams>
    <OutputParams />
  </Command>
</Communication>'''
        try:
            self.post_xml("CLOSE STREAM", xml, "07_close_stream_response.xml")
        except Exception as exc:
            self.log.write("[WARN] CloseStream failed: " + repr(exc))

    def open_video(self, video_id: str) -> requests.Response:
        url = f"{self.base_url}/XProtectMobile/Video/{video_id}"
        self.log.sub("OPEN VIDEO STREAM")
        self.log.write("GET: " + url)
        resp = self.http.get(url, stream=True, timeout=(self.cfg.timeout, max(5, self.cfg.sample_seconds + 10)), verify=self.cfg.verify_ssl)
        self.log.write("STATUS: " + str(resp.status_code))
        self.log.write("RESPONSE HEADERS:")
        for k, v in resp.headers.items():
            self.log.write(f"  {k}: {v}")
        resp.raise_for_status()
        return resp


def iter_jpegs_from_bytes(data: bytes) -> Iterator[bytes]:
    offset = 0
    while True:
        start = data.find(JPEG_SOI, offset)
        if start < 0:
            return
        end = data.find(JPEG_EOI, start + 2)
        if end < 0:
            return
        yield data[start:end + 2]
        offset = end + 2


def iter_jpegs_from_stream(resp: requests.Response, seconds: int, log: Logger, raw_path: Path) -> Tuple[List[bytes], bytes]:
    deadline = time.monotonic() + seconds
    buffer = bytearray()
    frames: List[bytes] = []
    raw = bytearray()

    for chunk in resp.iter_content(chunk_size=8192):
        if chunk:
            raw.extend(chunk)
            buffer.extend(chunk)

            while True:
                start = buffer.find(JPEG_SOI)
                if start < 0:
                    if len(buffer) > 1024 * 1024:
                        del buffer[:-2]
                    break
                end = buffer.find(JPEG_EOI, start + 2)
                if end < 0:
                    if start > 0:
                        del buffer[:start]
                    break
                frame = bytes(buffer[start:end + 2])
                del buffer[:end + 2]
                frames.append(frame)
                log.write(f"[FRAME] extracted jpeg #{len(frames)} bytes={len(frame)}")

        if time.monotonic() >= deadline:
            break

    raw_path.write_bytes(bytes(raw))
    return frames, bytes(raw)


def save_sample_outputs(cfg: ProbeConfig, log: Logger, camera_id: str, frames: List[bytes], raw: bytes) -> None:
    sample_bin = cfg.output_dir / "05_video_sample.bin"
    sample_bin.write_bytes(raw)
    log.write("Saved raw sample: " + str(sample_bin))
    log.write("Raw sample bytes: " + str(len(raw)))

    frames_dir = cfg.output_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    for idx, frame in enumerate(frames[:20], start=1):
        (frames_dir / f"frame_{idx:03d}.jpg").write_bytes(frame)

    log.write(f"Saved extracted frames: {min(len(frames), 20)} -> {frames_dir}")

    if frames:
        cfg.thumbnail_root.mkdir(parents=True, exist_ok=True)
        thumb_path = cfg.thumbnail_root / f"{camera_id}.jpg"
        latest_dir = cfg.hls_root / camera_id
        latest_dir.mkdir(parents=True, exist_ok=True)
        latest_path = latest_dir / "latest.jpg"
        thumb_path.write_bytes(frames[-1])
        latest_path.write_bytes(frames[-1])
        log.write("Saved thumbnail: " + str(thumb_path))
        log.write("Saved latest: " + str(latest_path))


def run_ffmpeg_hls(cfg: ProbeConfig, log: Logger, camera_id: str, frames: List[bytes]) -> None:
    if not frames:
        log.write("[SKIP] No frames extracted, skip FFmpeg HLS test")
        return

    ffmpeg = shutil.which(cfg.ffmpeg_bin) or str(Path(cfg.ffmpeg_bin))
    log.sub("FFMPEG HLS TEST")
    log.write("ffmpeg: " + ffmpeg)

    hls_dir = cfg.hls_root / camera_id
    hls_dir.mkdir(parents=True, exist_ok=True)

    for pattern in ("*.ts", "*.m3u8", "*.tmp"):
        for path in hls_dir.glob(pattern):
            try:
                path.unlink()
            except Exception as exc:
                log.write(f"[WARN] Cannot delete old file {path}: {exc!r}")

    segment_pattern = str(hls_dir / "seg_%05d.ts")
    index_path = str(hls_dir / "index.m3u8")

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "info",
        "-f",
        "mjpeg",
        "-framerate",
        str(cfg.fps),
        "-i",
        "pipe:0",
        "-vf",
        f"scale={cfg.width}:{cfg.height}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-tune",
        "zerolatency",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(cfg.fps),
        "-g",
        str(cfg.fps * 2),
        "-b:v",
        cfg.bitrate,
        "-maxrate",
        cfg.maxrate,
        "-bufsize",
        cfg.bufsize,
        "-hls_time",
        "1",
        "-hls_list_size",
        "5",
        "-hls_flags",
        "delete_segments+omit_endlist",
        "-hls_delete_threshold",
        "2",
        "-hls_segment_filename",
        segment_pattern,
        index_path,
    ]

    log.write("COMMAND:")
    log.write(" ".join(cmd))

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None

    # Repeat frames for a few seconds so HLS has enough data.
    repeat_until = time.monotonic() + max(6, cfg.sample_seconds)
    frame_idx = 0
    while time.monotonic() < repeat_until:
        proc.stdin.write(frames[frame_idx % len(frames)])
        proc.stdin.flush()
        frame_idx += 1
        time.sleep(1 / max(1, cfg.fps))

    proc.stdin.close()
    stdout, stderr = proc.communicate(timeout=20)
    log.write("FFmpeg return code: " + str(proc.returncode))
    log.write("FFmpeg stdout:")
    log.write(stdout.decode("utf-8", errors="replace"))
    log.write("FFmpeg stderr:")
    log.write(stderr.decode("utf-8", errors="replace"))

    log.write("HLS dir listing:")
    for path in sorted(hls_dir.glob("*")):
        log.write(f"  {path.name} | {path.stat().st_size} bytes")


def check_environment(cfg: ProbeConfig, log: Logger) -> None:
    log.section("ENVIRONMENT CHECK")
    log.write("Python executable: " + sys.executable)
    log.write("Python version: " + sys.version.replace("\n", " "))
    log.write("Base URL: " + cfg.base_url)
    log.write("Output dir: " + str(cfg.output_dir))
    log.write("HLS root: " + str(cfg.hls_root))
    log.write("Thumbnail root: " + str(cfg.thumbnail_root))
    log.write("FFmpeg bin setting: " + cfg.ffmpeg_bin)
    log.write("FFmpeg resolved: " + str(shutil.which(cfg.ffmpeg_bin) or Path(cfg.ffmpeg_bin)))

    if cfg.nginx_root:
        log.write("Nginx root: " + str(cfg.nginx_root))
        log.write("Nginx exists: " + str(cfg.nginx_root.exists()))
        nginx_exe = cfg.nginx_root / "nginx.exe"
        log.write("nginx.exe exists: " + str(nginx_exe.exists()))

    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    cfg.hls_root.mkdir(parents=True, exist_ok=True)
    cfg.thumbnail_root.mkdir(parents=True, exist_ok=True)

    write_test_1 = cfg.hls_root / "write_test.txt"
    write_test_2 = cfg.thumbnail_root / "write_test.txt"
    write_test_1.write_text("ok", encoding="utf-8")
    write_test_2.write_text("ok", encoding="utf-8")
    log.write("Write test HLS: " + str(write_test_1.exists()))
    log.write("Write test thumbnails: " + str(write_test_2.exists()))


def choose_camera(cameras: List[Dict[str, str]], requested: Optional[str], log: Logger) -> str:
    if requested:
        for cam in cameras:
            if cam["id"].lower() == requested.lower():
                log.write("Selected camera from --camera-id: " + requested)
                return cam["id"]
        log.write("[WARN] --camera-id not found in current camera list. Will still try requested id: " + requested)
        return requested

    if not cameras:
        raise RuntimeError("No cameras returned from GetAllViewsAndCameras")

    for cam in cameras:
        if cam.get("live") == "Yes":
            log.write("Selected first Live=Yes camera: " + cam["id"] + " | " + cam.get("name", ""))
            return cam["id"]

    log.write("Selected first camera: " + cameras[0]["id"])
    return cameras[0]["id"]


def parse_args() -> ProbeConfig:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("MILESTONE_BASE_URL", "http://10.2.18.11:8081"))
    parser.add_argument(
        "--login-type",
        choices=["Basic", "ActiveDirectory"],
        default=os.getenv("MILESTONE_LOGIN_TYPE", "ActiveDirectory"),
    )
    parser.add_argument("--domain", default=os.getenv("MILESTONE_DOMAIN", "VMS-ITS"))
    parser.add_argument("--username", default=os.getenv("MILESTONE_USERNAME", "administrator"))
    parser.add_argument("--password", default=os.getenv("MILESTONE_PASSWORD", ""))
    parser.add_argument("--verify-ssl", action="store_true")
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--camera-id", default=os.getenv("MILESTONE_CAMERA_ID"))
    parser.add_argument("--sample-seconds", type=int, default=8)
    parser.add_argument("--output-dir", default=r"C:\milestone_probe")
    parser.add_argument("--hls-root", default=r"C:\hls")
    parser.add_argument("--thumbnail-root", default=r"C:\thumbnails")
    parser.add_argument("--ffmpeg-bin", default=os.getenv("FFMPEG_BIN", "ffmpeg"))
    parser.add_argument("--width", type=int, default=854)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=2)
    parser.add_argument("--bitrate", default="300k")
    parser.add_argument("--maxrate", default="400k")
    parser.add_argument("--bufsize", default="800k")
    parser.add_argument("--nginx-root", default=r"C:\nginx")
    parser.add_argument(
        "--request-variant",
        choices=["websdk", "minimal", "cameraid"],
        default="websdk",
        help="websdk uses ItemId + common mipsdkmobile-web params. minimal uses fewer params. cameraid uses CameraId instead of ItemId.",
    )

    args = parser.parse_args()

    if not args.password:
        raise SystemExit(
            "Missing password. Use --password \"...\" or set environment variable MILESTONE_PASSWORD."
        )

    return ProbeConfig(
        base_url=args.base_url,
        login_type=args.login_type,
        domain=args.domain,
        username=args.username,
        password=args.password,
        verify_ssl=args.verify_ssl,
        timeout=args.timeout,
        camera_id=args.camera_id,
        sample_seconds=args.sample_seconds,
        output_dir=Path(args.output_dir),
        hls_root=Path(args.hls_root),
        thumbnail_root=Path(args.thumbnail_root),
        ffmpeg_bin=args.ffmpeg_bin,
        width=args.width,
        height=args.height,
        fps=args.fps,
        bitrate=args.bitrate,
        maxrate=args.maxrate,
        bufsize=args.bufsize,
        nginx_root=Path(args.nginx_root) if args.nginx_root else None,
        request_variant=args.request_variant,
    )


def main() -> int:
    cfg = parse_args()
    log = Logger(cfg.output_dir)

    try:
        check_environment(cfg, log)

        client = MilestoneProbeClient(cfg, log)

        log.section("MILESTONE CONNECT / LOGIN / CAMERA LIST")
        session = client.connect()
        client.login(session)
        cameras = client.get_all_views_and_cameras(session.connection_id)
        camera_id = choose_camera(cameras, cfg.camera_id, log)

        log.section("REQUEST STREAM + SAMPLE VIDEO")
        video_id = None
        stream_id = None
        response = None
        try:
            video_id, stream_id = client.request_stream(session.connection_id, camera_id)
            log.write("VideoId: " + video_id)
            log.write("StreamId: " + str(stream_id))

            response = client.open_video(video_id)
            raw_sample_path = cfg.output_dir / "05_video_sample.bin"
            frames, raw = iter_jpegs_from_stream(response, cfg.sample_seconds, log, raw_sample_path)
            save_sample_outputs(cfg, log, camera_id, frames, raw)
            run_ffmpeg_hls(cfg, log, camera_id, frames)

        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            if video_id:
                client.close_stream(session.connection_id, video_id, stream_id)

        log.section("DONE")
        log.write("Log file: " + str(log.log_path))
        log.write("Output dir: " + str(cfg.output_dir))
        return 0

    except Exception as exc:
        log.section("FAILED")
        log.write("Exception: " + repr(exc))
        log.write(traceback.format_exc())
        log.write("Log file: " + str(log.log_path))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
