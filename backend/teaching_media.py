"""Media policy for the teaching library only; no transcoder or external hosts."""
import hashlib
from pathlib import Path
from urllib.parse import quote
from fastapi import HTTPException
from fastapi.responses import FileResponse
from . import homework_files as files

MEDIA = {'.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.m4a': 'audio/mp4',
         '.ogg': 'audio/ogg', '.mp4': 'video/mp4', '.webm': 'video/webm'}
IMAGES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}


def name_for(filename):
    name = filename.replace('\\', '/').rsplit('/', 1)[-1].strip()
    if Path(name).suffix.lower() not in MEDIA:
        return files.name_for(filename)
    if not name or len(name) > 180 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise HTTPException(422, 'Используйте имя файла длиной до 180 символов без управляющих знаков.')
    return name


def mp3_frame_size(header):
    if len(header) != 4: return 0
    value = int.from_bytes(header, 'big')
    version, layer = (value >> 19) & 3, (value >> 17) & 3
    rate, sample = (value >> 12) & 15, (value >> 10) & 3
    if value >> 21 != 0x7ff or version == 1 or layer != 1 or rate in (0, 15) or sample == 3: return 0
    bitrate = ([0,32,40,48,56,64,80,96,112,128,160,192,224,256,320] if version == 3 else
               [0,8,16,24,32,40,48,56,64,80,96,112,128,144,160])[rate] * 1000
    frequency = [44100,48000,32000][sample] // (1 if version == 3 else 2 if version == 2 else 4)
    return (144 if version == 3 else 72) * bitrate // frequency + ((value >> 9) & 1)


def valid_mp3(stream, size):
    prefix = stream.read(10); offset = 0
    if prefix[:3] == b'ID3':
        if len(prefix) < 10 or prefix[3] not in (2,3,4) or any(x > 127 for x in prefix[6:10]): return False
        offset = 10 + sum(x << shift for x,shift in zip(prefix[6:10], (21,14,7,0)))
        if prefix[3] == 4 and prefix[5] & 16: offset += 10
    # Two consecutive Layer III frames, after an optional bounded ID3 tag.
    for _ in range(2):
        if offset + 4 > size: return False
        stream.seek(offset); length = mp3_frame_size(stream.read(4))
        if not length or offset + length > size: return False
        offset += length
    return True


def valid_wav(stream, size):
    header = stream.read(12)
    if len(header) != 12 or header[:4] != b'RIFF' or header[8:] != b'WAVE': return False
    end = int.from_bytes(header[4:8], 'little') + 8
    if end != size: return False
    found = set(); offset = 12
    for _ in range(10000):
        if offset + 8 > end: break
        stream.seek(offset); header = stream.read(8); length = int.from_bytes(header[4:], 'little')
        if offset + 8 + length > end: return False
        if header[:4] == b'fmt ' and length >= 16: found.add('format')
        if header[:4] == b'data' and length: found.add('data')
        offset += 8 + length + length % 2
    return offset == end and found == {'format','data'}


def valid_mp4(stream, size):
    offset = 0; found = set()
    for _ in range(10000):
        if offset + 8 > size: break
        stream.seek(offset); header = stream.read(8); length = int.from_bytes(header[:4], 'big'); head_size = 8
        if length == 1:
            length = int.from_bytes(stream.read(8), 'big'); head_size = 16
        elif length == 0: length = size - offset
        if length < head_size or offset + length > size: return False
        kind = header[4:]
        if kind == b'ftyp':
            data = stream.read(min(length - head_size, 512))
            brands = {data[:4], *(data[i:i+4] for i in range(8, len(data), 4))}
            if len(data) < 8 or not brands & {b'isom',b'iso2',b'iso5',b'iso6',b'mp41',b'mp42',b'avc1',b'M4A ',b'M4V ',b'dash'}: return False
            found.add('type')
        elif kind == b'moov' and length > head_size: found.add('metadata')
        elif kind == b'mdat' and length > head_size: found.add('data')
        offset += length
    return offset == size and found == {'type','metadata','data'}


def vint(data, offset, keep_marker=False):
    if offset >= len(data) or not data[offset]: return None
    length = 1
    while length <= 8 and not data[offset] & (1 << (8-length)): length += 1
    if length > 8 or offset + length > len(data): return None
    raw = int.from_bytes(data[offset:offset+length], 'big')
    return (raw if keep_marker else raw & ((1 << (7*length))-1), offset+length)


def valid_webm(stream, size):
    data = stream.read(8192)
    if data[:4] != b'\x1a\x45\xdf\xa3': return False
    header = vint(data, 4)
    if not header: return False
    length, offset = header; end = offset + length
    if end > len(data): return False
    doctype = False
    while offset < end:
        tag = vint(data, offset, True)
        if not tag: return False
        element = vint(data, tag[1])
        if not element: return False
        length, start = element
        if start + length > end: return False
        if tag[0] == 0x4282: doctype = data[start:start+length] == b'webm'
        offset = start + length
    # A WebM EBML header must be followed by a Segment, not arbitrary HTML.
    return doctype and data[end:end+4] == b'\x18\x53\x80\x67' and size > end + 12


def valid_ogg(stream, size):
    header = stream.read(27)
    if len(header) != 27 or header[:4] != b'OggS' or header[4] != 0 or not header[5] & 2: return False
    lacing = stream.read(header[26]); length = sum(lacing)
    if len(lacing) != header[26] or length < 8 or 27+len(lacing)+length > size: return False
    packet = stream.read(min(length, 16))
    return packet.startswith(b'OpusHead') or packet.startswith(b'\x01vorbis')


def validate_file(path, name):
    suffix = Path(name).suffix.lower()
    if suffix not in MEDIA: return files.validate_file(path, name)
    validate = {'.mp3':valid_mp3,'.wav':valid_wav,'.mp4':valid_mp4,'.m4a':valid_mp4,
                '.webm':valid_webm,'.ogg':valid_ogg}[suffix]
    with path.open('rb') as stream:
        if not validate(stream, path.stat().st_size):
            raise HTTPException(422, 'Содержимое не соответствует формату аудио/видео или файл повреждён.')
        stream.seek(0)
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def response(path, name, *, download=False, inline_images=False):
    suffix = Path(name).suffix.lower()
    mime = MEDIA.get(suffix) or (IMAGES.get(suffix) if inline_images else None)
    inline = mime and not download
    headers = {'Content-Disposition': ('inline' if inline else 'attachment') + "; filename*=UTF-8''" + quote(name),
               'Cache-Control': 'no-store', 'Vary': 'Cookie', 'X-Content-Type-Options': 'nosniff',
               'Content-Security-Policy': "sandbox; default-src 'none'"}
    # FileResponse streams bounded chunks and implements Range/If-Range after ACL.
    return FileResponse(path, media_type=mime if inline else 'application/octet-stream', headers=headers)
