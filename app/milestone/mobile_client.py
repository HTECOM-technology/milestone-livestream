import base64
import random
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from urllib3.exceptions import InsecureRequestWarning

from app.core.config import get_settings

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

PRIME_1024_HEX = (
    "F488FD584E49DBCD20B49DE49107366B336C380D451D0F7C88B31C7C5B2D8EF6"
    "F3C923C043F0A55B188D8EBB558CB85D38D334FD7C175743A31D186CDE33212"
    "CB52AFF3CE1B1294018118D7C84A70A72D686C40319C807297ACA950CD9969F"
    "ABD00A509B0246D3083D66A45D419F9C7CBD894B221926BAABA25EC355E92F78C7"
)

PRIME = int(PRIME_1024_HEX, 16)
GENERATOR = 2


@dataclass
class MilestoneSession:
    connection_id: str
    iv: bytes
    aes_key: bytes


@dataclass
class RequestStreamResult:
    video_id: str
    stream_id: Optional[str]
    src_width: Optional[int]
    src_height: Optional[int]
    raw_response: str


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


def normalize_xml_text(text: str) -> str:
    if not text:
        return text

    text = text.strip()

    # Nếu response có ký tự rác trước XML
    xml_start = text.find("<?xml")
    communication_start = text.find("<Communication")

    if xml_start >= 0:
        start = xml_start
    elif communication_start >= 0:
        start = communication_start
    else:
        start = 0

    text = text[start:].strip()

    # Nếu sau </Communication> còn dữ liệu thừa thì cắt bỏ
    end_tag = "</Communication>"
    end = text.find(end_tag)

    if end >= 0:
        text = text[: end + len(end_tag)]

    return text.strip()


def parse_xml(text: str):
    text = normalize_xml_text(text)
    return ET.fromstring(text)


def extract_params(xml_text: str) -> dict:
    root = parse_xml(xml_text)
    params: dict[str, str] = {}

    for elem in root.iter():
        if elem.tag.endswith("Param"):
            name = elem.attrib.get("Name")
            value = elem.attrib.get("Value")
            if name:
                params[name] = value

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


class MilestoneMobileClient:
    """
    Milestone XProtect Mobile Server 2022 R1 client.

    Flow đã xác minh từ script debug:
    - Connect: gửi PublicKey + PrimeLength + EncryptionPadding
    - LogIn: encrypt domain\\username và password bằng AES-CBC PKCS7
    - RequestStream: dùng ItemId là Id của camera trong GetAllViewsAndCameras
    - Video stream: GET /XProtectMobile/Video/{VideoId}
    - CloseStream: Param VideoId
    """

    def __init__(self) -> None:
        settings = get_settings()

        self.base_url = settings.milestone_base_url.rstrip("/")
        self.comm_url = f"{self.base_url}/XProtectMobile/Communication"

        self.full_username = settings.milestone_username

        self.password = settings.milestone_password
        self.timeout = settings.milestone_request_timeout_seconds
        self.stream_timeout = settings.milestone_stream_timeout_seconds
        self.verify_ssl = getattr(settings, "milestone_verify_ssl", False)

        self.http = requests.Session()
        self.sequence_id = 1

        self.session: Optional[MilestoneSession] = None

    def connect(self) -> MilestoneSession:
        private_key = random.getrandbits(160)
        public_key_int = pow(GENERATOR, private_key, PRIME)

        public_key_le = int_to_little_endian_bytes(public_key_int)
        public_key_b64 = base64.b64encode(public_key_le).decode("ascii")

        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<Communication xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <ConnectionId />
  <Command SequenceId="{self._next_sequence()}">
    <Type>Request</Type>
    <Name>Connect</Name>
    <InputParams>
      <Param Name="PublicKey" Value="{public_key_b64}" />
      <Param Name="PrimeLength" Value="1024" />
      <Param Name="EncryptionPadding" Value="PKCS7" />
    </InputParams>
    <OutputParams />
  </Command>
</Communication>"""

        text = self._post_xml(xml)
        parsed = extract_params(text)
        params = parsed["params"]

        connection_id = parsed["connection_id"] or params.get("ConnectionId")
        server_public_key_b64 = params.get("PublicKey")

        if not connection_id:
            raise RuntimeError(f"Connect failed: missing ConnectionId. Response: {text[:1000]}")

        if not server_public_key_b64:
            raise RuntimeError(f"Connect failed: missing PublicKey. Response: {text[:1000]}")

        server_public_key_le = base64.b64decode(server_public_key_b64)
        server_public_key_int = little_endian_bytes_to_int(server_public_key_le)

        shared_key_int = pow(server_public_key_int, private_key, PRIME)
        shared_key_bytes = int_to_little_endian_bytes(shared_key_int)

        if len(shared_key_bytes) < 48:
            raise RuntimeError(f"Connect failed: shared key too short: {len(shared_key_bytes)}")

        iv = shared_key_bytes[0:16]
        aes_key = shared_key_bytes[16:48]

        self.session = MilestoneSession(
            connection_id=connection_id,
            iv=iv,
            aes_key=aes_key,
        )

        return self.session

    def login(self) -> None:
        if not self.session:
            raise RuntimeError("connect() must be called before login()")

        encrypted_username = self._encrypt_value(self.full_username)
        encrypted_password = self._encrypt_value(self.password)

        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<Communication xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <ConnectionId>{self.session.connection_id}</ConnectionId>
  <Command SequenceId="{self._next_sequence()}">
    <Type>Request</Type>
    <Name>LogIn</Name>
    <InputParams>
      <Param Name="Username" Value="{encrypted_username}" />
      <Param Name="Password" Value="{encrypted_password}" />
      <Param Name="SupportsResampling" Value="Yes" />
      <Param Name="SupportsExtendedResamplingFactor" Value="Yes" />
      <Param Name="ClientType" Value="WebClient" />
    </InputParams>
    <OutputParams />
  </Command>
</Communication>"""

        text = self._post_xml(xml)
        parsed = extract_params(text)

        if parsed["result"] != "OK":
            raise RuntimeError(
                f"LogIn failed. result={parsed['result']}, "
                f"error_code={parsed['error_code']}, error_text={parsed['error_text']}"
            )

    def connect_and_login(self) -> None:
        self.connect()
        self.login()

    def get_all_views_and_cameras_raw(self) -> str:
        if not self.session:
            raise RuntimeError("connect_and_login() must be called before get_all_views_and_cameras_raw()")

        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<Communication xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <ConnectionId>{self.session.connection_id}</ConnectionId>
  <Command SequenceId="{self._next_sequence()}">
    <Type>Request</Type>
    <Name>GetAllViewsAndCameras</Name>
    <InputParams />
    <OutputParams />
  </Command>
</Communication>"""

        text = self._post_xml(xml)
        parsed = extract_params(text)

        if parsed["result"] != "OK":
            raise RuntimeError(
                f"GetAllViewsAndCameras failed. result={parsed['result']}, "
                f"error_code={parsed['error_code']}, error_text={parsed['error_text']}"
            )

        return text

    def get_all_views_and_cameras(self) -> str:
        return self.get_all_views_and_cameras_raw()

    def request_stream(
        self,
        camera_id: str,
        fps: int = 2,
        width: int = 854,
        height: int = 480,
        compression_level: int = 70,
    ) -> RequestStreamResult:
        if not self.session:
            raise RuntimeError("connect_and_login() must be called before request_stream()")

        # Time theo mẫu request đã chạy thành công. Milestone không nhất thiết cần thời gian hiện tại chuẩn tuyệt đối,
        # nhưng giữ milliseconds để tương thích WebClient request.
        timestamp_ms = int(time.time() * 1000)

        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<Communication xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <ConnectionId>{self.session.connection_id}</ConnectionId>
  <Command SequenceId="{self._next_sequence()}">
    <Type>Request</Type>
    <Name>RequestStream</Name>
    <InputParams>
      <Param Name="Fps" Value="{fps}" />
      <Param Name="DestHeight" Value="{height}" />
      <Param Name="StreamType" Value="Transcoded" />
      <Param Name="KeyFramesOnly" Value="No" />
      <Param Name="DestWidth" Value="{width}" />
      <Param Name="MethodType" Value="Push" />
      <Param Name="ItemId" Value="{xml_escape(camera_id)}" />
      <Param Name="SignalType" Value="Live" />
      <Param Name="ComprLevel" Value="{compression_level}" />
      <Param Name="Time" Value="{timestamp_ms}" />
      <Param Name="ExportAvi" Value="Yes" />
      <Param Name="ExportDatabase" Value="Yes" />
      <Param Name="ExportJpeg" Value="Yes" />
    </InputParams>
    <OutputParams />
  </Command>
</Communication>"""

        text = self._post_xml(xml)
        parsed = extract_params(text)
        params = parsed["params"]

        if parsed["result"] != "OK":
            raise RuntimeError(
                f"RequestStream failed. result={parsed['result']}, "
                f"error_code={parsed['error_code']}, error_text={parsed['error_text']}, response={text[:1000]}"
            )

        video_id = params.get("VideoId")
        stream_id = params.get("StreamId")

        if not video_id:
            raise RuntimeError(f"RequestStream failed: missing VideoId. Response: {text[:1000]}")

        return RequestStreamResult(
            video_id=video_id,
            stream_id=stream_id,
            src_width=int(params["SrcWidth"]) if params.get("SrcWidth") else None,
            src_height=int(params["SrcHeight"]) if params.get("SrcHeight") else None,
            raw_response=text,
        )

    def open_video_stream(self, video_id: str) -> requests.Response:
        url = f"{self.base_url}/XProtectMobile/Video/{video_id}"

        response = self.http.get(
            url,
            stream=True,
            timeout=(self.timeout, self.stream_timeout),
            verify=self.verify_ssl,
            headers={"Accept": "*/*"},
        )

        response.raise_for_status()
        return response

    def close_stream(self, video_id: str) -> None:
        if not self.session:
            return

        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<Communication xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <ConnectionId>{self.session.connection_id}</ConnectionId>
  <Command SequenceId="{self._next_sequence()}">
    <Type>Request</Type>
    <Name>CloseStream</Name>
    <InputParams>
      <Param Name="VideoId" Value="{xml_escape(video_id)}" />
    </InputParams>
    <OutputParams />
  </Command>
</Communication>"""

        try:
            self._post_xml(xml)
        except Exception:
            pass

    def close(self) -> None:
        self.http.close()

    def _post_xml(self, xml_body: str) -> str:
        response = self.http.post(
            self.comm_url,
            data=xml_body.encode("utf-8"),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "Accept": "text/xml",
            },
            timeout=self.timeout,
            verify=self.verify_ssl,
        )
        response.raise_for_status()
        return response.text

    def _encrypt_value(self, value: str) -> str:
        if not self.session:
            raise RuntimeError("connect() must be called before encryption")

        cipher = AES.new(self.session.aes_key, AES.MODE_CBC, self.session.iv)
        encrypted = cipher.encrypt(pad(value.encode("utf-8"), AES.block_size, style="pkcs7"))
        return base64.b64encode(encrypted).decode("ascii")

    def _next_sequence(self) -> int:
        current = self.sequence_id
        self.sequence_id += 1
        return current
